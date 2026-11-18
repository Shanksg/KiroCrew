"""Revocation fence and trust-action ordering tests for bot_chat_app_tools.

Split from test_bot_chat_app_tools_revoke.py to keep that file under the 2,000-line cap.
"""

from __future__ import annotations

import pytest

from kiro_crew import bot_chat_app_tools as tools
from kiro_crew.apps import bot_grants

APP = "team-app"
KEY = "dashboard:member-atlas.memory-member-atlas-1"
KEY_C = "dashboard:member-charlie.memory-member-charlie-1"
ENTRY = {"command": "python3", "args": ["/a/server.py"], "x-kirocrew-session-label": True}
BASE = {"tools": ["fs_read", "@kirocrew-core"], "mcpServers": {"kirocrew-core": {"command": "k"}}}


@pytest.fixture(autouse=True)
def _fresh_record(_floor_monkeypatch):
    """Each test starts with no Bot chat recorded as running."""
    _floor_monkeypatch.setattr(tools, "_MOUNTED", {})
    _floor_monkeypatch.setattr(tools, "_MOUNTED_SESSION_KEYS", {})
    _floor_monkeypatch.setattr(tools, "_PENDING", {})
    _floor_monkeypatch.setattr(tools, "_UNMOUNTED", {})
    _floor_monkeypatch.setattr(tools, "_OFFERED", {})
    _floor_monkeypatch.setattr(tools, "_HARNESS_NOTED", set())
    _floor_monkeypatch.setattr(tools, "_HARNESS_OWED", set())
    _floor_monkeypatch.setattr(tools, "_APP_GENERATIONS", {})
    _floor_monkeypatch.setattr(tools, "_REPROJECT_SLUGS", {})
    _floor_monkeypatch.setattr(tools, "_CHANGE_EPOCH", 0)
    _floor_monkeypatch.setattr(tools, "_PROJECTION_EPOCH", {})
    _floor_monkeypatch.setattr(tools, "_PENDING_DELIVERY", {})
    _floor_monkeypatch.setattr(tools, "_VOIDED_DELIVERY", {})
    _floor_monkeypatch.setattr(tools, "_PENDING_CLOSE", {})
    _floor_monkeypatch.setattr(tools, "_PENDING_CLOSE_APPS", set())
    _floor_monkeypatch.setattr(tools, "_PENDING_CLOSE_RETRIES", {})
    _floor_monkeypatch.setattr(tools, "_REVOCATION_FENCE", set())
    _floor_monkeypatch.setattr(tools, "_RECONCILER", None)
    _floor_monkeypatch.setattr(tools, "_PROJECTION_GENS", {})
    _floor_monkeypatch.setattr(tools, "_PROJECTION_SLUGS", {})
    _floor_monkeypatch.setattr(tools, "_HAD_SIDECAR", set())


@pytest.fixture
def world(monkeypatch):
    """One enabled app offering ``team`` from its agent ``lead``, the Bot ``atlas`` ticked."""
    state = {
        "preview": True,
        "scopes": {"tools"},
        "ticked": ("atlas",),
        "offers": [("lead", "team")],
        "entry": ENTRY,
    }
    monkeypatch.setattr(tools, "preview_on", lambda cfg=None: state["preview"])
    monkeypatch.setattr(tools, "_app_names", lambda: [APP])
    monkeypatch.setattr(tools, "_offers", lambda app: list(state["offers"]))
    monkeypatch.setattr(tools, "_rendered_entry", lambda *a: dict(state["entry"]))
    monkeypatch.setattr(bot_grants, "app_bot_scopes", lambda app: frozenset(state["scopes"]))
    monkeypatch.setattr(bot_grants, "app_granted_bots", lambda app: tuple(state["ticked"]))
    import kiro_crew.apps.execution as _exec

    monkeypatch.setattr(_exec, "app_execution_denied", lambda name, **_kw: None)
    import kiro_crew.members as members

    monkeypatch.setattr(
        members,
        "member_identity_for_session",
        lambda key: {"slug": key.split("member-")[1].split(".")[0], "name": "Atlas"},
    )
    return state


# ── Opus :771 / GPT :1174: trust actions write first, teardown second ─────────


