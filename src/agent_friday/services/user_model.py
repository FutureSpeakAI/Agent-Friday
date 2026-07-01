"""
Agent Friday — User Modeling
FutureSpeak.AI · Asimov's Mind

Tracks who the user is and how they like to work, then injects a compact model
into every system prompt so Friday personalizes without being told twice.

What it learns (all local, all heuristic — no cloud, no LLM):
  • Communication style  — formality, verbosity (from how the user writes).
  • Domain expertise     — per-domain novice→expert (from vocabulary + asks).
  • Workflow patterns    — active hours, top tools, top workspaces.
  • Durable facts        — preferences/decisions surfaced by memory dreaming.

Persistence: ``~/.friday/user_model.db`` (SQLite). The injected summary is
TIER_1 behavioral preference text — never raw PII — so it is safe for the system
prompt on any provider.

Leaf module — no Flask. Graceful degradation everywhere (returns envelopes,
never raises to the caller).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
FRIDAY_DIR = _HOME / ".friday"
DB_PATH = FRIDAY_DIR / "user_model.db"

_LOCK = threading.Lock()

# ── Heuristic lexicons ────────────────────────────────────────────────────────
_CASUAL_MARKERS = [
    "lol", "haha", "yeah", "yep", "nope", "gonna", "wanna", "kinda", "sorta",
    "cool", "awesome", "cheers", "thanks!", "thx", "😂", "🙂", "🔥", "btw", "tbh",
]
_FORMAL_MARKERS = [
    "please", "kindly", "regards", "furthermore", "however", "therefore",
    "additionally", "would you", "could you please", "i would like to",
]
# Domain vocabularies — presence signals engagement/expertise in that domain.
_DOMAINS = {
    "code": ["function", "variable", "compile", "refactor", "async", "api",
             "regex", "stack trace", "commit", "merge", "deploy", "docker",
             "python", "javascript", "typescript", "sql", "kubernetes"],
    "finance": ["portfolio", "equity", "invoice", "revenue", "margin", "budget",
                "cash flow", "valuation", "dividend", "roi", "p&l"],
    "legal": ["contract", "clause", "liability", "nda", "compliance", "statute",
              "plaintiff", "indemnif", "jurisdiction"],
    "health": ["symptom", "diagnosis", "dosage", "clinical", "cardio", "protein",
               "workout", "nutrition"],
    "writing": ["draft", "prose", "narrative", "edit", "manuscript", "byline",
                "headline", "copy", "tone"],
    "science": ["hypothesis", "experiment", "dataset", "regression", "p-value",
                "genome", "molecule", "quantum"],
}
_NOVICE_ASKS = [
    "what is", "what's a", "how do i", "can you explain", "eli5", "i don't know",
    "i'm new to", "beginner", "simple terms", "in plain english",
]


# ── DB ────────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS traits(
            key TEXT PRIMARY KEY, value TEXT, confidence REAL,
            updated_ts REAL, evidence INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS facts(
            fact_id TEXT PRIMARY KEY, ts REAL, category TEXT, text TEXT,
            confidence REAL, source TEXT
        );
        CREATE TABLE IF NOT EXISTS signals(
            sig_id TEXT PRIMARY KEY, ts REAL, kind TEXT, value TEXT
        );
        """
    )
    conn.commit()
    return conn


def _enabled() -> bool:
    try:
        from agent_friday.core import _load_settings
        return bool((_load_settings().get("user_modeling") or {}).get("enabled", True))
    except Exception:
        return True


