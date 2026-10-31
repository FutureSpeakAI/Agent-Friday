"""Friday's waiting list on the owner's phone: a list in Google Tasks (Part 1).

Spec: ftv/program/specs/google_tasks_approval_inbox_STORM_2026-10-23.md, phases
GT0 + GT1, decided by the owner on 2026-10-23. In Part 1 a tick in Google Tasks
APPROVES NOTHING. What the list can do:

* show what Friday is waiting on: one item per card, under one parent item per
  background task when a task has cards waiting (Google allows one level of
  subtasks);
* **bring to my desk**: a tick on an item puts a notice in Needs you on the PC
  ("a tick was seen in Google Tasks; Friday can't tell who made it");
* **decline**: deleting an item, or ticking its "No, not now" subtask, declines
  the card, but only for card kinds whose "no" just means "don't do it"
  (``PHONE_DECLINABLE_KINDS``). Every other card stays on the PC.

Why a tick is so weak. The Tasks API names no actor: a tick proves only that
something could write to that account's tasks. That could be the owner, anyone
holding the unlocked phone, any app given Tasks access, or Google's own
assistant. So nothing here can produce "owner:ui", "owner" or a voice label, and
``approvals.decide_with_outcome`` refuses an approve labelled ``TICK_LABEL``.

Where authority lives. Not in Google. Each item Friday posts is a line in a
signed, append-only, hash-chained record (``record.jsonl``): which card it
stands for, the Google ids, the hash of the exact words Friday wrote, and the
etag of every write Friday made. Anything in the list that is not in the
record is someone else's and is never acted on. An item whose words changed
outside Friday is void. A record whose chain does not verify pauses the inbox.

The fence. ``google_accounts`` refuses the inbox list to every caller but this
module (``is_inbox_list``), so neither Friday's own Tasks tools nor a run
grant (#332, A24) can write to it or tick its items.

What leaves the PC. Every word posted goes through the egress gate (the same
check as every Tasks write) and the checkpoint's signed receipt
(``action_gate.record_external``) before it reaches Google. The owner picks
how much text: ``full`` (exactly what a notice or a text-message offer may
already carry), ``short`` (fixed words from code, recommended) or ``minimal``.
Under "only on this PC" only ``minimal`` is used. Nothing is posted off the
record.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac as _hmac
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_friday.paths import friday_home

_log = logging.getLogger("friday.tasks_inbox")
_LOCK = threading.RLock()

#: The label a decision made from Google Tasks carries. It has no "owner:" prefix, so it
#: never falls into any owner path; approvals.decide_with_outcome refuses it for an approve.
TICK_LABEL = "remote:tasks_tick"
#: The receipt's decided_source for it (approvals.decision_source).
DECISION_SOURCE = "google_tasks_tick"

TEXT_LEVELS = ("full", "short", "minimal")
RECOMMENDED_LEVEL = "short"

#: Card kinds a phone may decline. For each, "no" only means "don't do it": an email not
#: sent, a tool call not made, an outside action not taken, a missed job not run late.
PHONE_DECLINABLE_KINDS = frozenset({"tool_confirm", "tainted_action", "governed_action",
                                    "external_message", "scheduled_late_run"})

#: Kinds never posted: screen-only cards are posted as info items (handled below); these
#: are not posted at all (pairing, widening Friday's own powers, money).
NEVER_POSTED_KINDS = frozenset({"caller_pair", "caller_release", "caller_spend",
                                # Website-task plans stay on the PC (owner decision D9).
                                "web_task_plan", "web_task_plan_new_sites", "web_task_question"})

#: At the "full" level, the kinds whose own title and notice may go to Google, because they carry
#: no recipient, number, address, path or outside party's words. Every other kind is posted with
#: the "short" words even at "full" (review 1: an email's notice names its recipient, a text or a
#: call its number, a call-time card the caller's own words).
#: Today that is only a missed one-time job, whose title is the name the owner gave the job; a
#: kind whose words are written by a model or come from outside (a goal's critique, a trial's
#: summary) is not on it (review 2).
FULL_WORDS_KINDS = frozenset({"scheduled_late_run"})

MAX_POSTS_PER_DAY = 30
MAX_REPOSTS_PER_HOUR = 5
TIDY_AFTER_SECONDS = 7 * 86400
BURST_CHANGES = 3
BURST_WINDOW_SECONDS = 10.0
POLL_OPEN_SECONDS = 60.0
POLL_IDLE_SECONDS = 15 * 60.0
UPDATED_OVERLAP_SECONDS = 120.0
MAX_FOREIGN_NOTED = 50

HONEST_LIMIT = ("A tick in Google Tasks shows only that something could change your tasks: you, anyone "
                "holding your phone, an app you gave Tasks access to, or Google's own assistant. So in this "
                "version a tick approves nothing. It can bring a card to the top of Friday on your PC, and "
                "deleting an item (or ticking its \"No, not now\") declines the kinds of card where no just "
                "means don't do it. Whatever Friday posts is stored by Google and shows wherever your tasks show. "
                "The fence that keeps Friday's own tools off the list covers the Tasks API; if you let Friday "
                "drive a browser signed in to Google, the Google Tasks web page is not fenced.")

NO_TITLE = "No, not now"


# ── files ────────────────────────────────────────────────────────────────────

def _dir() -> Path:
    d = Path(friday_home()) / "tasks_inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _config_path() -> Path:
    return _dir() / "config.json"


def _record_path() -> Path:
    return _dir() / "record.jsonl"


def _key() -> bytes:
    from agent_friday.governance import action_gate
    return action_gate._governance_key()


def _canon(obj: dict) -> bytes:
    return json.dumps({k: v for k, v in obj.items() if k != "sig"}, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")


def _sign(obj: dict) -> str:
    return _hmac.new(_key(), _canon(obj), hashlib.sha256).hexdigest()


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_iso(value) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _text_hash(title: str, notes: str) -> str:
    return hashlib.sha256(("%s\n\x1e\n%s" % (title or "", notes or "")).encode("utf-8")).hexdigest()


# ── configuration (signed) ───────────────────────────────────────────────────

def _read_config_raw() -> dict:
    try:
        return json.loads(_config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {"_unreadable": True}


def config() -> dict:
    """The inbox's settings, verified. A file that is missing, unreadable or not signed by
    this Friday reads as off (fail closed), with ``problem`` saying why."""
    raw = _read_config_raw()
    if not raw:
        return {"enabled": False}
    if raw.get("_unreadable"):
        return {"enabled": False, "problem": "The inbox's settings could not be read, so it is off."}
    try:
        good = _hmac.compare_digest(str(raw.get("sig") or ""), _sign(raw))
    except Exception:
        good = False
    if not good:
        out = {k: raw.get(k) for k in ("account_id", "list_id")}
        out.update(enabled=False, problem="The inbox's settings were changed outside Friday, so it is off.")
        return out
    return {k: v for k, v in raw.items() if k != "sig"}


def _write_config(cfg: dict) -> dict:
    cfg = {k: v for k, v in cfg.items() if k not in ("sig", "problem")}
    cfg["v"] = 1
    cfg["sig"] = _sign(cfg)
    path = _config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    tmp.replace(path)
    return {k: v for k, v in cfg.items() if k != "sig"}


# ── the record (signed, append-only, chained) ────────────────────────────────

#: The verified head of the record, kept so an append does not re-read the whole file. It is
#: trusted only while the file's size is exactly what this process last read or wrote.
_HEAD: Dict[str, Any] = {"path": "", "size": -1, "good_end": 0, "prev": "", "seq": 0}


def _read_record() -> Tuple[List[dict], bool]:
    """Every line, and whether the whole chain verifies. A torn last line (a kill mid-write)
    is ignored; anything else that does not verify breaks the chain."""
    path = _record_path()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return [], True
    lines = data.split(b"\n")
    out, prev, ok = [], "", True
    offset, good_end = 0, 0
    for i, raw in enumerate(lines):
        start = offset
        offset += len(raw) + 1
        if not raw.strip():
            if not out and start == 0:
                good_end = 0
            continue
        try:
            line = json.loads(raw.decode("utf-8"))
        except Exception:
            if i == len(lines) - 1 or (i == len(lines) - 2 and not lines[-1].strip()):
                break                                   # a torn or garbage last line: cut at the next append
            ok = False
            break
        try:
            if line.get("prev") != prev or not _hmac.compare_digest(str(line.get("sig") or ""), _sign(line)):
                ok = False
                break
        except Exception:
            ok = False
            break
        prev = hashlib.sha256(_canon(line)).hexdigest()
        out.append(line)
        good_end = min(offset, len(data))           # a last line with no newline ends at len(data)
    if ok:
        _HEAD.update(path=str(path), size=len(data), good_end=good_end, prev=prev, seq=len(out))
    return out, ok


def _append(event: str, **fields) -> dict:
    """Append one signed line chained to the last. Raises if it cannot be written:
    a caller that cannot record what it did does not do it."""
    import os
    with _LOCK:
        path = _record_path()
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if not (_HEAD["path"] == str(path) and _HEAD["size"] == size and size > 0):
            _lines, ok = _read_record()
            if not ok:
                raise RuntimeError("the inbox record does not verify")
            if not _lines:
                _HEAD.update(path=str(path), size=size, good_end=0, prev="", seq=0)
        keep = int(_HEAD.get("good_end") or 0)
        if size > keep:
            # A line a kill cut short, or a last line that is not a record line, is dropped
            # before the next one is written: it never ends up in the middle of the chain.
            with open(path, "rb+") as fh:
                fh.truncate(keep)
            size = keep
        prev, seq = (_HEAD["prev"], _HEAD["seq"]) if size else ("", 0)
        line = {"seq": seq + 1, "prev": prev, "at": _now(), "event": event}
        line.update(fields)
        line["sig"] = _sign(line)
        blob = json.dumps(line, sort_keys=True, default=str).encode("utf-8") + b"\n"
        if size:
            # A verified last line that lost only its newline keeps its place: the newline is
            # put back first, so the new line is never joined onto it (review 2).
            with open(path, "rb") as fh:
                fh.seek(size - 1)
                if fh.read(1) != b"\n":
                    blob = b"\n" + blob
        with open(path, "ab") as fh:
            fh.write(blob)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
        _HEAD.update(path=str(path), size=size + len(blob), good_end=size + len(blob),
                     prev=hashlib.sha256(_canon(line)).hexdigest(), seq=seq + 1)
        return line


def _state() -> Tuple[Dict[str, dict], dict, bool]:
    """(items by item_id, extras, chain_ok), replayed from the record."""
    lines, ok = _read_record()
    items: Dict[str, dict] = {}
    extras: Dict[str, Any] = {"foreign": set(), "lists": set(), "posts": [], "reposts": [],
                              "last_poll": 0.0, "events": []}
    for ln in lines:
        ev = ln.get("event")
        iid = ln.get("item_id")
        if ev == "list":
            extras["lists"].add((ln.get("account_id"), ln.get("list_id")))
        elif ev == "posted":
            items[iid] = {"item_id": iid, "role": ln.get("role"), "approval_id": ln.get("approval_id"),
                          "task_key": ln.get("task_key"), "parent_item": ln.get("parent_item"),
                          "account_id": ln.get("account_id"), "list_id": ln.get("list_id"),
                          "gid": ln.get("gid"), "etags": [ln.get("etag")], "text_sha": ln.get("text_sha"),
                          "status": ln.get("status") or "needsAction", "state": "open",
                          "posted_at": ln.get("at"), "ended_at": None, "closed": False,
                          "declinable": bool(ln.get("declinable")), "removed": False}
            extras["lists"].add((ln.get("account_id"), ln.get("list_id")))
            if ln.get("role") in ("card", "group"):
                extras["posts"].append(ln.get("at"))
            if ln.get("repost_of"):
                extras["reposts"].append(ln.get("at"))
        elif iid in items:
            it = items[iid]
            if ev == "own_write":
                it["etags"].append(ln.get("etag"))
                if ln.get("text_sha"):
                    it["text_sha"] = ln.get("text_sha")
                if ln.get("status"):
                    it["status"] = ln.get("status")
                if ln.get("closed"):
                    it["closed"] = True
            elif ev == "seen":
                it["status"] = ln.get("status") or it["status"]
            elif ev in ("ticked", "untick"):
                it["status"] = "completed" if ev == "ticked" else "needsAction"
                if ev == "ticked" and it["state"] == "open":
                    it["state"] = "ticked"
            elif ev == "void":
                it["state"] = "void"
            elif ev == "deleted":
                it["state"] = "deleted"
                it["removed"] = True
            elif ev == "delete_intent":
                it["own_delete"] = True
            elif ev == "removed":
                it["removed"] = True
            elif ev == "ended":
                it["ended_at"] = ln.get("at")
        if ev == "foreign":
            extras["foreign"].add(ln.get("gid"))
        if ev in ("ticked", "declined", "held", "void", "foreign", "deleted"):
            extras["events"].append({k: ln.get(k) for k in ("event", "at", "item_id", "approval_id", "why")})
    extras["last_poll"] = _last_poll()
    return items, extras, ok


def _poll_path() -> Path:
    return _dir() / "last_poll.json"


def _last_poll() -> float:
    """When the list was last read. Timing only (it narrows the next read with updatedMin);
    it decides nothing, so it lives outside the signed record."""
    try:
        at = float(json.loads(_poll_path().read_text(encoding="utf-8")).get("at") or 0.0)
    except Exception:
        return 0.0
    return at if at <= _now() + 60 else 0.0


def _note_poll(at: float) -> None:
    try:
        _poll_path().write_text(json.dumps({"at": at}), encoding="utf-8")
    except Exception:
        pass


# ── the fence (read by google_accounts) ──────────────────────────────────────

def reserved_lists() -> set:
    """Every (account_id, list_id) that is or was Friday's inbox list, from the settings
    file (signed or not) and the record (verified or not). Conservative on purpose: a list
    stays fenced after the inbox is switched off, and a damaged file still fences the list it
    names. RAISES when a file exists but cannot be read, or the settings file names no list
    it can be read for: the caller (``is_inbox_list``) then refuses (review 1)."""
    out = set()
    config_bad = False
    try:
        raw = json.loads(_config_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("list_id"):
            out.add((raw.get("account_id"), raw.get("list_id")))
    except FileNotFoundError:
        pass
    except ValueError:
        config_bad = True                       # garbled JSON: fall back to the record
    try:
        data = _record_path().read_bytes()      # an I/O error here propagates: refuse
    except FileNotFoundError:
        data = b""
    for raw_line in data.split(b"\n"):
        try:
            ln = json.loads(raw_line.decode("utf-8"))
        except Exception:
            continue
        if isinstance(ln, dict) and ln.get("list_id"):
            out.add((ln.get("account_id"), ln.get("list_id")))
    if config_bad and not out:
        raise RuntimeError("the inbox settings cannot be read and the record names no list")
    return out


def is_inbox_list(account_id, tasklist_id) -> bool:
    """True when ``tasklist_id`` is (or was) Friday's inbox list. Any account matches: the
    fence never depends on a caller naming the right account."""
    if not tasklist_id:
        return False
    try:
        return any(lid == tasklist_id for _aid, lid in reserved_lists())
    except Exception:
        # The fence cannot be checked: refuse rather than allow.
        return True


# ── Google ───────────────────────────────────────────────────────────────────

def _service(account_id: str):
    """The Tasks API for one account, for this module only. Never handed to a model or a tool."""
    from agent_friday.services import google_accounts as ga
    creds = ga.credentials_for(account_id)
    if not creds:
        return None
    from googleapiclient.discovery import build
    from agent_friday.services import egress_record
    # Every write through this client is a row in the egress ledger (no unannounced egress).
    return egress_record.tasks(build("tasks", "v1", credentials=creds, cache_discovery=False))


def writable_accounts() -> List[dict]:
    """Connected accounts that allowed task changes (the ``tasks`` permission)."""
    from agent_friday.services import google_accounts as ga
    out = []
    for rec in ga.list_accounts():
        scopes = rec.get("scopes") or []
        if ga.TASKS_RW in scopes and (rec.get("services") or {}).get("tasks", True):
            out.append({"id": rec.get("id"), "email": rec.get("email"), "label": rec.get("label")})
    return out


def _assistant() -> str:
    try:
        from agent_friday.services import agent_identity
        return agent_identity.assistant_name() or "Friday"
    except Exception:
        return "Friday"


def list_title() -> str:
    return "%s — awaiting you" % _assistant()


# ── words (built by code, gated before they leave) ───────────────────────────

_KIND_WORDS = {
    "governed_action": "an action outside %s",
    "external_message": "an email",
    "scheduled_late_run": "a job that missed its time",
    "goal_milestone_escalation": "a goal step",
}


def _tool_name(rec: dict) -> str:
    tool = str((rec.get("payload") or {}).get("tool") or "")
    return tool if re.fullmatch(r"[a-z][a-z0-9_]{0,48}", tool) else ""


def effective_level(cfg: Optional[dict] = None) -> str:
    cfg = cfg if cfg is not None else config()
    level = cfg.get("text_level") if cfg.get("text_level") in TEXT_LEVELS else "minimal"
    try:
        from agent_friday.services import cloud_mode_policy
        if cloud_mode_policy.is_local_mode():
            return "minimal"
    except Exception:
        return "minimal"
    return level


def _card_title(rec: dict, level: str) -> str:
    name = _assistant()
    if level == "minimal":
        return "Something needs you in %s" % name
    if level == "full" and rec.get("kind") in FULL_WORDS_KINDS:
        from agent_friday.services import approvals as ap
        return ap.notice_title(rec)
    tool = _tool_name(rec)
    if tool:
        return "%s needs your yes for %s" % (name, tool)
    words = _KIND_WORDS.get(str(rec.get("kind") or ""), "a decision")
    return "%s needs your decision: %s" % (name, words % name if "%s" in words else words)


def _card_notes(rec: dict, level: str, declinable: bool, grouped: bool = False) -> str:
    name = _assistant()
    lines = []
    if level == "full" and rec.get("kind") in FULL_WORDS_KINDS:
        from agent_friday.services import approvals as ap
        body = ap.notice_body(rec)
        if body:
            lines.append(body[:600])
    lines.append("Posted by %s at %s." % (name, _hhmm(_now())))
    lines.append("Ticking this brings it to the top of %s on your PC. It approves nothing." % name)
    if declinable and grouped:
        lines.append("To decline it from here, delete this item.")
    elif declinable:
        lines.append("To decline it from here, delete this item or tick \"%s\"." % NO_TITLE)
    else:
        lines.append("Decide it on your PC.")
    exp = rec.get("expires_at")
    if exp:
        lines.append("Ends %s." % datetime.fromtimestamp(float(exp)).strftime("%d %b %H:%M"))
    lines.append("%s never asks you to open a link or type anything in Google Tasks." % name)
    return "\n".join(lines)


def _group_words(level: str) -> Tuple[str, str]:
    name = _assistant()
    title = ("Something needs you in %s" % name) if level == "minimal" else ("A task of %s's is waiting for you" % name)
    notes = ("Its steps waiting for you are below. Ticking this brings them all to the top of %s on your PC; "
             "it approves nothing. Tick \"%s\" (or delete this item) to decline the ones that can be declined "
             "from here.\n%s never asks you to open a link or type anything in Google Tasks." % (name, NO_TITLE, name))
    return title, notes


def _gate(text: str, field: str) -> Optional[str]:
    """The egress gate's verdict on one field. None when anything was withheld or the gate
    could not run: the caller then posts less, or nothing."""
    try:
        from agent_friday.services import egress_gate as _eg
        gated = _eg._gate_text(str(text), "google", "tasks_inbox.%s" % field)
    except Exception as e:
        _log.info("tasks inbox: the privacy gate could not run (%s)", type(e).__name__)
        return None
    return gated if gated == text else None


#: The decision the last `_checkpoint` in this context wrote, for `_settle`.
_DECISION: contextvars.ContextVar = contextvars.ContextVar("tasks_inbox_decision", default=None)


def _checkpoint(action: str, approval_id: Optional[str]) -> bool:
    """The cLaws check and a signed receipt before anything leaves (action_gate.record_external)."""
    _DECISION.set(None)
    try:
        from agent_friday.governance import action_gate
        _DECISION.set(action_gate.record_external(
            action, surface="tasks_inbox", approval_id=None,
            target="google_tasks" + (":%s" % approval_id if approval_id else "")))
        return True
    except Exception as e:
        _log.info("tasks inbox: the checkpoint held %s (%s)", action, e)
        return False


def _settle(ok: bool, detail: str = "") -> None:
    """The terminal outcome of the change the last `_checkpoint` let through (BD-11a)."""
    decision = _DECISION.get()
    _DECISION.set(None)
    try:
        from agent_friday.governance import action_gate
        action_gate.close_external(decision, ok, detail=detail)
    except Exception:  # noqa: BLE001 - a receipt never stops the sync
        pass


def _words_out(title: str, notes: str, fallback_title: str) -> Optional[Tuple[str, str]]:
    t = _gate(title, "title")
    if t is None:
        t = _gate(fallback_title, "title")
        if t is None:
            return None
    n = _gate(notes, "notes")
    if n is None:
        return None
    return t, n


# ── enable / disable (the routes decide who may) ─────────────────────────────

def enable(account_id: str, text_level: str) -> dict:
    """Turn the inbox on for one account at one text level. The route requires a press on
    this PC's screen; nothing here is reachable from a model or a tool."""
    # egress: recorded by _service (egress_record.tasks wraps the only client this module uses)
    if text_level not in TEXT_LEVELS:
        return {"ok": False, "message": "Choose how much text goes to Google first."}
    if not any(a["id"] == account_id for a in writable_accounts()):
        return {"ok": False, "message": "That account has not allowed task changes. Connect it with task "
                                        "changes allowed (Settings > Connections > Google), then try again."}
    try:
        from agent_friday.services import off_record
        if off_record.active():
            return {"ok": False, "message": "Off the record is on, so nothing is posted to Google."}
    except Exception:
        pass
    with _LOCK:
        cur = config()
        svc = _service(account_id)
        if svc is None:
            return {"ok": False, "message": "The Google account needs signing in again."}
        list_id = None
        if cur.get("account_id") == account_id and cur.get("list_id"):
            try:
                svc.tasklists().get(tasklist=cur["list_id"]).execute()
                list_id = cur["list_id"]
            except Exception:
                list_id = None
        if list_id is None:
            if not _checkpoint("tasks_inbox_create_list", None):
                return {"ok": False, "message": "Nothing was created: the checkpoint held it."}
            try:
                made = svc.tasklists().insert(body={"title": list_title()}).execute()
            except Exception as e:
                _settle(False, "provider_refused")
                return {"ok": False, "message": "Google did not create the list (%s)." % type(e).__name__}
            _settle(True, "list_created")
            list_id = made.get("id")
            _append("list", account_id=account_id, list_id=list_id)
        cfg = _write_config({"enabled": True, "account_id": account_id, "list_id": list_id,
                             "list_title": list_title(), "text_level": text_level,
                             "enabled_at": _now(), "instance": uuid.uuid4().hex[:12]})
    return {"ok": True, "config": cfg}


