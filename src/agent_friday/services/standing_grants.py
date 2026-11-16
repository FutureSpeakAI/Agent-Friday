"""Standing grants: the shell's offer card, the press that accepts it, and the grant it makes
(Compressor eval K5, brief sections 3.4 to 3.6).

Familiarity never silences a card. Only the owner's press on an offer card, raised here and nowhere
else, makes a grant, and the grant is made here and nowhere else (tests/unit/test_standing_grants.py::
test_only_this_module_makes_a_standing_grant). ``create_grant`` refuses a pattern-scoped grant that
``standing_refusal`` refuses, so a hand-made request is refused too.

A grant answers only "the owner would be asked again for what they already said yes to". ``cover``
returns a grant for a call only when the pattern is not excluded, the instance is a ``seen`` one and
nothing in how the gate judged it points at private data or Laya; every other check (cLaws, ledger
holds, safe mode) already ran before the gate asks.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agent_friday.services import action_pattern, approval_novelty, standing_offers

_log = logging.getLogger("friday.standing_grants")

OFFER_KIND = "standing_grant_offer"
PATTERN_SCOPE = "pattern:"
GRANT_SECONDS = 30 * 86400
MAX_GRANT_SECONDS = 90 * 86400
GRANT_USES = 50
SHELL = "shell"
PRESS = "owner:ui"
_ESCALATION_WORDS = ("laya", "private data")


def scope_of(fingerprint: str) -> str:
    return PATTERN_SCOPE + fingerprint


def standing_refusal(tools, expires_in_seconds: float, max_uses: int, created_by: str) -> Optional[str]:
    """Why a pattern-scoped grant may not be made, or None. Read by create_grant."""
    if len(tools or []) != 1:
        return "a standing grant covers exactly one tool"
    excluded = action_pattern.tool_exclusion(next(iter(tools)))
    if excluded is not None:
        return "a standing grant is never made for this action (%s)" % excluded.value
    if expires_in_seconds > MAX_GRANT_SECONDS or max_uses > GRANT_USES:
        return "a standing grant lasts at most %d days and %d uses" % (MAX_GRANT_SECONDS // 86400, GRANT_USES)
    if created_by != PRESS and not str(created_by).startswith("device:"):
        return "a standing grant is made only on the owner's press"
    return None


def _ledger(kind: str, **fields: Any) -> None:
    from agent_friday.services import action_ledger
    action_ledger.try_append(kind, fields, durable=True)


def _snapshot(card: Dict[str, Any]) -> Dict[str, Any]:
    payload = card.get("payload") or {}
    return {"kind": "tool_confirm", "policy_class": card.get("policy_class"),
            "principal": card.get("principal") or "owner",
            "requested_by": card.get("requested_by") or "",
            "payload": {"tool": payload.get("tool"), "input": payload.get("input") or {},
                        "origin": payload.get("origin") or ""}}


def maybe_offer(decided_card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """After an approved tool card: raise the offer card when one is due. Never raises."""
    try:
        if decided_card.get("status") != "approved":
            return None
        pattern = standing_offers.offer_due(decided_card)
        if pattern is None:
            return None
        from agent_friday.services import approvals
        fingerprint = pattern.fingerprint()
        where = ", ".join(pattern.destination) or "no named recipient"
        card = approvals.create_approval(
            kind=OFFER_KIND, subject_type="pattern", subject_id=fingerprint,
            title="Stop asking about %s?" % pattern.tool,
            description=("You have approved this same action %d times. Friday can stop asking for "
                         "exactly this one: %s (%s) for %d days or %d uses, whichever is first. "
                         "You can take this back in Settings."
                         % (standing_offers.OFFER_AFTER_APPROVALS, pattern.tool, where,
                            GRANT_SECONDS // 86400, GRANT_USES)),
            action_description="Offer a standing grant for %s" % pattern.tool,
            force_gate=True, requested_by=SHELL,
            payload={"pattern": fingerprint, "snapshot": _snapshot(decided_card)})
        standing_offers.record_offer_raised(fingerprint)
        _ledger("standing_offer_shown", pattern=fingerprint, tool=pattern.tool)
        return card
    except Exception as exc:  # noqa: BLE001
        _log.warning("standing offer not raised: %s", exc)
        return None


def on_offer_decided(card: Dict[str, Any]) -> None:
    """Decision hook for the offer card: a decline is remembered; an approval makes the grant if
    the action is still the one that was shown."""
    payload = card.get("payload") or {}
    fingerprint = str(payload.get("pattern") or "")
    if card.get("status") != "approved":
        standing_offers.record_offer_closed(fingerprint, declined=True)
        _ledger("standing_offer_declined", pattern=fingerprint)
        return
    snapshot = payload.get("snapshot") or {}
    pattern = action_pattern.from_card(snapshot)
    still_what_was_shown = (pattern is not None and pattern.fingerprint() == fingerprint
                            and action_pattern.exclusion_of(snapshot) is None
                            and standing_offers.still_earned(fingerprint))
    standing_offers.record_offer_closed(fingerprint, declined=False)
    if not still_what_was_shown:
        _ledger("standing_offer_stale", pattern=fingerprint)
        return
    from agent_friday.governance import action_gate
    grant = action_gate.create_grant(
        tools=[pattern.tool], scope=scope_of(fingerprint), expires_in_seconds=GRANT_SECONDS,
        max_uses=GRANT_USES, created_by=str(card.get("decided_by") or ""),
        note="standing grant (K5) from card %s" % card.get("approval_id"),
        limits={"subject_kind": "pattern", "principal": pattern.principal})
    _ledger("standing_offer_accepted", pattern=fingerprint, grant=grant["grant_id"],
            by=str(card.get("decided_by") or ""))


def cover(tool_name: str, args: Any, ctx: Dict[str, Any], why: str) -> Optional[dict]:
    """A live standing grant spent on this call, or None when the owner is asked as usual."""
    try:
        if any(word in str(why or "").lower() for word in _ESCALATION_WORDS):
            return None
        principal = str((ctx or {}).get("principal") or "owner")
        card = action_pattern.card_of_call(tool_name, args, principal=principal,
                                           task_id=str((ctx or {}).get("task_id") or ""))
        if action_pattern.exclusion_of(card) is not None:
            return None
        if approval_novelty.peek(card) is not approval_novelty.Novelty.SEEN:
            return None
        from agent_friday.governance import action_gate
        fingerprint = action_pattern.from_card(card).fingerprint()
        return action_gate._use_grant(tool_name, dict(ctx or {}, grant_scope=scope_of(fingerprint)), args)
    except Exception as exc:  # noqa: BLE001
        _log.warning("standing grant could not be checked, the owner is asked: %s", exc)
        return None


def register() -> None:
    from agent_friday.services import approvals
    approvals.register_decision_hook(OFFER_KIND, on_offer_decided)
