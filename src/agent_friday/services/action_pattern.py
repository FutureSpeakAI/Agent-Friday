"""The same action, as one typed value (Compressor eval K5, brief section 3.1).

An approved tool card is parsed once, here, into a frozen ``ActionPattern``: the tool, where it goes,
the shape and fixed values of its arguments (never free text), who raised it and which surface asked.
Any difference in any part is a different pattern, so a new recipient, a changed fixed value or a new
origin is a new action. ``fingerprint`` names a pattern; it is what services/approval_novelty records
on every decision and what services/standing_offers counts.

``exclusion_of`` is the one place that says which actions may never be offered a standing grant
(``GrantExclusion``). tests/unit/test_standing_offers.py walks every known tool against every
exclusion.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

_FIXED_STRING = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
_SEND_NAME = re.compile(r"(?:^|_)(?:send|post|publish|reply|forward|share|invite|message|tweet|email)(?:_|$)", re.I)
_CREDENTIAL = re.compile(r"password|passphrase|secret|token|credential|api_?key|sign_?in|login|otp|2fa|vault", re.I)
_DEVICE = re.compile(r"pair|device|remote_right|grant_right", re.I)


class GrantExclusion(str, Enum):
    NOT_A_TOOL_CARD = "not_a_tool_card"
    UNKNOWN_TOOL = "unknown_tool"
    SCREEN_ONLY = "screen_only"
    SEARCH_CARD = "search_card"
    LAYA_CARD = "laya_card"
    MONEY = "money"
    NO_UNDO = "no_undo"
    CREDENTIAL = "credential"
    DEVICE_RIGHT = "device_right"
    UNNAMED_RECIPIENT = "unnamed_recipient"


@dataclass(frozen=True)
class ActionPattern:
    tool: str
    destination: Tuple[str, ...]
    arg_rule: Tuple[Tuple[str, str], ...]
    principal: str
    origin: str

    def fingerprint(self) -> str:
        body = json.dumps([self.tool, list(self.destination), [list(p) for p in self.arg_rule],
                           self.principal, self.origin], sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def _destinations(tool_input: Dict[str, Any]) -> Tuple[str, ...]:
    from agent_friday.governance.grant_record import DESTINATION_ARGS
    found = []
    for key in DESTINATION_ARGS:
        value = tool_input.get(key)
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if isinstance(item, str) and item.strip():
                found.append(f"{key}={item.strip()}")
    return tuple(sorted(set(found)))


def _arg_rule(tool_input: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    from agent_friday.governance.grant_record import DESTINATION_ARGS
    rule = []
    for key in sorted(tool_input):
        if key in DESTINATION_ARGS:
            continue
        value = tool_input[key]
        if value is None or isinstance(value, (bool, int, float)):
            rule.append((str(key), repr(value)))
        elif isinstance(value, str) and _FIXED_STRING.match(value):
            rule.append((str(key), value))
        else:
            rule.append((str(key), type(value).__name__))
    return tuple(rule)


#: Who raises the card for a tool call that waits for the owner's yes (agent._confirm_card).
GATE_ORIGIN = "confirmation_gate"


def from_call(tool: str, tool_input: Any, *, principal: str = "owner",
              origin: str = GATE_ORIGIN) -> ActionPattern:
    """The pattern of a tool call about to be decided: what its card would parse to."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    return ActionPattern(tool=str(tool)[:80], destination=_destinations(tool_input),
                         arg_rule=_arg_rule(tool_input), principal=str(principal or "owner")[:60],
                         origin=str(origin or "")[:60])


def card_of_call(tool: str, tool_input: Any, *, principal: str = "owner",
                 origin: str = GATE_ORIGIN, task_id: str = "") -> Dict[str, Any]:
    """The card a call would be raised as, for the checks that read a card (exclusion, novelty)."""
    return {"kind": "tool_confirm", "principal": principal, "requested_by": origin,
            "payload": {"tool": tool, "input": tool_input if isinstance(tool_input, dict) else {},
                        "task_id": task_id}}


def from_card(card: Dict[str, Any]) -> Optional[ActionPattern]:
    """The pattern of a tool card, or None for any other kind of card."""
    payload = card.get("payload") if isinstance(card.get("payload"), dict) else {}
    tool = payload.get("tool")
    if card.get("kind") != "tool_confirm" or not isinstance(tool, str) or not tool:
        return None
    return from_call(tool, payload.get("input"), principal=str(card.get("principal") or "owner"),
                     origin=str(payload.get("origin") or card.get("requested_by") or ""))


def known_tools() -> frozenset:
    from agent_friday.governance import action_gate
    return frozenset(action_gate.OUTWARD_TOOLS | action_gate.INTERNAL_TOOLS
                     | action_gate.CONNECTED_SERVICE_TOOLS)


def _sends(pattern: ActionPattern, tool_input: Dict[str, Any]) -> bool:
    return bool(_SEND_NAME.search(pattern.tool)) or tool_input.get("send") is True


def tool_exclusion(tool: str) -> Optional[GrantExclusion]:
    """The exclusions that depend on the tool alone. Grant creation checks this too, so a request
    made by hand for such a tool is refused."""
    from agent_friday.governance import action_gate
    if action_gate.narrow_grant_only(tool):
        return GrantExclusion.NO_UNDO
    if _CREDENTIAL.search(tool):
        return GrantExclusion.CREDENTIAL
    if _DEVICE.search(tool):
        return GrantExclusion.DEVICE_RIGHT
    if tool not in known_tools():
        return GrantExclusion.UNKNOWN_TOOL
    return None


def exclusion_of(card: Dict[str, Any]) -> Optional[GrantExclusion]:
    """Why this card's action may never have a standing grant, or None when it may be offered one.
    Fails closed: anything not recognised is excluded."""
    from agent_friday.services import approvals
    pattern = from_card(card)
    if pattern is None:
        return GrantExclusion.NOT_A_TOOL_CARD
    payload = card.get("payload") or {}
    tool_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    if card.get("kind") in approvals.SCREEN_ONLY_KINDS:
        return GrantExclusion.SCREEN_ONLY
    if approvals._is_public_query_card(card):
        return GrantExclusion.SEARCH_CARD
    if payload.get("origin") == "laya_safe_write":
        return GrantExclusion.LAYA_CARD
    if card.get("policy_class") in ("spend", "irreversible"):
        return GrantExclusion.MONEY if card.get("policy_class") == "spend" else GrantExclusion.NO_UNDO
    by_tool = tool_exclusion(pattern.tool)
    if by_tool is not None:
        return by_tool
    if any(_CREDENTIAL.search(str(k)) for k in tool_input):
        return GrantExclusion.CREDENTIAL
    if _sends(pattern, tool_input) and not pattern.destination:
        return GrantExclusion.UNNAMED_RECIPIENT
    return None
