"""Malformed payloads cannot carry sensitive text past the egress gate (north star NS-34.6-8).

The gate read only the shapes it expected: a message dict whose content is a
string or a list of typed parts. Anything else passed through untouched: a bare
string where a message belongs, a tuple of messages, a content dict, a bare
string part, a part of an unknown type with a "text" field, a list nested in the
parts. Each now goes through the same tiers, and a shape that could hold text the
gate cannot read is withheld (fail closed). Harmless text keeps its place.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services.egress_gate import _gate_messages, seal_outbound

SECRET = "my custody hearing and divorce settlement terms"


def _leaks(obj) -> bool:
    return "custody" in json.dumps(obj, default=str)


@pytest.mark.parametrize("msg", [
    SECRET,                                                                  # a bare string message
    ("user", SECRET),                                                        # a tuple message
    [SECRET],                                                                # a list message
    {"role": "user", "content": {"type": "text", "text": SECRET}},          # a content dict
    {"role": "user", "content": {"parts": [SECRET]}},                       # a content dict with no text field
    {"role": "user", "content": [SECRET]},                                  # a bare string part
    {"role": "user", "content": [{"type": "input_text", "text": SECRET}]},  # an unknown part type with text
    {"role": "user", "content": [[SECRET]]},                                # a list inside the parts
], ids=["str", "tuple", "list", "content-dict", "content-dict-no-text", "str-part", "unknown-part", "nested-list"])
def test_a_malformed_message_never_carries_sensitive_text_to_a_cloud_model(msg):
    out = _gate_messages([msg], "anthropic")
    assert not _leaks(out), out


@pytest.mark.parametrize("messages", [(SECRET,), SECRET], ids=["tuple", "str"])
def test_a_malformed_messages_field_is_gated_not_skipped(messages):
    out = seal_outbound({"model": "claude-x", "messages": messages}, "anthropic")
    assert not _leaks(out["messages"]), out["messages"]


def test_harmless_text_in_an_odd_shape_keeps_its_place():
    out = _gate_messages(["how do I sort a list?", {"role": "user", "content": ["hello", {"type": "input_text", "text": "hi"}]}],
                         "anthropic")
    assert out[0] == "how do I sort a list?"
    assert out[1]["content"] == ["hello", {"type": "input_text", "text": "hi"}]


def test_a_local_model_still_gets_everything():
    assert _gate_messages is not None
    out = seal_outbound({"messages": [SECRET]}, "ollama")
    assert out["messages"] == [SECRET]