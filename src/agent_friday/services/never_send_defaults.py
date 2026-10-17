"""Card numbers and bank or account numbers: on every never-send list by default (owner ruling, 2026-10-17).

Two entries join the owner's never-send list on every install, new and existing:

* ``[card numbers]``             -- any payment-card number (13 to 19 digits that pass the card
                                    checksum, so order numbers and long digit runs do not trip it);
* ``[bank and account numbers]`` -- an IBAN (its own checksum), and a number written as a bank or
                                    account number: after "account number", "acct", "a/c",
                                    "routing number", "ABA" or "sort code".

They are entries like any other: the never-send floor (``judgment_gate.never_send_hits``, run by
``egress_gate`` before every cloud call in every mode, "Cloud for everything" included) refuses a
payload that holds one, and the owner can remove either (Settings > Privacy & Data > Locks). The
brackets mark a kind of number rather than literal text; the floor matches them by pattern.

The migration runs once per install (``MARK`` in ``privacy_shield.json``'s ``defaults_applied``):
it adds whichever entry is missing, then never again, so a removal sticks. When it added anything
it says so once in Activity (emitter NS1). An unreadable shield file is left exactly as it is and
the migration tries again on the next start.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

_log = logging.getLogger("friday.never_send_defaults")

CARD = "[card numbers]"
BANK = "[bank and account numbers]"
ENTRIES = (CARD, BANK)
LABELS = {CARD: "Card numbers", BANK: "Bank and account numbers"}
SOURCE = "default"
MARK = "numbers-2026-10"
NOTICE_KEY = "never-send-defaults-numbers"

_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
_LABELLED_RE = re.compile(
    r"(?i)\b(?:(?:bank\s+)?account\s*(?:number|no\.?|num\.?|#)|acct\.?\s*(?:number|no\.?|#)?|a/c\s*(?:no\.?|number)?"
    r"|routing\s*(?:number|no\.?|#)?|aba(?:\s*(?:number|no\.?|#))?|sort\s*code)"
    r"\s*(?:is\s*)?[:#]?\s*(\d[\d -]{4,32}\d)")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "").upper()
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1
    except ValueError:
        return False


def has_card_number(text: str) -> bool:
    for m in _CARD_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return True
    return False


def has_bank_number(text: str) -> bool:
    t = text or ""
    for m in _IBAN_RE.finditer(t):
        # The match may run on into a following capitalised word; try each shorter grouping too.
        parts = m.group(0).split(" ")
        if any(_iban_ok(" ".join(parts[:k])) for k in range(len(parts), 0, -1)):
            return True
    for m in _LABELLED_RE.finditer(t):
        if len(re.sub(r"\D", "", m.group(1))) >= 6:
            return True
    return False


_DETECT = {CARD: has_card_number, BANK: has_bank_number}


def is_category(entry) -> bool:
    return str(entry or "").strip().casefold() in {e.casefold() for e in ENTRIES}


def category_hit(entry: str, text: str) -> bool:
    """True when `text` holds a number of the kind the category entry names."""
    key = next((e for e in ENTRIES if e.casefold() == str(entry or "").strip().casefold()), None)
    return bool(key and _DETECT[key](text))


def migrate() -> list:
    """Once per install: add the missing default entries, mark it done, say so once. Returns what
    was added. Never raises; an unreadable shield file is left alone and retried next start."""
    try:
        from agent_friday.services import privacy_shield_store as ps
        if ps.default_applied(MARK):
            return []
        added = ps.add("never_send", list(ENTRIES), source=SOURCE)
        ps.mark_default_applied(MARK, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    except Exception as e:  # noqa: BLE001 - the floor's own checks still run; this retries next start
        _log.warning("default never-send entries were not added: %s", type(e).__name__)
        return []
    if added:
        _log.info("never-send list: added %d default entr%s", len(added), "y" if len(added) == 1 else "ies")
        _notify(added)
    return added


def _notify(added: list) -> None:
    names = " and ".join(LABELS.get(a, a).lower() for a in added)
    try:
        import agent_friday.notifications_engine as _ne
        _ne.push(title="Card and bank numbers are on your never-send list", body=(
            "Friday now refuses to send %s to any cloud model, whatever you chose for the cloud. "
            "They are entries on your never-send list like any other; you can remove either in "
            "Settings > Privacy & Data > Locks." % names),
            priority="low", source="privacy", kind="never_send_defaults", tier="fyi",
            emitter="NS1", dedupe_key=NOTICE_KEY, target={"workspace": "settings", "tab": "privacy"})
    except Exception as e:  # noqa: BLE001
        _log.warning("never-send defaults notice not shown: %s", e)


def listing() -> list:
    """The never-send list for the Locks page: [{entry, label, kind}] with the owner's own words for
    their entries and a plain name for a category. Read on this PC only."""
    from agent_friday.services import privacy_shield_store as ps
    out = []
    for e in ps.entries("never_send"):
        key = next((c for c in ENTRIES if c.casefold() == e.strip().casefold()), None)
        out.append({"entry": e, "label": LABELS[key] if key else e,
                    "kind": "category" if key else "words"})
    return out
