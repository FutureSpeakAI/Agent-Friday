"""Core memory: ONE rule decides whether a cloud model sees the lines about the owner. SYNTHETIC ENTRIES ONLY.

Before: the prompt builder followed the owner's vault posture (ungated => the memory card was in the prompt) while the tool
hard-coded "any model not known to be local gets nothing", so the model read a line in its prompt, `update_core_memory show` said
the list was empty, and `replace` said "no entry contains that text". Both halves now ask core_memory.user_lines_visible().

Each case runs with a CLOUD model and a synthetic user line:
  * posture ungated  : the prompt holds the line, `show` lists it, `replace` lands (also when the line carries a correction tag);
  * posture gated    : the prompt does not hold the line, `show` is withheld, `replace`/`remove` return the withheld error,
                       never "no entry contains that text", and the file is untouched.
"""
from __future__ import annotations

import pytest

import agent_friday.core as core
import agent_friday.services.agent as agent
from agent_friday.services import approvals, taint
from agent_friday.services import core_memory as cm

LINE = "Keeps bees behind the Quillfeather Harbor library."
NEW = "Keeps goats behind the Quillfeather Harbor library."
WITHHELD = "withheld from this model; edit it on the desktop or switch to a local model."
SID = "2026-11-22"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    cm.add("user", LINE, actor="owner")
    yield
    taint.reset()


def _posture(monkeypatch, *, local_only: bool):
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {"vault_local_only": local_only}})


def _cloud_loop():
    return [agent._CURRENT_PROVIDER.set("anthropic"), agent._CURRENT_MODEL.set("claude-test-cloud"),
            agent._CURRENT_ORIGIN.set("")]


def _reset(tokens):
    for var, tok in zip((agent._CURRENT_PROVIDER, agent._CURRENT_MODEL, agent._CURRENT_ORIGIN), tokens):
        var.reset(tok)


def _prompt(*, with_control: bool):
    from agent_friday.services.model_router import _get_vault_control, memory_head_blocks
    vc = _get_vault_control() if with_control else None
    return memory_head_blocks("", None, provider="anthropic", vault_control=vc, model="claude-test-cloud")


def _tool(action, **inp):
    ctx = agent.prepare_confirmation_ctx(SID, "Change what you know about my bees: " + NEW,
                                         {"authenticated": True, "owner_surface": True})
    tokens = _cloud_loop()
    try:
        return agent._execute_tool("update_core_memory", dict(action=action, target="user", **inp), session_ctx=ctx)
    finally:
        _reset(tokens)


def test_ungated_posture_prompt_show_and_replace_all_see_the_line(monkeypatch):
    _posture(monkeypatch, local_only=False)
    assert LINE in _prompt(with_control=False)
    shown = _tool("show")
    assert LINE in shown and "withheld" not in shown
    out = _tool("replace", old="bees", text=NEW)
    assert "no entry contains" not in out, out
    assert cm.entries("user") == [NEW]


def test_ungated_replace_lands_on_a_line_that_carries_a_correction_tag(monkeypatch):
    """The prompt shows a corrected line with a trailing [correction:DATE]; quoting what the model read must still match."""
    _posture(monkeypatch, local_only=False)
    shown_line = LINE + " [correction:2026-11-01]"          # what the card renders for a corrected line; the file holds LINE
    out = _tool("replace", old=shown_line, text=NEW)
    assert "no entry contains" not in out, out
    assert cm.entries("user") == [NEW]


def test_gated_posture_prompt_hides_the_line_and_the_tool_says_withheld(monkeypatch):
    _posture(monkeypatch, local_only=True)
    for with_control in (True, False):
        assert LINE not in _prompt(with_control=with_control), "the line reached a cloud prompt"
    shown = _tool("show")
    assert LINE not in shown and "withheld" in shown
    for action, extra in (("replace", {"old": "bees", "text": NEW}), ("remove", {"old": "bees"})):
        out = _tool(action, **extra)
        assert WITHHELD in out, out
        assert "no entry contains" not in out
    assert cm.entries("user") == [LINE]


def test_the_rule_is_one_function_both_halves_call():
    import inspect
    from agent_friday.services import core_memory_tools, model_router
    assert callable(cm.user_lines_visible)
    assert "user_lines_visible" in inspect.getsource(core_memory_tools)
    assert "user_lines_visible" in inspect.getsource(model_router.memory_head_blocks)
