"""
Agent Friday — Positron/Negatron Economy (Layer 3)
FutureSpeak.AI · Asimov's Mind

Positron (ψ) = value creation currency (earned by creating content, receiving
               likes/shares, completing tasks, early adopter bonus)
Negatron (η) = cost/obligation currency (spent on API calls, purchases, bandwidth,
               minted by system for violations)
Net Charge Q = Σψ − Ση  — net value contribution to the network

All amounts in milliPositrons (mψ) — integer only, no floats.

Public API
----------
get_wallet(agent_id)     → wallet dict {agent_id, psi_balance, eta_balance, q_score, ...}
earn(agent_id, amount_mpsi, reason)     → credit Positrons, return tx record
spend(agent_id, amount_meta, reason)    → credit Negatrons, return tx record
mint_negatron(agent_id, amount_meta, reason)  → system-mint η against agent
burn_negatron(agent_id, amount_meta)    → burn η by spending ψ (active annihilation)
transfer(from_agent, to_agent, amount_mpsi, reason)  → move ψ between wallets
get_transactions(agent_id, limit=50)    → transaction history
get_leaderboard(limit=20)              → ranked by q_score DESC
apply_genesis_bonus(agent_id)          → award genesis ψ if eligible (first 1000 users)

Storage: ~/.friday/economy.db (SQLite, WAL)
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

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "economy.db"
_LOCK = threading.RLock()

# ─────────────────────────────────────────────────────────────────────────────
#  EARNING / SPENDING CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

PSI_CREATE_CONTENT    = 10_000   # mψ (10ψ) per creation
PSI_LIKE              = 1_000    # mψ per like/share received
PSI_TASK              = 5_000    # mψ per federation task completed
ETA_BANDWIDTH_PER_MB  = 100      # mψ per MB

# Genesis bonus cohorts
_GENESIS_COHORTS = [
    (1_000,  1_000_000),   # cohort 1: first 1000 → 1000ψ
    (2_000,    500_000),   # cohort 2: next 1000  → 500ψ
    (4_000,    250_000),   # cohort 3: next 2000  → 250ψ
]

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    agent_id    TEXT PRIMARY KEY,
    psi_balance INTEGER DEFAULT 0,
    eta_balance INTEGER DEFAULT 0,
    q_score     INTEGER DEFAULT 0,
    created_at  TEXT,
    is_genesis  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id          TEXT PRIMARY KEY,
    from_agent  TEXT,
    to_agent    TEXT,
    amount_mpsi INTEGER,
    currency    TEXT,
    reason      TEXT,
    created_at  TEXT,
    signature   TEXT,
    prev_hash   TEXT,
    tx_hash     TEXT
);

CREATE TABLE IF NOT EXISTS genesis_registry (
    agent_id    TEXT PRIMARY KEY,
    cohort      INTEGER,
    bonus_mpsi  INTEGER,
    awarded_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_from    ON transactions(from_agent, created_at);
CREATE INDEX IF NOT EXISTS idx_tx_to      ON transactions(to_agent, created_at);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_wallets_q  ON wallets(q_score);
"""


# ─────────────────────────────────────────────────────────────────────────────
#  CONNECTION HELPERS
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
    """Return the IntegrityEngine singleton. Never raises."""
    try:
        from agent_friday.governance.proof_of_integrity import get_integrity_engine
        return get_integrity_engine()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_wallet(agent_id: str) -> None:
    """INSERT OR IGNORE a wallet row for *agent_id*. Called before any balance op."""
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO wallets (agent_id, psi_balance, eta_balance, q_score, created_at) "
            "VALUES (?,0,0,0,?)",
            (agent_id, _now_iso()),
        )


def _update_q_score(con: sqlite3.Connection, agent_id: str) -> None:
    """Recompute and store q_score = psi_balance - eta_balance in-place."""
    con.execute(
        "UPDATE wallets SET q_score = psi_balance - eta_balance WHERE agent_id = ?",
        (agent_id,),
    )


