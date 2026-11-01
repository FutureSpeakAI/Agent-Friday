"""Which idempotency keys were already carried out (NS-36.3-5).

The action gate refuses an outward action whose request already ran: its
idempotency key (governance/action_gate.ActionEnvelope) names an outward
decision whose action has an executed or completed_verified outcome. This
module answers that one question from the action ledger.

Bounded and cached: only the last WINDOW_S seconds are read, and the index is
kept in memory and brought up to date incrementally. It is warmed once at boot
in a background thread (`warm_in_background`). After that a lookup reads only
the bytes appended to the live ledger file since the last one (a remembered
offset, so trailing unsigned lines are read once, not on every call). A full
read from the newest end, stopping at the last seq seen, happens only when the
live file was rotated or cut; it is capped (MAX_SCAN_LINES, MAX_SCAN_BYTES),
and what lies beyond the cap is unknown, which refuses nothing.

A different ledger directory, a ledger replaced in place (its first line is
another one), or a ledger whose newest seq went back rebuilds it.

Only signed lines count (an unsigned or changed line never refuses anything).
A lookup that cannot run returns None: it refuses nothing, as before.
Read-only; ids and keys only.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

#: How far back an executed request still refuses a repeat.
WINDOW_S = 30 * 86400.0
#: How often entries older than the window are dropped from the index.
PRUNE_EVERY_S = 600.0
#: A full read from the newest end stops after this many lines or bytes;
#: anything older is unknown (fail-open: it refuses nothing).
MAX_SCAN_LINES = 200_000
MAX_SCAN_BYTES = 64 * 1024 * 1024
#: How long a lookup waits for the index (a boot warm-up still running)
#: before it answers "unknown", which refuses nothing.
LOOKUP_WAIT_S = 5.0

_DONE = ("executed", "completed_verified")
_LOCK = threading.Lock()
_log = logging.getLogger("friday.idempotency_index")


def _fresh() -> dict:
    return {"dir": None, "ident": None, "seq": 0, "built_at": 0.0, "pruned_at": 0.0,
            # the live file as last read: its first line's id and the byte
            # offset just past the last whole line read
            "live_head": None, "live_off": 0,
            # a full read stopped at the cap: older entries are unknown
            "truncated": False,
            # idempotency key -> (action id that carried it out, t)
            "done": {},
            # action id -> (idempotency key, t), outward decisions with a key
            "decisions": {},
            # decision seq -> action id (an outcome may name only its decision's seq)
            "by_seq": {}}


_STATE: dict = _fresh()


def reset() -> None:
    """Forget the index (tests)."""
    global _STATE
    with _LOCK:
        _STATE = _fresh()


def _word(rec: dict) -> str:
    w = rec.get("outcome")
    if isinstance(w, str) and w:
        return w
    return {"ok": "executed"}.get(str(rec.get("status") or ""), "")


def _first_line_id(p: Optional[Path]) -> Optional[str]:
    """An id for a file's first line (which ledger this is), or None."""
    if p is None:
        return None
    try:
        with open(p, "rb") as f:
            first = f.readline(65536)
    except OSError:
        return None
    first = first.strip()
    return hashlib.sha256(first).hexdigest()[:16] if first else None


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _whole_lines_end(p: Path, size: int) -> int:
    """The offset just past the last newline at or before `size` (0 if none)."""
    if size <= 0:
        return 0
    try:
        with open(p, "rb") as f:
            start = max(0, size - 65536)
            f.seek(start)
            buf = f.read(size - start)
    except OSError:
        return 0
    i = buf.rfind(b"\n")
    return start + i + 1 if i >= 0 else 0


def _new_lines(L, key, since_seq: int, cutoff: float) -> Tuple[list, int, bool]:
    """(signed decision/outcome entries after `since_seq` and not older than
    `cutoff`, oldest first; the newest signed seq on disk; whether the cap
    stopped the read). Reads from the end and stops at `since_seq`. Raises
    when the ledger cannot be read."""
    rows: list = []
    tip = 0
    lines = nbytes = 0
    capped = False
    for p in reversed(L._reader_segments()):
        stop = False
        for raw in L._reverse_lines(p):
            lines += 1
            nbytes += len(raw) + 1
            if lines > MAX_SCAN_LINES or nbytes > MAX_SCAN_BYTES:
                capped = stop = True
                break
            e = L._safe_json(raw)
            if not isinstance(e, dict) or not isinstance(e.get("seq"), int):
                continue
            if not L.entry_ok(e, key):
                continue
            tip = max(tip, e["seq"])
            if e["seq"] <= since_seq:
                stop = True
                break
            t = e.get("t")
            if isinstance(t, (int, float)) and t < cutoff:
                stop = True
                break
            if e.get("kind") in ("decision", "outcome"):
                rows.append(e)
        if stop:
            break
    rows.reverse()
    return rows, tip, capped


def _tail_rows(L, key, live: Path, off: int, since_seq: int) -> Tuple[list, int, int]:
    """(signed decision/outcome entries in the live file after byte `off`,
    oldest first; the newest signed seq among them; the offset past the last
    whole line read). A line still being written is left for the next call."""
    rows: list = []
    tip = 0
    with open(live, "rb") as f:
        f.seek(off)
        data = f.read()
    end = data.rfind(b"\n") + 1
    for raw in data[:end].split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        e = L._safe_json(raw)
        if not isinstance(e, dict) or not isinstance(e.get("seq"), int) or not L.entry_ok(e, key):
            continue
        tip = max(tip, e["seq"])
        if e["seq"] > since_seq and e.get("kind") in ("decision", "outcome"):
            rows.append(e)
    return rows, tip, off + end


