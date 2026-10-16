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

# No cap unless the owner sets one. A cap of 0 is no cap everywhere it is read
# here. A workspace without its own policy takes the owner's default from
# settings.spending_caps (workspace_monthly_mpsi, workspace_task_mpsi), which
# the first-run setup conversation asks about (services/spending_caps.py) and
# which ship as None. The figures this module used to impose (1000ψ a month,
# 100ψ a task) are now only the suggestions offered there.
_DEFAULT_MONTHLY_CAP = 0
_DEFAULT_PER_TASK    = 0
_DEFAULT_WARN_PCT    = 80          # warn at 80% of a cap the owner set
NO_CAP_LABEL = "No cap (ask me during setup)"

# ─────────────────────────────────────────────────────────────────────────────
#  Schema
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_policies (
    workspace       TEXT PRIMARY KEY,
    monthly_cap_mψ  INTEGER DEFAULT 0,
    per_task_cap_mψ INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS budget_meta (
    key             TEXT PRIMARY KEY,
    value           TEXT
);
"""


_SCHEMA_DONE = False
_SCHEMA_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _SCHEMA_DONE
    from agent_friday.services.db_util import ClosingConnection
    c = sqlite3.connect(str(DB_PATH), timeout=10, factory=ClosingConnection)
    c.row_factory = sqlite3.Row
    if not _SCHEMA_DONE:
        with _SCHEMA_LOCK:
            if not _SCHEMA_DONE:
                c.executescript(_SCHEMA)
                migrate_old_default_policies(c)
                _SCHEMA_DONE = True
    return c


# ─────────────────────────────────────────────────────────────────────────────
#  One-time migration: policy rows at the caps this module used to impose
# ─────────────────────────────────────────────────────────────────────────────
#
# Before caps shipped off, the Settings budget panel saved a workspace's policy
# with the form pre-filled from the old defaults (and POST /api/budget/policy
# filled a missing figure with them), so budgets.db can hold rows at exactly
# 1,000,000 mψ a month and 100,000 mψ a task that nobody chose. Rows record no
# owner and get_policy never inserted defaults, so a defaulted row cannot be
# told from a chosen one by its history; the rule is therefore the
# conservative one: only a row whose BOTH caps equal the old defaults is
# migrated. Its caps become 0 (no cap), warn_pct is kept, and updated_at is
# set to MIGRATED_MARK + time, which _get_policy_row reads as "not the
# owner's figure": the owner's default from settings.spending_caps applies
# until the owner saves a policy for that workspace. The run is recorded in
# budget_meta and never repeats.

_OLD_DEFAULT_MONTHLY_CAP = 1_000_000
_OLD_DEFAULT_PER_TASK = 100_000
MIGRATION_KEY = "defaults_off_migrated"
MIGRATED_MARK = "migrated-defaults-off:"


def migrate_old_default_policies(c: sqlite3.Connection) -> int:
    """Clear policy rows at exactly the old default caps, once. Returns how
    many rows changed (0 also when the migration has already run). Never
    raises: a failure is logged and retried by the next process."""
    import logging
    log = logging.getLogger("friday.budget")
    try:
        if c.execute("SELECT 1 FROM budget_meta WHERE key=?", (MIGRATION_KEY,)).fetchone():
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = c.execute(
            "SELECT workspace FROM budget_policies "
            "WHERE monthly_cap_mψ=? AND per_task_cap_mψ=?",
            (_OLD_DEFAULT_MONTHLY_CAP, _OLD_DEFAULT_PER_TASK)).fetchall()
        names = [r[0] for r in rows]
        for ws in names:
            c.execute("UPDATE budget_policies SET monthly_cap_mψ=0, per_task_cap_mψ=0, "
                      "updated_at=? WHERE workspace=?", (MIGRATED_MARK + now, ws))
            log.info("budget_enforcer: workspace %r had the old shipped caps (%d mψ a "
                     "month, %d mψ a task), never distinguishable from a choice; they "
                     "are now off (no cap) and the owner's default applies. One-time "
                     "migration.", ws, _OLD_DEFAULT_MONTHLY_CAP, _OLD_DEFAULT_PER_TASK)
        c.execute("INSERT INTO budget_meta(key, value) VALUES(?, ?)",
                  (MIGRATION_KEY, "%s rows=%d" % (now, len(names))))
        c.commit()
        if not names:
            log.info("budget_enforcer: one-time defaults-off migration found no policy "
                     "at the old shipped caps")
        return len(names)
    except Exception as e:                                   # noqa: BLE001
        try:
            c.rollback()
        except Exception:
            pass
        log.warning("budget_enforcer: defaults-off migration skipped (%s: %s); "
                    "it runs again next start", type(e).__name__, e)
        return 0


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

def _owner_default(key: str, fallback: int) -> int:
    """The owner's default cap from settings.spending_caps[key]; None, 0 or
    anything unreadable is `fallback` (no cap)."""
    try:
        from agent_friday.core import _load_settings
        v = ((_load_settings() or {}).get("spending_caps") or {}).get(key)
        v = int(v or 0)
        return v if v > 0 else fallback
    except Exception:
        return fallback


def _get_policy_row(workspace: str, conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT * FROM budget_policies WHERE workspace=?", (workspace,)
    ).fetchone()
    if row:
        out = dict(row)
        if str(out.get("updated_at") or "").startswith(MIGRATED_MARK):
            # Cleared by the one-time migration, not set by the owner: the
            # owner's default applies until they save a policy here.
            if not int(out.get("monthly_cap_mψ") or 0):
                out["monthly_cap_mψ"] = _owner_default("workspace_monthly_mpsi",
                                                       _DEFAULT_MONTHLY_CAP)
            if not int(out.get("per_task_cap_mψ") or 0):
                out["per_task_cap_mψ"] = _owner_default("workspace_task_mpsi",
                                                        _DEFAULT_PER_TASK)
        return out
    return {
        "workspace": workspace,
        "monthly_cap_mψ": _owner_default("workspace_monthly_mpsi", _DEFAULT_MONTHLY_CAP),
        "per_task_cap_mψ": _owner_default("workspace_task_mpsi", _DEFAULT_PER_TASK),
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


def check_remaining(workspace: str) -> Optional[int]:
    """mψ left this month, or None when the workspace has no monthly cap."""
    policy = get_policy(workspace)
    cap = int(policy.get("monthly_cap_mψ") or 0)
    if cap <= 0:
        return None
    spent = monthly_spend(workspace)
    return max(0, cap - spent)


# ─────────────────────────────────────────────────────────────────────────────
#  Reserve / Release
# ─────────────────────────────────────────────────────────────────────────────

import uuid as _uuid


def reserve_budget(workspace: str, amount_mψ: int) -> bool:
    """Atomically reserve amount_mψ. Returns False if the per-task cap or the monthly cap would be exceeded."""
    if amount_mψ <= 0:
        return True
    policy = get_policy(workspace)
    # The per-task cap is the most one task may reserve at once.
    task_cap = int(policy.get("per_task_cap_mψ") or 0)
    if task_cap > 0 and amount_mψ > task_cap:
        _log_refusal(workspace, "per-task cap", amount_mψ, task_cap)
        return False
    monthly_cap = int(policy.get("monthly_cap_mψ") or 0)   # 0: no monthly cap
    mk = _month_key()
    now = datetime.now(timezone.utc).isoformat()

    with _LOCK, _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount_mψ),0) as total FROM budget_reservations "
            "WHERE workspace=? AND month_key=? AND released=0",
            (workspace, mk),
        ).fetchone()
        current_spend = int(row["total"]) if row else 0

        if monthly_cap > 0 and current_spend + amount_mψ > monthly_cap:
            _log_refusal(workspace, "monthly cap", current_spend + amount_mψ, monthly_cap)
            return False

        c.execute(
            "INSERT INTO budget_reservations(reservation_id,workspace,amount_mψ,reserved_at,month_key) "
            "VALUES(?,?,?,?,?)",
            (str(_uuid.uuid4()), workspace, amount_mψ, now, mk),
        )

    # Warn at threshold
    try:
        new_total = current_spend + amount_mψ
        cap = monthly_cap
        warn_pct = policy.get("warn_pct", 80)
        if cap > 0 and (new_total / cap) * 100 >= warn_pct:
            _maybe_warn(workspace, new_total, cap)
    except Exception:
        pass

    return True


def _log_refusal(workspace: str, which: str, wanted: int, cap: int) -> None:
    try:
        import logging
        logging.getLogger("friday.budget").warning(
            "budget_enforcer refused a reservation for workspace %r: %d mψ would pass "
            "the owner's %s of %d mψ", workspace, wanted, which, cap)
    except Exception:
        pass


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
        import agent_friday.notifications_engine as notifier
        pct = int((spent / cap) * 100)
        notifier.push(
            title=(f"Budget alert: {workspace} has used {pct}% of monthly compute budget "
                   f"({spent // 1000}ψ / {cap // 1000}ψ)"),
            kind="budget_warning",
            source="budget",
            priority="high",
            dedupe_key=f"budget_warning:{workspace}:{_month_key()}",
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
    """A workspace's spend and caps. A cap of 0 is no cap: its remaining is
    None and the status carries NO_CAP_LABEL for the Settings panel."""
    policy = get_policy(workspace)
    spent = monthly_spend(workspace)
    cap = int(policy.get("monthly_cap_mψ") or 0)
    task_cap = int(policy.get("per_task_cap_mψ") or 0)
    return {
        "workspace": workspace,
        "monthly_cap_mψ": cap,
        "spent_mψ": spent,
        "remaining_mψ": max(0, cap - spent) if cap > 0 else None,
        "per_task_cap_mψ": task_cap,
        "warn_pct": policy.get("warn_pct", 80),
        "pct_used": round((spent / cap) * 100, 1) if cap > 0 else 0,
        "capped": cap > 0,
        "task_capped": task_cap > 0,
        "no_cap_label": NO_CAP_LABEL,
    }
