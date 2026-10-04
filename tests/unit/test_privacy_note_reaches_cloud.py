"""The note that tells a cloud model how to use [PII:...] tags must reach it.

It names identifier types ("ssn", "cc"), which the egress gate classifies as
sensitive; unregistered, the gate withheld the note and sent its "no local
model" placeholder instead, and the model refused every action on a masked
value."""
from __future__ import annotations

import agent_friday.routes.chat as chat_mod
from agent_friday.services import egress_gate as eg


def test_the_placeholder_note_passes_the_gate_for_a_cloud_provider(tmp_path):
    system = "You are Friday.\n\n" + chat_mod.PRIVACY_PLACEHOLDERS_NOTE
    out = eg._gate_text_span(system, "openrouter", "system", log_path=tmp_path / "egress.jsonl")
    assert chat_mod.PRIVACY_PLACEHOLDERS_NOTE in out
    assert "EGRESS-GATE" not in out


def test_the_placeholder_note_passes_the_gate_in_the_turn_context(tmp_path):
    """On /api/chat the note rides in the per-turn context block of the newest
    user turn, which the gate judges as message text, not as the system
    prompt; it must survive there too."""
    block = chat_mod.turn_context_block("Now: 12:00" + "\n\n" + chat_mod.PRIVACY_PLACEHOLDERS_NOTE)
    turn = [{"role": "user", "content": [{"type": "text", "text": block},
                                         {"type": "text", "text": "email [PII:email:1]"}]}]
    out = eg._gate_messages(turn, "openrouter", log_path=tmp_path / "egress.jsonl")
    text = out[0]["content"][0]["text"]
    assert chat_mod.PRIVACY_PLACEHOLDERS_NOTE in text
    assert "EGRESS-GATE" not in text


def test_the_note_says_tags_work_in_tool_arguments():
    assert "tool arguments" in chat_mod.PRIVACY_PLACEHOLDERS_NOTE