def _sign_tx(tx_dict: Dict[str, Any]) -> str:
    """
    Ed25519-sign a transaction.

    Signs the canonical JSON of {id, from_agent, to_agent, amount_mpsi,
    currency, reason, created_at} with sort_keys=True.  Returns hex signature
    or 'unsigned' when the engine is unavailable.
    """
    try:
        engine = _get_engine()
        if engine is None:
            return "unsigned"
        payload_fields = {
            "id":          tx_dict["id"],
            "from_agent":  tx_dict.get("from_agent") or "",
            "to_agent":    tx_dict.get("to_agent") or "",
            "amount_mpsi": tx_dict["amount_mpsi"],
            "currency":    tx_dict["currency"],
            "reason":      tx_dict.get("reason") or "",
            "created_at":  tx_dict["created_at"],
        }
        payload = json.dumps(payload_fields, sort_keys=True).encode("utf-8")
        sig = engine.sign_payload(payload)
        return sig or "unsigned"
    except Exception:
        return "unsigned"


def _chain_hash(tx_dict: Dict[str, Any], prev_hash: str) -> str:
    """sha256(prev_hash_bytes + tx_json_bytes) for hash-chain integrity."""
    tx_json = json.dumps(
        {k: tx_dict[k] for k in ("id", "from_agent", "to_agent",
                                  "amount_mpsi", "currency", "reason", "created_at")
         if k in tx_dict},
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256((prev_hash or "").encode("utf-8") + tx_json).hexdigest()


def _get_prev_hash(agent_id: str) -> str:
    """Return the tx_hash of the most recent transaction involving *agent_id*."""
    try:
        with _conn() as con:
            row = con.execute(
                """SELECT tx_hash FROM transactions
                   WHERE from_agent=? OR to_agent=?
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (agent_id, agent_id),
            ).fetchone()
            return row["tx_hash"] if row else "genesis"
    except Exception:
        return "genesis"


def _insert_tx(
    from_agent: Optional[str],
    to_agent: Optional[str],
    amount_mpsi: int,
    currency: str,
    reason: str,
    *,
    chain_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build, sign, and insert a transaction row.

    *chain_agent* selects which agent's chain to use for prev_hash (defaults
    to from_agent or to_agent, whichever is set).
    """
    tx_id = str(uuid.uuid4())
    created_at = _now_iso()

    chain_ref = chain_agent or from_agent or to_agent or ""
    prev_hash = _get_prev_hash(chain_ref)

    tx: Dict[str, Any] = {
        "id":          tx_id,
        "from_agent":  from_agent or "",
        "to_agent":    to_agent or "",
        "amount_mpsi": int(amount_mpsi),
        "currency":    currency,
        "reason":      reason,
        "created_at":  created_at,
    }
    tx["signature"] = _sign_tx(tx)
    tx["prev_hash"] = prev_hash
    tx["tx_hash"]   = _chain_hash(tx, prev_hash)

    with _conn() as con:
        con.execute(
            """INSERT INTO transactions
               (id, from_agent, to_agent, amount_mpsi, currency,
                reason, created_at, signature, prev_hash, tx_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (tx["id"], tx["from_agent"], tx["to_agent"], tx["amount_mpsi"],
             tx["currency"], tx["reason"], tx["created_at"],
             tx["signature"], tx["prev_hash"], tx["tx_hash"]),
        )
    return tx


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def get_wallet(agent_id: str) -> Dict[str, Any]:
    """
    Return the wallet dict for *agent_id*, creating it if absent.

    Fields: agent_id, psi_balance, eta_balance, q_score, created_at, is_genesis.
    q_score is always psi_balance - eta_balance (authoritative Python value).
    """
    try:
        with _LOCK:
            _ensure_wallet(agent_id)
            with _conn() as con:
                row = con.execute(
                    "SELECT * FROM wallets WHERE agent_id=?", (agent_id,)
                ).fetchone()
                if not row:
                    return {"agent_id": agent_id, "psi_balance": 0,
                            "eta_balance": 0, "q_score": 0}
                d = dict(row)
                d["q_score"] = d["psi_balance"] - d["eta_balance"]
                return d
    except Exception as e:
        print(f"  [economy] get_wallet failed: {e}")
        return {"agent_id": agent_id, "psi_balance": 0,
                "eta_balance": 0, "q_score": 0}


def earn(agent_id: str, amount_mpsi: int, reason: str) -> Optional[Dict[str, Any]]:
    """
    Credit *amount_mpsi* Positrons (ψ) to *agent_id*.

    Returns the transaction record, or None on failure.
    """
    try:
        amount_mpsi = max(0, int(amount_mpsi))
        with _LOCK:
            _ensure_wallet(agent_id)
            with _conn() as con:
                con.execute(
                    "UPDATE wallets SET psi_balance = psi_balance + ? WHERE agent_id = ?",
                    (amount_mpsi, agent_id),
                )
                _update_q_score(con, agent_id)
            return _insert_tx(
                from_agent=None,
                to_agent=agent_id,
                amount_mpsi=amount_mpsi,
                currency="PSI",
                reason=reason,
                chain_agent=agent_id,
            )
    except Exception as e:
        print(f"  [economy] earn failed: {e}")
        return None


def spend(agent_id: str, amount_meta: int, reason: str) -> Optional[Dict[str, Any]]:
    """
    Record *amount_meta* milliNegatrons (mη) against *agent_id*.

    Increases eta_balance (cost ledger). Does not check for psi solvency —
    agents may run a negative Q temporarily.  Returns the transaction record.
    """
    try:
        amount_meta = max(0, int(amount_meta))
        with _LOCK:
            _ensure_wallet(agent_id)
            with _conn() as con:
                con.execute(
                    "UPDATE wallets SET eta_balance = eta_balance + ? WHERE agent_id = ?",
                    (amount_meta, agent_id),
                )
                _update_q_score(con, agent_id)
            return _insert_tx(
                from_agent=agent_id,
                to_agent=None,
                amount_mpsi=amount_meta,
                currency="ETA",
                reason=reason,
                chain_agent=agent_id,
            )
    except Exception as e:
        print(f"  [economy] spend failed: {e}")
        return None


def mint_negatron(agent_id: str, amount_meta: int, reason: str) -> Optional[Dict[str, Any]]:
    """
    System-mint Negatrons against *agent_id* (e.g. for a violation penalty).

    Identical to spend() but currency="ETA" and reason is prefixed with
    "system:" to mark it as a network-issued debit.
    """
    try:
        amount_meta = max(0, int(amount_meta))
        sys_reason = reason if reason.startswith("system:") else f"system:{reason}"
        with _LOCK:
            _ensure_wallet(agent_id)
            with _conn() as con:
                con.execute(
                    "UPDATE wallets SET eta_balance = eta_balance + ? WHERE agent_id = ?",
                    (amount_meta, agent_id),
                )
                _update_q_score(con, agent_id)
            return _insert_tx(
                from_agent=agent_id,
                to_agent=None,
                amount_mpsi=amount_meta,
                currency="ETA",
                reason=sys_reason,
                chain_agent=agent_id,
            )
    except Exception as e:
        print(f"  [economy] mint_negatron failed: {e}")
        return None


def burn_negatron(agent_id: str, amount_meta: int) -> Optional[List[Dict[str, Any]]]:
    """
    Active annihilation: cancel *amount_meta* mη by spending an equal amount of
    mψ from the same agent's wallet.

    Both balances are decremented atomically.  If the agent lacks sufficient ψ,
    the burn is capped at the current psi_balance.  Returns a list of two tx
    records (burn-psi, burn-eta), or None on failure.
    """
    try:
        amount_meta = max(0, int(amount_meta))
        with _LOCK:
            _ensure_wallet(agent_id)
            with _conn() as con:
                row = con.execute(
                    "SELECT psi_balance, eta_balance FROM wallets WHERE agent_id=?",
                    (agent_id,),
                ).fetchone()
                if not row:
                    return None
                psi_bal = int(row["psi_balance"])
                eta_bal = int(row["eta_balance"])

                # Cap burn at available ψ and η
                burn_psi = min(amount_meta, psi_bal)
                burn_eta = min(amount_meta, eta_bal)

                con.execute(
                    "UPDATE wallets SET psi_balance = psi_balance - ?, "
                    "eta_balance = eta_balance - ? WHERE agent_id = ?",
                    (burn_psi, burn_eta, agent_id),
                )
                _update_q_score(con, agent_id)

            tx_psi = _insert_tx(
                from_agent=agent_id,
                to_agent=None,
                amount_mpsi=burn_psi,
                currency="PSI",
                reason="annihilation:burn-psi",
                chain_agent=agent_id,
            )
            tx_eta = _insert_tx(
                from_agent=agent_id,
                to_agent=None,
                amount_mpsi=burn_eta,
                currency="ETA",
                reason="annihilation:burn-eta",
                chain_agent=agent_id,
            )
            return [tx_psi, tx_eta]
    except Exception as e:
        print(f"  [economy] burn_negatron failed: {e}")
        return None


def transfer(
    from_agent: str,
    to_agent: str,
    amount_mpsi: int,
    reason: str,
) -> Optional[Dict[str, Any]]:
    """
    Move *amount_mpsi* mψ from *from_agent* to *to_agent* atomically.

    Fails (returns None) if *from_agent* has insufficient psi_balance.
    """
    try:
        amount_mpsi = max(0, int(amount_mpsi))
        with _LOCK:
            _ensure_wallet(from_agent)
            _ensure_wallet(to_agent)
            with _conn() as con:
                row = con.execute(
                    "SELECT psi_balance FROM wallets WHERE agent_id=?",
                    (from_agent,),
                ).fetchone()
                if not row or int(row["psi_balance"]) < amount_mpsi:
                    print(f"  [economy] transfer: insufficient psi_balance for {from_agent}")
                    return None

                con.execute(
                    "UPDATE wallets SET psi_balance = psi_balance - ? WHERE agent_id = ?",
                    (amount_mpsi, from_agent),
                )
                _update_q_score(con, from_agent)
                con.execute(
                    "UPDATE wallets SET psi_balance = psi_balance + ? WHERE agent_id = ?",
                    (amount_mpsi, to_agent),
                )
                _update_q_score(con, to_agent)

            return _insert_tx(
                from_agent=from_agent,
                to_agent=to_agent,
                amount_mpsi=amount_mpsi,
                currency="PSI",
                reason=reason,
                chain_agent=from_agent,
            )
    except Exception as e:
        print(f"  [economy] transfer failed: {e}")
        return None


def get_transactions(agent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return up to *limit* transactions involving *agent_id*, newest first."""
    try:
        limit = max(1, int(limit))
        with _conn() as con:
            rows = con.execute(
                """SELECT * FROM transactions
                   WHERE from_agent=? OR to_agent=?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (agent_id, agent_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"  [economy] get_transactions failed: {e}")
        return []


def get_leaderboard(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Return wallets ranked by q_score (psi_balance - eta_balance) descending.

    q_score is recomputed in Python for each row to stay authoritative.
    """
    try:
        limit = max(1, int(limit))
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM wallets ORDER BY (psi_balance - eta_balance) DESC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["q_score"] = d["psi_balance"] - d["eta_balance"]
                result.append(d)
            return result
    except Exception as e:
        print(f"  [economy] get_leaderboard failed: {e}")
        return []


def apply_genesis_bonus(agent_id: str) -> Optional[Dict[str, Any]]:
    """
    Award the early-adopter genesis bonus to *agent_id* if eligible.

    Cohorts (by total registered wallets at time of award):
      cohort 1: wallets 1–1000   → 1 000 000 mψ (1000ψ)
      cohort 2: wallets 1001–2000 → 500 000 mψ (500ψ)
      cohort 3: wallets 2001–4000 → 250 000 mψ (250ψ)

    Idempotent: a second call for the same *agent_id* returns None (already
    awarded).  Returns the wallet dict on success.
    """
    try:
        with _LOCK:
            # Check already awarded
            with _conn() as con:
                already = con.execute(
                    "SELECT agent_id FROM genesis_registry WHERE agent_id=?",
                    (agent_id,),
                ).fetchone()
                if already:
                    return None  # idempotent — already awarded

            _ensure_wallet(agent_id)

            with _conn() as con:
                total = con.execute(
                    "SELECT COUNT(*) AS n FROM wallets"
                ).fetchone()["n"]

            # Determine cohort
            cohort_num: Optional[int] = None
            bonus_mpsi: int = 0
            for threshold, bonus in _GENESIS_COHORTS:
                if total <= threshold:
                    cohort_num = _GENESIS_COHORTS.index((threshold, bonus)) + 1
                    bonus_mpsi = bonus
                    break

            if cohort_num is None or bonus_mpsi == 0:
                return None  # outside genesis window

            # Award
            earned_tx = earn(agent_id, bonus_mpsi, f"genesis:cohort-{cohort_num}")
            if earned_tx is None:
                return None

            now = _now_iso()
            with _LOCK:
                with _conn() as con:
                    con.execute(
                        "UPDATE wallets SET is_genesis=1 WHERE agent_id=?",
                        (agent_id,),
                    )
                    con.execute(
                        "INSERT OR IGNORE INTO genesis_registry "
                        "(agent_id, cohort, bonus_mpsi, awarded_at) VALUES (?,?,?,?)",
                        (agent_id, cohort_num, bonus_mpsi, now),
                    )
            return get_wallet(agent_id)
    except Exception as e:
        print(f"  [economy] apply_genesis_bonus failed: {e}")
        return None
