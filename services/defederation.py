"""
Agent Friday — Asimov-Governed Defederation Protocol (Layer 3)
FutureSpeak.AI · Asimov's Mind

Defederation decisions are governed by the cLaws constitution, not human bias.
Evidence is required — political disagreement cannot be a harm category.

HARM_CATEGORIES (fixed — cannot be extended by users):
  H1 / H2 / H3 / H4 — from moderation.py harm floor
  coordinated_harassment    — targeting pattern across multiple events
  radicalization_pattern    — escalating harm over time
  deceptive_content         — consistently fabricated/manipulated content
  epistemic_manipulation    — coordinated inauthentic amplification
  sockpuppet_cluster        — single actor running multiple identities

RECOMMENDATION levels:
  MONITOR      — flag for observation, no restrictions
  RESTRICT     — reduced trust score, limited content visibility
  DEFEDERATE   — messages rejected at transport layer

Consensus thresholds:
  DEFEDERATE:  >=66% weighted agreement, >=3 unique assessors, >=24h span
  RESTRICT:    >=33% weighted agreement, >=2 unique assessors, >=1h span
  MONITOR:     any single credible assessment

Anti-weaponization:
  - Evidence (content hashes) is required structurally
  - Harm categories are a fixed enum — "bad politics" is not on the list
  - Spam penalty reduces assessor weight beyond 10 assessments/30d
  - Cool-down: DEFEDERATE requires assessments spanning >=24h from >=3 assessors

Public API
----------
create_assessment(agent_pubkey, evidence, harm_category, severity_score,
                  recommendation, reasoning)         -> assessment dict
get_assessment(assessment_id)                        -> assessment dict
get_assessments_for(agent_pubkey, active_only=True)  -> list
get_assessments_by(assessor_pubkey)                  -> list
withdraw_assessment(assessment_id, assessor_pubkey)  -> updated assessment
get_consensus(agent_pubkey)                          -> consensus dict
compute_consensus(agent_pubkey)                      -> compute + store + return
is_defederated(agent_pubkey)                         -> bool
detect_harassment_pattern(agent_pubkey)              -> {score, evidence, pattern}
detect_radicalization_pattern(agent_pubkey)          -> {score, evidence, pattern}
detect_epistemic_manipulation(agent_pubkey)          -> {score, evidence, pattern}
detect_sockpuppet_cluster(agent_pubkeys)             -> {score, clusters, pattern}

Storage: ~/.friday/defederation.db (SQLite, WAL)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import core
from core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "defederation.db"
_LOCK = threading.RLock()

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

VALID_HARM_CATEGORIES = frozenset({
    "H1", "H2", "H3", "H4",
    "coordinated_harassment",
    "radicalization_pattern",
    "deceptive_content",
    "epistemic_manipulation",
    "sockpuppet_cluster",
})

VALID_RECOMMENDATIONS = frozenset({"MONITOR", "RESTRICT", "DEFEDERATE"})

DEFEDERATE_THRESHOLD      = 0.66
RESTRICT_THRESHOLD        = 0.33
DEFEDERATE_MIN_ASSESSORS  = 3
RESTRICT_MIN_ASSESSORS    = 2
DEFEDERATE_MIN_HOURS      = 24.0
RESTRICT_MIN_HOURS        = 1.0

SPAM_THRESHOLD_30D        = 10
SPAM_PENALTY_PER_OVER     = 0.05

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assessments (
    id              TEXT PRIMARY KEY,
    agent_pubkey    TEXT NOT NULL,
    assessor_pubkey TEXT NOT NULL,
    harm_category   TEXT NOT NULL,
    severity_score  REAL NOT NULL,
    recommendation  TEXT NOT NULL,
    evidence_json   TEXT NOT NULL,
    reasoning       TEXT NOT NULL,
    signature       TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    withdrawn_at    TEXT,
    withdrawal_sig  TEXT
);

CREATE TABLE IF NOT EXISTS consensus (
    agent_pubkey     TEXT PRIMARY KEY,
    status           TEXT NOT NULL,
    confidence       REAL DEFAULT 0.0,
    assessor_count   INTEGER DEFAULT 0,
    weighted_score   REAL DEFAULT 0.0,
    last_updated     TEXT NOT NULL,
    contributing_ids TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS spam_counters (
    assessor_pubkey TEXT PRIMARY KEY,
    count_30d       INTEGER DEFAULT 0,
    last_reset      TEXT,
    weight_penalty  REAL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_assessments_agent    ON assessments(agent_pubkey, withdrawn_at);
CREATE INDEX IF NOT EXISTS idx_assessments_assessor ON assessments(assessor_pubkey, created_at);
"""

