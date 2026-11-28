"""The governance receipt store: what Friday did, which model served it and where it ran, in plain words.

A source row is parsed into one typed `Receipt` where it enters (`from_activity`) or refused (None). The store is an
index, not the record: the signed action ledger stays the record, and every receipt here names its origin and its
place there (`receipt_id` is `<origin>:<source>:<id>`), so the store can be rebuilt from the ledger at any time.
Each receipt is kept once: `put` is insert-or-ignore on `receipt_id`, so a re-sync or a retried batch stores nothing.

Rules (tests/unit/test_governance_receipts.py):
* the plain line is built from the fixed words in `_LINES` plus a model or tool name that is name-shaped; no other
  field of a row ever reaches it (test_no_free_text_from_any_activity_field_ever_reaches_the_line);
* where a model ran is said only when the provider was recorded, never guessed
  (test_where_a_model_ran_is_never_guessed_when_the_provider_was_not_recorded);
* every activity kind has a line, so a new kind without one fails that same test.

Every receipt read here is the owner's (`audience` "owner"); the governance view is never served to a device
(tests/unit/test_governance_view.py: test_the_governance_view_is_never_served_to_a_device).
"""
from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import threading
import time
from dataclasses import astuple, dataclass, fields
from pathlib import Path
from typing import Iterable, Optional

_log = logging.getLogger(__name__)

ORIGIN_PC = "pc"
ON_THIS_PC, CLOUD = "on_this_pc", "cloud"
WHERE_WORDS = {ON_THIS_PC: "on this PC", CLOUD: "in the cloud", None: "not recorded"}
FAMILIES = ("model", "tool", "trace", "task", "notice", "privacy", "other")
OUTCOMES = ("done", "failed")
AUDIENCE_OWNER = "owner"

READ_MAX = 200
SYNC_ROWS = 500
SYNC_MIN_S = 15.0

#: None means the default under the Friday folder; tests point it at a temporary file.
DB_PATH: Optional[Path] = None

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,79}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,79}")

#: activity kind -> (family, line with {model}/{tool} only). The only words a line is made of.
_LINES = {
    "model_invocation": ("model", "{model} answered"),
    "reasoning_trace": ("trace", "{model} kept a reasoning trace"),
    "tool_call": ("tool", "Called {tool}"),
    "subagent_spawn": ("task", "Started a helper task"),
    "journal_read": ("privacy", "A task's record was shown to another device"),
    "meeting_deletion": ("privacy", "Deleted meeting recordings for good"),
    "notification": ("notice", "Sent you a notification"),
    "notice": ("notice", "Showed you a notice"),
    "quiet_on": ("notice", "Quiet was turned on"),
    "disk_full": ("other", "Paused background work while the disk was full"),
    "first_action_skipped": ("other", "You skipped the first action that needed you"),
}


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    origin: str
    at: float
    family: str
    kind: str
    line: str
    model: Optional[str] = None
    where: Optional[str] = None
    outcome: Optional[str] = None
    trace_id: Optional[str] = None
    task_id: Optional[str] = None
    turn_id: Optional[str] = None
    audience: str = AUDIENCE_OWNER


_COLUMNS = tuple(f.name for f in fields(Receipt))


def _name(v) -> Optional[str]:
    return v if isinstance(v, str) and _NAME.fullmatch(v) else None


def _id(v) -> Optional[str]:
    return v if isinstance(v, str) and _ID.fullmatch(v) else None


def _is_local(provider: str, model: str) -> bool:
    from agent_friday.services import seat_policy
    return seat_policy.is_local_seat(provider, model)


def _where(row: dict, model: Optional[str]) -> Optional[str]:
    provider = row.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        return None
    try:
        return ON_THIS_PC if _is_local(provider, model or "") else CLOUD
    except Exception:
        return None


def from_activity(row) -> Optional[Receipt]:
    """One activity-ledger row (activity_ledger.read) as a Receipt, or None when it is not one."""
    if not isinstance(row, dict) or row.get("kind") not in _LINES:
        return None
    seq, at = row.get("seq"), row.get("ts")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        return None
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return None
    kind = row["kind"]
    family, line = _LINES[kind]
    model = _name(row.get("model"))
    tool = _name(row.get("tool"))
    where = _where(row, model) if kind == "model_invocation" else None
    outcome = None
    line = line.format(model=model or "A model", tool=tool or "a tool")
    if kind == "model_invocation":
        line += " (where it ran was not recorded)" if where is None else " " + WHERE_WORDS[where]
    elif kind == "tool_call" and isinstance(row.get("ok"), bool):
        outcome = "done" if row["ok"] else "failed"
        line = ("Used %s" if row["ok"] else "Tried %s; it failed") % (tool or "a tool")
    return Receipt(receipt_id="%s:activity:%d" % (ORIGIN_PC, seq), origin=ORIGIN_PC, at=float(at), family=family,
                   kind=kind, line=line, model=model, where=where, outcome=outcome,
                   trace_id=_id(row.get("trace_id")), task_id=_id(row.get("task_id")), turn_id=_id(row.get("turn_id")))