def set_level(text_level: str) -> dict:
    if text_level not in TEXT_LEVELS:
        return {"ok": False, "message": "Unknown text level."}
    with _LOCK:
        cur = config()
        if not cur.get("enabled"):
            return {"ok": False, "message": "The inbox is off."}
        cur["text_level"] = text_level
        return {"ok": True, "config": _write_config(cur)}


def is_raise(text_level: str) -> bool:
    """Would this level send more text to Google than the current one?"""
    cur = config().get("text_level")
    order = {"minimal": 0, "short": 1, "full": 2}
    return order.get(text_level, 9) > order.get(cur, 0)


def disable(remove_list: bool = False) -> dict:
    """Turn the inbox off. Removing Friday's list deletes only the list Friday made."""
    # egress: recorded by _service (egress_record.tasks wraps the only client this module uses)
    with _LOCK:
        cur = config()
        raw = _read_config_raw()
        account_id, list_id = raw.get("account_id"), raw.get("list_id")
        removed = False
        if remove_list and account_id and list_id:
            svc = _service(account_id)
            if svc is not None and _checkpoint("tasks_inbox_remove_list", None):
                try:
                    _append("delete_intent", item_id="", list_id=list_id, account_id=account_id)
                    svc.tasklists().delete(tasklist=list_id).execute()
                    removed = True
                except Exception as e:
                    _log.info("tasks inbox: the list was not removed (%s)", type(e).__name__)
                _settle(removed, "list_removed" if removed else "provider_refused")
        keep = {k: cur.get(k) for k in ("account_id", "list_id", "text_level")}
        keep.update(enabled=False, disabled_at=_now(), list_removed=removed)
        _write_config(keep)
    return {"ok": True, "list_removed": removed}


