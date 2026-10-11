"""Opaque handles: how a cloud model refers to private material without seeing it.

Cloud specifies, local executes (Beta 1.1, addendum CS-1). When Friday describes
material to a cloud model ("3 wiki pages about Project X: h_..., h_..."), each
item is a HANDLE: ``h_`` plus 20 random characters. The handle is not derived
from the thing it names, it carries no path, id or title, and it is useless
outside the conversation it was issued in. Underneath, it maps locally to
``(kind, local ref, scope, expiry)``; the local ref (a Library document id, a
wiki path resolved at execution time, a KG fact id, a file root and relative
path, a mail thread id) never leaves this module except through ``resolve``,
which only local code calls.

What a cloud model may see about a handle is ``metadata()``: kind, a coarse
date bucket, a size class, and a title ONLY when the title passes the egress
gate. Handles live in memory and expire after 24 hours by default.
"""
from __future__ import annotations

import logging
import re
import secrets
import string
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_log = logging.getLogger("friday.handles")

PREFIX = "h_"
_ALPHABET = string.ascii_letters + string.digits
BODY_LEN = 20
HANDLE_RE = re.compile(r"^h_[A-Za-z0-9]{20}$")

DEFAULT_TTL_S = 24 * 3600.0
MAX_TTL_S = 7 * 24 * 3600.0
MAX_HANDLES = 5000

#: What a handle can name. The local ref is kind-specific and stays local.
KINDS = ("library_doc", "wiki_page", "kg_fact", "kg_entity", "file", "mail_thread",
         "calendar_event", "note", "vault_item")

#: How private the material is. "private" and "vault" force the P3 class.
TIERS = ("public", "normal", "private", "vault")
PRIVATE_TIERS = frozenset({"private", "vault"})


class HandleError(ValueError):
    """A handle could not be issued."""


@dataclass
class HandleRecord:
    handle: str
    kind: str
    conversation_id: str
    #: The local reference. Never shown, never in a repr, never in metadata.
    ref: object = field(repr=False)
    tier: str = "normal"
    scope: str = ""
    created: float = field(default_factory=time.time)
    expires: float = 0.0
    #: When the material itself dates from (epoch seconds), for the date bucket.
    dated: Optional[float] = None
    size: int = 0
    title: str = field(default="", repr=False)

    @property
    def private(self) -> bool:
        return self.tier in PRIVATE_TIERS or self.kind == "vault_item"


_LOCK = threading.RLock()
_STORE: Dict[str, HandleRecord] = {}
#: conversation_id -> {handle: when} a cloud model was shown (for the receipt).
_SEEN: Dict[str, Dict[str, float]] = {}


def _new_body() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(BODY_LEN))


def looks_like_handle(value) -> bool:
    return isinstance(value, str) and bool(HANDLE_RE.match(value))


def _sweep(now: float) -> None:
    for h in [h for h, r in _STORE.items() if r.expires <= now]:
        _STORE.pop(h, None)
    for conv in list(_SEEN):
        _SEEN[conv] = {h: t for h, t in _SEEN[conv].items() if h in _STORE}
        if not _SEEN[conv]:
            _SEEN.pop(conv, None)


def issue(kind: str, ref, conversation_id: str, *, tier: str = "normal",
          scope: str = "", ttl_s: Optional[float] = None, dated: Optional[float] = None,
          size: int = 0, title: str = "") -> str:
    """Mint a handle for ``ref`` in one conversation. Returns the handle string."""
    conv = str(conversation_id or "").strip()
    if not conv:
        raise HandleError("a handle belongs to a conversation")
    if kind not in KINDS:
        raise HandleError("unknown handle kind: %s" % kind)
    if tier not in TIERS:
        raise HandleError("unknown tier: %s" % tier)
    if ref is None or ref == "":
        raise HandleError("a handle needs something to point at")
    ttl = DEFAULT_TTL_S if ttl_s is None else max(1.0, min(float(ttl_s), MAX_TTL_S))
    now = time.time()
    with _LOCK:
        _sweep(now)
        if len(_STORE) >= MAX_HANDLES:
            raise HandleError("too many open handles")
        while True:
            h = PREFIX + _new_body()
            if h not in _STORE:
                break
        _STORE[h] = HandleRecord(
            handle=h, kind=kind, conversation_id=conv, ref=ref, tier=tier,
            scope=str(scope or ""), created=now, expires=now + ttl,
            dated=dated, size=max(0, int(size or 0)), title=str(title or ""))
    return h