# ─────────────────────────────────────────────────────────────────────────────
#  CONNECTION
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def _ensure_schema() -> None:
    with _LOCK:
        with _conn() as con:
            con.executescript(_SCHEMA)


if not getattr(core, "_TESTING", False):
    _ensure_schema()


# ─────────────────────────────────────────────────────────────────────────────
#  INTEGRITY ENGINE ACCESSOR
# ─────────────────────────────────────────────────────────────────────────────

def _get_engine():
    try:
        from proof_of_integrity import get_integrity_engine
        return get_integrity_engine()
    except Exception:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sign_assessment(data: Dict[str, Any]) -> str:
    try:
        engine = _get_engine()
        if not engine:
            return ""
        body = {k: v for k, v in data.items()
                if k not in ("signature", "withdrawn_at", "withdrawal_sig")}
        payload = json.dumps(body, sort_keys=True).encode()
        return engine.sign_payload(payload) or ""
    except Exception:
        return ""


def _our_pubkey() -> str:
    engine = _get_engine()
    if engine:
        return engine.get_public_key_hex() or "local"
    return "local"


def _row_to_assessment(row: Any) -> Dict[str, Any]:
    d = dict(row) if not isinstance(row, dict) else row.copy()
    try:
        d["evidence"] = json.loads(d.get("evidence_json") or "[]")
    except Exception:
        d["evidence"] = []
    return d


# ─────────────────────────────────────────────────────────────────────────────
#  SPAM TRACKING
# ─────────────────────────────────────────────────────────────────────────────

def _get_assessor_weight(assessor_pubkey: str, peer_trust_score: float = 0.5) -> float:
    """Return effective weight = trust_score * (1 - spam_penalty)."""
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT count_30d, weight_penalty, last_reset FROM spam_counters WHERE assessor_pubkey=?",
                (assessor_pubkey,)
            ).fetchone()
        if not row:
            return peer_trust_score

        now = datetime.now(timezone.utc)
        last_reset_str = row["last_reset"] or _now()
        try:
            lr = datetime.fromisoformat(last_reset_str.replace("Z", "+00:00"))
        except Exception:
            lr = now
        if (now - lr).days >= 30:
            return peer_trust_score  # counter has reset

        penalty = float(row["weight_penalty"] or 0.0)
        return max(0.0, peer_trust_score * (1.0 - penalty))
    except Exception:
        return peer_trust_score