# ── the sync (a scheduler built-in, every minute) ────────────────────────────

def _pending_cards() -> List[dict]:
    from agent_friday.services import approvals as ap
    out = []
    for rec in ap.list_approvals(status="pending"):
        if rec.get("kind") in NEVER_POSTED_KINDS or str((rec.get("payload") or {}).get("origin") or "").startswith("caller:"):
            continue
        if rec.get("off_record") or (rec.get("payload") or {}).get("handler") == "cloud_release":
            continue
        out.append(rec)
    return out


def _declinable(rec: dict) -> bool:
    from agent_friday.services import approvals as ap
    return rec.get("kind") in PHONE_DECLINABLE_KINDS and rec.get("kind") not in ap.SCREEN_ONLY_KINDS


def _task_key(rec: dict) -> str:
    tid = str((rec.get("payload") or {}).get("task_id") or "")
    return tid if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", tid) else ""


def _insert(svc, list_id: str, body: dict, parent: Optional[str] = None) -> dict:
    # egress: recorded by _service (egress_record.tasks wraps the only client this module uses)
    kw = {"tasklist": list_id, "body": body}
    if parent:
        kw["parent"] = parent
    return svc.tasks().insert(**kw).execute()


def _post_one(svc, cfg: dict, level: str, rec: dict, parent_item: Optional[dict],
              repost_of: Optional[str] = None) -> Optional[dict]:
    declinable = _declinable(rec)
    title, notes = _card_title(rec, level), _card_notes(rec, level, declinable, grouped=bool(parent_item))
    words = _words_out(title, notes, _card_title(rec, "minimal"))
    if words is None:
        return None
    title, notes = words
    nt = None
    if declinable and not parent_item:
        nt = _gate(NO_TITLE, "title")
        if nt is None:
            return None
    if not _checkpoint("tasks_inbox_post", rec.get("approval_id")):
        return None
    iid = "ti_" + uuid.uuid4().hex[:12]
    try:
        made = _insert(svc, cfg["list_id"], {"title": title, "notes": notes},
                       parent=(parent_item or {}).get("gid"))
    except Exception:
        _settle(False, "provider_refused")
        raise
    _settle(True, "posted")
    line = _append("posted", item_id=iid, role="card", approval_id=rec.get("approval_id"),
                   task_key=(parent_item or {}).get("task_key") or "", parent_item=(parent_item or {}).get("item_id"),
                   account_id=cfg["account_id"], list_id=cfg["list_id"], gid=made.get("id"),
                   etag=made.get("etag"), status=made.get("status") or "needsAction",
                   text_sha=_text_hash(made.get("title", title), made.get("notes", notes)),
                   declinable=declinable, repost_of=repost_of)
    if nt is not None:
        no = _insert(svc, cfg["list_id"], {"title": nt}, parent=made.get("id"))
        _append("posted", item_id="ti_" + uuid.uuid4().hex[:12], role="no", approval_id=rec.get("approval_id"),
                task_key="", parent_item=iid, account_id=cfg["account_id"], list_id=cfg["list_id"],
                gid=no.get("id"), etag=no.get("etag"), status=no.get("status") or "needsAction",
                text_sha=_text_hash(no.get("title", nt), no.get("notes", "")), declinable=True)
    return line


