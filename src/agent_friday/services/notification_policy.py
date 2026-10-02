"""What the notification tray shows, and when.

The tray is for the owner, not for the build. Three tiers:

* NEEDS_YOU - approvals and failures that ask for a decision. A card, unread.
* FYI       - things that happened. A card that never bumps the badge; the
              panel rolls them into a digest.
* LOG_ONLY  - housekeeping (schedule retirements, network flaps, build-system
              internals). Written to the activity log only, never the tray.

Repeats of the same thing collapse into one card with a count; a run keeps one
card that is updated in place; a job's failure card resolves itself when the
next run succeeds. The owner can mute a kind (reversible in Settings); a muted
kind goes to the activity log. While the owner is mid-conversation, new cards
are held and delivered after.

APPROVALS ARE NEVER DEMOTED, MUTED OR HELD AWAY. An approval is always its own
NEEDS_YOU card; mid-conversation it is delivered without interrupting.
"""
from __future__ import annotations

import re
import threading
import time

NEEDS_YOU, FYI, LOG_ONLY = "needs_you", "fyi", "log_only"
TIERS = (NEEDS_YOU, FYI, LOG_ONLY)

#: Kinds that are a decision waiting on the owner.
APPROVAL_KINDS = {"approval_pending"}
#: Kinds that need the owner even without an approval.
NEEDS_YOU_KINDS = {"security", "task_unrecorded", "tasks_interrupted",
                   "connector_down", "scheduled_failure", "seat_notice"}
#: (kind, source) pairs that are housekeeping: activity log only.
LOG_ONLY_PAIRS = {("info", "network"), ("schedule_retired", "scheduler"),
                  ("build", "lane"), ("build", "deploy")}
LOG_ONLY_SOURCES = {"lane", "deploy", "build", "guard"}

#: Seconds after the owner's last turn during which new cards are held.
QUIET_S = 90.0
_LAST_TURN = [0.0]
_TURN_LOCK = threading.Lock()


def is_approval(kind: str) -> bool:
    return str(kind or "") in APPROVAL_KINDS


def tier_for(kind: str, source: str, priority: str, tier: str | None = None) -> str:
    """The tier of a notification. An emitter may name one; an approval is
    always NEEDS_YOU whatever it names."""
    kind, source = str(kind or ""), str(source or "")
    if is_approval(kind):
        return NEEDS_YOU
    if tier in TIERS:
        return tier
    if (kind, source) in LOG_ONLY_PAIRS or source in LOG_ONLY_SOURCES:
        return LOG_ONLY
    if kind in NEEDS_YOU_KINDS or priority == "critical":
        return NEEDS_YOU
    if priority == "high" and kind in ("warning", "error"):
        return NEEDS_YOU
    return FYI


def mute_key(kind: str, source: str) -> str:
    return "%s|%s" % (kind or "", source or "")


def collapse_key(kind: str, source: str, title: str, dedupe_key: str | None) -> str:
    """Repeats of the same event share a key: the emitter's dedupe key, else
    kind + source + title with numbers folded ("move to Trash 3 conversations"
    and "... 5 conversations" are one kind of event)."""
    if dedupe_key:
        return str(dedupe_key)
    folded = re.sub(r"\d+", "#", str(title or "")).strip().lower()
    return "%s|%s|%s" % (kind or "", source or "", folded)


def muted_kinds() -> set:
    try:
        from agent_friday.core import _load_settings
        return set((_load_settings() or {}).get("notification_mutes") or [])
    except Exception:
        return set()


def is_muted(kind: str, source: str) -> bool:
    if is_approval(kind):
        return False                      # never muted away
    return mute_key(kind, source) in muted_kinds()


def note_owner_turn(when: float | None = None) -> None:
    """The owner just spoke to Friday (a chat or voice turn)."""
    with _TURN_LOCK:
        _LAST_TURN[0] = when if when is not None else time.time()


def owner_in_conversation(now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    with _TURN_LOCK:
        return (now - _LAST_TURN[0]) < QUIET_S
