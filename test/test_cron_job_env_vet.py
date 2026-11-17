"""A script or command cron's ``env`` values are vetted as command lines at fire time.

A job's ``env`` block reaches its script or command child, and a tool can run an env
value as a command (``GIT_SSH_COMMAND``, say). So ``vet_job_at_fire_time`` judges every
value with the same composition scan a command body gets, and refuses the run when one
fails it. An agent cron's ``env`` is untouched by this gate.
"""

from __future__ import annotations

import pytest

from kiro_crew import mcp_cron
from kiro_crew.cron import CronJob

_SSH_HELPER = "cat ~/.ssh/id_ed25519 > ~/cron-key-copy; exit 1"


@pytest.fixture(autouse=True)
def _gates(_floor_monkeypatch, named_cron_caller):
    monkeypatch = _floor_monkeypatch
    monkeypatch.setattr(mcp_cron, "_vet_cron_capability_governance", lambda **_kw: None)
    audited: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mcp_cron,
        "_audit_fire_time_decision",
        lambda job_id, scope, outcome, reason="": audited.append((scope, outcome)),
    )
    return audited


def test_the_helper_value_is_one_the_command_scan_refuses():
    assert mcp_cron._vet_shell_command(_SSH_HELPER) is not None


@pytest.mark.parametrize("kind", ["command", "script"])
def test_an_env_value_the_command_scan_refuses_fails_the_run(kind, _gates, monkeypatch):
    monkeypatch.setattr(mcp_cron, "_vet_script_file", lambda _p: None)
    monkeypatch.setattr(mcp_cron, "resolve_script_path", lambda spec, **_k: (spec, "run"))
    body = {"command": "git ls-remote origin"} if kind == "command" else {"script": "job.py:run"}
    job = CronJob(
        id="envvet", name="envvet", message="", env={"GIT_SSH_COMMAND": _SSH_HELPER}, **body
    )
    reason = mcp_cron.vet_job_at_fire_time(job)
    assert reason is not None and reason.startswith("Error:")
    assert "GIT_SSH_COMMAND" in reason
    assert ("cron_job_env", "denied") in _gates


@pytest.mark.parametrize(
    "value", ["1", "us-east-1", "prod", "/data/out", "https://api.example.com/v1"]
)
def test_a_plain_data_value_still_fires(value, _gates):
    job = CronJob(id="envok", name="envok", message="", command="df -h", env={"K6_ENV": value})
    assert mcp_cron.vet_job_at_fire_time(job) is None
    assert ("cron_job_env", "allowed") in _gates


def test_an_agent_cron_env_is_not_judged_here(_gates):
    job = CronJob(id="agent", name="agent", message="hi", env={"GIT_SSH_COMMAND": _SSH_HELPER})
    assert mcp_cron.vet_job_at_fire_time(job) is None
    assert not any(scope == "cron_job_env" for scope, _ in _gates)


def test_the_refusal_names_the_key_and_not_the_value(_gates):
    job = CronJob(
        id="envmsg",
        name="envmsg",
        message="",
        command="df -h",
        env={"GIT_SSH_COMMAND": _SSH_HELPER},
    )
    reason = mcp_cron.vet_job_at_fire_time(job)
    assert reason is not None and "id_ed25519" not in reason


def test_a_commands_allow_policy_judges_the_command_not_its_env_values(monkeypatch):
    from types import SimpleNamespace

    from kiro_crew.platform import governance_profiles

    def _allow_df_only(_scope, command, **_kw):
        return SimpleNamespace(permitted=command.startswith("df"), reason="not on the allow list")

    monkeypatch.setattr(governance_profiles, "governance_permits", _allow_df_only)
    ok = CronJob(
        id="allow1", name="allow1", message="", command="df -h", env={"REGION": "us-east-1"}
    )
    assert mcp_cron.vet_job_at_fire_time(ok) is None
    off = CronJob(id="allow2", name="allow2", message="", command="ls", env={"REGION": "us-east-1"})
    assert mcp_cron.vet_job_at_fire_time(off) is not None


def test_compact_json_is_refused_with_a_rewrite_hint_and_spaced_json_fires():
    compact = CronJob(
        id="json1", name="json1", message="", command="df -h", env={"CFG": '{"a":1,"b":2}'}
    )
    reason = mcp_cron.vet_job_at_fire_time(compact)
    assert reason is not None and "'CFG'" in reason and "rewritten" in reason
    spaced = CronJob(
        id="json2", name="json2", message="", command="df -h", env={"CFG": '{ "a": 1, "b": 2 }'}
    )
    assert mcp_cron.vet_job_at_fire_time(spaced) is None
