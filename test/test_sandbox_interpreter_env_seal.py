"""A packaged gateway's interpreter environment is sealed read-only for sandboxed children.

The gateway and every Python child it starts outside the sandbox load the venv
(``site-packages``, ``.pth`` files, ``__pycache__``, ``pyvenv.cfg``), the base
stdlib and every other directory on the interpreter's search path at start. A
sandboxed child that could write them would choose code the gateway runs with its
own identity. These directories ride the install-root seal on both backends, rename
guards included. A gateway-initiated install (memory embeddings setup) lifts them
for its own spawn with ``gateway_env_writable``; the ``kiro_crew`` package directory
stays sealed even then.
"""

from __future__ import annotations

import ast
import logging
import os
import sys
import types
from pathlib import Path

import pytest

from kiro_crew import sandbox, sandbox_env_scan, sandbox_plan, sandbox_tiers
from kiro_crew.sandbox_plan import BACKEND_NAMESPACE, BACKEND_SEATBELT, SandboxRequest
from kiro_crew.testing.wait import FALLBACK_TIMEOUT_SECS

_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths only")

ENV = "/opt/crew-venv"
BASE = "/opt/python/3.12.13"
INSTALL = f"{ENV}/lib/python3.12/site-packages/kiro_crew"


def _env_targets(
    tmp_path: Path,
    prefixes: tuple[str, ...],
    homes=("/nonexistent-home",),
    path_dirs: tuple[str, ...] = (),
    site_dirs: tuple[str, ...] = (),
):
    package = tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "kiro_crew"
    package.mkdir(parents=True, exist_ok=True)
    return sandbox._interpreter_env_targets_cached.__wrapped__(
        str(package), prefixes, path_dirs, site_dirs, homes
    )


