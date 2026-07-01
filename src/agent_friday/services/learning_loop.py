"""
Agent Friday — Learning Loop Engine
FutureSpeak.AI · Asimov's Mind

A local, closed-loop self-improvement engine. Friday observes which approaches
succeed for which task types, mines successful patterns into *skill candidates*,
scores them against their trial record, and promotes the best to active use —
where they are injected as advisory heuristics into the system prompt.

Hard rules (cLaws-safe):
  • Local-only. No cloud, no LLM. Pure SQLite + heuristics → Ring-0.
  • Skills are TEXT HEURISTICS, never executable code. The loop can change what
    Friday is *reminded* of, never what she is *able* to do. No new tool surface.
  • Bounded. ``max_active_skills`` caps the prompt-injection budget.

Persistence: ``~/.friday/learning.db``.
Leaf module — no Flask; every function returns an envelope and never raises.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
FRIDAY_DIR = _HOME / ".friday"
DB_PATH = FRIDAY_DIR / "learning.db"

# Reentrant: promote() holds the lock across a read-count/score/promote sequence
# and calls score_skill(), which re-acquires it — a plain Lock would deadlock.
_LOCK = threading.RLock()

_PROMOTE_THRESHOLD = 0.65
_RETIRE_THRESHOLD = 0.40
_DEFAULT_MAX_ACTIVE = 50


def _settings() -> Dict[str, Any]:
    try:
        from agent_friday.core import _load_settings
        return _load_settings().get("learning_loop") or {}
    except Exception:
        return {}


def _enabled() -> bool:
    return bool(_settings().get("enabled", True))


def _max_active() -> int:
    try:
        return int(_settings().get("max_active_skills", _DEFAULT_MAX_ACTIVE))
    except Exception:
        return _DEFAULT_MAX_ACTIVE


# ── DB ────────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS observations(
            obs_id TEXT PRIMARY KEY, ts REAL, task_type TEXT, prompt_hash TEXT,
            approach TEXT, success INTEGER, satisfaction REAL, revisions INTEGER,
            duration_s REAL, tokens INTEGER, workspace TEXT, meta_json TEXT
        );
        CREATE TABLE IF NOT EXISTS skills(
            skill_id TEXT PRIMARY KEY, name TEXT, task_type TEXT, created_ts REAL,
            pattern TEXT, status TEXT, score REAL, trials INTEGER, wins INTEGER,
            source_obs_json TEXT
        );
        CREATE TABLE IF NOT EXISTS skill_trials(
            trial_id TEXT PRIMARY KEY, skill_id TEXT, ts REAL, success INTEGER,
            satisfaction REAL, note TEXT
        );
        -- Enforce the dedup invariant in the schema so a check-then-insert race
        -- can't create two skills for the same pattern (INSERT OR IGNORE below).
        CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_pattern ON skills(pattern);
        """
    )
    conn.commit()
    return conn


