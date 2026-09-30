"""An approval resumes the stored action without model regeneration."""
import threading

import pytest

from agent_friday.services import agent, approvals, approval_executor


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    agent._PENDING_CONFIRMATIONS.clear()
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(agent, "_sandbox_policy", lambda *a: (True, ""))
    monkeypatch.setattr(agent, "_tools_requiring_confirmation", lambda: {"run_command"})
    yield
    agent._PENDING_CONFIRMATIONS.clear()


def ask(command="Write-Output first", conversation="one"):
    ctx = agent.prepare_confirmation_ctx(
        "same-day", "run this", {"authenticated": True, "conversation_id": conversation})
    result = agent._execute_tool("run_command", {"command": command}, session_ctx=ctx)
    assert "CONFIRMATION REQUIRED" in result
    return ctx


def answer(text="go ahead", conversation="one"):
    return agent.prepare_confirmation_ctx(
        "same-day", text, {"authenticated": True, "conversation_id": conversation})


def test_yes_resumes_exact_stored_command_without_a_model(monkeypatch):
    calls = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(dict(args)) or "observed output")
    ask("Write-Output    first")
    result = agent.resume_confirmed_action(answer())
    assert calls == [{"command": "Write-Output    first"}]
    assert result["result"] == "observed output"
    assert result["name"] == "run_command"
    assert agent.resume_confirmed_action(answer("yes")) is None


def test_confirmation_is_scoped_to_its_conversation(monkeypatch):
    calls = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(args["command"]) or "done")
    ask("Write-Output one", "one")
    ask("Write-Output two", "two")
    agent.resume_confirmed_action(answer(conversation="one"))
    assert calls == ["Write-Output one"]
    agent.resume_confirmed_action(answer(conversation="two"))
    assert calls == ["Write-Output one", "Write-Output two"]


def test_resume_still_runs_the_governance_hooks(monkeypatch):
    ask()
    calls = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command", lambda args: calls.append(args))
    monkeypatch.setattr(agent, "_governance_check", lambda *a, **kw: (False, "test policy hold"))
    result = agent.resume_confirmed_action(answer())
    assert "GOVERNANCE DENY" in result["result"]
    assert not calls
    assert agent.resume_confirmed_action(answer()) is None


def test_two_simultaneous_replays_run_once(monkeypatch):
    ask()
    ctx = answer()
    calls = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(args) or "done")
    start = threading.Barrier(2)
    results = []

    def run():
        start.wait()
        results.append(agent.resume_confirmed_action(ctx))

    workers = [threading.Thread(target=run) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(calls) == 1
    assert sum(result is not None for result in results) == 1


def escalated():
    ask()
    ctx = answer("what does that do?")
    result = agent._execute_tool("run_command", {"command": "Write-Output first"}, session_ctx=ctx)
    assert "CONFIRMATION ESCALATED" in result
    return approvals.list_approvals(status="pending")[0]


def test_escalated_card_runs_and_reports_to_the_original_conversation(monkeypatch):
    calls, reports = [], []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(args) or "observed output")
    monkeypatch.setattr(approval_executor, "_post_back", lambda rec, text: reports.append((rec, text)))
    card = escalated()
    assert card["payload"]["conversation_id"] == "one"
    approvals.decide(card["approval_id"], "approve")
    approvals.decide(card["approval_id"], "approve")
    assert len(calls) == 1
    assert reports and "observed output" in reports[0][1]
    assert agent.resume_confirmed_action(answer()) is None


def test_chat_approval_of_escalated_card_runs_once(monkeypatch):
    calls = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(args) or "done")
    card = escalated()
    agent.resume_confirmed_action(answer())
    approvals.decide(card["approval_id"], "approve")
    assert len(calls) == 1
    assert approvals.get_approval(card["approval_id"])["consumed"]


def test_old_card_does_not_clear_newer_pending_action(monkeypatch):
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command", lambda args: "done")
    card = escalated()
    ask("Write-Output newer")
    approvals.decide(card["approval_id"], "approve")
    result = agent.resume_confirmed_action(answer())
    assert result["input"] == {"command": "Write-Output newer"}


def test_pending_arguments_are_an_immutable_snapshot(monkeypatch):
    inp = {"command": "Write-Output original"}
    ctx = agent.prepare_confirmation_ctx("same-day", "run this", {
        "authenticated": True, "conversation_id": "one"})
    agent._execute_tool("run_command", inp, session_ctx=ctx)
    inp["command"] = "Write-Output replacement"
    calls = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(dict(args)) or "done")
    agent.resume_confirmed_action(answer())
    assert calls == [{"command": "Write-Output original"}]


