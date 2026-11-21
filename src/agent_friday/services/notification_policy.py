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

Dismissal is keyed to the JOB a card is about (a schedule, a run, an approval),
not to the card: dismissing it hides that job's cards at that rank and below,
on every surface, across restarts. A later card for the same job shows again
only when its rank is higher (a failure after progress). When the job's
failure resolves, the dismissal drops back to the FYI rank, so the next
failure is news again.

Cards are grouped by job, then kind, then source. Memory proposals (approvals
that would save something into Friday's memory) form one group whose items
are kept or skipped one by one. Clearing a group never clears an approval.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

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

INTERRUPT_SETTING = "notification_interrupt_rules"


@dataclass(frozen=True)
class InterruptRule:
    """One thing the owner has said may interrupt him. It names the emitters (services/notice_registry)
    whose notice is that thing HAPPENING WELL, never the words a notice uses."""
    id: str
    words: str
    emitters: tuple
    default_on: bool


#: The owner's rule (2026-11-21): the successful completion or delivery of a scheduled task or routine
#: may interrupt; status and routing notices and news do not; a failure keeps its own tier above.
#: The briefing and the weekly editions are his to decide, so they start off.
INTERRUPT_RULES = (
    InterruptRule("scheduled_runs", "A scheduled job that finished successfully.", ("E07",), True),
    InterruptRule("front_page", "A Front Page edition that was delivered.", ("E11",), True),
    InterruptRule("daily_creation", "The Daily Creation that was made.", ("E17",), True),
    InterruptRule("daily_briefing", "The daily briefing that was delivered.", ("E14",), False),
    InterruptRule("weekly_editions", "The weekly digest and the weekly editorial that were delivered.",
                  ("E12", "E13"), False),
)


def _chosen_rules() -> dict:
    """rule id -> on or off, as the owner set them; anything not a plain yes or no is ignored."""
    try:
        from agent_friday.core import _load_settings
        stored = (_load_settings() or {}).get(INTERRUPT_SETTING)
    except Exception:
        stored = None
    if not isinstance(stored, dict):
        return {}
    known = {rule.id for rule in INTERRUPT_RULES}
    return {rule_id: on for rule_id, on in stored.items() if rule_id in known and isinstance(on, bool)}


def rules_in_words() -> list:
    """The rule, as the owner reads and edits it: [{id, words, on}]."""
    chosen = _chosen_rules()
    return [{"id": rule.id, "words": rule.words, "on": chosen.get(rule.id, rule.default_on)}
            for rule in INTERRUPT_RULES]


def interrupts_by_rule(emitter: str) -> bool:
    """True when this emitter's notice is the successful completion or delivery of something the owner
    said may interrupt him, and that rule is on."""
    if not emitter:
        return False
    chosen = _chosen_rules()
    return any(emitter in rule.emitters and chosen.get(rule.id, rule.default_on) for rule in INTERRUPT_RULES)


#: Seconds after the owner's last turn during which new cards are held.
QUIET_S = 90.0
_LAST_TURN = [0.0]
_TURN_LOCK = threading.Lock()


def is_approval(kind: str) -> bool:
    return str(kind or "") in APPROVAL_KINDS


def _safety_critical(kind: str, source: str = "") -> bool:
    """Spend halt, credential problem, budget alert, seat change, a cLaw block
    (services/notice_registry): always reach the owner."""
    try:
        from agent_friday.services import notice_registry
        return notice_registry.is_safety_critical(kind, source=source)
    except Exception:
        return False


def _named_kinds() -> set:
    """Kinds this policy decides by name; the encoder head never second-guesses them."""
    return set(APPROVAL_KINDS) | set(NEEDS_YOU_KINDS) | {k for k, _ in LOG_ONLY_PAIRS}


def tier_for(kind: str, source: str, priority: str, tier: str | None = None,
             text: str | None = None, emitter: str | None = None) -> str:
    """The tier of a notification. An emitter may name one; an approval is
    always NEEDS_YOU whatever it names. `text` (title and body) lets the local
    encoder head raise an FYI of an unnamed kind to NEEDS_YOU; it is the only
    thing it can do (services/triage_heads). `emitter` lets the owner's interrupt rule raise the
    successful completion of a scheduled routine; it is decided before the head is asked."""
    kind, source = str(kind or ""), str(source or "")
    if is_approval(kind):
        return NEEDS_YOU
    if tier in TIERS:
        return tier
    if _safety_critical(kind, source):
        return NEEDS_YOU
    if (kind, source) in LOG_ONLY_PAIRS or source in LOG_ONLY_SOURCES:
        return LOG_ONLY
    if kind in NEEDS_YOU_KINDS or priority == "critical":
        return NEEDS_YOU
    if priority == "high" and kind in ("warning", "error"):
        return NEEDS_YOU
    if tier is None and interrupts_by_rule(str(emitter or "")):
        return NEEDS_YOU
    if tier is None and text:
        try:
            from agent_friday.services import triage_heads
            return triage_heads.notification_tier(
                FYI, kind, source, priority, text,
                needs_you=NEEDS_YOU, known_kinds=_named_kinds())
        except Exception:
            return FYI
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
    if _safety_critical(kind, source):
        return False                      # a spend halt is not a thing to mute
    return mute_key(kind, source) in muted_kinds()


#: Tools and approval kinds that propose a change to Friday's memory.
MEMORY_TOOLS = {"learn_skill", "correct_wiki", "propose_wiki_update", "update_core_memory"}
MEMORY_APPROVAL_KINDS = {"learning_skill_change"}
MEMORY_TITLE = "Save something into Friday's memory"
MEMORY_GROUP = "memory_proposals"

#: Emitter key prefixes that name the job a card is about.
#: A success has its own key: it can be a needs-you card now (the owner's interrupt rule), and
#: dismissing it must never hide that job's next failure.
_JOB_PREFIXES = (("sched-fail:", "sched:"), ("sched-wait:", "sched:"),
                 ("sched-ok:", "sched-ok:"), ("run:", "run:"), ("approval:", "approval:"))


def rank(tier: str, priority: str = "") -> int:
    """How loud a card is: FYI 1, NEEDS_YOU 2, critical 3. A dismissed job
    shows again only for a card of a higher rank than the one dismissed."""
    if priority == "critical":
        return 3
    return 2 if tier == NEEDS_YOU else 1


def job_for(*, job: str | None = None, meta: dict | None = None,
            dedupe_key: str | None = None, resolve_key: str | None = None) -> str | None:
    """The job a notification is about: the emitter's `job`, else a job, run
    or task id in its meta, else the job its scheduler/run/approval key names.
    None when it is about no job (a one-off)."""
    if job:
        return str(job)
    m = meta if isinstance(meta, dict) else {}
    for field, prefix in (("job_id", "job:"), ("run_id", "run:"), ("task_id", "task:")):
        if m.get(field):
            return prefix + str(m[field])
    for key in (dedupe_key, resolve_key):
        if not key:
            continue
        key = str(key)
        for pre, out in _JOB_PREFIXES:
            if key.startswith(pre):
                return out + key[len(pre):]
    return None


def dismissal_key(n: dict) -> str:
    """What a dismissal of this card is remembered under: its job, else the
    thing it reports (its collapse key)."""
    job = n.get("job") or job_for(meta=n.get("meta"), dedupe_key=n.get("dedupe_key"),
                                  resolve_key=n.get("resolve_key"))
    if job:
        return str(job)
    return "thing:" + str(n.get("collapse_key") or n.get("dedupe_key") or n.get("id") or "")


def is_memory_proposal(record: dict) -> bool:
    """An approval record that would save something into Friday's memory."""
    if not isinstance(record, dict):
        return False
    if record.get("kind") in MEMORY_APPROVAL_KINDS:
        return True
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    if payload.get("tool") in MEMORY_TOOLS:
        return True
    return MEMORY_TITLE.lower() in str(record.get("title") or "").lower()


def group_key(n: dict) -> str:
    """The group a card belongs to: a named group (memory proposals), else its
    job, else its kind and source."""
    meta = n.get("meta") if isinstance(n.get("meta"), dict) else {}
    if meta.get("group"):
        return str(meta["group"])
    if n.get("job"):
        return "job:" + str(n["job"])
    return "kind:%s|%s" % (n.get("kind") or "", n.get("source") or "")


def note_owner_turn(when: float | None = None) -> None:
    """The owner just spoke to Friday (a chat or voice turn). Also the moment
    the local work scheduler's idle clock restarts."""
    with _TURN_LOCK:
        _LAST_TURN[0] = when if when is not None else time.time()
    try:
        from agent_friday.services import local_scheduler as _ls
        _ls.mark_activity("turn", when)
    except Exception:
        pass


def owner_in_conversation(now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    with _TURN_LOCK:
        return (now - _LAST_TURN[0]) < QUIET_S
