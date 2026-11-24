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
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_friday.paths import friday_home

_log = logging.getLogger("friday.tasks_inbox")
_LOCK = threading.RLock()

#: The label a decision made from Google Tasks carries. It has no "owner:" prefix, so it
#: never falls into any owner path; approvals.decide_with_outcome refuses it for an approve.
TICK_LABEL = "remote:tasks_tick"

TEXT_LEVELS = ("full", "short", "minimal")
RECOMMENDED_LEVEL = "short"

#: Card kinds a phone may decline. For each, "no" only means "don't do it": an email not
#: sent, a tool call not made, an outside action not taken, a missed job not run late.
PHONE_DECLINABLE_KINDS = frozenset({"tool_confirm", "tainted_action", "governed_action",
                                    "external_message", "scheduled_late_run"})

#: Kinds never posted: screen-only cards are posted as info items (handled below); these
#: are not posted at all (pairing, widening Friday's own powers, money).
NEVER_POSTED_KINDS = frozenset({"caller_pair", "caller_release", "caller_spend", "device_pair", "device_right",
                                # Website-task plans stay on the PC (owner decision D9).
                                "web_task_plan", "web_task_plan_new_sites", "web_task_question"})


MAX_POSTS_PER_DAY = 30
MAX_REPOSTS_PER_HOUR = 5

HONEST_LIMIT = ("Friday no longer mirrors approvals to Google Tasks: nothing is posted there and nothing "
                "there is read back. Approvals wait in the tray, which your phone, tablet and widget reach.")


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


def _parse_iso(value) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


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


# ── enable / disable (the routes decide who may) ─────────────────────────────

def enable(account_id: str, text_level: str) -> dict:
    """Retired: approvals are no longer mirrored to Google Tasks, so the list cannot be turned on again."""
    return {"ok": False, "message": "Friday no longer mirrors approvals to Google Tasks. They wait in the tray, "
                                    "which your phone, tablet and widget reach through the gateway."}


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

#: Notice kinds the old mirror made. Nothing makes them any more; any left in the list is cleared.
RETIRED_NOTICE_KINDS = frozenset({"tasks_inbox_tick", "tasks_inbox_change"})


def clear_retired_notices() -> int:
    """Dismiss every notice the old mirror left behind. Never raises."""
    try:
        import agent_friday.notifications_engine as _ne
        return _ne.dismiss_kinds(RETIRED_NOTICE_KINDS)
    except Exception as e:
        _log.debug("tasks inbox: old notices not cleared (%s)", type(e).__name__)
        return 0


def tick(now: Optional[float] = None, force: bool = False) -> dict:
    """Retired: approval cards are no longer mirrored to Google Tasks, and nothing is read back from there.

    Approvals live only in Friday's tray, which the phone, tablet and widget reach through the gateway. A machine
    whose settings still say "on" posts nothing and reads nothing; what the old mirror left in the notice list
    (a "brought to your desk from Google Tasks" notice, which never closed with its card) is cleared. The list in the
    owner's Google account is left as it is: turning it off (``disable``) is the owner's to do."""
    clear_retired_notices()
    if not config().get("enabled"):
        return {"ok": True, "skipped": "off"}
    return {"ok": True, "skipped": "retired",
            "message": "Friday no longer mirrors approvals to Google Tasks; decide them in the tray."}


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
        # Retired (owner decision 2026-11-22): approvals live only in the tray. A machine that had it on may turn it off.
        "retired": True,
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
            "Nothing: Friday no longer reads this list. Decide approvals in the tray.",
        ],
        "where_it_goes": "Google stores what is posted and shows it wherever your tasks show: your phone, "
                         "Gmail, Calendar, and any app you gave Tasks access to.",
        "honest_limit": HONEST_LIMIT,
    }