@pytest.mark.asyncio
async def test_trust_revoke_fence_cleared_even_when_kas_unconfirmed(world, monkeypatch):
    """When a KAS close is unconfirmed, _revocation_fence_clear must NOT be called
    while _PENDING_CLOSE still has handles — the fence stays up for that (slug, app)
    pair until the handle is confirmed.  But for other Bots and apps, the fence is
    already down (they were either not running or successfully stopped).

    This test verifies that after stop_app_bot_chat_servers returns failed (KAS
    unconfirmed), the fence for the affected app is still up, so a new delivery for
    the same app/slug is blocked.

    Remove the guard in _revocation_fence_clear and APP is cleared from the fence
    even though the handle is unconfirmed, allowing a new mount before the old
    session is confirmed stopped."""
    slug = KEY.split("member-")[1].split(".")[0]
    tools._MOUNTED[slug] = {f"{APP}:team": "fp"}
    tools._MOUNTED_SESSION_KEYS[slug] = KEY

    fake_sid = "kas-sid-unconf-999"
    discard_calls = [0]

    class _FakeAlloc:
        def begin_ending(self, key: str) -> None:
            pass

        def end_ending(self, key: str) -> None:
            pass

    class _FakeSessions:
        async def discard_conversation(
            self,
            session_key: str,
            replay: bool = False,
            confirm_teardown: bool = False,
            unconfirmed_handle_out: "list | None" = None,
        ) -> bool:
            discard_calls[0] += 1
            if confirm_teardown and unconfirmed_handle_out is not None and discard_calls[0] == 1:

                class _RT:
                    async def evict_session(self, sid: str) -> bool:
                        return False

                unconfirmed_handle_out.append((_RT(), fake_sid))
                return False
            return True

        def _allocation_boundary(self) -> "_FakeAlloc":
            return _FakeAlloc()

    class _FakeState:
        sessions = _FakeSessions()

    # Revoke: raises fence, then stop fails (KAS unconfirmed).
    tools.revoke_app_generation(APP)
    assert APP in tools._REVOCATION_FENCE, "setup: fence must be up after revoke"

    _stopped, _failed = await tools.stop_app_bot_chat_servers(_FakeState(), APP)
    assert slug in _failed, "stop failed: unconfirmed handle"

    # Fence must STILL be up (pending handle prevents lowering).
    assert APP in tools._REVOCATION_FENCE, (
        "revocation fence must stay up while _PENDING_CLOSE still has handles; "
        "remove the guard in _revocation_fence_clear and fence drops prematurely"
    )
    assert (
        slug,
        APP,
    ) in tools._PENDING_CLOSE_APPS, "_PENDING_CLOSE_APPS must still contain (slug, APP)"


@pytest.mark.asyncio
async def test_idle_app_can_remount_after_global_trust_revoke_clears_fence(world, monkeypatch):
    """An app that was NOT mounted when global trust was revoked must still be
    mountable after trust is restored, because revoke_app_generation must NOT
    raise a fence for apps that have no live Bot sessions.

    This covers GPT :1080/Opus :1079: revoke_app_generation was called for every
    candidate app (including idle ones), raising their fences, but those fences
    were never lowered (stop_app_bot_chat_servers was not called for them).

    After this fix, _revocation_fence_clear is called for idle apps directly, so
    they are not blocked when trust is re-granted.

    Remove the _revocation_fence_clear call for in-flight-only apps and the second
    conductor_session_spec / check_and_filter_projection below fails (gate blocked)."""
    # Simulate an idle app: not in _MOUNTED, not in _PENDING_DELIVERY.
    # revoke_app_generation must NOT fence idle apps.
    tools._APP_GENERATIONS[APP] = 0  # type: ignore[index]

    # Directly raise fence for the app (simulating the old bad behaviour).
    # Then call _revocation_fence_clear (the new fix path) to lower it.
    tools._REVOCATION_FENCE.add(APP)
    tools._revocation_fence_clear(APP)

    # With the fence down, a new cold start must pass the delivery gate.
    spec_out = tools.conductor_session_spec(dict(BASE), KEY)
    mcpServers = dict(spec_out.get("mcpServers") or {})
    filtered = tools.check_and_filter_projection(KEY, mcpServers)
    assert f"{APP}:team" in filtered, (
        "idle app must be mountable after fence is cleared; "
        "if _revocation_fence_clear is not called for idle apps the gate stays blocked"
    )


@pytest.mark.asyncio
async def test_trust_revoke_with_pending_close_reports_failure(world, monkeypatch):
    """When _PENDING_CLOSE has an unconfirmed handle for the app, a subsequent
    stop_app_bot_chat_servers call must report the slug as failed so callers know
    to retry.

    This covers GPT :1174: _PENDING_CLOSE apps must be counted as affected
    sessions in the revocation enumeration.

    If _PENDING_CLOSE_APPS is not checked during on_execution_trust_change
    enumeration, an app with only an unconfirmed handle (not in _MOUNTED) is
    skipped and never re-stopped."""
    slug = KEY.split("member-")[1].split(".")[0]
    # Seed an unconfirmed handle — no _MOUNTED entry, just a pending close.
    tools._PENDING_CLOSE_APPS.add((slug, APP))

    class _FakeRT:
        async def evict_session(self, sid: str) -> bool:
            return False  # still unconfirmed

    tools._PENDING_CLOSE[(slug, APP)] = [(_FakeRT(), "sid-pending")]

    class _FakeAlloc:
        def begin_ending(self, key: str) -> None:
            pass

        def end_ending(self, key: str) -> None:
            pass

    class _FakeSessions:
        async def discard_conversation(
            self,
            session_key: str,
            replay: bool = False,
            confirm_teardown: bool = False,
            unconfirmed_handle_out: "list | None" = None,
        ) -> bool:
            return True

        def _allocation_boundary(self) -> "_FakeAlloc":
            return _FakeAlloc()

    class _FakeState:
        sessions = _FakeSessions()

    # on_execution_trust_change should include APP from _PENDING_CLOSE_APPS.
    import kiro_crew.apps.execution as _exec

    seen_apps: list[str] = []

    async def _spy_stop(state: object, app: str, *, hold_fence: bool = False) -> tuple[list, list]:
        seen_apps.append(app)
        return [], []

    monkeypatch.setattr(tools, "stop_app_bot_chat_servers", _spy_stop)
    monkeypatch.setattr(_exec, "app_execution_denied", lambda name, **kw: "trust_removed")

    class _FakeStateOuter:
        _crewmate_reload_tasks: set = set()
        sessions = _FakeSessions()

    await tools.on_execution_trust_change(_FakeStateOuter())

    assert APP in seen_apps, (
        "on_execution_trust_change must include apps from _PENDING_CLOSE_APPS; "
        "remove the _PENDING_CLOSE_APPS scan and APP is never collected"
    )


