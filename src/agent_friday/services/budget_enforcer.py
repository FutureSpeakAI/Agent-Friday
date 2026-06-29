"""
Agent Friday — Budget Enforcer
FutureSpeak.AI · Asimov's Mind

Per-workspace and per-task budget enforcement for the orchestration engine.
All amounts in milliPositrons (mψ). Storage: ~/.friday/budgets.db (SQLite).

Public API
----------
reserve_budget(workspace, amount_mψ)  → bool  (atomic; False = cap exceeded)
release_budget(workspace, amount_mψ)  → None  (return unused)
check_remaining(workspace)            → int    (mψ remaining this month)
get_policy(workspace)                 → dict
set_policy(workspace, monthly_cap_mψ, per_task_cap_mψ, warn_pct) → dict
get_all_policies()                    → list[dict]
enforce_hard_stop(worker_id)          → bool  (cancel the worker)
monthly_spend(workspace)              → int   (mψ spent so far this month)
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent_friday.core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "budgets.db"
_LOCK = threading.RLock()

_DEFAULT_MONTHLY_CAP = 1_000_000   # 1000ψ per workspace per month
_DEFAULT_PER_TASK    = 100_000     # 100ψ per task
_DEFAULT_WARN_PCT    = 80          # warn at 80%

# ─────────────────────────────────────────────────────────────────────────────
#  Schema
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_policies (
    workspace       TEXT PRIMARY KEY,
    monthly_cap_mψ  INTEGER DEFAULT 1000000,
    per_task_cap_mψ INTEGER DEFAULT 100000,
    warn_pct        INTEGER DEFAULT 80,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS budget_reservations (
    reservation_id  TEXT PRIMARY KEY,
    workspace       TEXT NOT NULL,
    amount_mψ       INTEGER NOT NULL,
    reserved_at     TEXT NOT NULL,
    month_key       TEXT NOT NULL,
    released        INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_res_workspace ON budget_reservations(workspace, month_key, released);
"""


_SCHEMA_DONE = False
_SCHEMA_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _SCHEMA_DONE
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    if not _SCHEMA_DONE:
        with _SCHEMA_LOCK:
            if not _SCHEMA_DONE:
                c.executescript(_SCHEMA)
                _SCHEMA_DONE = True
    return c


def _ensure_schema():
    with _conn() as c:
        pass  # _conn() handles it


try:
    import agent_friday.core as core_mod
except Exception:
    class core_mod:  # type: ignore
        _TESTING = False


def _month_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year}-{now.month:02d}"


# ─────────────────────────────────────────────────────────────────────────────
#  Policy management
# ─────────────────────────────────────────────────────────────────────────────

def _get_policy_row(workspace: str, conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT * FROM budget_policies WHERE workspace=?", (workspace,)
    ).fetchone()
    if row:
        return dict(row)
    return {
        "workspace": workspace,
        "monthly_cap_mψ": _DEFAULT_MONTHLY_CAP,
        "per_task_cap_mψ": _DEFAULT_PER_TASK,
        "warn_pct": _DEFAULT_WARN_PCT,
    }


def get_policy(workspace: str) -> dict:
    with _LOCK, _conn() as c:
        return _get_policy_row(workspace, c)


def set_policy(
    workspace: str,
    monthly_cap_mψ: int = _DEFAULT_MONTHLY_CAP,
    per_task_cap_mψ: int = _DEFAULT_PER_TASK,
    warn_pct: int = _DEFAULT_WARN_PCT,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK, _conn() as c:
        c.execute(
            """INSERT INTO budget_policies(workspace, monthly_cap_mψ, per_task_cap_mψ, warn_pct, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(workspace) DO UPDATE SET
                 monthly_cap_mψ=excluded.monthly_cap_mψ,
                 per_task_cap_mψ=excluded.per_task_cap_mψ,
                 warn_pct=excluded.warn_pct,
                 updated_at=excluded.updated_at""",
            (workspace, monthly_cap_mψ, per_task_cap_mψ, warn_pct, now),
        )
        return _get_policy_row(workspace, c)


def get_all_policies() -> list:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT * FROM budget_policies ORDER BY workspace").fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
#  Spend accounting
# ─────────────────────────────────────────────────────────────────────────────

def monthly_spend(workspace: str) -> int:
    """Total reserved (non-released) mψ in the current month for a workspace."""
    mk = _month_key()
    with _LOCK, _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount_mψ),0) as total FROM budget_reservations "
            "WHERE workspace=? AND month_key=? AND released=0",
            (workspace, mk),
        ).fetchone()
        return int(row["total"]) if row else 0


