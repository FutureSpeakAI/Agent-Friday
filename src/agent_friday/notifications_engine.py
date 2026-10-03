"""
notifications_engine.py — Persistent notification queue for Agent Friday.

Stores notifications in ~/.friday/notifications.json. Background triggers
push() new entries; the UI polls /api/notifications and can dismiss/read.

A notification may also carry `proactive_chat=True`, in which case the chat
panel surfaces it as an unprompted Friday message (with a "proactive" badge).
The frontend polls /api/notifications/chat-injections and acknowledges them.

Priority levels:
    critical  — red, immediate, pulses the bell
    high      — orange, within the hour
    medium    — yellow, daily digest
    low       — blue, when convenient
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from agent_friday import brand
from agent_friday.paths import friday_home

FRIDAY_DIR = friday_home()
FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
NOTIF_FILE = FRIDAY_DIR / "notifications.json"
TRIGGER_STATE_FILE = FRIDAY_DIR / "notif_trigger_state.json"

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PRIORITY_COLORS = {
    "critical": brand.WARN,
    "high":     brand.CYAN,
    "medium":   brand.VIOLET_SOFT,
    "low":      brand.NEUTRAL,
}

_LOCK = threading.RLock()
_MAX_QUEUE = 200  # cap to keep the file small


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


#: Notifications raised off the record, in full, by id. The file keeps only a
#: content-free stub of each (services/off_record); the words live in memory.
#: The stub holds no field derived from the card's content: not its title or
#: body, not its actions or target, and not its grouping, dedupe, resolve or
#: job keys (those embed titles and ids). Those keys are stored as opaque
#: values salted per process, so they match nothing after a restart.
_OFF_RECORD_ITEMS: Dict[str, Dict[str, Any]] = {}
#: Dismissals of off-the-record cards, in memory only; never in the store.
_OFF_RECORD_LEDGER: Dict[str, Dict[str, Any]] = {}
_STUB_KEYS = ("id", "priority", "source", "kind", "read", "dismissed", "created_at",
              "chat_injected", "off_record", "tier", "count", "held", "quiet",
              "resolved_at")
_OPAQUE_KEYS = ("dedupe_key", "collapse_key", "resolve_key", "job")
_SALT = os.urandom(16)


def _opaque(value: Any) -> str:
    return "off:" + hmac.new(_SALT, str(value).encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def _disk_view(n: Dict[str, Any]) -> Dict[str, Any]:
    if not n.get("off_record"):
        return n
    _OFF_RECORD_ITEMS[n["id"]] = n
    stub = {k: n.get(k) for k in _STUB_KEYS if k in n}
    stub.update({k: _opaque(n[k]) for k in _OPAQUE_KEYS if n.get(k)})
    stub.update(title="Off the record", body="", proactive_chat=False,
                chat_message=None, meta={}, actions=[], target={})
    return stub


#: Dismissed jobs kept in the store. The queue is capped at _MAX_QUEUE cards and
#: clear_dismissed() drops dismissed cards, so a dismissal lives here, not on
#: the card: forgetting the card must not forget the dismissal.
_LEDGER_MAX = 2000


def _read_store() -> tuple:
    """(cards, dismissed_jobs) from notifications.json. The file is
    {"items": [...], "dismissed_jobs": {key: {"rank", "at"}}}; a bare list
    (the older shape) is cards with no dismissals."""
    if not NOTIF_FILE.exists():
        return [], {}
    try:
        data = json.loads(NOTIF_FILE.read_text(encoding="utf-8"))
    except Exception:
        return [], {}
    ledger: Dict[str, Any] = {}
    older_shape = isinstance(data, list)
    if isinstance(data, dict):
        if isinstance(data.get("dismissed_jobs"), dict):
            ledger = data["dismissed_jobs"]
        data = data.get("items") if isinstance(data.get("items"), list) else []
    if not isinstance(data, list):
        return [], ledger
    items = [dict(_OFF_RECORD_ITEMS[n["id"]], read=n.get("read"),
                  dismissed=n.get("dismissed"), chat_injected=n.get("chat_injected"))
             if isinstance(n, dict) and n.get("off_record") and n.get("id") in _OFF_RECORD_ITEMS
             else n for n in data]
    if older_shape:
        # A store written before dismissals were kept: the owner's dismissals
        # are the dismissed cards that nothing resolved.
        for n in items:
            if isinstance(n, dict) and n.get("dismissed") and not n.get("resolved_at"):
                _remember_dismissal(ledger, n)
    return items, ledger


def _load() -> List[Dict[str, Any]]:
    return _read_store()[0]


def _load_ledger() -> Dict[str, Any]:
    return _read_store()[1]


def _save(items: List[Dict[str, Any]], ledger: Optional[Dict[str, Any]] = None) -> None:
    """Write the cards. The dismissed-jobs ledger is kept as stored unless a
    new one is given."""
    if ledger is None:
        ledger = _load_ledger()
    if len(items) > _MAX_QUEUE:
        items = items[-_MAX_QUEUE:]
    if len(ledger) > _LEDGER_MAX:
        keep = sorted(ledger.items(), key=lambda kv: str((kv[1] or {}).get("at") or ""))
        ledger = dict(keep[-_LEDGER_MAX:])
    items = [_disk_view(n) if isinstance(n, dict) else n for n in items]
    try:
        NOTIF_FILE.write_text(json.dumps({"items": items, "dismissed_jobs": ledger}, indent=2),
                              encoding="utf-8")
    except Exception as e:
        print(f"[notifications_engine] save failed: {e}")


# ── Dismissal, keyed to the job ──────────────────────────────────────────────

def _dkey(n: Dict[str, Any]) -> str:
    """The ledger key of a card. A job id is stored as is; a card about no job
    is remembered by a hash of what it reports, so no title reaches the file."""
    from agent_friday.services import notification_policy as _pol
    key = _pol.dismissal_key(n)
    if key.startswith("thing:"):
        key = "thing:" + hashlib.sha1(key[6:].encode("utf-8")).hexdigest()[:20]
    return key


def _rank_of(n: Dict[str, Any]) -> int:
    from agent_friday.services import notification_policy as _pol
    return _pol.rank(n.get("tier") or _pol.tier_for(n.get("kind"), n.get("source"),
                                                    n.get("priority")),
                     n.get("priority") or "")


def _hidden(n: Dict[str, Any], ledger: Dict[str, Any]) -> bool:
    """A card the owner has already dismissed: its job is in the ledger at
    this card's rank or higher. A pending approval is never hidden here."""
    from agent_friday.services import notification_policy as _pol
    if _pol.is_approval(n.get("kind")):
        return False
    key = _dkey(n)
    rank = _rank_of(n)
    return any(int((led.get(key) or {}).get("rank") or 0) >= rank
               for led in (ledger, _OFF_RECORD_LEDGER))


