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
Doctor report carries them, its text lists each with where it is, and the
Doctor page links each to its section.

A check with no repair listed says so ("no repair here"), so the owner is
never left guessing whether one exists.

``FLOW`` says, for each repair, what it does at each step after the Doctor
has detected, explained and proposed it (north star §28.1-2): preview, back
up, verify, roll back, receipt. Each answer is "yes", "partly", "n/a" (the
step does not apply, and why) or "not yet" (a gap, kept visible here the way
self_verify.NOT_COVERED keeps its gaps). Every offered repair carries its
flow, in the Doctor report and its text.
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

STEPS = ("preview", "backup", "verify", "rollback", "receipt")
STATES = ("yes", "partly", "n/a", "not yet")
_HAND = ("n/a", "a step you take; Friday changes nothing")

#: repair id -> {step: (state, words)} (§28.1-2).
FLOW: dict = {
    "update_friday": {"preview": _HAND, "backup": ("n/a", "nothing in the data folder changes"),
                      "verify": ("yes", "the upgrade check runs again at the next start"),
                      "rollback": ("n/a", "nothing changes"), "receipt": _HAND},
    "migration_retry": {"preview": ("not yet", "the upgrade runs at the restart without showing its steps first"),
                        "backup": ("yes", "the data folder (not downloads, caches or logs) is copied before "
                                          "the first step"),
                        "verify": ("yes", "each step's own check runs after it"),
                        "rollback": ("yes", "a step that fails has its files put back"),
                        "receipt": ("yes", "a migration entry in the action ledger")},
    "diagnostic_start": {"preview": ("yes", "the page shows the next start's mode before you press"),
                         "backup": ("n/a", "only a marker file is written"),
                         "verify": ("partly", "the marker is read back"),
                         "rollback": ("yes", "the same press turns it off"),
                         "receipt": ("yes", "a checkpoint decision in the action ledger")},
    "leave_safe_mode": {"preview": ("yes", "the page shows how the next start is planned"),
                        "backup": ("n/a", "only the marker is removed; the ledger keeps that it was a failed "
                                          "upgrade, and its open items are marked sorted"),
                        "verify": ("partly", "the plan is read back"),
                        "rollback": ("partly", "Start in safe mode next time turns it on again, without the old "
                                               "reason"),
                        "receipt": ("yes", "a checkpoint decision in the action ledger")},
    "safe_mode_next": {"preview": ("yes", "the page shows how the next start is planned"),
                       "backup": ("n/a", "only a marker file is written"),
                       "verify": ("partly", "the plan is read back"),
                       "rollback": ("yes", "Leave safe mode turns it off"),
                       "receipt": ("yes", "a checkpoint decision in the action ledger")},
    "start_normally": {"preview": _HAND, "backup": ("n/a", "a restart changes nothing"),
                       "verify": ("yes", "the checks run again at the start"),
                       "rollback": ("n/a", "nothing changes"), "receipt": _HAND},
    "rebuild_now": {"preview": ("partly", "the page lists what is waiting to be rebuilt"),
                    "backup": ("n/a", "an index is made again from Friday's own stores"),
                    "verify": ("partly", "the index's own result is recorded; there is no separate re-check"),
                    "rollback": ("n/a", "a failed rebuild is tried again, up to three times, then listed for you"),
                    "receipt": ("partly", "the ask and the result, each in the action ledger; a run that "
                                          "has to wait records no result")},
    "mark_sorted": {"preview": ("yes", "the item is shown before you press"),
                    "backup": ("n/a", "only the time it was sorted is written"),
                    "verify": ("n/a", "nothing to check"),
                    "rollback": ("not yet", "there is no undo"),
                    "receipt": ("yes", "a checkpoint decision in the action ledger")},
    "make_bundle": {"preview": ("partly", "you choose what goes in and the passphrase"),
                    "backup": ("n/a", "it writes a new file and changes nothing"),
                    "verify": ("yes", "the bundle is opened and checked right after it is made"),
                    "rollback": ("yes", "nothing else is changed; a bundle that fails its check is deleted, "
                                        "as a scheduled one is"),
                    "receipt": ("yes", "a backup entry in the action ledger for a bundle made and checked, "
                                       "and one for a bundle that failed")},
    "rehearse_restore": {"preview": ("n/a", "it is itself a dry run"),
                         "backup": ("n/a", "it writes only to a temporary folder"),
                         "verify": ("yes", "the bundle is checked and its contents compared with its manifest"),
                         "rollback": ("n/a", "nothing is changed"),
                         "receipt": ("yes", "a backup entry in the action ledger")},
    "review_ledger": {"preview": ("yes", "you acknowledge the breaks you were shown; if they changed, it is "
                                         "refused"),
                      "backup": ("n/a", "a record is added; none is rewritten"),
                      "verify": ("yes", "the anchor is read back and the ledger checked again"),
                      "rollback": ("n/a", "an acknowledgement is itself a record"),
                      "receipt": ("yes", "an acknowledgement entry in the action ledger")},
    "restore_key": {"preview": ("partly", "friday restore --dry-run shows the plan first; it is optional"),
                    "backup": ("yes", "the current data folder is moved aside, never deleted"),
                    "verify": ("yes", "every file's hash is checked, staged and in place"),
                    "rollback": ("yes", "a restore cut off before the swap puts the old folder back"),
                    "receipt": ("yes", "a restore entry in the action ledger")},
    "settings_by_hand": {"preview": _HAND, "backup": ("n/a", "the Doctor never writes settings.json"),
                         "verify": ("yes", "the settings check runs again when the page loads"),
                         "rollback": ("n/a", "the Doctor changes nothing"), "receipt": _HAND},
    "restore_authority": {"preview": ("partly", "the page shows the hold; there is no confirm step"),
                          "backup": ("n/a", "only the hold is removed"),
                          "verify": ("not yet", "it is not re-checked afterwards"),
                          "rollback": ("partly", "Revoke all puts the hold back, and also takes back every "
                                                 "permission given since"),
                          "receipt": ("yes", "an authority entry, written before the hold is lifted")},
}


def flow(rid: str) -> dict:
    """{step: "state: words"} for one repair; a repair not described here
    says so at each step."""
    f = FLOW.get(rid)
    if not f:
        return {st: "not described: this build has no record of this step for it" for st in STEPS}
    return {st: "%s: %s" % f[st] for st in STEPS}


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
        out.append({"id": r["id"], "label": r["label"], "where": r["where"], "flow": flow(r["id"])})
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