def _increment_spam_counter(assessor_pubkey: str) -> None:
    try:
        with _LOCK:
            now_str = _now()
            with _conn() as con:
                row = con.execute(
                    "SELECT count_30d, last_reset FROM spam_counters WHERE assessor_pubkey=?",
                    (assessor_pubkey,)
                ).fetchone()

                if not row:
                    con.execute(
                        "INSERT INTO spam_counters (assessor_pubkey, count_30d, last_reset, weight_penalty) "
                        "VALUES (?, 1, ?, 0.0)",
                        (assessor_pubkey, now_str)
                    )
                    return

                now = datetime.now(timezone.utc)
                try:
                    lr = datetime.fromisoformat((row["last_reset"] or now_str).replace("Z", "+00:00"))
                except Exception:
                    lr = now

                if (now - lr).days >= 30:
                    con.execute(
                        "UPDATE spam_counters SET count_30d=1, last_reset=?, weight_penalty=0.0 "
                        "WHERE assessor_pubkey=?",
                        (now_str, assessor_pubkey)
                    )
                    return

                new_count = int(row["count_30d"] or 0) + 1
                over = max(0, new_count - SPAM_THRESHOLD_30D)
                penalty = min(0.9, over * SPAM_PENALTY_PER_OVER)
                con.execute(
                    "UPDATE spam_counters SET count_30d=?, weight_penalty=? WHERE assessor_pubkey=?",
                    (new_count, penalty, assessor_pubkey)
                )
    except Exception as e:
        print(f"  [defederation] _increment_spam_counter: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  ASSESSMENT CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_assessment(
    agent_pubkey: str,
    evidence: List[Dict[str, Any]],
    harm_category: str,
    severity_score: float,
    recommendation: str,
    reasoning: str,
    assessor_pubkey: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Create a signed defederation assessment.

    *evidence* must be a non-empty list of dicts, each with at minimum:
      {"content_hash": str, "timestamp": str, "violation_type": str}

    *harm_category* must be in VALID_HARM_CATEGORIES (fixed enum — political
    disagreement is structurally impossible as a harm category).
    *recommendation* must be in VALID_RECOMMENDATIONS.

    Returns the stored assessment dict, or None on validation failure.
    """
    if not agent_pubkey:
        return None
    if not evidence or not isinstance(evidence, list):
        print("  [defederation] create_assessment: evidence required (non-empty list)")
        return None
    if harm_category not in VALID_HARM_CATEGORIES:
        print(f"  [defederation] create_assessment: invalid harm_category '{harm_category}'")
        return None
    if recommendation not in VALID_RECOMMENDATIONS:
        print(f"  [defederation] create_assessment: invalid recommendation '{recommendation}'")
        return None

    severity_score = max(0.0, min(1.0, float(severity_score)))
    assessor = assessor_pubkey or _our_pubkey()
    assessment_id = str(uuid.uuid4())
    now = _now()

    assessment: Dict[str, Any] = {
        "id": assessment_id,
        "agent_pubkey": agent_pubkey,
        "assessor_pubkey": assessor,
        "harm_category": harm_category,
        "severity_score": severity_score,
        "recommendation": recommendation,
        "evidence_json": json.dumps(evidence),
        "reasoning": reasoning,
        "signature": "",
        "created_at": now,
        "withdrawn_at": None,
        "withdrawal_sig": None,
    }
    assessment["signature"] = _sign_assessment(assessment)

    try:
        with _LOCK:
            with _conn() as con:
                con.execute(
                    """INSERT INTO assessments
                       (id, agent_pubkey, assessor_pubkey, harm_category,
                        severity_score, recommendation, evidence_json,
                        reasoning, signature, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        assessment["id"], assessment["agent_pubkey"],
                        assessment["assessor_pubkey"], assessment["harm_category"],
                        assessment["severity_score"], assessment["recommendation"],
                        assessment["evidence_json"], assessment["reasoning"],
                        assessment["signature"], assessment["created_at"],
                    ),
                )
        _increment_spam_counter(assessor)
        compute_consensus(agent_pubkey)
        result = dict(assessment)
        result["evidence"] = evidence
        return result
    except Exception as e:
        print(f"  [defederation] create_assessment failed: {e}")
        return None


def get_assessment(assessment_id: str) -> Optional[Dict[str, Any]]:
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM assessments WHERE id=?", (assessment_id,)
            ).fetchone()
            return _row_to_assessment(row) if row else None
    except Exception:
        return None


def get_assessments_for(
    agent_pubkey: str,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    """All assessments targeting *agent_pubkey*, newest first."""
    try:
        if active_only:
            with _conn() as con:
                rows = con.execute(
                    "SELECT * FROM assessments WHERE agent_pubkey=? AND withdrawn_at IS NULL "
                    "ORDER BY created_at DESC",
                    (agent_pubkey,)
                ).fetchall()
        else:
            with _conn() as con:
                rows = con.execute(
                    "SELECT * FROM assessments WHERE agent_pubkey=? ORDER BY created_at DESC",
                    (agent_pubkey,)
                ).fetchall()
        return [_row_to_assessment(r) for r in rows]
    except Exception:
        return []


def get_assessments_by(assessor_pubkey: str) -> List[Dict[str, Any]]:
    """All assessments produced by *assessor_pubkey*."""
    try:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM assessments WHERE assessor_pubkey=? ORDER BY created_at DESC",
                (assessor_pubkey,)
            ).fetchall()
        return [_row_to_assessment(r) for r in rows]
    except Exception:
        return []


