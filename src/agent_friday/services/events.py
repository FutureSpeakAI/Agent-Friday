"""services/events.py -- the canonical event envelope, persisted before notification (BD-16).

North Star 31.6-1: one envelope for every event -- id, type, version, principal scope, related
ids, payload, source, time, integrity. 31.7-1: a critical event is persisted before anyone is
notified of it. Until now each subsystem wrote its own shape (task_journal lines, desktop_bus
commands, notification cards), so "what happened, in order, and was it recorded before the
owner was told?" had no single answer.

    ev = events.make("notification.needs_you", source="notifications_engine",
                     payload={...}, related_ids={"notification": nid})
    events.persist_then_notify(ev, notify)     # notify() runs only after the line is on disk

* The log is one append-only JSON-lines file under <Friday home>/events/, fsynced per critical
  event. A torn last line (a crash mid-write) is skipped on replay; a line whose integrity
  does not verify is reported, never used.
* Integrity is an HMAC over the envelope's canonical JSON with the governance key; with no key
  the envelope says "unsigned" and replay treats it as unverified.
* Payloads carry ids, kinds and counts, not the owner's words (A20: content stays sealed where
  it lives; the event log points at it).
* Off-the-record work writes no event.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

VERSION = 1
MAX_BYTES = 20 * 1024 * 1024          # rolled to events.1.jsonl past this
_LOCK = threading.Lock()
_log = logging.getLogger("friday.events")


class EventNotPersisted(RuntimeError):
    """The event could not be written; whoever would have been notified is not."""


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    version: int
    principal_scope: str
    related_ids: Dict[str, str]
    payload: Dict[str, Any]
    source: str
    created_at: float
    integrity: str = ""

    def body(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("integrity", None)
        return d


def _dir() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "events"


def log_path() -> Path:
    return _dir() / "events.jsonl"


def _canon(d: Dict[str, Any]) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False).encode("utf-8")


def _key() -> Optional[bytes]:
    try:
        from agent_friday.governance.action_gate import _governance_key
        return _governance_key()
    except Exception:
        return None


def _sign(body: Dict[str, Any]) -> str:
    k = _key()
    if not k:
        return "unsigned"
    return "hmac-sha256:" + _hmac.new(k, _canon(body), hashlib.sha256).hexdigest()


def make(type: str, *, source: str, payload: Optional[Dict[str, Any]] = None,
         related_ids: Optional[Dict[str, Any]] = None, principal_scope: str = "owner") -> Event:
    """A new envelope (signed). `type` is dotted ("notification.needs_you", "task.halt")."""
    if not type or not isinstance(type, str):
        raise ValueError("an event needs a type")
    body = {"id": str(uuid.uuid4()), "type": type[:80], "version": VERSION,
            "principal_scope": str(principal_scope or "owner")[:60],
            "related_ids": {str(k)[:40]: str(v)[:120] for k, v in (related_ids or {}).items()
                            if v is not None},
            "payload": dict(payload or {}), "source": str(source or "")[:60],
            "created_at": round(time.time(), 6)}
    return Event(integrity=_sign(body), **body)


def verified(ev: Dict[str, Any]) -> bool:
    integ = str(ev.get("integrity") or "")
    if not integ.startswith("hmac-sha256:"):
        return False
    body = {k: v for k, v in ev.items() if k != "integrity"}
    try:
        return _hmac.compare_digest(integ, _sign(body))
    except Exception:
        return False


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.skip("events"))
    except Exception:
        return False


def persist(ev: Event, *, durable: bool = True) -> None:
    """Append one envelope to the log. Raises EventNotPersisted if it cannot be written."""
    line = json.dumps(asdict(ev), sort_keys=True, default=str, ensure_ascii=False) + "\n"
    try:
        with _LOCK:
            d = _dir()
            d.mkdir(parents=True, exist_ok=True)
            p = log_path()
            if p.exists() and p.stat().st_size > MAX_BYTES:
                os.replace(p, d / "events.1.jsonl")
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                if durable:
                    os.fsync(f.fileno())
    except OSError as e:
        raise EventNotPersisted("the event log could not be written (%s)" % type(e).__name__) from e


def persist_then_notify(ev: Event, notify: Optional[Callable[[Event], Any]] = None, *,
                        durable: bool = True) -> Any:
    """NS-31.7-1: the event is on disk before anyone is told. If it cannot be written,
    EventNotPersisted is raised and `notify` is not called."""
    persist(ev, durable=durable)
    return notify(ev) if notify else None


def replay(*, types: Optional[List[str]] = None, include_unverified: bool = False) -> Iterator[Dict[str, Any]]:
    """Envelopes in order. A torn or unreadable line is skipped; a line that does not verify is
    skipped unless `include_unverified` (then it carries "_verified": False)."""
    for p in (_dir() / "events.1.jsonl", log_path()):
        if not p.exists():
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue                          # a torn line (a crash mid-write)
                if not isinstance(ev, dict) or (types and ev.get("type") not in types):
                    continue
                ok = verified(ev)
                if ok or include_unverified:
                    if not ok:
                        ev = dict(ev, _verified=False)
                    yield ev


def record_critical(type: str, *, source: str, payload: Dict[str, Any],
                    related_ids: Optional[Dict[str, Any]] = None) -> Optional[Event]:
    """Persist a critical event for a caller that must tell the owner either way (a safety
    notice is shown even if its record fails). Returns the event, or None when nothing was
    written (off the record, or the write failed: logged)."""
    if _off_record():
        return None
    try:
        ev = make(type, source=source, payload=payload, related_ids=related_ids)
        persist(ev)
        return ev
    except Exception as e:  # noqa: BLE001
        _log.error("event %s not recorded: %s", type, e)
        return None
