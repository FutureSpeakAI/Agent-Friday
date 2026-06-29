"""
Agent Friday — Compute Provider (Friday as Employee)
FutureSpeak.AI · Asimov's Mind

Accept federated compute jobs from trusted peers, execute them locally,
and return results. All jobs pass through the cLaws harm gate before execution.

Public API
----------
advertise_capabilities()          → dict  (CapabilityCard)
accept_job(job_request)           → (bool, str)  (accepted, reason)
execute_job(job)                  → dict  (JobResult)
reject_job(job_request, reason)   → dict  (rejection envelope)
get_active_jobs()                 → list[dict]
get_job_status(job_id)            → dict | None
get_job_result(job_id)            → dict | None
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_friday.core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "compute_jobs.db"
_LOCK = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS compute_jobs (
    job_id          TEXT PRIMARY KEY,
    requester_id    TEXT,
    capability      TEXT,
    prompt          TEXT,
    offered_mψ      INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'PENDING',
    received_at     TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    result_json     TEXT,
    error           TEXT,
    quality_score   REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_cj_status ON compute_jobs(status);
CREATE INDEX IF NOT EXISTS idx_cj_requester ON compute_jobs(requester_id);
"""

_CAPABILITIES = [
    {"type": "text.generate",  "description": "Generate text responses via local LLM",      "price_mψ": 1_000, "avg_duration_seconds": 15},
    {"type": "text.summarize", "description": "Summarize documents via local LLM",           "price_mψ": 500,   "avg_duration_seconds": 10},
    {"type": "code.generate",  "description": "Generate code via local LLM",                 "price_mψ": 2_000, "avg_duration_seconds": 30},
    {"type": "analysis.run",   "description": "Run a Python analysis script locally",        "price_mψ": 3_000, "avg_duration_seconds": 60},
    {"type": "research.web",   "description": "Web research via local search tools",         "price_mψ": 5_000, "avg_duration_seconds": 120},
]

_ACTIVE_JOBS: Dict[str, dict] = {}
_ACTIVE_LOCK = threading.RLock()


_CP_SCHEMA_DONE = False
_CP_SCHEMA_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _CP_SCHEMA_DONE
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    if not _CP_SCHEMA_DONE:
        with _CP_SCHEMA_LOCK:
            if not _CP_SCHEMA_DONE:
                c.executescript(_SCHEMA)
                _CP_SCHEMA_DONE = True
    return c


def _ensure_schema():
    with _conn() as c:
        pass  # _conn() handles it


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
#  CapabilityCard
# ─────────────────────────────────────────────────────────────────────────────

