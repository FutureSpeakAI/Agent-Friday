"""Every spending cap Friday has, in one place, ready for the first-run setup
conversation to ask about.

The owner's ruling (north-star amendment A17, 2026-10-21): ONE cap ships on,
the daily cap on everything sent to paid cloud services, at a modest
DAILY_CAP_DEFAULT_USD a day. The person can change it at setup and in
Settings > Spending, and it is always shown in plain words (plain_words()).
Work that reaches it pauses with a card (services/spend_guard). Every other
cap below ships OFF, and None (or 0) means "no cap" everywhere its reader
reads it.

An install from before A17 keeps the caps it had: migrate_daily_cap() makes
its daily cap explicitly what it already was and queues one card
(push_daily_cap_card_if_pending) that explains the new cap and takes the
person to set it.

CAPS is the registry. Each entry says what the cap limits, in plain words,
the figure the setup conversation suggests, and how to read and write it
through the existing settings (the readers that enforce each cap are named in
`reader`). setup_questions() returns the caps not yet answered, phrased for
the owner. record_answer() is how the conversation saves an answer: a figure
turns the cap on, None leaves it off; either way it counts as answered.

Settings: `spending_caps` = {"asked": bool, "answered": {key: iso time},
"workspace_monthly_mpsi": None, "workspace_task_mpsi": None}, plus
MIGRATION_STAMP once migrate_settings() has run. The other caps keep the
settings keys their readers already use.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("friday.spending_caps")

NO_CAP_LABEL = "No cap (ask me during setup)"

#: The daily cap a new install starts with (A17): modest, on, the person's to change.
DAILY_CAP_DEFAULT_USD = 2.00


def _num(v) -> Optional[float]:
    """A cap figure, or None for no cap (None, 0, negative, unreadable)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _block(s: dict, key: str) -> dict:
    b = (s or {}).get(key)
    return b if isinstance(b, dict) else {}


# ── readers and writers, one pair per cap ───────────────────────────────────

def _get_creation(s):
    return _num((s or {}).get("daily_creation_budget_usd"))


def _set_creation(v):
    return {"daily_creation_budget_usd": v}


def _get_stop(period):
    def get(s):
        b = _block(s, "cost_budget")
        if not b.get("hard_stop_%s_enabled" % period):
            return None
        return _num(b.get("hard_stop_%s" % period))
    return get


def _set_stop(period):
    def put(v):
        if v is None:
            return {"cost_budget": {"hard_stop_%s_enabled" % period: False}}
        return {"cost_budget": {"hard_stop_%s" % period: v,
                                "hard_stop_%s_enabled" % period: True}}
    return put


def _get_caps_key(key):
    def get(s):
        v = _num(_block(s, "spending_caps").get(key))
        return int(v) if v is not None else None
    return get


def _set_caps_key(key):
    def put(v):
        return {"spending_caps": {key: (int(v) if v is not None else None)}}
    return put


#: The registry. `question` takes {name} (her name) and {suggested} (the
#: suggested figure in the cap's unit, already formatted).
CAPS: List[Dict[str, Any]] = [
    {
        "key": "daily_creation",
        "label": "Daily creation ceiling",
        "unit": "USD/day",
        "scope": "the daily creation: what is made unprompted each day",
        "suggested": 0.50,
        "question": ("Do you want a daily ceiling on what {name} spends making things "
                     "unprompted? Suggested {suggested} a day. Without one, {name} may "
                     "choose an image, music or a short film on any day."),
        "reader": "services/creations.py:_daily_budget_remaining",
        "get": _get_creation,
        "set": _set_creation,
    },
    {
        "key": "cloud_daily_stop",
        "label": "Daily cloud spending stop",
        "unit": "USD/day",
        "scope": "everything sent to cloud models in a day",
        "suggested": DAILY_CAP_DEFAULT_USD,
        "question": ("{name} stops spending on cloud AI after {suggested} a day, unless you "
                     "change it. Work that reaches it pauses and asks you; models on this "
                     "computer keep working. Keep {suggested}, pick another amount, or no "
                     "daily cap?"),
        "reader": "services/spend_guard.py:check",
        "get": _get_stop("daily"),
        "set": _set_stop("daily"),
    },
    {
        "key": "cloud_monthly_stop",
        "label": "Monthly cloud spending stop",
        "unit": "USD/month",
        "scope": "everything sent to cloud models in a month",
        "suggested": 50.00,
        "question": ("Do you also want a monthly limit on cloud spending? Work that "
                     "reaches it pauses the same way. Suggested {suggested} a month."),
        "reader": "services/spend_guard.py:check",
        "get": _get_stop("monthly"),
        "set": _set_stop("monthly"),
    },
    {
        "key": "workspace_monthly",
        "label": "Monthly task budget per workspace",
        "unit": "mψ/month per workspace",
        "scope": "the compute each workspace's background tasks may reserve in a month",
        "suggested": 1_000_000,
        "question": ("Do you want a monthly limit on the compute each workspace's "
                     "background tasks may use? Suggested {suggested} a month per "
                     "workspace."),
        "reader": "services/budget_enforcer.py:reserve_budget",
        "get": _get_caps_key("workspace_monthly_mpsi"),
        "set": _set_caps_key("workspace_monthly_mpsi"),
    },
    {
        "key": "workspace_task",
        "label": "Per-task budget",
        "unit": "mψ per task",
        "scope": "the most one background task may reserve",
        "suggested": 100_000,
        "question": ("And a limit on any single background task? Suggested {suggested} "
                     "a task."),
        "reader": "services/budget_enforcer.py:reserve_budget",
        "get": _get_caps_key("workspace_task_mpsi"),
        "set": _set_caps_key("workspace_task_mpsi"),
    },
]