def _post_group(svc, cfg: dict, level: str, task_key: str) -> Optional[dict]:
    title, notes = _group_words(level)
    words = _words_out(title, notes, _group_words("minimal")[0])
    if words is None:
        return None
    title, notes = words
    nt = _gate(NO_TITLE, "title")
    if nt is None:
        return None
    if not _checkpoint("tasks_inbox_post", None):
        return None
    iid = "ti_" + uuid.uuid4().hex[:12]
    try:
        made = _insert(svc, cfg["list_id"], {"title": title, "notes": notes})
    except Exception:
        _settle(False, "provider_refused")
        raise
    _settle(True, "posted")
    _append("posted", item_id=iid, role="group", approval_id="", task_key=task_key, parent_item="",
                   account_id=cfg["account_id"], list_id=cfg["list_id"], gid=made.get("id"),
                   etag=made.get("etag"), status=made.get("status") or "needsAction",
                   text_sha=_text_hash(made.get("title", title), made.get("notes", notes)), declinable=True)
    no = _insert(svc, cfg["list_id"], {"title": nt}, parent=made.get("id"))
    _append("posted", item_id="ti_" + uuid.uuid4().hex[:12], role="no", approval_id="", task_key=task_key,
            parent_item=iid, account_id=cfg["account_id"], list_id=cfg["list_id"], gid=no.get("id"),
            etag=no.get("etag"), status=no.get("status") or "needsAction",
            text_sha=_text_hash(no.get("title", nt), no.get("notes", "")), declinable=True)
    return {"item_id": iid, "gid": made.get("id"), "task_key": task_key}


