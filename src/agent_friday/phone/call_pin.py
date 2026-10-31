"""The spoken call PIN (owner ruling 8, 2026-10-28).

A call from the owner's cell is caller ID, and caller ID can be forged. So before
Friday reads any of the owner's records on such a call, the caller says the call
PIN. It is on by default; until a PIN is set up, a call from the owner's cell reads
no records at all.

* The PIN is set, changed or switched off only by a press in Settings on this PC
  (routes/phone.py, the same desk-press guard as the other phone settings).
* Only a salted, slow hash is kept, keyed with Friday's governance key, so a copy of
  the file alone does not give the PIN up to a quick search.
* The PIN is 6 to 10 digits. Each call gets three tries (phone/live_call.py); ten wrong
  PINs since it was set lock it until it is set again on the PC (a right PIN does not
  reset the count). Every wrong PIN is a ledger entry and a notice to the owner.
* Until the PIN is said, nothing the caller says reaches the model (live_call): words
  with a number in them are not even kept in the call's history.
* Each change is a signed entry in the action ledger; no entry, no change.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from agent_friday.user_errors import UserFacingValueError

MIN_DIGITS, MAX_DIGITS = 6, 10
TRIES_PER_CALL = 3
LOCK_AFTER = 10                # wrong PINs since it was set lock it until set again at the PC
_LOCK = threading.Lock()
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}

_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
          "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_TOKEN = re.compile(r"[a-z]+|\d")


class PinError(UserFacingValueError):
    """A PIN or setting that cannot be used, in words for the owner."""


def _file() -> Path:
    import agent_friday.core as core
    d = Path(core.FRIDAY_DIR) / "security"
    d.mkdir(parents=True, exist_ok=True)
    return d / "call_pin.json"


def _read() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    f = _file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, f)
    try:
        from agent_friday.services import credential_store as _cs
        _cs.harden_permissions(f)
    except Exception:  # noqa: BLE001
        pass


def _pepper() -> bytes:
    from agent_friday.governance.action_gate import _governance_key
    return hashlib.sha256(b"agent-friday/call-pin/v1\0" + _governance_key()).digest()


def _hash(pin: str, salt: bytes) -> str:
    slow = hashlib.scrypt(pin.encode("utf-8"), salt=salt, **_SCRYPT)
    return hmac.new(_pepper(), slow, hashlib.sha256).hexdigest()


def _ledger(what: str, **extra) -> None:
    """A signed entry for a change; raises when it cannot be written."""
    from agent_friday.governance import action_gate as _gate
    _gate._receipt(dict({"tool": "call_pin_" + what, "class": getattr(_gate, "INTERNAL", "internal"),
                         "decision": "allow", "surface": "phone_settings",
                         "decided_source": "desktop_press"}, **extra))


# ── what the settings page sees ──────────────────────────────────────────────

def required() -> bool:
    """On unless the owner switched it off at the PC. An unreadable file keeps it on."""
    return _read().get("required", True) is not False


def is_set() -> bool:
    d = _read()
    return bool(d.get("hash") and d.get("salt"))


def _wrong(d: dict) -> int:
    try:
        return max(0, int(d.get("wrong") or 0))
    except (TypeError, ValueError):
        return LOCK_AFTER                 # unreadable count: locked (unsure means closed)


def locked() -> bool:
    """Too many wrong PINs since it was set: locked until set again at the PC. A right
    PIN does not reset the count, so slow guessing over days still runs out."""
    return _wrong(_read()) >= LOCK_AFTER


def status() -> dict:
    return {"required": required(), "set": is_set(), "locked": locked(),
            "digits": "%d to %d" % (MIN_DIGITS, MAX_DIGITS)}


# ── changes (a press on this PC; routes/phone.py checks it) ──────────────────

def set_pin(pin: str) -> dict:
    pin = str(pin or "").strip()
    if not pin.isdigit() or not MIN_DIGITS <= len(pin) <= MAX_DIGITS or not pin.isascii():
        raise PinError("The call PIN is %d to %d digits." % (MIN_DIGITS, MAX_DIGITS))
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210":
        raise PinError("Pick a PIN that is not one digit repeated or a run like 1234.")
    salt = secrets.token_bytes(16)
    digest = _hash(pin, salt)
    with _LOCK:
        _ledger("set")
        d = _read()
        d.pop("fails", None)
        d.update({"hash": digest, "salt": salt.hex(), "set_at": int(time.time()), "wrong": 0})
        d.setdefault("required", True)
        _write(d)
    return status()


def set_required(on: bool) -> dict:
    with _LOCK:
        _ledger("required_on" if on else "required_off")
        d = _read()
        d["required"] = bool(on)
        _write(d)
    return status()


def clear() -> dict:
    with _LOCK:
        _ledger("cleared")
        d = _read()
        for k in ("hash", "salt", "set_at", "fails", "wrong"):
            d.pop(k, None)
        _write(d)
    return status()


# ── on a call ────────────────────────────────────────────────────────────────

def digits_in(text: str) -> Optional[str]:
    """The PIN-shaped digits a caller said ("4 7 2 9", "four seven two nine", "4729"),
    or None when the words are not a PIN attempt (no digits, or too few or too many)."""
    out = []
    for tok in _TOKEN.findall(str(text or "").lower()):
        if tok.isdigit():
            out.append(tok)
        elif tok in _WORDS:
            out.append(_WORDS[tok])
    got = "".join(out)
    return got if MIN_DIGITS <= len(got) <= MAX_DIGITS else None


def has_number(text: str) -> bool:
    """Any digit or number word: words that may hold part of a PIN."""
    return any(tok.isdigit() or tok in _WORDS for tok in _TOKEN.findall(str(text or "").lower()))


def verify(spoken_digits: str) -> bool:
    """True for the PIN. A wrong one is counted (and must be: if the count cannot be
    written, the PIN is treated as locked), written in the action ledger and shown to
    the owner. A locked or unset PIN never matches."""
    with _LOCK:
        d = _read()
        if not (d.get("hash") and d.get("salt")) or _wrong(d) >= LOCK_AFTER:
            return False
        try:
            good = hmac.compare_digest(_hash(str(spoken_digits or ""), bytes.fromhex(d["salt"])),
                                       str(d["hash"]))
        except Exception:  # noqa: BLE001 - a check that cannot run does not match
            good = False
        if good:
            return True
        d["wrong"] = _wrong(d) + 1
        try:
            _write(d)
        except OSError:
            d["wrong"] = LOCK_AFTER
        count = d["wrong"]
    _tell_owner(count)
    return False


def _tell_owner(count: int) -> None:
    """A wrong call PIN is the owner's to know about: a ledger entry and a notice."""
    try:
        _ledger("wrong", count=int(count), decided_source="phone_call")
    except Exception:  # noqa: BLE001
        pass
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title="A wrong call PIN was said",
                body=("Someone calling from your cell's number said a wrong call PIN (%d so far). "
                      "%s" % (count, "The PIN is now locked: set it again in Friday's phone settings "
                              "on this computer." if count >= LOCK_AFTER else
                              "If that was not you, someone may be using your number.")),
                priority="high", source="phone", kind="call_pin_wrong", emitter="CP1",
                dedupe_key="call-pin-wrong")
    except Exception:  # noqa: BLE001
        pass
