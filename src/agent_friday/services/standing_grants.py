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
import uuid
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
MAX_SHOWN_DESTINATIONS = 3
MAX_DESTINATION_CHARS = 60
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


def _shown(destinations) -> str:
    """The recipients as the card shows them: a few, each cut short, one line each. They come from tool input, which
    a model or an email can write, so they are bounded and never carry a line break."""
    names = [" ".join(str(d).split())[:MAX_DESTINATION_CHARS] for d in list(destinations)[:MAX_SHOWN_DESTINATIONS]]
    more = len(destinations) - len(names)
    return ", ".join(names) + (" and %d more" % more if more > 0 else "") if names else "no named recipient"


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
        # One card per OFFER, not per pattern: create_approval returns the newest card for a subject whatever its
        # status, so after a decline, an expiry or a revoke the pattern's subject would hand back the old decided
        # card and no new offer could ever be raised.
        card = approvals.create_approval(
            kind=OFFER_KIND, subject_type="pattern", subject_id="%s:%s" % (fingerprint, uuid.uuid4().hex[:8]),
            title="Stop asking about %s?" % pattern.tool,
            description=("Friday can stop asking for this same action, which you have approved %d times, "
                         "for %d days or %d uses, whichever is first. You can take this back in Settings. "
                         "Other details you typed are not part of it, and Friday still asks if an action "
                         "looks like spending money or cannot be undone. Action: %s. Recipients: %s."
                         % (standing_offers.OFFER_AFTER_APPROVALS, GRANT_SECONDS // 86400, GRANT_USES,
                            pattern.tool[:80], _shown(pattern.destination))),
            action_description="Offer a standing grant for %s" % pattern.tool,
            force_gate=True, requested_by=SHELL,
            payload={"pattern": fingerprint, "snapshot": _snapshot(decided_card)})
        if card.get("status") != "pending":
            return None
        standing_offers.record_offer_raised(fingerprint, tool=pattern.tool)
        _ledger("standing_offer_shown", pattern=fingerprint, tool=pattern.tool)
        return card
    except Exception as exc:
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
        note=("standing grant from card %s; to: %s"
              % (card.get("approval_id"), ", ".join(pattern.destination) or "no named recipient"))[:200],
        limits={"subject_kind": "pattern", "principal": pattern.principal})
    _ledger("standing_offer_accepted", pattern=fingerprint, grant=grant["grant_id"],
            by=str(card.get("decided_by") or ""))


def listing() -> Dict[str, Any]:
    """Settings' view: every live standing grant (what, to whom, who pressed and on which device,
    when, uses so far, when it ends) and the offers declined or stopped. Words only, no card text."""
    from agent_friday.governance import action_gate
    from agent_friday.services import approvals
    rows = []
    for grant in action_gate.list_grants():
        if not str(grant.get("scope", "")).startswith(PATTERN_SCOPE):
            continue
        note = str(grant.get("note") or "")
        card_id = note.split(" from card ", 1)[-1].split(";", 1)[0].strip()
        card = approvals.get_approval(card_id) or {}
        rows.append({
            "grant_id": grant["grant_id"], "tool": (grant.get("tools") or [""])[0],
            "to": note.split("; to: ", 1)[-1] if "; to: " in note else "",
            "pressed_by": grant.get("created_by") or "", "device": card.get("decided_device") or "",
            "made_at": grant.get("created_at"), "ends_at": grant.get("expires_at"),
            "uses_used": int(grant.get("used_count") or 0), "uses_max": grant.get("max_uses")})
    return {"grants": rows, **standing_offers.declined_and_stopped()}


def cover(tool_name: str, args: Any, ctx: Dict[str, Any], why: str) -> Optional[dict]:
    """A live standing grant spent on this call, or None when the owner is asked as usual.

    A grant is earned on the confirmation cards of a chat session (the pattern's origin), so it covers only a call
    from an interactive chat session: a scheduled, background or session-less call is a different origin and is
    never covered. The call's own money and irreversible class is judged here, on the call, because free text is
    outside the pattern and the card that earned the grant may have said something harmless."""
    try:
        from agent_friday.governance import action_gate
        ctx = ctx or {}
        if not action_gate._interactive(ctx):
            return None
        if any(word in str(why or "").lower() for word in _ESCALATION_WORDS):
            return None
        from agent_friday.services import approvals
        principal = str(ctx.get("principal") or "owner")
        card = action_pattern.card_of_call(tool_name, args, principal=principal,
                                           task_id=str(ctx.get("task_id") or ""))
        card["policy_class"] = approvals.classify("%s %r" % (tool_name, args))["policy_class"]
        if action_pattern.exclusion_of(card) is not None:
            return None
        if approval_novelty.peek(card) is not approval_novelty.Novelty.SEEN:
            return None
        fingerprint = action_pattern.from_card(card).fingerprint()
        return action_gate._use_grant(tool_name, {"grant_scope": scope_of(fingerprint)}, args)
    except Exception as exc:
        _log.warning("standing grant could not be checked, the owner is asked: %s", exc)
        return None


def register() -> None:
    from agent_friday.services import approvals
    approvals.register_decision_hook(OFFER_KIND, on_offer_decided)