# ── Observation ───────────────────────────────────────────────────────────────
def observe(task_type: str, prompt: str, *, approach: str, success: bool,
            satisfaction: Optional[float] = None, revisions: int = 0,
            duration_s: float = 0.0, tokens: int = 0, workspace: str = "",
            meta: Optional[dict] = None) -> Dict[str, Any]:
    """Record one task outcome. Best-effort; safe to fire-and-forget."""
    if not _enabled():
        return {"ok": True, "skipped": True}
    try:
        import json
        obs_id = uuid.uuid4().hex[:12]
        ph = hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16]
        sat = _clamp01(satisfaction) if satisfaction is not None else (
            0.85 if success else 0.2)
        # Defensive input coercion — a caller passing garbage types must degrade
        # to safe defaults, never crash the observation path or poison a row.
        revisions = _coerce_int(revisions, 0)
        duration_s = _coerce_float(duration_s, 0.0)
        tokens = _coerce_int(tokens, 0)
        meta_json = json.dumps(meta or {}, default=str)
        if len(meta_json) > 4096:  # cap meta blob growth from unbounded dicts
            meta_json = "{}"
        with _LOCK:
            conn = _connect()
            conn.execute(
                "INSERT INTO observations(obs_id,ts,task_type,prompt_hash,approach,"
                "success,satisfaction,revisions,duration_s,tokens,workspace,meta_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (obs_id, time.time(), _norm(task_type), ph, _norm(approach),
                 1 if success else 0, float(sat), revisions,
                 duration_s, tokens, _norm(workspace),
                 meta_json))
            conn.commit()
            conn.close()
        return {"ok": True, "obs_id": obs_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Candidate mining ──────────────────────────────────────────────────────────
def mine_candidates(min_success: float = 0.7, min_samples: int = 3,
                    min_distinct: int = 2, max_new: int = 20) -> List[Dict[str, Any]]:
    """Cluster observations by (task_type, approach); emit skill candidates for
    clusters whose success-rate and sample-count clear the floor. Idempotent —
    a pattern that already exists is skipped (UNIQUE index + INSERT OR IGNORE).

    Anti-flood: a bucket must contain at least ``min_distinct`` DISTINCT prompts
    (so N near-identical trivial observations can't mint a promotable skill), and
    at most ``max_new`` candidates are minted per call (largest buckets first).
    The whole check-and-insert runs under _LOCK so a concurrent epoch + API call
    can't both insert for the same pattern.
    """
    if not _enabled():
        return []
    # Parameter hygiene — clamp caller-supplied knobs into sane bounds so a bad
    # value (API caller, corrupted settings) can't mint unbounded skills.
    min_success = _clamp01(min_success)
    min_samples = max(1, _coerce_int(min_samples, 3))
    min_distinct = max(1, _coerce_int(min_distinct, 2))
    max_new = max(1, min(100, _coerce_int(max_new, 20)))
    try:
        import json
        with _LOCK:
            conn = _connect()
            rows = conn.execute(
                "SELECT task_type, approach, success, satisfaction, obs_id, prompt_hash "
                "FROM observations").fetchall()
            buckets: Dict[tuple, Dict[str, Any]] = defaultdict(
                lambda: {"n": 0, "wins": 0, "sat": 0.0, "obs": [], "hashes": set()})
            for tt, ap, succ, sat, oid, ph in rows:
                b = buckets[(tt, ap)]
                b["n"] += 1
                b["wins"] += int(succ or 0)
                b["sat"] += float(sat or 0)
                b["obs"].append(oid)
                if ph:
                    b["hashes"].add(ph)
            created = []
            # Largest buckets first so the per-call cap keeps the best-evidenced.
            for (tt, ap), b in sorted(buckets.items(), key=lambda kv: -kv[1]["n"]):
                if len(created) >= max_new:
                    break
                if b["n"] < min_samples or len(b["hashes"]) < min_distinct:
                    continue
                rate = b["wins"] / b["n"]
                if rate < min_success:
                    continue
                pattern = _pattern_text(tt, ap)
                sid = uuid.uuid4().hex[:12]
                name = f"{tt}:{ap}"[:60]
                cur = conn.execute(
                    "INSERT OR IGNORE INTO skills(skill_id,name,task_type,created_ts,"
                    "pattern,status,score,trials,wins,source_obs_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (sid, name, tt, time.time(), pattern, "candidate",
                     round(rate, 4), 0, 0, json.dumps(b["obs"][:20])))
                if cur.rowcount:  # a dup pattern is IGNOREd (rowcount 0)
                    created.append({"skill_id": sid, "name": name, "pattern": pattern,
                                    "sample_rate": round(rate, 3), "samples": b["n"]})
            conn.commit()
            conn.close()
            return created
    except Exception:
        return []


# ── Scoring ───────────────────────────────────────────────────────────────────
def record_trial(skill_id: str, success: bool, satisfaction: Optional[float] = None,
                 note: str = "") -> Dict[str, Any]:
    if not _enabled():
        return {"ok": True, "skipped": True}
    if not skill_id or not isinstance(skill_id, str):
        return {"ok": False, "error": "invalid skill_id"}
    note = str(note or "")
    try:
        sat = _clamp01(satisfaction) if satisfaction is not None else (
            0.85 if success else 0.2)
        with _LOCK:
            conn = _connect()
            conn.execute(
                "INSERT INTO skill_trials(trial_id,skill_id,ts,success,satisfaction,note)"
                " VALUES(?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], skill_id, time.time(),
                 1 if success else 0, float(sat), note[:200]))
            conn.execute(
                "UPDATE skills SET trials=trials+1, wins=wins+? WHERE skill_id=?",
                (1 if success else 0, skill_id))
            conn.commit()
            conn.close()
        new_score = score_skill(skill_id)
        return {"ok": True, "score": new_score}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def score_skill(skill_id: str) -> float:
    """Wilson lower bound over trials wins/n, blended with mean satisfaction.

    Falls back to the mined sample-rate when there are no trials yet, so a fresh
    candidate isn't scored 0 before it has had a chance to prove itself.
    """
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT trials, wins, score FROM skills WHERE skill_id=?",
            (skill_id,)).fetchone()
        sats = conn.execute(
            "SELECT satisfaction FROM skill_trials WHERE skill_id=?",
            (skill_id,)).fetchall()
        conn.close()
        if not row:
            return 0.0
        trials, wins, sample_rate = int(row[0] or 0), int(row[1] or 0), float(row[2] or 0)
        if trials == 0:
            score = sample_rate
        else:
            wilson = _wilson_lower_bound(wins, trials)
            mean_sat = sum(s[0] for s in sats) / len(sats) if sats else 0.5
            score = round(0.7 * wilson + 0.3 * mean_sat, 4)
        with _LOCK:
            conn = _connect()
            conn.execute("UPDATE skills SET score=? WHERE skill_id=?", (score, skill_id))
            conn.commit()
            conn.close()
        return score
    except Exception:
        return 0.0


