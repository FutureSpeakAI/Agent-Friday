"""The TV's grown-up window, issued and checked by this PC (DECISIONS-FOR-STEPHEN TV-P1).

The TV says who is watching in its own header (``grownup | family | kids``). That header alone never carries
the owner's memory (services/tv_memory_access): the memory gate also needs a window grant this PC issued:

  * The PC keeps its own verifier of the grown-up PIN (scrypt, salted, HMAC-signed with the governance key,
    like household.json). The PIN itself is never stored. It is set from the PC's Settings page or an
    owner-bound phone or tablet, never from the TV.
  * The TV sends the PIN once, over the gateway's pinned TLS 1.3 session, right after the owner unlocks it
    there (POST /tv/v1/grownup/open). On a match the PC returns an opaque grant: 32 random bytes, held only in
    this process (as a hash), bound to the TV that opened it. It ends on the PC's clock when the TV goes idle
    (no grown-up turn for IDLE_S), at TTL_S however busy the TV is, or at POST /tv/v1/grownup/close
    (test_a_window_ends_when_the_tv_goes_idle, test_a_window_ends_at_its_cap_however_busy_the_tv_is). It is not
    bound to one device session: the TV renews its session hourly and each screen keeps its own link, and every
    request already proves the TV with its key (test_tv_grownup_window_route.py::
    test_the_window_outlives_a_session_renewal_but_not_a_change_of_tv).
  * Misses lock the whole household out, not one device (FREE_TRIES, then doubling waits up to LOCK_MAX_S;
    test_the_lock_out_counts_every_tv_in_the_household, test_the_lock_out_schedule). The count lives in this
    process, so a PC restart starts it again; the TV cannot restart the PC.
  * Only the household TV (kind tv, bound to the family member) may open one. Kids get no memory whatever
    the window says (tv_memory_access.allows).
  * Every open, refusal and close leaves a ledger row (``tv_grownup_window``), never the PIN or the grant
    (test_no_ledger_row_carries_the_pin).

Tests: tests/unit/test_tv_grownup_window.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TTL_S = 3600                     # the cap, however busy the TV is (test_the_window_is_short)
IDLE_S = 600                     # no grown-up turn for this long ends it (test_a_window_ends_when_the_tv_goes_idle)
OPENED_BY_PIN = "pin"
ENDED_IDLE, ENDED_CAP = "idle", "cap"     # a ledger "ended" row's by
_HOUSEHOLD = "household"         # one miss count for every TV (test_the_lock_out_counts_every_tv_in_the_household)
FREE_TRIES = 5
LOCK_BASE_S = 30                 # the first lock-out; each further miss doubles it (test_the_lock_out_schedule)
LOCK_MAX_S = 3600
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
_PIN_MIN, _PIN_MAX = 4, 8        # the TV's own grown-up PIN (safety/Pin.java; test_the_pins_length_is_the_tvs)

_log = logging.getLogger(__name__)
_LOCK = threading.Lock()
_GRANTS: dict = {}               # sha256(grant) -> {"dev": id, "until": cap, "seen": last turn, "by": "pin"}
_PIN_GEN = [0]                   # moves on with every set_pin: an open checked against an older PIN is refused
_MISSES: dict = {}               # _HOUSEHOLD -> {"n": misses, "until": t}


@dataclass(frozen=True)
class Window:
    """A live window this PC issued, as a turn carries it. Only this type unlocks memory in a TV turn
    (test_tv_memory_access.py::test_only_a_window_this_pc_issued_unlocks_memory)."""
    id: str                      # the first 16 hex of sha256(grant): names the window, cannot open it
    by: str

    def trace(self) -> dict:
        return {"id": self.id, "by": self.by}


class WindowRefused(Exception):
    def __init__(self, status: int, message: str, code: str):
        super().__init__(message)
        self.status, self.code = status, code


def _now() -> float:
    return time.time()


def _path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "security" / "tv_grownup_pin.json"


def _key() -> bytes:
    from agent_friday.governance.proof_of_integrity import get_governance_key
    return get_governance_key()


def _sign(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_key(), b"friday-tv-grownup-pin-v1|" + raw, hashlib.sha256).hexdigest()


def _derive(pin: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)


def _valid_pin(pin) -> bool:
    return isinstance(pin, str) and pin.isdigit() and pin.isascii() and _PIN_MIN <= len(pin) <= _PIN_MAX


def _ledger(event: str, dev: Optional[dict], **extra) -> None:
    try:
        from agent_friday.services import action_ledger
        action_ledger.append("tv_grownup_window", {"event": event, "device_kind": (dev or {}).get("kind"),
                                                   "principal": ("device:%s" % dev.get("id")) if dev
                                                   else "owner:%s" % extra.get("by", "owner"), **extra})
    except Exception as e:
        _log.warning("tv_grownup_window: ledger row %s not written (%s)", event, type(e).__name__)


def _load() -> Optional[dict]:
    """The signed verifier, or None. Read under the lock a save holds, so on Windows a save's os.replace never
    meets an open reader (test_the_pin_file_is_read_only_under_the_lock)."""
    try:
        with _LOCK:
            raw = _path().read_text(encoding="utf-8")
        doc = json.loads(raw)
        body, sig = doc["body"], doc["sig"]
        if isinstance(body, dict) and hmac.compare_digest(_sign(body), str(sig)):
            return body
    except Exception:
        pass
    return None


# ── the verifier ────────────────────────────────────────────────────────────

def has_pin() -> bool:
    return _load() is not None


def set_pin(pin, dev: Optional[dict] = None, *, by: str = "owner") -> None:
    """Store a verifier for `pin`. Only an owner action calls this (the Settings page's verified click, an
    owner-bound device). Every open window ends: a new PIN is a new start."""
    if not _valid_pin(pin):
        raise ValueError("the PIN is 4 to 8 digits")
    salt = os.urandom(16)
    body = {"v": 1, "salt": salt.hex(), **_SCRYPT, "hash": _derive(pin, salt, **_SCRYPT).hex()}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # One step under the lock: the new verifier, the generation and the end of every window move together, so
    # no open can start between them (test_an_old_pin_open_started_during_a_save_gets_no_window,
    # test_an_old_pin_open_just_before_a_save_takes_the_lock_gets_no_window) and two saves
    # never share the temporary file (test_two_saves_at_once_leave_one_pin_and_both_finish).
    with _LOCK:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"body": body, "sig": _sign(body)}), encoding="utf-8")
        os.replace(tmp, p)
        _PIN_GEN[0] += 1
        _GRANTS.clear()
        _MISSES.clear()
    _ledger("pin_set", dev, by=by)


def _matches(pin) -> bool:
    body = _load()
    if body is None or not _valid_pin(pin):
        return False
    try:
        got = _derive(pin, bytes.fromhex(body["salt"]), int(body["n"]), int(body["r"]), int(body["p"]))
    except Exception:
        return False
    return hmac.compare_digest(got.hex(), str(body["hash"]))


# ── windows ─────────────────────────────────────────────────────────────────

def _is_household_tv(dev: Optional[dict]) -> bool:
    if not dev or str(dev.get("kind") or "").lower() != "tv":
        return False
    try:
        from agent_friday.tv_gateway import household
        return household.member_for_device(str(dev.get("id"))) == household.FAMILY_ID
    except Exception:
        return False


def _hash(grant: str) -> str:
    return hashlib.sha256(str(grant).encode("utf-8")).hexdigest()


def open_with_pin(dev: dict, session_key: str, pin) -> str:
    """A grant for this TV (opened on a live session), or WindowRefused (403 wrong PIN, 429 locked out, 409 no PIN set,
    403 not the household TV)."""
    if not _is_household_tv(dev):
        raise WindowRefused(403, "only the household TV opens a grown-up window", "not_household_tv")
    if not session_key:
        raise WindowRefused(401, "device session required", "session_required")
    if not has_pin():
        raise WindowRefused(409, "set the grown-up PIN on the PC or your phone first", "no_pin")
    dev_id, now = str(dev["id"]), _now()
    # The try is counted before the PIN is checked, under the lock, so tries sent in parallel cannot outrun the
    # lock-out (test_wrong_pins_sent_at_once_cannot_outrun_the_lock_out). A right PIN clears the household count,
    # so each real unlock gives the next FREE_TRIES again (test_a_wrong_pin_is_refused_and_repeated_misses_lock_it).
    with _LOCK:
        m = _MISSES.setdefault(_HOUSEHOLD, {"n": 0, "until": 0.0})
        if m["until"] > now:
            raise WindowRefused(429, "too many tries; wait %d s" % int(m["until"] - now + 1), "locked")
        m["n"] += 1
        tries = m["n"]
        gen = _PIN_GEN[0]
        if tries >= FREE_TRIES:
            m["until"] = now + min(LOCK_MAX_S, LOCK_BASE_S * 2 ** (tries - FREE_TRIES))
    if not _matches(pin):
        _ledger("refused", dev, misses=tries)
        raise WindowRefused(403, "that PIN is not right", "wrong_pin")
    grant = secrets.token_urlsafe(32)
    with _LOCK:
        if gen != _PIN_GEN[0]:
            # The PIN changed while this one was being checked (test_a_pin_changed_while_an_open_is_checking_wins).
            raise WindowRefused(409, "the grown-up PIN was just changed; enter the new one", "pin_changed")
        _MISSES.pop(_HOUSEHOLD, None)
        for h in [h for h, v in _GRANTS.items() if v["dev"] == dev_id]:
            del _GRANTS[h]                                # one window per TV (test_one_window_per_tv)
        _GRANTS[_hash(grant)] = {"dev": dev_id, "until": now + TTL_S, "seen": now, "by": OPENED_BY_PIN}
    _ledger("opened", dev, by=OPENED_BY_PIN, seconds=TTL_S, idle_s=IDLE_S)
    return grant


def window_for(dev: Optional[dict], grant: str, *, touch: bool = False) -> Optional[Window]:
    """The live window this PC issued to this device (test_the_window_a_turn_used_is_named_for_its_trace), or None.
    `touch`: a grown-up turn is using it, so the idle clock starts again (the messages route); a status read
    never keeps it open (test_looking_at_a_window_does_not_keep_it_open). An ended window's row names the TV
    that opened it (test_an_ended_window_is_recorded_under_the_tv_that_opened_it)."""
    if not dev or not grant:
        return None
    h, now, ended = _hash(grant), _now(), None
    with _LOCK:
        v = _GRANTS.get(h)
        if v is None:
            return None
        if v["until"] <= now or now - v["seen"] >= IDLE_S:
            _GRANTS.pop(h, None)
            ended = ENDED_CAP if v["until"] <= now else ENDED_IDLE
        elif v["dev"] != str(dev.get("id")):
            return None
        elif touch:
            v["seen"] = now
    if ended:
        _ledger("ended", {"id": v["dev"], "kind": "tv"}, by=ended)
        return None
    return Window(id=h[:16], by=v["by"])


def check(dev: Optional[dict], grant: str, *, touch: bool = False) -> bool:
    """True only for a live grant this PC issued to this device (window_for)."""
    return window_for(dev, grant, touch=touch) is not None


def close(dev: Optional[dict]) -> None:
    dev_id = str((dev or {}).get("id"))
    with _LOCK:
        gone = [h for h, v in _GRANTS.items() if v["dev"] == dev_id]
        for h in gone:
            del _GRANTS[h]
    _ledger("closed", dev, windows=len(gone))


def status(dev: Optional[dict], grant: str) -> dict:
    return {"has_pin": has_pin(), "open": check(dev, grant), "ttl_s": TTL_S, "idle_s": IDLE_S}


def _reset_for_tests() -> None:
    with _LOCK:
        _GRANTS.clear()
        _MISSES.clear()