def check_remaining(workspace: str) -> int:
    policy = get_policy(workspace)
    spent = monthly_spend(workspace)
    return max(0, policy["monthly_cap_mψ"] - spent)


# ─────────────────────────────────────────────────────────────────────────────
#  Reserve / Release
# ─────────────────────────────────────────────────────────────────────────────

import uuid as _uuid


def reserve_budget(workspace: str, amount_mψ: int) -> bool:
    """Atomically reserve amount_mψ. Returns False if monthly cap would be exceeded."""
    if amount_mψ <= 0:
        return True
    policy = get_policy(workspace)
    mk = _month_key()
    now = datetime.now(timezone.utc).isoformat()

    with _LOCK, _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount_mψ),0) as total FROM budget_reservations "
            "WHERE workspace=? AND month_key=? AND released=0",
            (workspace, mk),
        ).fetchone()
        current_spend = int(row["total"]) if row else 0

        if current_spend + amount_mψ > policy["monthly_cap_mψ"]:
            return False

        c.execute(
            "INSERT INTO budget_reservations(reservation_id,workspace,amount_mψ,reserved_at,month_key) "
            "VALUES(?,?,?,?,?)",
            (str(_uuid.uuid4()), workspace, amount_mψ, now, mk),
        )

    # Warn at threshold
    try:
        new_total = current_spend + amount_mψ
        cap = policy["monthly_cap_mψ"]
        warn_pct = policy.get("warn_pct", 80)
        if cap > 0 and (new_total / cap) * 100 >= warn_pct:
            _maybe_warn(workspace, new_total, cap)
    except Exception:
        pass

    return True


def release_budget(workspace: str, amount_mψ: int) -> None:
    """Return unused budget by marking the most recent active reservation as released."""
    if amount_mψ <= 0:
        return
    mk = _month_key()
    with _LOCK, _conn() as c:
        row = c.execute(
            "SELECT reservation_id FROM budget_reservations "
            "WHERE workspace=? AND month_key=? AND released=0 "
            "ORDER BY reserved_at DESC LIMIT 1",
            (workspace, mk),
        ).fetchone()
        if row:
            c.execute(
                "UPDATE budget_reservations SET released=1, amount_mψ=MAX(0, amount_mψ-?) "
                "WHERE reservation_id=?",
                (amount_mψ, row["reservation_id"]),
            )


def _maybe_warn(workspace: str, spent: int, cap: int):
    try:
        from agent_friday.services.notifications import push_notification
        pct = int((spent / cap) * 100)
        push_notification(
            f"Budget alert: {workspace} has used {pct}% of monthly compute budget "
            f"({spent // 1000}ψ / {cap // 1000}ψ)",
            kind="budget_warning",
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Hard stop
# ─────────────────────────────────────────────────────────────────────────────

def enforce_hard_stop(worker_id: str) -> bool:
    """Cancel a worker that has exceeded its budget."""
    try:
        from agent_friday.services.orchestrator import get_orchestrator
        orch = get_orchestrator()
        return orch.cancel_worker(worker_id)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Status summary
# ─────────────────────────────────────────────────────────────────────────────

def budget_status(workspace: str) -> dict:
    policy = get_policy(workspace)
    spent = monthly_spend(workspace)
    cap = policy["monthly_cap_mψ"]
    return {
        "workspace": workspace,
        "monthly_cap_mψ": cap,
        "spent_mψ": spent,
        "remaining_mψ": max(0, cap - spent),
        "per_task_cap_mψ": policy["per_task_cap_mψ"],
        "warn_pct": policy.get("warn_pct", 80),
        "pct_used": round((spent / cap) * 100, 1) if cap else 0,
    }
