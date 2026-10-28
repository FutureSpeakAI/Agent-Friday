"""governance/trust_anchor.py -- one-time trust carry-overs, anchored in the action ledger.

Two lists start by trusting what the owner already had, once, at the first start that has the
list: the remote add-on hosts (governance/mcp_hosts, BD-07 M1) and the connectors from before
tool manifests (governance/tool_manifests, A30). Each list kept its "it already ran" marker in
its own files, so deleting those files made a later start carry over again: whatever was
configured at that moment became trusted. The owner ruled (2026-10-28) that deleting files
must not reset trust.

So the carry-over is recorded first in the append-only, signed action ledger
(services/action_ledger), whose own anchor detects a ledger that was cut short or changed:

* ``record(name, items)`` writes the carry-over (which hosts / which connectors and their
  fingerprints) as a durable ledger entry BEFORE the list is written. If it cannot be written,
  nothing is carried over.
* ``carried(name)`` says whether a carry-over already happened and what it trusted, minus
  what was ``release``d since (an answered card). A list whose files were deleted is rebuilt
  from this, never extended, and never carried over again.
* ``ledger_ok()``: a ledger that was changed on disk or cannot be checked
  (action_ledger.hold_reason) means no carry-over at all: fail closed, every connector asks.

Only the owner can reset the ledger's own anchor, and doing so already discards its history
(the same accepted limit as decision 9 in DECISIONS-FOR-STEPHEN.md).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

KIND = "trust_carry_over"
RELEASE = "trust_carry_over_released"
_LOCK = threading.Lock()
_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}
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


def _read(name: str) -> Optional[Dict[str, Any]]:
    """{"items": {...}} still carried for `name`, or None if no carry-over is recorded.
    Raises AnchorUnavailable when the ledger cannot be read in full."""
    try:
        rows = _al().entries([KIND, RELEASE], newest_first=False, verified=True, strict=True,
                             archive=True,
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
    """Cache key: the list and the ledger it was read from (one ledger per Friday home)."""
    try:
        return "%s|%s" % (name, _al().ledger_dir())
    except Exception:
        return name


def carried(name: str, *, fresh: bool = False) -> Optional[Dict[str, Any]]:
    k = _ck(name)
    with _LOCK:
        if fresh or k not in _CACHE:
            _CACHE[k] = _read(name)
        v = _CACHE[k]
        return {"items": dict(v["items"]), "seq": v.get("seq")} if v else None


def record(name: str, items: Dict[str, Any]) -> None:
    """Anchor a carry-over before the list is written. Raises if it cannot be anchored, so the
    caller carries nothing over."""
    if not ledger_ok():
        raise AnchorUnavailable("the action ledger has a problem the owner has not reviewed")
    with _LOCK:
        if _read(name) is not None:
            raise AnchorUnavailable("a carry-over for %s is already recorded" % name)
        _al().append(KIND, {"list": name, "items": dict(items), "made_by": "carried_over"},
                     durable=True)
        _CACHE[_ck(name)] = {"items": dict(items), "seq": None}


def release(name: str, item: str, *, why: str) -> None:
    """The owner answered for one carried item (its card): it is no longer carried."""
    try:
        _al().append(RELEASE, {"list": name, "item": str(item)[:200], "why": str(why)[:80]},
                     durable=True)
    except Exception as e:  # noqa: BLE001 - the list's own row is released either way
        _log.error("trust anchor: release of %s/%s not recorded: %s", name, item, type(e).__name__)
    with _LOCK:
        v = _CACHE.get(_ck(name))
        if v:
            v["items"].pop(str(item), None)


def _reset_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()
