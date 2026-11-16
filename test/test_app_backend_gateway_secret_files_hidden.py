"""An app backend's sandbox cannot read the gateway's internal-API secret files.

``.local_secret`` in the data home and every ``run/gateway-<port>.secret`` and
``run/gateway-<port>-<addr>.secret`` hold the same value. The spawn of an app backend
passes them to ``wrap_argv`` as ``extra_hidden_dirs``, so the sandbox plan masks them.
Every spawned backend gets the same list. The Sage app declares no backend process: its
routes run inside the gateway, so nothing of it is spawned and nothing needs an exception.

Only the arguments of the spawn and the sandbox plan are checked; no sandbox and no
process runs here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from app_backend_helpers import app_env_fixture  # noqa: F401
from test_sandbox_launcher_program import CoveringLibc, launch, payload

from kiro_crew import sandbox, sandbox_launcher_program, sandbox_plan
from kiro_crew.apps.manager import APP_MANIFEST_FILENAME, install_app

_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX sandbox only")
_SAGE = "code-review-sage"
_SECRET_NAMES = (
    ".local_secret",
    "run/gateway-8765.secret",
    "run/gateway-8765-127.0.0.1.secret",
)
_NOT_SECRET_NAMES = ("run/gateway-8765.pid", "run/gateway-8765.bin", "other.json")


def _seed(home: Path) -> None:
    (home / "run").mkdir(exist_ok=True)
    for name in (*_SECRET_NAMES, *_NOT_SECRET_NAMES):
        (home / name).write_text("x", encoding="utf-8")


def _spy_spawn(bmod, monkeypatch) -> dict:
    seen: dict = {}

    def _spy_wrap(argv, **kwargs):
        seen["hidden"] = tuple(kwargs.get("extra_hidden_dirs") or ())
        seen["visible"] = tuple(kwargs.get("extra_visible_dirs") or ())
        return (list(argv), None)

    monkeypatch.setattr(bmod, "wrap_argv", _spy_wrap)
    monkeypatch.setattr(
        bmod.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("stop"))
    )
    return seen


def _install_plain(tmp_path: Path, name: str) -> None:
    src = tmp_path / "source" / name
    src.mkdir(parents=True)
    (src / APP_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": "Plain",
                "description": "a backend",
                "backend": {"entryPoint": "server.py", "healthCheck": "/health"},
            }
        )
    )
    (src / "server.py").write_text("import time\ntime.sleep(30)\n")
    install_app(src)


class TestTheBackendSpawnHidesTheSecretFiles:
    def test_a_third_party_backend_hides_every_secret_file(self, app_env, tmp_path, monkeypatch):
        import kiro_crew.apps.backend as bmod

        _seed(app_env)
        seen = _spy_spawn(bmod, monkeypatch)
        _install_plain(tmp_path, "plain-app")

        bmod.start_app_backend("plain-app")

        for name in _SECRET_NAMES:
            assert str(app_env / name) in seen["hidden"], f"{name} is readable by the backend"

    def test_only_the_secret_files_are_hidden(self, app_env, tmp_path, monkeypatch):
        import kiro_crew.apps.backend as bmod

        _seed(app_env)
        seen = _spy_spawn(bmod, monkeypatch)
        _install_plain(tmp_path, "plain-app")

        bmod.start_app_backend("plain-app")

        assert seen["hidden"], "the backend hides nothing"
        for name in _NOT_SECRET_NAMES:
            assert str(app_env / name) not in seen["hidden"]
        assert str(app_env / "run") not in seen["hidden"]
        assert str(app_env) not in seen["hidden"]

    def test_a_shipped_builtin_other_than_sage_hides_them_too(self, app_env, monkeypatch):
        import kiro_crew.apps.backend as bmod
        from kiro_crew.apps.manager import register_builtin_apps

        _seed(app_env)
        register_builtin_apps()
        seen = _spy_spawn(bmod, monkeypatch)

        bmod.start_app_backend("file-explorer")

        for name in _SECRET_NAMES:
            assert str(app_env / name) in seen["hidden"]

    def test_an_installed_app_wearing_a_builtin_name_hides_them(
        self, app_env, tmp_path, monkeypatch
    ):
        """No name, shipped or not, buys back the files."""
        import kiro_crew.apps.backend as bmod

        _seed(app_env)
        seen = _spy_spawn(bmod, monkeypatch)
        _install_plain(tmp_path, _SAGE)

        bmod.start_app_backend(_SAGE)

        assert seen, "the impostor never reached the sandbox wrap"
        for name in _SECRET_NAMES:
            assert str(app_env / name) in seen["hidden"], f"{name} given to a name-only claim"


@_POSIX_ONLY
class TestTheTargetsAndThePlan:
    def test_the_targets_list_only_files_that_exist(self, app_env):
        (app_env / "run").mkdir()
        (app_env / "run" / "gateway-1.secret").write_text("x", encoding="utf-8")

        targets = sandbox.app_backend_secret_mask_targets()

        assert str(app_env / "run" / "gateway-1.secret") in targets
        assert str(app_env / ".local_secret") not in targets

    def test_the_backend_plan_masks_the_files_and_the_plain_plan_does_not(self, app_env):
        _seed(app_env)
        targets = sandbox.app_backend_secret_mask_targets()
        backend_plan = sandbox._spawn_plan(
            sandbox_plan.BACKEND_NAMESPACE, "standard", extra_hidden_dirs=targets
        )
        plain_plan = sandbox._spawn_plan(sandbox_plan.BACKEND_NAMESPACE, "standard")

        masked = set(backend_plan.sensitive_dirs)
        for name in _SECRET_NAMES:
            assert str(app_env / name) in masked
        assert str(app_env / "run") not in masked
        # A spawn that passes no hidden paths keeps today's plan.
        for name in _SECRET_NAMES:
            assert str(app_env / name) not in set(plain_plan.sensitive_dirs)


@_POSIX_ONLY
class TestTheLauncherMasksAFileEntry:
    """A regular file in the hidden entries is bound over, not skipped as a non-directory.

    The plan offers every hidden path to both launcher loops (``sensitive_files`` is the
    files plus every masked tree), and each loop classifies the entry itself. The fake
    libc covers a bound name the way a mount does; no namespace and no process run.
    """

    def test_a_listed_regular_file_reads_empty_and_its_sibling_is_untouched(
        self, tmp_path: Path
    ) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        secret = run_dir / "gateway-8765.secret"
        secret.write_text("value", encoding="utf-8")
        sibling = run_dir / "gateway-8765.pid"
        sibling.write_text("42", encoding="utf-8")
        entry = str(secret)
        run = launch(
            tmp_path,
            payload(sensitive_dirs=[entry], sensitive_files=[entry]),
            libc=CoveringLibc(),
        )

        sandbox_launcher_program.mask_sensitive(run)
        sandbox_launcher_program.mask_sensitive_files(run)

        assert secret.read_text(encoding="utf-8") == ""
        assert sibling.read_text(encoding="utf-8") == "42"