# ── Promotion ─────────────────────────────────────────────────────────────────
def promote(threshold: float = _PROMOTE_THRESHOLD, min_trials: int = 3,
            retire: float = _RETIRE_THRESHOLD) -> List[Dict[str, Any]]:
    """candidate/validating → active when score clears threshold (and, once it
    has trials, min_trials met). active → retired when score decays below
    `retire`. Respects max_active_skills."""
    if not _enabled():
        return []
    threshold = _clamp01(threshold)
    retire = _clamp01(retire)
    min_trials = max(0, _coerce_int(min_trials, 3))
    changes: List[Dict[str, Any]] = []
    try:
        # Serialize the whole read-count/score/promote sequence: without this two
        # concurrent epochs (scheduler + POST /api/learning/epoch) both read
        # active_count, both see room under the cap, and both promote — blowing
        # past max_active_skills. _LOCK is an RLock so score_skill can re-enter.
        with _LOCK:
            conn = _connect()
            skills = conn.execute(
                "SELECT skill_id, status, trials FROM skills").fetchall()
            conn.close()
            active_count = _count_active()
            max_active = _max_active()
            for sid, status, trials in skills:
                sc = score_skill(sid)
                if status in ("candidate", "validating"):
                    ready = sc >= threshold and (trials == 0 or trials >= min_trials)
                    if ready and active_count < max_active:
                        _set_status(sid, "active")
                        active_count += 1
                        changes.append({"skill_id": sid, "to": "active", "score": sc})
                    elif sc >= threshold and status == "candidate":
                        _set_status(sid, "validating")
                        changes.append({"skill_id": sid, "to": "validating", "score": sc})
                elif status == "active" and sc < retire:
                    _set_status(sid, "retired")
                    active_count -= 1
                    changes.append({"skill_id": sid, "to": "retired", "score": sc})
            return changes
    except Exception:
        return []


def active_skills(task_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Promoted heuristics for system-prompt injection (bounded to max_active)."""
    if not _enabled():
        return []
    try:
        conn = _connect()
        if task_type:
            rows = conn.execute(
                "SELECT skill_id,name,pattern,score,task_type FROM skills "
                "WHERE status='active' AND task_type=? ORDER BY score DESC LIMIT ?",
                (_norm(task_type), _max_active())).fetchall()
        else:
            rows = conn.execute(
                "SELECT skill_id,name,pattern,score,task_type FROM skills "
                "WHERE status='active' ORDER BY score DESC LIMIT ?",
                (_max_active(),)).fetchall()
        conn.close()
        return [{"skill_id": s, "name": n, "pattern": p, "score": sc, "task_type": tt}
                for s, n, p, sc, tt in rows]
    except Exception:
        return []


def render_heuristics_prompt(task_type: Optional[str] = None, limit: int = 10) -> str:
    """`== LEARNED HEURISTICS ==` block body for the system prompt (bounded)."""
    limit = max(1, min(50, _coerce_int(limit, 10)))
    skills = active_skills(task_type)[:limit]
    if not skills:
        return ""
    return "\n".join(f"• {s['pattern']}" for s in skills)


# ── Epoch ─────────────────────────────────────────────────────────────────────
def run_epoch() -> Dict[str, Any]:
    """One full learning cycle — the scheduler drives this weekly."""
    if not _enabled():
        return {"ok": True, "skipped": True, "reason": "disabled"}
    try:
        mined = mine_candidates()
        changed = promote()
        st = state()
        return {"ok": True, "mined": len(mined), "promoted": changed,
                "counts": st.get("counts", {})}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def state() -> Dict[str, Any]:
    try:
        conn = _connect()
        counts = {}
        for status in ("candidate", "validating", "active", "retired"):
            counts[status] = conn.execute(
                "SELECT COUNT(*) FROM skills WHERE status=?", (status,)).fetchone()[0]
        obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        top = conn.execute(
            "SELECT name, pattern, score, status FROM skills "
            "ORDER BY score DESC LIMIT 10").fetchall()
        conn.close()
        return {
            "available": True,
            "observations": obs,
            "counts": counts,
            "top_skills": [{"name": n, "pattern": p, "score": sc, "status": stt}
                           for n, p, sc, stt in top],
            "max_active": _max_active(),
            "enabled": _enabled(),
        }
    except Exception as e:
        return {"available": False, "error": str(e), "counts": {}}


# ── internals ─────────────────────────────────────────────────────────────────
def _set_status(skill_id: str, status: str) -> None:
    try:
        conn = _connect()
        conn.execute("UPDATE skills SET status=? WHERE skill_id=?", (status, skill_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _count_active() -> int:
    try:
        conn = _connect()
        n = conn.execute("SELECT COUNT(*) FROM skills WHERE status='active'").fetchone()[0]
        conn.close()
        return int(n)
    except Exception:
        return 0


def _pattern_text(task_type: str, approach: str) -> str:
    tt = task_type.replace("_", " ")
    ap = approach.replace("_", " ")
    return f"For {tt} tasks, {ap} — this has worked well before."


def _wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    phat = wins / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return round(max(0.0, (centre - margin) / denom), 4)


def _coerce_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _coerce_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def _norm(s) -> str:
    return re.sub(r"\s+", "_", str(s or "").strip().lower())[:60] or "general"
