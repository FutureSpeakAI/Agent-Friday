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

Persistence: ``~/.friday/dreams.db`` + human-readable ``~/.friday/dreams/<day>.md``.
Leaf module — no Flask; returns envelopes, never raises to the caller.
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
    conn.commit()
    return conn


def _yesterday() -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))


# ── Core pass ─────────────────────────────────────────────────────────────────
def dream(day: Optional[str] = None, *, memory=None) -> Dict[str, Any]:
    """Consolidate one day's conversation turns. Defaults to *yesterday* so a
    03:00 run reviews the day that just ended.

    `memory` may be injected (tests pass a stub); otherwise the process-wide
    ConversationMemory singleton is used. Degrades to a well-formed empty
    envelope when memory is unavailable.
    """
    if not _enabled():
        return {"ok": True, "skipped": True, "reason": "disabled"}
    day = str(day).strip() if day else _yesterday()
    if not _DAY_RE.match(day):
        return {"ok": False, "error": "invalid day — expected YYYY-MM-DD"}
    try:
        if memory is None:
            try:
                from agent_friday.conversation_memory import get_conversation_memory
                memory = get_conversation_memory()
            except Exception:
                memory = None
        turns, capped = _pull_turns(memory, day)
        if capped:
            _log.warning("dream(%s): recent()-window saturated at %d turns; some of "
                         "that day's turns may be unconsolidated.", day, _PULL_WINDOW)
        if not turns:
            return {"ok": True, "day": day, "turns_reviewed": 0,
                    "topics": [], "consolidated": [], "pruned": 0, "capped": capped,
                    "summary": "Nothing to consolidate." if not capped else
                    "Nothing to consolidate (turn window saturated — may be incomplete)."}

        topics = _extract_topics(turns)
        facts = _mine_facts(turns)
        pruned = _count_noise(turns)
        # NS-15.4-3: a regex is inferring. A sentence or topic that touches a
        # special category (health, sexuality, politics, religion, ...) is
        # dropped here, so it is neither queued nor written to dreams.db or
        # the day's markdown; only a count by category is kept.
        facts, topics = _drop_special(facts, topics)

        # A regex over yesterday's turns is not a review. Nothing it finds is
        # written into what Friday knows (the user model, the knowledge graph):
        # each candidate waits in the memory card's review queue, and only the
        # owner's Keep puts it on the card (services/core_memory). Queuing is
        # idempotent, so re-running a day files nothing twice.
        consolidated = []
        for f in facts:
            consolidated.append(f)
            if f["confidence"] >= 0.6:
                try:
                    from agent_friday.services import core_memory
                    core_memory.queue("user", "add", text=f["text"], source=f"dream:{day}",
                                      reason="Nightly dreaming found this in your "
                                             "conversations. Keep it only if it is true.")
                except Exception:
                    pass

        summary = _summarize(day, len(turns), topics, consolidated, pruned)
        _persist(day, len(turns), topics, consolidated, pruned, summary)
        _write_markdown(day, len(turns), topics, consolidated, pruned, summary)

        return {"ok": True, "day": day, "turns_reviewed": len(turns),
                "topics": topics, "consolidated": consolidated,
                "pruned": pruned, "capped": capped, "summary": summary}
    except Exception as e:
        return {"ok": False, "day": day, "error": exception_text(e)}


def recent_dreams(n: int = 7) -> List[Dict[str, Any]]:
    try:
        n = max(1, min(100, int(n)))
    except (TypeError, ValueError):
        n = 7
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT day, ts, turns_reviewed, topics_json, consolidated_json, "
            "pruned, summary FROM dreams ORDER BY ts DESC LIMIT ?", (int(n),)).fetchall()
        conn.close()
        out = []
        for day, ts, tr, tj, cj, pr, summ in rows:
            out.append({
                "day": day, "ts": ts, "turns_reviewed": tr,
                "topics": _loads(tj, []), "consolidated": _loads(cj, []),
                "pruned": pr, "summary": summ,
            })
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
        # A superseded turn is history, not evidence: never consolidated.
        if rdate == day and not r.get("superseded"):
            out.append(r)
    capped = len(rows) >= _PULL_WINDOW and (oldest_date is None or oldest_date >= day)
    return out, capped


