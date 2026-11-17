"""One bell note while a config file the running gateway watches does not parse.

The live watcher keeps the settings already in force while ``config.json`` (or
``config.local.json``) does not parse, so the edit the person just saved does not
apply and nothing on screen says why. The watcher hands this module one sentence
per torn file (the file, the line, the column and the parser's reason) each time a
save leaves it torn, and an empty tuple once a later read adopts it whole.

The note carries a ``key``, so the bell holds one such note. A save that tears the
file the same way again publishes nothing: the note already says it. A different
error first removes the stored note, so the new one rings instead of replacing it
quietly. Files that parse again remove the note, and so does the first whole read
of a later process, so a note stored before a restart does not outlive the repair.
A removal or a note that is not stored raises :class:`NoteNotSaved`, and the
watcher retries it on its next tick. The file itself is never touched.
"""

from __future__ import annotations

from typing import Any

from kiro_crew.notifications.bus import NotificationPayload

CHANNEL = "system.agent"
SOURCE = "system"
#: Replaces the stored note of the same source, so the bell holds one.
KEY = "config-syntax-error"


def note_body(tear: tuple[str, ...]) -> str:
    """The bell note's body: one paragraph per file, since the bell renders Markdown."""
    return "\n\n".join(
        (
            *tear,
            "Kiro Crew keeps running on the settings already in force, so the edit "
            "you saved is not applied yet. The file is left as you saved it.",
            "To fix it: correct the file and save it again. The change applies "
            "within a few seconds, with no restart.",
        )
    )


class NoteNotSaved(RuntimeError):
    """The bell change was not stored; the watcher retries it on its next tick."""


async def publish(state: Any, tear: tuple[str, ...]) -> bool:
    """Show *tear* in the bell, or remove the note when it is empty. True when a note was pushed.

    Raises :class:`NoteNotSaved` when the removal or the note was not stored.
    """
    stored = [
        n
        for n in getattr(state, "_notification_log", ())
        if n.get("source") == SOURCE and n.get("key") == KEY
    ]
    if not tear:
        # Dismissed even when no note is stored yet: a note whose write is still in
        # flight lands only when the write finishes, and the dismissal waits behind it.
        _removed, persisted = await state.dismiss_notification(source=SOURCE, key=KEY)
        if not persisted:
            raise NoteNotSaved("could not remove the resolved config parse note")
        return False
    body = note_body(tear)
    if stored and stored[-1].get("body") == body:
        return False
    if stored:
        # A same-key push at the same priority replaces the old note quietly,
        # so a new error would ring nothing. Remove the old note first.
        _removed, persisted = await state.dismiss_notification(source=SOURCE, key=KEY)
        if not persisted:
            raise NoteNotSaved("could not remove the earlier config parse note")
    state.notification_bus.push(
        NotificationPayload(
            source=SOURCE,
            channel=CHANNEL,
            # Every later edit to the file is ignored until it parses.
            priority="critical",
            title="A config file does not parse; your last edit is not applied",
            body=body,
            key=KEY,
        )
    )
    # Read before any await: a later note replaces the state-wide future.
    persisted = getattr(state, "last_notification_persist", None)
    if persisted is not None and not await persisted:
        raise NoteNotSaved("the config parse note was not stored")
    return True
