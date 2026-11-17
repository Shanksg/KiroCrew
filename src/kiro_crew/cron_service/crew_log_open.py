"""Opening a message cron run's crew log ahead of its prompt.

A session's crew log is created by ``crew_log_emit.on_session_opened``, and the
work a session records into it -- the session ledger (``session_ledger_record``),
``work_ledger_record`` and ``work_report`` -- is refused or rolled back while the
session has none. The dashboard runner and the channel dispatchers open their own
sessions' logs every turn; :func:`open_cron_crew_log` is the cron run's opener, called
by both cron dispatch sites (``slack/gateway_runtime/cron_turn.py``) once the claim
returned and before the prompt is built.
"""

from __future__ import annotations

import logging
from typing import Any

from kiro_crew.history import transcript_stem

logger = logging.getLogger(__name__)


def open_cron_crew_log(provider: Any, *, session_key: str, agent: str, resumed: bool) -> None:
    """Open the crew log of the cron session behind *provider*, as a chat turn does.

    Only facts the cron run can establish are recorded; an absent field means "not
    observed". No ACP session id on *provider* means no log. ``slot`` is the run's
    session key folded to the filename charset (:func:`transcript_stem`), the name
    the channel dispatcher records for its own keys and one of the names
    ``crew_log_slots_for`` looks a cron conversation up by. Served model and cwd
    come off the provider; ``resumed`` is the claim's answer. A cron run is a root
    of the session tree, so ``depth`` comes from ``root_depth`` and no ``parent``
    is written. No predecessor is named and no class is stated: the run observes
    neither, and the emitter reads their absence as "not observed".

    Free while the emitter is off (``on_session_opened`` returns at once), and it
    never raises: the run must not be lost to its own record.
    """
    # Function-local on purpose: the gateway imports the cron runtime at boot, and a
    # flag-off launch must not load the crew-log storage package.
    from kiro_crew.crew_log import emit as crew_log_emit
    from kiro_crew.crew_log import session_tree_projection as crew_log_tree

    try:
        session_id = crew_log_emit.session_id_of(provider)
        if not session_id:
            return
        crew_log_emit.on_session_opened(
            session_id,
            agent=agent or "",
            slot=transcript_stem(session_key),
            model=str(getattr(provider, "served_model", "") or ""),
            cwd=str(getattr(provider, "cwd", "") or ""),
            resumed=bool(resumed),
            depth=crew_log_tree.root_depth(),
        )
    except Exception:
        logger.debug("crew log: cron opener skipped for %s", session_key, exc_info=True)
