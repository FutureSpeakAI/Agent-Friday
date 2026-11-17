"""When Friday may offer a standing grant (Compressor eval K5, brief section 3.2). Offers only; no grant.

The count comes from the label rows services/approval_novelty already writes on every decision, keyed
by ``ActionPattern.fingerprint``; there is no second counter. ``offer_due`` answers whether an offer
card may be raised for the card just decided. It reads and returns a value: familiarity never silences
a card (tests/unit/test_standing_offers.py::test_a_thousand_approvals_without_a_press_change_no_card).

An offer is due when all of these hold:
  * the pattern is not excluded (``action_pattern.exclusion_of``);
  * ``OFFER_AFTER_APPROVALS`` approvals of the pattern inside ``WINDOW_DAYS``, none denied since;
  * the latest instance was labelled ``seen``;
  * no denial of the pattern, and no declined offer for it, in the last ``SUPPRESS_DAYS``;
  * the owner has not said "don't offer this again";
  * no offer is pending and none was raised today.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Optional

from agent_friday.paths import friday_home
from agent_friday.services import action_pattern, approval_novelty

OFFER_AFTER_APPROVALS = 5
WINDOW_DAYS = 30
SUPPRESS_DAYS = 30
_DAY_S = 86400.0

STATE_FILE = friday_home() / "standing_offers.json"
_LOCK = threading.Lock()


def _empty() -> Dict[str, Any]:
    return {"declined_at": {}, "never_again": [], "pending": "", "last_offer_at": 0.0}


def _load() -> Dict[str, Any]:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return {**_empty(), **state}
    except (FileNotFoundError, ValueError):
        pass
    return _empty()


def _save(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _recent_rows(fingerprint: str, now: float) -> list:
    since = now - WINDOW_DAYS * _DAY_S
    return [r for r in approval_novelty.decided_rows()
            if r.get("pattern") == fingerprint and float(r.get("t") or 0) >= since]


def _approvals_since_last_denial(rows: list) -> int:
    run = 0
    for row in rows:
        run = run + 1 if row.get("decision") == "approved" else 0
    return run


def _denied_recently(fingerprint: str, now: float) -> bool:
    cutoff = now - SUPPRESS_DAYS * _DAY_S
    return any(r.get("decision") == "denied" and float(r.get("t") or 0) >= cutoff
               for r in approval_novelty.decided_rows() if r.get("pattern") == fingerprint)


def offer_due(card: Dict[str, Any], now: Optional[float] = None) -> Optional[action_pattern.ActionPattern]:
    """The pattern to offer a standing grant for after this decided card, or None. Never raises."""
    try:
        now = time.time() if now is None else now
        pattern = action_pattern.from_card(card)
        if pattern is None or action_pattern.exclusion_of(card) is not None:
            return None
        fingerprint = pattern.fingerprint()
        rows = _recent_rows(fingerprint, now)
        if (not rows or rows[-1].get("approval_id") != card.get("approval_id")
                or rows[-1].get("label") != approval_novelty.Novelty.SEEN.value):
            return None
        if _approvals_since_last_denial(rows) < OFFER_AFTER_APPROVALS or _denied_recently(fingerprint, now):
            return None
        with _LOCK:
            state = _load()
        if fingerprint in state["never_again"] or state["pending"]:
            return None
        if now - float(state["declined_at"].get(fingerprint) or 0) < SUPPRESS_DAYS * _DAY_S:
            return None
        if now - float(state["last_offer_at"]) < _DAY_S:
            return None
        return pattern
    except Exception:
        return None


def record_offer_raised(fingerprint: str, now: Optional[float] = None) -> None:
    with _LOCK:
        state = _load()
        state["pending"] = fingerprint
        state["last_offer_at"] = time.time() if now is None else now
        _save(state)


def record_offer_closed(fingerprint: str, *, declined: bool, never_again: bool = False,
                        now: Optional[float] = None) -> None:
    """The offer card was decided. A decline stops offers for SUPPRESS_DAYS; "don't offer again"
    stops them for good; an accepted offer only clears the pending slot."""
    with _LOCK:
        state = _load()
        if state["pending"] == fingerprint:
            state["pending"] = ""
        if declined:
            state["declined_at"][fingerprint] = time.time() if now is None else now
        if never_again and fingerprint not in state["never_again"]:
            state["never_again"].append(fingerprint)
        _save(state)
