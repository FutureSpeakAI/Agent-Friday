"""services/onboarding_status.py -- when onboarding is done (North Star 9.6, BD-09 I4).

The `.setup_complete` marker says the setup steps ran (the wizard, the installer, the scheduler
and the tray read it, and it keeps that meaning). It never meant the person got value. Onboarding
is done only when all six of section 9.6 hold:

    reviewed     the owner has marked at least one "What I see so far" item
    action       Friday has completed one useful action the owner approved (a ledger receipt)
    receipt      that action has its receipt (the same ledger entry, by its sequence number)
    where_ran    the owner has said they saw where it ran (onboarding.json action_place_ack)
    memory       at least one memory confirmed or corrected by the owner
    goal         a goal exists, or the owner explicitly skipped it (onboarding.json goal_skipped)

Each check reads a record that already exists; none of them writes. A check that cannot be read
counts as not met (never as met).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

CHECKS = ("reviewed", "action", "receipt", "where_ran", "memory", "goal")
LABELS = {
    "reviewed": "You reviewed what I see so far",
    "action": "I completed one useful action you approved",
    "receipt": "That action has its receipt",
    "where_ran": "You saw where it ran",
    "memory": "You confirmed or corrected one memory",
    "goal": "You set a goal, or chose to skip it",
}


def _home() -> Path:
    from agent_friday import paths
    return paths.friday_home()


def _acks() -> dict:
    """routes/core_routes /api/onboarding/acks writes this file under core.FRIDAY_DIR."""
    try:
        try:
            from agent_friday import core
            base = Path(core.FRIDAY_DIR)
        except Exception:
            base = _home()
        d = json.loads((base / "onboarding.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _reviewed() -> bool:
    from agent_friday.services import first_synthesis
    return first_synthesis.reviewed()


def _action_receipt() -> Optional[dict]:
    """The first receipt of an approved action that ran: an `outcome` with status ok that names
    the approval it ran under. A reading tool's outcome names no approval, so it never counts."""
    from agent_friday.services import action_ledger
    for r in action_ledger.records("outcome"):
        if r.get("status") == "ok" and r.get("approval"):
            return {"seq": r.get("_seq"), "tool": str(r.get("tool") or "")[:60], "ts": r.get("_ts"),
                    "approval": str(r.get("approval"))[:60]}
    return None


def _memory() -> bool:
    from agent_friday.services import core_memory
    rows = core_memory._read_jsonl(core_memory._journal_path())
    if any(isinstance(r, dict) and r.get("actor") == "owner" for r in rows):
        return True
    try:
        from agent_friday.services import first_synthesis
        if first_synthesis.corrections():
            return True
    except Exception:
        pass
    try:
        from agent_friday.services import import_staging
        if (import_staging.counts() or {}).get("accepted"):
            return True
    except Exception:
        pass
    return False


def _goal() -> bool:
    if _acks().get("goal_skipped") is True:
        return True
    from agent_friday.services import goals
    return bool(goals.list_goals())


def status() -> Dict[str, Any]:
    """Each of the six checks, met or not, and done only when all six are met."""
    met: Dict[str, bool] = {}
    receipt = None
    try:
        met["reviewed"] = bool(_reviewed())
    except Exception:
        met["reviewed"] = False
    try:
        receipt = _action_receipt()
    except Exception:
        receipt = None
    met["action"] = receipt is not None
    met["receipt"] = bool(receipt and receipt.get("seq") is not None)
    met["where_ran"] = met["receipt"] and _acks().get("action_place_ack") is True
    try:
        met["memory"] = bool(_memory())
    except Exception:
        met["memory"] = False
    try:
        met["goal"] = bool(_goal())
    except Exception:
        met["goal"] = False
    try:
        from agent_friday.core import _is_existing_install
        setup = bool(_is_existing_install())
    except Exception:
        setup = False
    return {"ok": True, "done": all(met[k] for k in CHECKS), "setup_complete": setup,
            "checks": [{"id": k, "label": LABELS[k], "met": met[k]} for k in CHECKS],
            "receipt": receipt}


def done() -> bool:
    return status()["done"]
