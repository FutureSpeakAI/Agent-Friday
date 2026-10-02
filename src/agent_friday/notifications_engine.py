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

import json
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
_OFF_RECORD_ITEMS: Dict[str, Dict[str, Any]] = {}
_STUB_KEYS = ("id", "priority", "source", "kind", "actions", "target", "read",
              "dismissed", "created_at", "dedupe_key", "chat_injected", "off_record",
              "tier", "count", "collapse_key", "held", "quiet", "resolve_key",
              "resolved_at")


def _disk_view(n: Dict[str, Any]) -> Dict[str, Any]:
    if not n.get("off_record"):
        return n
    _OFF_RECORD_ITEMS[n["id"]] = n
    stub = {k: n.get(k) for k in _STUB_KEYS if k in n}
    stub.update(title="Off the record", body="", proactive_chat=False,
                chat_message=None, meta={})
    return stub


def _load() -> List[Dict[str, Any]]:
    if not NOTIF_FILE.exists():
        return []
    try:
        data = json.loads(NOTIF_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data = data["items"]
        if isinstance(data, list):
            return [dict(_OFF_RECORD_ITEMS[n["id"]], read=n.get("read"),
                         dismissed=n.get("dismissed"), chat_injected=n.get("chat_injected"))
                    if isinstance(n, dict) and n.get("off_record") and n.get("id") in _OFF_RECORD_ITEMS
                    else n for n in data]
    except Exception:
        pass
    return []


def _save(items: List[Dict[str, Any]]) -> None:
    if len(items) > _MAX_QUEUE:
        items = items[-_MAX_QUEUE:]
    items = [_disk_view(n) if isinstance(n, dict) else n for n in items]
    try:
        NOTIF_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[notifications_engine] save failed: {e}")


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
) -> Dict[str, Any]:
    """Add a notification to the tray (services/notification_policy).

    tier:        NEEDS_YOU / FYI / LOG_ONLY; derived from kind, source and
                 priority when omitted. An approval is always NEEDS_YOU.
    resolve_key: a later resolve(resolve_key) closes this card (a failure
                 that the next successful run of the same job resolves).
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
        logged = {"title": title, "kind": kind, "source": source, "priority": priority,
                  "tier": _pol.LOG_ONLY, "logged": True,
                  "muted": t != _pol.LOG_ONLY, "created_at": _now_iso()}
        _log_only(logged)
        return logged
    quiet = _pol.owner_in_conversation()
    key = dedupe_key if approval else _pol.collapse_key(kind, source, title, dedupe_key)
    with _LOCK:
        items = _load()
        if key:
            for n in items:
                if n.get("dismissed"):
                    continue
                if (n.get("collapse_key") or n.get("dedupe_key")) != key:
                    continue
                if approval:
                    return n                     # the same approval, already waiting
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
                _save(items)
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
        }
        if t == _pol.FYI:
            entry["read"] = True              # an FYI never bumps the badge
        try:
            from agent_friday.services import off_record as _off
            if _off.skip("notifications"):
                entry["off_record"] = True
        except Exception:
            pass
        items.append(entry)
        _save(items)
        return entry


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
    """Close the cards a later success resolves. Returns how many closed."""
    if not resolve_key:
        return 0
    closed = 0
    with _LOCK:
        items = _load()
        for n in items:
            if n.get("resolve_key") == resolve_key and not n.get("dismissed"):
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
    """Clear the tray. Pending approvals stay: they are decisions, not news."""
    with _LOCK:
        items = _load()
        n = 0
        for it in items:
            if not it.get("dismissed") and not _is_pending_approval(it):
                it["dismissed"] = True
                it["read"] = True
                n += 1
        if n:
            _save(items)
    return n


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
    """Return notifications newest-first."""
    with _LOCK:
        items = _load()
        if _release_held_locked(items):
            _save(items)
    items = [n for n in items if not n.get("held")]
    if not include_dismissed:
        items = [n for n in items if not n.get("dismissed")]
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
    with _LOCK:
        items = _load()
        for n in items:
            if n.get("id") == notif_id:
                n["dismissed"] = True
                n["read"] = True
                _save(items)
                return True
    return False


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
        items = _load()
        if _release_held_locked(items):
            _save(items)
    return sum(1 for n in items if not n.get("read") and not n.get("dismissed")
               and not n.get("held") and n.get("tier", "needs_you") != "fyi")


# ────────────────────────────────────────────────────────────────────────
#  Proactive chat injection
# ────────────────────────────────────────────────────────────────────────

def pending_chat_injections() -> List[Dict[str, Any]]:
    """Notifications that should appear in the chat stream and haven't yet."""
    from agent_friday.services import notification_policy as _pol
    if _pol.owner_in_conversation():
        return []                       # never mid-sentence; delivered after
    with _LOCK:
        items = _load()
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
    "clear_dismissed",
    "unread_count",
    "pending_chat_injections",
    "ack_chat_injection",
    "get_trigger_state",
    "set_trigger_state",
    "PRIORITY_COLORS",
    "PRIORITY_ORDER",
]
