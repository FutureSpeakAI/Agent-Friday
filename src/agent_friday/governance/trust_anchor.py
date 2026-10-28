"""governance/trust_anchor.py -- one-time trust carry-overs that deleting files cannot reset.

Two lists start by trusting what the owner already had, once, at the first start that has the
list: the remote add-on hosts (governance/mcp_hosts, BD-07 M1) and the connectors from before
tool manifests (governance/tool_manifests, A30). Each kept its "it already ran" marker in its
own files, so deleting them made a later start carry over again: whatever was configured then
became trusted. The owner ruled (2026-10-28) that deleting files must not reset trust.

Two records, neither in Friday's data folder alone:

* The carry-over itself, as a durable signed entry in the append-only action ledger
  (services/action_ledger): which hosts / connectors it trusted. Releases (a card answered,
  a host removed) are entries too. A deleted list is rebuilt from this, never extended.
* A "carried over" flag per list in the OS keychain, beside the ledger's own anchor. It
  outlives the ledger: if the ledger files are deleted and the owner acknowledges the break
  (which he must, to unhold outward actions), the flag still says the carry-over ran, so it
  never runs again; with no ledger record to rebuild from, the list starts empty and every
  connector or host asks (fail closed). Where there is no keychain (the anchor would live in
  the data folder) a fresh carry-over is refused.

A carry-over needs a healthy ledger (action_ledger.hold_reason is None), the flag not set, and
the anchor to have moved past the new entry; otherwise nothing is carried over.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

KIND = "trust_carry_over"
RELEASE = "trust_carry_over_released"
_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}
_NEG_TTL_S = 30.0                 # an unreadable ledger is asked again after this long
_TEST_FLAGS: Dict[str, str] = {}
_log = logging.getLogger("friday.trust_anchor")


class AnchorUnavailable(RuntimeError):
    """The ledger cannot say whether a carry-over happened: carry nothing over."""


def _al():
    from agent_friday.services import action_ledger
    return action_ledger


def ledger_ok() -> bool:
    try:
        return _al().hold_reason() is None
    except Exception:
        return False


# ── the keychain flag ────────────────────────────────────────────────────────

def _flag_account(name: str) -> str:
    return "%s:trust-carry-over:%s" % (_al()._anchor_account(), name)


def _test_store() -> bool:
    al = _al()
    return al._testing() and os.environ.get("FRIDAY_LEDGER_ANCHOR") != "keyring"


def flag_get(name: str) -> Optional[str]:
    """When the carry-over for `name` ran (the keychain's word), or None. Raises
    AnchorUnavailable when there is no keychain to ask."""
    acct = _flag_account(name)
    if _test_store():
        return _TEST_FLAGS.get(acct)
    try:
        import keyring
        from keyring.errors import NoKeyringError
    except ImportError:
        raise AnchorUnavailable("no OS keychain: the carry-over cannot be anchored outside the data folder")
    try:
        return keyring.get_password(_al().ANCHOR_SERVICE, acct)
    except NoKeyringError:
        raise AnchorUnavailable("no OS keychain: the carry-over cannot be anchored outside the data folder")
    except Exception as e:  # noqa: BLE001
        raise AnchorUnavailable("the OS keychain could not be read (%s)" % type(e).__name__)


def _flag_set(name: str) -> None:
    acct, val = _flag_account(name), "%.0f" % time.time()
    if _test_store():
        _TEST_FLAGS[acct] = val
        return
    import keyring
    keyring.set_password(_al().ANCHOR_SERVICE, acct, val)


# ── the ledger record ────────────────────────────────────────────────────────

def _read(name: str) -> Optional[Dict[str, Any]]:
    """{"items": {...}} still carried for `name`, or None if the ledger records no carry-over.
    Raises AnchorUnavailable when the ledger cannot be read in full."""
    try:
        rows = _al().entries([KIND, RELEASE], newest_first=False, strict=True, archive=True,
                             where=lambda e: (e.get("rec") or {}).get("list") == name)
    except Exception as e:  # noqa: BLE001
        raise AnchorUnavailable("the action ledger could not be read (%s)" % type(e).__name__)
    found: Optional[Dict[str, Any]] = None
    for e in rows:
        rec = e.get("rec") or {}
        if e.get("kind") == KIND and found is None:
            items = rec.get("items")
            found = {"items": dict(items) if isinstance(items, dict) else
                     {str(i): True for i in (items or [])}, "seq": e.get("seq")}
        elif e.get("kind") == RELEASE and found is not None:
            found["items"].pop(str(rec.get("item")), None)
    return found


def _ck(name: str) -> str:
    try:
        return "%s|%s" % (name, _al().ledger_dir())
    except Exception:
        return name


def carried(name: str, *, fresh: bool = False) -> Optional[Dict[str, Any]]:
    """What the carry-over for `name` still trusts, or None if it never ran. Raises
    AnchorUnavailable when that cannot be known (the ledger cannot be read, or the keychain
    says it ran but the ledger has no record of it: deleted)."""
    k = _ck(name)
    with _LOCK:
        hit = _CACHE.get(k)
        if hit is not None and not fresh:
            if hit[0] == "err":
                if time.time() - hit[2] < _NEG_TTL_S:
                    raise AnchorUnavailable(hit[1])
            else:
                v = hit[1]
                return {"items": dict(v["items"]), "seq": v.get("seq")} if v else None
        try:
            v = _read(name)
            if v is None and flag_get(name):
                raise AnchorUnavailable("the carry-over for %s ran but the ledger no longer has it" % name)
        except AnchorUnavailable as e:
            _CACHE[k] = ("err", str(e), time.time())
            raise
        _CACHE[k] = ("ok", v)
        return {"items": dict(v["items"]), "seq": v.get("seq")} if v else None


def record(name: str, items: Dict[str, Any]) -> None:
    """Anchor a carry-over before the list is written. Raises if it cannot be anchored (the
    ledger has a problem, it ran before, no keychain, the anchor did not move), so the caller
    carries nothing over."""
    if not ledger_ok():
        raise AnchorUnavailable("the action ledger has a problem the owner has not reviewed")
    with _LOCK:
        if flag_get(name):
            raise AnchorUnavailable("the carry-over for %s already ran" % name)
        if _read(name) is not None:
            raise AnchorUnavailable("a carry-over for %s is already recorded" % name)
        al = _al()
        entry = al.append(KIND, {"list": name, "items": dict(items), "made_by": "carried_over"},
                          durable=True)
        try:
            stored, _where = al.anchor_get()
        except Exception:
            stored = None
        if not stored or int(stored[0]) < int((entry or {}).get("seq") or 0):
            raise AnchorUnavailable("the ledger's anchor did not move past the carry-over")
        _flag_set(name)
        _CACHE[_ck(name)] = ("ok", {"items": dict(items), "seq": (entry or {}).get("seq")})


def release(name: str, item: str, *, why: str) -> None:
    """The owner answered for one carried item: it is no longer carried. Raises if the release
    cannot be recorded, so the caller changes nothing (a deleted list would otherwise be
    rebuilt with it)."""
    _al().append(RELEASE, {"list": name, "item": str(item)[:200], "why": str(why)[:80]}, durable=True)
    with _LOCK:
        hit = _CACHE.get(_ck(name))
        if hit and hit[0] == "ok" and hit[1]:
            hit[1]["items"].pop(str(item), None)


def _reset_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()
        _TEST_FLAGS.clear()
