"""The voice front's prompt is Friday's voice, not a flattened one (local
voice spec P1, §8 P6).

The brain path pins every reply to "one to three short sentences, under
about 60 spoken words" and a 300-token cap. The front instead carries the
adaptive VOICE_LENGTH_RULE cloud voice follows, the persona opening AND
closing the prompt, the honest handoff to the deeper mind, a tool line
rendered from the contract's own names, and a bounded context digest rather
than the ~26K-token standing prompt. The default spoken cap is 400 tokens.
"""
import pytest


@pytest.fixture
def front_prompt(monkeypatch):
    import agent_friday.routes.voice as rv
    from agent_friday.services import voice_context_digest
    monkeypatch.setattr(voice_context_digest, "build", lambda settings=None: "DIGEST-TEXT")
    monkeypatch.setattr(rv, "_get_voice_style_prompt",
                        lambda: "Dry wit, quick, a little mischievous.")
    contract = {"names": ["query_calendar", "ask_friday", "delegate_to_friday"]}
    return rv._build_front_system_prompt({}, contract, "Qwen3-4B-Instruct-2507"), rv


def test_no_sixty_word_pin(front_prompt):
    p, _rv = front_prompt
    assert "60 spoken words" not in p and "ONE to THREE" not in p


def test_the_adaptive_length_rule_and_the_persona_both_ends(front_prompt):
    from agent_friday.services.voice_persona import PERSONA_HEADER, VOICE_LENGTH_RULE
    p, _rv = front_prompt
    assert VOICE_LENGTH_RULE.strip()[:60] in p
    assert p.count(PERSONA_HEADER) == 2, "the persona opens and closes the prompt"


def test_handoff_tool_line_and_digest(front_prompt):
    p, rv = front_prompt
    assert rv.VOICE_FRONT_HANDOFF_RULE.strip()[:40] in p
    assert "THE TOOLS YOU HOLD IN THIS CONVERSATION: query_calendar, ask_friday, " \
           "delegate_to_friday." in p
    assert "DIGEST-TEXT" in p
    assert "Qwen3-4B-Instruct-2507" in p, "the prompt names the model serving"


def test_the_spoken_reply_cap_defaults_to_400():
    import agent_friday.routes.voice as rv
    assert rv._voice_reply_cap({}) == 400


def test_the_digest_drops_text_chat_tool_sections_and_keeps_its_budget():
    from agent_friday.services import voice_context_digest as d
    ctx = ("You are Friday.\n"
           "== AVAILABLE TOOLS ==\n" + "tool " * 400 + "\n"
           "== CORE IDENTITY ==\nThe owner's name and household.\n"
           "== PROFESSIONAL INDEX ==\nprojects...\n"
           "== NOTES ==\n" + "x" * 40000 + "\n")
    out = d.digest(ctx, budget_tokens=500)
    assert "AVAILABLE TOOLS" not in out
    assert "CORE IDENTITY" in out and "PROFESSIONAL INDEX" in out
    assert len(out) <= 500 * 4 + 16
    assert out.index("You are Friday") < out.index("CORE IDENTITY")
