"""The Locks page: how the person's things are locked on this PC, in plain words.

First-run spec C13. One place that says, line by line, what protects what,
what the person chose, and what that cannot stop, each line built from the
store that actually decides it, never from a setting that only claims it. A
line whose truth Friday cannot read says "I couldn't tell", never a guess.

What is not built yet says so ("comes with the encryption update") rather
than being left off: a page about locks that is silent about a missing lock
reads as a lock that exists.

Reads only; nothing here changes a setting, and nothing leaves the PC.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

_log = logging.getLogger("friday.locks")

#: Always shown at the bottom of the page.
HONEST_LIMIT = (
    "Your files are locked on disk where Friday has a lock for them. While Friday "
    "is running, anything you run on this PC under your own Windows account could "
    "read what Friday can read, the same as it could open your documents. Keep "
    "Windows updated, use antivirus, and don't run programs you don't trust.")

#: The Windows settings pages the page may open, by name; nothing else.
WINDOWS_PAGES = {"device_encryption": "ms-settings:deviceencryption",
                 "other_users": "ms-settings:otherusers"}

_POSTURE_WORDS = {"local_private": "Only on this PC",
                  "cloud_guarded": "Cloud for public things",
                  "cloud_unrestricted": "Cloud for everything"}


def _line(key: str, title: str, state: str, words: str, **extra) -> dict:
    return {"key": key, "title": title, "state": state, "words": words, **extra}


def _lock() -> dict:
    try:
        from agent_friday import core
        enc = getattr(core, "_VAULT_ENCRYPTION_STATE", {}) or {}
        on = bool(enc.get("enabled"))
    except Exception:
        on = None
    if on is None:
        return _line("lock", "Your lock", "unknown", "I couldn't tell whether the vault's lock is on.")
    if on:
        return _line("lock", "Your lock", "on",
                     "Your private records (finance, health, legal, family) are encrypted with your "
                     "passphrase (AES-256-GCM).")
    return _line("lock", "Your lock", "off",
                 "No passphrase is set, so your private records are stored readable on this disk. "
                 "Set one in Privacy & Data > Vault.")


def _recovery() -> dict:
    return _line("recovery", "Recovery", "not_built",
                 "There is no recovery kit yet; it comes with the encryption update. Until then, "
                 "if you lose your passphrase the vault cannot be opened, so keep it in a "
                 "password manager.")


def _everything_else() -> dict:
    return _line("stores", "Everything else", "not_built",
                 "Your conversations, memory and most of Friday's databases are not encrypted yet. "
                 "Encrypting every store comes with the encryption update; until then they are "
                 "protected by your Windows account only.")


#: Test seam: callable() -> "on" | "off" | "unknown".
DISK_STATE = None


def _disk() -> dict:
    """Windows' own disk encryption. Reading it without administrator rights
    relies on a shell property whose values are not documented, so until that
    reading is verified on known machines the answer is "I couldn't tell"."""
    state = DISK_STATE() if DISK_STATE else "unknown"
    words = {"on": "Windows encryption is on for this disk.",
             "off": ("Windows encryption is off for this disk. Windows' own disk encryption also "
                     "protects everything else, including copies left behind by deleted files."),
             "unknown": ("I couldn't tell whether Windows' disk encryption is on. Windows' own "
                         "settings page can show you.")}[state]
    return _line("disk", "Your disk", state, words, open_page="device_encryption")


def _today_egress() -> dict:
    """What left this PC today, by destination, from the egress log."""
    try:
        from agent_friday.services import egress_gate
        path = egress_gate._DEFAULT_LOG
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        by = {}
        if path.exists():
            with open(path, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    try:
                        e = json.loads(ln)
                    except ValueError:
                        continue
                    if float(e.get("ts") or 0) < start:
                        continue
                    row = by.setdefault(str(e.get("provider") or "?"), {"sent": 0, "stopped": 0})
                    row["sent" if e.get("action") == "allow" else "stopped"] += 1
        return by
    except Exception:
        return {}


def _where() -> dict:
    try:
        from agent_friday.privacy import cloud_consent
        st = cloud_consent.resolve()
        posture = _POSTURE_WORDS.get(st.choice or "", "") if st.answered else ""
    except Exception:
        posture = ""
    try:
        from agent_friday.services import privacy_shield_store as ps
        never = len(ps.entries("never_send"))
        shielded = len(ps.entries("watchlist"))
    except Exception:
        never = shielded = 0
    today = _today_egress()
    sent = sum(r["sent"] for r in today.values())
    words = ("%s. " % posture if posture else "Not chosen yet, so every cloud call keeps all the safeguards on. ")
    words += ("Your never-send list has %d item%s of your own, plus passwords, keys, ID and account "
              "numbers and your vault's private records, always. %d of your own identifiers are kept "
              "out of anything sent to a cloud model." % (never, "" if never == 1 else "s", shielded))
    return _line("where", "Where I think", posture and "chosen" or "unanswered", words,
                 today=[{"destination": k, **v} for k, v in sorted(today.items())],
                 today_words=("Nothing left this PC today." if not sent else
                              "%d request%s left this PC today." % (sent, "" if sent == 1 else "s")))


def _keys() -> dict:
    try:
        from agent_friday.services import credential_store as cs
        names = sorted(cs.list_provider_keys())
    except Exception:
        names = []
    words = ("Stored encrypted on this PC: %s. Stored is not the same as working; each key is "
             "checked when it is used." % ", ".join(names)) if names else "No provider keys are stored."
    return _line("keys", "Keys you've given me", "stored" if names else "none", words, providers=names)


def _records() -> dict:
    checks = []
    try:
        from agent_friday.services import setup_record
        v = setup_record.verify()
        checks.append({"name": "Setup record", "ok": v["ok"], "count": v["count"], "why": v["reason"]})
    except Exception as e:  # noqa: BLE001
        checks.append({"name": "Setup record", "ok": None, "why": type(e).__name__})
    try:
        from agent_friday.services import inbox_cleanup
        v = inbox_cleanup.verify_receipts()
        checks.append({"name": "Inbox clean-up receipts", "ok": v["ok"], "count": v["count"], "why": v["reason"]})
    except Exception as e:  # noqa: BLE001
        checks.append({"name": "Inbox clean-up receipts", "ok": None, "why": type(e).__name__})
    bad = [c for c in checks if c["ok"] is False]
    if bad:
        words = "One record was changed after it was written: %s (%s)." % (bad[0]["name"], bad[0]["why"])
        state = "changed"
    elif any(c["ok"] is None for c in checks):
        words, state = "I couldn't check every record.", "unknown"
    else:
        words, state = "All records check out: nothing was changed after it was written.", "ok"
    return _line("records", "My records", state, words, checks=checks)


def _held() -> dict:
    """Mail archives and statements read without the owner's yes, held from
    every cloud payload (decision O7), and whether the hold is failing closed."""
    from agent_friday.services import judgment_gate
    rh = judgment_gate.raw_hold_status()
    files = list(rh.get("held") or [])
    if rh["fail_closed"]:
        return _line("held", "Held from the cloud", "fail_closed",
                     "I couldn't keep the list of held mail archives and statements in full (%s), so "
                     "every cloud message is stopped while this lasts. Start the list fresh to clear it."
                     % rh["why"], held_files=files, can_reset=True)
    if not files:
        return _line("held", "Held from the cloud", "none",
                     "No mail archive or statement is held: none has been opened without your yes.",
                     held_files=[], can_reset=False)
    n = len(files)
    return _line("held", "Held from the cloud", "held",
                 "%d mail archive%s or statement%s I opened %s held: nothing from %s goes to a cloud "
                 "model until you allow that file." % (n, "" if n == 1 else "s", "" if n == 1 else "s",
                                                       "is" if n == 1 else "are", "it" if n == 1 else "them"),
                 held_files=files, can_reset=True)


def _updates() -> dict:
    try:
        from agent_friday.services import update_check
        st = update_check.status()
    except Exception:
        return _line("updates", "Updates", "unknown", "I couldn't read the update check's state.")
    if not st.get("enabled"):
        return _line("updates", "Updates", "off",
                     "Checking for new versions is off. Turn it on in About.")
    when = st.get("last_checked_at") or "not yet"
    ver = st.get("verification")
    tail = {"verified": " The newest release's manifest was signed and verified.",
            "unsigned": " The newest release had no signed manifest to check.",
            "no_trusted_key": " This copy has no release-signing key to check against yet."}.get(ver or "", "")
    return _line("updates", "Updates", "on", "Checked %s.%s" % (when, tail),
                 installer=("The installer is not code-signed yet; check its SHA-256 against the "
                            "release page before running it."))


def _forget() -> dict:
    return _line("forget", "Forget", "partial",
                 "You can forget a person (Contacts) and erase all of Friday's data (Privacy & Data). "
                 "Forgetting one source, with every copy made from it, comes with the encryption update.")


def view() -> dict:
    """Every line of the Locks page, in the order the page shows them."""
    lines = []
    for fn in (_lock, _recovery, _everything_else, _disk, _where, _keys, _records, _held, _updates, _forget):
        try:
            lines.append(fn())
        except Exception as e:  # noqa: BLE001
            _log.warning("locks line %s failed: %s", fn.__name__, type(e).__name__)
            lines.append(_line(fn.__name__.strip("_"), fn.__name__.strip("_").title(), "unknown",
                               "I couldn't read this one."))
    return {"lines": lines, "honest_limit": HONEST_LIMIT, "at": time.time()}


RESET_KIND = "raw_holds_reset"
RESET_WORDS = (
    "Start the list of held mail archives and statements fresh. Lines from the %d file%s held "
    "now could then reach a cloud model when something quotes them, and the warning that the "
    "hold could not be kept in full is cleared. Any of those files read again is held again "
    "until you allow it. Your never-send list is not changed.")
_HOOKED = {"done": False}


def held_records() -> dict:
    """The raw records held from the cloud, by file, for the Locks page."""
    from agent_friday.services import judgment_gate
    return judgment_gate.raw_hold_status()


def ask_for_held(path: str) -> dict:
    """Raise the file-access card for one held file now (the owner's press)."""
    from pathlib import Path

    from agent_friday.services import file_grants, judgment_gate
    if not path or file_grants._raw_key(path) not in judgment_gate.held_keys():
        return {"ok": False, "message": "That file isn't held."}
    if not Path(path).is_file():
        return {"ok": False, "message": "That file is no longer there; start the list fresh to let it go."}
    card = file_grants.ask_for_raw_source(Path(path), force=True)
    if not card or not card.get("ok"):
        return {"ok": False, "message": "No card could be raised for that file."}
    return {"ok": True, "approval_id": card.get("approval_id")}


def request_reset(*, requested_by: str = "owner") -> dict:
    """Raise the one card that starts the hold list fresh. Changes nothing
    until the owner approves it on screen."""
    import uuid

    from agent_friday.services import approvals
    ensure_hook()
    n = int(held_records().get("files") or 0)
    words = RESET_WORDS % (n, "" if n == 1 else "s")
    card = approvals.create_approval(
        kind=RESET_KIND, subject_type="privacy", subject_id="raw-holds-%s" % uuid.uuid4().hex[:12],
        title="Start the held-records list fresh", description=words, action_description=words,
        payload={"handler": RESET_KIND, "files": n}, requested_by=requested_by,
        action_class="irreversible", force_gate=True)
    return {"ok": True, "approval_id": card.get("approval_id"), "status": card.get("status")}


def _on_reset_decision(record: dict) -> None:
    from agent_friday.services import approvals, judgment_gate
    if record.get("kind") != RESET_KIND or record.get("status") != "approved":
        return
    if record.get("decided_by") not in approvals.SCREEN_ONLY_KINDS.get(RESET_KIND, frozenset()):
        return
    if not approvals.claim_for_execution(record["approval_id"]):
        return
    out = judgment_gate.reset_raw_holds(confirmed_by=record["decided_by"])
    approvals.mark_used(record["approval_id"], "locks", out)


def ensure_hook() -> None:
    if _HOOKED["done"]:
        return
    from agent_friday.services import approvals
    approvals.register_decision_hook(RESET_KIND, _on_reset_decision)
    _HOOKED["done"] = True


def open_windows_page(name: str) -> dict:
    """Open one of WINDOWS_PAGES (the person's own press)."""
    import os
    import sys
    uri = WINDOWS_PAGES.get(str(name or ""))
    if not uri:
        return {"ok": False, "message": "That isn't a page Friday opens."}
    if not sys.platform.startswith("win"):
        return {"ok": False, "message": "This opens a Windows page; it needs Windows."}
    try:
        os.startfile(uri)  # noqa: S606 - a fixed Windows settings URI from the table above
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "message": "Windows did not open the page (%s)." % type(e).__name__}
