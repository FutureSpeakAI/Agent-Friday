"""The phone's on-disk state: the inbound spool, replay keys, and the ledger.

One SQLite file, `<friday home>/phone/phone.db`, in WAL mode so the ingress
thread and the rest of Friday can use it at once.

  events   every validated webhook, once, keyed by (kind, Twilio SID). The
           ingress only writes here; Friday's phone service reads and marks
           rows handled. A SID seen twice is stored once, which is what makes a
           Twilio retry harmless.
  replay   hashes of signed requests already accepted. Twilio's signature has
           no timestamp, so a captured request stays valid forever; this table
           is what makes replaying one a no-op.
  ledger   the call and text log the owner reads in Settings: direction, the
           other party, status, cost. Message bodies are NOT kept here; the
           events table holds the inbound text until it is handled, and the
           conversation it lands in is where it lives after that.

Nothing here imports `agent_friday.core`.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from agent_friday.paths import friday_home

_LOCK = threading.RLock()

#: Replay keys are kept this long. Twilio does not retry a webhook for longer
#: than a day; a request older than this that replays is still rejected by the
#: SID uniqueness in `events`.
REPLAY_TTL_S = 30 * 86400


def db_path() -> Path:
    return friday_home() / "phone" / "phone.db"


def _connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10, isolation_level=None,
                           check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            sid TEXT NOT NULL,
            received_at REAL NOT NULL,
            data TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            handled_at REAL,
            note TEXT,
            UNIQUE(kind, sid)
        );
        CREATE TABLE IF NOT EXISTS replay (
            key TEXT PRIMARY KEY,
            seen_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at REAL NOT NULL,
            channel TEXT NOT NULL,
            direction TEXT NOT NULL,
            party TEXT,
            sid TEXT,
            status TEXT,
            detail TEXT,
            price_usd REAL,
            units INTEGER
        );
        CREATE INDEX IF NOT EXISTS ledger_sid ON ledger(sid);
    """)
    return conn


# ── replay ───────────────────────────────────────────────────────────────────

def seen_before(key: str, now: Optional[float] = None) -> bool:
    """Record `key`; True if it was already recorded (a replay or a retry).

    Raises on a database error so the ingress can fail closed: "could not
    check" must never read as "not seen".
    """
    now = time.time() if now is None else now
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM replay WHERE seen_at < ?", (now - REPLAY_TTL_S,))
            try:
                conn.execute("INSERT INTO replay(key, seen_at) VALUES (?, ?)", (key, now))
                return False
            except sqlite3.IntegrityError:
                return True
        finally:
            conn.close()


# ── events ───────────────────────────────────────────────────────────────────

def put_event(kind: str, sid: str, data: dict, now: Optional[float] = None) -> bool:
    """Store a validated event once. True if new, False if this SID was already
    stored. Raises on a database error (the ingress fails closed)."""
    now = time.time() if now is None else now
    with _LOCK:
        conn = _connect()
        try:
            try:
                conn.execute(
                    "INSERT INTO events(kind, sid, received_at, data) VALUES (?,?,?,?)",
                    (kind, sid, now, json.dumps(data, ensure_ascii=False)))
                return True
            except sqlite3.IntegrityError:
                return False
        finally:
            conn.close()


def pending_events(limit: int = 50) -> list:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT id, kind, sid, received_at, data FROM events "
                "WHERE status='new' ORDER BY id LIMIT ?", (int(limit),)).fetchall()
        finally:
            conn.close()
    return [{"id": r[0], "kind": r[1], "sid": r[2], "received_at": r[3],
             "data": json.loads(r[4])} for r in rows]


def mark_event(event_id: int, status: str, note: str = "",
               scrub: bool = True) -> None:
    """Mark an event handled. `scrub` drops its payload, so an inbound text
    body does not outlive its handling in this file."""
    with _LOCK:
        conn = _connect()
        try:
            if scrub:
                conn.execute("UPDATE events SET status=?, handled_at=?, note=?, data='{}' "
                             "WHERE id=?", (status, time.time(), note[:500], event_id))
            else:
                conn.execute("UPDATE events SET status=?, handled_at=?, note=? WHERE id=?",
                             (status, time.time(), note[:500], event_id))
        finally:
            conn.close()


def count_recent(kind: str, since: float) -> int:
    with _LOCK:
        conn = _connect()
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind=? AND received_at>=?",
                (kind, since)).fetchone()[0])
        finally:
            conn.close()


# ── ledger ───────────────────────────────────────────────────────────────────

def ledger_add(*, channel: str, direction: str, party: str = "", sid: str = "",
               status: str = "", detail: str = "", price_usd: Optional[float] = None,
               units: Optional[int] = None, now: Optional[float] = None) -> int:
    """One row of the call/text log. `detail` is a short human description,
    never a message body."""
    now = time.time() if now is None else now
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO ledger(at, channel, direction, party, sid, status, detail, "
                "price_usd, units) VALUES (?,?,?,?,?,?,?,?,?)",
                (now, channel, direction, party, sid, status, detail[:300],
                 price_usd, units))
            return int(cur.lastrowid)
        finally:
            conn.close()


def ledger_update_by_sid(sid: str, **fields: Any) -> int:
    allowed = {"status", "detail", "price_usd", "units"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sid or not sets:
        return 0
    cols = ", ".join("%s=?" % k for k in sets)
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("UPDATE ledger SET %s WHERE sid=?" % cols,
                               (*sets.values(), sid))
            return cur.rowcount
        finally:
            conn.close()


def ledger_rows(limit: int = 100) -> list:
    try:
        with _LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT id, at, channel, direction, party, sid, status, detail, "
                    "price_usd, units FROM ledger ORDER BY id DESC LIMIT ?",
                    (int(limit),)).fetchall()
            finally:
                conn.close()
    except Exception:
        return []
    keys = ("id", "at", "channel", "direction", "party", "sid", "status",
            "detail", "price_usd", "units")
    return [dict(zip(keys, r)) for r in rows]


def ledger_totals() -> dict:
    """Costs to date, by channel. `unpriced` counts rows Twilio has not priced
    yet (it prices a message or call after the fact)."""
    out = {"total_usd": 0.0, "by_channel": {}, "unpriced": 0}
    try:
        with _LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT channel, COUNT(*), SUM(COALESCE(price_usd,0)), "
                    "SUM(CASE WHEN price_usd IS NULL THEN 1 ELSE 0 END), "
                    "SUM(COALESCE(units,0)) FROM ledger GROUP BY channel").fetchall()
            finally:
                conn.close()
    except Exception:
        return out
    for ch, n, usd, unpriced, units in rows:
        out["by_channel"][ch] = {"count": n, "usd": round(usd or 0.0, 4),
                                 "unpriced": unpriced or 0, "units": units or 0}
        out["total_usd"] += usd or 0.0
        out["unpriced"] += unpriced or 0
    out["total_usd"] = round(out["total_usd"], 4)
    return out
