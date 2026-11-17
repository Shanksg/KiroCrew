"""A restore never installs the archive's host-local workspace root or session map.

``workspace_dir`` roots every session work directory, outbox and upload root, and
``session_map.json`` holds the working folder a resumed session starts in. Both name paths
on the machine that made the archive, so an archive from someone else must not set them
here, in replace mode or in merge mode, whether this install already has the file or not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import snapshot_helpers
from snapshot_helpers import unpinnable_argv
from test_portability_home_paths import homes as _portability_homes
from test_snapshot import _make_snapshot, _setup_fake_kirocrew

from kiro_crew import portability
from kiro_crew.portability import apply_import_zip, create_export_zip
from kiro_crew.snapshot import restore_main

_no_gateway = snapshot_helpers._no_gateway
homes = _portability_homes  # the import tests below use that module's two-homes fixture

_ARCHIVE_WORKSPACE = "/archive-host/elsewhere/workspace\n"
_ARCHIVE_SESSION_MAP = '{"chat-1": {"cwd": "/archive-host/elsewhere/project"}}'
_LIVE_WORKSPACE = "/live-host/workspace\n"
_LIVE_SESSION_MAP = '{"chat-2": {"cwd": "/live-host/project"}}'


@pytest.fixture
def tarball(tmp_path, monkeypatch):
    """An archive whose workspace_dir and session_map.json name another host's paths."""
    src = tmp_path / "src"
    _setup_fake_kirocrew(src)
    (src / "workspace_dir").write_text(_ARCHIVE_WORKSPACE)
    (src / "session_map.json").write_text(_ARCHIVE_SESSION_MAP)
    monkeypatch.setenv("KIROCREW_HOME", str(src))
    return _make_snapshot(src, tmp_path / "out")


def _restore(tarball, mode):
    args = [str(tarball), "--mode", mode, "--force", "--components", "config"]
    return restore_main(args + unpinnable_argv())


def _live_home(tmp_path, monkeypatch):
    live = tmp_path / "live"
    _setup_fake_kirocrew(live)
    (live / "workspace_dir").write_text(_LIVE_WORKSPACE)
    (live / "session_map.json").write_text(_LIVE_SESSION_MAP)
    monkeypatch.setenv("KIROCREW_HOME", str(live))
    return live


def _empty_home(tmp_path, monkeypatch):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setenv("KIROCREW_HOME", str(fresh))
    return fresh


@pytest.mark.parametrize("mode", ["replace", "merge"])
def test_the_live_copies_are_kept(tarball, tmp_path, monkeypatch, mode):
    live = _live_home(tmp_path, monkeypatch)
    _restore(tarball, mode)
    assert (live / "workspace_dir").read_text() == _LIVE_WORKSPACE
    assert (live / "session_map.json").read_text() == _LIVE_SESSION_MAP


@pytest.mark.parametrize("mode", ["replace", "merge"])
def test_nothing_is_created_when_the_install_has_none(tarball, tmp_path, monkeypatch, mode):
    fresh = _empty_home(tmp_path, monkeypatch)
    _restore(tarball, mode)
    assert not (fresh / "workspace_dir").exists()
    assert not (fresh / "session_map.json").exists()


def test_replace_still_installs_the_settings_files(tarball, tmp_path, monkeypatch):
    fresh = _empty_home(tmp_path, monkeypatch)
    _restore(tarball, "replace")
    assert (fresh / "config.json").is_file()
    assert (fresh / "hooks.json").is_file()


def test_the_summary_names_what_was_kept(tarball, tmp_path, monkeypatch, capsys):
    _live_home(tmp_path, monkeypatch)
    _restore(tarball, "replace")
    out = capsys.readouterr().out
    said = "not restored (the backup's copy points at the machine that made it); this install's"
    assert f"workspace_dir: {said} copy is kept" in out
    assert f"session_map.json: {said} copy is kept" in out


def test_the_summary_does_not_claim_a_copy_was_kept_when_there_was_none(
    tarball, tmp_path, monkeypatch, capsys
):
    _empty_home(tmp_path, monkeypatch)
    _restore(tarball, "replace")
    out = capsys.readouterr().out
    assert "workspace_dir: not restored (the backup's copy points at the machine" in out
    assert "copy is kept" not in out


# The dashboard import moves the archive's saved folders under THIS home first, so it may
# install the workspace root when the moved folder is an ordinary one under this home.


def _import_replace(homes, tmp_path, monkeypatch, folder):
    """Export an install whose workspace_dir names *folder*, replace-import it into another."""
    old_home, new_home, _ = homes
    (old_home / ".kiro" / "crew" / "workspace_dir").write_text(f"{folder}\n")
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(create_export_zip(include_sessions=True)[0])
    target = new_home / ".kiro" / "crew"
    target.mkdir(parents=True)
    (target / "workspace_dir").write_text(_LIVE_WORKSPACE)
    monkeypatch.setenv("HOME", str(new_home))
    monkeypatch.setenv("KIROCREW_HOME", str(target))
    return target, apply_import_zip(zip_path, mode="replace")


