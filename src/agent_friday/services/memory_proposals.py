"""Model-driven durable-fact extraction, proposed for review before it is kept.

WHY THIS EXISTS
---------------
``services/memory_dreaming.py`` consolidates each day's turns by matching six
regexes (``i prefer``, ``we decided``, ``my deadline``, ``remember that`` ...)
against the user's own turns. It ran nightly, on schedule, reported green, and
extracted **0 durable facts from 215 turns** (recorded in
``services/liveness_audit.py``).

The patterns are not subtly mistuned. They are listening for a way of speaking
Stephen does not use. He talks to Friday in the register of a technical review
-- decisive, specific, rarely first-person-declarative in those exact shapes --
so the extractor found nothing, every night, and said it was fine.

Meanwhile ``capability_routing.memory_manager`` -- the seat that exists to put
a MODEL on this job -- had been assigned on his machine
(``arbiter-local``) and read by nothing. This module is that instruction,
carried out.

REGEX vs MODEL: they are reliable about different things
--------------------------------------------------------
A regex matching ``my deadline is Friday`` is a perfect witness to the words it
saw; it is quoting. It is a poor judge of whether the sentence deserves
remembering, which is exactly why it produced nothing useful.

A model saying "Stephen prefers concise answers" is INTERPRETING, possibly
across several turns, and may describe something he never literally said. It is
the better judge of durability and the worse witness to the text.

So model facts enter at LOWER confidence than pattern matches -- not because
the regex is more trustworthy, but because the two are certain about different
axes and only one of them is quoting. ``MODEL_CONFIDENCE`` sits deliberately
below the 0.6 threshold ``memory_dreaming.dream()`` already uses to decide what
reaches ``user_model.note_fact`` and the knowledge graph. That existing gate is
the review boundary; nothing new was built to hold it.

MANUAL FIRST, NIGHTLY OFF
-------------------------
Nothing here is scheduled. ``propose()`` is something the user RUNS, and its
output is shown to him before any of it becomes durable. A model reading his
private conversations and writing conclusions into his long-term memory,
unattended, with nobody checking, is a thing that degrades silently if it is
wrong -- and this codebase's whole problem is things that degrade silently.

He turns the schedule on later, if he reads a batch and believes it.

THE SEAT IS PINNED. THERE IS NO FALLBACK.
-----------------------------------------
This deliberately does NOT use ``model_router._generate_text``, which tries the
routed provider and then falls back through EVERY other provider so generation
"never hard-fails". On an unattended job over a growing history that helper is
a billing hazard: one transient llama-server hiccup and consolidating his own
diary silently walks the chain to Anthropic.

Here, if the assigned seat cannot answer, the run FAILS and stores nothing.
A loud failure costs him a retry. A silent fallback costs him money.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from agent_friday.core import FRIDAY_DIR

_log = logging.getLogger("friday.memory_proposals")

DB_PATH = FRIDAY_DIR / "dreams.db"          # shares the dreaming store

#: Model-extracted facts enter below memory_dreaming's 0.6 durability gate, so
#: they are PROPOSED rather than kept. See the module docstring for why this is
#: not a statement that the regex is more reliable.
MODEL_CONFIDENCE = 0.45

#: What an approved fact is worth. A human confirming it outranks both a
#: pattern match (0.6/0.7) and the model's own guess.
APPROVED_CONFIDENCE = 0.8

#: Bounds. A growing history must not turn into an unbounded prompt.
MAX_TURNS = 400
MAX_CHARS = 40000
MAX_FACTS = 40

_CATEGORIES = ("preference", "bio", "workflow", "project", "relationship",
               "constraint")

_PROMPT = """You are reading a day of conversation between a user and their \
assistant. Extract only DURABLE facts about the USER -- things that will still \
be true next month and that an assistant should remember.

Include: stated preferences, how they want to be worked with, their tools, \
projects, deadlines, people they work with, constraints they operate under, \
decisions they made that will persist.

Exclude: anything about the assistant, transient task detail, one-off \
questions, anything you are inferring with low confidence, and anything that \
is merely a topic rather than a fact about the person.

