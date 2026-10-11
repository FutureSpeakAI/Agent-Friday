"""Consent, retention and suggestions for the meeting recorder.

Everything the owner chooses about recording other people lives here, apart
from the recorder itself (services/meeting_capture.py):

* the consent line, an editable sentence the owner can copy into a call chat or
  have Friday say aloud when recording starts, and the jurisdiction note that
  goes with it;
* retention: how long audio, transcripts and notes are kept, offered with
  nothing preselected;
* the "take notes?" suggestion, shown at most once per calendar event and
  switched off after two declines;
* the retention sweep, which runs as an item in the work queue.

Nothing here starts a recording. Starting is the owner's act on screen and goes
only through routes/meetings.py (see tests/unit/test_meeting_start_guard.py).
Nothing here sends anything anywhere: Friday does not post the consent line
into other apps, she gives it to the owner to paste.

TODO(MT-2): a cloud grant for one meeting's notes (cloud_intent + the egress
gate, an explicit per-meeting choice). Until then meeting content has no way
out and tests/unit/test_meeting_egress_canary.py holds that line.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("friday.meeting_policy")

# ── Consent ─────────────────────────────────────────────────────────────────

DEFAULT_CONSENT_LINE = ("Heads up: I'm recording this call so I can take notes. "
                        "The recording stays on my computer. Tell me now if "
                        "you'd rather I didn't.")

JURISDICTION_NOTE = ("Some places require everyone's consent to record a call "
                     "or a meeting, and some only the person recording. Friday "
                     "can't decide the law for you, so the default is to ask "
                     "everyone.")

#: "ask_everyone" is the default; "owner_decides" is for when the owner has
#: checked what applies. Both still need the owner's own confirmation to start.
CONSENT_MODES = ("ask_everyone", "owner_decides")
DEFAULT_CONSENT_MODE = "ask_everyone"
CONSENT_LINE_MAX = 500

# ── Retention ───────────────────────────────────────────────────────────────

#: value, label, hint. Nothing is preselected: an unset choice is "" and means
#: nothing is deleted on a timer.
AUDIO_RETENTION = (
    ("delete_after_notes", "Delete the audio once the notes are made",
     "The transcript and notes stay."),
    ("30_days", "Delete the audio after 30 days", "The transcript and notes stay."),
    ("keep", "Keep the audio", "Until you delete it."),
)
RECOMMENDED_AUDIO = "delete_after_notes"
AUDIO_DAYS = {"30_days": 30}

TEXT_RETENTION = (
    ("keep", "Keep transcripts and notes", "Until you delete them."),
    ("days", "Delete transcripts and notes after a number of days",
     "The whole meeting is removed, audio included."),
)
TEXT_DAYS_DEFAULT = 90
TEXT_DAYS_MAX = 3650

SETTING_KEYS = ("meeting_consent_line", "meeting_consent_mode",
                "meeting_speak_consent", "meeting_suggestions",
                "meeting_audio_retention", "meeting_text_retention",
                "meeting_text_retention_days")

# ── Suggestions ─────────────────────────────────────────────────────────────

#: A suggestion that was declined this many times switches itself off.
DECLINES_BEFORE_OFF = 2
_OFFERED_KEEP = 300


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _save(delta: dict) -> None:
    from agent_friday.core import _save_settings
    _save_settings(delta)


# Seams for tests: the settings store and the clock.
_get_settings = _settings
_put_settings = _save


def _state_path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "meetings" / "policy_state.json"


_STATE_LOCK = threading.RLock()


def _load_state() -> dict:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_state(d: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, p)


# ── Consent line ────────────────────────────────────────────────────────────

def clean_consent_line(text) -> str:
    return " ".join(str(text or "").split())[:CONSENT_LINE_MAX]


def consent() -> dict:
    """What the start card shows: the line (editable), the note and the mode."""
    s = _get_settings()
    line = clean_consent_line(s.get("meeting_consent_line")) or DEFAULT_CONSENT_LINE
    mode = s.get("meeting_consent_mode")
    return {"line": line, "default_line": DEFAULT_CONSENT_LINE,
            "edited": line != DEFAULT_CONSENT_LINE,
            "mode": mode if mode in CONSENT_MODES else DEFAULT_CONSENT_MODE,
            "modes": list(CONSENT_MODES),
            "jurisdiction_note": JURISDICTION_NOTE,
            "speak": bool(s.get("meeting_speak_consent"))}


def set_consent(line=None, mode=None, speak=None) -> dict:
    delta = {}
    if line is not None:
        text = clean_consent_line(line)
        delta["meeting_consent_line"] = "" if text == DEFAULT_CONSENT_LINE else text
    if mode is not None:
        if mode not in CONSENT_MODES:
            raise ValueError("unknown consent mode")
        delta["meeting_consent_mode"] = mode
    if speak is not None:
        delta["meeting_speak_consent"] = bool(speak)
    if delta:
        _put_settings(delta)
    return consent()


# ── Retention ───────────────────────────────────────────────────────────────

def audio_retention() -> str:
    v = _get_settings().get("meeting_audio_retention")
    return v if v in dict((o[0], o) for o in AUDIO_RETENTION) else ""


def text_retention() -> tuple:
    """(mode, days): mode is "", "keep" or "days"."""
    s = _get_settings()
    mode = s.get("meeting_text_retention")
    if mode not in ("keep", "days"):
        return "", 0
    try:
        days = int(s.get("meeting_text_retention_days") or TEXT_DAYS_DEFAULT)
    except (TypeError, ValueError):
        days = TEXT_DAYS_DEFAULT
    return mode, max(1, min(TEXT_DAYS_MAX, days))


def retention() -> dict:
    """The retention choices, for the page. `selected` is "" until the owner
    chooses; `recommended` marks one option and never selects it."""
    t_mode, t_days = text_retention()
    return {
        "audio": {"selected": audio_retention(),
                  "options": [{"value": v, "label": l, "hint": h,
                               "recommended": v == RECOMMENDED_AUDIO}
                              for v, l, h in AUDIO_RETENTION]},
        "text": {"selected": t_mode, "days": t_days or TEXT_DAYS_DEFAULT,
                 "options": [{"value": v, "label": l, "hint": h, "recommended": False}
                             for v, l, h in TEXT_RETENTION]},
    }


def set_retention(audio=None, text=None, days=None) -> dict:
    delta = {}
    if audio is not None:
        if audio not in [o[0] for o in AUDIO_RETENTION]:
            raise ValueError("unknown audio retention")
        delta["meeting_audio_retention"] = audio
    if text is not None:
        if text not in [o[0] for o in TEXT_RETENTION]:
            raise ValueError("unknown transcript retention")
        delta["meeting_text_retention"] = text
    if days is not None:
        n = int(days)
        if not 1 <= n <= TEXT_DAYS_MAX:
            raise ValueError("days must be between 1 and %d" % TEXT_DAYS_MAX)
        delta["meeting_text_retention_days"] = n
    if delta:
        _put_settings(delta)
    return retention()


def _age_days(iso, now: datetime):
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds() / 86400.0
    except Exception:
        return None


def sweep(manager, now: datetime | None = None) -> dict:
    """Apply the owner's retention choices once. Returns counts only.

    Audio deletion and meeting deletion are separate real deletions through the
    manager, which records each in the activity ledger as a count. A meeting
    that is recording or still being finished is never touched.
    """
    now = now or datetime.now()
    a_mode = audio_retention()
    t_mode, t_days = text_retention()
    audio_files = meetings = 0
    for row in manager.list():
        mid = row.get("id")
        if not mid or row.get("state") in ("recording", "transcribing", "unreadable"):
            continue
        age = _age_days(row.get("ended_at") or row.get("started_at"), now)
        if t_mode == "days" and age is not None and age >= t_days:
            try:
                manager.delete(mid, reason="retention")
                meetings += 1
                continue
            except Exception as e:
                log.info("retention: a meeting was not deleted: %s", e)
        if not manager.has_audio(mid):
            continue
        due = ((a_mode == "30_days" and age is not None and age >= AUDIO_DAYS["30_days"])
               or (a_mode == "delete_after_notes" and row.get("summary_status") == "done"))
        if due:
            try:
                audio_files += manager.delete_audio(mid, reason="retention").get("audio_files", 0)
            except Exception as e:
                log.info("retention: audio was not deleted: %s", e)
    return {"audio_files": audio_files, "meetings": meetings}


# ── The retention sweep as a work-queue item ────────────────────────────────

RETENTION_TITLE = "Meeting retention sweep"
RETENTION_SPEC = json.dumps({"kind": "meeting_retention"})
RETENTION_EVERY_S = 20 * 3600


def _is_ours(item: dict) -> bool:
    return item.get("title") == RETENTION_TITLE and item.get("spec") == RETENTION_SPEC


def queue_retention(*, now: float | None = None) -> dict | None:
    """Put the sweep in the work queue unless one is already waiting or one ran
    recently. Returns the queued item, or None. Does nothing while no retention
    choice has been made: with nothing chosen, nothing is deleted."""
    if not (audio_retention() or text_retention()[0] == "days"):
        return None
    from agent_friday.services import work_queue as wq
    now = time.time() if now is None else now
    with _STATE_LOCK:
        mine = [i for i in wq.all_items() if _is_ours(i)]
        if any(i["status"] in ("queued", "running") for i in mine):
            return None
        last = max([i.get("finished_at") or 0 for i in mine] + [0])
        if now - last < RETENTION_EVERY_S:
            return None
        return wq.enqueue(RETENTION_TITLE, RETENTION_SPEC, cls="background",
                          disposition="when_away")


def run_retention(manager) -> dict:
    """Run the queued sweep item(s) if the owner is away and not in a meeting.

    The item is the unit the work queue tracks: queued, running, done or
    failed, with the counts as its result.
    """
    from agent_friday.services import turn_queue as tq
    from agent_friday.services import work_queue as wq
    if tq.background_pause_reason():
        return {"ran": 0, "why": "you are in a meeting"}
    items = [i for i in wq.pending("background") if _is_ours(i)]
    if not items:
        return {"ran": 0, "why": "nothing queued"}
    if not wq.is_away():
        return {"ran": 0, "why": "waiting until you are away"}
    ran = 0
    result = {}
    for it in items:
        wq.update(it["id"], status="running", started_at=time.time())
        try:
            result = sweep(manager)
            wq.update(it["id"], status="done", result=result, finished_at=time.time())
        except Exception as e:  # noqa: BLE001 - recorded on the item
            wq.update(it["id"], status="failed", error=str(e)[:300],
                      finished_at=time.time())
        ran += 1
    return {"ran": ran, "result": result}


def retention_tick(manager=None) -> dict:
    """One scheduler tick: queue the sweep when it is due, run it when allowed."""
    try:
        if manager is None:
            from agent_friday.services import meeting_capture as mc
            manager = mc.get_manager()
        queue_retention()
        return run_retention(manager)
    except Exception as e:  # noqa: BLE001 - a tick never raises
        log.info("meeting retention tick failed: %s", e)
        return {"ran": 0, "why": "error"}


# ── "Take notes?" suggestions (No Pestering) ────────────────────────────────

def _event_key(event: dict, today: str) -> str:
    raw = str(event.get("event_id") or "") or ("%s|%s" % (event.get("title") or "", today))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def suggestion_state() -> dict:
    s = _get_settings()
    st = _load_state()
    declines = int(st.get("declines") or 0)
    on = bool(s.get("meeting_suggestions", True))
    return {"enabled": on and declines < DECLINES_BEFORE_OFF,
            "setting_on": on, "declines": declines,
            "off_after_declines": declines >= DECLINES_BEFORE_OFF}


def suggest(event: dict | None, *, recording: bool = False,
            today: str | None = None) -> dict | None:
    """The suggestion card for `event`, or None.

    Asking spends nothing: the same card comes back until the page reports it
    was actually on screen (`mark_shown`). At most once per event after that.
    Never when a recording is running, never when the owner has switched
    suggestions off, never after two declines.
    """
    if not event or not event.get("title") or recording:
        return None
    if not suggestion_state()["enabled"]:
        return None
    today = today or datetime.now().strftime("%Y-%m-%d")
    key = _event_key(event, today)
    with _STATE_LOCK:
        if key in (_load_state().get("offered") or []):
            return None
    return {"event": {"title": str(event.get("title"))[:200],
                      "event_id": str(event.get("event_id") or "")[:200]},
            "text": "Your “%s” is starting. Take notes?" % str(event.get("title"))[:120],
            "key": key}


def mark_shown(key) -> dict:
    """The card was visible to the owner: this event will not be offered again.
    Called by the page when the card scrolls into view, not when it loads."""
    key = str(key or "")
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        raise ValueError("not a suggestion key")
    with _STATE_LOCK:
        st = _load_state()
        offered = list(st.get("offered") or [])
        if key not in offered:
            offered.append(key)
            st["offered"] = offered[-_OFFERED_KEEP:]
            _write_state(st)
    return suggestion_state()


def answer_suggestion(action: str, key=None) -> dict:
    """The owner's answer. "decline" counts toward switching suggestions off;
    "accept" only means the owner opened the start card (it starts nothing)."""
    if action not in ("accept", "decline", "shown"):
        raise ValueError("action must be shown, accept or decline")
    if action == "shown" or key:
        mark_shown(key)
    if action == "shown":
        return suggestion_state()
    if action == "decline":
        with _STATE_LOCK:
            st = _load_state()
            st["declines"] = int(st.get("declines") or 0) + 1
            _write_state(st)
    return suggestion_state()


def reset_suggestions() -> dict:
    """Turn suggestions back on: the owner asked."""
    with _STATE_LOCK:
        st = _load_state()
        st["declines"] = 0
        _write_state(st)
    _put_settings({"meeting_suggestions": True})
    return suggestion_state()


def speak_line_if_asked(speaker, *, want: bool | None = None,
                        line: str | None = None) -> bool:
    """Say the consent line aloud, when the owner asked for that. `speaker` is
    a callable taking the text. `line` is the wording on the start card right
    now (it may be edited and not saved); without it the saved line is said.
    Returns True if it was spoken."""
    c = consent()
    if not (c["speak"] if want is None else want):
        return False
    try:
        speaker(clean_consent_line(line) or c["line"])
        return True
    except Exception as e:  # noqa: BLE001
        log.info("the consent line was not spoken: %s", e)
        return False
