"""POST /api/chat/slots/model with ``reasoning_effort``: one pass sets the model and the effort."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from kiro_crew.dashboard.state import DashboardState, _ChatSlot
from kiro_crew.providers.acp import AcpProvider

OPUS = "claude-opus-4.8"


def _app(state: DashboardState) -> web.Application:
    from kiro_crew.dashboard.chat import api_chat_slots_model

    @web.middleware
    async def dashboard_auth_marker(request, handler):
        request["app"] = ""
        return await handler(request)

    app = web.Application(middlewares=[dashboard_auth_marker])
    app["state"] = state
    app.router.add_post("/api/chat/slots/model", api_chat_slots_model)
    return app


async def _post(state: DashboardState, body: dict) -> tuple[int, dict]:
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.post("/api/chat/slots/model", json=body)
        return resp.status, await resp.json()


def _live(*, pushed: object = True, active: bool = False) -> MagicMock:
    provider = MagicMock(spec=AcpProvider)
    provider.supports_effort.return_value = True
    provider.accepts_effort_level.return_value = True
    provider.has_active_turn.return_value = active
    provider.change_effort = AsyncMock(return_value=pushed)
    provider.clear_effort = AsyncMock(return_value=pushed)
    return provider


def _state(tmp_path, provider: object = None, *, effort: str = "low") -> tuple:
    state = _make_state(tmp_path)
    state.sessions.reset = AsyncMock()
    state.sessions.get_provider = MagicMock(return_value=provider)
    slot = state.get_or_create_slot("a", model=OPUS)
    slot.reasoning_effort = effort
    state.push_slots_update = MagicMock()
    return state, slot


def _mark_running(slot: _ChatSlot) -> None:
    task = MagicMock()
    task.done.return_value = False
    slot.task = task


@pytest.mark.asyncio
async def test_a_model_change_commits_the_level_with_the_reset(tmp_path):
    state, slot = _state(tmp_path)
    slot.model = "claude-sonnet-4.6"

    status, data = await _post(state, {"model": OPUS, "reasoning_effort": "high"})

    assert status == 200
    assert data["switched"] == ["a"]
    assert data["effort_deferred"] == []
    assert (slot.model, slot.reasoning_effort) == (OPUS, "high")
    state.sessions.reset.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_effort_only_change_pushes_live_and_never_resets(tmp_path):
    provider = _live()
    state, slot = _state(tmp_path, provider)

    status, data = await _post(state, {"model": OPUS, "reasoning_effort": "high"})

    assert status == 200
    assert data["switched"] == ["a"]
    assert data["effort_deferred"] == []
    assert slot.reasoning_effort == "high"
    provider.change_effort.assert_awaited_once_with("high")
    state.sessions.reset.assert_not_awaited()
    state.sessions.record_effort_override.assert_called_once_with("dashboard:a", "high")


@pytest.mark.asyncio
async def test_default_clears_the_override_and_never_pins_a_level(tmp_path):
    provider = _live()
    state, slot = _state(tmp_path, provider)

    status, data = await _post(state, {"model": OPUS, "reasoning_effort": ""})

    assert status == 200
    assert data["switched"] == ["a"]
    assert slot.reasoning_effort == ""
    provider.clear_effort.assert_awaited_once_with(every_model=True)
    provider.change_effort.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_push_the_session_cannot_take_live_is_recorded_and_deferred(tmp_path):
    # With no default configured, a live kiro session reaches the model's own
    # default only on a restart: the single-session control resets for it; the
    # bulk switch records it and names the session instead.
    provider = _live(pushed=False)
    state, slot = _state(tmp_path, provider)

    status, data = await _post(state, {"model": OPUS, "reasoning_effort": ""})

    assert status == 200
    assert data["switched"] == ["a"]
    assert data["effort_deferred"] == ["a"]
    assert slot.reasoning_effort == ""
    state.sessions.reset.assert_not_awaited()
    state.sessions.record_effort_override.assert_called_once_with("dashboard:a", "")


@pytest.mark.asyncio
async def test_a_running_session_is_skipped_by_default_and_deferred_when_not(tmp_path):
    provider = _live(active=True)
    state, slot = _state(tmp_path, provider)
    _mark_running(slot)

    status, data = await _post(state, {"model": OPUS, "reasoning_effort": "high"})
    assert status == 200
    assert data["skipped_running"] == ["a"]
    assert slot.reasoning_effort == "low"

    status, data = await _post(
        state, {"model": OPUS, "reasoning_effort": "high", "skip_running": False}
    )
    assert status == 200
    assert data["effort_deferred"] == ["a"]
    assert slot.reasoning_effort == "high"
    # A push now would race the streaming turn, and no turn is torn down.
    provider.change_effort.assert_not_awaited()
    state.sessions.reset.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_setup", "bucket"),
    [
        ("raises", "failed"),
        ("not_offered", "failed"),
        ("overlay_busy", "skipped_running"),
    ],
)
async def test_a_push_that_changed_nothing_leaves_the_slot_alone(tmp_path, provider_setup, bucket):
    provider = _live(pushed=None if provider_setup == "overlay_busy" else True)
    if provider_setup == "raises":
        provider.change_effort = AsyncMock(side_effect=RuntimeError("boom"))
    if provider_setup == "not_offered":
        provider.accepts_effort_level.return_value = False
    state, slot = _state(tmp_path, provider)

    effort = "" if provider_setup == "overlay_busy" else "high"
    status, data = await _post(state, {"model": OPUS, "reasoning_effort": effort})

    assert status == 200
    assert data[bucket] == ["a"]
    assert data["switched"] == []
    assert slot.reasoning_effort == "low"
    state.sessions.reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_live_session_records_the_level_for_the_next_start(tmp_path):
    state, slot = _state(tmp_path)

    status, data = await _post(state, {"model": OPUS, "reasoning_effort": "max"})

    assert status == 200
    assert data["switched"] == ["a"]
    assert slot.reasoning_effort == "max"
    state.sessions.reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_absent_level_leaves_every_effort_alone(tmp_path):
    provider = _live()
    state, slot = _state(tmp_path, provider)

    status, data = await _post(state, {"model": OPUS})

    assert status == 200
    assert data["unchanged"] == ["a"]
    assert slot.reasoning_effort == "low"
    provider.change_effort.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["turbo", 3, ["high"]])
async def test_an_invalid_level_is_refused_and_nothing_changes(tmp_path, bad):
    state, slot = _state(tmp_path)

    status, data = await _post(state, {"model": OPUS, "reasoning_effort": bad})

    assert status == 400
    assert "reasoning_effort" in data["error"]
    assert slot.reasoning_effort == "low"


@pytest.mark.asyncio
async def test_a_relay_archive_keeps_its_effort(tmp_path):
    # The effort route refuses a relay archive (409), so the bulk pass must not
    # write a level onto one either.
    state, slot = _state(tmp_path)
    slot.executor = "remote"

    status, data = await _post(state, {"model": OPUS, "reasoning_effort": "high"})

    assert status == 200
    assert data["unchanged"] == ["a"]
    assert slot.reasoning_effort == "low"
