"""Commands and the agent run in a codebase behind ONE card per task (Chat Hub
M3b; salon spec §4.5 "Trust this codebase"): the first `codebase_run` raises a
single approval card; approving it mints a grant scoped to that codebase, the
first command runs, and the rest of the task runs on the grant without a card
per command. No grant, no card: nothing runs. `codebase_agent` sits behind the
same gate.
"""
from __future__ import annotations

import shutil

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent as ag
from agent_friday.services import approvals
from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs

needs_powershell = pytest.mark.skipif(
    shutil.which("powershell") is None,
    reason="the Terminal runs Windows PowerShell (powershell.exe); this host has none")


@pytest.fixture(autouse=True)
def _roots(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    # The approvals store and the grants are this test's own: a card another
    # test left pending must not be counted here, nor this test's left behind.
    from agent_friday.services import approvals as _ap
    monkeypatch.setattr(_ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(_ap, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(_ap, "_notify_pending", lambda rec: None)
    from agent_friday.governance import action_gate as _gate
    monkeypatch.setattr(_gate, "_grants_file", lambda: tmp_path / "grants.json")
    yield


def _task_cards(cid):
    return [a for a in approvals.list_approvals(status="pending")
            if a.get("kind") == "codebase_task" and (a.get("payload") or {}).get("codebase_id") == cid]


def _bound():
    conv = convs.create("Build it")
    rec = cb.create("Rent tracker", template="static", conversation_id=conv["id"])
    return conv, rec


def test_run_and_agent_are_outward_and_gate_themselves():
    assert "codebase_run" not in action_gate.INTERNAL_TOOLS
    assert "codebase_agent" not in action_gate.INTERNAL_TOOLS
    assert "codebase_run" in action_gate.OUTWARD_TOOLS and "codebase_agent" in action_gate.OUTWARD_TOOLS
    assert "codebase_run" in action_gate.SELF_GATED and "codebase_agent" in action_gate.SELF_GATED
    assert "codebase_run" in ag.CLAUDE_TOOL_HANDLERS and ag.TOOL_RINGS.get("codebase_run") == 1


def test_the_file_tools_are_decided_by_which_codebase():
    for name in ("codebase_edit", "codebase_undo", "codebase_read"):
        assert name in action_gate.BY_ARGUMENT and action_gate.known(name), name
    conv = convs.create("Build it")
    rec = cb.create("Rent tracker", template="static", conversation_id=conv["id"])
    assert action_gate.classify("codebase_read", {"codebase_id": rec["id"]})[0] == action_gate.INTERNAL
    assert action_gate.classify("codebase_edit", {"codebase_id": rec["id"]})[0] == action_gate.INTERNAL, "her own codebase"
    assert action_gate.classify("codebase_edit", {"codebase_id": "cb-none"})[0] == action_gate.OUTWARD, "nothing in scope"


def test_a_codebase_grant_is_consumed_by_its_scope_only(tmp_path):
    g = action_gate.create_grant(tools=["codebase_run"], scope="codebase:cb-1", expires_in_seconds=60, max_uses=2)
    assert action_gate.consume_grant("codebase_run", "codebase:cb-2") is None
    assert action_gate.consume_grant("codebase_agent", "codebase:cb-1") is None
    assert action_gate.consume_grant("codebase_run", "codebase:cb-1")["grant_id"] == g["grant_id"]
    assert action_gate.consume_grant("codebase_run", "codebase:cb-1") is not None
    assert action_gate.consume_grant("codebase_run", "codebase:cb-1") is None, "two uses, then nothing"


@needs_powershell
def test_the_first_command_raises_one_card_and_approval_runs_the_task(monkeypatch):
    conv, rec = _bound()
    tok = ag._CURRENT_CONVERSATION.set(conv["id"])
    try:
        out = ag._tool_codebase_run({"command": "Write-Output first"})
        assert out["status"] == "waiting" and out.get("approval_id")
        cards = _task_cards(rec["id"])
        assert len(cards) == 1 and cards[0]["approval_id"] == out["approval_id"]
        assert cb.runs(rec["id"]) == [], "nothing ran before the decision"
        # asking again for the same task does not raise a second card
        again = ag._tool_codebase_run({"command": "Write-Output first"})
        assert again["status"] == "waiting" and again["approval_id"] == out["approval_id"]
        assert len(_task_cards(rec["id"])) == 1

        approvals.decide(out["approval_id"], "approve")
        runs = cb.runs(rec["id"])
        assert len(runs) == 1 and "first" in runs[0]["output"], "approval ran the first command"
        grants = [g for g in action_gate.list_grants() if g["scope"] == "codebase:" + rec["id"]]
        assert len(grants) == 1 and "codebase_run" in grants[0]["tools"] and "codebase_agent" in grants[0]["tools"]
        # the rest of the task runs on the grant, no card
        out2 = ag._tool_codebase_run({"command": "Write-Output second"})
        assert out2["status"] == "ok" and "second" in out2["output"]
        assert len(_task_cards(rec["id"])) == 0
        # the result reached the conversation
        texts = [m.get("text") or "" for m in convs.messages(conv["id"])]
        assert any("first" in t for t in texts)
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)


def test_a_denied_card_runs_nothing_and_mints_nothing():
    conv, rec = _bound()
    tok = ag._CURRENT_CONVERSATION.set(conv["id"])
    try:
        out = ag._tool_codebase_run({"command": "Write-Output nope"})
        approvals.decide(out["approval_id"], "deny")
        assert cb.runs(rec["id"]) == []
        assert [g for g in action_gate.list_grants() if g["scope"] == "codebase:" + rec["id"]] == []
        out2 = ag._tool_codebase_run({"command": "Write-Output nope"})
        assert out2["status"] == "waiting", "a new ask raises a new card, nothing runs on its own"
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)


def test_the_agent_needs_the_same_grant(monkeypatch):
    conv, rec = _bound()
    cb.set_engine(rec["id"], "claude_agent")
    calls = []
    monkeypatch.setattr("agent_friday.services.claude_engine.run_task", lambda *a, **k: calls.append(a) or {"status": "ok", "say": "done"})
    tok = ag._CURRENT_CONVERSATION.set(conv["id"])
    try:
        out = ag._tool_codebase_agent({"task": "add a search box"})
        assert out["status"] == "waiting" and calls == []
        approvals.decide(out["approval_id"], "approve")
        assert len(calls) == 1, "approval ran the task"
        out2 = ag._tool_codebase_agent({"task": "and a footer"})
        assert out2["status"] == "ok" and len(calls) == 2, "on the grant, no card"
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