def _list_changes(svc, list_id: str, since: float) -> List[dict]:
    out, token = [], None
    for _page in range(20):
        kw = {"tasklist": list_id, "showCompleted": True, "showHidden": True, "showDeleted": True,
              "showAssigned": False, "maxResults": 100}
        if since:
            kw["updatedMin"] = _iso(max(0.0, since - UPDATED_OVERLAP_SECONDS))
        if token:
            kw["pageToken"] = token
        resp = svc.tasks().list(**kw).execute()
        out.extend(resp.get("items") or [])
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def _notify(emitter: str, approval_id: str, title: str, body: str, priority: str = "high") -> None:
    try:
        import agent_friday.notifications_engine as _ne
        if emitter == "TI1":
            _ne.push(title=title, body=body, priority=priority, source="tasks_inbox", kind="tasks_inbox_tick",
                     emitter="TI1", dedupe_key="tasks-inbox-tick:%s" % approval_id,
                     target={"workspace": "system", "tab": "approvals"},
                     actions=[{"type": "navigate", "label": "Review", "workspace": "system", "tab": "approvals"}])
        else:
            _ne.push(title=title, body=body, priority=priority, source="tasks_inbox", kind="tasks_inbox_change",
                     emitter="TI2", dedupe_key="tasks-inbox-change:%s" % approval_id,
                     target={"workspace": "settings", "tab": "connectors"})
    except Exception as e:
        _log.debug("tasks inbox notice failed: %s", e)


