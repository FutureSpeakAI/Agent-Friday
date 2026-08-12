"""FR-4 — honest-degradation system prompt rule (toolcall-integrity-v5).

The rule lives directly in FRIDAY_SYSTEM_PROMPT (services/model_router.py),
which is the base layer of every provider's system prompt (see
_build_context_prompt's `add(FRIDAY_SYSTEM_PROMPT, _T1)` and
_get_friday_system_prompt's fallback) — so it applies to every seat
(cloud/Claude, local/Ollama, OpenAI-compatible) uniformly, not just one
provider's prompt path. It is FR-2/FR-3's backstop: even before a fabricated
reply reaches the post-generation validator, the model is told plainly not
to narrate unreceived tool results or write tool names as pseudo-calls.
"""
from __future__ import annotations

from agent_friday.services.model_router import FRIDAY_SYSTEM_PROMPT


def test_honest_degradation_section_present():
    assert "HONEST DEGRADATION" in FRIDAY_SYSTEM_PROMPT


def test_instructs_saying_so_and_stopping_on_tool_failure():
    section = FRIDAY_SYSTEM_PROMPT.split("HONEST DEGRADATION", 1)[1]
    assert "SAY SO AND STOP" in section


def test_forbids_writing_tool_names_as_pseudo_calls():
    section = FRIDAY_SYSTEM_PROMPT.split("HONEST DEGRADATION", 1)[1][:1200]
    assert "fabrication" in section.lower()
    assert "query_calendar" in section  # concrete example, not just abstract rule


def test_forbids_inventing_results():
    section = FRIDAY_SYSTEM_PROMPT.split("HONEST DEGRADATION", 1)[1][:1200]
    assert "never invent" in section.lower()


def test_base_prompt_is_included_in_context_prompt_layer_zero():
    # _build_context_prompt's very first `add()` call is
    # add(FRIDAY_SYSTEM_PROMPT, _T1) — confirm the substring still appears
    # after passing through the context-prompt builder for a bare message,
    # so every _prep_for(provider) call in routes/chat.py inherits it
    # regardless of which provider (local/openai/cloud) is dispatched to.
    from agent_friday.services.model_router import _build_context_prompt
    prompt, _sources = _build_context_prompt("hello", workspace="chat")
    assert "HONEST DEGRADATION" in prompt
