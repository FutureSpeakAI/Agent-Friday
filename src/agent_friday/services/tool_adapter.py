"""Mode T: a tool-skill adapter on the brain seat that is applied only on short tool turns.

A seat whose record says `lora_apply: "per_request"` is started with its adapter loaded at
strength 0 (`--lora-init-without-apply`), so on its own it is the plain base model. Each
request then names the strength: 1 only when the owner's `tool_adapter.mode` is
`tool_turns`, Laya's turn shape routed the turn as a confident short tool turn ("reflex"),
and the router did not mark the turn as vault work (its vault verdict includes the
sensitivity check's private and sensitive tiers).
Every other turn is sent strength 0 explicitly, so vault and private work always runs on
the plain base. The setting ships `off`.

Held by tests/unit/test_tool_adapter_mode_t.py (every small case of the rule, the request
field, and the seat's launch flag and readiness check).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class Mode(str, Enum):
    OFF = "off"
    TOOL_TURNS = "tool_turns"


PER_REQUEST = "per_request"


def mode(settings: Optional[dict]) -> Mode:
    raw = ((settings or {}).get("tool_adapter") or {}).get("mode")
    try:
        return Mode(raw)
    except ValueError:
        return Mode.OFF


def per_request(record: Optional[dict]) -> bool:
    """Does this seat record load its adapter unapplied and take a strength per request?"""
    rec = record or {}
    return bool(rec.get("lora")) and rec.get("lora_apply") == PER_REQUEST


def is_tool_turn(turn_shape: Optional[dict]) -> bool:
    """Laya's turn shape routed this turn as a confident short tool turn. `reflex_turn.decide`
    already demands the confidence and margin thresholds and refuses state-changing shapes, so
    a degraded or unsure shape is never a tool turn here."""
    shape = turn_shape or {}
    return shape.get("route") == "reflex" and not shape.get("degraded")


def touches_private(session_ctx: Optional[dict]) -> bool:
    ctx = session_ctx or {}
    return bool(ctx.get("vault_access"))


def strength(settings: Optional[dict], turn_shape: Optional[dict],
             session_ctx: Optional[dict]) -> int:
    if mode(settings) is not Mode.TOOL_TURNS:
        return 0
    if touches_private(session_ctx):
        return 0
    return 1 if is_tool_turn(turn_shape) else 0


def request_field(record: Optional[dict], settings: Optional[dict], turn_shape: Optional[dict],
                  session_ctx: Optional[dict]) -> Optional[list]:
    """The `lora` body field for one request to this seat, or None for a seat without a
    per-request adapter (nothing is added to its requests)."""
    if not per_request(record):
        return None
    return [{"id": 0, "scale": strength(settings, turn_shape, session_ctx)}]