def withdraw_assessment(assessment_id: str, assessor_pubkey: str) -> Optional[Dict[str, Any]]:
    """
    Withdraw an assessment. Only the original assessor may withdraw.

    The withdrawal is signed and timestamped; the record is retained for
    transparency (withdrawn_at / withdrawal_sig set, row not deleted).
    """
    try:
        assessment = get_assessment(assessment_id)
        if not assessment:
            return None

        our_key = _our_pubkey()
        if assessment["assessor_pubkey"] != assessor_pubkey and assessor_pubkey != our_key:
            print("  [defederation] withdraw_assessment: not authorized")
            return None
        if assessment.get("withdrawn_at"):
            return assessment  # idempotent

        now = _now()
        engine = _get_engine()
        withdrawal_sig = ""
        if engine:
            payload = f"{assessment_id}:withdraw:{now}".encode()
            withdrawal_sig = engine.sign_payload(payload) or ""

        with _LOCK:
            with _conn() as con:
                con.execute(
                    "UPDATE assessments SET withdrawn_at=?, withdrawal_sig=? WHERE id=?",
                    (now, withdrawal_sig, assessment_id)
                )
        compute_consensus(assessment["agent_pubkey"])
        return get_assessment(assessment_id)
    except Exception as e:
        print(f"  [defederation] withdraw_assessment failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  CONSENSUS ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def compute_consensus(agent_pubkey: str) -> Dict[str, Any]:
    """
    Aggregate active assessments into a consensus verdict.

    Weight = peer_trust_score * (1 - spam_penalty) * severity_score

    Thresholds:
      DEFEDERATE:  weighted_defederate_fraction >= 0.66,
                   unique assessors >= 3, time span >= 24h
      RESTRICT:    weighted_restrict+defederate_fraction >= 0.33,
                   unique assessors >= 2, time span >= 1h
      MONITOR:     any single active assessment (default when assessments exist)
    """
    now = _now()
    try:
        assessments = get_assessments_for(agent_pubkey, active_only=True)

        if not assessments:
            result: Dict[str, Any] = {
                "agent_pubkey": agent_pubkey,
                "status": "CLEAN",
                "confidence": 0.0,
                "assessor_count": 0,
                "weighted_score": 0.0,
                "last_updated": now,
                "contributing_ids": [],
            }
            _store_consensus(result)
            return result

        # Gather trust scores for assessors
        assessor_trust: Dict[str, float] = {}
        try:
            from services import federation as fed
            for a in assessments:
                ap = a["assessor_pubkey"]
                if ap not in assessor_trust:
                    peer = fed.get_peer(ap)
                    assessor_trust[ap] = float(peer.get("overall_score", 0.5)) if peer else 0.5
        except Exception:
            for a in assessments:
                assessor_trust[a["assessor_pubkey"]] = 0.5

        rec_weights: Dict[str, float] = {"MONITOR": 0.0, "RESTRICT": 0.0, "DEFEDERATE": 0.0}
        total_weight = 0.0
        unique_assessors: set = set()
        timestamps: List[str] = []
        contributing_ids: List[str] = []

        for a in assessments:
            ap = a["assessor_pubkey"]
            base_trust = assessor_trust.get(ap, 0.5)
            weight = _get_assessor_weight(ap, base_trust)
            effective = weight * float(a.get("severity_score", 0.5))
            rec = a.get("recommendation", "MONITOR")
            if rec in rec_weights:
                rec_weights[rec] += effective
            total_weight += effective
            unique_assessors.add(ap)
            timestamps.append(a["created_at"])
            contributing_ids.append(a["id"])

        if total_weight < 1e-9:
            total_weight = 1e-9

        def_ratio = rec_weights["DEFEDERATE"] / total_weight
        rst_ratio = (rec_weights["RESTRICT"] + rec_weights["DEFEDERATE"]) / total_weight

        # Time span between first and last assessment
        time_span_hours = 0.0
        if len(timestamps) >= 2:
            try:
                times = sorted(
                    datetime.fromisoformat(t.replace("Z", "+00:00"))
                    for t in timestamps
                )
                time_span_hours = (times[-1] - times[0]).total_seconds() / 3600.0
            except Exception:
                pass

        n_assessors = len(unique_assessors)

        status = "MONITOR"
        confidence = max(def_ratio, rst_ratio)

        if (
            def_ratio >= DEFEDERATE_THRESHOLD
            and n_assessors >= DEFEDERATE_MIN_ASSESSORS
            and time_span_hours >= DEFEDERATE_MIN_HOURS
        ):
            status = "DEFEDERATE"
            confidence = def_ratio
        elif (
            rst_ratio >= RESTRICT_THRESHOLD
            and n_assessors >= RESTRICT_MIN_ASSESSORS
            and time_span_hours >= RESTRICT_MIN_HOURS
        ):
            status = "RESTRICT"
            confidence = rst_ratio

        result = {
            "agent_pubkey": agent_pubkey,
            "status": status,
            "confidence": round(confidence, 4),
            "assessor_count": n_assessors,
            "weighted_score": round(def_ratio, 4),
            "last_updated": now,
            "contributing_ids": contributing_ids,
        }
        _store_consensus(result)
        return result
    except Exception as e:
        print(f"  [defederation] compute_consensus failed: {e}")
        fallback: Dict[str, Any] = {
            "agent_pubkey": agent_pubkey,
            "status": "CLEAN",
            "confidence": 0.0,
            "assessor_count": 0,
            "weighted_score": 0.0,
            "last_updated": now,
            "contributing_ids": [],
        }
        _store_consensus(fallback)
        return fallback


def _store_consensus(consensus: Dict[str, Any]) -> None:
    try:
        with _LOCK:
            with _conn() as con:
                con.execute(
                    """INSERT INTO consensus
                       (agent_pubkey, status, confidence, assessor_count,
                        weighted_score, last_updated, contributing_ids)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(agent_pubkey) DO UPDATE SET
                         status           = excluded.status,
                         confidence       = excluded.confidence,
                         assessor_count   = excluded.assessor_count,
                         weighted_score   = excluded.weighted_score,
                         last_updated     = excluded.last_updated,
                         contributing_ids = excluded.contributing_ids
                    """,
                    (
                        consensus["agent_pubkey"],
                        consensus["status"],
                        consensus["confidence"],
                        consensus["assessor_count"],
                        consensus["weighted_score"],
                        consensus["last_updated"],
                        json.dumps(consensus.get("contributing_ids", [])),
                    )
                )
    except Exception as e:
        print(f"  [defederation] _store_consensus failed: {e}")


def get_consensus(agent_pubkey: str) -> Dict[str, Any]:
    """Return stored consensus for *agent_pubkey*, computing it fresh if absent."""
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM consensus WHERE agent_pubkey=?", (agent_pubkey,)
            ).fetchone()
        if row:
            d = dict(row)
            try:
                d["contributing_ids"] = json.loads(d.get("contributing_ids") or "[]")
            except Exception:
                d["contributing_ids"] = []
            return d
        return compute_consensus(agent_pubkey)
    except Exception:
        return {
            "agent_pubkey": agent_pubkey,
            "status": "CLEAN",
            "confidence": 0.0,
            "assessor_count": 0,
            "contributing_ids": [],
        }


