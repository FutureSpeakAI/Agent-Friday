"""The governance receipt store: what Friday did, which model served it and where it ran, in plain words.

A source row is parsed into one typed `Receipt` where it enters (`from_activity`) or refused (None). The store is an
index, not the record: the signed action ledger stays the record, and every receipt here names its origin and its
place there (`receipt_id` is `<origin>:<source>:<id>`). Each receipt is kept once: `put` is insert-or-ignore on
`receipt_id`, so a re-sync or a retried batch stores nothing.

Sync keeps a cursor over the ledger's activity seqs: every seq in [bottom, top] is indexed. Each sync reads the rows
above `top` (newest first, stopping at the first known one) and one page below `bottom`, until the ledger's start is
reached; so no row is skipped however busy the PC was while the view was closed, and `rebuild()` refills the whole
index from the ledger (test_a_busy_ledger_is_indexed_whole_across_syncs, test_rebuild_refills_the_index_from_the_ledger).

Rules (tests/unit/test_governance_receipts.py):
* the plain line is built from the fixed words in `_LINES` plus the shown model or tool name (`_display`); no other
  field of a row ever reaches it (test_no_free_text_from_any_activity_field_ever_reaches_the_line);
* where a model ran comes from the seat the caller recorded, then the provider, and is otherwise "not recorded",
  never guessed (test_where_a_model_ran_is_never_guessed_when_the_provider_was_not_recorded,
  test_a_local_seat_is_on_this_pc_whatever_dialect_it_spoke);
* a recorded model name is never erased: it is shown in a form the view cannot drop, and the models table counts
  every model receipt (test_every_name_a_receipt_keeps_survives_the_view); a path shows only its file name and an
  address is never shown (test_a_path_or_address_is_never_taken_for_a_name);
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
SYNC_PAGE = 500
SYNC_MIN_S = 15.0
#: Seats the caller records for work done on this PC, and for work sent out (services/answer_truth.is_local_seat).
LOCAL_SEATS = frozenset({"local", "llama-cpp-local", "ollama-local"})

#: None means the default under the Friday folder; tests point it at a temporary file.
DB_PATH: Optional[Path] = None

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


#: A drive path, a rooted or home path, or a backslash: a file the model was loaded from, shown by its file name.
_PATHISH = re.compile(r"^[A-Za-z]:[/\\]|^[/~]|\\")
_RECORDED_MAX = 200          # the activity ledger's own cap on a text field
SHOWN_MAX = 80
#: A recorded name that cannot be shown without showing an address.
NAME_NOT_SHOWN = "name not shown"
#: What the models table says for model work whose name was not recorded.
NAME_NOT_RECORDED = "name not recorded"


def _recorded(v) -> Optional[str]:
    """A recorded model or tool id: one line of text, at most the ledger's cap; None when nothing was recorded."""
    if not isinstance(v, str) or not v.strip() or len(v) > _RECORDED_MAX:
        return None
    return None if any(ord(c) < 32 or 0x7f <= ord(c) < 0xa0 for c in v) else v.strip()


def _display(name: Optional[str]) -> Optional[str]:
    """A recorded id as the view shows it, never erased: a path by its file name (never the folders, which can name
    a person); an id the view keeps as it is, unchanged; otherwise (one the A2UI profile would read as a link, such
    as a hub id, or one too long) every `:tag` as ` (tag)` and every `/` spaced, at most SHOWN_MAX characters; an id
    that is or holds an address is NAME_NOT_SHOWN. The result never trips the
    profile's own has_url, the rule the broker drops rows by (test_every_name_a_receipt_keeps_survives_the_view,
    test_a_path_or_address_is_never_taken_for_a_name)."""
    from agent_friday.services import a2ui_profile
    if name is None:
        return None
    if _PATHISH.search(name):
        name = re.split(r"[/\\]", name)[-1] or NAME_NOT_SHOWN
    if "@" in name:
        return NAME_NOT_SHOWN
    if len(name) <= SHOWN_MAX and not a2ui_profile.has_url(name):
        return name
    head, *tags = name.split(":")
    shown = " / ".join(p for p in head.split("/") if p) + "".join(" (%s)" % t for t in tags if t)
    if len(shown) > SHOWN_MAX:
        shown = shown[:SHOWN_MAX - 1].rstrip() + "…"
    if not shown.strip() or a2ui_profile.has_url(shown):
        return NAME_NOT_SHOWN
    return shown


def _id(v) -> Optional[str]:
    return v if isinstance(v, str) and _ID.fullmatch(v) else None


def _is_local(provider: str, model: str) -> bool:
    from agent_friday.services import seat_policy
    return seat_policy.is_local_seat(provider, model)


