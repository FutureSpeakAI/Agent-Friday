"""
Agent Friday — Memory Dreaming
FutureSpeak.AI · Asimov's Mind

Overnight consolidation. While the user sleeps, Friday reviews the day's
conversation turns (from the local ChromaDB conversation memory), extracts
recurring topics and durable facts, writes consolidated long-term entries, and
tags noise for pruning.

Everything is LOCAL — heuristic pattern extraction over turns already on disk.
No cloud call, no LLM. Ring-0.

Persistence: ``~/.friday/dreams.db`` + human-readable ``~/.friday/dreams/<day>.md``.
Leaf module — no Flask; returns envelopes, never raises to the caller.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
FRIDAY_DIR = _HOME / ".friday"
DB_PATH = FRIDAY_DIR / "dreams.db"
DREAMS_DIR = FRIDAY_DIR / "dreams"

_LOCK = threading.Lock()

# Durable-fact patterns — sentences worth remembering long-term.
_FACT_PATTERNS = [
    (re.compile(r"\bi (?:prefer|like|love|hate|always|never|usually|tend to)\b", re.I), "preference"),
    (re.compile(r"\b(?:we|i) (?:decided|agreed|chose|settled on|going with)\b", re.I), "workflow"),
    (re.compile(r"\bmy (?:name|email|role|job|title|company|team|goal|deadline|birthday)\b", re.I), "bio"),
    (re.compile(r"\bremember (?:that|to)\b", re.I), "preference"),
    (re.compile(r"\bi'?m (?:working on|building|learning|trying to)\b", re.I), "workflow"),
    (re.compile(r"\bdon'?t (?:ever )?(?:show|send|use|include)\b", re.I), "preference"),
]
# Low-value turns to tag for pruning (greetings, one-word clarifications).
_NOISE_PATTERNS = [
    re.compile(r"^\s*(?:hi|hey|hello|thanks|thank you|ok|okay|yes|no|yep|nope|cool|great|got it)[!.\s]*$", re.I),
]


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
    day = day or _yesterday()
    try:
        if memory is None:
            try:
                from agent_friday.conversation_memory import get_conversation_memory
                memory = get_conversation_memory()
            except Exception:
                memory = None
        turns = _pull_turns(memory, day)
        if not turns:
            return {"ok": True, "day": day, "turns_reviewed": 0,
                    "topics": [], "consolidated": [], "pruned": 0,
                    "summary": "Nothing to consolidate."}

        topics = _extract_topics(turns)
        facts = _mine_facts(turns)
        pruned = _count_noise(turns)

        # Hand durable, high-confidence facts to the user model.
        consolidated = []
        for f in facts:
            consolidated.append(f)
            if f["confidence"] >= 0.6:
                try:
                    from agent_friday.services import user_model
                    user_model.note_fact(f["category"], f["text"],
                                          confidence=f["confidence"], source="dream")
                except Exception:
                    pass

        summary = _summarize(day, len(turns), topics, consolidated, pruned)
        _persist(day, len(turns), topics, consolidated, pruned, summary)
        _write_markdown(day, len(turns), topics, consolidated, pruned, summary)

        return {"ok": True, "day": day, "turns_reviewed": len(turns),
                "topics": topics, "consolidated": consolidated,
                "pruned": pruned, "summary": summary}
    except Exception as e:
        return {"ok": False, "day": day, "error": str(e)}


def recent_dreams(n: int = 7) -> List[Dict[str, Any]]:
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
        return {"available": False, "error": str(e)}


# ── extraction internals ──────────────────────────────────────────────────────
def _pull_turns(memory, day: str) -> List[Dict[str, Any]]:
    """All stored turns whose `date` == day. Pulls a generous recent window then
    filters, so we don't depend on memory supporting date queries."""
    if memory is None:
        return []
    try:
        rows = memory.recent(n=2000)
    except Exception:
        return []
    out = []
    for r in rows or []:
        rdate = r.get("date") or (r.get("timestamp") or "")[:10]
        if rdate == day:
            out.append(r)
    return out


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
            f"flagged {pruned} low-value turn(s).")


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


def _write_markdown(day, n_turns, topics, consolidated, pruned, summary) -> None:
    try:
        DREAMS_DIR.mkdir(parents=True, exist_ok=True)
        lines = [f"# Dream — {day}", "", f"*{summary}*", "",
                 f"- Turns reviewed: {n_turns}", f"- Low-value turns flagged: {pruned}",
                 "", "## Top topics"]
        for t in topics:
            lines.append(f"- {t['topic']} ({t['mentions']})")
        lines += ["", "## Consolidated facts"]
        if consolidated:
            for f in consolidated:
                lines.append(f"- **[{f['category']}]** {f['text']}")
        else:
            lines.append("- (none)")
        (DREAMS_DIR / f"{day}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default
