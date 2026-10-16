"""The owner's standing choices: eight decisions Friday asks once and then follows.

The rule behind every one: no blanket rule decides for the owner. Each choice has a default (what the
owner chose when the question was put to them), is changeable in Settings, and is asked once in the
first-run setup conversation (`setup_questions`, the same hook shape as spending_caps.setup_questions).

Readers here are the one place each value is read and checked, so a bad value in settings.json reads as
the default rather than as something the code never meant:

  email_cloud_release            "ask" | "withhold"   (services/cloud_release.py)
  schedules.missed_default       "run_once" | "skip"  (services/scheduler.py, per job: `missed`)
  schedules.held_default         "retry" | "wait"     (services/scheduler.py, per job: `held`)
  unattended_send_undo_s         10..600 seconds      (services/gmail_send.py)
  trust_scoring.*                prior strength and Boost/Ban amounts (source_trust_graph.py)
  podcasts.hosts.b.name          the co-host's name   (services/podcast_engine.py)
  dock_by_asking                 "apply_with_undo" | "ask_first" (services/setting_proposals.py)
  calendar_delete_notify_guests  bool                 (services/calendar_write.py)
"""
from __future__ import annotations

import math

EMAIL_RELEASE_CHOICES = ("ask", "withhold")
MISSED_CHOICES = ("run_once", "skip")
HELD_CHOICES = ("retry", "wait")
DOCK_CHOICES = ("apply_with_undo", "ask_first")

UNDO_MIN_S, UNDO_MAX_S, UNDO_DEFAULT_S = 10, 600, 120
#: Sends the owner approves in person (a card, or chat with them there) keep this window.
ATTENDED_UNDO_S = 10

#: trust_scoring: key -> (default, lowest, highest). The starting score (0.5) is not a choice.
TRUST_SCORING = {
    "prior_strength": (3.0, 0.5, 20.0),
    "boost_lift": (0.15, 0.0, 0.5),
    "boost_floor": (0.70, 0.5, 0.95),
    "ban_ceiling": (0.15, 0.0, 0.5),
}


def _settings(settings=None) -> dict:
    if isinstance(settings, dict):
        return settings
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _block(s: dict, key: str) -> dict:
    v = (s or {}).get(key)
    return v if isinstance(v, dict) else {}


# ── readers ─────────────────────────────────────────────────────────────────

def email_cloud_release(settings=None) -> str:
    v = _settings(settings).get("email_cloud_release")
    return v if v in EMAIL_RELEASE_CHOICES else "ask"


def missed_default(settings=None) -> str:
    v = _block(_settings(settings), "schedules").get("missed_default")
    return v if v in MISSED_CHOICES else "run_once"


def held_default(settings=None) -> str:
    v = _block(_settings(settings), "schedules").get("held_default")
    return v if v in HELD_CHOICES else "retry"


def _num(v):
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def unattended_undo_seconds(settings=None) -> int:
    """The Undo window of an unattended pre-approved email: the setting, held to 10 s..10 min."""
    v = _num(_settings(settings).get("unattended_send_undo_s"))
    if v is None:
        return UNDO_DEFAULT_S
    return int(min(UNDO_MAX_S, max(UNDO_MIN_S, round(v))))


def trust_scoring(settings=None) -> dict:
    """The trust-scoring numbers, each held to its range (an out-of-range value reads as its bound)."""
    got = _block(_settings(settings), "trust_scoring")
    out = {}
    for k, (default, lo, hi) in TRUST_SCORING.items():
        v = _num(got.get(k))
        out[k] = default if v is None else min(hi, max(lo, v))
    return out


def dock_by_asking(settings=None) -> str:
    v = _settings(settings).get("dock_by_asking")
    return v if v in DOCK_CHOICES else "apply_with_undo"


def calendar_delete_notify_guests(settings=None) -> bool:
    return _settings(settings).get("calendar_delete_notify_guests") is True


# ── validation for POST /api/settings ───────────────────────────────────────

def validate(new_settings) -> dict | None:
    """An error dict ({status, message}) for a value outside its choices or range, or None. A bad value
    would otherwise be read as the default, so the row would show one thing and Friday do another."""
    ns = new_settings if isinstance(new_settings, dict) else {}

    def err(msg):
        return {"status": "error", "message": msg}
    if "email_cloud_release" in ns and ns["email_cloud_release"] not in EMAIL_RELEASE_CHOICES:
        return err("email_cloud_release is one of %s (got %r)" % (", ".join(EMAIL_RELEASE_CHOICES),
                                                                   ns["email_cloud_release"]))
    if "schedules" in ns:
        sch = ns["schedules"]
        if not isinstance(sch, dict):
            return err("schedules is {missed_default, held_default}")
        if "missed_default" in sch and sch["missed_default"] not in MISSED_CHOICES:
            return err("schedules.missed_default is run_once or skip (got %r)" % (sch["missed_default"],))
        if "held_default" in sch and sch["held_default"] not in HELD_CHOICES:
            return err("schedules.held_default is retry or wait (got %r)" % (sch["held_default"],))
    if "unattended_send_undo_s" in ns:
        v = _num(ns["unattended_send_undo_s"])
        if v is None or not UNDO_MIN_S <= v <= UNDO_MAX_S:
            return err("unattended_send_undo_s is a number of seconds from %d to %d (got %r)"
                       % (UNDO_MIN_S, UNDO_MAX_S, ns["unattended_send_undo_s"]))
    if "trust_scoring" in ns:
        ts = ns["trust_scoring"]
        if not isinstance(ts, dict):
            return err("trust_scoring is {%s}" % ", ".join(TRUST_SCORING))
        for k, v in ts.items():
            if k not in TRUST_SCORING:
                return err("trust_scoring has no %r; it has %s" % (k, ", ".join(TRUST_SCORING)))
            _d, lo, hi = TRUST_SCORING[k]
            n = _num(v)
            if n is None or not lo <= n <= hi:
                return err("trust_scoring.%s is a number from %s to %s (got %r)" % (k, lo, hi, v))
    if "dock_by_asking" in ns and ns["dock_by_asking"] not in DOCK_CHOICES:
        return err("dock_by_asking is apply_with_undo or ask_first (got %r)" % (ns["dock_by_asking"],))
    if "calendar_delete_notify_guests" in ns and not isinstance(ns["calendar_delete_notify_guests"], bool):
        return err("calendar_delete_notify_guests is true or false (got %r)" % (ns["calendar_delete_notify_guests"],))
    return None