Return ONLY a JSON array, no prose, no code fence. Each element:
  {{"category": one of {cats}, "text": "<one sentence, third person, about the user>", "evidence": "<a short quote from the conversation>"}}

If there are no durable facts, return []. An empty array is a valid and \
useful answer -- do not invent facts to fill it.

CONVERSATION:
{convo}
"""


# ── Seat ─────────────────────────────────────────────────────────────────────
def seat() -> Dict[str, Any]:
    """The model assigned to capability_routing.memory_manager.

    This is THE consumer of that seat. Before this module it was declared,
    defaulted, mirrored, labelled in the picker, assigned on Stephen's machine
    -- and read by nothing.
    """
    try:
        from agent_friday.core import _load_settings
        cr = (_load_settings() or {}).get("capability_routing") or {}
        entry = cr.get("memory_manager") or {}
        model = (entry.get("model") or "").strip()
        provider = (entry.get("provider") or "").strip()
    except Exception as exc:                                # noqa: BLE001
        return {"assigned": False, "model": "", "provider": "",
                "reason": f"settings unreadable: {exc}"}
    if not model:
        return {"assigned": False, "model": "", "provider": provider,
                "reason": "No model assigned to the memory_manager seat. "
                          "Settings -> Intelligence -> Memory keeper."}
    return {"assigned": True, "model": model, "provider": provider,
            "reason": ""}


def _is_local(provider: str) -> bool:
    try:
        from agent_friday.routing.model_router import ModelRouter
        return provider in ModelRouter._LOCAL_PROVIDERS
    except Exception:
        return provider in {"ollama-local", "arbiter-local", "llama-cpp-local",
                            "local"}


# ── Pinned call ──────────────────────────────────────────────────────────────
class SeatUnavailable(RuntimeError):
    """The assigned seat could not answer. We do NOT fall back to a paid one."""


def _ask_seat(prompt: str, model: str, provider: str, *,
              max_tokens: int = 2048) -> str:
    """Single-shot call to the assigned seat. Raises rather than falling back.

    Cloud seats are refused outright for now: this path reads the user's whole
    private conversation history, and shipping that to a paid API on a job he
    has not yet chosen to trust is not a default anyone should get by accident.
    """
    if not _is_local(provider):
        raise SeatUnavailable(
            f"the memory_manager seat is assigned to a non-local provider "
            f"({provider!r}). This path is local-only for now: it reads your "
            f"full conversation history, and sending that to a paid API is not "
            f"something to enable by default. Assign a local model, or say so "
            f"explicitly and this restriction can be lifted.")
    try:
        from agent_friday.services.model_router import _call_ollama
    except Exception as exc:                                # noqa: BLE001
        raise SeatUnavailable(f"local transport unavailable: {exc}") from exc

    try:
        text, _trace = _call_ollama(
            [{"role": "user", "content": prompt}],
            model=model, max_tokens=max_tokens, temperature=0.1,
            orb_label="🧠 Reading the day", tools=None)
    except Exception as exc:                                # noqa: BLE001
        # No fallback chain. This is the point of the module.
        raise SeatUnavailable(
            f"the assigned seat ({model}) could not answer: {exc}. Nothing was "
            f"stored. This run does not fall back to a cloud model -- that is "
            f"deliberate.") from exc
    if not (text or "").strip():
        raise SeatUnavailable(
            f"the assigned seat ({model}) returned an empty response. Nothing "
            f"was stored.")
    return text


# ── Parsing ──────────────────────────────────────────────────────────────────
def _parse_facts(raw: str) -> List[Dict[str, Any]]:
    """Pull a JSON array of facts out of a model response. Tolerant, bounded."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    data = None
    try:
        data = json.loads(text)
    except Exception:                                       # noqa: BLE001
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:                               # noqa: BLE001
                data = None
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in data[:MAX_FACTS]:
        if not isinstance(item, dict):
            continue
        body = str(item.get("text") or "").strip()
        if len(body) < 8:
            continue
        key = body.lower()
        if key in seen:
            continue
        seen.add(key)
        cat = str(item.get("category") or "").strip().lower()
        if cat not in _CATEGORIES:
            cat = "preference"
        out.append({
            "category": cat,
            "text": body[:400],
            "evidence": str(item.get("evidence") or "").strip()[:400],
            "confidence": MODEL_CONFIDENCE,
        })
    return out


