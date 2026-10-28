"""The repairs the Doctor offers for a check that is not healthy (north star
§28.1-1: critical diagnosis and repair do not depend on a model being
available).

Every repair here is deterministic: a press on the Doctor page (or a command
on this PC) that runs Friday's own code, or a plain step the owner takes. None
asks a model, none reaches the network, and none changes the owner's settings
behind their back. ``offered(checks)`` maps each check id that is not healthy
to the repairs for it, as {id, label, where}: ``where`` is the Doctor page
section that holds the press (an anchor), or "" for a step taken elsewhere.
The Doctor report carries the ids and labels; the page links them.

A check with no repair listed says so ("no repair here"), so the owner is
never left guessing whether one exists.
"""
from __future__ import annotations

import importlib.util
from typing import Iterable

NEEDS_MODEL = False                     # the rule for every entry below; tests hold it

#: check id -> repairs. `needs` names a module that must be in this build for
#: the repair to be offered.
REPAIRS: dict = {
    "migrations": [
        {"id": "migration_retry", "label": "Restart Friday: the next start tries the upgrade step again",
         "where": ""},
        {"id": "diagnostic_start", "label": "Make the next start a diagnostic start", "where": "#diagnostic",
         "needs": "agent_friday.services.diagnostic_start"},
        {"id": "leave_safe_mode", "label": "Leave safe mode once the upgrade has run", "where": "#safe"},
    ],
    "after_upgrade": [
        {"id": "rebuild_now", "label": "Rebuild an index now", "where": "#afterupgrade",
         "needs": "agent_friday.services.derived_rebuild"},
        {"id": "mark_sorted", "label": "Mark an item sorted once you have dealt with it", "where": "#afterupgrade",
         "needs": "agent_friday.services.upgrade_review"},
    ],
    "local_brain": [
        {"id": "local_model_repair", "label": "Restart, check or reinstall the local model", "where": "#localmodel",
         "needs": "agent_friday.services.local_repair"},
    ],
    "model_files": [
        {"id": "local_model_verify", "label": "Check every model file against its checksum", "where": "#localmodel",
         "needs": "agent_friday.services.local_repair"},
    ],
    "startup": [
        {"id": "diagnostic_start", "label": "Make the next start a diagnostic start", "where": "#diagnostic",
         "needs": "agent_friday.services.diagnostic_start"},
    ],
    "boot": [
        {"id": "diagnostic_start", "label": "Make the next start a diagnostic start", "where": "#diagnostic",
         "needs": "agent_friday.services.diagnostic_start"},
        {"id": "safe_mode_next", "label": "Start in safe mode next time", "where": "#safe"},
    ],
    "crashes": [
        {"id": "diagnostic_start", "label": "Make the next start a diagnostic start", "where": "#diagnostic",
         "needs": "agent_friday.services.diagnostic_start"},
    ],
    "safe_mode": [
        {"id": "leave_safe_mode", "label": "Leave safe mode at the next start", "where": "#safe"},
    ],
    "backup": [
        {"id": "make_bundle", "label": "Make a Friday Bundle now", "where": "#bundle"},
        {"id": "rehearse_restore", "label": "Rehearse a restore from the newest scheduled bundle",
         "where": "#schedule"},
    ],
    "receipt_chain": [
        {"id": "review_ledger", "label": "Open Activity and review the break: acknowledging adds a record and "
                                         "never rewrites one", "where": ""},
    ],
    "governance_key": [
        {"id": "restore_key", "label": "Restore a Friday Bundle with the receipts chosen: the key travels with "
                                       "them", "where": "#bundle"},
    ],
    "settings": [
        {"id": "settings_by_hand", "label": "Fix settings.json by hand or restore a Friday Bundle; the Doctor never "
                                            "rewrites it", "where": ""},
    ],
    "settings_read": [
        {"id": "settings_by_hand", "label": "Fix settings.json by hand or restore a Friday Bundle; the Doctor never "
                                            "rewrites it", "where": ""},
    ],
    "authority": [
        {"id": "restore_authority", "label": "Restore action authority (you give permissions back one by one)",
         "where": "#authority"},
    ],
}

NONE = {"id": "none", "label": "No repair here: the check's code says what is wrong", "where": ""}


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def for_check(cid: str) -> list:
    out = []
    for r in REPAIRS.get(cid, []):
        if r.get("needs") and not _have(r["needs"]):
            continue
        out.append({"id": r["id"], "label": r["label"], "where": r["where"]})
    return out or [dict(NONE)]


def offered(checks: Iterable[dict]) -> dict:
    """{check id: [repairs]} for every check that is not healthy (unavailable
    by configuration or hardware is not a fault and gets none)."""
    calm = {"healthy", "unavailable_by_configuration", "unavailable_by_hardware"}
    return {c["id"]: for_check(c["id"]) for c in checks
            if isinstance(c, dict) and c.get("id") and c.get("state") not in calm}