# ── the first-run question hook ─────────────────────────────────────────────

def _get(s: dict, dotted: str):
    cur = s
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def setup_questions(settings=None) -> list:
    """The eight decisions, phrased for the first-run setup conversation: each {key, question, default,
    options, why, where, current}. `key` is the settings key the answer is saved to (dotted for a nested
    one); `where` is the Settings row that changes it later. Nothing here asks anyone; it says what to
    ask. Every key is in core.DEFAULT_SETTINGS and has a Settings control (a test holds that)."""
    s = _settings(settings)
    try:
        from agent_friday.services import podcast_engine as _pe
        cohost = _pe.cohost_default_name(s)
    except Exception:
        cohost = ""
    qs = [
        {"key": "email_cloud_release",
         "question": ("When you have chosen the cautious cloud setting and an email I read for you has a "
                      "paragraph about money (an invoice, a payment, a price), should I show you exactly that "
                      "paragraph and ask before it goes to the cloud model, or always keep it on this PC?"),
         "default": "ask", "options": list(EMAIL_RELEASE_CHOICES),
         "why": ("Money details are private, but sometimes the cloud model needs them to answer you. Asking "
                 "lets you decide per message, and you can tell me to remember the answer for a whole "
                 "thread or sender."),
         "where": "Settings > Privacy & Data > Money paragraphs in email"},
        {"key": "schedules.missed_default",
         "question": ("If one of your scheduled jobs was due while I was closed, should I run it once when I "
                      "next start, or skip it until its next time?"),
         "default": "run_once", "options": list(MISSED_CHOICES),
         "why": "A missed morning briefing is usually still wanted; a missed reminder may not be. Each job can differ.",
         "where": "Settings > General > Scheduled jobs (and each job's card in Workflows)"},
        {"key": "schedules.held_default",
         "question": ("If a scheduled job is held because the model you pinned it to is unavailable, should I "
                      "run it once as soon as that model is back, or wait for its next time?"),
         "default": "retry", "options": list(HELD_CHOICES),
         "why": "A pinned job never moves to another model; this only decides when it runs once its model returns.",
         "where": "Settings > General > Scheduled jobs (and each job's card in Workflows)"},
        {"key": "unattended_send_undo_s",
         "question": ("When a scheduled job sends an email you pre-approved, how long should I wait before it "
                      "goes, so you can undo it (anything from 10 seconds to 10 minutes)?"),
         "default": UNDO_DEFAULT_S, "options": {"min": UNDO_MIN_S, "max": UNDO_MAX_S, "unit": "seconds"},
         "why": "Nobody is watching an unattended send; a longer window gives you time to see the notice and take it back.",
         "where": "Settings > Privacy & Data > Pre-approved sends"},
        {"key": "trust_scoring",
         "question": ("News outlets start at a neutral trust score of 0.5. How quickly should evidence move them "
                      "off it, and how strong should your Boost and Ban be?"),
         "default": {k: v[0] for k, v in TRUST_SCORING.items()},
         "options": {k: {"min": v[1], "max": v[2]} for k, v in TRUST_SCORING.items()},
         "why": "A slower rate needs more evidence before a score moves; Boost and Ban are your own say, shown as their own line.",
         "where": "Settings > Advanced > News source trust (also News > Customize)"},
        {"key": "podcasts.hosts.b.name",
         "question": ("Podcasts are one voice unless you ask for two hosts. If you do, what should the co-host "
                      "be called?"),
         "default": cohost, "options": "a name",
         "why": "Two hosts need two names; I ask once, the first time you choose two hosts.",
         "where": "Settings > Voice > Podcasts > Co-host's name"},
        {"key": "dock_by_asking",
         "question": ("When you ask me to show or hide a workspace in the dock, should I just do it (with an "
                      "Undo), or ask you to confirm first?"),
         "default": "apply_with_undo", "options": list(DOCK_CHOICES),
         "why": "It changes only what the dock shows, and Undo puts it back.",
         "where": "Settings > Appearance > The dock"},
        {"key": "calendar_delete_notify_guests",
         "question": ("When you delete a calendar event that has guests, should the 'notify guests' box start "
                      "ticked (Google emails them a cancellation) or unticked?"),
         "default": False, "options": [False, True],
         "why": "Telling guests sends an email in your name; you can tick it each time.",
         "where": "Settings > Privacy & Data > Deleting calendar events"},
    ]
    for q in qs:
        q["current"] = _get(s, q["key"])
    return qs
