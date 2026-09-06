"""Gauntlet finding F12: egress_gate.py's _gate_messages() gates the
Anthropic tool_use block shape (fixed historically, see _gate_tool_use's
docstring: "a local seat that falls back to cloud carries its own tool
calls with it") but has zero handling for the OpenAI-compatible wire
shape's equivalent field: an assistant message's `tool_calls[].function.
arguments` (a JSON-encoded string carrying real user data -- what was
written to the vault, a file path, a search query).

A dedicated verification pass (docs/history/audits/gauntlet-2026-09-03/findings.jsonl
F12) confirmed this gap is NOT reachable today from any real entry point --
every current retry-with-provider-switch path rebuilds a fresh, plain
{role, content} message list rather than reusing a raw `tool_calls`-bearing
object. So this is hardening, not a live-leak fix: closing it now, while it
costs nothing to reach, is cheaper than waiting for the one refactor (a
future _call_ollama/_call_openai "optimization" that reuses the caller's
messages list directly) that would make it live.

This probe must be RED before the fix (tool_calls sail through _gate_messages
byte-for-byte unchanged, sensitive argument value included) and GREEN after
(mirrors _gate_tool_use's existing treatment: gate every string value,
preserve keys/structure, re-serialize back to a JSON string since OpenAI's
wire format requires `arguments` to remain a string).
"""
from __future__ import annotations

import json

from agent_friday.services import egress_gate as eg


_SENSITIVE = "my social security number is 123-45-6789"  # pragma: allowlist secret


def _tool_calls_message(arguments_obj):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps(arguments_obj)},
        }],
    }


class TestEgressGateOpenAIToolCallsShape:
    def test_sensitive_argument_value_is_gated_not_passed_through(self):
        msg = _tool_calls_message({"path": "/vault/notes.txt",
                                    "content": _SENSITIVE})
        gated = eg._gate_messages([msg], "openai")
        out_args_raw = gated[0]["tool_calls"][0]["function"]["arguments"]
        # It must still be valid JSON (OpenAI's wire format requires this;
        # a tool call whose arguments aren't parseable JSON breaks replay).
        out_args = json.loads(out_args_raw)
        assert _SENSITIVE not in json.dumps(out_args), (
            "a sensitive tool-call argument value survived _gate_messages() "
            "unchanged -- egress_gate.py has no handling for the "
            "OpenAI-compatible tool_calls wire shape, unlike the already-"
            "gated Anthropic tool_use equivalent"
        )

    def test_argument_keys_and_call_id_are_preserved(self):
        """No-op-shaped sanity check: gating must not corrupt the structure
        a tool-call replay depends on (id pairing, argument keys)."""
        msg = _tool_calls_message({"path": "/vault/notes.txt",
                                    "content": _SENSITIVE})
        gated = eg._gate_messages([msg], "openai")
        tc = gated[0]["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["function"]["name"] == "write_file"
        out_args = json.loads(tc["function"]["arguments"])
        assert set(out_args.keys()) == {"path", "content"}
        assert out_args["path"] == "/vault/notes.txt", (
            "a non-sensitive argument value should pass through unchanged"
        )

    def test_non_json_arguments_string_falls_back_to_text_gating(self):
        """Defensive case: a malformed/non-JSON arguments string must not
        crash the gate -- it should be treated as opaque text, same fallback
        _gate_tool_result already uses for non-JSON tool output."""
        msg = {
            "role": "assistant", "content": "",
            "tool_calls": [{
                "id": "call_2", "type": "function",
                "function": {"name": "search", "arguments": _SENSITIVE},
            }],
        }
        gated = eg._gate_messages([msg], "openai")
        out_args = gated[0]["tool_calls"][0]["function"]["arguments"]
        assert _SENSITIVE not in out_args
