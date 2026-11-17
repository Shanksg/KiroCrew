"""The setup wizard's note on the folder a new dashboard chat starts in.

The workspace step's answer does not move that folder, so the step names it,
chosen by the rule ``chat_api.slot_create._birth_default_project`` applies to a
chat whose session folder names no project.
"""

from __future__ import annotations

import os

from kiro_crew.config import KiroCrewConfig
from kiro_crew.config.loader import workspace_dir_for
from kiro_crew.sandbox import voice_runtime_workspace_conflict
from kiro_crew.security import is_sensitive_canonical_path

_FOLDER_OVERRIDE_LINE = (
    "  A session folder that names its own project overrides this for its chats."
)


def _usable_default_project(configured: object) -> str:
    """*configured* resolved, when a new chat would start there; else ``""``."""
    if not isinstance(configured, str) or not configured:
        return ""
    resolved = os.path.realpath(os.path.expanduser(configured))
    if (
        os.path.isdir(resolved)
        and not is_sensitive_canonical_path(resolved)
        and voice_runtime_workspace_conflict(resolved) is None
    ):
        return resolved
    return ""


def dashboard_chat_folder_lines() -> list[str]:
    """The lines naming where a new dashboard chat starts and the setting that sets it.

    Never raises: the note is informational, and a config this cannot resolve (a
    ``~user`` folder whose account is gone) must not stop the wizard.
    """
    try:
        cfg = KiroCrewConfig.load()
        override = _usable_default_project(cfg.dashboard.default_project)
        if override:
            return [
                f"  Dashboard chats start in: {override}",
                "  (set by `dashboard.default_project` in config.json)",
                _FOLDER_OVERRIDE_LINE,
            ]
        folder = workspace_dir_for()
    except Exception:
        return [
            "  The folder dashboard chats start in could not be resolved;",
            "  `kirocrew workspace list` names the default workspace's folder.",
        ]
    return [
        f"  Dashboard chats start in the default workspace's folder: {folder}",
        "  (listed by `kirocrew workspace list`; set by `workspaces.<name>.dir` in config.json,",
        "  unless `dashboard.default_project` names a folder)",
        _FOLDER_OVERRIDE_LINE,
    ]
