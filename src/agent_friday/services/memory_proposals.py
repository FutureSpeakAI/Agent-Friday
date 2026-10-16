"""Model-driven durable-fact extraction, proposed for review before it is kept.

WHY THIS EXISTS
---------------
``services/memory_dreaming.py`` consolidates each day's turns by matching six
regexes (``i prefer``, ``we decided``, ``my deadline``, ``remember that`` ...)
against the user's own turns. It ran nightly, on schedule, reported green, and
extracted **0 durable facts from 215 turns** (recorded in
``services/liveness_audit.py``).

The patterns are not subtly mistuned. They are listening for a way of speaking
many users do not use. A user who talks to Friday in the register of a
technical review -- decisive, specific, rarely first-person-declarative in
those exact shapes -- gives the extractor nothing, every night, and it reports
that it is fine.

Meanwhile ``capability_routing.memory_manager`` -- the seat that exists to put
a MODEL on this job -- can be assigned (e.g. ``arbiter-local``) and read by
nothing. This module is that instruction, carried out.

REGEX vs MODEL: they are reliable about different things
--------------------------------------------------------
A regex matching ``my deadline is Friday`` is a perfect witness to the words it
saw; it is quoting. It is a poor judge of whether the sentence deserves
remembering, which is exactly why it produced nothing useful.

A model saying "the user prefers concise answers" is INTERPRETING, possibly
across several turns, and may describe something they never literally said. It is
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
output is shown to them before any of it becomes durable. A model reading their
private conversations and writing conclusions into their long-term memory,
unattended, with nobody checking, is a thing that degrades silently if it is
wrong -- and this codebase's whole problem is things that degrade silently.

The user turns the schedule on later, if they read a batch and believe it.

THE SEAT IS PINNED. THERE IS NO FALLBACK.
-----------------------------------------
This deliberately does NOT use ``model_router._generate_text``, which tries the
routed provider and then falls back through EVERY other provider so generation
"never hard-fails". On an unattended job over a growing history that helper is
a billing hazard: one transient llama-server hiccup and consolidating the
user's own diary silently walks the chain to Anthropic.

Here, if the assigned seat cannot answer, the run FAILS and stores nothing.
A loud failure costs the user a retry. A silent fallback costs them money.
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
from agent_friday.user_errors import ExceptionText, exception_text
from agent_friday.services.visible_jobs import visible_job

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

    This is THE consumer of that seat. Without this module it is declared,
    defaulted, mirrored, labelled in the picker, assignable in settings
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
                "reason": ExceptionText(f"settings unreadable: {exc}")}
    if not model:
        return {"assigned": False, "model": "", "provider": provider,
                "reason": "No model assigned to the memory_manager seat. "
                          "Settings -> Models -> Memory keeper."}
    return {"assigned": True, "model": model, "provider": provider,
            "reason": ""}


def _notify_seat_refusal(reason: str) -> None:
    """Tell the user the Memory keeper stood down, and why.

    The refusal used to be a log warning and an `ok: False` return. Nobody reads
    either, so the seat sat misconfigured and the feature was simply absent --
    which is the failure mode this whole module was written to prevent. A
    capability that declines to run has to say so where the user is.
    """
    try:
        from agent_friday.services.voice_engine import _notif_engine
    except Exception:
        return
    if not _notif_engine:
        return
    try:
        _notif_engine.push(
            title="Memory keeper stood down — nothing was sent",
            body=reason,
            priority="high", source="memory_proposals", kind="warning",
            dedupe_key="memory_keeper_seat_refusal",
        )
    except Exception:
        pass


def _is_local(provider: str, model: str = "") -> bool:
    """Delegates to `seat_policy`, which is where the local-only rule lives.

    This used to carry its own copy of the provider set. Two copies of one fact
    drift, and the drift here would mean the label and the enforcement disagreeing
    about what "local" means.
    """
    try:
        from agent_friday.services.seat_policy import is_local_seat
        return is_local_seat(provider, model)
    except Exception:
        return False


# ── Pinned call ──────────────────────────────────────────────────────────────
class SeatUnavailable(RuntimeError):
    """The assigned seat could not answer. We do NOT fall back to a paid one."""


def _ask_seat(prompt: str, model: str, provider: str, *,
              max_tokens: int = 2048) -> str:
    """Single-shot call to the assigned seat. Raises rather than falling back.

    Cloud seats are refused outright for now: this path reads the user's whole
    private conversation history, and shipping that to a paid API on a job they
    have not yet chosen to trust is not a default anyone should get by accident.
    """
    if not _is_local(provider, model):
        raise SeatUnavailable(
            f"the memory_manager seat cannot verify this model and provider "
            f"as local ({provider!r}/{model!r}). This path is local-only for now: it reads your "
            f"full conversation history, and sending that to a paid API is not "
            f"something to enable by default. Assign a local model, or say so "
            f"explicitly and this restriction can be lifted.")
    try:
        from agent_friday.services.model_router import _call_ollama
        from agent_friday.services.provider_registry import get_provider_registry
        registry = get_provider_registry()
        name = str(provider or "").strip()
        descriptor = registry.get_provider(name) or registry.get_provider(name.lower())
        resolved_name = (descriptor or {}).get("name") or name
        origin = getattr(registry, "provider_origin", lambda _: "unknown")(resolved_name)
        # The shipped daemon name is also the legacy local-discovery alias:
        # its settings URL or owned model seat may differ from the default.
        # A file/UI override is an explicit assignment and remains pinned.
        if name.lower() == "ollama-local" and origin == "builtin":
            descriptor = None
    except Exception as exc:                                # noqa: BLE001
        raise SeatUnavailable(f"local transport unavailable: {exc}") from exc

    try:
        from agent_friday.services.local_only_guard import local_only
        with local_only("Memory keeper"):
            text, _trace = _call_ollama(
                [{"role": "user", "content": prompt}],
                model=model, max_tokens=max_tokens, temperature=0.1,
                orb_label="🧠 Reading the day", tools=None, provider=descriptor)
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
    # Where a proposal came from, when it came from more than a day of chat: a
    # meeting's id, the transcript span and the model that read it (JSON).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(proposed_facts)")}
    if "provenance" not in cols:
        conn.execute("ALTER TABLE proposed_facts ADD COLUMN provenance TEXT")
    conn.commit()
    return conn


def _already_proposed(conn, text: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM proposed_facts WHERE lower(text)=lower(?) LIMIT 1",
        (text,)).fetchone()
    return bool(row)


# ── Public API ───────────────────────────────────────────────────────────────
@visible_job('Memory proposals', kind='memory_proposals', workspace='memory')
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
        _notify_seat_refusal(exception_text(exc))
        return {"ok": False, "day": day, "seat": s,
                "turns_reviewed": len(turns), "facts": [], "stored": 0,
                "reason": exception_text(exc),
                "summary": ExceptionText(f"Reviewed nothing for {day} — {exc}")}
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


#: What a meeting may propose. A decision is not one of the day-review
#: categories above (those stay as they were); it is stored under its own word.
MEETING_CATEGORIES = ("relationship", "project", "decision")


def propose_from_meeting(candidates: List[Dict[str, Any]], meeting_id: str, day: str,
                         model: str) -> int:
    """Store people, decisions and projects from a meeting as PENDING proposals.

    The same table, review and confidence as every other proposal
    (``MODEL_CONFIDENCE``, under the 0.6 durability gate): nothing here reaches
    ``user_model`` or the knowledge graph; only ``approve()`` does, on the
    owner's word. Each carries provenance: the meeting id, the transcript span
    it traces to and the model that read it. A candidate with no span is not
    stored, because a proposal that cannot be traced is not worth a review.
    Returns how many were stored.
    """
    stored = 0
    conn = _connect()
    try:
        for c in (candidates or [])[:MAX_FACTS]:
            text = str(c.get("text") or "").strip()[:400]
            cat = str(c.get("category") or "").strip().lower()
            span = c.get("span") if isinstance(c.get("span"), dict) else None
            if len(text) < 4 or cat not in MEETING_CATEGORIES or not span:
                continue
            if _already_proposed(conn, text) or _already_from_meeting(conn, str(meeting_id), text):
                continue
            executing = str(c.get("model") or model)
            prov = json.dumps({"meeting_id": str(meeting_id),
                               "span": {"segment": int(span.get("i")), "t": float(span.get("t") or 0)},
                               "model": executing}, ensure_ascii=False)
            conn.execute(
                "INSERT INTO proposed_facts (fact_id, ts, day, category, "
                "text, evidence, confidence, model, status, provenance) "
                "VALUES (?,?,?,?,?,?,?,?,'pending',?)",
                (uuid.uuid4().hex[:16], time.time(), str(day), cat, _seal(text),
                 _seal(str(c.get("evidence") or "")[:400]), MODEL_CONFIDENCE, executing, prov))
            stored += 1
        conn.commit()
    finally:
        conn.close()
    return stored


def pending(day: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """Facts proposed and not yet decided. Nothing here is in memory."""
    conn = _connect()
    try:
        sql = ("SELECT fact_id, ts, day, category, text, evidence, confidence, "
               "model, provenance FROM proposed_facts WHERE status='pending'")
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
    out = []
    for r in rows:
        d = dict(zip(cols, r[:8]))
        was_sealed = isinstance(d.get("text"), str) and d["text"].startswith(_SEALED)
        d["text"], d["evidence"] = _open(d.get("text")), _open(d.get("evidence"))
        if was_sealed and not d["text"]:
            d["text"] = "(locked)"           # sealed and the keystore cannot open it: not a blank line
        if r[8]:
            try:
                d["provenance"] = json.loads(r[8])
            except ValueError:
                pass
        out.append(d)
    return out


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
    unopened: List[str] = []
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT fact_id, day, category, text, provenance FROM proposed_facts "
            f"WHERE fact_id IN ({marks}) AND status='pending'", ids).fetchall()
        for fact_id, day, category, text, prov in rows:
            sealed = isinstance(text, str) and text.startswith(_SEALED)
            text = _open(text)
            if sealed and not text.strip():
                unopened.append(fact_id)          # the keystore cannot open it: never an empty fact, never a blanked row
                continue
            origin = f"proposed:{day}"
            if prov:
                try:
                    origin = "meeting:%s" % json.loads(prov).get("meeting_id")
                except ValueError:
                    pass
            try:
                from agent_friday.services import user_model
                user_model.note_fact(category, text,
                                     confidence=APPROVED_CONFIDENCE,
                                     source=origin)
            except Exception as exc:                        # noqa: BLE001
                _log.warning("approve(%s): user_model rejected it: %s",
                             fact_id, exc)
                continue
            # The owner kept it, so it goes on the memory card, which is what
            # rides in Friday's prompt (the USER MODEL lists no facts now). A
            # full card leaves it in the review queue rather than dropping it.
            try:
                from agent_friday.services import core_memory
                _cm = core_memory.add("user", text, actor="owner")
                if not _cm.get("ok") and _cm.get("full"):
                    core_memory.queue("user", "add", text=text, source=origin,
                                      reason="You kept this, but the memory card was full.")
            except Exception:                               # noqa: BLE001
                pass
            try:
                from agent_friday.services.knowledge_graph.integration import (
                    ingest_fact)
                ingest_fact(text, source_kind="conversation",
                            source_key=origin, category=category)
            except Exception:                               # noqa: BLE001
                pass
            conn.execute("UPDATE proposed_facts SET status='approved', "
                         "decided_ts=? WHERE fact_id=?", (time.time(), fact_id))
            if _meeting_id_of(prov):
                # a kept fact lives on in what Friday knows, under its own protection: the row keeps no copy
                conn.execute("UPDATE proposed_facts SET text='', evidence='' WHERE fact_id=?", (fact_id,))
            promoted.append({"fact_id": fact_id, "category": category,
                             "text": text})
        conn.commit()
    finally:
        conn.close()
    out = {"ok": True, "approved": len(promoted), "facts": promoted}
    if unopened:
        out.update(ok=bool(promoted), refused=unopened,
                   message="This suggestion cannot be opened on this PC right now (its key is not available), "
                           "so it was not kept. Nothing was changed.")
    return out


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
        conn.execute(f"UPDATE proposed_facts SET text='', evidence='' WHERE fact_id IN ({marks}) "
                     f"AND status='rejected' AND provenance IS NOT NULL AND provenance != ''", ids)   # a decided meeting suggestion keeps no text
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


_SEALED = "enc:"


def _seal(text: str) -> str:
    """A meeting's suggestion is meeting content: it sits in this plain-file table only sealed with the same keystore
    the meeting's own record uses."""
    import base64
    from agent_friday.services import credential_store as cs
    blob, _method = cs.protect(str(text or "").encode("utf-8"))
    return _SEALED + base64.b64encode(blob).decode("ascii")