def _bring_to_desk(item: dict, at: float) -> None:
    from agent_friday.services import approvals as ap
    aid = item.get("approval_id")
    if not aid:
        return
    rec = ap.get_approval(aid)
    if not rec or rec.get("status") != "pending":
        return
    words = "A tick was seen in Google Tasks at %s. %s can't tell who made it." % (_hhmm(at), _assistant())
    try:
        ap._patch(aid, phone_tick={"at": at, "words": words})
    except Exception:
        pass
    _notify("TI1", aid, "Brought to your desk from Google Tasks: %s" % ap.waiting_notice_title(rec), words)


def _decline(item: dict, at: float, why: str) -> bool:
    from agent_friday.services import approvals as ap
    aid = item.get("approval_id")
    rec = ap.get_approval(aid) if aid else None
    if not rec or rec.get("status") != "pending" or not _declinable(rec):
        return False
    note = "Declined from Google Tasks at %s (%s). %s can't tell who did it." % (_hhmm(at), why, _assistant())
    out, won = ap.decide_with_outcome(aid, "deny", decided_by=TICK_LABEL, note=note)
    if won:
        _append("declined", item_id=item["item_id"], approval_id=aid, why=why)
        _notify("TI2", aid, "Declined from Google Tasks: %s" % ap.waiting_notice_title(rec),
                note + " If that wasn't you, turn the phone list off in Settings and ask %s again." % _assistant(),
                priority="medium")
    return bool(won)


def _result_words(rec: Optional[dict]) -> Tuple[str, bool]:
    """(the line Friday adds to an item once its card is settled, whether the card has ended)."""
    if not rec:
        return "This card is no longer on your PC.", True
    st = rec.get("status")
    if st == "pending":
        return "", False
    at = rec.get("decided_at") or _now()
    by = str(rec.get("decided_by") or "")
    if st == "approved" or st == "auto_approved":
        return "Decided on your PC at %s: approved." % _hhmm(at), True
    if st == "denied":
        if by == TICK_LABEL:
            return "Declined from Google Tasks at %s. Nothing ran." % _hhmm(at), True
        if by.startswith("friday:"):
            return "Withdrawn by %s at %s: it was no longer needed." % (_assistant(), _hhmm(at)), True
        return "Declined on your PC at %s. Nothing ran." % _hhmm(at), True
    if st == "expired":
        return "Expired at %s. Nothing ran." % _hhmm(at), True
    return "No longer waiting (%s)." % st, True


def _patch_own(svc, item: dict, body: dict, *, closed: bool = False) -> Optional[dict]:
    """Patch one of Friday's own items and record its new etag. Never sends status unless
    closing an item that is still open. Returns the response, or None.

    Part 1 writes only to items whose card is already settled, so a tick that lands between
    Friday's read and this write (and is then hidden behind Friday's own etag) can change
    nothing: the card's decision was made before it."""
    # egress: recorded by _service (egress_record.tasks wraps the only client this module uses)
    try:
        resp = svc.tasks().patch(tasklist=item["list_id"], task=item["gid"], body=body).execute()
    except Exception as e:
        _log.info("tasks inbox: a write did not go through (%s)", type(e).__name__)
        return None
    _append("own_write", item_id=item["item_id"], etag=resp.get("etag"),
            status=resp.get("status") if "status" in body else None,
            text_sha=_text_hash(resp.get("title") or "", resp.get("notes") or ""), closed=closed)
    return resp


def tick(now: Optional[float] = None, force: bool = False) -> dict:
    """One sync: post what is waiting, read what changed, act on it (Part 1: bring to desk,
    decline), write results back, tidy up. Safe to call at any time; does nothing when off."""
    now = _now() if now is None else now
    cfg = config()
    if not cfg.get("enabled"):
        return {"ok": True, "skipped": "off"}
    with _LOCK:
        items, extras, chain_ok = _state()
        if not chain_ok:
            return {"ok": False, "skipped": "record", "message": "The inbox record does not verify, so the phone "
                                                                  "list is paused until you look at it on the PC."}
        open_items = [i for i in items.values() if i["state"] in ("open", "ticked") and not i["removed"]]
        last = extras["last_poll"]
        if not force and now - last < (POLL_OPEN_SECONDS if open_items or _pending_cards() else POLL_IDLE_SECONDS) - 1:
            return {"ok": True, "skipped": "not_due"}
        svc = _service(cfg["account_id"])
        if svc is None:
            return {"ok": False, "skipped": "account", "message": "The Google account needs signing in again."}
        try:
            from agent_friday.services import off_record
            off = off_record.active()
        except Exception:
            off = True
        summary = {"ok": True, "posted": 0, "ticks": 0, "declined": 0, "held": 0, "void": 0, "foreign": 0}
        polled_at = _now()
        try:
            changes = _list_changes(svc, cfg["list_id"], last)
        except Exception as e:
            return {"ok": False, "skipped": "google", "message": "Google could not be reached (%s)." % type(e).__name__}
        _apply_changes(svc, cfg, items, extras, changes, now, summary)
        _note_poll(polled_at)
        items, extras, _ok = _state()
        if not off:
            _post_new(svc, cfg, items, extras, now, summary)
            items, extras, _ok = _state()
            _write_results(svc, items, now)
        _tidy(svc, items, now)
    return summary