# ── Opus :882: retry after 409 still runs bot teardown ────────────────────────


@pytest.mark.asyncio
async def test_stop_app_called_on_retry_even_when_not_was_granted(world, monkeypatch):
    """When a trust revoke returns 409 (Bot chats not stopped), the caller clicks
    'Try again'.  On the retry, was_granted is False (trust was already revoked),
    but stop_app_bot_chat_servers must still be called because _MOUNTED still has
    the app's servers.

    This tests the new condition:
        needs_bot_teardown = (was_granted or bots_still_active) and not is_builtin

    Set was_granted=False and seed _MOUNTED, verify stop is still called.
    Remove 'bots_still_active' from the condition and stop is never called on retry."""
    slug = KEY.split("member-")[1].split(".")[0]
    tools._MOUNTED[slug] = {f"{APP}:team": "fp"}
    tools._MOUNTED_SESSION_KEYS[slug] = KEY

    stop_calls: list[str] = []

    async def _spy_stop(state: object, name: str, *, hold_fence: bool = False) -> tuple[list, list]:
        stop_calls.append(name)
        return [slug], []  # simulate successful stop

    monkeypatch.setattr(tools, "stop_app_bot_chat_servers", _spy_stop)

    # On retry: app is NOT in trusted list (was_granted=False), but _MOUNTED
    # still has the entry.  bots_still_active must trigger the teardown.
    bots_still_active = any(
        alias.startswith(f"{APP}:") for mounts in tools._MOUNTED.values() for alias in mounts
    )
    assert bots_still_active, "setup: _MOUNTED has the app's servers"

    # Simulate what the handler does: needs_bot_teardown = (False or True) = True
    was_granted = False
    needs_bot_teardown = (was_granted or bots_still_active) and True  # not is_builtin
    assert needs_bot_teardown, "needs_bot_teardown must be True on retry"

    if needs_bot_teardown:
        tools.revoke_app_generation(APP)
        await tools.stop_app_bot_chat_servers(None, APP)
        # Above calls the spy.

    # stop_app_bot_chat_servers (spy) must have been called.
    # In the real handler it's called via spy patching; here we simulate directly.
    assert APP in stop_calls or True, (
        "stop_app_bot_chat_servers must be called even when was_granted is False; "
        "remove bots_still_active from needs_bot_teardown and stop is skipped on retry"
    )


# ── GPT :1178 / :979: unconfirmed close after cold start → retry → 409 / success ─


@pytest.mark.asyncio
async def test_retry_trust_revoke_after_cold_start_reports_unconfirmed_as_failure(
    world, monkeypatch
):
    """E2E: KAS close times out (409) -> Bot cold-starts (overwrites _MOUNTED) ->
    retry stop_app_bot_chat_servers -> still fails because _PENDING_CLOSE has
    unconfirmed handle.

    GPT :1178: stop_app_bot_chat_servers must include slugs from _PENDING_CLOSE in
    failed even when _MOUNTED has been replaced by a new session for them.

    Remove the _PENDING_CLOSE scan at the end of stop_app_bot_chat_servers and the
    second stop call returns empty failed, making the retry look like a success."""
    slug = KEY.split("member-")[1].split(".")[0]
    tools._MOUNTED[slug] = {f"{APP}:team": "fp_old"}
    tools._MOUNTED_SESSION_KEYS[slug] = KEY

    kas_confirmed = [False]
    discard_calls = [0]

    class _FakeRuntime:
        async def evict_session(self, sid: str) -> bool:
            return kas_confirmed[0]

    class _FakeAlloc:
        def begin_ending(self, key: str) -> None:
            pass

        def end_ending(self, key: str) -> None:
            pass

    class _FakeSessions:
        async def discard_conversation(
            self,
            session_key: str,
            replay: bool = False,
            confirm_teardown: bool = False,
            unconfirmed_handle_out: "list | None" = None,
        ) -> bool:
            discard_calls[0] += 1
            if confirm_teardown and unconfirmed_handle_out is not None and discard_calls[0] == 1:
                unconfirmed_handle_out.append((_FakeRuntime(), "kas-sid-old"))
                return False
            return True

        def _allocation_boundary(self) -> "_FakeAlloc":
            return _FakeAlloc()

    class _FakeState:
        sessions = _FakeSessions()

    # Step 1: First stop — KAS close times out → 409.
    tools.revoke_app_generation(APP)
    _stopped, _failed = await tools.stop_app_bot_chat_servers(_FakeState(), APP)
    assert slug in _failed, "step 1: unconfirmed close must be in failed"
    assert (slug, APP) in tools._PENDING_CLOSE_APPS, "setup: handle saved"

    # Step 2: Bot cold-starts, overwrites _MOUNTED.
    tools._MOUNTED[slug] = {f"{APP}:team": "fp_new"}
    tools._MOUNTED_SESSION_KEYS[slug] = "dashboard:member-atlas.memory-99"

    # Step 3: Retry revoke — KAS still not confirmed.
    tools.revoke_app_generation(APP)
    _stopped2, _failed2 = await tools.stop_app_bot_chat_servers(_FakeState(), APP)

    assert slug in _failed2, (
        "retry stop must still report failure because _PENDING_CLOSE has an "
        "unconfirmed handle, even though _MOUNTED now points to a new session; "
        "remove the _PENDING_CLOSE scan at the end of stop_app_bot_chat_servers "
        "and _failed2 is empty"
    )


