"""
Agent Friday — Compute Client (Friday outsourcing work to peers)
FutureSpeak.AI · Asimov's Mind

When local adapters can't handle a task (no GPU, busy, no capability),
the Orchestrator can delegate to federation peers via this client.
All outbound jobs go through the egress gate.

Public API
----------
find_providers(capability_type)              → list[dict]  (CapabilityCards)
request_job(provider_id, task_spec, mψ)     → dict  (JobRequest)
await_result(job_id, timeout)               → dict  (JobResult)
rate_provider(job_id, quality_score)         → bool  (updates trust)
get_sent_jobs(limit)                         → list[dict]
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "compute_sent_jobs.db"
_LOCK = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_jobs (
    job_id          TEXT PRIMARY KEY,
    provider_id     TEXT,
    capability      TEXT,
    prompt_hash     TEXT,
    offered_mψ      INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'PENDING',
    sent_at         TEXT,
    completed_at    TEXT,
    result_json     TEXT,
    error           TEXT,
    quality_score   REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_sj_status ON sent_jobs(status);
"""


_CC_SCHEMA_DONE = False
_CC_SCHEMA_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _CC_SCHEMA_DONE
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    if not _CC_SCHEMA_DONE:
        with _CC_SCHEMA_LOCK:
            if not _CC_SCHEMA_DONE:
                c.executescript(_SCHEMA)
                _CC_SCHEMA_DONE = True
    return c


def _ensure_schema():
    with _conn() as c:
        pass  # _conn() handles it


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_prompt(prompt: str) -> str:
    import hashlib
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def _own_id() -> str:
    try:
        from agent_friday.services.federation import get_identity
        return get_identity().get("agent_id", "unknown")
    except Exception:
        return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
#  Discovery
# ─────────────────────────────────────────────────────────────────────────────

def find_providers(capability_type: str) -> List[dict]:
    """Query known peers for ones that advertise the requested capability."""
    providers = []
    try:
        from agent_friday.services.federation import get_peers
        peers = get_peers()
    except Exception:
        return []

    for peer in peers:
        endpoints = peer.get("endpoints") or []
        if isinstance(endpoints, str):
            try:
                endpoints = json.loads(endpoints)
            except Exception:
                endpoints = []
        caps = peer.get("capabilities") or []
        if isinstance(caps, str):
            try:
                caps = json.loads(caps)
            except Exception:
                caps = []

        # Check if peer advertises this capability
        has_cap = any(
            (c == capability_type or (isinstance(c, dict) and c.get("type") == capability_type))
            for c in caps
        )
        if not has_cap:
            continue

        # Try to fetch their capability card
        for endpoint in endpoints[:1]:  # try first endpoint
            try:
                url = endpoint.rstrip("/") + "/api/federation/capabilities"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    card = json.loads(resp.read())
                card["_peer_id"] = peer.get("agent_id")
                card["_endpoint"] = endpoint
                card["_trust_score"] = peer.get("overall_score", 0.5)
                providers.append(card)
            except Exception:
                pass

    return providers


# ─────────────────────────────────────────────────────────────────────────────
#  Request
# ─────────────────────────────────────────────────────────────────────────────

def request_job(
    provider_endpoint: str,
    task_spec: dict,
    offered_mψ: int = 1_000,
) -> dict:
    """Send a job to a provider. Returns the job_request dict (with job_id)."""
    prompt = task_spec.get("prompt", "")

    # Egress gate — check before sending
    try:
        from agent_friday.services.egress_gate import seal_outbound
        safe_payload = seal_outbound({"prompt": prompt}, provider="federation")
        prompt = safe_payload.get("prompt", prompt)
    except Exception:
        pass

    job_id = str(uuid.uuid4())
    payload = {
        "job_id": job_id,
        "requester_id": _own_id(),
        "requester_trust_score": 0.8,  # self-reported; provider verifies independently
        "capability": task_spec.get("capability", "text.generate"),
        "prompt": prompt,
        "offered_mψ": offered_mψ,
        "sent_at": _now(),
        "context": task_spec.get("context", {}),
    }

    url = provider_endpoint.rstrip("/") + "/api/federation/compute/request"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            response = json.loads(resp.read())
    except Exception as exc:
        response = {"error": str(exc)}

    with _LOCK, _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO sent_jobs "
            "(job_id,provider_id,capability,prompt_hash,offered_mψ,status,sent_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (job_id, provider_endpoint, task_spec.get("capability", "text.generate"),
             _hash_prompt(prompt), offered_mψ, "PENDING", _now()),
        )

    # Spend mψ
    try:
        from agent_friday.services.economy import spend
        spend(_own_id(), offered_mψ, f"compute_client:{job_id}")
    except Exception:
        pass

    return {**payload, "provider_response": response}


# ─────────────────────────────────────────────────────────────────────────────
#  Await
# ─────────────────────────────────────────────────────────────────────────────

def await_result(job_id: str, provider_endpoint: str, timeout: float = 300.0) -> dict:
    """Poll the provider until the job completes or timeout."""
    url = provider_endpoint.rstrip("/") + f"/api/federation/compute/status/{job_id}"
    deadline = time.time() + timeout
    poll_interval = 3.0

    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = json.loads(resp.read())
            if status.get("status") in ("COMPLETED", "FAILED"):
                with _LOCK, _conn() as c:
                    c.execute(
                        "UPDATE sent_jobs SET status=?, completed_at=?, result_json=? WHERE job_id=?",
                        (status.get("status"), _now(), json.dumps(status), job_id),
                    )
                return status
        except Exception:
            pass
        time.sleep(poll_interval)

    with _LOCK, _conn() as c:
        c.execute("UPDATE sent_jobs SET status='TIMEOUT' WHERE job_id=?", (job_id,))
    return {"job_id": job_id, "status": "TIMEOUT", "error": "await_result timed out"}


# ─────────────────────────────────────────────────────────────────────────────
#  Rate
# ─────────────────────────────────────────────────────────────────────────────

def rate_provider(job_id: str, quality_score: float) -> bool:
    """Record quality score locally and update federation trust."""
    quality_score = max(0.0, min(1.0, quality_score))
    with _LOCK, _conn() as c:
        row = c.execute("SELECT provider_id FROM sent_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return False
        c.execute("UPDATE sent_jobs SET quality_score=? WHERE job_id=?", (quality_score, job_id))
        provider_id = row["provider_id"]

    try:
        from agent_friday.services.federation import update_peer_trust
        update_peer_trust(provider_id, {"quality": quality_score})
    except Exception:
        pass

    return True


# ─────────────────────────────────────────────────────────────────────────────
#  History
# ─────────────────────────────────────────────────────────────────────────────

def get_sent_jobs(limit: int = 50) -> List[dict]:
    with _LOCK, _conn() as c:
        rows = c.execute(
            "SELECT * FROM sent_jobs ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
