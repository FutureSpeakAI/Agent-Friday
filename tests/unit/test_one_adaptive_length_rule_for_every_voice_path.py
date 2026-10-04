"""Spoken length is adaptive, and every voice path says so the same way.

Brief for back-and-forth, fuller for the news, explanations and stories. No
voice path carries an absolute cap ("one to three sentences", "short, like
texting"), and the saved voice persona holds on the local voice path too.
"""
import inspect

import agent_friday.core as core
import agent_friday.routes.voice as rv
import agent_friday.services.model_router as mr
import agent_friday.services.voice_engine as ve
import agent_friday.services.voice_persona as vp
from agent_friday.services import soul

ABSOLUTES = ("like texting", "ONE to THREE", "one to three", "1-3 sentences", "60 spoken words")


def _local_prompt(monkeypatch, style="Dry, quick and fond of a pun."):
    ctx = ("== AGENT PERSONALITY ==\n- " + core.LEGACY_LENGTH_LINE + "\n"
           "== RESPONSE PREFERENCES ==\n" + core.RESPONSE_LENGTH_HINTS["detailed"] + "\n"
           "rest of the context")
    monkeypatch.setattr(rv, "_get_friday_system_prompt", lambda **kw: ctx)
    monkeypatch.setattr(rv, "_get_voice_style_prompt", lambda: style)
    prompt, _meta = rv._build_voice_system_prompt({"orchestrator_model": "local:x"}, description="")
    return prompt


def test_the_rule_is_adaptive_and_names_no_tool():
    r = vp.VOICE_LENGTH_RULE
    for cue in ("a sentence or two", "news", "explanation", "story", "connected paragraphs"):
        assert cue in r
    assert "note_conversation_state" not in r, "the shared rule must work where that tool is absent"


def test_the_local_voice_prompt_uses_the_shared_rule_and_no_absolute_cap(monkeypatch):
    prompt = _local_prompt(monkeypatch)
    assert vp.VOICE_LENGTH_RULE in prompt
    for absolute in ABSOLUTES:
        assert absolute not in prompt, absolute
    assert core.RESPONSE_LENGTH_HINTS["detailed"] not in prompt, "the text-chat length hint stayed"


def test_the_local_voice_prompt_keeps_the_persona_from_first_line_to_last(monkeypatch):
    prompt = _local_prompt(monkeypatch)
    assert prompt.count("Dry, quick and fond of a pun.") == 2


def test_chat_voice_mode_and_the_relay_carry_no_absolute_cap():
    import agent_friday.routes.chat as rc
    chat_src = inspect.getsource(rc)
    assert "Keep it SHORT (1-3 sentences)" not in chat_src
    assert "+ VOICE_LENGTH_RULE +" in chat_src
    relay_src = inspect.getsource(ve)
    assert "Answer in one to three plain spoken sentences" not in relay_src


def test_the_base_persona_and_the_default_soul_are_adaptive():
    assert "like texting" not in mr.FRIDAY_SYSTEM_PROMPT
    assert core.ADAPTIVE_LENGTH_LINE in " ".join(mr.FRIDAY_SYSTEM_PROMPT.split())
    assert core.LEGACY_LENGTH_LINE not in inspect.getsource(soul)


def test_a_personality_file_with_the_old_default_reads_the_adaptive_line():
    prefix = core._settings_system_prefix({}, "- " + core.LEGACY_LENGTH_LINE + "\n- Be direct.")
    assert core.LEGACY_LENGTH_LINE not in prefix
    assert core.ADAPTIVE_LENGTH_LINE in prefix and "Be direct." in prefix
