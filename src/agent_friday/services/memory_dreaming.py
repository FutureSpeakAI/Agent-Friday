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
  * never deletes    the conversation store is read through a read-only view;
                     a write or delete through it raises SourceDeletionRefused.
                     The only row removed is this module's own derived row for
                     the day being re-made
  * marked           each record says what made it (``made_by``) and whether a
                     model wrote it (``model_summary``); the md says so in words
  * principal scope  each record and item carries ``principal`` ("owner") and
                     reads filter by it
  * sensitivity      off the record nothing is consolidated; a sentence in a
                     special category (health, sexuality) is not mined
  * rebuildable      ``rebuild_day(day)`` recomputes the day from stored turns;
                     running it again gives the same items
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
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
#: Special categories (health, sexuality) never mined into a suggestion.
_SPECIAL = ("health", "sexuality")
#: The stores consolidation reads and may never delete from.
SOURCE_STORES = ("conversation_memory", "conversations", "conversation_fts")
#: Its own derived table, the only thing it replaces.
_DERIVED_TABLE = "dreams"


class SourceDeletionRefused(RuntimeError):
    """Consolidation tried to write to or delete from a source store."""


class _ReadOnlySource:
    """The conversation store as consolidation sees it: reads pass through,
    anything that would change or remove a stored turn raises."""

    _WRITES = frozenset({"delete", "supersede", "unsupersede", "update", "upsert",
                         "index", "index_exchange", "reset", "clear", "_set_supersession"})

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name in _ReadOnlySource._WRITES:
            raise SourceDeletionRefused(
                f"consolidation never changes its sources (tried {name!r})")
        return getattr(self._inner, name)


def _assert_derived(table: str) -> None:
    """Only consolidation's own derived table may lose a row."""
    if table != _DERIVED_TABLE or table in SOURCE_STORES:
        raise SourceDeletionRefused(f"consolidation may not delete from {table!r}")


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.skip("memory_dreaming"))
    except Exception:
        return False


def _special_category(sentence: str) -> Optional[str]:
    """The special category `sentence` touches, or None. The shared category
    lists live in services/setup_research; without them nothing is skipped."""
    try:
        from agent_friday.services.setup_research import sensitive_category
    except Exception:
        return None
    try:
        cat = sensitive_category(sentence)
    except Exception:
        return None
    return cat if cat in _SPECIAL else None


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
        """
    )
    # Added for NS-15.9-1; a database from before them gains the columns here.
    # A row without a principal predates it and is the owner's.
    for col in ("principal TEXT", "meta_json TEXT"):
        try:
            conn.execute(f"ALTER TABLE dreams ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass                                # already there
    conn.commit()
    return conn


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
        # Off the record nothing about the conversation is written (services/off_record).
        return {"ok": True, "skipped": True, "reason": "off_record", "day": day}
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
            return {"ok": True, "day": day, "turns_reviewed": 0,
                    "topics": [], "consolidated": [], "pruned": 0, "capped": capped,
                    "principal": PRINCIPAL, "source_message_ids": [],
                    "summary": "Nothing to consolidate." if not capped else
                    "Nothing to consolidate (turn window saturated — may be incomplete)."}

        topics = _extract_topics(turns)
        facts = _mine_facts(turns)
        pruned = _count_noise(turns)
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
    except SourceDeletionRefused:
        raise
    except Exception as e:
        return {"ok": False, "day": day, "error": exception_text(e)}


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
        for sentence in _split_sentences(text):
            if _special_category(sentence):
                # Health or sexuality is never turned into a suggestion.
                continue
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


def _write_markdown(day, n_turns, topics, consolidated, pruned, summary, meta=None) -> None:
    meta = dict(meta or {})
    try:
        DREAMS_DIR.mkdir(parents=True, exist_ok=True)
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
        (DREAMS_DIR / f"{day}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        # The same record as data, with every source pointer.
        record = {"day": day, "turns_reviewed": n_turns, "pruned": pruned,
                  "summary": summary, "topics": topics, "consolidated": consolidated}
        record.update(meta)
        (DREAMS_DIR / f"{day}.json").write_text(json.dumps(record, indent=2),
                                                encoding="utf-8")
    except Exception:
        pass


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default