def lookup(handle, conversation_id: str) -> Optional[HandleRecord]:
    """The record for ``handle`` if it exists, is unexpired and belongs to this
    conversation; else None. Local callers only: the record holds the local ref."""
    if not looks_like_handle(handle):
        return None
    conv = str(conversation_id or "").strip()
    if not conv:
        return None
    now = time.time()
    with _LOCK:
        rec = _STORE.get(handle)
        if rec is None:
            return None
        if rec.expires <= now:
            _STORE.pop(handle, None)
            return None
        if rec.conversation_id != conv:
            return None
        return rec


def resolve(handle, conversation_id: str):
    """The local reference behind a handle, for local executors only. None when
    the handle is unknown, expired or belongs to another conversation."""
    rec = lookup(handle, conversation_id)
    return None if rec is None else rec.ref


def revoke(handle: str) -> bool:
    with _LOCK:
        return _STORE.pop(handle, None) is not None


def revoke_conversation(conversation_id: str) -> int:
    conv = str(conversation_id or "")
    with _LOCK:
        gone = [h for h, r in _STORE.items() if r.conversation_id == conv]
        for h in gone:
            _STORE.pop(h, None)
        _SEEN.pop(conv, None)
    return len(gone)


def clear() -> None:
    with _LOCK:
        _STORE.clear()
        _SEEN.clear()


# ── what a cloud model may see ───────────────────────────────────────────────

def date_bucket(ts: Optional[float], now: Optional[float] = None) -> str:
    """A coarse date, never a timestamp."""
    if not ts:
        return "undated"
    now = time.time() if now is None else now
    days = (now - float(ts)) / 86400.0
    if days < 1:
        return "today"
    if days < 7:
        return "this week"
    if days < 31:
        return "this month"
    if days < 366:
        return "this year"
    return "older"


def size_class(size: int) -> str:
    n = int(size or 0)
    if n < 1_000:
        return "tiny"
    if n < 10_000:
        return "small"
    if n < 100_000:
        return "medium"
    return "large"


def _title_if_clear(title: str, provider: str) -> str:
    """The title, only if the egress gate lets it through unchanged. Any change,
    any error, any hesitation: no title."""
    t = (title or "").strip()
    if not t or not provider:
        return ""
    try:
        from agent_friday.services import egress_gate
        out = egress_gate.gate_text(t, provider, "work_order_handle_title")
    except Exception:
        _log.debug("handle title check failed; title withheld", exc_info=True)
        return ""
    return t if out == t else ""


def metadata(handle, conversation_id: str, *, provider: str = "",
             include_title: bool = True) -> Optional[dict]:
    """What a cloud model is told about a handle: kind, date bucket, size class,
    and the title only when it passes the egress gate. Never a path or a ref.
    None for a handle this conversation does not hold."""
    rec = lookup(handle, conversation_id)
    if rec is None:
        return None
    out = {"handle": rec.handle, "kind": rec.kind,
           "date": date_bucket(rec.dated), "size": size_class(rec.size)}
    if include_title:
        title = _title_if_clear(rec.title, provider)
        if title:
            out["title"] = title
    if provider:
        with _LOCK:
            _SEEN.setdefault(rec.conversation_id, {})[rec.handle] = time.time()
    return out


def seen_by_cloud(conversation_id: str) -> List[str]:
    """Every handle whose metadata a cloud model was shown in this conversation,
    for the turn receipt."""
    with _LOCK:
        return sorted((_SEEN.get(str(conversation_id or "")) or {}))


def any_private(handles, conversation_id: str) -> bool:
    for h in handles or []:
        rec = lookup(h, conversation_id)
        if rec is not None and rec.private:
            return True
    return False