def _apply_changes(svc, cfg, items, extras, changes, now, summary) -> None:
    by_gid = {i["gid"]: i for i in items.values() if i.get("gid")}
    events: List[Tuple[str, dict, dict]] = []
    for t in changes:
        gid = t.get("id")
        item = by_gid.get(gid)
        if item is None:
            if gid not in extras["foreign"] and len(extras["foreign"]) < MAX_FOREIGN_NOTED and not t.get("deleted"):
                _append("foreign", gid=gid)
                summary["foreign"] += 1
            continue
        if item["state"] in ("void", "deleted") or item.get("removed"):
            continue
        own = t.get("etag") in set(item["etags"])
        if t.get("deleted"):
            if item.get("own_delete") or own:
                continue
            events.append(("deleted", item, t))
            continue
        if own:
            continue
        sha = _text_hash(t.get("title") or "", t.get("notes") or "")
        if sha != item["text_sha"]:
            events.append(("edited", item, t))
            continue
        status = t.get("status") or "needsAction"
        if status != item["status"]:
            events.append(("ticked" if status == "completed" else "untick", item, t))
    # Deleting a parent deletes its subtasks with it: those are one change, the parent's.
    deleted_ids = {i["item_id"] for kind, i, _t in events if kind == "deleted"}
    events = [(k, i, t) for k, i, t in events
              if not (k == "deleted" and i.get("parent_item") in deleted_ids)]
    # The burst rule: several of Friday's items changed within seconds of each other (a
    # "clear completed", an assistant told to finish everything). Nothing is declined.
    stamps = sorted(_parse_iso(t.get("updated")) or now for kind, _i, t in events if kind in ("ticked", "deleted"))
    burst = any(stamps[k + BURST_CHANGES] - stamps[k] <= BURST_WINDOW_SECONDS
                for k in range(0, max(0, len(stamps) - BURST_CHANGES)))
    for kind, item, t in events:
        if kind == "edited":
            _append("void", item_id=item["item_id"], approval_id=item.get("approval_id"), why="edited")
            summary["void"] += 1
            for child in items.values():
                if child.get("parent_item") == item["item_id"] and child["state"] in ("open", "ticked"):
                    _append("void", item_id=child["item_id"], approval_id=child.get("approval_id"), why="parent edited")
            if item.get("approval_id"):
                _notify("TI2", item["approval_id"], "An item in Google Tasks was changed outside %s" % _assistant(),
                        "%s can't tell who changed it. Nothing was approved or declined; the card is unchanged on "
                        "your PC, and a fresh item replaces the changed one." % _assistant(), priority="low")
            continue
        if kind == "untick":
            _append("untick", item_id=item["item_id"])
            continue
        if kind == "ticked":
            _append("ticked", item_id=item["item_id"], approval_id=item.get("approval_id"))
            summary["ticks"] += 1
            targets = _targets(items, item)
            if item["role"] == "no":
                if burst:
                    _append("held", item_id=item["item_id"], why="burst")
                    summary["held"] += 1
                    for tgt in targets:
                        _bring_to_desk(tgt, now)
                    continue
                for tgt in targets:
                    if _decline(tgt, now, "\"%s\" was ticked" % NO_TITLE):
                        summary["declined"] += 1
            else:
                for tgt in targets:
                    _bring_to_desk(tgt, now)
            continue
        if kind == "deleted":
            _append("deleted", item_id=item["item_id"], approval_id=item.get("approval_id"))
            if item["role"] == "no":
                continue                                 # deleting the "No" line declines nothing
            if burst:
                _append("held", item_id=item["item_id"], why="burst")
                summary["held"] += 1
                for tgt in _targets(items, item):
                    _bring_to_desk(tgt, now)
                continue
            for tgt in _targets(items, item):
                if _decline(tgt, now, "the item was deleted"):
                    summary["declined"] += 1


def _targets(items: Dict[str, dict], item: dict) -> List[dict]:
    """The card items one change stands for: a card item is itself; a group (or its "No")
    is every open card item under that group; a card's "No" is that card."""
    if item["role"] == "card":
        return [item]
    anchor = items.get(item.get("parent_item")) if item["role"] == "no" else item
    if anchor is None:
        return []
    if anchor["role"] == "card":
        return [anchor] if anchor["state"] in ("open", "ticked") else []
    return [c for c in items.values() if c.get("parent_item") == anchor["item_id"] and c["role"] == "card"
            and c["state"] in ("open", "ticked")]


def _post_new(svc, cfg, items, extras, now, summary) -> None:
    level = effective_level(cfg)
    # A card counts as posted while any item for it is not void: one the owner deleted (a
    # decline that did not apply, or one held in a burst) is not posted again (review 1).
    posted_for = {i["approval_id"] for i in items.values()
                  if i["role"] == "card" and i["state"] in ("open", "ticked", "deleted")
                  and not (i["removed"] and i["state"] != "deleted")}
    voided = {i["approval_id"] for i in items.values() if i["role"] == "card" and i["state"] == "void"}
    groups = {i["task_key"]: i for i in items.values()
              if i["role"] == "group" and i["state"] in ("open", "ticked") and not i["removed"]
              and not i["closed"] and not i.get("ended_at")}
    day_posts = [t for t in extras["posts"] if t and now - float(t) < 86400]
    hour_reposts = [t for t in extras["reposts"] if t and now - float(t) < 3600]
    pending = _pending_cards()
    by_task: Dict[str, List[dict]] = {}
    for rec in pending:
        by_task.setdefault(_task_key(rec), []).append(rec)
    for rec in pending:
        aid = rec.get("approval_id")
        if aid in posted_for:
            continue
        repost = aid in voided
        if len(day_posts) >= MAX_POSTS_PER_DAY or (repost and len(hour_reposts) >= MAX_REPOSTS_PER_HOUR):
            break
        try:
            key = _task_key(rec)
            parent = None
            if key and (key in groups or len(by_task.get(key) or []) > 1):
                parent = groups.get(key)
                if parent is None:
                    parent = _post_group(svc, cfg, level, key)
                    if parent is None:
                        continue
                    groups[key] = parent
                    day_posts.append(now)
            line = _post_one(svc, cfg, level, rec, parent, repost_of=aid if repost else None)
        except Exception as e:
            _log.info("tasks inbox: a post did not go through (%s)", type(e).__name__)
            continue
        if line is not None:
            summary["posted"] += 1
            day_posts.append(now)
            if repost:
                hour_reposts.append(now)