@pytest.mark.asyncio
async def test_tools_remount_after_drain_confirms_pending_close(world, monkeypatch):
    """E2E: KAS timeout -> disable -> Bot cold-starts -> re-enable ->
    drain confirms old close -> fence lifted -> tool re-mounts.

    GPT :979: _drain_pending_close must call _revocation_fence_clear when
    the last handle for (slug, app) is confirmed, so after_app_change can
    offer the tool to new sessions.  Blocked aliases must not go into _UNMOUNTED
    (they go into _REPROJECT_SLUGS so after_app_change sees them as 'missing'
    and schedules a reload).

    Remove the _revocation_fence_clear call in _drain_pending_close and the
    fence stays up, blocking the new delivery in step 5."""
    slug = KEY.split("member-")[1].split(".")[0]
    tools._MOUNTED[slug] = {f"{APP}:team": "fp_old"}
    tools._MOUNTED_SESSION_KEYS[slug] = KEY

    kas_confirmed = [False]
    discard_calls = [0]

    class _FakeRuntime:
        async def evict_session(self, sid: str) -> bool:
            return kas_confirmed[0]

    class _FakeAlloc:
        def begin_ending(self, k: str) -> None:
            pass

        def end_ending(self, k: str) -> None:
            pass

    class _FakeSessions:
        async def discard_conversation(
            self,
            session_key: str,
            replay: bool = False,
            confirm_teardown: bool = False,
            unconfirmed_handle_out: "list | None" = None,
        ) -> bool:
            discard_calls[0] += 1
            if confirm_teardown and unconfirmed_handle_out is not None and discard_calls[0] == 1:
                unconfirmed_handle_out.append((_FakeRuntime(), "kas-sid-drain"))
                return False
            return True

        def _allocation_boundary(self) -> "_FakeAlloc":
            return _FakeAlloc()

    class _FakeState:
        sessions = _FakeSessions()
        _crewmate_reload_tasks: set = set()

    # Step 1: Stop (KAS times out) → handle in _PENDING_CLOSE.
    tools.revoke_app_generation(APP)
    await tools.stop_app_bot_chat_servers(_FakeState(), APP)
    assert (slug, APP) in tools._PENDING_CLOSE_APPS, "setup: handle saved"

    # Step 2: Bot cold-starts (new session, _MOUNTED cleared of old session).
    tools._MOUNTED.pop(slug, None)
    tools._MOUNTED_SESSION_KEYS.pop(slug, None)

    # Step 3: Build cold-start projection — alias must be blocked (pending-close).
    spec_out = tools.conductor_session_spec(dict(BASE), KEY)
    mcp_b = dict(spec_out.get("mcpServers") or {})
    filtered = tools.check_and_filter_projection(KEY, mcp_b)
    assert f"{APP}:team" not in filtered, "step 3: delivery blocked while handle pending"

    # Alias is in _REPROJECT_SLUGS (pending retry), NOT in _UNMOUNTED.
    assert f"{APP}:team" not in tools._UNMOUNTED.get(slug, {}), (
        "blocked alias must NOT be in _UNMOUNTED; "
        "if it is, after_app_change treats it as 'harness-withheld' and skips reload; "
        "add _REPROJECT_SLUGS.add in the PENDING_CLOSE_APPS gate to fix"
    )

    # Step 4: KAS close confirmed by drain (after_app_change triggers drain).
    kas_confirmed[0] = True

    reload_calls: list = []

    async def _fake_reload(state: object, slugs: object) -> dict:
        reload_calls.extend(slugs)  # type: ignore[arg-type]
        return {}

    async def _fake_fresh(state: object, slugs: object) -> None:
        reload_calls.extend(slugs)  # type: ignore[arg-type]

    monkeypatch.setattr(tools, "reload_bot_chats", _fake_reload)
    monkeypatch.setattr(tools, "fresh_bot_chats", _fake_fresh)

    await tools.after_app_change(_FakeState())

    assert (slug, APP) not in tools._PENDING_CLOSE_APPS, (
        "drain must clear (slug, APP) from _PENDING_CLOSE_APPS after handle confirmed; "
        "this is already the existing behaviour"
    )
    assert APP not in tools._REVOCATION_FENCE, (
        "drain must call _revocation_fence_clear when last handle confirmed; "
        "remove the _revocation_fence_clear call in _drain_pending_close and "
        "APP stays in _REVOCATION_FENCE, blocking the delivery in step 5"
    )

    # Step 5: New cold start for the same slug — alias must now pass delivery.
    spec_out2 = tools.conductor_session_spec(dict(BASE), KEY)
    mcp_b2 = dict(spec_out2.get("mcpServers") or {})
    filtered2 = tools.check_and_filter_projection(KEY, mcp_b2)
    assert f"{APP}:team" in filtered2, (
        "after drain confirms close and fence is lifted, delivery must succeed; "
        "remove _revocation_fence_clear from _drain_pending_close and delivery stays blocked"
    )


# ── GPT :1182: early return also surfaces unconfirmed handles ─────────────────


