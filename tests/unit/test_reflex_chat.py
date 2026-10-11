"""P7-5: chat turns answered without a model round.

Approval replies (default on) and "open the X" (opt-in) are settled by rules
and the item resolver, and run through the same governed tool path a model's
call takes. Surfaces other than the owner's own typing in the app never get a
reflex, and "yes, but ..." is never an approval.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent, approvals, off_record
from agent_friday.services import reflex_chat as rc
from agent_friday.services import turn_plan as tp
from agent_friday.services import turn_receipt as tr

KEY = b"k" * 32
SID = "sess-1"
CONV = "conv-1"
TURN = "turn-reflex-1"
PAGE = {"source": "app_page"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    agent._PENDING_CONFIRMATIONS.clear()
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(agent, "_sandbox_policy", lambda *a: (True, ""))
    monkeypatch.setattr(agent, "_tools_requiring_confirmation", lambda: {"run_command"})
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(tmp_path))
    monkeypatch.setattr(action_gate, "_governance_key", lambda: KEY)
    monkeypatch.setattr(tp, "plans_path", lambda: tmp_path / "plans.jsonl")
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    tp._recent.clear()
    tp.begin_turn(None)
    rc.reset_stats()
    yield tmp_path
    tp.begin_turn(None)
    agent._PENDING_CONFIRMATIONS.clear()


def _ask(command="Write-Output first"):
    ctx = agent.prepare_confirmation_ctx(
        SID, "run this", {"authenticated": True, "conversation_id": CONV})
    result = agent._execute_tool("run_command", {"command": command}, session_ctx=ctx)
    assert "CONFIRMATION REQUIRED" in result
    return ctx


def _approve(message, **kw):
    kw.setdefault("local", True)
    return rc.approval_turn(message, kw.pop("data", dict(PAGE)), session_id=SID,
                            conversation_id=CONV, turn_id=TURN,
                            settings=kw.pop("settings", {}), **kw)


def _handler(monkeypatch):
    calls = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(dict(args)) or "observed output")
    return calls


# --- approval replies -----------------------------------------------------------

def test_a_whole_yes_runs_the_stored_action_with_the_same_outcome(monkeypatch):
    calls = _handler(monkeypatch)
    _ask("Write-Output    first")
    ctx = _approve("yes")
    assert ctx is not None
    out = agent.resume_confirmed_action(ctx)
    assert calls == [{"command": "Write-Output    first"}]
    assert out["result"] == "observed output"
    # The question is spent; a second yes runs nothing.
    assert agent.resume_confirmed_action(
        agent.prepare_confirmation_ctx(SID, "yes", {"authenticated": True,
                                                    "conversation_id": CONV})) is None
    assert len(calls) == 1


def test_the_reflex_outcome_equals_the_ordinary_claim_path(monkeypatch):
    """Same call, same result: the rule only moves the claim ahead of the prep."""
    calls = _handler(monkeypatch)
    _ask()
    via_rule = agent.resume_confirmed_action(_approve("go ahead"))
    agent._PENDING_CONFIRMATIONS.clear()
    _ask()
    ordinary = agent.resume_confirmed_action(agent.prepare_confirmation_ctx(
        SID, "go ahead", {"authenticated": True, "conversation_id": CONV}))
    assert via_rule["name"] == ordinary["name"]
    assert via_rule["input"] == ordinary["input"]
    assert via_rule["result"] == ordinary["result"]
    assert len(calls) == 2


def test_the_reflex_context_names_the_rule_as_the_chooser(monkeypatch, isolated):
    _handler(monkeypatch)
    _ask()
    ctx = _approve("yes")
    assert ctx["chosen_by"] == "rule"
    plan = tp.get(TURN)
    assert plan["route_proposal"] == "reflex"
    assert plan["reflex"] == {"by": "rule", "kind": "approval_reply", "outcome": "approved"}


def test_an_escalated_card_is_resolved_once_by_the_reflex(monkeypatch):
    calls = _handler(monkeypatch)
    _ask()
    ctx2 = agent.prepare_confirmation_ctx(
        SID, "what does that do?", {"authenticated": True, "conversation_id": CONV})
    assert "CONFIRMATION ESCALATED" in agent._execute_tool(
        "run_command", {"command": "Write-Output first"}, session_ctx=ctx2)
    card = approvals.list_approvals(status="pending")[0]
    agent.resume_confirmed_action(_approve("yes"))
    approvals.decide(card["approval_id"], "approve")
    assert len(calls) == 1
    assert approvals.get_approval(card["approval_id"])["consumed"]


@pytest.mark.parametrize("reply", [
    "yes, but put it in the other folder",
    "sure but only the first one",
    "ok do it, but not today",
    "yes and also delete the old copy",
])
def test_yes_but_is_a_change_request_never_an_approval(monkeypatch, reply):
    calls = _handler(monkeypatch)
    _ask()
    assert _approve(reply) is None
    assert not calls
    plan = tp.get(TURN)
    assert plan["reflex"]["outcome"] == "change_request"
    assert plan["route_proposal"] is None
    # Today's path: the model reads the words and raises a REVISED question.
    ctx = agent.prepare_confirmation_ctx(
        SID, reply, {"authenticated": True, "conversation_id": CONV})
    assert not ctx.get("confirm_granted_fp")
    revised = agent._execute_tool("run_command", {"command": "Write-Output elsewhere"},
                                  session_ctx=ctx)
    assert "CONFIRMATION REQUIRED" in revised and not calls
    # ... and the original action is not approved by the reply either.
    assert agent.resume_confirmed_action(ctx) is None and not calls


@pytest.mark.parametrize("reply", ["wait, yes", "no", "what does that do?", "hmm"])
def test_other_replies_take_the_ordinary_path(monkeypatch, reply):
    calls = _handler(monkeypatch)
    _ask()
    assert _approve(reply) is None
    assert not calls


def test_a_yes_with_nothing_waiting_is_not_a_reflex(monkeypatch):
    _handler(monkeypatch)
    assert _approve("yes") is None


@pytest.mark.parametrize("data", [
    {"source": "sms"}, {"source": "telegram"}, {"surface": "discord"},
    {"channel": "live_call"}, {"origin": "local_api"}, {"via": "webhook"},
    {"image": "data:image/png;base64,AAAA"}, {"attachments": [{"name": "x"}]},
])
def test_untrusted_surfaces_never_get_a_reflex(monkeypatch, data):
    calls = _handler(monkeypatch)
    _ask()
    assert _approve("yes", data=data) is None
    assert not calls


def test_a_request_from_off_this_pc_never_gets_a_reflex(monkeypatch):
    """A paired phone arrives through the tunnel, so it is not local."""
    calls = _handler(monkeypatch)
    _ask()
    assert _approve("yes", local=False) is None
    assert not calls


def test_pasted_content_is_not_a_reply(monkeypatch):
    calls = _handler(monkeypatch)
    _ask()
    assert _approve("yes\n" + "x" * 300) is None
    assert _approve("yes " + "x" * 300) is None
    assert not calls


def test_the_approval_setting_off_sends_every_reply_the_ordinary_way(monkeypatch):
    calls = _handler(monkeypatch)
    _ask()
    assert _approve("yes", settings={"reflex_approval_replies": False}) is None
    assert not calls
    # unset is on
    assert _approve("yes", settings={}) is not None


def test_off_the_record_leaves_no_plan_and_no_count(monkeypatch):
    _handler(monkeypatch)
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    _ask()
    assert _approve("yes") is not None
    assert tp.get(TURN) is None
    assert rc.stats() == {}


# --- "open the X" ---------------------------------------------------------------

class _Res:
    def __init__(self, status, **kw):
        self.status = status
        self.text = kw.get("text", "")
        self.command = kw.get("command")
        self.chosen = kw.get("chosen")
        self.question = kw.get("question", "")
        self.reason = kw.get("reason", "")


class _Chosen:
    title = "Go-live checklist"


OPEN_ON = {"reflex_open_enabled": True}


def _open(message="open the go-live checklist", *, resolve, settings=OPEN_ON, **kw):
    kw.setdefault("local", True)
    return rc.open_turn(message, kw.pop("data", dict(PAGE)), session_id=SID,
                        conversation_id=CONV, turn_id=TURN, settings=settings,
                        resolve=resolve, **kw)


def _boom(*a, **k):
    raise AssertionError("the resolver must not be called")


@pytest.mark.parametrize("settings", [{}, {"reflex_open_enabled": None},
                                      {"reflex_open_enabled": False}])
def test_opt_in_off_never_calls_the_resolver(settings):
    assert _open(resolve=_boom, settings=settings) is None


def test_the_default_setting_is_unchosen_and_approval_is_on():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["reflex_open_enabled"] is None
    assert DEFAULT_SETTINGS["reflex_approval_replies"] is True


@pytest.mark.parametrize("data,local", [({"source": "sms"}, True), ({"source": "telegram"}, True),
                                        ({"origin": "local_api"}, True), ({}, False),
                                        ({"screenshot": "x"}, True)])
def test_untrusted_surfaces_never_reach_the_resolver(data, local):
    assert _open(resolve=_boom, data=data, local=local) is None


def test_a_resolved_command_goes_through_authorize_before_the_handler(monkeypatch):
    order = []
    real = action_gate.authorize

    def spy(tool_name, args, session_ctx=None, **kw):
        order.append(("gate", tool_name, (session_ctx or {}).get("chosen_by")))
        return real(tool_name, args, session_ctx, **kw)
    monkeypatch.setattr(action_gate, "authorize", spy)
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "navigate_to",
                        lambda inp: order.append(("handler", dict(inp))) or "NAV_OK:email shown")
    res = _Res("command", command={"tool": "navigate_to",
                                   "input": {"kind": "email", "id": "t-1"}},
               chosen=_Chosen())
    out = _open(resolve=lambda text: res)
    assert out["outcome"] == "done"
    assert out["reply"] == "Done: opened Go-live checklist (instant, no model)."
    assert [o[0] for o in order] == ["gate", "handler"]
    assert order[0][1:] == ("navigate_to", "resolver")
    plan = tp.get(TURN)
    assert plan["reflex"] == {"by": "resolver", "kind": "open", "tool": "navigate_to",
                              "outcome": "done"}
    assert plan["route_proposal"] == "reflex"
    # no words of the turn and no item title in the plan
    blob = repr(plan)
    assert "go-live" not in blob.lower() and "t-1" not in blob


def test_a_refusal_is_reported_as_one_and_nothing_opens(monkeypatch):
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "navigate_to",
                        lambda inp: ran.append(inp) or "NAV_OK:x")
    monkeypatch.setattr(agent, "_governance_check", lambda *a, **kw: (False, "test policy hold"))
    res = _Res("command", command={"tool": "navigate_to", "input": {"kind": "email", "id": "t"}},
               chosen=_Chosen())
    out = _open(resolve=lambda text: res)
    assert not ran
    assert out["outcome"] == "refused"
    assert out["reply"].startswith("I did not open Go-live checklist")
    assert "Done:" not in out["reply"]


def test_a_tool_that_needs_a_yes_asks_and_the_yes_is_a_reflex(monkeypatch):
    opened = []
    monkeypatch.setattr(agent, "_tools_requiring_confirmation", lambda: {"open_url"})
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "open_url",
                        lambda inp: opened.append(dict(inp)) or "Opened.")
    res = _Res("command", command={"tool": "open_url", "input": {"url": "https://example.com/a"}},
               chosen=_Chosen())
    out = _open(resolve=lambda text: res)
    assert out["outcome"] == "asked" and not opened
    assert "example.com/a" in out["reply"]
    ctx = _approve("yes")
    assert ctx is not None
    done = agent.resume_confirmed_action(ctx)
    assert opened == [{"url": "https://example.com/a"}] and done["result"] == "Opened."


def test_navigation_the_desktop_did_not_confirm_is_not_called_done(monkeypatch):
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "navigate_to",
                        lambda inp: "NAV_FAIL: no desktop is connected")
    res = _Res("command", command={"tool": "navigate_to", "input": {"kind": "email", "id": "t"}},
               chosen=_Chosen())
    out = _open(resolve=lambda text: res)
    assert out["outcome"] == "not_confirmed"
    assert "Done:" not in out["reply"]


def test_ask_is_one_question_back_with_no_command(monkeypatch):
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "navigate_to", lambda inp: ran.append(inp))
    res = _Res("ask", question="Did you mean A, or B?")
    out = _open(resolve=lambda text: res)
    assert out["reply"] == "Did you mean A, or B?" and out["outcome"] == "ask"
    assert not ran


@pytest.mark.parametrize("status,reason,counted", [
    ("brain", "no_open_verb", "open_to_model"),
    ("not_found", "no_candidates", "open_to_model"),
    ("brain", "laya not loaded", "open_degraded"),
    ("brain", "error: ModuleNotFoundError", "open_degraded"),
])
def test_brain_and_not_found_take_the_normal_turn_and_are_counted(status, reason, counted):
    assert _open(resolve=lambda text: _Res(status, reason=reason)) is None
    assert rc.stats() == {counted: 1}


def test_laya_absent_degrades_to_the_normal_turn(monkeypatch):
    """The real resolver with Laya missing: a plain 'brain', counted as degraded."""
    from agent_friday.services import laya_runtime
    monkeypatch.setattr(laya_runtime, "ask", lambda *a, **k: {
        "status": "missing", "answers": {}, "cached": [], "reason": "laya not loaded",
        "elapsed_ms": 0.1, "engine": None})
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "navigate_to", lambda inp: ran.append(inp))
    out = rc.open_turn("open the go-live checklist", {}, session_id=SID, conversation_id=CONV,
                       turn_id=TURN, settings=OPEN_ON, local=True)
    assert out is None and not ran
    assert rc.stats() == {"open_degraded": 1}


def test_a_resolver_that_raises_takes_the_normal_turn():
    def bad(text):
        raise RuntimeError("encoder exploded")
    assert _open(resolve=bad) is None
    assert rc.stats() == {"open_error": 1}


def test_the_receipt_says_resolver(monkeypatch, isolated):
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "navigate_to", lambda inp: "NAV_OK:shown")
    res = _Res("command", command={"tool": "navigate_to", "input": {"kind": "email", "id": "t"}},
               chosen=_Chosen())
    _open(resolve=lambda text: res)
    receipt = tr.build(TURN)
    assert receipt["plan"]["reflex"]["by"] == "resolver"
    assert all(d.get("chosen_by") in (None, "resolver") for d in receipt["decisions"])


def test_a_turn_with_no_opening_verb_is_never_looked_at():
    assert _open("what is the weather like", resolve=_boom) is None
    assert rc.stats() == {}


# --- a Laya question must not leave an older question armed (stray-yes) -------------

def _armed():
    return any(e.get("awaiting_reply") for b in agent._PENDING_CONFIRMATIONS.values() for e in b.values())


def test_a_resolver_question_disarms_an_older_pending_send_so_a_yes_approves_nothing(monkeypatch):
    """The model asked 'send this?' (armed); the owner moved on and said 'open the email from Mara'; the
    resolver asks 'did you mean ...?'; the next 'yes' answers THAT question, never the old send."""
    ran = _handler(monkeypatch)
    _ask()
    assert _armed()
    out = _open("open the email from Mara", resolve=lambda t: _Res("ask", question="Did you mean the one from Mara?"))
    assert out["outcome"] == "ask"
    assert not _armed()
    assert _approve("yes") is None
    assert ran == []


def test_a_point_and_act_question_disarms_an_older_pending_send(monkeypatch):
    from agent_friday.services import laya, laya_router as lr, point_act
    ran = _handler(monkeypatch)
    _ask()
    assert _armed()
    monkeypatch.setattr(point_act, "looks_like", lambda m: True)
    monkeypatch.setattr(laya, "point_act_turn", lambda *a, **k: lr.Route(
        "ask", question="Did you mean the one from Mara?", layer="point_act"))
    out = rc.open_turn("open the second one", {}, session_id=SID, conversation_id=CONV, turn_id=TURN,
                       settings=OPEN_ON, local=True)
    assert out["outcome"] == "ask"
    assert not _armed()
    assert _approve("yes") is None
    assert ran == []


def test_disarm_keeps_the_entry_but_it_can_no_longer_be_granted(monkeypatch):
    _ask()
    assert agent.disarm_pending(SID, CONV) >= 1
    assert any(agent._PENDING_CONFIRMATIONS.values())            # stored, not dropped
    assert not _armed()


# --- the typed surface needs the page's own proof -------------------------------------

def test_a_local_post_that_omits_the_page_source_is_not_trusted():
    assert rc.trusted_surface({"source": "app_page"}, "yes", local=True) is True
    assert rc.trusted_surface({}, "yes", local=True) is False
    assert rc.trusted_surface({"source": ""}, "yes", local=True) is False
    assert rc.trusted_surface({"source": "app"}, "yes", local=True) is False


def test_a_post_without_the_page_session_is_not_trusted_whatever_it_says():
    assert rc.trusted_surface({"source": "app_page"}, "yes", local=True, page=False) is False


def test_a_voice_mode_post_is_not_a_typed_line():
    assert rc.trusted_surface({"source": "app_page", "voice_mode": True}, "yes", local=True) is False
