"""What the first-run conversation's choices DO: one function per step.

services/setup_chat.py is the state machine (which step, what Friday says,
resuming). This module turns an answer into the change it stands for, through
the store that already owns that setting, and builds the options a step
offers. Nothing here decides the order of the conversation or writes copy the
person reads; the words are in setup_chat_copy.

Every option a step offers is a dict with ``value`` and ``label``; the one
Friday would pick carries ``recommended: True`` and is labelled, never
pre-selected (first-run spec rule 7). A choice is applied only from the
person's own answer, or from "Use recommended for the rest", which the
person presses.
"""
from __future__ import annotations

import logging
import re

from agent_friday.services import setup_chat_copy as copy

_log = logging.getLogger("friday.setup_steps")


# ── settings, one block at a time ─────────────────────────────────────────────

def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _save(delta: dict, **kw) -> None:
    from agent_friday.core import _save_settings
    _save_settings(delta, **kw)


def _save_block(key: str, patch: dict) -> None:
    """Write `patch` into the settings block `key`, keeping its siblings.

    Several blocks are replaced wholesale by a top-level save, so the block is
    read and merged here rather than trusting the writer to merge it.
    """
    cur = _settings().get(key)
    block = dict(cur) if isinstance(cur, dict) else {}
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(block.get(k), dict):
            inner = dict(block[k])
            inner.update(v)
            block[k] = inner
        else:
            block[k] = v
    _save({key: block})


# ── her identity ──────────────────────────────────────────────────────────────

#: The local voices offered at setup, in this order, when they are installed.
#: Four, two each way, so the choice is a choice and not a catalogue.
VOICE_CHOICES = (
    ("af_heart", "Heart", "warm, American"),
    ("am_michael", "Michael", "calm, American"),
    ("bf_emma", "Emma", "clear, British"),
    ("bm_george", "George", "low, British"),
)

#: Test seam: callable() -> list of installed Kokoro voice ids.
INSTALLED_VOICES = None


def installed_voices() -> list:
    if INSTALLED_VOICES is not None:
        return list(INSTALLED_VOICES())
    try:
        from agent_friday.services import podcast_render
        return list(podcast_render.installed_voices())
    except Exception:
        return []


def voice_options() -> list:
    have = set(installed_voices())
    return [{"value": vid, "label": "%s (%s)" % (name, desc), "name": name}
            for vid, name, desc in VOICE_CHOICES if vid in have]


def apply_voice(voice: str) -> None:
    _save({"local_voice_kokoro_voice": voice})


def voice_sample(voice: str, agent: str = "Friday") -> bytes:
    """A short WAV of `voice` saying one line, synthesised on this computer."""
    from agent_friday.services import podcast_render
    line = "Hi, I'm %s. This is how I sound." % (agent or "Friday")
    pcm, _timings = podcast_render.render_lines([{"speaker": "a", "text": line}], {"a": voice})
    return podcast_render.wav_bytes(pcm)


PRONOUN_CHOICES = (("she/her", "She"), ("he/him", "He"), ("they/them", "They"),
                   ("name_only", "Just use my name"))

_PRONOUN_WORDS = {
    "she": "she/her", "her": "she/her", "she/her": "she/her",
    "he": "he/him", "him": "he/him", "he/him": "he/him",
    "they": "they/them", "them": "they/them", "they/them": "they/them",
    "name": "name_only", "name only": "name_only", "just use my name": "name_only",
    "use your name": "name_only", "name_only": "name_only",
}


def parse_pronouns(text: str) -> str | None:
    t = re.sub(r"[^a-z/ ]", "", str(text or "").lower()).strip()
    return _PRONOUN_WORDS.get(t)


def apply_pronouns(label: str, note: str = "") -> None:
    patch = {"pronouns": label}
    if note:
        patch["pronouns_note"] = note[:40]
    _save_block("identity", patch)


FRAMING_CHOICES = (("assistant", "An assistant"), ("colleague", "A colleague"),
                   ("family", "More like family"))


def apply_framing(framing: str) -> None:
    _save_block("identity", {"framing": framing})


# ── about the person ─────────────────────────────────────────────────────────

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_BY_NAME = {v.lower(): k for k, v in US_STATES.items()}


def parse_place(text: str) -> dict:
    """{"city", "state", "label"} from what the person typed.

    The state is read only from an explicit "City, ST" or "City, State Name";
    nothing is guessed from the city alone (two Springfields, one answer).
    """
    raw = " ".join(str(text or "").split())[:80].strip(" ,")
    if not raw:
        return {"city": "", "state": "", "label": ""}
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    city, state = parts[0], ""
    if len(parts) >= 2:
        tail = parts[1]
        if tail.upper() in US_STATES:
            state = tail.upper()
        elif tail.lower() in _STATE_BY_NAME:
            state = _STATE_BY_NAME[tail.lower()]
    label = city + (", " + US_STATES[state] if state else
                    (", " + ", ".join(parts[1:]) if len(parts) > 1 else ""))
    return {"city": city, "state": state, "label": label}


