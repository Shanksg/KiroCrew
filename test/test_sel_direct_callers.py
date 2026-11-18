"""Backend-run SEL callers log through ``sel()``, which is the gateway client in a backend.

In a spawned app backend the SEL signing key is masked, so building a
``SecurityEventLog`` directly fails there. Each caller below must reach the log through
``sel()``: the real ``SelClient`` posts the row to the real gateway route, the row is
app-attributed ``api_access``, and a deny exception is still granted (no fail-closed
loss) because its row is taken by the gateway.
"""

# flake8: noqa: F811  -- ``rec`` and ``gateway`` are fixtures imported from the sel route tests
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_sel_client import gateway  # noqa: F401  (the real route and token exchange)
from test_sel_routes import _A, rec  # noqa: F401  (``rec`` is the route tests' fixture)

from kiro_crew.acp import mcp_mount_check
from kiro_crew.apps import sel_client
from kiro_crew.security import deny_gate, exfil_oauth
from kiro_crew.sel import sel as _real_sel  # the ``rec`` fixture replaces ``kiro_crew.sel.sel``


class _MaskedKey:
    """The direct constructor, as a backend with the key masked sees it."""

    built = 0

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        type(self).built += 1
        raise RuntimeError("sel_hmac.key unreadable")


@pytest.fixture
def backend_env(monkeypatch: pytest.MonkeyPatch, rec):
    """Mask the key; return a setter that points the spawn env at a gateway."""
    _MaskedKey.built = 0
    monkeypatch.setattr("kiro_crew.sel.SecurityEventLog", _MaskedKey)
    monkeypatch.setattr("kiro_crew.sel.sel", _real_sel)  # call-time importers (mount check)

    def point_at(client: TestClient, secret: str = "per-app-secret") -> None:
        monkeypatch.setenv("KIROCREW_SPAWNED", "1")
        monkeypatch.setenv("KIROCREW_APP_NAME", _A)
        monkeypatch.setenv("KIROCREW_GATEWAY_ORIGIN", str(client.make_url("")).rstrip("/"))
        monkeypatch.setenv("KIROCREW_PROXY_SECRET", secret)

    return point_at


def _rows(rec, event_type: str) -> list[dict[str, Any]]:
    return [kw for _n, kw in rec.app_rows() if kw["operation"] == f"{_A}.security.{event_type}"]


@pytest.mark.asyncio
async def test_deny_exception_is_granted_with_the_key_masked(gateway, rec, backend_env) -> None:
    async with TestClient(TestServer(gateway)) as client:
        backend_env(client)
        granted = await asyncio.to_thread(deny_gate._emit_deny_exception_event, "Bash", "rm -rf")
    assert granted is True
    rows = _rows(rec, "deny_exception")
    assert [(kw["caller"], kw["outcome"]) for kw in rows] == [(f"app:{_A}", "allowed")]
    assert _MaskedKey.built == 0


@pytest.mark.asyncio
async def test_deny_exception_is_refused_when_the_gateway_does_not_take_the_row(
    gateway, rec, backend_env
) -> None:
    async with TestClient(TestServer(gateway)) as client:
        backend_env(client, secret="wrong-secret")
        granted = await asyncio.to_thread(deny_gate._emit_deny_exception_event, "Bash", "rm -rf")
    assert granted is False
    assert _rows(rec, "deny_exception") == []


@pytest.mark.asyncio
async def test_the_other_backend_run_callers_land_as_app_api_access_rows(
    gateway, rec, backend_env
) -> None:
    def emit_all() -> None:
        deny_gate._emit_deny_event("Bash", "rm -rf", "rm -rf /")
        deny_gate._emit_push_allow_event("git push origin feature")
        deny_gate.audit_injection_dropped(surface="slack_thread_parent")
        exfil_oauth._emit_oauth_discovered_endpoint_event("idp.example", "/authorize", "https://x")
        exfil_oauth._log_operator_list_use("oauth_endpoint_extension_used", "o", "r", lambda: {})
        mcp_mount_check._audit_missing("goose", "sid", "skey", ["srv"])
        sel_client.from_env()._queue.join()  # the non-critical rows are sent from a thread

    async with TestClient(TestServer(gateway)) as client:
        backend_env(client)
        await asyncio.to_thread(emit_all)
    expected = [
        "deny_event",
        "push_allowed",
        "prompt_injection_dropped",
        "oauth_endpoint_discovered",
        "oauth_endpoint_extension_used",
    ]
    for event_type in expected:
        assert [kw["caller"] for kw in _rows(rec, event_type)] == [f"app:{_A}"], event_type
    assert [kw["caller"] for kw in _rows(rec, mcp_mount_check._EVENT_TYPE)] == [f"app:{_A}"]
    assert {name for name, _kw in rec.rows} == {"log_api_access"}  # no new event kind
    assert _MaskedKey.built == 0