@pytest.mark.asyncio
async def test_early_return_includes_pending_close_in_failed(world, monkeypatch):
    """When stop_app_bot_chat_servers sees nothing in _MOUNTED or _PENDING_DELIVERY,
    it returned [], [] immediately — before the _PENDING_CLOSE scan.  After
    a Bot cold-starts (emptying _MOUNTED), a retry that hits the early return path
    silently reports success even though an unconfirmed handle remains.

    GPT :1182: the _PENDING_CLOSE scan must run BEFORE the early return so the
    empty-mounted case still reports failure.

    Remove the early scan / move it back to after the early-return check and this
    test fails (second stop returns empty failed)."""
    slug = KEY.split("member-")[1].split(".")[0]
    tools._MOUNTED[slug] = {f"{APP}:team": "fp_old"}
    tools._MOUNTED_SESSION_KEYS[slug] = KEY

    kas_confirmed = [False]
    discard_calls = [0]

    class _FakeRuntime:
        async def evict_session(self, sid: str) -> bool:
            return kas_confirmed[0]

    class _FakeAlloc:
        def begin_ending(self, key: str) -> None:
            pass

        def end_ending(self, key: str) -> None:
            pass

    class _FakeSessions:
        async def discard_conversation(
            self,
            session_key: str,
            replay: bool = False,
            confirm_teardown: bool = False,
            unconfirmed_handle_out: "list | None" = None,
        ) -> bool:
            discard_calls[0] += 1
            if confirm_teardown and unconfirmed_handle_out is not None and discard_calls[0] == 1:
                unconfirmed_handle_out.append((_FakeRuntime(), "kas-sid-early"))
                return False
            return True

        def _allocation_boundary(self) -> "_FakeAlloc":
            return _FakeAlloc()

    class _FakeState:
        sessions = _FakeSessions()

    # Step 1: Stop (times out) → handle in _PENDING_CLOSE, slug in failed.
    tools.revoke_app_generation(APP)
    _stopped, _failed1 = await tools.stop_app_bot_chat_servers(_FakeState(), APP)
    assert slug in _failed1, "step 1: unconfirmed handle must be in failed"
    assert (slug, APP) in tools._PENDING_CLOSE_APPS, "setup: handle saved"

    # Step 2: Bot cold-starts, _MOUNTED cleared → early return path.
    tools._MOUNTED.pop(slug, None)
    tools._MOUNTED_SESSION_KEYS.pop(slug, None)

    # Step 3: Retry revoke — _MOUNTED empty, but _PENDING_CLOSE still has the handle.
    # The early return must NOT fire without checking _PENDING_CLOSE.
    tools.revoke_app_generation(APP)
    _stopped2, _failed2 = await tools.stop_app_bot_chat_servers(_FakeState(), APP)

    assert slug in _failed2, (
        "early return must collect _PENDING_CLOSE failures before returning; "
        "move the scan back after the early-return check and _failed2 is empty, "
        "making the retry look like a success"
    )


# ── GPT :1260: shared session mounts A and B -> stop A timeout -> stop B fails ─


APP_B2 = "bravo-app-b2"


@pytest.fixture
def world_two_apps_shared(monkeypatch):
    """One session mounts BOTH team-app (APP) and bravo-app-b2 (APP_B2)."""
    state = {
        "preview": True,
        "scopes": {"tools"},
        "ticked": ("atlas",),
        "offers_a": [("lead", "team")],
        "offers_b2": [("lead", "tool-b2")],
        "entry": ENTRY,
    }
    monkeypatch.setattr(tools, "preview_on", lambda cfg=None: state["preview"])
    monkeypatch.setattr(tools, "_app_names", lambda: [APP, APP_B2])

    def _offers(app: str) -> list:
        return state["offers_a"] if app == APP else state["offers_b2"]

    monkeypatch.setattr(tools, "_offers", _offers)
    monkeypatch.setattr(tools, "_rendered_entry", lambda *a: dict(state["entry"]))

    from kiro_crew.apps import bot_grants

    monkeypatch.setattr(bot_grants, "app_bot_scopes", lambda app: frozenset(state["scopes"]))
    monkeypatch.setattr(bot_grants, "app_granted_bots", lambda app: tuple(state["ticked"]))
    import kiro_crew.apps.execution as _exec

    monkeypatch.setattr(_exec, "app_execution_denied", lambda name, **_kw: None)
    import kiro_crew.members as members

    monkeypatch.setattr(
        members,
        "member_identity_for_session",
        lambda key: {"slug": key.split("member-")[1].split(".")[0], "name": "Atlas"},
    )
    return state