def _take(st: dict, e: dict) -> None:
    rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
    t = e.get("t") if isinstance(e.get("t"), (int, float)) else time.time()
    if e.get("kind") == "decision":
        # a refused decision carried nothing out; any other may have (a
        # confirmed chat question's call closes the confirm decision)
        if rec.get("class") != "outward" or rec.get("decision") == "deny":
            return
        # a key scoped by a session or a schedule, or a tool call that is not
        # a one-shot send or write, refuses nothing (the gate's rule, shared
        # with the outward audit's duplicate_key)
        from agent_friday.governance import action_gate as G
        if not G.recorded_key_names_one_request(rec):
            return
        env = rec.get("envelope") if isinstance(rec.get("envelope"), dict) else {}
        k = env.get("idempotency_key")
        aid = rec.get("action_id") or env.get("action_id")
        if k and aid:
            st["decisions"][str(aid)] = (str(k), t)
            st["by_seq"][e.get("seq")] = (str(aid), t)
        return
    if _word(rec) not in _DONE:
        return
    aid = rec.get("action_id")
    if not aid or rec.get("action_id_origin") == "outcome":
        aid = (st["by_seq"].get(rec.get("decision_seq")) or (None,))[0]
    hit = st["decisions"].get(str(aid)) if aid else None
    if hit and hit[0] not in st["done"]:
        st["done"][hit[0]] = (str(aid), t)


def _prune(st: dict, cutoff: float) -> None:
    for name in ("done", "decisions", "by_seq"):
        d = st[name]
        for k in [k for k, v in d.items() if v[1] < cutoff]:
            del d[k]


def _refresh(now: float) -> dict:
    from agent_friday.services import action_ledger as L
    global _STATE
    cutoff = now - WINDOW_S
    where = str(L.ledger_dir())
    segs = L._reader_segments()
    ident = _first_line_id(segs[0]) if segs else None
    live = L.live_path()
    live_head = _first_line_id(live)
    st = _STATE
    if st["dir"] != where or st["ident"] != ident:
        # another ledger, or this one replaced in place: read it again
        st = _fresh()
        st["dir"], st["ident"] = where, ident
    key = L._key()
    size = _size(live)
    if (st["built_at"] and live_head is not None and live_head == st["live_head"]
            and size >= st["live_off"]):
        # the same live file, grown or not: only the bytes appended since
        rows, tip, st["live_off"] = _tail_rows(L, key, live, st["live_off"], st["seq"])
    else:
        # first read, or the live file was rotated or cut: from the newest
        # end down to the last seq seen (capped)
        end = _whole_lines_end(live, size)
        rows, tip, capped = _new_lines(L, key, st["seq"], cutoff)
        if tip < st["seq"]:
            # the ledger went back (cut, rolled back or replaced): read it again
            st = _fresh()
            st["dir"], st["ident"] = where, ident
            rows, tip, capped = _new_lines(L, key, 0, cutoff)
        st["truncated"] = st["truncated"] or capped
        # lines appended between `end` and the read above are taken again
        # next time; taking an entry twice changes nothing
        st["live_head"], st["live_off"] = live_head, end
        st["built_at"] = now
    for e in rows:
        _take(st, e)
    st["seq"] = max(st["seq"], tip)
    if now - st["pruned_at"] > PRUNE_EVERY_S:
        _prune(st, cutoff)
        st["pruned_at"] = now
    _STATE = st
    return st


def done_action(idem_key: Optional[str], *, now: Optional[float] = None) -> Optional[str]:
    """The action id that already carried out `idem_key` (an outward decision
    with this key whose action ended executed or completed_verified, in the
    last WINDOW_S seconds), else None. None as well when the ledger cannot be
    read or the index is busy for longer than LOOKUP_WAIT_S. Never raises."""
    if not idem_key:
        return None
    now = time.time() if now is None else float(now)
    if not _LOCK.acquire(timeout=LOOKUP_WAIT_S):
        _log.warning("idempotency index busy: this lookup refuses nothing")
        return None
    try:
        st = _refresh(now)
        hit = st["done"].get(str(idem_key))
    except Exception:  # noqa: BLE001 - a lookup that cannot run refuses nothing
        return None
    finally:
        _LOCK.release()
    if not hit or hit[1] < now - WINDOW_S:
        return None
    return hit[0]


def warm() -> None:
    """Build the index now (the boot warm-up). Never raises."""
    try:
        with _LOCK:
            _refresh(time.time())
    except Exception as e:  # noqa: BLE001 - the first lookup builds it instead
        _log.warning("idempotency index not warmed: %s", type(e).__name__)


def warm_in_background() -> None:
    """Warm the index once, in a daemon thread, so the first outward action
    after boot does not pay for reading the ledger. Never raises."""
    try:
        threading.Thread(target=warm, daemon=True, name="idempotency-index-warm").start()
    except Exception as e:  # noqa: BLE001
        _log.warning("idempotency index warm-up not started: %s", type(e).__name__)


def stats() -> Dict[str, int]:
    """Sizes of the index (for the Doctor and tests). Counts only."""
    with _LOCK:
        return {"keys_done": len(_STATE["done"]), "decisions": len(_STATE["decisions"]),
                "seq": int(_STATE["seq"] or 0), "truncated": bool(_STATE["truncated"])}