# ── the store ─────────────────────────────────────────────────────────────────────────────────────────────────────

_LOCK = threading.Lock()
_last_sync = 0.0


def _path() -> Path:
    if DB_PATH is not None:
        return Path(DB_PATH)
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "governance" / "receipts.db"


@contextlib.contextmanager
def _db():
    """One connection under the store lock: committed on success, rolled back on error, always closed."""
    with _LOCK:
        con = _connect()
        try:
            with con:
                yield con
        finally:
            con.close()


def _connect() -> sqlite3.Connection:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS receipts (receipt_id TEXT PRIMARY KEY, origin TEXT NOT NULL, "
                "at REAL NOT NULL, family TEXT NOT NULL, kind TEXT NOT NULL, line TEXT NOT NULL, model TEXT, "
                "where_ran TEXT, outcome TEXT, trace_id TEXT, task_id TEXT, turn_id TEXT, audience TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS receipts_at ON receipts (at DESC)")
    return con


_SQL_COLUMNS = ", ".join("where_ran" if c == "where" else c for c in _COLUMNS)


def reset() -> None:
    """Forget when the last sync ran (tests, and a store pointed somewhere new)."""
    global _last_sync
    _last_sync = 0.0


def put(receipts: Iterable[Receipt]) -> int:
    """Store receipts not already stored; returns how many were new."""
    rows = [astuple(r) for r in receipts if isinstance(r, Receipt)]
    if not rows:
        return 0
    with _db() as con:
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO receipts (%s) VALUES (%s)" % (_SQL_COLUMNS, ",".join("?" * len(_COLUMNS))),
                        rows)
        return con.total_changes - before


def recent(*, limit: int = 50, family: Optional[str] = None, words: Optional[str] = None) -> list[Receipt]:
    """The owner's receipts, newest first. `family` is one of FAMILIES (ValueError otherwise); `words` match the plain
    line or the model name literally; `limit` is clamped to 1..READ_MAX."""
    if family is not None and family not in FAMILIES:
        raise ValueError("unknown family")
    where, args = ["audience = ?"], [AUDIENCE_OWNER]
    if family is not None:
        where.append("family = ?")
        args.append(family)
    if words:
        pattern = "%" + re.sub(r"([\\%_])", r"\\\1", str(words)[:80]) + "%"
        where.append("(line LIKE ? ESCAPE '\\' OR model LIKE ? ESCAPE '\\')")
        args += [pattern, pattern]
    args.append(max(1, min(int(limit), READ_MAX)))
    with _db() as con:
        got = con.execute("SELECT %s FROM receipts WHERE %s ORDER BY at DESC, receipt_id DESC LIMIT ?"
                          % (_SQL_COLUMNS, " AND ".join(where)), args).fetchall()
    return [Receipt(*row) for row in got]


def models() -> list[dict]:
    """Each model that served the owner, where it ran in words, and how many turns: most turns first."""
    with _db() as con:
        got = con.execute("SELECT model, where_ran, COUNT(*) AS n FROM receipts WHERE family = 'model' AND audience = ? "
                          "AND model IS NOT NULL GROUP BY model, where_ran ORDER BY n DESC, model",
                          (AUDIENCE_OWNER,)).fetchall()
    return [{"model": m, "where": WHERE_WORDS.get(w, WHERE_WORDS[None]), "turns": n} for m, w, n in got]


def sync_from_activity(limit: int = SYNC_ROWS) -> int:
    """Index the newest activity rows; returns how many receipts were new."""
    from agent_friday.services import activity_ledger
    return put(r for r in map(from_activity, activity_ledger.read(limit=limit)) if r is not None)


def refresh() -> None:
    """Sync at most once per SYNC_MIN_S; a failure leaves the store as it was (the view shows what is indexed)."""
    global _last_sync
    now = time.monotonic()
    if _last_sync and now - _last_sync < SYNC_MIN_S:
        return
    _last_sync = now
    try:
        sync_from_activity()
    except (sqlite3.Error, OSError) as e:
        _log.warning("governance receipts: sync failed: %s", type(e).__name__)
