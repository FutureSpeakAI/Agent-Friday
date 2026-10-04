"""How hard the local brain thinks on a turn (speed spec section 2.3).

Bonsai 2 accepts `reasoning_effort` on the request: `xhigh` (its own
default), `medium` (injects nothing) and `none`; `high` makes its template
raise, so it is never sent. Measured on one turn shape: xhigh 159.8 s, medium
77.1 s, thinking off 49.5 s. The right effort depends on the turn, which is
what Laya 2's turn-shape head (services/reflex_turn) tells us before the brain
starts.

Two settings decide, and the pinned one wins:

  local_reasoning_effort         "auto" lets the policy choose; any other
                                 value ("medium", "xhigh", "none", "low") is
                                 sent as it is on every turn; "default" sends
                                 nothing and leaves the model to its own.
  local_reasoning_effort_policy  {"reflex_thinking_off": bool,
                                  "deep_xhigh": bool}

`reflex_thinking_off` ships False: thinking off for reflex shapes is gated
on the strict tool-call harness holding within 2 points on those shapes, and
until that receipt exists every turn without a deep verdict keeps `medium`,
which is exactly what Friday sent before this module existed.
"""
from __future__ import annotations

from typing import Optional

DEFAULT_EFFORT = "medium"
DEFAULT_POLICY = {"reflex_thinking_off": False, "deep_xhigh": True}
#: Spellings a person may write for "no thinking".
_OFF = ("none", "off", "disabled")


def policy_from(settings: Optional[dict]) -> dict:
    policy = dict(DEFAULT_POLICY)
    raw = (settings or {}).get("local_reasoning_effort_policy")
    if isinstance(raw, dict):
        for key in DEFAULT_POLICY:
            if key in raw:
                policy[key] = bool(raw[key])
    return policy


def reasoning_effort_for_turn(shape_verdict: Optional[dict], settings: Optional[dict]) -> Optional[str]:
    """The `reasoning_effort` to send, or None to send nothing. Pure.

    A pinned `local_reasoning_effort` that is not "auto" wins outright. With
    "auto": a deep verdict thinks at `xhigh` when the policy allows it, a
    reflex-routed verdict thinks not at all when `reflex_thinking_off` is on,
    and everything else (no verdict, a degraded one, an ordinary turn) keeps
    `medium`.
    """
    pinned = str((settings or {}).get("local_reasoning_effort") or "auto").strip().lower()
    if pinned in _OFF:
        return "none"
    if pinned == "default":
        return None
    if pinned == "high":
        # Bonsai 2's template raises on `high`; the nearest effort it serves.
        return "xhigh"
    if pinned not in ("auto", ""):
        return pinned
    policy = policy_from(settings)
    v = shape_verdict or {}
    if v.get("degraded"):
        return DEFAULT_EFFORT
    shape = str(v.get("shape") or "")
    if shape.startswith("deep_") and policy["deep_xhigh"]:
        return "xhigh"
    if v.get("route") == "reflex" and policy["reflex_thinking_off"]:
        return "none"
    return DEFAULT_EFFORT