_BY_KEY: Dict[str, Dict[str, Any]] = {c["key"]: c for c in CAPS}


def format_figure(cap: Dict[str, Any], value) -> str:
    """A figure in the cap's unit, as a person reads it; NO_CAP_LABEL for None."""
    if value is None:
        return NO_CAP_LABEL
    if cap["unit"].startswith("USD"):
        return "$%.2f" % float(value)
    return "%sψ" % format(int(value) // 1000, ",")


def _settings() -> dict:
    from agent_friday.core import _load_settings
    return _load_settings() or {}


def _her_name(s: dict) -> str:
    from agent_friday import brand
    return brand.her_name((s or {}).get("agent_name"))


def current(settings: Optional[dict] = None) -> List[Dict[str, Any]]:
    """Every cap with its value now (None: no cap) and its label for Settings."""
    s = _settings() if settings is None else settings
    answered = _block(s, "spending_caps").get("answered") or {}
    out = []
    for cap in CAPS:
        v = cap["get"](s)
        out.append({"key": cap["key"], "label": cap["label"], "unit": cap["unit"],
                    "value": v, "on": v is not None,
                    "display": format_figure(cap, v),
                    "answered": cap["key"] in answered})
    return out


def setup_questions(settings: Optional[dict] = None) -> List[Dict[str, Any]]:
    """The caps the owner has not answered yet, phrased for the first-run setup
    conversation to ask. A cap the owner has already set (in Settings, say)
    counts as answered. Nothing here asks anyone; it only says what to ask."""
    s = _settings() if settings is None else settings
    answered = _block(s, "spending_caps").get("answered") or {}
    name = _her_name(s)
    out = []
    for cap in CAPS:
        if cap["key"] in answered or cap["get"](s) is not None:
            continue
        out.append({
            "key": cap["key"],
            "label": cap["label"],
            "unit": cap["unit"],
            "scope": cap["scope"],
            "suggested": cap["suggested"],
            "question": cap["question"].format(
                name=name, suggested=format_figure(cap, cap["suggested"])),
        })
    return out


def record_answer(key: str, value) -> Dict[str, Any]:
    """Save the owner's answer for one cap: a positive figure turns it on,
    None (or 0) leaves it off. Either way the cap is answered and is not asked
    again. Returns {"ok", "key", "value", "display"} or {"ok": False, "error"}."""
    cap = _BY_KEY.get(key)
    if cap is None:
        return {"ok": False, "error": "no such spending cap %r; one of: %s"
                % (key, ", ".join(_BY_KEY))}
    v = _num(value)
    from agent_friday.core import _save_settings
    delta: Dict[str, Any] = cap["set"](v)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    caps_delta = dict(delta.get("spending_caps") or {})
    caps_delta["answered"] = {key: stamp}
    delta["spending_caps"] = caps_delta
    _save_settings(delta)
    log.info("spending cap %s answered: %s", key, format_figure(cap, v))
    return {"ok": True, "key": key, "value": v, "display": format_figure(cap, v)}


# ── the one-time migration for installs from before caps shipped off ────────
#
# Before this change settings.json was written out with every default, so an
# existing install has "daily_creation_budget_usd": 0.5 on disk. Read as a
# figure, that keeps the cap on after the update and makes setup_questions()
# treat it as answered, though nobody chose it. migrate_settings() clears the
# old default (exactly 0.5, with no answer recorded) once, logs it, and stamps
# the block so it never runs again: a 0.5 the owner sets later is theirs.
# budget_enforcer does the same for budgets.db rows at its old defaults.

#: Stamped into settings.spending_caps once migrate_settings() has run.
MIGRATION_STAMP = "defaults_off_migrated"
#: The shipped default this migration clears (origin/main before caps shipped off).
OLD_DAILY_CREATION_DEFAULT = 0.5

#: The one stamp this process gives the migration. Until the stamp is on disk
#: (the persist is tried once per process and can fail), every read applies the
#: migration in memory again; each of those must carry the SAME value, or two
#: reads of an unchanged settings.json differ, and a later save persists
#: whichever stamp it happened to see.
_PROCESS_STAMP: List[str] = []


def _migration_stamp() -> str:
    if not _PROCESS_STAMP:
        _PROCESS_STAMP.append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return _PROCESS_STAMP[0]


def migrate_settings(settings: dict) -> Optional[Dict[str, Any]]:
    """Clear the old shipped daily creation ceiling, once. Pure apart from the
    log line.

    `settings` is the merged settings dict and is updated in place (the
    spending_caps block is replaced by a new dict, never mutated, since it may
    be DEFAULT_SETTINGS' own). Returns the delta to persist, or None when the
    migration has already run (the stored block carries the stamp: it is never
    re-stamped). The delta always carries the stamp, so even a run that changes
    nothing is recorded and never repeated. Until it is persisted, every
    in-memory application in this process uses the same stamp value.
    """
    blk = _block(settings, "spending_caps")
    if blk.get(MIGRATION_STAMP):
        return None
    answered = blk.get("answered") if isinstance(blk.get("answered"), dict) else {}
    stamp = _migration_stamp()
    delta: Dict[str, Any] = {"spending_caps": {MIGRATION_STAMP: stamp}}
    v = (settings or {}).get("daily_creation_budget_usd")
    try:
        is_old_default = (not isinstance(v, bool) and v is not None
                          and float(v) == OLD_DAILY_CREATION_DEFAULT)
    except (TypeError, ValueError):
        is_old_default = False
    if is_old_default and "daily_creation" not in answered:
        settings["daily_creation_budget_usd"] = None
        delta["daily_creation_budget_usd"] = None
        log.info("spending caps: the daily creation ceiling of $%.2f was the old shipped "
                 "default, never chosen by the owner; it is now off (no cap) and the "
                 "first-run setup conversation will ask about it. One-time migration "
                 "(settings.spending_caps.%s).", OLD_DAILY_CREATION_DEFAULT, MIGRATION_STAMP)
    else:
        log.info("spending caps: one-time defaults-off migration found nothing to "
                 "change (daily_creation_budget_usd=%r, answered=%s)",
                 v, "yes" if "daily_creation" in answered else "no")
    settings["spending_caps"] = dict(blk, **{MIGRATION_STAMP: stamp})
    return delta


# ── A17: the daily cap in plain words, the migration, the one-time card ─────

def daily_cap(settings: Optional[dict] = None) -> Optional[float]:
    """The daily cap on paid cloud spend in dollars, or None for no daily cap."""
    s = _settings() if settings is None else settings
    return _BY_KEY["cloud_daily_stop"]["get"](s)


def plain_words(settings: Optional[dict] = None) -> str:
    """The daily cap as one sentence a person understands (A17)."""
    s = _settings() if settings is None else settings
    name = _her_name(s)
    cap = daily_cap(s)
    if cap is None:
        return ("There is no daily cap: %s can spend on cloud AI without a daily limit. "
                "You can set one here." % name)
    return ("%s stops spending on cloud AI after $%.2f a day. Work that reaches it pauses "
            "and asks you. Models on this computer are never capped." % (name, cap))


#: Stamped into settings.spending_caps once migrate_daily_cap() has run.
DAILY_CAP_STAMP = "daily_cap_on_migrated"
#: The one-time card's state, kept in the install-origin record beside
#: settings.json (core.install_choices_state), which wholesale rewrites of
#: settings.json cannot reach: "pending" (show once), "shown", "not_needed".
CARD_KEY = "daily_cap_card"


def seed_choices(seed: dict, origin: str) -> None:
    """The first settings.json a process writes (no file yet). A new install
    keeps the defaults: the daily cap on. An install that existed and lost its
    settings.json starts with no daily cap, as before A17."""
    from agent_friday import core
    if origin != core.NEW_INSTALL:
        seed["cost_budget"] = dict(_block(seed, "cost_budget"), hard_stop_daily_enabled=False)


def _card_state() -> Optional[str]:
    from agent_friday import core
    v = core.install_choices_state().get(CARD_KEY)
    return v if v in ("pending", "shown", "not_needed") else None


def _set_card_state(value: str) -> None:
    from agent_friday import core
    core.update_install_choices_state(**{CARD_KEY: value})


def migrate_daily_cap(settings: dict, stored: Optional[dict], origin: str) -> Optional[Dict[str, Any]]:
    """An install from before A17 keeps the daily cap it had, once.

    `settings` is the merged settings dict (updated in place); `stored` is the
    file as read; `origin` is core.install_origin(). A NEW install keeps the
    defaults (the cap on) and needs no card. An EXISTING one: before A17 a
    daily cap that was never set read as off, so a stored cost_budget without
    `hard_stop_daily_enabled` (or no cost_budget at all) is written down as
    explicitly off, with the default figure ready for when the person turns it
    on; the new default must not switch it on behind anyone's back. If its
    daily cap ends up off and the person never answered it, the one-time card
    is queued (once per install: its state lives outside settings.json).
    Returns the delta to persist, or None when the migration has already run.
    """
    from agent_friday import core
    blk = _block(settings, "spending_caps")
    if blk.get(DAILY_CAP_STAMP):
        return None
    stamp = _migration_stamp()
    delta: Dict[str, Any] = {"spending_caps": {DAILY_CAP_STAMP: stamp}}
    settings["spending_caps"] = dict(blk, **{DAILY_CAP_STAMP: stamp})
    if origin == core.NEW_INSTALL:
        if _card_state() is None:
            _set_card_state("not_needed")
        return delta
    stored_cb = (stored or {}).get("cost_budget")
    stored_cb = stored_cb if isinstance(stored_cb, dict) else None
    if stored_cb is None or "hard_stop_daily_enabled" not in stored_cb:
        fix: Dict[str, Any] = {"hard_stop_daily_enabled": False}
        if stored_cb is None or "hard_stop_daily" not in stored_cb:
            fix["hard_stop_daily"] = DAILY_CAP_DEFAULT_USD
        # A whole block when none was stored, so a later save of one toggle
        # never leaves the others unset.
        cb = dict(_block(settings, "cost_budget"), **fix)
        settings["cost_budget"] = cb
        delta["cost_budget"] = dict(cb) if stored_cb is None else fix
    answered = blk.get("answered") if isinstance(blk.get("answered"), dict) else {}
    card = _card_state()
    if card is None:
        card = "pending" if (daily_cap(settings) is None
                             and "cloud_daily_stop" not in answered) else "not_needed"
        try:
            _set_card_state(card)
        except Exception as e:
            log.warning("the daily cap card's state could not be saved (%s); no card", e)
    log.info("spending caps: this install is from before the daily cap shipped on; it "
             "keeps its daily cap as it was (%s); card: %s. One-time migration "
             "(settings.spending_caps.%s).",
             format_figure(_BY_KEY["cloud_daily_stop"], daily_cap(settings)), card,
             DAILY_CAP_STAMP)
    return delta


CARD_TITLE = "New: a daily spending cap"


def card_body(settings: Optional[dict] = None) -> str:
    s = _settings() if settings is None else settings
    name = _her_name(s)
    return ("%s can now stop spending on cloud AI after an amount you choose each day. "
            "New installs start at $%.2f a day. Yours has no daily cap, so nothing has "
            "changed: set one, or keep no cap, in Settings > Spending. Work that reaches "
            "a cap pauses and asks you; models on this computer are never capped."
            % (name, DAILY_CAP_DEFAULT_USD))


CARD_ACTIONS = [{"label": "Set my daily cap", "workspace": "settings", "tab": "costs"}]


def push_daily_cap_card_if_pending() -> bool:
    """Show the one-time card to an install from before A17, once. Returns
    True when it was pushed. It is marked shown only after the push went
    through, so a failed push is tried again at the next start; the dedupe key
    keeps a retry from making a second card."""
    s = _settings()          # runs the migration, which queues the card
    if _card_state() != "pending":
        return False
    if daily_cap(s) is not None or "cloud_daily_stop" in (
            _block(s, "spending_caps").get("answered") or {}):
        # The person has set or answered the cap since the card was queued:
        # its words ("yours has no daily cap") would no longer be true.
        _set_card_state("not_needed")
        return False
    try:
        import agent_friday.notifications_engine as _ne
        _ne.push(title=CARD_TITLE, body=card_body(s), priority="high",
                 source="spending-caps", kind="spend_cap_intro", emitter="SC1",
                 dedupe_key="spend-cap-intro", actions=list(CARD_ACTIONS),
                 target={"workspace": "settings", "tab": "costs"})
    except Exception as e:
        log.warning("the daily cap card could not be shown; it is tried again at the "
                    "next start: %s", e)
        return False
    _set_card_state("shown")
    return True


def mark_asked() -> None:
    """The setup conversation has been through the caps (answered or not)."""
    from agent_friday.core import _save_settings
    _save_settings({"spending_caps": {"asked": True}})