def valid_zone(name: str) -> str:
    """The IANA zone name if this computer knows it, else ""."""
    name = str(name or "").strip()
    if not name or len(name) > 64 or not re.fullmatch(r"[A-Za-z0-9_+\-/]+", name):
        return ""
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return name
    except Exception:
        return ""


def zone_words(zone: str) -> str:
    """A zone said the way a person says it: "Chicago time (America/Chicago)"."""
    city = zone.rsplit("/", 1)[-1].replace("_", " ")
    return "%s time (%s)" % (city, zone) if "/" in zone else zone


def apply_timezone(zone: str) -> None:
    _save({"timezone": zone})


#: The "About you" card's fact category and source while the core memory card
#: is not shipped; the panel lists and deletes exactly these.
FACT_CATEGORY = "bio"
FACT_SOURCE = "setup"


def note_about(text: str) -> str:
    """Write one "About you" line; returns its fact id ("" on failure)."""
    try:
        from agent_friday.services import user_model
        out = user_model.note_fact(FACT_CATEGORY, text, confidence=0.95, source=FACT_SOURCE)
        return str(out.get("fact_id") or "") if out.get("ok") else ""
    except Exception as e:
        _log.warning("setup could not write an About you line: %s", type(e).__name__)
        return ""


def about_lines() -> list:
    """[{id, text}] for the About you panel: the setup-sourced lines only."""
    try:
        from agent_friday.services import user_model
        rows = user_model.facts_from_source(FACT_CATEGORY, FACT_SOURCE)
    except Exception:
        return []
    return [{"id": r.get("fact_id"), "text": r.get("text")} for r in rows]


def forget_about_line(fact_id: str) -> bool:
    from agent_friday.services import user_model
    return bool(user_model.forget_fact(fact_id, source=FACT_SOURCE).get("ok"))


def shield_identifiers(tokens) -> list:
    """The person's own identifiers into the watchlist: scrubbed from anything
    sent to a cloud model. Returns what was added."""
    from agent_friday.services import privacy_shield_store as ps
    try:
        return ps.add("watchlist", tokens, source=FACT_SOURCE)
    except RuntimeError as e:
        _log.warning("setup could not add identifiers to the privacy shield: %s", e)
        return []


# ── privacy posture ──────────────────────────────────────────────────────────

#: Test seam: callable() -> the capability assessment cloud_consent makes.
CAPABILITY = None

POSTURES = ("local", "public", "everything")
_POSTURE_TO_CONSENT = {"local": "local_private", "public": "cloud_guarded",
                       "everything": "cloud_unrestricted"}


def capability() -> dict:
    if CAPABILITY is not None:
        return dict(CAPABILITY())
    try:
        from agent_friday.privacy import cloud_consent
        return dict(cloud_consent._capability_for_status(None) or {})
    except Exception as e:
        _log.warning("capability check failed: %s", type(e).__name__)
        return {"capable": False, "pending": False, "roles": {}, "chain_why": "the check failed"}


def _why_not_local(cap: dict) -> str:
    if cap.get("pending"):
        return copy.POSTURE_LOCAL_PENDING
    failing = [r for r, row in (cap.get("roles") or {}).items()
               if isinstance(row, dict) and not row.get("ok")]
    names = {"text": "thinking", "voice": "voice", "image": "pictures", "video": "video"}
    if failing:
        return copy.POSTURE_LOCAL_CANT.format(what=", ".join(names.get(r, r) for r in failing))
    return copy.POSTURE_LOCAL_CANT.format(what=str(cap.get("chain_why") or "the full job"))


def posture_view() -> dict:
    """The three cards, with what each means on THIS computer."""
    cap = capability()
    capable = bool(cap.get("capable"))
    rec = "local" if capable else "public"
    cards = []
    for value in POSTURES:
        means, costs = copy.POSTURE_MEANS[value], copy.POSTURE_COSTS[value]
        if value == "public" and not capable:
            means = copy.POSTURE_MEANS_PUBLIC_NO_LOCAL
        cards.append({
            "value": value, "label": copy.POSTURE_LABELS[value],
            "means": means, "costs": costs,
            "recommended": value == rec,
            "available": capable or value != "local",
            "reason": "" if (capable or value != "local") else _why_not_local(cap),
        })
    return {"capable": capable, "pending": bool(cap.get("pending")),
            "recommended": rec, "cards": cards}


