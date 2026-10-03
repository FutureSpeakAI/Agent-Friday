"""The evidence log: a git log for trust.

Every change to a trust score is an event in an append-only JSONL log with
a SHA-256 hash chain. An event records what moved (`effect`: dimension,
before, after, delta), why (`kind`, `detail`, `provenance`), who (`origin`:
system, owner or peer) and what it answers (`because`: earlier event ids).
Nothing is rewritten: a mistaken event is answered by an `owner_correction`
that names it. A snapshot that disagrees with the log is the thing that is
wrong.

Two files under `<friday_home>/trust/log/`: `sources-YYYY-MM.jsonl` for
outlets and Friday herself (outlet names and article hashes, nothing
personal) and `people.jsonl` for persons. Forgetting a person removes their
events and re-anchors the chain with a `forget_marker` that carries only a
count.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

GENESIS = "0" * 64
KINDS = ("observation", "owner_statement", "owner_rule", "owner_correction", "proposal",
         "proposal_decision", "reevaluation", "merge", "forget_marker", "person_added")
ORIGINS = ("system", "owner", "peer")
MAX_DETAIL = 160

_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.RLock()
        return _LOCKS[key]


def log_dir() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "trust" / "log"


def sources_path(now: Optional[datetime] = None) -> Path:
    now = now or datetime.now()
    return log_dir() / f"sources-{now:%Y-%m}.jsonl"


def people_path() -> Path:
    return log_dir() / "people.jsonl"


def _canon(core: Dict[str, Any]) -> bytes:
    return json.dumps(core, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _hash(core: Dict[str, Any]) -> str:
    return hashlib.sha256(_canon(core)).hexdigest()


def _read_lines(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            out.append({"_corrupt": ln[:80]})
    return out


def _tip(path: Path) -> str:
    rows = _read_lines(path)
    for r in reversed(rows):
        if isinstance(r, dict) and r.get("hash"):
            return r["hash"]
    return GENESIS


def append(path: Path, *, entity_id: str, kind: str, origin: str = "system",
           dimension: Optional[str] = None, topic: Optional[str] = None,
           signal: Optional[float] = None, provenance: Optional[Dict[str, Any]] = None,
           detail: str = "", effect: Optional[List[Dict[str, Any]]] = None,
           because: Optional[Iterable[str]] = None, entity_kind: str = "source") -> Dict[str, Any]:
    """Append one event and return it (with its id and hash)."""
    if kind not in KINDS:
        raise ValueError(f"unknown event kind {kind!r}")
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin {origin!r}")
    with _lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        core = {
            "event_id": uuid.uuid4().hex,
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "entity_id": str(entity_id),
            "entity_kind": entity_kind,
            "kind": kind,
            "dimension": dimension,
            "topic": topic,
            "signal": None if signal is None else round(float(signal), 4),
            "origin": origin,
            "provenance": dict(provenance or {}),
            "detail": str(detail or "")[:MAX_DETAIL],
            "effect": [dict(e) for e in (effect or [])],
            "because": [str(b) for b in (because or [])],
            "prev_hash": _tip(path),
        }
        core["hash"] = _hash(core)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(core, ensure_ascii=False, default=str) + "\n")
        return core


def read(path: Path, *, entity_id: Optional[str] = None,
         dimension: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = [r for r in _read_lines(path) if isinstance(r, dict) and "event_id" in r]
    if entity_id is not None:
        rows = [r for r in rows if r.get("entity_id") == str(entity_id)]
    if dimension is not None:
        rows = [r for r in rows if r.get("dimension") == dimension
                or any(e.get("dimension") == dimension for e in r.get("effect") or [])]
    return rows


def find(path: Path, event_id: str) -> Optional[Dict[str, Any]]:
    for r in _read_lines(path):
        if isinstance(r, dict) and r.get("event_id") == event_id:
            return r
    return None


def verify_chain(path: Path) -> Dict[str, Any]:
    """Recompute every hash and link. {"valid", "records", "break_at", "reason"}."""
    rows = _read_lines(path)
    prev = GENESIS
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or "_corrupt" in r:
            return {"valid": False, "records": i, "break_at": i, "reason": "unreadable line"}
        core = {k: v for k, v in r.items() if k != "hash"}
        if r.get("prev_hash") != prev:
            return {"valid": False, "records": i, "break_at": i, "reason": "broken link"}
        if _hash(core) != r.get("hash"):
            return {"valid": False, "records": i, "break_at": i, "reason": "hash mismatch"}
        prev = r["hash"]
    return {"valid": True, "records": len(rows), "break_at": None, "reason": None}


def replay_scores(path: Path, entity_id: str) -> Dict[str, float]:
    """The score each dimension should hold now, from the log alone: the
    `after` of the last effect that moved it."""
    out: Dict[str, float] = {}
    for r in read(path, entity_id=entity_id):
        for e in r.get("effect") or []:
            if e.get("dimension") and e.get("after") is not None:
                out[e["dimension"]] = float(e["after"])
    return out


def effect_between(before: Dict[str, Any], after: Dict[str, Any],
                   dimensions: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """The per-dimension movement between two score dicts (unchanged ones omitted)."""
    dims = list(dimensions) if dimensions else sorted(set(before) | set(after))
    out = []
    for d in dims:
        b, a = before.get(d), after.get(d)
        if not isinstance(b, (int, float)) and not isinstance(a, (int, float)):
            continue
        b = float(b) if isinstance(b, (int, float)) else None
        a = float(a) if isinstance(a, (int, float)) else None
        if b == a:
            continue
        out.append({"dimension": d, "before": b, "after": a,
                    "delta": (None if b is None or a is None else round(a - b, 4))})
    return out


def forget_entity(path: Path, *, match) -> int:
    """Remove every event for which ``match(event)`` is true and re-anchor
    the chain with a forget_marker that carries only a count. Returns the
    number of events removed."""
    with _lock_for(path):
        rows = [r for r in _read_lines(path) if isinstance(r, dict) and "event_id" in r]
        keep = [r for r in rows if not match(r)]
        removed = len(rows) - len(keep)
        if removed == 0:
            return 0
        # Re-chain what is kept, then the marker.
        prev = GENESIS
        rebuilt = []
        for r in keep:
            core = {k: v for k, v in r.items() if k != "hash"}
            core["prev_hash"] = prev
            core["hash"] = _hash(core)
            prev = core["hash"]
            rebuilt.append(core)
        marker = {
            "event_id": uuid.uuid4().hex,
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "entity_id": "", "entity_kind": "person", "kind": "forget_marker",
            "dimension": None, "topic": None, "signal": None, "origin": "owner",
            "provenance": {}, "detail": f"{removed} event(s) removed at the owner's request",
            "effect": [], "because": [], "prev_hash": prev,
        }
        marker["hash"] = _hash(marker)
        rebuilt.append(marker)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rebuilt),
                       encoding="utf-8")
        os.replace(tmp, path)
        return removed
