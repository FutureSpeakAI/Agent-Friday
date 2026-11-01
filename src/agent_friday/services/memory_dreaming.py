"""
Agent Friday — Memory Dreaming
FutureSpeak.AI · Asimov's Mind

Overnight consolidation. While the user sleeps, Friday reviews the day's
conversation turns (from the local ChromaDB conversation memory), extracts
recurring topics and durable facts, writes consolidated long-term entries, and
counts low-value ("noise") turns. (It reports that count; it does not itself
delete or tag turns in the conversation store — pruning is left to the retention
policy so consolidation is never destructive.)

Everything is LOCAL — heuristic pattern extraction over turns already on disk.
No cloud call, no LLM. Ring-0.

Persistence: ``~/.friday/dreams.db`` + human-readable ``~/.friday/dreams/<day>.md``
and a ``<day>.json`` sidecar. Leaf module — no Flask; returns envelopes, never
raises to the caller.

Consolidation rules (north star NS-15.9-1):
  * source pointers  every consolidated fact and topic lists the ids of the
                     stored turns it came from (``message_ids``), not just the day
  * never deletes    the conversation store is read through a read-only view
                     (an allowlist of reads); anything else through it raises
                     SourceDeletionRefused, which dream() turns into an error
                     envelope and an ERROR log line. The only things removed are
                     this module's own derived row and files for the day being
                     re-made (all three when a rebuilt day has no turns left)
  * marked           each record says what made it (``made_by``) and whether a
                     model wrote it (``model_summary``); the md says so in words
  * principal scope  each record and item carries ``principal`` ("owner") and
                     reads filter by it
  * sensitivity      off the record nothing is consolidated (the day is
                     remembered, date only, and the next run catches it up); a
                     fact or topic in a special category is dropped before it
                     is queued or written (_drop_special, NS-15.4-3)
  * forget           a forgotten person leaves the dreams too: forget_registry
                     store "dreams" (services/forget_derived -> forget_matching
                     below: the db row, <day>.md and the <day>.json sidecar)
  * rebuildable      ``rebuild_day(day)`` recomputes the day from stored turns;
                     running it again gives the same items
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional
from agent_friday.paths import friday_home
from agent_friday.user_errors import exception_text

# Friday's state root. Resolved centrally so FRIDAY_HOME redirects this
# module along with everything else (see agent_friday/paths.py).
FRIDAY_DIR = friday_home()
DB_PATH = FRIDAY_DIR / "dreams.db"
DREAMS_DIR = FRIDAY_DIR / "dreams"

_LOCK = threading.Lock()
_log = logging.getLogger("friday.dreaming")

# Upper bound on how many recent turns we scan to find one day's messages. The
# conversation memory has no date-scoped query, so we pull a window and filter;
# this is generous enough that a normal day fits, and dream() flags 'capped' when
# it doesn't (rather than silently under-consolidating).
_PULL_WINDOW = 20000

# Durable-fact patterns — sentences worth remembering long-term.
_FACT_PATTERNS = [
    (re.compile(r"\bi (?:prefer|like|love|hate|always|never|usually|tend to)\b", re.I), "preference"),
    (re.compile(r"\b(?:we|i) (?:decided|agreed|chose|settled on|going with)\b", re.I), "workflow"),
    (re.compile(r"\bmy (?:name|email|role|job|title|company|team|goal|deadline|birthday)\b", re.I), "bio"),
    (re.compile(r"\bremember (?:that|to)\b", re.I), "preference"),
    (re.compile(r"\bi'?m (?:working on|building|learning|trying to)\b", re.I), "workflow"),
    (re.compile(r"\bdon'?t (?:ever )?(?:show|send|use|include)\b", re.I), "preference"),
]
# Low-value turns to COUNT as noise (greetings, one-word clarifications). Counted
# for reporting only — not deleted or tagged in the conversation store.
_NOISE_PATTERNS = [
    re.compile(r"^\s*(?:hi|hey|hello|thanks|thank you|ok|okay|yes|no|yep|nope|cool|great|got it)[!.\s]*$", re.I),
]

# A `day` value reaches BOTH a SQL parameter and a FILENAME (dreams/<day>.md).
# Accept only a literal YYYY-MM-DD date so hostile input like "../../evil" can
# never traverse out of the dreams directory (POST /api/memory/dream passes the
# caller's `day` straight through).
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Whose memory a record belongs to (A11). One principal today; every record
#: carries it so a household or delegate principal can be added without
#: mixing their consolidation into the owner's.
PRINCIPAL = "owner"
#: What makes a dream: a heuristic pass, not a model.
MADE_BY = "heuristic"
#: The stores consolidation reads and may never delete from.
SOURCE_STORES = ("conversation_memory", "conversations", "conversation_fts")
#: Its own derived table, the only thing it replaces.
_DERIVED_TABLE = "dreams"


class SourceDeletionRefused(RuntimeError):
    """Consolidation tried to write to or delete from a source store."""


class _ReadOnlySource:
    """The conversation store as consolidation sees it: the named reads pass
    through, anything else raises (an allowlist, so a write method added to
    the store later is refused without anyone remembering to list it here).
    There is no passthrough to the underlying collection."""

    #: The reads consolidation may use. dream() itself calls only `available`
    #: and `recent`; the rest are the store's other read-only queries.
    _READS = frozenset({"available", "recent", "search", "get_session", "find_ids", "stats"})

    __slots__ = ("__src",)

    def __init__(self, inner):
        object.__setattr__(self, "_ReadOnlySource__src", inner)

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)          # copy/pickle probes, not store access
        if name not in _ReadOnlySource._READS:
            raise SourceDeletionRefused(
                f"consolidation only reads its sources (tried {name!r})")
        return getattr(object.__getattribute__(self, "_ReadOnlySource__src"), name)

    def __setattr__(self, name, value):
        raise SourceDeletionRefused(f"consolidation never changes its sources (set {name!r})")


def _assert_derived(table: str) -> None:
    """Only consolidation's own derived table may lose a row."""
    if table != _DERIVED_TABLE or table in SOURCE_STORES:
        raise SourceDeletionRefused(f"consolidation may not delete from {table!r}")


