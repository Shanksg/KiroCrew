"""A config file that stops parsing while the gateway runs is reported in the bell.

Contract under test:
- a save that leaves ``config.json`` unparseable under a running watcher keeps the
  settings in force, leaves the file bytes alone, logs one WARNING carrying the
  line and column, and publishes one critical bell note naming the file, the line
  and the column and saying the previous settings are still in force;
- a second save with the same error publishes nothing new; a save with a
  different error replaces the note;
- a repaired file applies its values and removes the note;
- ``update_config_locked(..., on_corrupt="reset")`` (the ``config set --file``
  writer) still installs the new document over an unreadable file, and first
  logs a WARNING naming the file and the parse error and keeps the unreadable
  bytes beside it as ``config.json.unreadable``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from pathlib import Path

import pytest
from _hot_reload_helpers import write_config as _write
from config_live_helpers import cfg_file_fixture  # noqa: F401 -- fixture used by name

from kiro_crew.config.live import ConfigWatch
from kiro_crew.config.loader import KiroCrewConfig, update_config_locked
from kiro_crew.notifications import config_parse_note
from kiro_crew.notifications.bus import NotificationBus


class _State:
    """What the note publisher reads off the dashboard state."""

    def __init__(self) -> None:
        self._notification_log: list[dict] = []
        self.notification_bus = NotificationBus(sink=self._deliver)
        self.last_notification_persist: asyncio.Future[bool] | None = None
        self.pushed = 0
        self.dismiss_saves = True

    def _deliver(self, note: dict) -> None:
        self.pushed += 1
        # A keyed note replaces the stored note with the same source and key.
        self._notification_log[:] = [
            n
            for n in self._notification_log
            if not (n.get("source") == note.get("source") and n.get("key") == note.get("key"))
        ]
        self._notification_log.append(note)
        done: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        done.set_result(True)
        self.last_notification_persist = done

    async def dismiss_notification(self, *, source: str, key: str) -> tuple[bool, bool]:
        held = [
            n for n in self._notification_log if n.get("source") == source and n.get("key") == key
        ]
        if not self.dismiss_saves:
            return bool(held), False
        self._notification_log[:] = [n for n in self._notification_log if n not in held]
        return bool(held), True


def _save(path: Path, text: str, step: int) -> None:
    """Write *text* and move the mtime forward, so the watcher sees a new save."""
    path.write_text(text, encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + step * 2_000_000_000))


def _notes(state: _State) -> list[dict]:
    return [n for n in state._notification_log if n.get("key") == config_parse_note.KEY]


@pytest.mark.asyncio
async def test_a_file_torn_under_a_running_watcher_rings_once_and_clears_on_repair(
    cfg_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state = _State()
    w = ConfigWatch()
    w.prime(KiroCrewConfig.load())
    w.tear_reporter = functools.partial(config_parse_note.publish, state)

    missing_comma = '{\n  "agent": {\n    "model": "model-b"\n    "log_level": "INFO"\n  }\n}\n'
    with caplog.at_level(logging.WARNING, logger="kiro_crew.config.live"):
        _save(cfg_file, missing_comma, 1)
        assert await w.refresh_now() is None
    assert cfg_file.read_text(encoding="utf-8") == missing_comma, "the file is left alone"
    snap = w.snapshot()
    assert snap is not None and snap.agent.model == "model-a", "the previous settings stay"
    warned = [r.getMessage() for r in caplog.records if "does not parse" in r.getMessage()]
    assert len(warned) == 1 and "line 4, column 5" in warned[0]

    notes = _notes(state)
    assert len(notes) == 1
    note = notes[0]
    assert note["priority"] == "critical"
    assert str(cfg_file) in note["body"] and "line 4, column 5" in note["body"]
    assert "settings already in force" in note["body"]

    # The same error saved again rings nothing new.
    _save(cfg_file, missing_comma, 2)
    assert await w.refresh_now() is None
    assert len(_notes(state)) == 1 and state.pushed == 1

    # A different error replaces the note.
    _save(cfg_file, '{"agent": {"model": "model-b",}}', 3)
    assert await w.refresh_now() is None
    notes = _notes(state)
    assert len(notes) == 1 and state.pushed == 2
    assert "line 1, column" in notes[0]["body"] and "line 4, column 5" not in notes[0]["body"]

    # The repair applies and removes the note.
    _write(cfg_file, {"agent": {"model": "model-b", "log_level": "INFO"}})
    change = await w.refresh_now()
    assert change is not None and change.new.agent.model == "model-b"
    assert _notes(state) == []


@pytest.mark.asyncio
async def test_a_note_stored_before_a_restart_is_removed_by_the_first_whole_read(
    cfg_file: Path,
) -> None:
    """The file was repaired while no gateway ran: the new process clears the note."""
    state = _State()
    await config_parse_note.publish(state, (f"{cfg_file} has a JSON syntax error on line 1.",))
    assert len(_notes(state)) == 1
    w = ConfigWatch()
    w.prime(KiroCrewConfig.load())
    w.tear_reporter = functools.partial(config_parse_note.publish, state)
    await w.refresh_now()
    assert _notes(state) == []


@pytest.mark.asyncio
async def test_a_removal_that_was_not_saved_is_retried_on_an_unchanged_tick(
    cfg_file: Path,
) -> None:
    state = _State()
    w = ConfigWatch()
    w.prime(KiroCrewConfig.load())
    w.tear_reporter = functools.partial(config_parse_note.publish, state)
    await w.refresh_now()
    _save(cfg_file, '{"agent": {"model": "model-b",}}', 1)
    await w.refresh_now()
    assert len(_notes(state)) == 1

    state.dismiss_saves = False
    _write(cfg_file, {"agent": {"model": "model-b", "log_level": "INFO"}})
    await w.refresh_now()
    assert len(_notes(state)) == 1, "the removal was not saved"
    state.dismiss_saves = True
    assert await w.refresh_now() is None, "the file is unchanged"
    assert _notes(state) == [], "the unchanged tick retried the removal"


@pytest.mark.asyncio
async def test_a_repair_reported_while_the_note_write_is_in_flight_still_removes_it() -> None:
    """The note's write can outlast the reporter timeout; the removal waits behind it."""
    state = _State()
    loop = asyncio.get_running_loop()
    written: asyncio.Future[None] = loop.create_future()
    # The in-flight note enters the log when its write finishes.
    written.add_done_callback(
        lambda _f: state._notification_log.append(
            {"source": config_parse_note.SOURCE, "key": config_parse_note.KEY, "body": "x"}
        )
    )
    dismiss = state.dismiss_notification

    async def dismiss_after_write(*, source: str, key: str) -> tuple[bool, bool]:
        await written  # the rewrite lock the pending write holds
        return await dismiss(source=source, key=key)

    state.dismiss_notification = dismiss_after_write  # type: ignore[method-assign]
    loop.call_soon(written.set_result, None)
    await asyncio.wait_for(config_parse_note.publish(state, ()), timeout=60)
    await asyncio.wait_for(written, timeout=60)
    assert _notes(state) == []


