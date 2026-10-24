"""services/onboarding_status.py -- when onboarding is done (North Star 9.6, BD-09 I4).

The `.setup_complete` marker says the setup steps ran (the wizard, the installer, the scheduler
and the tray read it, and it keeps that meaning). It never meant the person got value. Onboarding
is done only when all six of section 9.6 hold:

    reviewed     the owner has marked at least one "What I see so far" item
    action       Friday has completed one useful action the owner approved (a ledger outcome
                 with status ok that names the approval it ran under): the first governed action
                 (services/first_action, A27) or any earlier one
    receipt      that action has its receipt (the same ledger entry, by its sequence number)
    where_ran    the owner said, by their own press, that they saw where THAT action ran
                 (A27: the owner's recorded skip of the first action meets these three, and the
                 check says it was skipped)
    memory       the owner confirmed or corrected a memory: "Remember this" or "Wrong" on an
                 item, or their own edit or deletion of a line on the memory card
    goal         a goal exists, or the owner explicitly skipped it by their own press

Each check reads a record that already exists; a check that cannot be read counts as not met
(never as met). The owner's two answers live in ~/.friday/first_synthesis/onboarding.json.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_lock = threading.Lock()
CHECKS = ("reviewed", "action", "receipt", "where_ran", "memory", "goal")
LABELS = {
    "reviewed": "You reviewed what I see so far",
    "action": "I completed one useful action you approved (or you skipped it)",
    "receipt": "That action has its receipt",
    "where_ran": "You saw where it ran",
    "memory": "You confirmed or corrected one memory",
    "goal": "You set a goal, or chose to skip it",
}


def _path() -> Path:
    from agent_friday import paths
    d = paths.friday_home() / "first_synthesis"
    d.mkdir(parents=True, exist_ok=True)
    return d / "onboarding.json"


def _answers() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _reviewed() -> bool:
    from agent_friday.services import first_synthesis
    return first_synthesis.reviewed()


def _action_receipt() -> Optional[dict]:
    """The first governed action's own receipt when it ran (services/first_action), else the first
    receipt of an approved action that ran: an `outcome` with status ok that names the approval it
    ran under. A reading tool's outcome names no approval, so it never counts."""
    try:
        from agent_friday.services import first_action
        seq = first_action.done_receipt()
        if seq is not None:
            return {"seq": seq, "tool": first_action.TOOL, "ts": None, "first_action": True}
    except Exception:
        pass
    from agent_friday.services import action_ledger
    for r in action_ledger.records("outcome"):
        if r.get("status") == "ok" and r.get("approval") and r.get("_seq") is not None:
            return {"seq": r.get("_seq"), "tool": str(r.get("tool") or "")[:60], "ts": r.get("_ts")}
    return None


def _memory() -> bool:
    try:
        from agent_friday.services import first_synthesis
        if first_synthesis.corrections():
            return True
    except Exception:
        pass
    # The owner's own edit or deletion of a card line. An owner add is not counted: a migration
    # writes those by itself, and adding is neither confirming nor correcting.
    from agent_friday.services import core_memory
    rows = core_memory._read_jsonl(core_memory._journal_path())
    return any(isinstance(r, dict) and r.get("actor") == "owner" and r.get("op") in ("replace", "remove")
               for r in rows)


def _goal() -> bool:
    if _answers().get("goal_skipped_ts"):
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
    met["where_ran"] = bool(met["receipt"] and _answers().get("saw_where_it_ran_seq") == receipt["seq"])
    fa: Dict[str, Any] = {"state": "unknown"}
    try:
        from agent_friday.services import first_action
        fa = first_action.state()
    except Exception:
        pass
    skipped = set()
    if fa.get("state") == "skipped" and not met["action"]:
        # A27: the owner's recorded skip of the first governed action is an answer.
        for k in ("action", "receipt", "where_ran"):
            met[k] = True
            skipped.add(k)
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
            "checks": [{"id": k, "label": LABELS[k], "met": met[k], "skipped": k in skipped}
                       for k in CHECKS],
            "receipt": receipt, "first_action": fa}


def answer(which: str, seq=None) -> Dict[str, Any]:
    """The owner's own answer (routes/first_synthesis, behind the desk press)."""
    with _lock:
        a = _answers()
        if which == "saw_where_it_ran":
            r = _action_receipt()
            if not r:
                return {"ok": False, "error": "There is no completed action with a receipt yet."}
            if isinstance(seq, bool) or not isinstance(seq, int) or seq != r["seq"]:
                return {"ok": False, "error": "That is not the action whose receipt is shown; look again."}
            a["saw_where_it_ran_seq"] = r["seq"]
        elif which == "skip_goal":
            a["goal_skipped_ts"] = time.time()
        elif which == "skip_first_action":
            from agent_friday.services import first_action
            out = first_action.skip("owner:ui")
            if not out.get("ok"):
                return {"ok": False, "error": out.get("why") or "not skipped"}
            return {"ok": True, "status": status()}
        else:
            return {"ok": False, "error": "unknown answer"}
        p = _path()
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(a), encoding="utf-8")
        os.replace(tmp, p)
    return {"ok": True, "status": status()}


def done() -> bool:
    return status()["done"]