def _where(row: dict, model: Optional[str]) -> Optional[str]:
    """On this PC when the caller recorded a local seat (the provider is only the call's dialect: a local
    llama-server seat speaks "openai"); otherwise from the provider; None when neither was recorded."""
    seat = row.get("seat")
    if isinstance(seat, str) and seat.strip().lower() in LOCAL_SEATS:
        return ON_THIS_PC
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
    recorded = _recorded(row.get("model"))
    model = _display(recorded)
    tool = _display(_recorded(row.get("tool")))
    where = _where(row, recorded) if kind == "model_invocation" else None
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
    con.execute("CREATE TABLE IF NOT EXISTS cursor (name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
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
    """Each model that served the owner, where it ran in words, and how many turns, most first. Every model receipt
    is counted: one whose name was not recorded is under NAME_NOT_RECORDED."""
    with _db() as con:
        got = con.execute("SELECT COALESCE(model, ?) AS m, where_ran, COUNT(*) AS n FROM receipts "
                          "WHERE family = 'model' AND audience = ? GROUP BY m, where_ran ORDER BY n DESC, m",
                          (NAME_NOT_RECORDED, AUDIENCE_OWNER)).fetchall()
    return [{"model": m, "where": WHERE_WORDS.get(w, WHERE_WORDS[None]), "turns": n} for m, w, n in got]


def _activity(*, above: Optional[int] = None, below: Optional[int] = None, limit: Optional[int] = None) -> list[dict]:
    """Activity rows (the row with its ledger `seq`), newest first, with above < seq < below. Reading stops at the
    first row at or under `above`, so a sync of new rows reads only what is newer than the index. The read is strict
    (a file that cannot be read raises LedgerError, never a short list) and includes the archive; it is not held by
    an open break, which the Ledger page shows, and a line that is not JSON is skipped, as every ledger reader does.
    Every entry read is returned, a non-record one as its bare seq, so a page's length is the entries it covered."""
    from agent_friday.services import action_ledger
    passed: list = []

    def want(e: dict) -> bool:
        seq = e.get("seq")
        if not isinstance(seq, int):
            return False
        if above is not None and seq <= above:
            passed.append(seq)
            return False
        return below is None or seq < below

    out = []
    for e in action_ledger.entries(["activity"], newest_first=True, limit=limit, where=want, stop=lambda: bool(passed),
                                   strict=True, gate=False, archive=True):
        rec = e.get("rec")
        out.append({**rec, "seq": e["seq"]} if isinstance(rec, dict) else {"seq": e["seq"]})
    return out


def _cursor(con) -> dict:
    return dict(con.execute("SELECT name, value FROM cursor").fetchall())


def sync_from_activity() -> int:
    """Index the activity rows newer than the index, then one older page until the ledger's start; returns how many
    receipts were new. The cursor moves in the same transaction as the receipts it covers, and only after every read
    of this sync finished: a read that fails part way changes nothing (test_a_read_that_fails_part_way_moves_nothing).
    A ledger whose newest row is older than the cursor was replaced: this PC's rows are indexed again from it
    (test_a_replaced_ledger_is_indexed_again)."""
    from agent_friday.services import action_ledger
    try:
        return _sync_once()
    except (action_ledger.LedgerError, OSError) as e:
        _log.warning("governance receipts: the activity ledger could not be read whole: %s", type(e).__name__)
        return 0


def _sync_once() -> int:
    with _db() as con:
        cur = _cursor(con)
    top, bottom, done = cur.get("top"), cur.get("bottom"), bool(cur.get("done"))
    replaced = False
    if top is not None and top >= 0:
        newest = _activity(limit=1)
        replaced = not newest or newest[0]["seq"] < top
        if replaced:
            top = None
    if top is None:
        rows = _activity(limit=SYNC_PAGE)
        seqs = [r["seq"] for r in rows]
        top, bottom, done = (max(seqs), min(seqs), len(rows) < SYNC_PAGE) if seqs else (-1, 0, True)
        older: list = []
    else:
        rows = _activity(above=top)
        top = max([top] + [r["seq"] for r in rows])
        older = [] if done else _activity(below=bottom, limit=SYNC_PAGE)
        if not done:
            bottom = min([bottom] + [r["seq"] for r in older])
            done = len(older) < SYNC_PAGE
    receipts = [r for r in map(from_activity, rows + older) if r is not None]
    with _db() as con:
        if replaced:
            con.execute("DELETE FROM receipts WHERE receipt_id LIKE ?", ("%s:activity:%%" % ORIGIN_PC,))
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO receipts (%s) VALUES (%s)" % (_SQL_COLUMNS, ",".join("?" * len(_COLUMNS))),
                        [astuple(r) for r in receipts])
        new = con.total_changes - before
        con.executemany("INSERT OR REPLACE INTO cursor (name, value) VALUES (?, ?)",
                        [("top", top), ("bottom", bottom), ("done", int(done))])
    return new


def rebuild(max_syncs: int = 10_000) -> int:
    """Empty the index and refill it from the ledger; returns how many receipts it holds after."""
    with _db() as con:
        con.execute("DELETE FROM receipts")
        con.execute("DELETE FROM cursor")
    for _ in range(max_syncs):
        sync_from_activity()
        with _db() as con:
            if _cursor(con).get("done"):
                break
    with _db() as con:
        return con.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]


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