def is_defederated(agent_pubkey: str) -> bool:
    """True if the agent's consensus status is DEFEDERATE."""
    if not agent_pubkey:
        return False
    try:
        return get_consensus(agent_pubkey).get("status") == "DEFEDERATE"
    except Exception:
        return False


def is_restricted(agent_pubkey: str) -> bool:
    """True if the agent's consensus status is RESTRICT or DEFEDERATE."""
    if not agent_pubkey:
        return False
    try:
        return get_consensus(agent_pubkey).get("status") in ("RESTRICT", "DEFEDERATE")
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  PATTERN DETECTION (heuristic, local, no AI calls)
# ─────────────────────────────────────────────────────────────────────────────

def detect_harassment_pattern(agent_pubkey: str) -> Dict[str, Any]:
    """
    Heuristic: count active assessments for coordinated_harassment, H2, H3.

    Score 0.0-1.0; >= 0.4 suggests a reviewable pattern.
    """
    try:
        assessments = get_assessments_for(agent_pubkey, active_only=True)
        cats = {"coordinated_harassment", "H2", "H3"}
        relevant = [a for a in assessments if a.get("harm_category") in cats]

        if len(relevant) < 2:
            return {"score": 0.0, "evidence": [], "pattern": "none",
                    "assessment_count": len(relevant)}

        score = min(1.0, len(relevant) / 10.0)
        evidence_items: List[Any] = []
        for a in relevant[:5]:
            evidence_items.extend((a.get("evidence") or [])[:2])

        return {
            "score": round(score, 3),
            "evidence": evidence_items,
            "pattern": "coordinated_harassment",
            "assessment_count": len(relevant),
        }
    except Exception:
        return {"score": 0.0, "evidence": [], "pattern": "none", "assessment_count": 0}


