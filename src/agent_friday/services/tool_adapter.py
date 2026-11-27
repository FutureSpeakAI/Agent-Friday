"""Mode T: a tool-skill adapter on the brain seat that is applied only on short tool turns.

A seat whose record says `lora_apply: "per_request"` is started with its adapter loaded at
strength 0 (`--lora-init-without-apply`), so on its own it is the plain base model. Each
request to it names the strength, and it is 1 only when all of these hold:

  * the owner's `tool_adapter.mode` is `tool_turns` (it ships `off`);
  * Laya's turn shape routed the turn as a confident short tool turn ("reflex", not degraded);
  * no tool has run in this request: no tool result and no tool call anywhere in the
    messages, whatever role a later message has (a loop's automated nudge is role "user");
  * the sensitivity classifier, run here on the owner's words, rates them public. Any error,
    and any vault mark already on the turn, counts as private.

Otherwise the request names strength 0, so vault and private work runs on the plain base
whatever the routing settings are. Held by tests/unit/test_tool_adapter_mode_t.py.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class Mode(str, Enum):
    OFF = "off"
    TOOL_TURNS = "tool_turns"


class Apply(str, Enum):
    """How a seat applies its adapter: on every token, or at the strength each request names."""
    ALWAYS = "always"
    PER_REQUEST = "per_request"


def mode(settings: Optional[dict]) -> Mode:
    raw = ((settings or {}).get("tool_adapter") or {}).get("mode")
    try:
        return Mode(raw)
    except ValueError:
        return Mode.OFF


def parse_apply(value) -> Optional[Apply]:
    """A record's `lora_apply`, parsed where it is written. A misspelling is an error, never a
    silent always-on seat."""
    return None if value in (None, "") else Apply(value)


def per_request(record: Optional[dict]) -> bool:
    rec = record or {}
    return bool(rec.get("lora")) and rec.get("lora_apply") == Apply.PER_REQUEST.value


def is_tool_turn(turn_shape: Optional[dict]) -> bool:
    shape = turn_shape or {}
    return shape.get("route") == "reflex" and not shape.get("degraded")


def _is_tool_round(m) -> bool:
    return not isinstance(m, dict) or m.get("role") in ("tool", "function") \
        or bool(m.get("tool_calls") or m.get("function_call"))


def choosing_a_tool(messages) -> bool:
    """The first round of a turn: the request ends with a user message and holds no tool call
    or tool result at all. A history that kept earlier tool rounds therefore gets strength 0,
    which is the safe side."""
    msgs = list(messages or [])
    return (bool(msgs) and isinstance(msgs[-1], dict) and msgs[-1].get("role") == "user"
            and not any(_is_tool_round(m) for m in msgs))


def _owner_text(messages) -> str:
    content = (list(messages or [])[-1] or {}).get("content")
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content or ()
                    if isinstance(p, dict) and p.get("type") == "text")


def clear_of_private(messages, session_ctx: Optional[dict]) -> bool:
    if (session_ctx or {}).get("vault_access"):
        return False
    try:
        from agent_friday.services.sensitivity_classifier import Tier, classify
        return classify(_owner_text(messages)) < Tier.PRIVATE
    except Exception:
        return False


def strength(settings: Optional[dict], turn_shape: Optional[dict], messages,
             session_ctx: Optional[dict]) -> int:
    if mode(settings) is not Mode.TOOL_TURNS or not is_tool_turn(turn_shape):
        return 0
    if not choosing_a_tool(messages):
        return 0
    return 1 if clear_of_private(messages, session_ctx) else 0


def request_field(record: Optional[dict], settings: Optional[dict], turn_shape: Optional[dict],
                  messages, session_ctx: Optional[dict]) -> Optional[list]:
    """The `lora` body field for one request to this seat, or None for a seat without a
    per-request adapter (nothing is added to its requests). Never raises for a per-request
    seat: a failure names strength 0."""
    if not per_request(record):
        return None
    try:
        s = strength(settings, turn_shape, messages, session_ctx)
    except Exception:
        s = 0
    return [{"id": 0, "scale": s}]
