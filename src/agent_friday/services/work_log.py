"""
Agent Friday — Work Log & Audit Trail
FutureSpeak.AI · Asimov's Mind

SQLite audit trail of every orchestrated action — employer and employee sides.

Public API
----------
log_start(task)             → work_id (str)
log_finish(task, result)    → None
get_log(limit, offset, workspace, worker_type, since, until) → list[dict]
get_entry(work_id)          → dict | None
delete_old_entries(days)    → int  (count pruned)

Storage: ~/.friday/work_log.db
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "work_log.db"
_LOCK = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_log (
    work_id             TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL,
    worker_type         TEXT,
    adapter_type        TEXT,
    prompt_hash         TEXT,
    workspace           TEXT,
    started_at          TEXT,
    completed_at        TEXT,
    tokens_in           INTEGER DEFAULT 0,
    tokens_out          INTEGER DEFAULT 0,
    cost_mψ             INTEGER DEFAULT 0,
    quality_score       REAL DEFAULT 0.0,
    status              TEXT,
    error               TEXT,
    artifacts_produced  TEXT DEFAULT '[]',
    goal_ancestry_json  TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_wl_started  ON work_log(started_at);
CREATE INDEX IF NOT EXISTS idx_wl_workspace ON work_log(workspace);
CREATE INDEX IF NOT EXISTS idx_wl_status    ON work_log(status);
CREATE INDEX IF NOT EXISTS idx_wl_task      ON work_log(task_id);
"""


_WL_SCHEMA_DONE = False
_WL_SCHEMA_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _WL_SCHEMA_DONE
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    if not _WL_SCHEMA_DONE:
        with _WL_SCHEMA_LOCK:
            if not _WL_SCHEMA_DONE:
                c.executescript(_SCHEMA)
                _WL_SCHEMA_DONE = True
    return c


def _ensure_schema():
    with _conn() as c:
        pass  # _conn() handles it

# ─── Map task_id → work_id for the two-step log_start/log_finish pattern ─────
_ACTIVE: Dict[str, str] = {}
_ACTIVE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
#  Write
# ─────────────────────────────────────────────────────────────────────────────

def log_start(task) -> str:
    """
    Open a work log entry when a worker starts.
    task should be a WorkerTask (or any object with .task_id, .prompt, etc.)
    Returns the work_id.
    """
    work_id = str(uuid.uuid4())
    task_id = getattr(task, "task_id", str(uuid.uuid4()))
    prompt = getattr(task, "prompt", "")
    adapter_type = getattr(task, "adapter_type", None)
    task_type = getattr(task, "task_type", None)
    context = getattr(task, "context", {}) or {}
    workspace = context.get("workspace_goal", context.get("workspace", "default"))

    with _ACTIVE_LOCK:
        _ACTIVE[task_id] = work_id

    row = {
        "work_id": work_id,
        "task_id": task_id,
        "worker_type": str(task_type.value) if hasattr(task_type, "value") else str(task_type or ""),
        "adapter_type": str(adapter_type.value) if hasattr(adapter_type, "value") else str(adapter_type or ""),
        "prompt_hash": _hash_prompt(prompt),
        "workspace": workspace,
        "started_at": _now(),
        "completed_at": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_mψ": 0,
        "quality_score": 0.0,
        "status": "RUNNING",
        "error": None,
        "artifacts_produced": "[]",
        "goal_ancestry_json": json.dumps(context),
    }

    with _LOCK, _conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO work_log
               (work_id,task_id,worker_type,adapter_type,prompt_hash,workspace,
                started_at,completed_at,tokens_in,tokens_out,cost_mψ,quality_score,
                status,error,artifacts_produced,goal_ancestry_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["work_id"], row["task_id"], row["worker_type"], row["adapter_type"],
             row["prompt_hash"], row["workspace"], row["started_at"], row["completed_at"],
             row["tokens_in"], row["tokens_out"], row["cost_mψ"], row["quality_score"],
             row["status"], row["error"], row["artifacts_produced"], row["goal_ancestry_json"]),
        )
    return work_id


def log_finish(task, result) -> None:
    """Update the open entry with final result data."""
    task_id = getattr(task, "task_id", None)
    if not task_id:
        return

    with _ACTIVE_LOCK:
        work_id = _ACTIVE.pop(task_id, None)
    if not work_id:
        return

    status = getattr(result, "status", None)
    if hasattr(status, "value"):
        status = status.value
    tokens = getattr(result, "tokens_used", 0) or 0
    cost = getattr(result, "cost_mψ", 0) or 0
    quality = getattr(result, "quality_score", 0.0) or 0.0
    error = getattr(result, "error", None)
    artifacts = json.dumps(getattr(result, "artifacts", []) or [])

    with _LOCK, _conn() as c:
        c.execute(
            """UPDATE work_log SET
               completed_at=?, tokens_out=?, cost_mψ=?, quality_score=?,
               status=?, error=?, artifacts_produced=?
               WHERE work_id=?""",
            (_now(), tokens, cost, quality, str(status), error, artifacts, work_id),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Read
# ─────────────────────────────────────────────────────────────────────────────

def get_log(
    limit: int = 50,
    offset: int = 0,
    workspace: Optional[str] = None,
    worker_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[dict]:
    clauses, params = [], []
    if workspace:
        clauses.append("workspace=?"); params.append(workspace)
    if worker_type:
        clauses.append("worker_type=?"); params.append(worker_type)
    if since:
        clauses.append("started_at>=?"); params.append(since)
    if until:
        clauses.append("started_at<=?"); params.append(until)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params += [limit, offset]

    with _LOCK, _conn() as c:
        rows = c.execute(
            f"SELECT * FROM work_log {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_entry(work_id: str) -> Optional[dict]:
    with _LOCK, _conn() as c:
        row = c.execute("SELECT * FROM work_log WHERE work_id=?", (work_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("artifacts_produced", "goal_ancestry_json"):
        try:
            d[key] = json.loads(d[key])
        except Exception:
            pass
    return d


def delete_old_entries(days: int = 90) -> int:
    cutoff = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=days)
    with _LOCK, _conn() as c:
        cur = c.execute(
            "DELETE FROM work_log WHERE started_at < ?",
            (cutoff.isoformat(),),
        )
        return cur.rowcount