def test_chat_and_card_approval_race_runs_once(monkeypatch):
    card = escalated()
    ctx = answer()
    calls, errors = [], []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(args) or "done")
    monkeypatch.setattr(approval_executor, "_post_back", lambda *a: None)
    start = threading.Barrier(2)

    def run(fn):
        start.wait()
        try:
            fn()
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=run, args=(fn,)) for fn in (
        lambda: agent.resume_confirmed_action(ctx),
        lambda: approvals.decide(card["approval_id"], "approve"))]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert not errors
    assert len(calls) == 1
    assert approvals.get_approval(card["approval_id"])["consumed"]


@pytest.mark.parametrize("reply", [
    "please tell me the weather", "show me the card", "right, explain the command",
    "Yes, but change the recipient to someone else", "Yes, but don't run it yet",
    "yes if the file is empty", "okay, use the other file instead", "no thanks",
])
def test_qualified_or_unrelated_reply_does_not_grant_a_pending_action(reply):
    ask()
    ctx = answer(reply)
    assert not ctx.get("confirm_granted")
    assert agent.resume_confirmed_action(ctx) is None


def test_yes_after_an_unrelated_turn_does_not_replay_a_stale_action():
    ask()
    answer("tell me the weather")
    ctx = answer("yes")
    assert not ctx.get("confirm_granted")
    assert agent.resume_confirmed_action(ctx) is None


@pytest.mark.parametrize("reply", ["go ahead", "Yes", "yes, approved. I don't see the card though"])
def test_transcript_approvals_still_resume(reply, monkeypatch):
    ask()
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command", lambda args: "observed")
    assert agent.resume_confirmed_action(answer(reply))["result"] == "observed"


def test_card_publication_and_chat_reply_share_one_execution(monkeypatch):
    ask()
    ctx = answer("what does this do?")
    published, release, replying, completed = (threading.Event() for _ in range(4))
    calls, errors = [], []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(args) or "done")
    create = approvals.create_approval

    def paused_create(**kwargs):
        card = create(**kwargs)
        published.set()
        assert release.wait(3)
        return card

    monkeypatch.setattr(approvals, "create_approval", paused_create)

    def escalate():
        try:
            agent._execute_tool("run_command", {"command": "Write-Output first"}, session_ctx=ctx)
        except Exception as exc:
            errors.append(exc)

    def reply():
        replying.set()
        try:
            agent.resume_confirmed_action(answer())
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()

    first = threading.Thread(target=escalate)
    first.start()
    assert published.wait(3)
    second = threading.Thread(target=reply)
    second.start()
    assert replying.wait(3)
    try:
        assert not completed.wait(0.05)
    finally:
        release.set()
        first.join(3)
        second.join(3)
    assert not errors
    card = approvals.list_approvals()[0]
    approvals.decide(card["approval_id"], "approve")
    assert len(calls) == 1


def test_later_repeat_gets_a_new_card(monkeypatch):
    calls = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(args) or "done")
    monkeypatch.setattr(approval_executor, "_post_back", lambda *a: None)
    first = escalated()
    approvals.decide(first["approval_id"], "approve")
    second = escalated()
    assert first["approval_id"] != second["approval_id"]
    approvals.decide(second["approval_id"], "approve")
    assert len(calls) == 2


@pytest.mark.parametrize("delivered", [True, False])
def test_navigation_card_sends_the_desktop_action_and_reports_its_ack(delivered, monkeypatch):
    from agent_friday.services import desktop_bus
    sent, reports = [], []

    def send(actions, **kwargs):
        sent.extend(actions)
        return {"delivered": delivered, "acked": delivered,
                "ack": {"opened": delivered}, "reason": "no desktop"}

    monkeypatch.setattr(desktop_bus, "send", send)
    monkeypatch.setattr(approval_executor, "_post_back", lambda rec, text: reports.append(text))
    card = approvals.create_approval(
        kind="tool_confirm", subject_type="tool_action", subject_id="navigate-card",
        title="Navigate", action_description="Navigate to knowledge", force_gate=True,
        payload={"tool": "navigate", "input": {"workspace": "knowledge"}})
    approvals.decide(card["approval_id"], "approve")
    assert sent == [{"type": "navigate", "workspace": "knowledge"}]
    detail = approvals.get_approval(card["approval_id"])["used_detail"]
    assert detail["ok"] is delivered
    assert ("NAV_OK:" if delivered else "NAV_FAIL:") in reports[0]
