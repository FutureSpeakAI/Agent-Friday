"""A cloud chat turn masks private values in block content too, so an address
the user typed reaches the model as a [PII:...] tag (usable in a tool call)
rather than raw, where the egress gate would withhold the whole paragraph."""
from __future__ import annotations

import agent_friday.routes.chat as chat_mod

ADDR = "someone" + "@example.com"


def test_string_and_block_content_are_both_masked():
    messages = [
        {"role": "user", "content": "write to " + ADDR},
        {"role": "user", "content": [
            {"type": "text", "text": "context"},
            {"type": "text", "text": "Email " + ADDR + " with the subject Hello"},
            {"type": "image", "source": {"data": "x"}},
        ]},
    ]
    lookup = {}
    chat_mod._scrub_messages_pii(messages, lookup)
    assert ADDR not in messages[0]["content"]
    assert ADDR not in messages[1]["content"][1]["text"]
    assert "[PII:email:" in messages[1]["content"][1]["text"]
    assert messages[1]["content"][2] == {"type": "image", "source": {"data": "x"}}
    assert ADDR in lookup.values()