def _extract_topics(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counter: Counter = Counter()
    # Prefer the keywords the memory already indexed; fall back to extracting.
    for t in turns:
        kws = t.get("topic_keywords") or []
        if not kws:
            kws = _keywords(t.get("text") or "")
        for k in kws:
            counter[k] += 1
    top = counter.most_common(_keep_topics())
    return [{"topic": k, "mentions": c} for k, c in top]


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
    seen = set()
    for t in turns:
        if t.get("role") not in (None, "user", "human"):
            # facts about the user come from the user's own turns
            continue
        text = (t.get("text") or "").strip()
        for sentence in _split_sentences(text):
            for pat, category in _FACT_PATTERNS:
                if pat.search(sentence):
                    norm = sentence.strip()[:240]
                    key = norm.lower()
                    if key in seen or len(norm) < 8:
                        continue
                    seen.add(key)
                    # confidence scales with how declarative the sentence is
                    conf = 0.7 if category in ("bio", "preference") else 0.6
                    facts.append({"category": category, "text": norm,
                                  "confidence": conf})
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


def _persist(day, n_turns, topics, consolidated, pruned, summary) -> None:
    with _LOCK:
        try:
            conn = _connect()
            # one dream row per day — replace on re-run
            conn.execute("DELETE FROM dreams WHERE day=?", (day,))
            conn.execute(
                "INSERT INTO dreams(dream_id,ts,day,turns_reviewed,topics_json,"
                "consolidated_json,pruned,summary) VALUES(?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], time.time(), day, n_turns,
                 json.dumps(topics), json.dumps(consolidated), int(pruned), summary))
            conn.commit()
            conn.close()
        except Exception:
            pass


def _render_markdown(day, n_turns, topics, consolidated, pruned, summary) -> str:
    lines = [f"# Dream — {day}", "", f"*{summary}*", "",
             f"- Turns reviewed: {n_turns}", f"- Low-value turns (counted): {pruned}",
             "", "## Top topics"]
    for t in topics:
        lines.append(f"- {t['topic']} ({t['mentions']})")
    lines += ["", "## Consolidated facts"]
    if consolidated:
        for f in consolidated:
            lines.append(f"- **[{f['category']}]** {f['text']}")
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` via a temp file and os.replace: a crash leaves
    the old file or the new one, never half of one. Raises on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_markdown(day, n_turns, topics, consolidated, pruned, summary) -> None:
    try:
        _write_atomic(DREAMS_DIR / f"{day}.md",
                      _render_markdown(day, n_turns, topics, consolidated, pruned, summary))
    except Exception:
        pass


def forget_matching(match, keyword=None, apply: bool = True) -> Dict[str, Any]:
    """Forget-person (services/forget_derived, NS-15.7-2): the consolidated
    facts whose text `match` accepts and the topics `keyword` (a predicate on
    one lowercase keyword) or `match` accepts leave each day's dream; the
    day's summary line is written again from what is left, and its
    ``dreams/<day>.md`` is rewritten from the cleaned row. A day log with no
    row loses only the lines that name the person, and its summary line
    (``*Reviewed ... Top topics: ...*``) loses the topics that name them.
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
                                    "pruned FROM dreams").fetchall()
                for did, day, tr, tj, cj, pr in rows:
                    topics, facts = _loads(tj, []), _loads(cj, [])
                    keep_t = [t for t in topics if not topic_named(t)]
                    keep_f = [f for f in facts if not match(str((f or {}).get("text") or ""))]
                    if len(keep_t) == len(topics) and len(keep_f) == len(facts):
                        continue
                    out["topics"] += len(topics) - len(keep_t)
                    out["facts"] += len(facts) - len(keep_f)
                    out["days"] += 1
                    if _DAY_RE.match(str(day or "")):
                        rewritten.add(day)          # its day log is written again from the row
                    if not apply:
                        continue
                    summary = _summarize(day, tr, keep_t, keep_f, pr)
                    conn.execute("UPDATE dreams SET topics_json=?, consolidated_json=?, summary=? "
                                 "WHERE dream_id=?", (json.dumps(keep_t), json.dumps(keep_f), summary, did))
                    if day in rewritten:
                        redo.append((day, tr, keep_t, keep_f, pr, summary))
                if apply:
                    conn.commit()
            finally:
                conn.close()
        for day, tr, keep_t, keep_f, pr, summary in redo:
            try:
                _write_atomic(DREAMS_DIR / f"{day}.md", _render_markdown(day, tr, keep_t, keep_f, pr, summary))
            except Exception as e:
                out["not_removable"].append(failed(day, e))
    if DREAMS_DIR.exists():
        topic_line = re.compile(r"^- (.+) \(\d+\)$")
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
                out["days"] += 1
                out["lines"] += changed
            except Exception as e:
                out["not_removable"].append(failed(p.stem, e))
    return out


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default