def _off_record() -> bool:
    """Off the record nothing is consolidated: storage stopped (skip), or the
    learning check (skip_learning, NS-15.8-2: an owner who keeps off-record
    transcripts still teaches Friday nothing; unreadable settings count as
    off the record)."""
    try:
        from agent_friday.services import off_record
        return bool(off_record.skip("memory_dreaming")
                    or off_record.skip_learning("memory_dreaming"))
    except Exception:
        return False


def _turn_id(t: Dict[str, Any]) -> str:
    return str(t.get("id") or t.get("msg_id") or t.get("message_id") or "")


def _settings() -> Dict[str, Any]:
    try:
        from agent_friday.core import _load_settings
        return _load_settings().get("memory_dreaming") or {}
    except Exception:
        return {}


def _enabled() -> bool:
    return bool(_settings().get("enabled", True))


def _keep_topics() -> int:
    try:
        return int(_settings().get("keep_topics", 12))
    except Exception:
        return 12


# ── DB ────────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dreams(
            dream_id TEXT PRIMARY KEY, ts REAL, day TEXT, turns_reviewed INTEGER,
            topics_json TEXT, consolidated_json TEXT, pruned INTEGER, summary TEXT
        );
        CREATE TABLE IF NOT EXISTS skipped_days(day TEXT PRIMARY KEY, ts REAL, reason TEXT);
        """
    )
    # Added for NS-15.9-1; a database from before them gains the columns here.
    # A row without a principal predates it and is the owner's. Only a column
    # that is missing is added, so any other error (a locked database) is
    # raised, not mistaken for "already there".
    have = {r[1] for r in conn.execute("PRAGMA table_info(dreams)").fetchall()}
    for name, decl in _ADDED_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE dreams ADD COLUMN {name} {decl}")
    conn.commit()
    return conn


#: Columns added after the table's first version, as (name, type).
_ADDED_COLUMNS = (("principal", "TEXT"), ("meta_json", "TEXT"))


def _yesterday() -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))


# ── Core pass ─────────────────────────────────────────────────────────────────
def dream(day: Optional[str] = None, *, memory=None, queue: bool = True,
          rebuilt: bool = False) -> Dict[str, Any]:
    """Consolidate one day's conversation turns. Defaults to *yesterday* so a
    03:00 run reviews the day that just ended.

    `memory` may be injected (tests pass a stub); otherwise the process-wide
    ConversationMemory singleton is used. Degrades to a well-formed empty
    envelope when memory is unavailable. `queue` files the candidates in the
    memory card's review queue (rebuild_day does not: a rebuild re-makes the
    record and must not bring back a suggestion the owner already turned down).
    """
    if not _enabled():
        return {"ok": True, "skipped": True, "reason": "disabled"}
    day = str(day).strip() if day else _yesterday()
    if not _DAY_RE.match(day):
        return {"ok": False, "error": "invalid day — expected YYYY-MM-DD"}
    if _off_record():
        # Off the record nothing about the conversation is written
        # (services/off_record). The stored turns carry no per-turn off-record
        # flag on this branch (an off-record turn is never stored at all), so
        # the run is skipped as a whole and the DAY (a date, no content) is
        # remembered: the next run that is not off the record consolidates it.
        _remember_skipped(day, "off_record")
        return {"ok": True, "skipped": True, "reason": "off_record", "day": day}
    res = _dream_day(day, memory=memory, queue=queue, rebuilt=rebuilt)
    if res.get("ok"):
        _forget_skipped(day)
        caught = _catch_up(day, memory=memory)
        if caught:
            res["caught_up"] = caught
    return res


def _dream_day(day: str, *, memory=None, queue: bool = True,
               rebuilt: bool = False) -> Dict[str, Any]:
    try:
        if memory is None:
            try:
                from agent_friday.conversation_memory import get_conversation_memory
                memory = get_conversation_memory()
            except Exception:
                memory = None
        # Read-only from here on: consolidation never changes its sources.
        source = _ReadOnlySource(memory) if memory is not None else None
        turns, capped = _pull_turns(source, day)
        if capped:
            _log.warning("dream(%s): recent()-window saturated at %d turns; some of "
                         "that day's turns may be unconsolidated.", day, _PULL_WINDOW)
        if not turns:
            cleared = False
            if rebuilt and not capped and _readable(source):
                # A rebuild recomputes the day from the stored turns, and there
                # are none now (forgotten or deleted): the day's derived record
                # goes too. Not when the store could not be read: an outage is
                # not an empty day.
                _clear_day(day)
                cleared = True
            return {"ok": True, "day": day, "turns_reviewed": 0,
                    "topics": [], "consolidated": [], "pruned": 0, "capped": capped,
                    "principal": PRINCIPAL, "source_message_ids": [],
                    "rebuilt": bool(rebuilt), "cleared": cleared,
                    "summary": "Nothing to consolidate." if not capped else
                    "Nothing to consolidate (turn window saturated — may be incomplete)."}

        topics = _extract_topics(turns)
        facts = _mine_facts(turns)
        if queue:
            # A rebuild files nothing new for review (NS-15.9-1), and the head
            # may store pending proposals, so it runs only on a queuing pass.
            try:
                # The encoder sentence reader: shadow log, or pending proposals the
                # owner reviews. It never feeds `facts`, so nothing below changes.
                from agent_friday.services import triage_heads
                triage_heads.memory_pass(turns, day, facts)
            except Exception:
                pass
        pruned = _count_noise(turns)
        # NS-15.4-3: a regex is inferring. A sentence or topic that touches a
        # special category (health, sexuality, politics, religion, ...) is
        # dropped here, so it is neither queued nor written to dreams.db or
        # the day's markdown; only a count by category is kept.
        facts, topics = _drop_special(facts, topics)
        source_ids = sorted({_turn_id(t) for t in turns if _turn_id(t)})

        # A regex over yesterday's turns is not a review. Nothing it finds is
        # written into what Friday knows (the user model, the knowledge graph):
        # each candidate waits in the memory card's review queue, and only the
        # owner's Keep puts it on the card (services/core_memory). Queuing is
        # idempotent, so re-running a day files nothing twice.
        consolidated = []
        for f in facts:
            consolidated.append(f)
            if queue and f["confidence"] >= 0.6:
                try:
                    from agent_friday.services import core_memory
                    core_memory.queue("user", "add", text=f["text"], source=f"dream:{day}",
                                      reason="Nightly dreaming found this in your "
                                             "conversations. Keep it only if it is true.")
                except Exception:
                    pass

        summary = _summarize(day, len(turns), topics, consolidated, pruned)
        meta = {"principal": PRINCIPAL, "made_by": MADE_BY, "model_summary": False,
                "kind": "inferred", "source_message_ids": source_ids, "rebuilt": bool(rebuilt)}
        _persist(day, len(turns), topics, consolidated, pruned, summary, meta)
        _write_markdown(day, len(turns), topics, consolidated, pruned, summary, meta)

        return {"ok": True, "day": day, "turns_reviewed": len(turns),
                "topics": topics, "consolidated": consolidated,
                "pruned": pruned, "capped": capped, "summary": summary,
                "principal": PRINCIPAL, "made_by": MADE_BY, "model_summary": False,
                "source_message_ids": source_ids, "rebuilt": bool(rebuilt)}
    except SourceDeletionRefused as e:
        # A bug, not a condition: consolidation reached for a source write.
        # Loud in the log, an envelope to the caller (this module never raises).
        _log.error("dream(%s) refused: %s", day, e)
        return {"ok": False, "day": day, "error": exception_text(e),
                "reason": "source_deletion_refused"}
    except Exception as e:
        return {"ok": False, "day": day, "error": exception_text(e)}


def _readable(source) -> bool:
    """Could the conversation store be read just now?"""
    if source is None:
        return False
    try:
        av = getattr(source, "available", None)       # a stub may not have it
        if callable(av) and not av():
            return False
        source.recent(n=1)
        return True
    except Exception:
        return False


def _clear_day(day: str, principal: str = PRINCIPAL) -> None:
    """Remove the day's derived record: its row, <day>.md and <day>.json."""
    with _LOCK:
        _assert_derived(_DERIVED_TABLE)
        conn = _connect()
        try:
            conn.execute("DELETE FROM dreams WHERE day=? AND COALESCE(principal, ?)=?",
                         (day, PRINCIPAL, principal))
            conn.commit()
        finally:
            conn.close()
    for ext in ("md", "json"):
        try:
            (DREAMS_DIR / f"{day}.{ext}").unlink()
        except FileNotFoundError:
            pass


