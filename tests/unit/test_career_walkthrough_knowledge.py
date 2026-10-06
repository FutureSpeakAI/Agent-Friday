"""Career help reaches real chat and voice context before any setup or action."""
import re

import pytest

from agent_friday import core
from agent_friday.services import agent, career_ops, egress_gate, model_router
from agent_friday.services import voice_context_digest, workflow_templates


HEADING = "### Career workspace walkthrough"


def _career_section(text):
    assert HEADING in text, "Friday's shipped knowledge has no Career walkthrough"
    body = text.split(HEADING, 1)[1]
    return re.split(r"^#{1,3} |^---\s*$", body, maxsplit=1, flags=re.M)[0].strip()


def test_career_help_reaches_real_standard_prompt_and_cloud_gate_without_setup(monkeypatch):
    monkeypatch.setattr(career_ops, "status", lambda *a, **k: pytest.fail("help assembly ran setup"))
    block = _career_section(core._load_self_knowledge())
    prompt = model_router._get_friday_system_prompt(provider="local", vault_control=None)
    assert block in prompt
    assert block in egress_gate._gate_text(prompt, "anthropic", "system")
    assert "even before setup or outside Career" in block
    assert "explanation only" in block
    assert "do not scan, evaluate, modify files, run or schedule workflows" in block


def test_walkthrough_names_registered_tools_and_actual_workflow_steps():
    block = _career_section(core._load_self_knowledge())
    names = set(re.findall(r"`((?:career_|workflow_)[a-z_]+)`", block))
    assert {"career_status", "career_scan", "career_evaluate", "career_tailor",
            "career_update_tracker", "career_inbox", "workflow_status"} <= names
    assert names <= set(agent.CLAUDE_TOOL_HANDLERS)
    for step in workflow_templates.CAREER_SEARCH["steps"]:
        assert step["name"] in block
    assert "does not submit applications or send outreach" in block
    assert "not an enforced engine limit" in block
    assert "not model/network availability or portal validity" in block


def test_shipped_voice_help_is_compact_and_distinct_from_the_text_tool_registry():
    block = _career_section(core._load_voice_demo())
    guide = voice_context_digest._public_career_guide()
    assert block in guide
    assert len(guide) <= voice_context_digest.PUBLIC_GUIDE_MAX_CHARS
    assert len(block.split()) <= 220
    assert "ask_friday" in block
    assert not re.search(r"\bcareer_(?:status|scan|evaluate|tailor|update_tracker|inbox)\b", guide)
    for step in workflow_templates.CAREER_SEARCH["steps"]:
        assert step["name"] in " ".join(block.split())


def test_fast_voice_keeps_career_help_identity_and_its_existing_budget(monkeypatch):
    context = ("Identity preamble.\n\n== CORE IDENTITY ==\nIdentity first.\n\n"
               "== AVAILABLE TOOLS ==\ncareer_scan PRIVATE_TOOL_MARKER\n\n"
               "== PROFESSIONAL INDEX ==\n" + "Private project context. " * 10000)
    monkeypatch.setattr(model_router, "_get_friday_system_prompt", lambda **kwargs: context)
    result = voice_context_digest.build({})
    guide = voice_context_digest._public_career_guide()
    assert guide in result
    assert "CORE IDENTITY" in result and "Identity first." in result
    assert result.index("Identity first.") < result.index("CAREER WORKSPACE HELP")
    assert "PRIVATE_TOOL_MARKER" not in result and "career_scan" not in result
    assert len(result) <= voice_context_digest.DIGEST_TOKEN_BUDGET * 4


@pytest.mark.parametrize("broken", ["", "No section here", HEADING + "\n" + "x" * 10000])
def test_missing_or_oversized_public_help_falls_back_without_breaking_voice(monkeypatch, broken):
    monkeypatch.setattr(core, "_load_voice_demo", lambda: broken)
    monkeypatch.setattr(model_router, "_get_friday_system_prompt", lambda **kwargs: "Identity first.")
    assert voice_context_digest._public_career_guide() == ""
    assert voice_context_digest.build({}) == "Identity first."


def test_voice_can_still_explain_career_when_private_context_fails(monkeypatch):
    def unavailable(**kwargs):
        raise OSError("synthetic context failure")

    monkeypatch.setattr(model_router, "_get_friday_system_prompt", unavailable)
    result = voice_context_digest.build({})
    assert "context unavailable: OSError" in result
    assert "CAREER WORKSPACE HELP" in result
    assert "does not submit applications" in result


def test_public_help_loader_failure_is_harmless(monkeypatch):
    def unavailable():
        raise OSError("synthetic help failure")

    monkeypatch.setattr(core, "_load_voice_demo", unavailable)
    monkeypatch.setattr(model_router, "_get_friday_system_prompt", lambda **kwargs: "Identity first.")
    assert voice_context_digest.build({}) == "Identity first."