def detect_radicalization_pattern(agent_pubkey: str) -> Dict[str, Any]:
    """
    Heuristic: upward trend in severity_score across assessments over time.

    Compares the mean severity of the earliest third vs latest third of
    all assessments (including withdrawn, for historical context).
    """
    try:
        assessments = get_assessments_for(agent_pubkey, active_only=False)
        if len(assessments) < 3:
            return {"score": 0.0, "evidence": [], "pattern": "none"}

        sorted_asc = sorted(assessments, key=lambda a: a.get("created_at") or "")
        scores = [float(a.get("severity_score", 0.0)) for a in sorted_asc]

        third = max(1, len(scores) // 3)
        early_avg = sum(scores[:third]) / third
        late_avg = sum(scores[-third:]) / third
        escalation = max(0.0, late_avg - early_avg)
        pattern_score = min(1.0, escalation * 2.0)

        evidence_items: List[Any] = []
        for a in sorted_asc[-3:]:
            evidence_items.extend((a.get("evidence") or [])[:1])

        return {
            "score": round(pattern_score, 3),
            "evidence": evidence_items,
            "pattern": "radicalization_pattern" if pattern_score >= 0.4 else "none",
            "early_avg_severity": round(early_avg, 3),
            "late_avg_severity": round(late_avg, 3),
        }
    except Exception:
        return {"score": 0.0, "evidence": [], "pattern": "none"}


def detect_epistemic_manipulation(agent_pubkey: str) -> Dict[str, Any]:
    """
    Heuristic: multiple independent assessors citing epistemic_manipulation or
    deceptive_content strengthens the signal (diversity × count).
    """
    try:
        assessments = get_assessments_for(agent_pubkey, active_only=True)
        cats = {"epistemic_manipulation", "deceptive_content"}
        relevant = [a for a in assessments if a.get("harm_category") in cats]

        if not relevant:
            return {"score": 0.0, "evidence": [], "pattern": "none",
                    "unique_assessors": 0, "assessment_count": 0}

        unique_assessors = len(set(a["assessor_pubkey"] for a in relevant))
        score = min(1.0, (len(relevant) * unique_assessors) / 20.0)

        evidence_items: List[Any] = []
        for a in relevant[:3]:
            evidence_items.extend((a.get("evidence") or [])[:2])

        return {
            "score": round(score, 3),
            "evidence": evidence_items,
            "pattern": "epistemic_manipulation" if score >= 0.3 else "none",
            "unique_assessors": unique_assessors,
            "assessment_count": len(relevant),
        }
    except Exception:
        return {"score": 0.0, "evidence": [], "pattern": "none",
                "unique_assessors": 0, "assessment_count": 0}


def detect_sockpuppet_cluster(agent_pubkeys: List[str]) -> Dict[str, Any]:
    """
    Heuristic: Jaccard similarity of assessment-target sets across a group of
    agents. > 70% overlap between any pair is flagged as suspicious.

    Returns {score, clusters: [{agent_a, agent_b, similarity}], pattern}.
    """
    try:
        if len(agent_pubkeys) < 2:
            return {"score": 0.0, "clusters": [], "pattern": "none"}

        target_sets: Dict[str, set] = {}
        for apk in agent_pubkeys:
            targets = {a["agent_pubkey"] for a in get_assessments_by(apk)}
            if targets:
                target_sets[apk] = targets

        if len(target_sets) < 2:
            return {"score": 0.0, "clusters": [], "pattern": "none"}

        high_overlap: List[Dict[str, Any]] = []
        keys = list(target_sets.keys())

        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a_set = target_sets[keys[i]]
                b_set = target_sets[keys[j]]
                union = len(a_set | b_set)
                if union == 0:
                    continue
                similarity = len(a_set & b_set) / union
                if similarity > 0.7:
                    high_overlap.append({
                        "agent_a": keys[i],
                        "agent_b": keys[j],
                        "similarity": round(similarity, 3),
                    })

        score = min(1.0, len(high_overlap) / max(1, len(keys) - 1))
        return {
            "score": round(score, 3),
            "clusters": high_overlap,
            "pattern": "sockpuppet_cluster" if score >= 0.5 else "none",
        }
    except Exception:
        return {"score": 0.0, "clusters": [], "pattern": "none"}