# ── Traits ────────────────────────────────────────────────────────────────────
def set_trait(key: str, value, confidence: float = 0.6, evidence: int = 1) -> Dict[str, Any]:
    with _LOCK:
        try:
            conn = _connect()
            row = conn.execute("SELECT confidence, evidence FROM traits WHERE key=?",
                               (key,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO traits(key,value,confidence,updated_ts,evidence) "
                    "VALUES(?,?,?,?,?)",
                    (key, str(value), float(confidence), time.time(), int(evidence)))
            else:
                new_ev = int(row[1] or 0) + int(evidence)
                conn.execute(
                    "UPDATE traits SET value=?, confidence=?, updated_ts=?, evidence=? "
                    "WHERE key=?",
                    (str(value), float(confidence), time.time(), new_ev, key))
            conn.commit()
            conn.close()
            return {"ok": True, "key": key, "value": value}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _nudge_trait(key: str, target: float, weight: float = 0.15) -> None:
    """Move a 0..1 trait toward `target` by an EMA step; bump evidence + confidence.

    The whole SELECT-compute-UPDATE runs under _LOCK: the value write is a plain
    overwrite of a Python-computed EMA derived from the SELECT, so two concurrent
    observers (e.g. chat + a channel poll thread) would otherwise read the same
    value and the later committer would clobber the earlier's nudge (lost update),
    while the SQL-relative evidence+1 still counted both — drifting value vs.
    evidence apart.
    """
    try:
        with _LOCK:
            conn = _connect()
            row = conn.execute("SELECT value, evidence FROM traits WHERE key=?",
                               (key,)).fetchone()
            cur = float(row[0]) if row and _isfloat(row[0]) else 0.5
            ev = int(row[1] or 0) if row else 0
            new = round(cur + (target - cur) * weight, 4)
            new = max(0.0, min(1.0, new))
            conf = min(0.95, 0.4 + 0.03 * (ev + 1))
            conn.execute(
                "INSERT INTO traits(key,value,confidence,updated_ts,evidence) VALUES(?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=?, confidence=?, updated_ts=?, "
                "evidence=evidence+1",
                (key, str(new), conf, time.time(), ev + 1, str(new), conf, time.time()))
            conn.commit()
            conn.close()
    except Exception:
        pass


def get_trait(key: str, default=None):
    try:
        conn = _connect()
        row = conn.execute("SELECT value FROM traits WHERE key=?", (key,)).fetchone()
        conn.close()
        if row is None:
            return default
        return float(row[0]) if _isfloat(row[0]) else row[0]
    except Exception:
        return default


# ── Observation ───────────────────────────────────────────────────────────────
def observe_message(text: str, *, role: str = "user", workspace: str = "",
                    ts: Optional[float] = None) -> Dict[str, Any]:
    """Update communication + expertise signals from one user message."""
    if not _enabled() or role != "user" or not text or not text.strip():
        return {"ok": True, "skipped": True}
    low = text.lower()
    try:
        # Formality: casual vs formal marker balance.
        casual = sum(1 for m in _CASUAL_MARKERS if m in low)
        formal = sum(1 for m in _FORMAL_MARKERS if m in low)
        if casual or formal:
            target = 1.0 if formal > casual else 0.0 if casual > formal else 0.5
            _nudge_trait("comm.formality", target)
        # Verbosity: message length as a rough proxy for the user's own verbosity.
        words = len(text.split())
        vtarget = 1.0 if words > 60 else 0.0 if words < 12 else 0.5
        _nudge_trait("comm.verbosity", vtarget, weight=0.08)
        # Expertise per domain.
        for dom, vocab in _DOMAINS.items():
            hits = sum(1 for w in vocab if w in low)
            if hits:
                novice = any(p in low for p in _NOVICE_ASKS)
                target = 0.2 if novice else min(1.0, 0.55 + 0.1 * hits)
                _nudge_trait(f"expertise.{dom}", target, weight=0.12)
        if workspace:
            observe_event("workspace", workspace)
        _record_signal("message", f"{workspace}:{len(text.split())}w")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def observe_event(kind: str, value: str) -> Dict[str, Any]:
    """Record a workflow event (tool use, workspace switch, session hour)."""
    if not _enabled():
        return {"ok": True, "skipped": True}
    try:
        _record_signal(kind, str(value))
        # Active-hour histogram lives in a trait keyed by hour.
        if kind == "session":
            hour = time.localtime().tm_hour
            _bump_counter(f"workflow.hour.{hour:02d}")
        elif kind == "tool":
            _bump_counter(f"workflow.tool.{_slug(value)}")
        elif kind == "workspace":
            _bump_counter(f"workflow.workspace.{_slug(value)}")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def note_fact(category: str, text: str, *, confidence: float = 0.6,
              source: str = "dream") -> Dict[str, Any]:
    """Store a durable fact (preference/expertise/workflow/bio). Dedups by text."""
    if not text or not text.strip():
        return {"ok": False, "error": "empty fact"}
    with _LOCK:
        try:
            conn = _connect()
            norm = text.strip()
            dup = conn.execute(
                "SELECT fact_id, confidence, source FROM facts WHERE category=? AND text=?",
                (category, norm)).fetchone()
            if dup:
                fid = dup[0]
                # Reinforce confidence ONLY on genuinely NEW evidence — a
                # different source than last time. Re-running the SAME day's dream
                # (identical source, e.g. "dream:2026-06-30") must NOT inflate
                # confidence toward the cap purely from re-processing the same
                # turns; a different day mentioning the same fact legitimately does.
                if source and source != (dup[2] or ""):
                    conn.execute(
                        "UPDATE facts SET confidence=?, source=?, ts=? WHERE fact_id=?",
                        (min(0.98, float(dup[1]) + 0.1), source, time.time(), fid))
                else:
                    conn.execute("UPDATE facts SET ts=? WHERE fact_id=?",
                                 (time.time(), fid))
            else:
                fid = uuid.uuid4().hex[:12]
                conn.execute(
                    "INSERT INTO facts(fact_id,ts,category,text,confidence,source) "
                    "VALUES(?,?,?,?,?,?)",
                    (fid, time.time(), category, norm, float(confidence), source))
            conn.commit()
            conn.close()
            return {"ok": True, "fact_id": fid}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ── Rendering ─────────────────────────────────────────────────────────────────
def render_user_model_prompt(max_facts: int = 8) -> str:
    """Compact `== USER MODEL ==` block for the system prompt. Empty if nothing learned."""
    if not _enabled():
        return ""
    try:
        settings_ok = True
        try:
            from agent_friday.core import _load_settings
            settings_ok = bool((_load_settings().get("user_modeling") or {})
                               .get("inject_prompt", True))
        except Exception:
            pass
        if not settings_ok:
            return ""
        lines: List[str] = []
        formality = get_trait("comm.formality")
        verbosity = get_trait("comm.verbosity")
        if formality is not None:
            lines.append(
                "Communication style: "
                + ("formal/professional" if formality > 0.6 else
                   "casual/relaxed" if formality < 0.4 else "balanced")
                + (", prefers detail" if (verbosity or 0.5) > 0.6 else
                   ", prefers terse answers" if (verbosity or 0.5) < 0.4 else ""))
        experts = []
        novices = []
        for key, val in _all_traits().items():
            if key.startswith("expertise.") and _isfloat(val):
                dom = key.split(".", 1)[1]
                fv = float(val)
                if fv >= 0.7:
                    experts.append(dom)
                elif fv <= 0.3:
                    novices.append(dom)
        if experts:
            lines.append("Strong domains (skip basics): " + ", ".join(sorted(experts)) + ".")
        if novices:
            lines.append("Newer domains (explain more): " + ", ".join(sorted(novices)) + ".")
        top_ws = _top_counters("workflow.workspace.", n=3)
        if top_ws:
            lines.append("Most-used workspaces: " + ", ".join(top_ws) + ".")
        facts = _recent_facts(max_facts)
        for f in facts:
            lines.append(f"• {f['text']}")
        if not lines:
            return ""
        return "\n".join(lines)
    except Exception:
        return ""


def profile() -> Dict[str, Any]:
    """Full user model for the Settings UI."""
    try:
        return {
            "available": True,
            "traits": _all_traits(),
            "facts": _recent_facts(50),
            "top_workspaces": _top_counters("workflow.workspace.", n=5),
            "top_tools": _top_counters("workflow.tool.", n=8),
            "active_hours": _active_hours(),
        }
    except Exception as e:
        return {"available": False, "error": str(e), "traits": {}, "facts": []}


def forget(category: Optional[str] = None) -> Dict[str, Any]:
    """Reset the user model — all of it, or one fact category."""
    with _LOCK:
        try:
            conn = _connect()
            if category:
                conn.execute("DELETE FROM facts WHERE category=?", (category,))
            else:
                conn.execute("DELETE FROM traits")
                conn.execute("DELETE FROM facts")
                conn.execute("DELETE FROM signals")
            conn.commit()
            conn.close()
            return {"ok": True, "category": category or "all"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ── internals ─────────────────────────────────────────────────────────────────
def _record_signal(kind: str, value: str) -> None:
    try:
        conn = _connect()
        conn.execute("INSERT INTO signals(sig_id,ts,kind,value) VALUES(?,?,?,?)",
                     (uuid.uuid4().hex[:12], time.time(), kind, value))
        # cap signal table growth
        conn.execute(
            "DELETE FROM signals WHERE sig_id IN "
            "(SELECT sig_id FROM signals ORDER BY ts DESC LIMIT -1 OFFSET 5000)")
        conn.commit()
        conn.close()
    except Exception:
        pass


def _bump_counter(key: str) -> None:
    # Locked read-modify-write: the value is a Python-computed cur+1 from the
    # SELECT, so two concurrent bumps would both read cur and both write cur+1,
    # undercounting. (See _nudge_trait for the same hazard.)
    try:
        with _LOCK:
            conn = _connect()
            row = conn.execute("SELECT value FROM traits WHERE key=?", (key,)).fetchone()
            cur = int(float(row[0])) if row and _isfloat(row[0]) else 0
            val = str(cur + 1)
            conn.execute(
                "INSERT INTO traits(key,value,confidence,updated_ts,evidence) VALUES(?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=?, updated_ts=?, evidence=evidence+1",
                (key, val, 1.0, time.time(), cur + 1, val, time.time()))
            conn.commit()
            conn.close()
    except Exception:
        pass


def _all_traits() -> Dict[str, Any]:
    try:
        conn = _connect()
        rows = conn.execute("SELECT key, value FROM traits").fetchall()
        conn.close()
        return {k: v for k, v in rows}
    except Exception:
        return {}


def _recent_facts(n: int) -> List[Dict[str, Any]]:
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT category, text, confidence, source, ts FROM facts "
            "ORDER BY confidence DESC, ts DESC LIMIT ?", (int(n),)).fetchall()
        conn.close()
        return [{"category": c, "text": t, "confidence": cf, "source": s, "ts": ts}
                for c, t, cf, s, ts in rows]
    except Exception:
        return []


def _top_counters(prefix: str, n: int = 5) -> List[str]:
    traits = _all_traits()
    items = []
    for k, v in traits.items():
        if k.startswith(prefix) and _isfloat(v):
            items.append((k[len(prefix):], int(float(v))))
    items.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in items[:n]]


def _active_hours() -> Dict[str, int]:
    traits = _all_traits()
    out = {}
    for k, v in traits.items():
        if k.startswith("workflow.hour.") and _isfloat(v):
            out[k[len("workflow.hour."):]] = int(float(v))
    return dict(sorted(out.items()))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(s).lower()).strip("_")[:40] or "x"


def _isfloat(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
