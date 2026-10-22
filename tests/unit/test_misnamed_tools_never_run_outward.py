"""BD-11a / NS-32.21-1: a misnamed tool call is never resolved to a tool that acts outside.

Before: a call whose name was not registered ran the one registered tool that matched it
(`create-calendar-event` ran create_calendar_event, `zz.get.sender` ran zz_sender), whatever
that tool did. A model's slip of the name could send, buy or invite under a name nobody chose.
Now the guess is only taken when the matched tool is internal for these arguments
(governance/action_gate.classify); otherwise nothing runs and the answer names the tool, so
the model can call it by its exact name, and that call passes every gate as itself.
Synthetic tools only, plus one real outward tool name.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import agent


@pytest.fixture
def probes(monkeypatch):
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "zz_sender", lambda inp: ran.append("sender") or "sent")
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "zz_reader", lambda inp: ran.append("reader") or "read")
    real = ag.classify

    def classify(name, args, ctx=None):
        if name == "zz_sender":
            return ag.OUTWARD, "sends to someone"
        if name == "zz_reader":
            return ag.INTERNAL, "reads this PC"
        return real(name, args, ctx)
    monkeypatch.setattr(ag, "classify", classify)
    return ran


def test_a_misnamed_outward_tool_does_not_run(probes, monkeypatch):
    # The checkpoint would let it through (as an owner's grant would): only the name check stops it.
    monkeypatch.setattr(ag, "_decide", lambda tool, klass, why, *a, **k: ag.Verdict("allow", klass, "allowed in test"))
    out = agent._execute_tool("zz.get.sender", {}, session_ctx={"authenticated": True, "is_background_task": True})
    assert probes == [], "an outward tool ran under a guessed name"
    assert out.startswith("TOOL CALL FAILED") and "'zz_sender'" in out and "exact name" in out
    assert agent._tool_call_status(out) == "error"


def test_a_connector_tool_never_runs_under_a_guessed_name(probes):
    assert agent._resolution_may_run("mcp_example_get_balance", {}) is False


def test_the_arguments_judged_are_the_ones_that_would_run(probes, monkeypatch):
    """A job set to send its mail turns a draft into a send before any gate: the guess is judged
    on that, not on the draft the model asked for."""
    seen = []
    monkeypatch.setattr(agent, "_job_send_args", lambda name, args, ctx: dict(args, send=True))
    real = ag.classify
    monkeypatch.setattr(ag, "classify", lambda name, args, ctx=None: seen.append(dict(args)) or real(name, args, ctx))
    agent._resolution_may_run("draft_email", {"to": "a@example.test"}, {"is_background_task": True})
    assert seen and seen[-1].get("send") is True


def test_a_misnamed_internal_tool_may_still_resolve(probes):
    assert agent._resolution_may_run("zz_reader", {}) is True
    assert agent._resolution_may_run("zz_sender", {}) is False


def _a_real_outward_tool_and_a_guess_at_its_name():
    """A registered tool that acts outside, and a name that resolves to it only by guessing
    (an invented verb in front, dots for underscores). Found from the live registry, so the test
    does not depend on which other tools happen to share a name."""
    for name in sorted(agent.CLAUDE_TOOL_HANDLERS):
        if name.startswith("mcp_") or "_" not in name:
            continue
        try:
            cls, _why = ag.classify(name, {}, None)
        except Exception:
            continue
        if cls == ag.INTERNAL:
            continue
        guess = "get." + name.replace("_", ".")
        if (agent._resolve_tool_name(guess)[0] == name
                and not agent._same_name_spelled_differently(guess, name)):
            return name, guess
    return None, None


def test_a_real_outward_tool_is_not_run_under_a_guessed_name(monkeypatch):
    name, guess = _a_real_outward_tool_and_a_guess_at_its_name()
    assert name, "no outward tool can be reached by a guessed name in this registry"
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, name, lambda inp: ran.append(inp) or "done")
    out = agent._execute_tool(guess, {}, session_ctx={"authenticated": True, "is_background_task": True})
    assert ran == [], "%s ran under the guessed name %s" % (name, guess)
    assert out.startswith("TOOL CALL FAILED") and ("'%s'" % name) in out


def test_the_same_name_spelled_with_other_separators_is_not_a_guess():
    """`site-action` is site_action itself (only separators and case differ): it runs as itself,
    through every gate, as the Sites and Crew boundaries expect."""
    assert agent._same_name_spelled_differently("site-action", "site_action")
    assert agent._same_name_spelled_differently("Create.Calendar.Event", "create_calendar_event")
    assert not agent._same_name_spelled_differently("calendar.create.event", "create_calendar_event")
    assert not agent._same_name_spelled_differently("zz.get.sender", "zz_sender")


def test_a_classification_that_cannot_run_is_not_a_yes(monkeypatch):
    monkeypatch.setattr(ag, "classify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert agent._resolution_may_run("search_web", {"query": "x"}) is False
