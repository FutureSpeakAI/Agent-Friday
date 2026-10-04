"""'Friday, improve the News workspace' (salon spec §4.9.1 item 3): one tool,
reachable from chat and from voice through the governed path, that opens a
workspace's codebase chat or says plainly why it cannot.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent as ag
from agent_friday.services import voice_engine
from agent_friday.services import workspace_bundles as wb


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    # The approvals store is this test's own, so a pending count is its own.
    from agent_friday.services import approvals as _ap
    monkeypatch.setattr(_ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(_ap, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(_ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(wb, "_root", lambda: tmp_path / "workspaces")
    from agent_friday.services import codebases as cb, conversations as convs
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    yield


def _tool(name):
    return next(t for t in (ag.CLAUDE_TOOLS + ag.WORKSPACE_TOOLS.get("hub", [])) if t["name"] == name)


def test_the_tool_is_declared_inside_and_in_ring_one():
    t = _tool("improve_workspace")
    assert "workspace" in t["input_schema"]["properties"]
    assert "improve_workspace" in action_gate.INTERNAL_TOOLS
    assert ag.TOOL_RINGS.get("improve_workspace") == 1
    assert "improve_workspace" in ag.CLAUDE_TOOL_HANDLERS
    # The description is the behaviour: it says what to do while it runs and
    # names the fallback for a native workspace.
    d = t["description"].lower()
    assert "native" in d and "not built" in d or "friday's own" in d


def test_voice_declares_the_same_tool_and_routes_it_through_the_gate():
    names = [t[0] for t in voice_engine._VOICE_LIVE_TOOLS]
    assert "improve_workspace" in names
    entry = next(t for t in voice_engine._VOICE_LIVE_TOOLS if t[0] == "improve_workspace")
    assert "workspace" in entry[2] and entry[3] == ["workspace"]
    import inspect
    src = inspect.getsource(voice_engine._voice_tool_run)
    branch = src[src.index('if name in ("improve_workspace", "workspace_swap"):'):]
    branch = branch[:branch.index("if name in (\"run_workflow\"")]
    assert "_tool_improve_workspace" in branch and "_governed(name, _fn, args)" in branch
    # The tool runs inside the call's own conversation, so its chat opens there.
    assert "_CURRENT_CONVERSATION.set(_cid)" in branch


def test_a_native_workspace_is_refused_with_a_typed_blocker_not_a_promise():
    out = ag.CLAUDE_TOOL_HANDLERS["improve_workspace"]({"workspace": "news"})
    assert out.get("status") == "refused" and out.get("blocker") == "needs_phase_7"
    assert "not built" in out.get("say", "").lower() or "own source" in out.get("say", "").lower()


def test_a_bundle_workspace_opens_its_codebase_chat():
    from agent_friday.services import codebases as cb
    rec = cb.create("Rent board", template="bundle")
    ws = wb.install(rec["id"])
    out = ag.CLAUDE_TOOL_HANDLERS["improve_workspace"]({"workspace": ws["id"]})
    assert out.get("status") == "ok" and out.get("conversation_id") and out.get("codebase_id")
    assert out.get("say")


def test_an_unknown_workspace_is_refused_plainly():
    out = ag.CLAUDE_TOOL_HANDLERS["improve_workspace"]({"workspace": "nope-nothing"})
    assert out.get("status") == "refused" and "workspace" in out.get("say", "").lower()


def test_the_swap_tool_raises_one_card_and_is_self_gated(monkeypatch):
    t = _tool("workspace_swap")
    assert "card" in t["description"].lower() and "never say" in t["description"].lower()
    assert "workspace_swap" in action_gate.INTERNAL_TOOLS and "workspace_swap" in action_gate.SELF_GATED
    assert ag.TOOL_RINGS.get("workspace_swap") == 1
    assert "workspace_swap" in [v[0] for v in voice_engine._VOICE_LIVE_TOOLS]
    from agent_friday.services import codebases as cb, approvals
    monkeypatch.setattr(cb, "smoke", lambda cid: {"ran": False, "ok": None, "errors": [], "note": "smoke not run (test)", "sha": "", "ms": 0})
    rec = cb.create("Chore wheel", template="bundle")
    out = ag.CLAUDE_TOOL_HANDLERS["workspace_swap"]({"codebase_id": rec["id"]})
    assert out["status"] == "ok" and out["card_status"] == "pending" and "Chore wheel" in out["spoken"]
    assert len(approvals.list_approvals(status="pending")) == 1
    # The same head asked twice is the same card.
    assert ag.CLAUDE_TOOL_HANDLERS["workspace_swap"]({"codebase_id": rec["id"]})["approval_id"] == out["approval_id"]
    # Leave no pending card behind: the approvals store is shared across tests.
    approvals.decide(out["approval_id"], "deny")