def _write_results(svc, items, now) -> None:
    from agent_friday.services import approvals as ap
    for item in list(items.values()):
        if item["role"] != "card" or item["closed"] or item["removed"] or item.get("ended_at"):
            continue
        words, ended = _result_words(ap.get_approval(item["approval_id"]))
        if not ended:
            continue
        if item["state"] == "void":
            # Changed outside Friday: its words are left as they are; it is only tidied later.
            _append("ended", item_id=item["item_id"], approval_id=item["approval_id"])
            continue
        line = _gate(words, "notes")
        body = {"notes": line} if line else {}
        if item["status"] != "completed":
            body["status"] = "completed"
        if body:
            _patch_own(svc, item, body, closed=True)
        _append("ended", item_id=item["item_id"], approval_id=item["approval_id"])
        for other in items.values():
            if (other.get("parent_item") == item["item_id"] and other["role"] == "no" and not other["closed"]
                    and not other.get("ended_at")):
                if other["status"] != "completed":
                    _patch_own(svc, other, {"status": "completed"}, closed=True)
                _append("ended", item_id=other["item_id"])
    # A group ends when none of its cards is still waiting (judged on the record as it is now,
    # after the loop above ended its cards; review 2).
    items, _extras, _ok = _state()
    for g in list(items.values()):
        if g["role"] != "group" or g["closed"] or g["removed"] or g.get("ended_at"):
            continue
        kids = [c for c in items.values() if c.get("parent_item") == g["item_id"] and c["role"] == "card"]
        if kids and all(c["closed"] or c["state"] == "void" or c["removed"] or c.get("ended_at") for c in kids):
            fresh, _e, _ok = _state()
            kids = [fresh.get(c["item_id"], c) for c in kids]
            if all(c["closed"] or c["state"] == "void" or c["removed"] or c.get("ended_at") for c in kids):
                if g["status"] != "completed":
                    _patch_own(svc, g, {"status": "completed"}, closed=True)
                _append("ended", item_id=g["item_id"])
                for no in items.values():
                    if (no.get("parent_item") == g["item_id"] and no["role"] == "no" and not no["closed"]
                            and not no.get("ended_at")):
                        if no["status"] != "completed":
                            _patch_own(svc, no, {"status": "completed"}, closed=True)
                        _append("ended", item_id=no["item_id"])


def _tidy(svc, items, now) -> None:
    """Delete Friday's own items a week after they ended. Never anything else."""
    # egress: recorded by _service (egress_record.tasks wraps the only client this module uses)
    for item in list(items.values()):
        ended = item.get("ended_at")
        if item["removed"] or not ended or now - float(ended) < TIDY_AFTER_SECONDS:
            continue
        if item.get("parent_item"):
            continue                                     # deleting the parent removes its subtasks
        try:
            _append("delete_intent", item_id=item["item_id"])
            svc.tasks().delete(tasklist=item["list_id"], task=item["gid"]).execute()
        except Exception as e:
            _log.info("tasks inbox: tidy-up did not delete an item (%s)", type(e).__name__)
            continue
        _append("removed", item_id=item["item_id"])
        for child in items.values():
            if child.get("parent_item") == item["item_id"]:
                _append("removed", item_id=child["item_id"])


# ── what the owner sees ──────────────────────────────────────────────────────

def view() -> dict:
    cfg = config()
    items, extras, ok = _state()
    from agent_friday.services import google_accounts as ga
    acct = ga.get_account(cfg.get("account_id")) if cfg.get("account_id") else None
    open_cards = [i for i in items.values() if i["role"] == "card" and i["state"] in ("open", "ticked")
                  and not i["removed"] and not i["closed"]]
    recent = [e for e in extras["events"] if e.get("at") and _now() - float(e["at"]) < 7 * 86400][-20:]
    return {
        "enabled": bool(cfg.get("enabled")),
        "problem": cfg.get("problem") or ("" if ok else "The inbox record does not verify, so the phone list is paused."),
        "account": ({"id": acct.get("id"), "email": acct.get("email"), "label": acct.get("label")} if acct else None),
        "list_title": cfg.get("list_title") or list_title(),
        "text_level": cfg.get("text_level") if cfg.get("enabled") else None,
        "effective_level": effective_level(cfg) if cfg.get("enabled") else None,
        "recommended_level": RECOMMENDED_LEVEL,
        "levels": [
            {"id": "full", "label": "Full", "words": "For a few kinds of card with nothing private in them "
             "(a job that missed its time, a goal step), the card's own title and notice. Every other card "
             "(an email, a text, a call, a file, an action) is posted with the Short words."},
            {"id": "short", "label": "Short", "words": "Fixed words from Friday's code: which action, never "
             "recipients, addresses, amounts, file names or quotes."},
            {"id": "minimal", "label": "Minimal", "words": "Just \"Something needs you\"."},
        ],
        "accounts": writable_accounts(),
        "open_items": len(open_cards),
        "recent": recent,
        "foreign": len(extras["foreign"]),
        "last_poll": extras["last_poll"] or None,
        "record_ok": ok,
        "what_a_tick_does": [
            "Tick an item: the card comes to the top of Friday on your PC. Nothing is approved.",
            "Delete an item, or tick its \"No, not now\": the card is declined, if it is the kind where no "
            "only means don't do it (an email, an action, a missed job). Other cards stay on your PC.",
            "Change an item's words: nothing happens to the card, and a fresh item replaces it.",
            "Anything in the list that Friday didn't write is never acted on.",
        ],
        "where_it_goes": "Google stores what is posted and shows it wherever your tasks show: your phone, "
                         "Gmail, Calendar, and any app you gave Tasks access to.",
        "honest_limit": HONEST_LIMIT,
    }
