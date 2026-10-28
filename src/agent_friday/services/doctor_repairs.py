"""The repairs the Doctor offers for a check that is not healthy (north star
§28.1-1: critical diagnosis and repair do not depend on a model being
available).

Every repair here is deterministic: a press on the Doctor page (or a command
on this PC) that runs Friday's own code, or a plain step the owner takes. None
asks a model, none reaches the network, and none changes the owner's settings
behind their back. ``offered(checks)`` maps each check id that is not healthy
to the repairs for it, as {id, label, where}: ``where`` is the Doctor page
section that holds the press (an anchor), or "" for a step taken elsewhere.
A repair can be limited to the check codes it fits (``codes``: prefixes), so
a newer data folder is never told to retry an upgrade. A check that could not
say anything (unverified) gets repairs only when the check itself failed. The
Doctor report carries them, and its text lists each with where it is.

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
        {"id": "update_friday", "label": "Update Friday to the version that last used this data folder; "
                                         "nothing in it was changed", "where": "", "codes": ("newer_folder",)},
        {"id": "migration_retry", "label": "Restart Friday: the next start tries the upgrade step again",
         "where": "", "codes": ("upgrade_failed", "migration_journal")},
        {"id": "diagnostic_start", "label": "Make the next start a diagnostic start", "where": "#diagnostic",
         "needs": "agent_friday.services.diagnostic_start", "codes": ("upgrade_failed", "migration_journal")},
        {"id": "leave_safe_mode", "label": "Leave safe mode once the upgrade has run", "where": "#safe",
         "codes": ("upgrade_failed",)},
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
        {"id": "leave_safe_mode", "label": "Leave safe mode at the next start", "where": "#safe",
         "codes": ("safe_mode_marker",)},
        {"id": "diagnostic_start", "label": "The last starts failed: make the next start a diagnostic start",
         "where": "#diagnostic", "needs": "agent_friday.services.diagnostic_start",
         "codes": ("safe_mode_failed_starts",)},
        {"id": "start_normally", "label": "This start was asked for in safe mode: start Friday normally",
         "where": "", "codes": ("safe_mode_start", "safe_mode_diagnostic")},
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
        {"id": "restore_key", "label": "Restore a Friday Bundle with the receipts chosen (friday restore "
                                       "<file> --replace): the key travels with them", "where": ""},
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


def for_check(cid: str, code: str = "") -> list:
    out = []
    for r in REPAIRS.get(cid, []):
        if r.get("needs") and not _have(r["needs"]):
            continue
        if r.get("codes") and not any(str(code or "").startswith(c) for c in r["codes"]):
            continue
        out.append({"id": r["id"], "label": r["label"], "where": r["where"]})
    return out or [dict(NONE)]


def offered(checks: Iterable[dict]) -> dict:
    """{check id: [repairs]} for every check that is not healthy. Unavailable
    by configuration or hardware is not a fault and gets none; unverified gets
    them only when the check itself failed to run."""
    calm = {"healthy", "unavailable_by_configuration", "unavailable_by_hardware"}
    out = {}
    for c in checks:
        if not (isinstance(c, dict) and c.get("id")) or c.get("state") in calm:
            continue
        code = str(c.get("code") or "")
        if c.get("state") == "unverified" and not (code.startswith("check_failed:") or code == "bad_state"):
            continue
        out[c["id"]] = for_check(c["id"], code)
    return out
