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
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

HOME = Path(os.path.expanduser("~"))
FRIDAY_DIR = HOME / ".friday"
FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
NOTIF_FILE = FRIDAY_DIR / "notifications.json"
TRIGGER_STATE_FILE = FRIDAY_DIR / "notif_trigger_state.json"

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PRIORITY_COLORS = {
    "critical": "#ff3366",
    "high":     "#ff8a00",
    "medium":   "#ffd23f",
    "low":      "#00d4ff",
}

_LOCK = threading.RLock()
_MAX_QUEUE = 200  # cap to keep the file small


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _load() -> List[Dict[str, Any]]:
    if not NOTIF_FILE.exists():
        return []
    try:
        data = json.loads(NOTIF_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
    except Exception:
        pass
    return []


def _save(items: List[Dict[str, Any]]) -> None:
    if len(items) > _MAX_QUEUE:
        items = items[-_MAX_QUEUE:]
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
) -> Dict[str, Any]:
    """Add a notification to the queue.

    dedupe_key: if provided, drop the new notification when an unread
                notification with the same key is already queued.
    chat_message: text to inject into the chat stream if proactive_chat=True.
                  Defaults to title + body.
    """
    if priority not in PRIORITY_ORDER:
        priority = "medium"
    with _LOCK:
        items = _load()
        if dedupe_key:
            for n in items:
                if n.get("dedupe_key") == dedupe_key and not n.get("dismissed"):
                    return n
        entry = {
            "id": str(uuid.uuid4()),
            "title": title,
            "body": body,
            "priority": priority,
            "source": source,
            "kind": kind,
            "actions": actions or [],
            "read": False,
            "dismissed": False,
            "created_at": _now_iso(),
            "proactive_chat": bool(proactive_chat),
            "chat_message": chat_message or (f"{title}\n\n{body}".strip() if proactive_chat else None),
            "chat_injected": False,
            "dedupe_key": dedupe_key,
            "meta": meta or {},
        }
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
    if not include_dismissed:
        items = [n for n in items if not n.get("dismissed")]
    items.sort(
        key=lambda n: (
            PRIORITY_ORDER.get(n.get("priority", "medium"), 9),
            -_iso_ts(n.get("created_at", "")),
        )
    )
    # Re-sort newest first within priority
    items.sort(key=lambda n: n.get("created_at", ""), reverse=True)
    items.sort(key=lambda n: PRIORITY_ORDER.get(n.get("priority", "medium"), 9))
    return items[:limit]


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
    with _LOCK:
        items = _load()
    return sum(1 for n in items if not n.get("read") and not n.get("dismissed"))


# ────────────────────────────────────────────────────────────────────────
#  Proactive chat injection
# ────────────────────────────────────────────────────────────────────────

def pending_chat_injections() -> List[Dict[str, Any]]:
    """Notifications that should appear in the chat stream and haven't yet."""
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