def _compute_specs() -> dict:
    specs = {
        "cpu_cores": os.cpu_count() or 1,
        "ram_gb": 0,
        "gpu_model": None,
        "gpu_vram_gb": None,
    }
    try:
        import psutil
        specs["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        pass
    return specs


def _own_pubkey() -> str:
    try:
        from agent_friday.services.federation import get_identity
        return get_identity().get("agent_id", "unknown")
    except Exception:
        return "unknown"


def _is_available() -> bool:
    with _ACTIVE_LOCK:
        return len(_ACTIVE_JOBS) < 3  # max 3 concurrent federated jobs


def advertise_capabilities() -> dict:
    return {
        "type": "FridayCapabilityCard",
        "version": "1.0",
        "agent_pubkey": _own_pubkey(),
        "capabilities": [c for c in _CAPABILITIES if c.get("enabled", True)],
        "compute_specs": _compute_specs(),
        "availability": {
            "online": True,
            "busy": not _is_available(),
            "active_jobs": len(_ACTIVE_JOBS),
            "busy_until": None,
        },
        "min_trust_score": 0.4,
        "advertised_at": _now(),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  cLaws gate
# ─────────────────────────────────────────────────────────────────────────────

def _claws_check(job_request: dict) -> Tuple[bool, str]:
    """Minimal harm gate — reject obviously harmful requests."""
    prompt = (job_request.get("prompt") or "").lower()
    harm_keywords = [
        "csam", "child abuse", "create malware", "ransomware",
        "doxx", "bomb making", "synthesis of", "instructions for harm",
    ]
    for kw in harm_keywords:
        if kw in prompt:
            return False, f"cLaws gate: request contains prohibited content ({kw!r})"
    return True, "ok"


# ─────────────────────────────────────────────────────────────────────────────
#  Accept / Reject
# ─────────────────────────────────────────────────────────────────────────────

def accept_job(job_request: dict) -> Tuple[bool, str]:
    """Evaluate a job request. Returns (accepted, reason)."""
    # Availability check
    if not _is_available():
        return False, "at capacity — too many active jobs"

    # Trust check
    requester_trust = job_request.get("requester_trust_score", 0.5)
    min_trust = 0.4
    if requester_trust < min_trust:
        return False, f"requester trust score {requester_trust:.2f} below minimum {min_trust}"

    # cLaws
    ok, reason = _claws_check(job_request)
    if not ok:
        return False, reason

    # Capability match
    cap = job_request.get("capability")
    supported = {c["type"] for c in _CAPABILITIES}
    if cap and cap not in supported:
        return False, f"capability {cap!r} not supported"

    # Price check
    offered = job_request.get("offered_mψ", 0)
    price = next((c["price_mψ"] for c in _CAPABILITIES if c["type"] == cap), 500)
    if offered < price * 0.5:  # allow 50% below list
        return False, f"offered price {offered}mψ below minimum {price * 0.5:.0f}mψ"

    return True, "accepted"


def reject_job(job_request: dict, reason: str) -> dict:
    return {
        "type": "FridayJobRejection",
        "job_id": job_request.get("job_id", str(uuid.uuid4())),
        "reason": reason,
        "rejected_at": _now(),
        "agent_pubkey": _own_pubkey(),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Execute
# ─────────────────────────────────────────────────────────────────────────────

def execute_job(job: dict) -> dict:
    """Execute a federated job and return the result dict."""
    job_id = job.get("job_id") or str(uuid.uuid4())
    prompt = job.get("prompt", "")
    cap = job.get("capability", "text.generate")
    offered_mψ = job.get("offered_mψ", 0)
    requester_id = job.get("requester_id", "unknown")

    # Record in DB
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO compute_jobs "
            "(job_id,requester_id,capability,prompt,offered_mψ,status,received_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (job_id, requester_id, cap, prompt, offered_mψ, "RUNNING", _now()),
        )

    with _ACTIVE_LOCK:
        _ACTIVE_JOBS[job_id] = {"status": "RUNNING", "started": time.time()}

    started = time.time()
    try:
        result_output = _dispatch(cap, prompt, job)
        duration = time.time() - started

        result = {
            "type": "FridayJobResult",
            "job_id": job_id,
            "status": "COMPLETED",
            "output": result_output,
            "duration_seconds": round(duration, 2),
            "cost_mψ": offered_mψ,
            "completed_at": _now(),
            "agent_pubkey": _own_pubkey(),
        }

        with _LOCK, _conn() as c:
            c.execute(
                "UPDATE compute_jobs SET status='COMPLETED', completed_at=?, "
                "result_json=?, started_at=? WHERE job_id=?",
                (_now(), json.dumps(result), datetime.now(timezone.utc).isoformat(), job_id),
            )

        # Credit economy
        try:
            from agent_friday.services.economy import earn
            earn(_own_pubkey(), offered_mψ, f"federated_job:{job_id}")
        except Exception:
            pass

    except Exception as exc:
        duration = time.time() - started
        result = {
            "type": "FridayJobResult",
            "job_id": job_id,
            "status": "FAILED",
            "output": None,
            "error": str(exc),
            "duration_seconds": round(duration, 2),
            "completed_at": _now(),
            "agent_pubkey": _own_pubkey(),
        }
        with _LOCK, _conn() as c:
            c.execute(
                "UPDATE compute_jobs SET status='FAILED', error=?, completed_at=? WHERE job_id=?",
                (str(exc), _now(), job_id),
            )

    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_JOBS.pop(job_id, None)

    return result


def _dispatch(capability: str, prompt: str, job: dict) -> Any:
    """Route to the appropriate local handler by capability type."""
    if capability in ("text.generate", "text.summarize", "code.generate"):
        from agent_friday.services.orchestrator import get_orchestrator, TaskType, AdapterType
        task_type_map = {
            "text.generate": TaskType.RESEARCH,
            "text.summarize": TaskType.ANALYSIS,
            "code.generate": TaskType.CODE,
        }
        orch = get_orchestrator()
        res = orch.delegate(
            prompt,
            task_type=task_type_map.get(capability, TaskType.CUSTOM),
            budget_mψ=job.get("offered_mψ", 50_000),
            context={"workspace": "federation", "job_id": job.get("job_id")},
            adapter_type=AdapterType.OLLAMA,
            deadline_seconds=120,
        )
        return res.output

    if capability == "analysis.run":
        from agent_friday.services.orchestrator import get_orchestrator, TaskType, AdapterType
        orch = get_orchestrator()
        res = orch.delegate(
            prompt,
            task_type=TaskType.ANALYSIS,
            budget_mψ=job.get("offered_mψ", 50_000),
            context={"workspace": "federation"},
            adapter_type=AdapterType.PYTHON_SCRIPT,
            deadline_seconds=180,
        )
        return res.output

    # Fallback
    return f"[compute_provider] capability={capability!r} handled locally. Prompt: {prompt[:200]}"


# ─────────────────────────────────────────────────────────────────────────────
#  Status queries
# ─────────────────────────────────────────────────────────────────────────────

def get_active_jobs() -> List[dict]:
    with _ACTIVE_LOCK:
        return [{"job_id": jid, **info} for jid, info in _ACTIVE_JOBS.items()]


def get_job_status(job_id: str) -> Optional[dict]:
    with _LOCK, _conn() as c:
        row = c.execute(
            "SELECT job_id, status, capability, received_at, started_at, completed_at, error "
            "FROM compute_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def get_job_result(job_id: str) -> Optional[dict]:
    with _LOCK, _conn() as c:
        row = c.execute("SELECT * FROM compute_jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("result_json"):
        try:
            d["result"] = json.loads(d["result_json"])
        except Exception:
            pass
    return d


def toggle_capability(name: str) -> bool:
    """Toggle a capability on/off. Returns new enabled state."""
    for cap in _CAPABILITIES:
        if cap["type"] == name:
            cap["enabled"] = not cap.get("enabled", True)
            return cap["enabled"]
    return False


def set_capability_price(name: str, price_mψ_per_ktoken: int) -> bool:
    """Set pricing for a capability. Returns True if found."""
    for cap in _CAPABILITIES:
        if cap["type"] == name:
            cap["price_mψ"] = int(price_mψ_per_ktoken)
            return True
    return False