def test_a_reset_write_keeps_the_unreadable_bytes_and_says_so(
    cfg_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    broken = '{"agent": {"model": "model-a" "x": 1}}'
    cfg_file.write_text(broken, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="kiro_crew.config.loader"):
        update_config_locked(
            cfg_file, mutate=lambda _d: {"agent": {"model": "model-c"}}, on_corrupt="reset"
        )
    assert '"model-c"' in cfg_file.read_text(encoding="utf-8"), "the new document is installed"
    kept = cfg_file.with_name("config.json.unreadable")
    assert kept.read_text(encoding="utf-8") == broken
    if os.name == "posix":
        assert kept.stat().st_mode & 0o077 == 0, "the copy is owner-only: it can hold credentials"
    warned = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "kiro_crew.config.loader"
    ]
    assert len(warned) == 1 and str(cfg_file) in warned[0] and "line 1 column 31" in warned[0]
    assert str(kept) in warned[0]


def test_a_refused_reset_write_leaves_the_earlier_copy_and_says_nothing(
    cfg_file: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kiro_crew.config import loader_document
    from kiro_crew.config.loader import ConfigWriteRefused

    kept = cfg_file.with_name("config.json.unreadable")
    kept.write_text("bytes an earlier reset replaced", encoding="utf-8")
    broken = '{"agent": {"model": "model-a" "x": 1}}'
    cfg_file.write_text(broken, encoding="utf-8")

    def refuse(*_a: object, **_k: object) -> None:
        raise ConfigWriteRefused("refused by the publish floor")

    monkeypatch.setattr(loader_document, "write_config_atomically", refuse)
    with caplog.at_level(logging.WARNING, logger="kiro_crew.config.loader"):
        with pytest.raises(ConfigWriteRefused):
            update_config_locked(
                cfg_file, mutate=lambda _d: {"agent": {"model": "model-c"}}, on_corrupt="reset"
            )
    assert cfg_file.read_text(encoding="utf-8") == broken
    assert kept.read_text(encoding="utf-8") == "bytes an earlier reset replaced"
    assert not cfg_file.with_name("config.json.unreadable.staged").exists()
    assert not [r for r in caplog.records if "kept its previous bytes" in r.getMessage()]


@pytest.mark.asyncio
async def test_the_gateway_points_the_process_watcher_at_the_bell(cfg_file: Path) -> None:
    from unittest.mock import MagicMock

    from aiohttp import web

    from kiro_crew.config import live
    from kiro_crew.dashboard.server import _register_config_watch

    state = _State()
    state.set_dynamic_cards_enabled = MagicMock()  # type: ignore[attr-defined]
    _register_config_watch(web.Application(), state, None)  # type: ignore[arg-type]
    reporter = live.watch().tear_reporter
    assert reporter is not None
    await reporter((f"{cfg_file} has a JSON syntax error on line 2, column 3: x.",))
    assert len(_notes(state)) == 1
    await reporter(())
    assert _notes(state) == []