def _render_turns(turns: List[Dict[str, Any]]) -> str:
    lines = []
    total = 0
    for t in turns[-MAX_TURNS:]:
        role = (t.get("role") or "user").strip() or "user"
        body = (t.get("text") or "").strip()
        if not body:
            continue
        chunk = f"{role.upper()}: {body}"
        total += len(chunk)
        if total > MAX_CHARS:
            break
        lines.append(chunk)
    return "\n".join(lines)


# ── Store ────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS proposed_facts(
            fact_id TEXT PRIMARY KEY, ts REAL, day TEXT, category TEXT,
            text TEXT, evidence TEXT, confidence REAL, model TEXT,
            status TEXT DEFAULT 'pending', decided_ts REAL
        );
        CREATE INDEX IF NOT EXISTS idx_pf_status ON proposed_facts(status);
        CREATE INDEX IF NOT EXISTS idx_pf_day ON proposed_facts(day);
        """
    )
    conn.commit()
    return conn


def _already_proposed(conn, text: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM proposed_facts WHERE lower(text)=lower(?) LIMIT 1",
        (text,)).fetchone()
    return bool(row)


# ── Public API ───────────────────────────────────────────────────────────────
def propose(day: Optional[str] = None, *, memory=None,
            store: bool = True) -> Dict[str, Any]:
    """Read a day (or the recent window) and PROPOSE durable facts.

    Nothing here becomes durable. Facts are stored at MODEL_CONFIDENCE, which
    is below the 0.6 threshold ``memory_dreaming`` uses for
    ``user_model.note_fact``, and wait for ``approve()``.

    A run that reads turns and proposes nothing is reported as ``barren`` and
    logged at WARNING. That is the entire reason this module exists: the thing
    it replaces reported success while producing nothing, every night, for
    months.
    """
    s = seat()
    if not s["assigned"]:
        return {"ok": False, "reason": s["reason"], "seat": s,
                "facts": [], "stored": 0}

    from agent_friday.services import memory_dreaming as md
    day = str(day).strip() if day else md._yesterday()
    if not md._DAY_RE.match(day):
        return {"ok": False, "reason": "invalid day — expected YYYY-MM-DD",
                "seat": s, "facts": [], "stored": 0}

    if memory is None:
        try:
            from agent_friday.conversation_memory import get_conversation_memory
            memory = get_conversation_memory()
        except Exception:                                   # noqa: BLE001
            memory = None

    turns, capped = md._pull_turns(memory, day)
    if not turns:
        return {"ok": True, "day": day, "seat": s, "turns_reviewed": 0,
                "facts": [], "stored": 0, "barren": False, "capped": capped,
                "summary": f"No conversation turns found for {day}."}

    convo = _render_turns(turns)
    prompt = _PROMPT.format(cats=list(_CATEGORIES), convo=convo)

    t0 = time.time()
    try:
        raw = _ask_seat(prompt, s["model"], s["provider"])
    except SeatUnavailable as exc:
        _log.warning("memory proposal FAILED for %s: %s", day, exc)
        return {"ok": False, "day": day, "seat": s,
                "turns_reviewed": len(turns), "facts": [], "stored": 0,
                "reason": str(exc),
                "summary": f"Reviewed nothing for {day} — {exc}"}
    took_ms = int((time.time() - t0) * 1000)

    facts = _parse_facts(raw)

    # A barren run is NOT a success. The regex extractor's whole failure was
    # looking like one 215 turns at a time.
    barren = bool(turns) and not facts
    if barren:
        _log.warning(
            "memory proposal for %s read %d turns and proposed NOTHING. That "
            "is a result worth looking at, not a green tick. Model=%s, "
            "raw response began: %r",
            day, len(turns), s["model"], (raw or "")[:200])

    stored = 0
    if store and facts:
        conn = _connect()
        try:
            for f in facts:
                if _already_proposed(conn, f["text"]):
                    continue
                conn.execute(
                    "INSERT INTO proposed_facts (fact_id, ts, day, category, "
                    "text, evidence, confidence, model, status) "
                    "VALUES (?,?,?,?,?,?,?,?,'pending')",
                    (uuid.uuid4().hex[:16], time.time(), day, f["category"],
                     f["text"], f["evidence"], f["confidence"], s["model"]))
                stored += 1
            conn.commit()
        finally:
            conn.close()

    if barren:
        summary = (f"Read {len(turns)} turns from {day} and found no durable "
                   f"facts. Nothing was stored. If that looks wrong, it "
                   f"probably is — check the seat model.")
    else:
        summary = (f"Read {len(turns)} turns from {day}. Proposed "
                   f"{len(facts)} fact(s), {stored} new and awaiting your "
                   f"review. None are in memory yet.")

    return {"ok": True, "day": day, "seat": s, "turns_reviewed": len(turns),
            "facts": facts, "stored": stored, "barren": barren,
            "capped": capped, "took_ms": took_ms, "summary": summary}


def pending(day: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """Facts proposed and not yet decided. Nothing here is in memory."""
    conn = _connect()
    try:
        sql = ("SELECT fact_id, ts, day, category, text, evidence, confidence, "
               "model FROM proposed_facts WHERE status='pending'")
        args: List[Any] = []
        if day:
            sql += " AND day=?"
            args.append(day)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(max(1, min(1000, int(limit))))
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    cols = ("fact_id", "ts", "day", "category", "text", "evidence",
            "confidence", "model")
    return [dict(zip(cols, r)) for r in rows]


def approve(fact_ids: List[str]) -> Dict[str, Any]:
    """Promote reviewed facts into durable memory.

    Approval is the human signal, so an approved fact carries
    APPROVED_CONFIDENCE -- above both a pattern match and the model's guess.
    """
    ids = [str(i) for i in (fact_ids or []) if str(i).strip()]
    if not ids:
        return {"ok": True, "approved": 0, "facts": []}
    conn = _connect()
    promoted = []
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT fact_id, day, category, text FROM proposed_facts "
            f"WHERE fact_id IN ({marks}) AND status='pending'", ids).fetchall()
        for fact_id, day, category, text in rows:
            try:
                from agent_friday.services import user_model
                user_model.note_fact(category, text,
                                     confidence=APPROVED_CONFIDENCE,
                                     source=f"proposed:{day}")
            except Exception as exc:                        # noqa: BLE001
                _log.warning("approve(%s): user_model rejected it: %s",
                             fact_id, exc)
                continue
            try:
                from agent_friday.services.knowledge_graph.integration import (
                    ingest_fact)
                ingest_fact(text, source_kind="conversation",
                            source_key=f"proposed:{day}", category=category)
            except Exception:                               # noqa: BLE001
                pass
            conn.execute("UPDATE proposed_facts SET status='approved', "
                         "decided_ts=? WHERE fact_id=?", (time.time(), fact_id))
            promoted.append({"fact_id": fact_id, "category": category,
                             "text": text})
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "approved": len(promoted), "facts": promoted}


def reject(fact_ids: List[str]) -> Dict[str, Any]:
    """Discard proposals. They stay in the table as a record of what was said."""
    ids = [str(i) for i in (fact_ids or []) if str(i).strip()]
    if not ids:
        return {"ok": True, "rejected": 0}
    conn = _connect()
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"UPDATE proposed_facts SET status='rejected', decided_ts=? "
            f"WHERE fact_id IN ({marks}) AND status='pending'",
            [time.time(), *ids])
        conn.commit()
        return {"ok": True, "rejected": cur.rowcount}
    finally:
        conn.close()


def state() -> Dict[str, Any]:
    """Counts by status, plus whether the seat is usable. For a status surface."""
    s = seat()
    conn = _connect()
    try:
        rows = conn.execute("SELECT status, COUNT(*) FROM proposed_facts "
                            "GROUP BY status").fetchall()
    finally:
        conn.close()
    counts = {r[0]: r[1] for r in rows}
    return {"seat": s,
            "seat_usable": bool(s["assigned"] and _is_local(s["provider"])),
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "scheduled": False,
            "note": "Manual only. Nothing here runs unattended, and nothing "
                    "reaches durable memory without being approved."}