@pytest.fixture
def no_conda_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No conda setting from the host leaks into a case: a fake home, no env vars."""
    for var in ("CONDA_PKGS_DIRS", "CONDA_ENVS_PATH", "CONDA_ENVS_DIRS", "CONDARC"):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home


# --------------------------------------------------------------------------- #
# Which prefixes are sealed.
# --------------------------------------------------------------------------- #


@_POSIX_ONLY
def test_the_four_interpreter_prefixes_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dirs = {name: tmp_path / name for name in ("p", "ep", "bp", "bep")}
    for path in dirs.values():
        path.mkdir()
    # The real sys module's attributes, not a stand-in module: sys is bound by more
    # than one sandbox owner, so it is never patched on one of them.
    monkeypatch.setattr(sys, "prefix", str(dirs["p"]))
    monkeypatch.setattr(sys, "exec_prefix", str(dirs["ep"]))
    monkeypatch.setattr(sys, "base_prefix", str(dirs["bp"]))
    monkeypatch.setattr(sys, "base_exec_prefix", str(dirs["bep"]))
    monkeypatch.setattr(sandbox_tiers, "_gateway_start_sys_path", lambda: ())
    import site

    # No real site dir reaches the resolver, so nothing outside tmp_path is created.
    monkeypatch.setattr(site, "getsitepackages", lambda *a: [])
    monkeypatch.setattr(site, "ENABLE_USER_SITE", False)
    monkeypatch.setattr(
        sandbox_tiers, "_gateway_package_dir", lambda: f"{ENV}/lib/site-packages/kiro_crew"
    )
    sandbox._interpreter_env_targets_cached.cache_clear()
    try:
        targets = sandbox._resolved_interpreter_env_targets()
    finally:
        sandbox._interpreter_env_targets_cached.cache_clear()
    assert targets[:4] == [str(dirs[name]) for name in ("p", "ep", "bp", "bep")]


@_POSIX_ONLY
def test_a_linked_venv_is_sealed_in_both_spellings(tmp_path: Path) -> None:
    real = tmp_path / "crew-venv-2"
    real.mkdir()
    current = tmp_path / "crew-venv-current"
    current.symlink_to(real, target_is_directory=True)
    assert _env_targets(tmp_path, (str(current), str(current))) == (str(current), str(real))


@_POSIX_ONLY
def test_an_editable_checkout_seals_no_prefix(tmp_path: Path) -> None:
    source = tmp_path / "checkout" / "src" / "kiro_crew"
    source.mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    targets = sandbox._interpreter_env_targets_cached.__wrapped__(
        str(source), (str(tmp_path),), (str(other),), (str(other),), ("/nonexistent-home",)
    )
    assert targets == ()


@_POSIX_ONLY
def test_a_prefix_this_uid_cannot_write_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv, system = tmp_path / "venv", tmp_path / "usr"
    venv.mkdir()
    system.mkdir()
    real_access = os.access
    monkeypatch.setattr(
        os, "access", lambda p, mode: False if str(p) == str(system) else real_access(p, mode)
    )
    other_uid = os.getuid() + 1  # neither writable nor owned, like a root-owned /usr
    monkeypatch.setattr(os, "getuid", lambda: other_uid)
    assert _env_targets(tmp_path, (str(venv), str(system))) == (str(venv),)


@_POSIX_ONLY
def test_an_owned_read_only_dir_on_sys_path_is_sealed(tmp_path: Path) -> None:
    # The owner can chmod it back, so read-only now does not mean read-only later.
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    site_dir.chmod(0o555)
    try:
        targets = _env_targets(tmp_path, (), path_dirs=(str(site_dir),))
    finally:
        site_dir.chmod(0o755)
    assert targets == (str(site_dir),)


@_POSIX_ONLY
def test_a_prefix_holding_home_is_skipped(tmp_path: Path) -> None:
    home = tmp_path / "home" / "u"
    home.mkdir(parents=True)
    venv = home / ".venv"
    venv.mkdir()
    prefixes = (str(tmp_path / "home"), str(home), str(venv))
    assert _env_targets(tmp_path, prefixes, homes=(str(home),)) == (str(venv),)


@_POSIX_ONLY
def test_a_prefix_holding_the_cwd_is_not_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv, other = tmp_path / "venv", tmp_path / "other"
    (venv / "work").mkdir(parents=True)
    other.mkdir()
    monkeypatch.setattr(os, "getcwd", lambda: str(venv / "work"))
    assert _env_targets(tmp_path, (str(venv), str(other))) == (str(other),)


@_POSIX_ONLY
def test_an_alias_of_the_cwd_on_sys_path_is_not_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "current"
    alias.symlink_to(project, target_is_directory=True)
    monkeypatch.setattr(os, "getcwd", lambda: str(project))
    assert _env_targets(tmp_path, (), path_dirs=(str(alias),)) == ()


@_POSIX_ONLY
def test_a_missing_entry_inside_an_excluded_prefix_is_still_created_and_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    (venv / "work").mkdir(parents=True)
    monkeypatch.setattr(os, "getcwd", lambda: str(venv / "work"))
    gone = venv / "lib" / "extra"
    targets = _env_targets(tmp_path, (str(venv),), path_dirs=(str(gone),))
    assert str(venv) not in targets
    assert str(gone) in targets and gone.is_dir()


def test_only_the_site_dirs_this_interpreter_uses_are_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A venv without system site-packages does not read its base's .pth files, and
    # site.getsitepackages() with no argument says so; passing prefixes would not.
    import site

    calls: list[tuple] = []
    monkeypatch.setattr(site, "getsitepackages", lambda *a: calls.append(a) or [])
    monkeypatch.setattr(site, "ENABLE_USER_SITE", False)
    sandbox._interpreter_env_targets_cached.cache_clear()
    try:
        sandbox._resolved_interpreter_env_targets()
    finally:
        sandbox._interpreter_env_targets_cached.cache_clear()
    assert calls == [()]


def test_the_resolver_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> str:
        raise RuntimeError("no package")

    monkeypatch.setattr(sandbox_tiers, "_gateway_package_dir", _boom)
    assert sandbox._resolved_interpreter_env_targets() == []


# --------------------------------------------------------------------------- #
# Every directory on the search path is sealed, .pth-added ones included.
# --------------------------------------------------------------------------- #


def _pipx_layout(root: Path) -> tuple[Path, Path, Path]:
    """pipx's layout: the gateway venv's ``site-packages`` holds ``pipx_shared.pth``
    naming ``$PIPX_HOME/shared/.../site-packages``, which is on ``sys.path`` but in
    no prefix. Returns ``(venv, venv_site, shared_site)``."""
    venv = root / "pipx" / "venvs" / "kiro-crew"
    venv_site = venv / "lib" / "python3.12" / "site-packages"
    shared_site = root / "pipx" / "shared" / "lib" / "python3.12" / "site-packages"
    venv_site.mkdir(parents=True)
    shared_site.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /x/bin\n", encoding="utf-8")
    (venv_site / "pipx_shared.pth").write_text(f"{shared_site}\n", encoding="utf-8")
    return venv, venv_site, shared_site


def _sealed(path: Path, targets: tuple[str, ...]) -> bool:
    return any(os.path.commonpath((str(path), t)) == t for t in targets)


@_POSIX_ONLY
def test_a_pth_added_dir_is_sealed_where_sitecustomize_would_be_planted(
    tmp_path: Path,
) -> None:
    venv, venv_site, shared_site = _pipx_layout(tmp_path)
    outside = tmp_path / "pipx"  # the venv's prefix does not reach the shared dir
    assert not _sealed(shared_site, (str(venv),))
    # Gateway started with -S or before site ran: only the site dir is known.
    targets = _env_targets(tmp_path, (str(venv),), site_dirs=(str(venv_site),))
    assert _sealed(shared_site / "sitecustomize.py", targets)
    assert str(outside) not in targets
    # And as the running gateway sees it: the .pth already put it on sys.path.
    on_path = _env_targets(tmp_path, (str(venv),), path_dirs=(str(shared_site),))
    assert _sealed(shared_site / "sitecustomize.py", on_path)


def test_pth_lines_are_read_as_site_reads_them(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    (site_dir / "rel").mkdir(parents=True)
    elsewhere = tmp_path / "abs" / "elsewhere"
    (site_dir / "a.pth").write_text(
        f"# comment\nimport os; os.getcwd()\n\nrel\n{elsewhere}\n", encoding="utf-8"
    )
    (site_dir / "b.txt").write_text("ignored\n", encoding="utf-8")
    (site_dir / ".hidden.pth").write_text(f"{tmp_path / 'hidden'}\n", encoding="utf-8")
    assert sandbox._pth_dirs((str(site_dir), str(tmp_path / "gone"))) == [
        str(site_dir / "rel"),
        str(elsewhere),
    ]


@_POSIX_ONLY
def test_zip_files_on_sys_path_are_skipped(tmp_path: Path) -> None:
    zipped = tmp_path / "python312.zip"
    zipped.write_bytes(b"")
    targets = _env_targets(tmp_path, (), path_dirs=(str(zipped), ""))
    assert targets == ()


@_POSIX_ONLY
def test_a_stale_pth_target_is_created_and_sealed_alone(tmp_path: Path) -> None:
    site_dir = tmp_path / "venv-site"
    site_dir.mkdir()
    projects = tmp_path / "projects"
    workspace = projects / "app"
    workspace.mkdir(parents=True)
    gone = projects / "removed" / "src"
    (site_dir / "_editable.pth").write_text(f"{gone}\n", encoding="utf-8")
    targets = _env_targets(tmp_path, (), site_dirs=(str(site_dir),))
    assert gone.is_dir()
    assert str(gone) in targets
    assert not _sealed(workspace, targets)
    assert str(projects) not in targets and str(projects / "removed") not in targets


@_POSIX_ONLY
def test_a_missing_user_site_is_created_and_sealed_alone(tmp_path: Path) -> None:
    home = tmp_path / "home"
    local = home / ".local"
    local.mkdir(parents=True)
    user_site = local / "lib" / "python3.12" / "site-packages"
    targets = _env_targets(tmp_path, (), homes=(str(home),), site_dirs=(str(user_site),))
    assert targets == (str(user_site),)


@_POSIX_ONLY
def test_a_missing_entry_under_a_sticky_dir_never_seals_that_dir(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o1777)
    entry = shared / "not-yet"
    targets = _env_targets(tmp_path, (), path_dirs=(str(entry),))
    assert str(shared) not in targets
    assert targets == (str(entry),)


@_POSIX_ONLY
def test_a_dangling_link_into_a_sticky_dir_seals_only_the_created_dir(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o1777)
    link = tmp_path / "link"
    link.symlink_to(shared / "target", target_is_directory=True)
    targets = _env_targets(tmp_path, (), path_dirs=(str(link / "lib"),))
    assert str(shared) not in targets
    assert str(shared / "target" / "lib") in targets


@_POSIX_ONLY
def test_a_missing_entry_that_cannot_be_created_warns_and_stays_unsealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _refuse(path, *args, **kwargs):
        raise PermissionError(path)

    monkeypatch.setattr(os, "makedirs", _refuse)
    entry = tmp_path / "libs" / "not-yet"
    with caplog.at_level(logging.WARNING, logger="kiro_crew.sandbox"):
        targets = _env_targets(tmp_path, (), path_dirs=(str(entry),))
    assert targets == ()
    warned = [r.getMessage() for r in caplog.records if "could not create" in r.getMessage()]
    assert len(warned) == 1 and str(entry) in warned[0]


@_POSIX_ONLY
def test_a_home_on_sys_path_is_skipped_and_a_user_site_is_not(tmp_path: Path) -> None:
    home = tmp_path / "home"
    user_site = home / ".local" / "lib" / "python3.12" / "site-packages"
    user_site.mkdir(parents=True)
    targets = _env_targets(tmp_path, (), homes=(str(home),), path_dirs=(str(home), str(user_site)))
    assert targets == (str(user_site),)


@_POSIX_ONLY
def test_a_project_venv_off_the_gateway_path_stays_writable(tmp_path: Path) -> None:
    venv, venv_site, _shared = _pipx_layout(tmp_path)
    project = tmp_path / "work" / ".venv" / "lib" / "python3.12" / "site-packages"
    project.mkdir(parents=True)
    targets = _env_targets(tmp_path, (str(venv),), path_dirs=(str(venv_site),))
    assert not _sealed(project, targets)


# --------------------------------------------------------------------------- #
# A pyenv base that also holds other environments is narrowed.
# --------------------------------------------------------------------------- #


def _tree(root: Path, *dirs: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel in dirs:
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


@_POSIX_ONLY
def test_a_pyenv_base_leaves_its_other_virtualenvs_writable(tmp_path: Path) -> None:
    base = _tree(
        tmp_path / ".pyenv" / "versions" / "3.12.1",
        "bin",
        "include/python3.12",
        "lib/python3.12/site-packages",
        "share",
        "envs/crew/lib/python3.12/site-packages",
        "envs/other",
    )
    venv = base / "envs" / "crew"
    (venv / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    site_dir = venv / "lib" / "python3.12" / "site-packages"
    targets = _env_targets(
        tmp_path,
        (str(venv), str(venv), str(base), str(base)),
        path_dirs=(str(base / "lib" / "python3.12"), str(site_dir)),
        site_dirs=(str(site_dir),),
    )
    assert targets[:5] == (str(venv), *(str(base / n) for n in ("bin", "include", "lib", "share")))
    assert not _sealed(base / "envs" / "other", targets)
    assert str(base) not in targets and str(base / "envs") not in targets


@_POSIX_ONLY
def test_a_base_prefix_holding_no_other_env_is_sealed_whole(tmp_path: Path) -> None:
    base = _tree(tmp_path / "mise" / "3.12.13", "bin", "lib/python3.12")
    assert _env_targets(tmp_path, (str(base),)) == (str(base),)


# --------------------------------------------------------------------------- #
# A conda-hosted gateway seals the whole install and every package cache.
# --------------------------------------------------------------------------- #


@_POSIX_ONLY
def test_a_conda_base_prefix_seals_the_whole_install(tmp_path: Path, no_conda_env: Path) -> None:
    conda = _tree(tmp_path / "miniconda3", "bin", "conda-meta", "lib", "envs/a", "pkgs")
    targets = _env_targets(tmp_path, (str(conda),) * 4, homes=(str(no_conda_env),))
    assert str(conda) in targets
    assert _sealed(conda / "envs" / "a", targets) and _sealed(conda / "pkgs", targets)


@_POSIX_ONLY
def test_a_gateway_in_a_conda_env_seals_its_install(tmp_path: Path, no_conda_env: Path) -> None:
    conda = _tree(tmp_path / "miniconda3", "bin", "conda-meta", "envs/other/lib", "pkgs")
    env = _tree(conda / "envs" / "kc", "bin", "conda-meta")
    targets = _env_targets(tmp_path, (str(env),) * 4, homes=(str(no_conda_env),))
    assert str(conda) in targets and _sealed(conda / "envs" / "other", targets)


@_POSIX_ONLY
def test_a_venv_on_a_conda_python_seals_that_install(tmp_path: Path, no_conda_env: Path) -> None:
    conda = _tree(tmp_path / "miniconda3", "bin", "conda-meta", "pkgs")
    venv = _tree(tmp_path / "venv", "bin")
    (venv / "pyvenv.cfg").write_text(f"home = {conda}/bin\n", encoding="utf-8")
    targets = _env_targets(
        tmp_path, (str(venv), str(venv), str(conda), str(conda)), homes=(str(no_conda_env),)
    )
    assert str(venv) in targets and str(conda) in targets


@_POSIX_ONLY
def test_conda_package_caches_outside_the_install_are_sealed(
    tmp_path: Path, no_conda_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = no_conda_env
    # A user env with the default user cache (the install root was not writable).
    env = _tree(home / ".conda" / "envs" / "kc", "bin", "conda-meta")
    default_cache = _tree(home / ".conda" / "pkgs")
    sibling = _tree(home / ".conda" / "envs" / "other")
    # A .condarc in the user's config, and one from CONDA_PKGS_DIRS.
    configured = _tree(tmp_path / "cache-from-condarc")
    from_env = _tree(tmp_path / "cache-from-env")
    (home / ".condarc").write_text(f"pkgs_dirs:\n  - {configured}\n", encoding="utf-8")
    monkeypatch.setenv("CONDA_PKGS_DIRS", str(from_env))
    targets = _env_targets(tmp_path, (str(env),) * 4, homes=(str(home),))
    for cache in (default_cache, configured, from_env, sibling):
        assert _sealed(cache, targets), cache
    assert not _sealed(home, targets)


@_POSIX_ONLY
def test_a_conda_cache_in_the_install_s_own_condarc_is_sealed(
    tmp_path: Path, no_conda_env: Path
) -> None:
    conda = _tree(tmp_path / "miniconda3", "bin", "conda-meta")
    cache = _tree(tmp_path / "shared-pkgs")
    (conda / ".condarc").write_text(f"pkgs_dirs: [{cache}]\nenvs_dirs: 3\n", encoding="utf-8")
    targets = _env_targets(tmp_path, (str(conda),), homes=(str(no_conda_env),))
    assert _sealed(cache, targets)


def _only_tmp_condarc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hide the host's system condarc files (``/etc/conda`` ...) from the reader."""
    import builtins

    def _open(path, *args, **kwargs):
        if not str(path).startswith(str(tmp_path)):
            raise FileNotFoundError(path)
        return builtins.open(path, *args, **kwargs)

    monkeypatch.setattr(sandbox_env_scan, "open", _open, raising=False)


