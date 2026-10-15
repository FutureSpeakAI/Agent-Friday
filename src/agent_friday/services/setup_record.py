"""The setup record: every choice made in the first-run conversation, in order.

One line per decision in ``<friday home>/setup/ledger.jsonl``. Each line says
which step it was, what kind of decision it was and who made it:

* ``you``          -- the person chose it ("you chose");
* ``recommended``  -- the person pressed "Use recommended for the rest" and the
                      recommended option was applied ("recommended, accepted by
                      you");
* ``default``      -- the step was skipped, so the safe default stands
                      ("default because skipped").

The record never holds a personal value. A name, a city, a never-send item or
an address is recorded as a label or a count ("name given", "3 items"), so the
file can be shown, copied into a support request or kept in a backup without
carrying the answers themselves; the answers live where their owners read them
(the encrypted setup profile, settings, the privacy shield).

The lines are hash-chained: each line carries the SHA-256 of the line before
it, and its own hash over its content. ``verify()`` walks the chain and names
the first line that does not fit, so an edited, deleted or reordered line is
found rather than trusted. A broken chain is reported, never repaired: a
tamper-evident record that rewrites itself is not one.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from agent_friday.paths import friday_home

_LOCK = threading.RLock()

#: Who made a decision, and how the receipt says it.
HOW_WORDS = {
    "you": "you chose",
    "recommended": "recommended, accepted by you",
    "default": "default because skipped",
}

GENESIS = "0" * 64


def ledger_path() -> Path:
    return friday_home() / "setup" / "ledger.jsonl"


def _canon(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _hash(prev: str, body: dict) -> str:
    return hashlib.sha256(prev.encode("ascii") + b"\n" + _canon(body)).hexdigest()


def _clean(value, limit: int = 160):
    """Labels and counts only: short strings, numbers, booleans, small lists."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_clean(v, 60) for v in list(value)[:20]]
    return str(value)[:limit]


def _lines() -> list:
    try:
        raw = ledger_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            rec = {"_unreadable": line[:80]}
        out.append(rec)
    return out


def append(step: str, decision: str, how: str, **detail) -> dict:
    """Record one decision. `detail` holds labels and counts, never values.

    Returns the stored line. `how` must be one of HOW_WORDS.
    """
    if how not in HOW_WORDS:
        raise ValueError("unknown decision source %r" % (how,))
    with _LOCK:
        lines = _lines()
        prev = lines[-1].get("hash", "") if lines else GENESIS
        body = {"seq": len(lines) + 1, "ts": round(time.time(), 3),
                "step": str(step)[:40], "decision": _clean(decision, 120),
                "how": how, "detail": {str(k)[:40]: _clean(v) for k, v in detail.items()},
                "prev": prev}
        rec = dict(body, hash=_hash(prev, body))
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, sort_keys=True, ensure_ascii=True) + "\n")
            f.flush()
        return rec


def entries() -> list:
    """Every recorded decision, oldest first, with `how_words` for display."""
    return [dict(r, how_words=HOW_WORDS.get(r.get("how"), "")) for r in _lines()
            if "_unreadable" not in r]


def latest_by_step() -> dict:
    """The newest decision for each step: what the receipt shows."""
    out = {}
    for r in entries():
        out[r.get("step")] = r
    return out


def verify() -> dict:
    """{ok, count, broken_at, reason}: walk the chain from the first line.

    `broken_at` is the 1-based line number of the first line whose hash, link
    or sequence does not fit; None when the whole chain verifies.
    """
    lines = _lines()
    prev = GENESIS
    for i, rec in enumerate(lines, start=1):
        if "_unreadable" in rec:
            return {"ok": False, "count": len(lines), "broken_at": i,
                    "reason": "line %d is not readable" % i}
        body = {k: v for k, v in rec.items() if k != "hash"}
        if rec.get("prev") != prev:
            return {"ok": False, "count": len(lines), "broken_at": i,
                    "reason": "line %d does not follow the line before it" % i}
        if rec.get("seq") != i:
            return {"ok": False, "count": len(lines), "broken_at": i,
                    "reason": "line %d is out of order" % i}
        if _hash(prev, body) != rec.get("hash"):
            return {"ok": False, "count": len(lines), "broken_at": i,
                    "reason": "line %d was changed after it was written" % i}
        prev = rec["hash"]
    return {"ok": True, "count": len(lines), "broken_at": None, "reason": ""}