_POSTURE_WORDS = (
    ("local", ("only on this pc", "only on this computer", "this pc", "local",
               "on this pc", "private", "only here", "here")),
    ("public", ("public", "cloud for public things", "public things", "guarded",
                "safeguards")),
    ("everything", ("everything", "cloud for everything", "all of it", "unrestricted",
                    "the cloud")),
)


def parse_posture(text: str) -> str | None:
    t = " ".join(re.sub(r"[^a-z ]", " ", str(text or "").lower()).split())
    if t in POSTURES:
        return t
    for value, words in _POSTURE_WORDS:
        if any(t == w or t.startswith(w + " ") for w in words):
            return value
    return None


def routing_for(posture: str, capable: bool) -> str:
    """The routing mode one posture answer implies.

    * Only on this PC: everything local.
    * Cloud for public things: chat stays local where the PC can think; on a
      PC that cannot, chat uses the cloud with every safeguard on (the card
      says so before the person chooses).
    * Cloud for everything: cloud.
    """
    if posture == "local":
        return "local_only"
    if posture == "public":
        return "local_only" if capable else "cloud_only"
    return "cloud_only"


class PostureUnavailable(ValueError):
    pass


def apply_posture(posture: str) -> dict:
    """Record the one answer everywhere it is read: the cloud consent, the
    routing mode and the scheduled-jobs answer. Returns what was written."""
    if posture not in POSTURES:
        raise ValueError(posture)
    view = posture_view()
    card = next(c for c in view["cards"] if c["value"] == posture)
    if not card["available"]:
        raise PostureUnavailable(card["reason"])
    from agent_friday.privacy import cloud_consent
    try:
        cloud_consent.record_consent(_POSTURE_TO_CONSENT[posture])
    except cloud_consent.ConsentRejected as e:
        raise PostureUnavailable(str(e)) from e
    mode = routing_for(posture, view["capable"])
    _save({"model_routing": {"mode": mode}}, owner_routing_change=True)
    try:
        from agent_friday.services import scheduled_cloud
        scheduled_cloud.save({"allow": posture != "local"})
    except Exception as e:
        _log.warning("setup could not record the scheduled-jobs answer: %s", type(e).__name__)
    return {"consent": _POSTURE_TO_CONSENT[posture], "routing_mode": mode,
            "scheduled_cloud": posture != "local"}


def parse_never_send(text: str) -> list:
    """One item per line or per comma; trimmed, deduplicated, at most 50."""
    out, seen = [], set()
    for part in re.split(r"[\n,;]+", str(text or "")):
        p = " ".join(part.split())[:120]
        if len(p) >= 2 and p.casefold() not in seen:
            seen.add(p.casefold())
            out.append(p)
    return out[:50]


def apply_never_send(items: list) -> list:
    from agent_friday.services import privacy_shield_store as ps
    return ps.add("never_send", items, source=FACT_SOURCE)


# ── money ────────────────────────────────────────────────────────────────────

MONEY_CHOICES = (("none", "No cap"), ("5", "$5 a month"), ("10", "$10 a month"),
                 ("25", "$25 a month"))
MONEY_RECOMMENDED = "10"


def parse_amount(text: str) -> float | None:
    """A monthly figure from "$12", "12 dollars", "twelve" is not attempted."""
    m = re.search(r"(\d{1,5}(?:\.\d{1,2})?)", str(text or ""))
    if not m:
        return None
    v = float(m.group(1))
    return v if 0 < v <= 10000 else None


def apply_monthly_cap(amount: float | None) -> None:
    """A figure turns the monthly hard stop on at that amount; None leaves it
    off. Either way the cap counts as asked (the same keys the spending-caps
    registry reads)."""
    from datetime import datetime, timezone
    if amount:
        _save_block("cost_budget", {"hard_stop_monthly": float(amount),
                                    "hard_stop_monthly_enabled": True})
    else:
        _save_block("cost_budget", {"hard_stop_monthly_enabled": False})
    _save_block("spending_caps", {
        "asked": True,
        "answered": {"cloud_monthly_stop": datetime.now(timezone.utc).isoformat(timespec="seconds")}})


def money_estimate() -> str:
    try:
        from agent_friday.services import scheduled_cloud
        est = scheduled_cloud.estimate()
        return "$%.2f" % float(est.get("total_usd_per_month") or 0)
    except Exception:
        return ""


# ── routines ─────────────────────────────────────────────────────────────────

