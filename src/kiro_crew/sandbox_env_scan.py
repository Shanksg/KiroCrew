"""Read-only scans the gateway's interpreter seal uses: which conda install a
prefix belongs to, the cache and env lists in the ``.condarc`` files conda reads,
and the count of sealed files that have a second hard-linked name.

Split out of :mod:`kiro_crew.sandbox_tiers`, which joins their results into the
seal; nothing here decides what is sealed.
"""

from __future__ import annotations

import logging
import os
import stat
import sys

logger = logging.getLogger("kiro_crew.sandbox")


def _conda_root(prefix: str) -> str:
    """The conda install *prefix* is (a base) or sits in (``<root>/envs/<name>``), or ``""``.

    A conda install, and each of its environments, is marked by a ``conda-meta``
    directory; pyenv and venvs are not. The owning install wins over the env itself.
    """
    parent = os.path.dirname(prefix)
    for root in (os.path.dirname(parent) if os.path.basename(parent) == "envs" else "", prefix):
        if root and os.path.isdir(os.path.join(root, "conda-meta")):
            return root
    return ""


def _condarc_dirs(root: str, home: str) -> list[str]:
    """``pkgs_dirs`` and ``envs_dirs`` from each ``.condarc`` conda reads for the
    install at *root* (system, the install's own, the user's, ``$CONDARC``).

    shortcut: ``condarc.d/`` directories are not read; add them if a deployment
    configures its caches there.
    """
    import yaml  # type: ignore[import-untyped]

    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    # conda's system search path is POSIX-only; Windows has no such directories.
    system = (
        ()
        if sys.platform == "win32"
        else (os.path.join("/etc", "conda"), os.path.join("/var", "lib", "conda"))
    )
    dirs = (
        *system,
        root,
        os.path.join(xdg, "conda"),
        os.path.join(home, ".conda"),
    )
    files = [os.path.join(d, n) for d in dirs for n in (".condarc", "condarc")]
    files.extend((os.path.join(home, ".condarc"), os.environ.get("CONDARC", "")))
    found: list[str] = []
    for path in dict.fromkeys(f for f in files if f):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                data = yaml.safe_load(fh.read(1 << 20))
        except OSError:
            continue
        except Exception:  # a YAML error, or RecursionError on deep nesting
            logger.warning("sandbox: could not read conda config %s; ignoring it", path)
            continue
        if not isinstance(data, dict):
            continue
        for key in ("pkgs_dirs", "envs_dirs"):
            value = data.get(key)
            if isinstance(value, list):
                found.extend(v for v in value if isinstance(v, str))
    return found


#: How many files :func:`_hard_linked_sealed_files` looks at, at most.
_LINK_CHECK_FILE_CAP = 50_000


def _hard_linked_sealed_files(
    targets: tuple[str, ...] | list[str], cap: int = _LINK_CHECK_FILE_CAP
) -> tuple[int, str, bool]:
    """``(count, example, truncated)``: regular files under the sealed *targets*
    with more than one name (``st_nlink > 1``), links not followed, after at most
    *cap* files. A second name outside the sealed directories (a uv or conda cache)
    writes the same inode, which a path seal does not cover."""
    tops: list[str] = []
    for top in sorted(dict.fromkeys(os.path.realpath(t) for t in targets)):
        if not any(os.path.commonpath((top, t)) == t for t in tops):
            tops.append(top)
    count, example, seen = 0, "", 0
    for top in tops:
        for parent, _dirs, files in os.walk(top):
            for name in files:
                seen += 1
                if seen > cap:
                    return count, example, True
                path = os.path.join(parent, name)
                try:
                    st = os.lstat(path)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                    count += 1
                    example = example or path
    return count, example, False