def test_a_broken_condarc_is_ignored(
    tmp_path: Path, no_conda_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only_tmp_condarc(monkeypatch, tmp_path)
    conda = _tree(tmp_path / "miniconda3", "conda-meta")
    (conda / ".condarc").write_text("pkgs_dirs: [unclosed\n", encoding="utf-8")
    assert sandbox_env_scan._condarc_dirs(str(conda), str(no_conda_env)) == []


def test_a_deeply_nested_condarc_drops_only_itself(
    tmp_path: Path, no_conda_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only_tmp_condarc(monkeypatch, tmp_path)
    conda = _tree(tmp_path / "miniconda3", "conda-meta")
    (conda / ".condarc").write_text("pkgs_dirs: [/srv/pkgs]\n", encoding="utf-8")
    (no_conda_env / ".condarc").write_text("[" * 5000, encoding="utf-8")
    assert sandbox_env_scan._condarc_dirs(str(conda), str(no_conda_env)) == ["/srv/pkgs"]


@_POSIX_ONLY
def test_a_nul_byte_entry_drops_only_itself(
    tmp_path: Path, no_conda_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only_tmp_condarc(monkeypatch, tmp_path)
    conda = _tree(tmp_path / "miniconda3", "conda-meta", "pkgs")
    (no_conda_env / ".condarc").write_text('pkgs_dirs: ["/x\\0y"]\n', encoding="utf-8")
    sealed = sandbox._conda_seal_dirs(str(conda), (str(no_conda_env),))
    assert str(conda) in sealed and str(conda / "pkgs") in sealed
    targets = _env_targets(tmp_path, (), path_dirs=("/x\0y", str(conda / "pkgs")))
    assert targets == (str(conda / "pkgs"),)


@_POSIX_ONLY
def test_a_pyenv_base_is_not_read_as_conda(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _tree(tmp_path / "versions" / "3.12.1", "bin", "lib", "envs/other", "pkgs")
    monkeypatch.setattr(sandbox_tiers, "_conda_seal_dirs", lambda *_a: pytest.fail("conda"))
    targets = _env_targets(tmp_path, (str(base),))
    assert str(base / "pkgs") in targets and str(base / "envs" / "other") not in targets


# --------------------------------------------------------------------------- #
# A hard-linked sealed file is reported once, at boot.
# --------------------------------------------------------------------------- #


@_POSIX_ONLY
def test_hard_linked_sealed_files_are_counted_once(tmp_path: Path) -> None:
    sealed = _tree(tmp_path / "venv", "lib")
    (sealed / "lib" / "plain.py").write_text("", encoding="utf-8")
    linked = sealed / "lib" / "six.py"
    linked.write_text("", encoding="utf-8")
    os.link(linked, tmp_path / "cache-six.py")
    # A nested target (site-packages inside the venv) is not walked twice.
    assert sandbox_env_scan._hard_linked_sealed_files([str(sealed), str(sealed / "lib")]) == (
        1,
        str(linked),
        False,
    )
    assert sandbox_env_scan._hard_linked_sealed_files([str(sealed)], cap=1)[2] is True


@_POSIX_ONLY
def test_the_boot_prime_warns_once_about_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sealed = _tree(tmp_path / "venv", "lib")
    linked = sealed / "lib" / "six.py"
    linked.write_text("", encoding="utf-8")
    os.link(linked, tmp_path / "cache-six.py")
    monkeypatch.setattr(sandbox, "_resolved_interpreter_env_targets", lambda: [str(sealed)])
    with caplog.at_level(logging.INFO, logger="kiro_crew.sandbox"):
        sandbox.prime_interpreter_env_seal()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "1 file(s)" in warnings[0].getMessage() and str(linked) in warnings[0].getMessage()
    caplog.clear()
    monkeypatch.setattr(sandbox, "_resolved_interpreter_env_targets", lambda: [])
    sandbox.prime_interpreter_env_seal()
    assert not caplog.records


def test_the_start_path_is_snapshotted(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox._gateway_start_sys_path.cache_clear()
    try:
        first = sandbox._gateway_start_sys_path()
        monkeypatch.setattr(sys, "path", [*sys.path, "/inserted/later"])
        assert sandbox._gateway_start_sys_path() == first
    finally:
        sandbox._gateway_start_sys_path.cache_clear()


# --------------------------------------------------------------------------- #
# The links the gateway reaches its interpreter through are guarded.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def linked_venv(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A real venv built through a link to the base Python, as uv, Homebrew ``opt/``
    and a mise version alias lay it out. Returns ``(venv, link)``."""
    import subprocess

    root = tmp_path_factory.mktemp("linked")
    link = root / "py-minor"
    link.symlink_to(os.path.realpath(sys.base_prefix), target_is_directory=True)
    python = link / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}"
    venv = root / "venv"
    subprocess.run(
        [str(python), "-I", "-m", "venv", "--without-pip", str(venv)],
        check=True,
        capture_output=True,
        timeout=FALLBACK_TIMEOUT_SECS,
    )
    return venv, link


@_POSIX_ONLY
@pytest.mark.skipif(not os.access(sys.base_prefix, os.R_OK), reason="needs a base Python")
@pytest.mark.parametrize("backend", [BACKEND_NAMESPACE, BACKEND_SEATBELT])
def test_the_link_to_the_base_python_is_guarded(
    linked_venv: tuple[Path, Path], backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv, link = linked_venv
    paths = sandbox._interpreter_link_paths_cached.__wrapped__(
        (str(venv),), (str(venv / "bin" / "python3"), ""), str(venv / "pyvenv.cfg")
    )
    if sys.platform == "darwin" and not any(p.startswith(str(link)) for p in paths):
        pytest.skip("this macOS venv records the resolved base Python, not the link")
    assert any(p.startswith(str(link)) for p in paths)
    monkeypatch.setattr(sandbox, "_resolved_interpreter_env_targets", lambda: [str(venv)])
    monkeypatch.setattr(sandbox, "_resolved_install_root_targets", lambda: [])
    monkeypatch.setattr(sandbox, "_resolved_interpreter_link_paths", lambda: list(paths))
    host = sandbox._live_plan_host(SandboxRequest(tier="standard", backend=backend))
    assert str(link) in host.readonly_ancestor_guards
    lifted = sandbox._live_plan_host(
        SandboxRequest(tier="standard", backend=backend, gateway_env_writable=True)
    )
    assert str(link) not in lifted.readonly_ancestor_guards


def test_no_link_is_guarded_without_a_sealed_env(tmp_path: Path) -> None:
    cfg = tmp_path / "pyvenv.cfg"
    cfg.write_text("home = /x/bin\n", encoding="utf-8")
    assert sandbox._interpreter_link_paths_cached.__wrapped__((), ("/x",), str(cfg)) == ()
    assert sandbox._interpreter_link_paths_cached.__wrapped__(("/v",), ("",), str(cfg)) == (
        os.path.abspath("/x/bin"),
    )


# --------------------------------------------------------------------------- #
# The plan seals them on both backends, unless the gateway lifts them.
# --------------------------------------------------------------------------- #


@pytest.fixture
def packaged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "_resolved_interpreter_env_targets", lambda: [ENV, BASE])
    monkeypatch.setattr(sandbox, "_resolved_install_root_targets", lambda: [INSTALL])
    monkeypatch.setattr(sandbox, "_resolved_interpreter_link_paths", lambda: [])


@_POSIX_ONLY
@pytest.mark.usefixtures("packaged")
@pytest.mark.parametrize("backend", [BACKEND_NAMESPACE, BACKEND_SEATBELT])
@pytest.mark.parametrize("tier", ["strict", "cc", "standard"])
def test_every_tier_seals_the_env_unless_the_gateway_lifts_it(backend: str, tier: str) -> None:
    sealed = sandbox._spawn_plan(backend, tier).readonly
    assert {ENV, BASE, INSTALL} <= set(sealed)
    lifted = sandbox._spawn_plan(backend, tier, gateway_env_writable=True).readonly
    assert ENV not in lifted and BASE not in lifted
    assert INSTALL in lifted


@_POSIX_ONLY
@pytest.mark.usefixtures("packaged")
def test_the_namespace_launcher_gets_the_env_before_the_package_dir() -> None:
    # The package directory's seal must mount on top of the env's, not under it.
    host = sandbox._live_plan_host(SandboxRequest(tier="standard"))
    assert host.install_root_targets == (ENV, BASE, INSTALL)
    readonly = sandbox_plan.namespace_payload(sandbox._spawn_plan(BACKEND_NAMESPACE, "standard"))[
        "readonly_dirs"
    ]
    assert {ENV, BASE, INSTALL} <= set(readonly)


@_POSIX_ONLY
@pytest.mark.usefixtures("packaged")
def test_the_seatbelt_profile_denies_writes_below_each_prefix() -> None:
    profile = sandbox._build_seatbelt_profile("standard")
    for target in (ENV, BASE):
        assert f'(deny file-write* (subpath "{target}"))' in profile
        assert f'(deny file-link (subpath "{target}"))' in profile
        assert f'(allow file-write* (subpath "{target}' not in profile
    lifted = sandbox._build_seatbelt_profile("standard", gateway_env_writable=True)
    assert f'(deny file-write* (subpath "{BASE}"))' not in lifted
    assert f'(deny file-write* (subpath "{INSTALL}"))' in lifted


@_POSIX_ONLY
@pytest.mark.usefixtures("packaged")
def test_renaming_a_parent_of_the_base_prefix_is_guarded() -> None:
    host = sandbox._live_plan_host(SandboxRequest(tier="standard", backend=BACKEND_SEATBELT))
    assert {"/opt/python", "/opt"} <= set(host.readonly_ancestor_guards)
    lifted = sandbox._live_plan_host(
        SandboxRequest(tier="standard", backend=BACKEND_SEATBELT, gateway_env_writable=True)
    )
    assert "/opt/python" not in lifted.readonly_ancestor_guards


# --------------------------------------------------------------------------- #
# The flag reaches the plan from both wrap entry points.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("flag", [False, True])
def test_wrap_argv_passes_the_flag_to_the_launcher(
    flag: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def namespace_argv(argv, level, **kwargs):
        seen.update(kwargs)
        return [sys.executable, *sandbox._LAUNCHER_INTERPRETER_FLAGS, "launcher.py", *argv]

    monkeypatch.setattr(sandbox, "_governance_sandbox_floor", lambda: None)
    monkeypatch.setattr(sandbox, "detect_backend", lambda **_: "namespace")
    monkeypatch.setattr(sandbox, "namespace_argv", namespace_argv)
    monkeypatch.setattr(sandbox, "_inside_kirocrew_sandbox", lambda: False)
    monkeypatch.setattr(sandbox, "_spawns_kiro_cli", lambda argv: False)
    sandbox.wrap_argv(["/bin/true"], mode="standard", gateway_env_writable=flag)
    assert seen["gateway_env_writable"] is flag


@pytest.mark.asyncio
async def test_wrap_argv_async_passes_the_flag() -> None:
    seen: dict = {}

    def prepare(argv, **kwargs):
        seen.update(kwargs)
        return list(argv), None

    await sandbox.wrap_argv_async(["/bin/true"], gateway_env_writable=True, _prepare=prepare)
    assert seen["gateway_env_writable"] is True


# --------------------------------------------------------------------------- #
# Only the gateway's own fixed-argv installs set it.
# --------------------------------------------------------------------------- #

#: ``path relative to the package -> number of ``gateway_env_writable=True`` sites``.
_ALLOWED_SETTERS = {
    # `ensurepip` and `pip install faiss-cpu`, both fixed argv. Nothing else: the
    # registry refuses every app build that would pip into the gateway env.
    "dashboard/handlers/memory_embeddings.py": 2,
}
#: Modules that only forward the caller's value.
_PLUMBING = {
    "sandbox_argv.py",
    "sandbox_plan.py",
    "sandbox_spawn.py",
    "sandbox_wrap.py",
}
_FLAG = "gateway_env_writable"


def _is_flag_name(node: ast.AST) -> bool:
    """``gateway_env_writable`` or ``bool(gateway_env_writable)``: a forwarded parameter."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "bool":
        return len(node.args) == 1 and not node.keywords and _is_flag_name(node.args[0])
    return isinstance(node, ast.Name) and node.id == _FLAG


def _flag_values(tree: ast.AST) -> list[ast.AST]:
    """Every value a module hands the flag: a keyword, a dict key (``{...}`` or
    ``**{...}``), a subscript assignment, ``setattr``. A ``"gateway_env_writable"``
    string anywhere else is refused, so no other spelling slips past."""
    values: list[ast.AST] = []
    consumed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == _FLAG:
            values.append(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == _FLAG:
                    values.append(value)
                    consumed.add(id(key))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                key = target.slice if isinstance(target, ast.Subscript) else None
                if isinstance(key, ast.Constant) and key.value == _FLAG:
                    values.append(node.value)
                    consumed.add(id(key))
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setattr":
            if len(node.args) == 3 and getattr(node.args[1], "value", None) == _FLAG:
                values.append(node.args[2])
                consumed.add(id(node.args[1]))
    stray = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == _FLAG]
    assert all(id(n) in consumed for n in stray), "unrecognised flag spelling"
    return values


def _flag_setters(text: str, relative: str) -> int:
    count = 0
    tree = ast.parse(text)
    for value in _flag_values(tree):
        if isinstance(value, ast.Constant) and value.value is False:
            continue
        if relative in _PLUMBING and _is_flag_name(value):
            continue
        # Anything else -- a computed value, a config read, ``True`` elsewhere.
        assert isinstance(value, ast.Constant) and value.value is True, relative
        count += 1
    for node in ast.walk(tree):
        # Every function taking the flag takes it keyword-only, and the request is
        # never built positionally, so no positional argument can carry it.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            positional = [a.arg for a in (*node.args.posonlyargs, *node.args.args)]
            assert _FLAG not in positional, f"{relative}:{node.name}"
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "SandboxRequest":
            assert not node.args, relative
    return count


def test_only_the_allowed_sites_set_the_flag() -> None:
    root = Path(sandbox.__file__).resolve().parent
    found: dict[str, int] = {}
    for source in root.rglob("*.py"):
        relative = source.relative_to(root).as_posix()
        text = source.read_text(encoding="utf-8")
        if _FLAG not in text:
            continue
        count = _flag_setters(text, relative)
        if count:
            found[relative] = count
    assert found == _ALLOWED_SETTERS


@pytest.mark.parametrize(
    "snippet",
    [
        'options["gateway_env_writable"] = True',
        'wrap_argv(cmd, **{"gateway_env_writable": True})',
        "wrap_argv(cmd, gateway_env_writable=flag)",
        'setattr(request, "gateway_env_writable", True)',
        'dict([("gateway_env_writable", True)])',
        "SandboxRequest(1, 2, 3)",
        "def wrap(argv, gateway_env_writable=False): pass",
    ],
)
def test_the_tripwire_catches_each_spelling(snippet: str) -> None:
    with pytest.raises(AssertionError):
        assert _flag_setters(snippet, "apps/other.py") == 0


@pytest.mark.parametrize(
    ("snippet", "ok"),
    [
        ("f(gateway_env_writable=x)", False),
        ('o["gateway_env_writable"] = x', False),
        ("f(gateway_env_writable=gateway_env_writable)", True),
        ("f(gateway_env_writable=bool(gateway_env_writable))", True),
        ('o["gateway_env_writable"] = gateway_env_writable', True),
    ],
)
def test_plumbing_forwards_only_the_parameter_name(snippet: str, ok: bool) -> None:
    if ok:
        assert _flag_setters(snippet, "sandbox_wrap.py") == 0
    else:
        with pytest.raises(AssertionError):
            _flag_setters(snippet, "sandbox_wrap.py")


def test_the_service_keeps_the_script_dir_off_sys_path(tmp_path: Path) -> None:
    # A --user install puts the console script in ~/.local/bin; with safe_path that
    # directory is not on the gateway's sys.path, so the seal does not take it.
    from kiro_crew.service.common import service_environment

    assert service_environment(str(tmp_path))["PYTHONSAFEPATH"] == "1"


@_POSIX_ONLY
def test_a_foreground_gateway_restarts_itself_with_safe_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kiro_crew.service import common

    calls: list[tuple] = []
    monkeypatch.setattr(os, "execve", lambda *a: calls.append(a))
    monkeypatch.setattr(sys, "orig_argv", ["python", "/u/.local/bin/kirocrew", "gateway"])
    common._reexec_with_safe_path_original()
    assert calls and calls[0][1][1:] == ["/u/.local/bin/kirocrew", "gateway"]
    assert calls[0][2]["PYTHONSAFEPATH"] == "1"
    calls.clear()
    monkeypatch.setattr(sys, "flags", types.SimpleNamespace(safe_path=True))
    common._reexec_with_safe_path_original()
    assert calls == []


def test_the_script_dir_is_not_a_start_path_under_safe_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(sys, "argv", [str(bin_dir / "kirocrew"), "gateway"])
    monkeypatch.setattr(sys, "path", [str(bin_dir), str(tmp_path / "site")])
    monkeypatch.setattr(sys, "flags", types.SimpleNamespace(safe_path=True))
    assert sandbox._gateway_start_sys_path.__wrapped__() == (str(tmp_path / "site"),)


@_POSIX_ONLY
def test_a_top_level_home_dir_on_sys_path_is_left_writable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    home = tmp_path / "home"
    (home / "projects" / "ws").mkdir(parents=True)
    (home / ".local").mkdir()
    entries = (str(home / "projects"), str(home / ".local"))
    with caplog.at_level(logging.WARNING, logger="kiro_crew.sandbox"):
        targets = _env_targets(tmp_path, (), homes=(str(home),), path_dirs=entries)
    assert targets == ()
    assert sum("left writable" in r.getMessage() for r in caplog.records) == 2


@_POSIX_ONLY
def test_a_dir_holding_the_workspace_is_left_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kiro_crew.config import loader_locations

    work = tmp_path / "code"
    (work / "crew-workspace").mkdir(parents=True)
    monkeypatch.setattr(
        loader_locations, "workspace_root", lambda create=True: work / "crew-workspace"
    )
    assert _env_targets(tmp_path, (), path_dirs=(str(work),)) == ()