#: The routines offered at setup: (schedule id, label, what it is). The time
#: shown comes from the schedule itself; nothing is ticked at first.
ROUTINES = (
    ("sch_news_morning", "Morning front page", "the news that matters to you, written each morning"),
    ("sch_front_page_evening", "Evening front page", "the day's news again in the evening"),
    ("sch_afternoon_briefing", "Afternoon briefing", "your day so far and what's next"),
    ("sch_weekly_editorial", "Weekly editorial", "a weekly opinion piece on what happened"),
    ("sch_heartbeat", "Hourly heartbeat", "checks mail and calendar for anything urgent; says nothing when nothing changed"),
)
PODCAST_ROUTINE = "podcast"
#: The podcast routine settings each schedule's episode is keyed by.
_PODCAST_KEYS = {"sch_news_morning": "front_page", "sch_front_page_evening": "front_page",
                 "sch_afternoon_briefing": "briefing", "sch_weekly_editorial": "editorial"}
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _when(rec: dict) -> str:
    spec = rec.get("spec") or {}
    trig = rec.get("trigger")
    hm = "%02d:%02d" % (int(spec.get("hour") or 0), int(spec.get("minute") or 0))
    if trig == "daily":
        return "every day at " + hm
    if trig == "weekly":
        try:
            return "%ss at %s" % (_WEEKDAYS[int(spec.get("weekday"))], hm)
        except (TypeError, ValueError, IndexError):
            return "weekly at " + hm
    if trig == "interval":
        every = int(spec.get("every_minutes") or 60)
        return "every hour" if every == 60 else "every %d minutes" % every
    return "on its own schedule"


def _schedules() -> dict:
    try:
        from agent_friday.services import scheduler
        return {r.get("id"): r for r in scheduler.list_schedules()}
    except Exception:
        return {}


def routines_view(posture: str = "") -> list:
    """The routines that exist on this install, each with when and where it runs."""
    have = _schedules()
    where = copy.ROUTINE_WHERE.get(posture or "", copy.ROUTINE_WHERE[""])
    out = []
    for sid, label, what in ROUTINES:
        rec = have.get(sid)
        if not rec:
            continue
        spec = rec.get("spec") or {}
        out.append({"id": sid, "label": label, "what": what, "when": _when(rec),
                    "trigger": rec.get("trigger"),
                    "time": ("%02d:%02d" % (int(spec.get("hour") or 0), int(spec.get("minute") or 0))
                             if rec.get("trigger") in ("daily", "weekly") else ""),
                    "where": where, "finish_notice": rec.get("finish_notice", True) is not False})
    if any(r["id"] in _PODCAST_KEYS for r in out):
        out.append({"id": PODCAST_ROUTINE, "label": "Podcast", "what": copy.ROUTINE_PODCAST_WHAT,
                    "when": "after each edition you tick", "trigger": "", "time": "",
                    "where": where, "finish_notice": True})
    return out


_HM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def apply_routines(picks: dict) -> list:
    """`picks` = {schedule id: {"on": bool, "time": "HH:MM", "finish_notice": bool}}.

    Every offered routine is written: ticked ones on (at their time), every
    other one off. Returns the labels of those switched on.
    """
    from agent_friday.services import scheduler
    picks = picks if isinstance(picks, dict) else {}
    on_labels = []
    have = _schedules()
    for sid, label, _what in ROUTINES:
        rec = have.get(sid)
        if not rec:
            continue
        p = picks.get(sid) if isinstance(picks.get(sid), dict) else {}
        on = bool(p.get("on"))
        patch = {"enabled": on}
        m = _HM.match(str(p.get("time") or ""))
        if on and m and rec.get("trigger") in ("daily", "weekly"):
            spec = dict(rec.get("spec") or {})
            spec.update({"hour": int(m.group(1)), "minute": int(m.group(2))})
            patch["spec"] = spec
        if "finish_notice" in p:
            patch["finish_notice"] = bool(p.get("finish_notice"))
        scheduler.update_schedule(sid, patch)
        if on:
            on_labels.append(label)
    pod = picks.get(PODCAST_ROUTINE) if isinstance(picks.get(PODCAST_ROUTINE), dict) else {}
    pod_on = bool(pod.get("on"))
    _save_block("podcasts", {"enabled_for_routines": {
        "front_page": pod_on, "briefing": pod_on, "weekly": pod_on, "editorial": pod_on}})
    if pod_on:
        on_labels.append("Podcast")
    return on_labels


def routines_all_off() -> None:
    """Nothing is scheduled without a tick: the state a skipped step leaves."""
    apply_routines({})


# ── the person's household ───────────────────────────────────────────────────

#: Windows' own page for adding another person's sign-in.
WINDOWS_OTHER_USERS = "ms-settings:otherusers"


# ── "Use recommended for the rest" ───────────────────────────────────────────

def recommended_for(stage: str):
    """The value the recommended option of `stage` answers with, or None when
    the step has no recommendation (the person's own choice)."""
    if stage == "posture":
        return posture_view()["recommended"]
    if stage == "money":
        return MONEY_RECOMMENDED
    if stage == "scheduled_cloud":
        return "yes"
    return None