def _not_installed(summary):
    return [i for i in summary["items"] if i.startswith("workspace_dir (not installed")]


_OUTSIDE_HOME = "it is not a folder inside this home"
_NOT_PLAIN = "the saved folder is not a plain absolute path"
_HOLDS_DATA_HOME = "it is the data home or a folder holding it"
_PROTECTED = "it is a protected folder"


@pytest.mark.skipif(sys.platform == "win32", reason="Home relocation is POSIX-only")
@pytest.mark.parametrize(
    ("where", "why"),
    [
        ("link_to_etc", _OUTSIDE_HOME),
        ("ssh", _PROTECTED),
        ("outside_home", _OUTSIDE_HOME),
        ("home_itself", _OUTSIDE_HOME),
        ("data_home", _HOLDS_DATA_HOME),
        ("data_home_parent", _HOLDS_DATA_HOME),
        ("relative", _NOT_PLAIN),
        ("dot_dot", _NOT_PLAIN),
        ("embedded_nul", _NOT_PLAIN),
    ],
)
def test_import_keeps_the_live_workspace_dir_for_an_unsafe_folder(
    homes, tmp_path, monkeypatch, where, why
):
    old_home, new_home, _ = homes
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (new_home / "link").symlink_to("/etc")
    folder = {
        "link_to_etc": old_home / "link",
        "ssh": old_home / ".ssh",
        "outside_home": outside,
        "home_itself": old_home,
        "data_home": old_home / ".kiro" / "crew",
        "data_home_parent": old_home / ".kiro",
        "relative": "work/project",
        "dot_dot": f"{outside}/../outside-dir",
        "embedded_nul": f"{old_home}/work\x00x",
    }[where]

    target, summary = _import_replace(homes, tmp_path, monkeypatch, folder)

    assert (target / "workspace_dir").read_text() == _LIVE_WORKSPACE
    assert any(why in item for item in _not_installed(summary)), summary["items"]


@pytest.mark.skipif(sys.platform == "win32", reason="Home relocation is POSIX-only")
def test_import_installs_an_ordinary_folder_under_this_home(homes, tmp_path, monkeypatch):
    old_home, new_home, _ = homes

    target, summary = _import_replace(homes, tmp_path, monkeypatch, old_home / "work" / "x")

    assert (target / "workspace_dir").read_text().strip() == str(new_home / "work" / "x")
    assert not _not_installed(summary)


@pytest.mark.skipif(sys.platform == "win32", reason="Home relocation is POSIX-only")
def test_import_writes_the_resolved_folder_not_the_raw_value(homes, tmp_path, monkeypatch):
    old_home, new_home, _ = homes
    (new_home / "real").mkdir()
    (new_home / "alias").symlink_to(new_home / "real")

    target, summary = _import_replace(homes, tmp_path, monkeypatch, old_home / "alias")

    assert (target / "workspace_dir").read_text().strip() == str((new_home / "real").resolve())
    assert not _not_installed(summary)


# Nothing that can refuse may run after the replace has committed: a failure there would
# report an import as failed that has already replaced the settings and trees.


@pytest.mark.skipif(sys.platform == "win32", reason="Home relocation is POSIX-only")
def test_import_completes_when_the_staged_workspace_dir_is_a_directory(
    homes, tmp_path, monkeypatch
):
    old_home, _new_home, _ = homes
    real_move = portability._move_home_paths

    def leave_a_directory_at_the_name(snap, summary):
        cleared = real_move(snap, summary)
        (snap / "workspace_dir").unlink()
        (snap / "workspace_dir").mkdir()
        return cleared

    monkeypatch.setattr(portability, "_move_home_paths", leave_a_directory_at_the_name)

    target, summary = _import_replace(homes, tmp_path, monkeypatch, old_home / "work")

    assert (target / "workspace_dir").read_text() == _LIVE_WORKSPACE
    assert any("the saved folder is not a plain file" in i for i in _not_installed(summary))
    assert "full replace" in summary["items"]


@pytest.mark.skipif(sys.platform == "win32", reason="Home relocation is POSIX-only")
def test_import_completes_when_the_workspace_dir_cannot_be_written(homes, tmp_path, monkeypatch):
    old_home, _new_home, _ = homes
    real_write = portability.atomic_write

    def fail_for_workspace_dir(path, *args, **kwargs):
        if Path(path).name == "workspace_dir":
            raise OSError(28, "No space left on device")
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr(portability, "atomic_write", fail_for_workspace_dir)

    target, summary = _import_replace(homes, tmp_path, monkeypatch, old_home / "work")

    assert (target / "workspace_dir").read_text() == _LIVE_WORKSPACE
    assert any("not installed: could not write it" in i for i in _not_installed(summary))
    assert "full replace" in summary["items"]