def _open(text) -> str:
    if not isinstance(text, str) or not text.startswith(_SEALED):
        return text if isinstance(text, str) else ""
    try:
        import base64
        from agent_friday.services import credential_store as cs
        return cs.unprotect(base64.b64decode(text[len(_SEALED):])).decode("utf-8")
    except Exception:  # noqa: BLE001 - what cannot be opened is shown as nothing
        return ""


# ── Meeting proposals leave with their meeting ───────────────────────────────
# A proposal from a meeting sits in this plain table, outside the meeting's encrypted record. It carries no
# transcript line and no title (meeting_workspace.proposal_candidates), but it does carry a decision, a name
# or a project. So it goes with the meeting: on delete, on retention, on "off", and on forget-person.

def _meeting_id_of(provenance) -> str:
    try:
        return str((json.loads(provenance) or {}).get("meeting_id") or "")
    except (TypeError, ValueError, AttributeError):
        return ""


def purge_meeting(meeting_id: Optional[str] = None) -> int:
    """Delete every proposal that came from the meeting (any status), or from any meeting when `meeting_id`
    is None. Returns how many rows went."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT fact_id, provenance FROM proposed_facts "
                            "WHERE provenance IS NOT NULL AND provenance != ''").fetchall()
        ids = [fid for fid, prov in rows
               if _meeting_id_of(prov) and (meeting_id is None or _meeting_id_of(prov) == str(meeting_id))]
        for fid in ids:
            conn.execute("DELETE FROM proposed_facts WHERE fact_id=?", (fid,))
        conn.commit()
        return len(ids)
    finally:
        conn.close()


LOCKED_MESSAGE = ("could not check meeting suggestions: the keystore that seals them is not available, so some "
                  "were not looked at")


class SuggestionsLocked(RuntimeError):
    pass


def _name_rows(conn, names) -> tuple:
    wanted = [n for n in {" ".join(str(x or "").split()).lower() for x in (names or [])} if len(n) >= 3]
    if not wanted:
        return [], 0
    pats = [re.compile(r"\b" + r"\s+".join(re.escape(w) for w in n.split(" ")) + r"\b", re.IGNORECASE) for n in wanted]
    out, locked = [], 0
    for fid, text, evidence in conn.execute("SELECT fact_id, text, evidence FROM proposed_facts").fetchall():
        opened = _open(text)
        if isinstance(text, str) and text.startswith(_SEALED) and not opened:
            locked += 1                      # sealed and not openable: unknown, never "does not name them"
            continue
        hay = "%s %s" % (opened, _open(evidence))
        if any(p.search(hay) for p in pats):
            out.append(fid)
    return out, locked


def _already_from_meeting(conn, meeting_id: str, text: str) -> bool:
    for fid, t, prov in conn.execute("SELECT fact_id, text, provenance FROM proposed_facts "
                                     "WHERE provenance IS NOT NULL AND provenance != ''").fetchall():
        if _meeting_id_of(prov) == meeting_id and _open(t).strip().lower() == text.strip().lower():
            return True
    return False


def people_find(names, emails) -> int:
    """forget-person: how many proposals name the person (any status)."""
    conn = _connect()
    try:
        ids, locked = _name_rows(conn, set(names or ()) | set(emails or ()))
        if locked:
            raise SuggestionsLocked(LOCKED_MESSAGE)
        return len(ids)
    finally:
        conn.close()


def people_purge(names, emails) -> int:
    conn = _connect()
    try:
        ids, locked = _name_rows(conn, set(names or ()) | set(emails or ()))
        for fid in ids:
            conn.execute("DELETE FROM proposed_facts WHERE fact_id=?", (fid,))
        conn.commit()
        if locked:
            raise SuggestionsLocked(LOCKED_MESSAGE)      # what could be opened is purged; the rest is reported
        return len(ids)
    finally:
        conn.close()


def any_from_meeting(fact_ids) -> bool:
    """Is any of these proposals one a meeting made? (Approving one needs a click on the screen.)"""
    ids = [str(i) for i in (fact_ids or []) if str(i).strip()]
    if not ids:
        return False
    conn = _connect()
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(f"SELECT provenance FROM proposed_facts WHERE fact_id IN ({marks})", ids).fetchall()
        return any(_meeting_id_of(r[0]) for r in rows)
    finally:
        conn.close()