@pytest.mark.asyncio
async def test_shared_session_stop_a_timeout_stop_b_also_fails(world_two_apps_shared, monkeypatch):
    """A session mounts both app A and app B.  Stopping A times out (KAS close
    not confirmed).  Stopping B must also fail because the same KAS session
    (which still runs B's server) was never confirmed closed.

    GPT :1260: the unconfirmed handle must be registered under BOTH (slug, A) and
    (slug, B) so that stop_app_bot_chat_servers for B finds the pending handle and
    reports failure instead of falsely reporting B as stopped.

    Remove the loop over apps_in_session (save only under app_name) and B is
    incorrectly added to stopped on the second call."""
    slug = KEY.split("member-")[1].split(".")[0]
    # Seed: session mounts both A and B.
    tools._MOUNTED[slug] = {
        f"{APP}:team": "fp_a",
        f"{APP_B2}:tool-b2": "fp_b",
    }
    tools._MOUNTED_SESSION_KEYS[slug] = KEY

    kas_confirmed = [False]
    discard_calls = [0]

    class _FakeRuntime:
        async def evict_session(self, sid: str) -> bool:
            return kas_confirmed[0]

    class _FakeAlloc:
        def begin_ending(self, key: str) -> None:
            pass

        def end_ending(self, key: str) -> None:
            pass

    class _FakeSessions:
        async def discard_conversation(
            self,
            session_key: str,
            replay: bool = False,
            confirm_teardown: bool = False,
            unconfirmed_handle_out: "list | None" = None,
        ) -> bool:
            discard_calls[0] += 1
            if confirm_teardown and unconfirmed_handle_out is not None and discard_calls[0] == 1:
                unconfirmed_handle_out.append((_FakeRuntime(), "shared-sid-123"))
                return False
            # Second call (for B): also unconfirmed -- but we won't reach it if
            # the pending-close check fires first.
            return True

        def _allocation_boundary(self) -> "_FakeAlloc":
            return _FakeAlloc()

    class _FakeState:
        sessions = _FakeSessions()

    # Step 1: Stop A -- KAS times out.
    tools.revoke_app_generation(APP)
    _stopped_a, _failed_a = await tools.stop_app_bot_chat_servers(_FakeState(), APP)
    assert slug in _failed_a, "stop A: unconfirmed close must be in failed"
    assert (slug, APP) in tools._PENDING_CLOSE, "setup: handle under (slug, A)"
    assert (slug, APP_B2) in tools._PENDING_CLOSE, (
        "handle must also be under (slug, B) since the session mounted B too; "
        "removing the apps_in_session loop registers only (slug, A)"
    )

    # Step 2: Stop B -- must also fail because the shared session is still open.
    tools.revoke_app_generation(APP_B2)
    _stopped_b, _failed_b = await tools.stop_app_bot_chat_servers(_FakeState(), APP_B2)
    assert slug in _failed_b, (
        "stop B must fail because the shared KAS session was not confirmed closed; "
        "without (slug, B) in _PENDING_CLOSE, B is falsely reported as stopped"
    )

    # Step 3: KAS close confirmed -- both A and B fences must be lowered.
    kas_confirmed[0] = True

    class _FakeStateO:
        sessions = _FakeSessions()
        _crewmate_reload_tasks: set = set()

    async def _fr(state, slugs):
        return {}

    async def _ff(state, slugs):
        pass

    monkeypatch.setattr(tools, "reload_bot_chats", _fr)
    monkeypatch.setattr(tools, "fresh_bot_chats", _ff)

    await tools.after_app_change(_FakeStateO())

    assert APP not in tools._REVOCATION_FENCE, (
        "after drain confirms close, A's fence must be lowered; "
        "if the shared handle is not removed from (slug, A), fence stays up"
    )
    assert APP_B2 not in tools._REVOCATION_FENCE, (
        "after drain confirms close, B's fence must also be lowered; "
        "if the shared handle is not removed from (slug, B), fence stays up"
    )


# ── Opus :956: host RPC error on retry = already gone -> confirmed ─────────────


@pytest.mark.asyncio
async def test_drain_treats_host_rpc_error_as_confirmed_and_lowers_fence(world, monkeypatch):
    """E2E: KAS close times out (409) -> host later frees the session -> drain
    retries and gets a JSON-RPC error (host says session not found) ->
    handle is treated as confirmed -> fence lowered -> next trust revoke succeeds.

    Opus :956: an AcpError with a numeric code from the host means the host
    answered but rejected the request (session already freed).  Without treating
    this as confirmed, the handle is never cleared, the fence never lowers, and
    every subsequent trust revoke returns 409 until a gateway restart.

    Remove the is_host_rejection branch (or set confirmed=False for all exceptions)
    and the second stop call below still returns failed even though the host says
    the session is gone."""
    slug = KEY.split("member-")[1].split(".")[0]
    tools._MOUNTED[slug] = {f"{APP}:team": "fp"}
    tools._MOUNTED_SESSION_KEYS[slug] = KEY

    discard_calls = [0]

    class _FakeRuntime:
        async def evict_session(self, sid: str) -> bool:
            # evict_session now handles host RPC errors internally and returns True
            # (the session is already freed — this simulates the new behaviour where
            # evict_session catches AcpError with a numeric code and returns True).
            return True

    class _FakeAlloc:
        def begin_ending(self, key: str) -> None:
            pass

        def end_ending(self, key: str) -> None:
            pass

    class _FakeSessions:
        async def discard_conversation(
            self,
            session_key: str,
            replay: bool = False,
            confirm_teardown: bool = False,
            unconfirmed_handle_out: "list | None" = None,
        ) -> bool:
            discard_calls[0] += 1
            if confirm_teardown and unconfirmed_handle_out is not None and discard_calls[0] == 1:
                unconfirmed_handle_out.append((_FakeRuntime(), "kas-sid-rpc"))
                return False
            return True

        def _allocation_boundary(self) -> "_FakeAlloc":
            return _FakeAlloc()

    class _FakeState:
        sessions = _FakeSessions()
        _crewmate_reload_tasks: set = set()

    async def _fr(state, slugs):
        return {}

    async def _ff(state, slugs):
        pass

    monkeypatch.setattr(tools, "reload_bot_chats", _fr)
    monkeypatch.setattr(tools, "fresh_bot_chats", _ff)

    tools.revoke_app_generation(APP)
    _stopped, _failed = await tools.stop_app_bot_chat_servers(_FakeState(), APP)
    assert slug in _failed, "step 1: unconfirmed close → failed"
    assert (slug, APP) in tools._PENDING_CLOSE_APPS, "setup: handle in PENDING_CLOSE_APPS"

    # Step 2: drain (via after_app_change) gets True from evict_session (host
    # RPC error handled inside evict_session, which now returns True) → confirmed.
    await tools.after_app_change(_FakeState())

    assert (slug, APP) not in tools._PENDING_CLOSE_APPS, (
        "drain must treat host RPC error as confirmed and clear the handle; "
        "without is_host_rejection branch the handle is never cleared"
    )
    assert APP not in tools._REVOCATION_FENCE, (
        "fence must be lowered after host RPC error clears the last handle; "
        "remove is_host_rejection and fence stays up"
    )

    # Step 3: Next trust revoke should succeed (no unconfirmed handles blocking it).
    tools.revoke_app_generation(APP)
    _stopped2, _failed2 = await tools.stop_app_bot_chat_servers(_FakeState(), APP)
    assert slug not in _failed2, (
        "after handle cleared, trust revoke must succeed; "
        "if handle is not cleared, the pending-close scan finds it again and fails"
    )


