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


# ── a phone or channel turn: the answer leaves this PC whichever model writes it ─────────────────────────────────────────────

def _local_prompt(*, with_control: bool):
    from agent_friday.services.model_router import _get_vault_control, memory_head_blocks
    vc = _get_vault_control() if with_control else None
    return memory_head_blocks("", None, provider="local", vault_control=vc, model="")


def test_a_local_model_on_this_pc_sees_the_line_in_the_prompt(monkeypatch):
    """The control for the cases below: the same local reader on a desktop turn gets the line."""
    _posture(monkeypatch, local_only=True)
    for with_control in (True, False):
        assert LINE in _local_prompt(with_control=with_control)


@pytest.mark.parametrize("origin", ["phone", "channel"])
def test_a_remote_turn_on_a_local_model_never_gets_a_withheld_line_in_its_prompt(monkeypatch, origin):
    """The tool withholds the lines from a phone or channel turn; the prompt built for that turn must agree. Before: the
    prompt builder never heard the origin, and the vault gate passed the raw text to any local provider."""
    _posture(monkeypatch, local_only=True)
    assert cm.user_lines_visible("local", "", origin) is False    # what the tool asks (a phone turn is refused the tool outright)
    tok = agent._CURRENT_ORIGIN.set(origin)
    try:
        for with_control in (True, False):
            assert LINE not in _local_prompt(with_control=with_control), "a withheld line reached a %s prompt" % origin
    finally:
        agent._CURRENT_ORIGIN.reset(tok)


@pytest.mark.parametrize("origin", ["phone", "channel"])
def test_the_turn_origin_is_known_when_the_prompt_is_built(monkeypatch, origin):
    """_generate_agent builds each leg's prompt through `system_builder`; the origin of the turn must already be set then,
    not only inside each tool call."""
    from agent_friday.services.model_router import _get_vault_control, memory_head_blocks
    _posture(monkeypatch, local_only=True)
    seen = {}

    def one_leg(messages, **kw):
        seen["origin"] = agent._CURRENT_ORIGIN.get()
        seen["prompt"] = kw["system_builder"]("local")
        return "ok", []
    monkeypatch.setattr(agent, "_generate_agent_untraced", one_leg)
    agent._generate_agent([{"role": "user", "content": "hello"}], session_ctx={"origin": origin}, workspace="chat",
                          system_builder=lambda p: memory_head_blocks("", None, provider=p,
                                                                      vault_control=_get_vault_control(), model=""))
    assert seen["origin"] == origin
    assert LINE not in seen["prompt"], "the %s turn's prompt carried a withheld line" % origin
    assert agent._CURRENT_ORIGIN.get() == "", "the turn's origin outlived the turn"