# ── days skipped off the record ───────────────────────────────────────────────
# Only the date and why are kept (no content), so a night that fell off the
# record is consolidated by the next run that is not.
_CATCH_UP_MAX = 14


def _remember_skipped(day: str, reason: str) -> None:
    try:
        conn = _connect()
        try:
            conn.execute("INSERT OR REPLACE INTO skipped_days(day, ts, reason) VALUES(?,?,?)",
                         (day, time.time(), reason))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        _log.warning("dream(%s): could not remember the skipped day: %s", day, e)


def _forget_skipped(day: str) -> None:
    try:
        conn = _connect()
        try:
            conn.execute("DELETE FROM skipped_days WHERE day=?", (day,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def skipped_days() -> List[str]:
    """Days a run skipped (off the record) and has not yet consolidated."""
    try:
        conn = _connect()
        try:
            return [r[0] for r in conn.execute(
                "SELECT day FROM skipped_days ORDER BY day").fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def _catch_up(done_day: str, *, memory=None) -> List[str]:
    """Consolidate the days skipped off the record (oldest first); each one
    that succeeds leaves the list."""
    caught = []
    for d in skipped_days()[:_CATCH_UP_MAX]:
        if d == done_day or not _DAY_RE.match(d):
            _forget_skipped(d)
            continue
        if _off_record():
            break
        r = _dream_day(d, memory=memory, queue=True, rebuilt=False)
        if r.get("ok"):
            _forget_skipped(d)
            caught.append(d)
    return caught


def rebuild_day(day: str, *, memory=None) -> Dict[str, Any]:
    """Re-make one day's consolidation from the stored turns (NS-15.9-1).

    Idempotent: the same stored turns give the same topics and items, and the
    day keeps one record. Nothing is queued for review again and no source is
    touched; the day's previous derived record is replaced."""
    return dream(day, memory=memory, queue=False, rebuilt=True)


def _principal_of(row_principal) -> str:
    return str(row_principal or PRINCIPAL)


def recent_dreams(n: int = 7, principal: str = PRINCIPAL) -> List[Dict[str, Any]]:
    """The newest dreams of `principal` (the owner by default), each item
    filtered to that principal too."""
    try:
        n = max(1, min(100, int(n)))
    except (TypeError, ValueError):
        n = 7
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT day, ts, turns_reviewed, topics_json, consolidated_json, "
            "pruned, summary, principal, meta_json FROM dreams ORDER BY ts DESC").fetchall()
        conn.close()
        out = []
        for day, ts, tr, tj, cj, pr, summ, pri, mj in rows:
            if _principal_of(pri) != principal:
                continue
            meta = _loads(mj, {})
            out.append({
                "day": day, "ts": ts, "turns_reviewed": tr,
                "topics": [t for t in _loads(tj, []) if _principal_of(t.get("principal")) == principal],
                "consolidated": [c for c in _loads(cj, [])
                                 if _principal_of(c.get("principal")) == principal],
                "pruned": pr, "summary": summ, "principal": _principal_of(pri),
                "made_by": meta.get("made_by", MADE_BY),
                "model_summary": bool(meta.get("model_summary", False)),
                "source_message_ids": meta.get("source_message_ids", []),
            })
            if len(out) >= n:
                break
        return out
    except Exception:
        return []


def state() -> Dict[str, Any]:
    try:
        conn = _connect()
        n = conn.execute("SELECT COUNT(*) FROM dreams").fetchone()[0]
        last = conn.execute(
            "SELECT day, ts, summary FROM dreams ORDER BY ts DESC LIMIT 1").fetchone()
        conn.close()
        return {
            "available": True,
            "enabled": _enabled(),
            "total_dreams": n,
            "last_day": last[0] if last else None,
            "last_ts": last[1] if last else None,
            "last_summary": last[2] if last else None,
        }
    except Exception as e:
        return {"available": False, "error": exception_text(e)}


# ── extraction internals ──────────────────────────────────────────────────────
def _pull_turns(memory, day: str):
    """Return (turns_for_day, capped).

    The conversation memory exposes no date-scoped query, so we pull a generous
    newest-first window and filter by date. `capped` is True when the window
    filled AND its oldest row is still on/after `day` — meaning older turns from
    `day` may have been cut off (so the caller can warn instead of silently
    under-consolidating). Returns ([], False) when memory is unavailable.
    """
    if memory is None:
        return [], False
    try:
        rows = memory.recent(n=_PULL_WINDOW) or []
    except Exception:
        return [], False
    out = []
    oldest_date = None  # rows are newest-first, so the last dated row is oldest
    for r in rows:
        rdate = r.get("date") or (r.get("timestamp") or "")[:10]
        if rdate:
            oldest_date = rdate
        # A superseded turn is history, not evidence: never consolidated. A
        # turn marked off the record (none should be stored) is skipped too.
        if rdate == day and not r.get("superseded") and not r.get("off_record"):
            out.append(r)
    capped = len(rows) >= _PULL_WINDOW and (oldest_date is None or oldest_date >= day)
    return out, capped


def _extract_topics(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counter: Counter = Counter()
    ids: Dict[str, List[str]] = {}
    # Prefer the keywords the memory already indexed; fall back to extracting.
    for t in turns:
        kws = t.get("topic_keywords") or []
        if not kws:
            kws = _keywords(t.get("text") or "")
        tid = _turn_id(t)
        for k in kws:
            counter[k] += 1
            if tid and tid not in ids.setdefault(k, []):
                ids[k].append(tid)
    top = counter.most_common(_keep_topics())
    # Each topic points at the turns that mention it (NS-15.9-1).
    return [{"topic": k, "mentions": c, "message_ids": sorted(ids.get(k, []))[:50],
             "principal": PRINCIPAL} for k, c in top]


def _keywords(text: str) -> List[str]:
    try:
        from agent_friday.conversation_memory import extract_keywords
        return extract_keywords(text)
    except Exception:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", (text or "").lower())
        return [w for w in words if w not in _STOP][:8]


_STOP = {"this", "that", "with", "have", "your", "will", "what", "when", "there",
         "would", "could", "should", "about", "which", "their", "them", "then",
         "from", "just", "like", "want", "need", "know", "make", "some", "more"}


def _mine_facts(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    facts: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for t in turns:
        if t.get("role") not in (None, "user", "human"):
            # facts about the user come from the user's own turns
            continue
        text = (t.get("text") or "").strip()
        tid = _turn_id(t)
        # A special-category sentence is dropped after mining (_drop_special),
        # where the drop is counted by category.
        for sentence in _split_sentences(text):
            for pat, category in _FACT_PATTERNS:
                if pat.search(sentence):
                    norm = sentence.strip()[:240]
                    key = norm.lower()
                    if len(norm) < 8:
                        continue
                    if key in seen:
                        # The same sentence again: one fact, every turn it came from.
                        ids = seen[key]["message_ids"]
                        if tid and tid not in ids:
                            ids.append(tid)
                        continue
                    # confidence scales with how declarative the sentence is
                    conf = 0.7 if category in ("bio", "preference") else 0.6
                    fact = {"category": category, "text": norm, "confidence": conf,
                            # Turn-level source pointers, whose memory, and what kind
                            # of information this is (NS-15.9-1, NS-3.4-1).
                            "message_ids": [tid] if tid else [],
                            "principal": PRINCIPAL, "kind": "inferred",
                            "made_by": MADE_BY, "model_summary": False}
                    seen[key] = fact
                    facts.append(fact)
                    break
    return facts[:20]


def _drop_special(facts: List[Dict[str, Any]], topics: List[Dict[str, Any]]):
    """Facts and topics less those that touch a special category
    (services/sensitive_categories). Dropped items are counted, not kept."""
    try:
        from agent_friday.services import sensitive_categories as _sc
    except Exception:
        return [], []                       # cannot check: keep nothing inferred
    kept_facts = []
    for f in facts:
        cat = _sc.special_category(f.get("text"))
        if cat:
            _sc.note_dropped("dreaming.fact", cat)
            continue
        kept_facts.append(f)
    kept_topics = []
    for t in topics:
        cat = _sc.special_category(t.get("topic"))
        if cat:
            _sc.note_dropped("dreaming.topic", cat)
            continue
        kept_topics.append(t)
    return kept_facts, kept_topics


def _count_noise(turns: List[Dict[str, Any]]) -> int:
    n = 0
    for t in turns:
        text = (t.get("text") or "").strip()
        if any(p.match(text) for p in _NOISE_PATTERNS):
            n += 1
    return n


def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _summarize(day, n_turns, topics, consolidated, pruned) -> str:
    top = ", ".join(t["topic"] for t in topics[:5]) or "—"
    return (f"Reviewed {n_turns} turns from {day}. "
            f"Top topics: {top}. "
            f"Consolidated {len(consolidated)} durable fact(s); "
            f"counted {pruned} low-value turn(s).")


def _persist(day, n_turns, topics, consolidated, pruned, summary, meta=None) -> None:
    meta = dict(meta or {})
    with _LOCK:
        try:
            conn = _connect()
            # one dream row per day — replace on re-run. The row is this
            # module's own derived record; no source store is ever touched.
            _assert_derived(_DERIVED_TABLE)
            conn.execute("DELETE FROM dreams WHERE day=? AND COALESCE(principal, ?)=?",
                         (day, PRINCIPAL, meta.get("principal", PRINCIPAL)))
            conn.execute(
                "INSERT INTO dreams(dream_id,ts,day,turns_reviewed,topics_json,"
                "consolidated_json,pruned,summary,principal,meta_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], time.time(), day, n_turns,
                 json.dumps(topics), json.dumps(consolidated), int(pruned), summary,
                 meta.get("principal", PRINCIPAL), json.dumps(meta)))
            conn.commit()
            conn.close()
        except SourceDeletionRefused:
            raise
        except Exception:
            pass


def _ids_text(ids) -> str:
    ids = [str(i) for i in (ids or []) if str(i)]
    if not ids:
        return "sources: the day's turns (no message ids stored)"
    shown = ", ".join(ids[:6]) + (f" and {len(ids) - 6} more" if len(ids) > 6 else "")
    return "from messages " + shown


def _render_markdown(day, n_turns, topics, consolidated, pruned, summary, meta=None) -> str:
    meta = dict(meta or {})
    lines = [f"# Dream — {day}", "",
             # What made this, and whose it is, in words (NS-15.9-1).
             f"> Made by Friday's overnight pass ({meta.get('made_by', MADE_BY)}, not a "
             f"model summary) for {meta.get('principal', PRINCIPAL)}. INFERRED: nothing "
             f"here is confirmed; each item names the messages it came from.",
             "", f"*{summary}*", "",
             f"- Turns reviewed: {n_turns}", f"- Low-value turns (counted): {pruned}",
             "", "## Top topics"]
    for t in topics:
        lines.append(f"- {t['topic']} ({t['mentions']}) — {_ids_text(t.get('message_ids'))}")
    lines += ["", "## Consolidated facts"]
    if consolidated:
        for f in consolidated:
            lines.append(f"- **[{f['category']}]** {f['text']} — "
                         f"{_ids_text(f.get('message_ids'))}")
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def _render_json(day, n_turns, topics, consolidated, pruned, summary, meta=None) -> str:
    """The same record as data, with every source pointer (the <day>.json sidecar)."""
    record = {"day": day, "turns_reviewed": n_turns, "pruned": pruned,
              "summary": summary, "topics": topics, "consolidated": consolidated}
    record.update(dict(meta or {}))
    return json.dumps(record, indent=2)


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` via a temp file and os.replace: a crash leaves
    the old file or the new one, never half of one. Raises on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_record(day, n_turns, topics, consolidated, pruned, summary, meta=None) -> None:
    """Write the day's <day>.md and its <day>.json sidecar, each atomically.
    Raises on failure (forget_matching reports it)."""
    from agent_friday.paths import contained
    _write_atomic(contained(DREAMS_DIR, f"{day}.md"),
                  _render_markdown(day, n_turns, topics, consolidated, pruned, summary, meta))
    _write_atomic(contained(DREAMS_DIR, f"{day}.json"),
                  _render_json(day, n_turns, topics, consolidated, pruned, summary, meta))


def _write_markdown(day, n_turns, topics, consolidated, pruned, summary, meta=None) -> None:
    try:
        _write_record(day, n_turns, topics, consolidated, pruned, summary, meta)
    except Exception:
        pass


def forget_matching(match, keyword=None, apply: bool = True) -> Dict[str, Any]:
    """Forget-person (services/forget_derived, NS-15.7-2): the consolidated
    facts whose text `match` accepts and the topics `keyword` (a predicate on
    one lowercase keyword) or `match` accepts leave each day's dream; the
    day's summary line is written again from what is left, and its
    ``dreams/<day>.md`` and ``<day>.json`` sidecar are rewritten from the
    cleaned row (keeping its principal, made_by and source message ids). A
    day log with no row loses only the lines that name the person, and its
    summary line (``*Reviewed ... Top topics: ...*``) loses the topics that
    name them; a sidecar with no row loses the items that name them (and is
    removed whole if it still names them after that: it is derived).
    With `apply=False` only counts. Returns {"facts", "topics", "lines" (day
    logs with no row: lines removed or edited), "days", "not_removable"}.
    Raises when the store cannot be read, so the receipt says it was not
    checked.

    The database is committed BEFORE any day log is written again, and each
    day log is written atomically (temp file, then os.replace) in its own
    try: a log that cannot be rewritten is listed in "not_removable" (and,
    for a log with no row, its lines are not counted as removed)."""
    from agent_friday.user_errors import clip
    kw = keyword or (lambda _k: False)
    touched = set()                               # days changed (or that would be)

    def named(k) -> bool:
        # A lowercase keyword (kw), or the matcher's rule on the topic as
        # written (a multi-word name in any case, a one-word one as cased).
        k = str(k or "").strip()
        return bool(k) and (kw(k.lower()) or match(k))

    def topic_named(t) -> bool:
        return named((t or {}).get("topic"))

    def failed(day, e) -> dict:
        return {"what": "the dreaming log for %s" % day,
                "reason": clip(exception_text(e, "it could not be written again, so it may still name them (%s)"),
                               200)}

    out: Dict[str, Any] = {"facts": 0, "topics": 0, "lines": 0, "days": 0, "not_removable": []}
    rewritten = set()
    redo = []                                     # day logs written again after the commit
    if DB_PATH.exists():
        with _LOCK:
            conn = _connect()
            try:
                rows = conn.execute("SELECT dream_id, day, turns_reviewed, topics_json, consolidated_json, "
                                    "pruned, principal, meta_json FROM dreams").fetchall()
                for did, day, tr, tj, cj, pr, pri, mj in rows:
                    topics, facts = _loads(tj, []), _loads(cj, [])
                    keep_t = [t for t in topics if not topic_named(t)]
                    keep_f = [f for f in facts if not match(str((f or {}).get("text") or ""))]
                    if len(keep_t) == len(topics) and len(keep_f) == len(facts):
                        continue
                    out["topics"] += len(topics) - len(keep_t)
                    out["facts"] += len(facts) - len(keep_f)
                    touched.add(str(day))
                    if _DAY_RE.match(str(day or "")):
                        rewritten.add(day)          # its day log is written again from the row
                    if not apply:
                        continue
                    summary = _summarize(day, tr, keep_t, keep_f, pr)
                    conn.execute("UPDATE dreams SET topics_json=?, consolidated_json=?, summary=? "
                                 "WHERE dream_id=?", (json.dumps(keep_t), json.dumps(keep_f), summary, did))
                    if day in rewritten:
                        # The record keeps what made it, whose it is and its sources.
                        meta = _loads(mj, {})
                        meta = meta if isinstance(meta, dict) else {}
                        meta.setdefault("principal", _principal_of(pri))
                        redo.append((day, tr, keep_t, keep_f, pr, summary, meta))
                if apply:
                    conn.commit()
            finally:
                conn.close()
        for day, tr, keep_t, keep_f, pr, summary, meta in redo:
            try:
                _write_record(day, tr, keep_t, keep_f, pr, summary, meta)
            except Exception as e:
                out["not_removable"].append(failed(day, e))
    if DREAMS_DIR.exists():
        # A topic line may end with its sources (" — from messages ...").
        topic_line = re.compile(r"^- (.+?) \(\d+\)(?: — .*)?$")
        summary_line = re.compile(r"^(\*Reviewed .*?Top topics: )(.*?)(\. Consolidated .*)$")
        for p in sorted(DREAMS_DIR.glob("*.md")):
            if p.stem in rewritten:
                continue
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
                keep, changed = [], 0
                for line in lines:
                    edited = line
                    s = summary_line.match(line)
                    if s:
                        tops = [x for x in s.group(2).split(", ") if x.strip() and x.strip() != "—"]
                        left = [x for x in tops if not named(x)]
                        if len(left) != len(tops):
                            edited = s.group(1) + (", ".join(left) or "—") + s.group(3)
                    t = topic_line.match(edited)
                    if match(edited) or (t and named(t.group(1))):
                        changed += 1                # the line goes
                        continue
                    changed += int(edited != line)  # the summary line loses the topics naming them
                    keep.append(edited)
                if not changed:
                    continue
                if apply:
                    _write_atomic(p, "\n".join(keep) + "\n")
                touched.add(p.stem)
                out["lines"] += changed
            except Exception as e:
                out["not_removable"].append(failed(p.stem, e))
        # <day>.json sidecars with no row: the items naming them go, and the
        # summary is made again from what is left.
        for p in sorted(DREAMS_DIR.glob("*.json")):
            if p.stem in rewritten or not _DAY_RE.match(p.stem):
                continue
            try:
                rec = _loads(p.read_text(encoding="utf-8"), None)
                if not isinstance(rec, dict):
                    continue
                topics = [t for t in (rec.get("topics") or []) if isinstance(t, dict)]
                facts = [f for f in (rec.get("consolidated") or []) if isinstance(f, dict)]
                keep_t = [t for t in topics if not topic_named(t)]
                keep_f = [f for f in facts if not match(str(f.get("text") or ""))]
                gone_t, gone_f = len(topics) - len(keep_t), len(facts) - len(keep_f)
                if gone_t or gone_f:
                    rec["topics"], rec["consolidated"] = keep_t, keep_f
                    rec["summary"] = _summarize(p.stem, rec.get("turns_reviewed", 0), keep_t, keep_f,
                                                rec.get("pruned", 0))
                text = json.dumps(rec, indent=2)
                # Still names them (a field the items do not cover): derived, removed whole.
                stray = match(json.dumps(rec, ensure_ascii=False))
                if not (gone_t or gone_f or stray):
                    continue
                if apply:
                    if stray:
                        p.unlink()
                    else:
                        _write_atomic(p, text)
                touched.add(p.stem)
                out["topics"] += gone_t
                out["facts"] += gone_f
                out["lines"] += int(stray and not (gone_t or gone_f))
            except Exception as e:
                out["not_removable"].append(failed(p.stem, e))
    out["days"] = len(touched)
    return out


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


# ── forget a person (services/forget_registry) ────────────────────────────────
# A dream names people in its topics and facts, in three places: the db row,
# <day>.md and the <day>.json sidecar. The registry's "dreams" store
# (services/forget_derived.dreams_find / dreams_purge) calls forget_matching
# above with the shared name matcher; these two give the count of days.

def _person_matchers(names, emails):
    from agent_friday.services import forget_derived as fd
    m = fd._matcher(names, emails)
    return m, fd._keyword_matcher(m)


def people_find(names, emails) -> int:
    """forget-person: how many days' dreams name the person."""
    m, kw = _person_matchers(names, emails)
    if not m:
        return 0
    return int(forget_matching(m, keyword=kw, apply=False).get("days") or 0)


def people_purge(names, emails) -> int:
    """forget-person: remove what names the person from every day's dream
    (row, md, json); returns the number of days touched."""
    m, kw = _person_matchers(names, emails)
    if not m:
        return 0
    return int(forget_matching(m, keyword=kw).get("days") or 0)