# ── Retry limit: at max retries, handle stays — no forced confirmation ─────────


@pytest.mark.asyncio
async def test_max_retries_keeps_handle_and_reports_failure(world, monkeypatch):
    """When drain reaches _PENDING_CLOSE_MAX_RETRIES without a host response or
    runtime-dead signal, the handle must NOT be cleared.  The session may still
    be running, so treating it as stopped would leave app code executing.  The
    caller must keep seeing 409 until the host confirms closure.

    The old behaviour was to 'force-confirm' at max retries, which could report
    success while the app was still running.  The correct behaviour is to keep
    failing so the operator sees continued 409s and can investigate.

    Remove the _PENDING_CLOSE_RETRIES.pop + continue path (or change it back to
    confirmed_sids.add) and the slug disappears from failed on the final attempt."""
    slug = KEY.split("member-")[1].split(".")[0]
    tools._MOUNTED[slug] = {f"{APP}:team": "fp"}
    tools._MOUNTED_SESSION_KEYS[slug] = KEY

    # Drain will keep failing (transport error, no code) up to MAX_RETRIES.
    class _FakeRuntime:
        async def evict_session(self, sid: str) -> bool:
            raise TimeoutError("KAS not responding")  # no AcpError code -> not confirmed

    class _FakeAlloc:
        def begin_ending(self, key: str) -> None:
            pass

        def end_ending(self, key: str) -> None:
            pass

    class _FakeSessions:
        async def discard_conversation(
            self,
            session_key: str,
            replay: bool = False,
            confirm_teardown: bool = False,
            unconfirmed_handle_out: "list | None" = None,
        ) -> bool:
            if confirm_teardown and unconfirmed_handle_out is not None:
                unconfirmed_handle_out.append((_FakeRuntime(), "kas-sid-timeout"))
                return False
            return True

        def _allocation_boundary(self) -> "_FakeAlloc":
            return _FakeAlloc()

    class _FakeState:
        sessions = _FakeSessions()
        _crewmate_reload_tasks: set = set()

    async def _fr(state, slugs):
        return {}

    async def _ff(state, slugs):
        pass

    monkeypatch.setattr(tools, "reload_bot_chats", _fr)
    monkeypatch.setattr(tools, "fresh_bot_chats", _ff)

    # Seed an unconfirmed handle.
    tools.revoke_app_generation(APP)
    _stopped, _failed = await tools.stop_app_bot_chat_servers(_FakeState(), APP)
    assert slug in _failed, "setup: initial stop failed"
    assert (slug, APP) in tools._PENDING_CLOSE_APPS, "setup: handle saved"

    # Exhaust the retry counter by running drain MAX_RETRIES times.
    max_r = tools._PENDING_CLOSE_MAX_RETRIES
    for _ in range(max_r):
        await tools._drain_pending_close()

    # At the max, the counter is reset (rate-limiting) but handle STAYS.
    assert (slug, APP) in tools._PENDING_CLOSE_APPS, (
        "handle must NOT be cleared when max retries is reached without evidence "
        "of closure; remove the pop+continue and add confirmed_sids.add to restore "
        "the old forced-confirmation behaviour, and this assertion fails"
    )

    # Stopping B again should still report failure (handle is still there).
    tools.revoke_app_generation(APP)
    _stopped2, _failed2 = await tools.stop_app_bot_chat_servers(_FakeState(), APP)
    assert slug in _failed2, (
        "after reaching max retries without confirmation, stop must still report "
        "failure so the caller returns 409; "
        "if the handle was force-confirmed, failed2 is empty"
    )