def _remember_dismissal(ledger: Dict[str, Any], n: Dict[str, Any]) -> None:
    from agent_friday.services import notification_policy as _pol
    if _pol.is_approval(n.get("kind")):
        return                              # an approval's fate is its decision
    if n.get("off_record"):
        ledger = _OFF_RECORD_LEDGER         # off the record: memory only
    key = _dkey(n)
    prev = int((ledger.get(key) or {}).get("rank") or 0)
    ledger[key] = {"rank": max(prev, _rank_of(n)), "at": _now_iso()}


def _load_trigger_state() -> Dict[str, Any]:
    if not TRIGGER_STATE_FILE.exists():
        return {}
    try:
        return json.loads(TRIGGER_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_trigger_state(state: Dict[str, Any]) -> None:
    try:
        TRIGGER_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────────
#  Public API
# ────────────────────────────────────────────────────────────────────────

def push(
    *,
    title: str,
    body: str = "",
    priority: str = "medium",
    source: str = "system",
    kind: str = "info",
    actions: Optional[List[Dict[str, Any]]] = None,
    proactive_chat: bool = False,
    chat_message: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    target: Optional[Dict[str, Any]] = None,
    tier: Optional[str] = None,
    resolve_key: Optional[str] = None,
    job: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a notification to the tray (services/notification_policy).

    tier:        NEEDS_YOU / FYI / LOG_ONLY; derived from kind, source and
                 priority when omitted. An approval is always NEEDS_YOU.
    resolve_key: a later resolve(resolve_key) closes this card (a failure
                 that the next successful run of the same job resolves).
    job:         the job this card is about; derived from meta and the
                 scheduler/run/approval keys when omitted. A job the owner
                 dismissed stays dismissed: a card for it at the same or a
                 lower rank is not shown (returned with suppressed=True), a
                 higher-rank card is.
    Repeats (same dedupe_key, else same kind + source + title with numbers
    folded) collapse into one card: the count goes up and the card shows the
    latest occurrence. Distinct approvals never collapse.

    dedupe_key: if provided, drop the new notification when an unread
                notification with the same key is already queued.
    chat_message: text to inject into the chat stream if proactive_chat=True.
                  Defaults to title + body.
    target:     deep-link descriptor the UI uses to navigate when the
                notification is clicked, e.g.
                  {"workspace": "news", "tab": "frontpage"}
                  {"workspace": "messages", "lane": "career", "thread_id": "..."}
                  {"workspace": "calendar", "event_id": "...", "date": "..."}
                  {"workspace": "studio"}  /  {"url": "https://..."}
    """
    if priority not in PRIORITY_ORDER:
        priority = "medium"
    from agent_friday.services import notification_policy as _pol
    approval = _pol.is_approval(kind)
    t = _pol.tier_for(kind, source, priority, tier)
    if not approval and (t == _pol.LOG_ONLY or _pol.is_muted(kind, source)):
        logged = {"title": "Off the record" if _off_record_now() else title,
                  "kind": kind, "source": source, "priority": priority,
                  "tier": _pol.LOG_ONLY, "logged": True,
                  "muted": t != _pol.LOG_ONLY, "created_at": _now_iso()}
        _log_only(logged)
        return logged
    quiet = _pol.owner_in_conversation()
    key = dedupe_key if approval else _pol.collapse_key(kind, source, title, dedupe_key)
    job = _pol.job_for(job=job, meta=meta, dedupe_key=dedupe_key, resolve_key=resolve_key)
    off = _off_record_now()
    with _LOCK:
        items, ledger = _read_store()
        probe = {"kind": kind, "source": source, "priority": priority, "tier": t,
                 "job": job, "collapse_key": key, "dedupe_key": dedupe_key,
                 "off_record": off}
        if not approval and _hidden(probe, ledger):
            return dict(probe, title=title, body=body, suppressed=True, dismissed=True,
                        read=True, created_at=_now_iso())
        if key:
            for n in items:
                if n.get("dismissed"):
                    continue
                if (n.get("collapse_key") or n.get("dedupe_key")) != key:
                    continue
                if approval:
                    return n                     # the same approval, already waiting
                before = _rank_of(n)
                n["count"] = int(n.get("count") or 1) + 1
                n["title"], n["body"] = title, body
                n["created_at"] = _now_iso()
                if PRIORITY_ORDER[priority] < PRIORITY_ORDER.get(n.get("priority"), 9):
                    n["priority"] = priority
                if t == _pol.NEEDS_YOU:
                    n["read"] = False
                if actions:
                    n["actions"] = actions
                if meta:
                    n["meta"] = dict(n.get("meta") or {}, **meta)
                if job and not n.get("job"):
                    n["job"] = job
                if _pol.rank(t, priority) > before:
                    n["tier"] = t                # a failure after progress is louder
                    n["read"] = t == _pol.FYI
                _save(items, ledger)
                return n
        entry = {
            "id": str(uuid.uuid4()),
            "title": title,
            "body": body,
            "priority": priority,
            "source": source,
            "kind": kind,
            "actions": actions or [],
            "target": target or {},
            "read": False,
            "dismissed": False,
            "created_at": _now_iso(),
            "proactive_chat": bool(proactive_chat),
            "chat_message": chat_message or (f"{title}\n\n{body}".strip() if proactive_chat else None),
            "chat_injected": False,
            "dedupe_key": dedupe_key,
            "meta": meta or {},
            "tier": t,
            "count": 1,
            "collapse_key": key,
            # Mid-conversation: a card waits until the owner is done; an
            # approval arrives anyway, marked quiet so nothing interrupts.
            "held": bool(quiet and not approval),
            "quiet": bool(quiet and approval),
            "resolve_key": resolve_key,
            "job": job,
        }
        if t == _pol.FYI:
            entry["read"] = True              # an FYI never bumps the badge
        if off:
            entry["off_record"] = True
        items.append(entry)
        _save(items, ledger)
        return entry


def _off_record_now() -> bool:
    try:
        from agent_friday.services import off_record as _off
        return bool(_off.skip("notifications"))
    except Exception:
        return False


def _log_only(entry: Dict[str, Any]) -> None:
    """Housekeeping and muted kinds: the activity log, never the tray."""
    try:
        import logging
        logging.getLogger("friday.notifications").info(
            "log-only notification [%s/%s]: %s", entry.get("kind"), entry.get("source"),
            entry.get("title"))
    except Exception:
        pass
    try:
        from agent_friday.services import activity_ledger
        activity_ledger.record("notification", kind=entry.get("kind"),
                               source=entry.get("source"), tier=entry.get("tier"),
                               muted=bool(entry.get("muted")))
    except Exception:
        pass


def run_card(run_key: str, state: str, *, title: str, body: str = "",
             source: str = "system", kind: str = "run",
             actions: Optional[List[Dict[str, Any]]] = None,
             target: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One card per run, updated in place: started -> done / failed. A failed
    run needs the owner; anything else is FYI."""
    from agent_friday.services import notification_policy as _pol
    t = _pol.NEEDS_YOU if state == "failed" else _pol.FYI
    key = "run:" + str(run_key)
    with _LOCK:
        items = _load()
        for n in items:
            if n.get("collapse_key") == key and not n.get("dismissed"):
                n.update(title=title, body=body, tier=t, created_at=_now_iso(),
                         read=(t == _pol.FYI))
                n["meta"] = dict(n.get("meta") or {}, state=state)
                if actions is not None:
                    n["actions"] = actions
                _save(items)
                return n
    return push(title=title, body=body, source=source, kind=kind,
                priority="high" if state == "failed" else "low", actions=actions,
                target=target, dedupe_key=key, tier=t, meta={"state": state})


def resolve(resolve_key: str) -> int:
    """Close the cards a later success resolves. Returns how many closed.

    The failure episode is over, so a dismissal of the job drops back to the
    FYI rank: its next failure is news again, its routine successes stay
    dismissed."""
    if not resolve_key:
        return 0
    from agent_friday.services import notification_policy as _pol
    closed = 0
    with _LOCK:
        items, ledger = _read_store()
        keys = set()
        job = _pol.job_for(resolve_key=resolve_key)
        if job:
            keys.add(job)
        for n in items:
            if n.get("resolve_key") != resolve_key:
                continue
            keys.add(_dkey(n))
            if not n.get("dismissed"):
                n["dismissed"] = True
                n["read"] = True
                n["resolved_at"] = _now_iso()
                closed += 1
        lowered = False
        for k in keys:
            for led in (ledger, _OFF_RECORD_LEDGER):
                hit = led.get(k)
                if hit and int(hit.get("rank") or 0) > 1:
                    led[k] = dict(hit, rank=1, at=_now_iso())
                    lowered = lowered or led is ledger
        if closed or lowered:
            _save(items, ledger)
    return closed


def resolve_approval(approval_id: str) -> int:
    """An approval was decided or expired: its card is no longer waiting.
    This is the approval's own outcome, never a dismissal on its behalf."""
    if not approval_id:
        return 0
    key = "approval:%s" % approval_id
    closed = 0
    with _LOCK:
        items = _load()
        for n in items:
            if n.get("dedupe_key") == key and not n.get("dismissed"):
                n["dismissed"] = True
                n["read"] = True
                n["resolved_at"] = _now_iso()
                closed += 1
        if closed:
            _save(items)
    return closed


def mute(kind: str, source: str) -> list:
    """Mute a kind (reversible in Settings). Approvals cannot be muted."""
    from agent_friday.services import notification_policy as _pol
    from agent_friday.core import _save_settings
    mutes = sorted(_pol.muted_kinds() | ({_pol.mute_key(kind, source)}
                                         if not _pol.is_approval(kind) else set()))
    _save_settings({"notification_mutes": mutes})
    return mutes


def unmute(kind: str, source: str) -> list:
    from agent_friday.services import notification_policy as _pol
    from agent_friday.core import _save_settings
    mutes = sorted(_pol.muted_kinds() - {_pol.mute_key(kind, source)})
    _save_settings({"notification_mutes": mutes})
    return mutes


def _is_pending_approval(n: Dict[str, Any]) -> bool:
    from agent_friday.services import notification_policy as _pol
    return _pol.is_approval(n.get("kind")) and not n.get("dismissed")


def dismiss_all() -> int:
    """Clear the tray. Pending approvals stay: they are decisions, not news.
    Every job cleared stays dismissed (see dismiss())."""
    with _LOCK:
        items, ledger = _read_store()
        n = 0
        for it in items:
            if not it.get("dismissed") and not _is_pending_approval(it):
                it["dismissed"] = True
                it["read"] = True
                _remember_dismissal(ledger, it)
                n += 1
        if n:
            _save(items, ledger)
    return n


def dismiss_group(group: str) -> int:
    """Clear one group (services/notification_policy.group_key). A pending
    approval in it stays: clearing a group is never a decision."""
    from agent_friday.services import notification_policy as _pol
    if not group:
        return 0
    with _LOCK:
        items, ledger = _read_store()
        n = 0
        for it in items:
            if it.get("dismissed") or _is_pending_approval(it):
                continue
            if _pol.group_key(it) != group:
                continue
            it["dismissed"] = True
            it["read"] = True
            _remember_dismissal(ledger, it)
            n += 1
        if n:
            _save(items, ledger)
    return n


def dismiss_job(item: Dict[str, Any]) -> bool:
    """Remember a dismissal for a card the queue does not hold (a computed
    card such as "Daily briefing ready"), keyed like any other card."""
    if not isinstance(item, dict):
        return False
    from agent_friday.services import notification_policy as _pol
    if _pol.is_approval(item.get("kind")):
        return False
    with _LOCK:
        items, ledger = _read_store()
        _remember_dismissal(ledger, item)
        _save(items, ledger)
    return True


def visible(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The cards of `items` the owner has not dismissed (by job). Used for
    computed cards, which are never stored."""
    ledger = _load_ledger()
    return [n for n in items if not _hidden(n, ledger)]


def groups(limit: int = 200) -> List[Dict[str, Any]]:
    """The tray's cards grouped by job, then kind, then source: needs-you
    groups first, each with its cards. A memory-proposal group lists each
    proposal as its own item to keep or skip."""
    from agent_friday.services import notification_policy as _pol
    out: Dict[str, Dict[str, Any]] = {}
    for n in list_notifications(limit=limit):
        g = n.get("group") or _pol.group_key(n)
        grp = out.get(g)
        if grp is None:
            grp = out[g] = {"key": g, "kind": n.get("kind"), "source": n.get("source"),
                            "title": n.get("title"), "tier": n.get("tier") or _pol.NEEDS_YOU,
                            "memory": g == _pol.MEMORY_GROUP, "count": 0, "approvals": 0,
                            "items": []}
        grp["items"].append(n)
        grp["count"] += int(n.get("count") or 1)
        if _pol.is_approval(n.get("kind")):
            grp["approvals"] += 1
        if (n.get("tier") or _pol.NEEDS_YOU) == _pol.NEEDS_YOU:
            grp["tier"] = _pol.NEEDS_YOU
    for grp in out.values():
        if grp["memory"]:
            k = len(grp["items"])
            grp["title"] = "%d memory proposal%s to keep or skip" % (k, "" if k == 1 else "s")
    return sorted(out.values(), key=lambda g: g["tier"] != _pol.NEEDS_YOU)


def _release_held_locked(items: List[Dict[str, Any]]) -> bool:
    """Deliver cards held during a conversation once it is over."""
    from agent_friday.services import notification_policy as _pol
    if _pol.owner_in_conversation():
        return False
    changed = False
    for n in items:
        if n.get("held"):
            n["held"] = False
            changed = True
        if n.get("quiet"):
            n["quiet"] = False
            changed = True
    return changed


def upsert_status(
    *,
    key: str,
    title: str,
    body: str = "",
    source: str = "system",
    kind: str = "status",
    priority: str = "low",
    target: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create or refresh a single, always-read 'status' entry keyed by
    ``dedupe_key``. Unlike push(), a matching existing entry is UPDATED in place
    (new title/body/timestamp) instead of being left stale or duplicated.

    The entry is marked read (``read=True``) so it shows in the panel as the
    latest status but never increments the unread badge — the pattern the hourly
    heartbeat uses to appear exactly once with its most recent run time.
    """
    if priority not in PRIORITY_ORDER:
        priority = "low"
    with _LOCK:
        items = _load()
        for n in items:
            if n.get("dedupe_key") == key and not n.get("dismissed"):
                n["title"] = title
                n["body"] = body
                n["priority"] = priority
                n["source"] = source
                n["kind"] = kind
                n["read"] = True
                n["created_at"] = _now_iso()
                if target is not None:
                    n["target"] = target
                if meta is not None:
                    n["meta"] = meta
                _save(items)
                return n
        entry = {
            "id": str(uuid.uuid4()),
            "title": title,
            "body": body,
            "priority": priority,
            "source": source,
            "kind": kind,
            "actions": [],
            "target": target or {},
            "read": True,          # status entries never bump the unread badge
            "dismissed": False,
            "created_at": _now_iso(),
            "proactive_chat": False,
            "chat_message": None,
            "chat_injected": False,
            "dedupe_key": key,
            "meta": meta or {},
        }
        try:
            from agent_friday.services import off_record as _off
            if _off.skip("notifications"):
                entry["off_record"] = True
        except Exception:
            pass
        items.append(entry)
        _save(items)
        return entry


def list_notifications(
    include_dismissed: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return notifications newest-first, each with its `group`
    (services/notification_policy.group_key). A card whose job the owner
    dismissed is left out like a dismissed card."""
    from agent_friday.services import notification_policy as _pol
    with _LOCK:
        items, ledger = _read_store()
        if _release_held_locked(items):
            _save(items, ledger)
    items = [n for n in items if not n.get("held")]
    if not include_dismissed:
        items = [n for n in items if not n.get("dismissed") and not _hidden(n, ledger)]
    items = [dict(n, group=_pol.group_key(n)) for n in items]
    # Priority, then newest first. created_at has one-second resolution, so
    # two notifications in the same second tie; new entries are appended, so
    # the later position in the stored list is the newer one.
    order = sorted(
        enumerate(items),
        key=lambda pair: (
            PRIORITY_ORDER.get(pair[1].get("priority", "medium"), 9),
            -_iso_ts(pair[1].get("created_at", "")),
            -pair[0],
        ),
    )
    return [n for _, n in order][:limit]


def _iso_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "")).timestamp()
    except Exception:
        return 0.0


def mark_read(notif_id: str) -> bool:
    with _LOCK:
        items = _load()
        for n in items:
            if n.get("id") == notif_id:
                n["read"] = True
                _save(items)
                return True
    return False


def mark_all_read() -> int:
    with _LOCK:
        items = _load()
        n = 0
        for it in items:
            if not it.get("read"):
                it["read"] = True
                n += 1
        if n:
            _save(items)
    return n


def dismiss(notif_id: str) -> bool:
    """Dismiss a card, and with it the job it is about: the job's other cards
    at this rank or lower close too, and later ones at this rank or lower are
    not shown (on any surface, across restarts)."""
    with _LOCK:
        items, ledger = _read_store()
        card = next((n for n in items if n.get("id") == notif_id), None)
        if card is None:
            return False
        card["dismissed"] = True
        card["read"] = True
        _remember_dismissal(ledger, card)
        for n in items:
            if n is not card and not n.get("dismissed") and _hidden(n, ledger):
                n["dismissed"] = True
                n["read"] = True
        _save(items, ledger)
        return True


def clear_dismissed() -> int:
    with _LOCK:
        items = _load()
        keep = [n for n in items if not n.get("dismissed")]
        removed = len(items) - len(keep)
        if removed:
            _save(keep)
    return removed


def unread_count() -> int:
    """The badge: unread cards that need the owner. FYI and held cards never
    count."""
    with _LOCK:
        items, ledger = _read_store()
        if _release_held_locked(items):
            _save(items, ledger)
    return sum(1 for n in items if not n.get("read") and not n.get("dismissed")
               and not n.get("held") and n.get("tier", "needs_you") != "fyi"
               and not _hidden(n, ledger))


# ────────────────────────────────────────────────────────────────────────
#  Proactive chat injection
# ────────────────────────────────────────────────────────────────────────

def pending_chat_injections() -> List[Dict[str, Any]]:
    """Notifications that should appear in the chat stream and haven't yet."""
    from agent_friday.services import notification_policy as _pol
    if _pol.owner_in_conversation():
        return []                       # never mid-sentence; delivered after
    with _LOCK:
        items, ledger = _read_store()
    items = [n for n in items if not _hidden(n, ledger)]
    return [
        {
            "id": n["id"],
            "priority": n.get("priority", "medium"),
            "text": n.get("chat_message") or n.get("title", ""),
            "title": n.get("title", ""),
            "source": n.get("source", "system"),
            "kind": n.get("kind", "info"),
            "created_at": n.get("created_at"),
        }
        for n in items
        if n.get("proactive_chat") and not n.get("chat_injected") and not n.get("dismissed")
    ]


def ack_chat_injection(notif_id: str) -> bool:
    with _LOCK:
        items = _load()
        for n in items:
            if n.get("id") == notif_id:
                n["chat_injected"] = True
                _save(items)
                return True
    return False


# ────────────────────────────────────────────────────────────────────────
#  Trigger helpers (called by background polling loop)
# ────────────────────────────────────────────────────────────────────────

def get_trigger_state(key: str, default: Any = None) -> Any:
    return _load_trigger_state().get(key, default)


def set_trigger_state(key: str, value: Any) -> None:
    st = _load_trigger_state()
    st[key] = value
    _save_trigger_state(st)


__all__ = [
    "push",
    "upsert_status",
    "list_notifications",
    "mark_read",
    "mark_all_read",
    "dismiss",
    "dismiss_all",
    "dismiss_group",
    "dismiss_job",
    "visible",
    "groups",
    "resolve",
    "resolve_approval",
    "clear_dismissed",
    "unread_count",
    "pending_chat_injections",
    "ack_chat_injection",
    "get_trigger_state",
    "set_trigger_state",
    "PRIORITY_COLORS",
    "PRIORITY_ORDER",
]