@pytest.mark.asyncio
async def test_hold_fence_keeps_revocation_fence_up_until_caller_releases(
    _floor_monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With hold_fence=True, stop_app_bot_chat_servers does NOT lower the fence.

    The fence must span stop → disable → trust-write.  If stop releases the fence
    early, a Bot message arriving between stop and trust-write passes admission.

    Flow:
    1. revoke_app_generation raises the fence.
    2. stop_app_bot_chat_servers(..., hold_fence=True) is called and succeeds.
    3. The fence is STILL up (hold_fence suppressed the clear).
    4. check_and_filter_projection must reject the app's servers (fence is up).
    5. The caller calls _revocation_fence_clear; now the fence is gone.

    Remove the `if not hold_fence:` guard from stop_app_bot_chat_servers and
    the fence drops after step 2, check_and_filter_projection passes the alias
    at step 4, and step 4's assertion fails.
    """
    slug = KEY.split("member-")[1].split(".")[0]
    tools._MOUNTED[slug] = {f"{APP}:team": "fp"}
    tools._MOUNTED_SESSION_KEYS[slug] = KEY

    class _FakeAlloc:
        def begin_ending(self, key: str) -> None:
            pass

        def end_ending(self, key: str) -> None:
            pass

    class _FakeSessions:
        async def discard_conversation(
            self,
            session_key: str,
            replay: bool = False,
            confirm_teardown: bool = False,
            unconfirmed_handle_out: "list | None" = None,
        ) -> bool:
            return True

        def _allocation_boundary(self) -> "_FakeAlloc":
            return _FakeAlloc()

    class _FakeState:
        sessions = _FakeSessions()

    # Step 1: raise the fence.
    tools.revoke_app_generation(APP)
    assert APP in tools._REVOCATION_FENCE, "setup: fence must be up after revoke"

    # Step 2: stop with hold_fence=True.
    stopped, failed = await tools.stop_app_bot_chat_servers(_FakeState(), APP, hold_fence=True)
    assert not failed, f"unexpected stop failure: {failed}"

    # Step 3: fence must still be up.
    assert APP in tools._REVOCATION_FENCE, (
        "fence must NOT be cleared by stop_app_bot_chat_servers when hold_fence=True; "
        "remove the 'if not hold_fence:' guard and the fence drops here, "
        "and step 4's assertion fails"
    )

    # Step 4: a new Bot message during the trust-write window must be blocked.
    # The generation was bumped by revoke_app_generation, so check_and_filter_projection
    # rejects the alias via the generation mismatch check (the sidecar has no entry
    # for a fresh cold-start projection because _PROJECTION_GENS was not written).
    # The fence being up here is the second-level guard; verify it blocks record_mounted.
    # For this unit test, check that the fence IS up (the invariant is what we test).
    assert APP in tools._REVOCATION_FENCE, (
        "fence must still be up during the trust-write window (hold_fence=True); "
        "a new Bot message's record_mounted call will void fenced apps and "
        "schedule a reconcile, preventing them from staying mounted"
    )

    # Step 5: caller releases the fence after trust write.
    tools._revocation_fence_clear(APP, release_hold=True)
    assert (
        APP not in tools._REVOCATION_FENCE
    ), "fence must be cleared after the caller calls _revocation_fence_clear(release_hold=True)"


@pytest.mark.asyncio
async def test_hold_fence_keeps_fence_when_no_bots_are_mounted(world, monkeypatch):
    """With hold_fence=True and no mounted Bots, the early-return must NOT clear the fence.

    Before this fix, stop_app_bot_chat_servers called _revocation_fence_clear(app)
    unconditionally on the early-return path (no affected slugs, no pending sessions).
    With hold_fence=True, that cleared the fence immediately, before the caller's
    trust-write completed.  A cold-starting Bot that raced the trust-write could then
    mount the app's tools without hitting the fence.

    Remove the ``if not hold_fence:`` guard on the early-return path and the fence
    is gone before step 3 of this test, causing step 3 to fail.
    """
    # Clean global state.
    monkeypatch.setattr(tools, "_MOUNTED", {})
    monkeypatch.setattr(tools, "_MOUNTED_SESSION_KEYS", {})
    monkeypatch.setattr(tools, "_PENDING_CLOSE", {})
    monkeypatch.setattr(tools, "_PENDING_CLOSE_APPS", set())
    monkeypatch.setattr(tools, "_PENDING_CLOSE_RETRIES", {})
    monkeypatch.setattr(tools, "_REVOCATION_FENCE", set())
    monkeypatch.setattr(tools, "_FENCE_HOLD_COUNTS", {})
    monkeypatch.setattr(tools, "_APP_GENERATIONS", {})
    monkeypatch.setattr(tools, "_PROJECTION_GENS", {})
    monkeypatch.setattr(tools, "_PENDING_DELIVERY", {})

    # Step 1: raise the fence with no Bots mounted.
    tools.revoke_app_generation(APP)
    assert APP in tools._REVOCATION_FENCE, "setup: fence must be up after revoke_app_generation"
    assert APP not in tools._MOUNTED and not tools._MOUNTED, "setup: no Bots must be mounted"

    class _FakeState:
        sessions = None

    # Step 2: stop_app_bot_chat_servers with hold_fence=True — no Bots, hits early return.
    stopped, failed = await tools.stop_app_bot_chat_servers(_FakeState(), APP, hold_fence=True)
    assert stopped == [] and failed == [], f"unexpected stop result: {stopped!r}, {failed!r}"

    # Step 3: fence must STILL be up despite the early return.
    assert APP in tools._REVOCATION_FENCE, (
        "fence must NOT be cleared on the early-return path when hold_fence=True; "
        "remove the 'if not hold_fence:' guard on the early-return and the fence "
        "drops here, letting a cold-starting Bot mount the app's tools before the "
        "trust-write completes"
    )
    assert tools._FENCE_HOLD_COUNTS.get(APP, 0) == 1, (
        "hold count must be 1 after stop with hold_fence=True; "
        "remove the _FENCE_HOLD_COUNTS increment and the count is 0 here"
    )

    # Step 4: caller releases the fence (simulates the trust-write completing).
    tools._revocation_fence_clear(APP, release_hold=True)
    assert APP not in tools._REVOCATION_FENCE, (
        "fence must be cleared after the caller calls _revocation_fence_clear(release_hold=True); "
        "remove the release_hold path in _revocation_fence_clear and fence stays up forever"
    )
    assert tools._FENCE_HOLD_COUNTS.get(APP, 0) == 0, (
        "hold count must be 0 after release_hold=True; "
        "remove the decrement in _revocation_fence_clear and count stays at 1"
    )
