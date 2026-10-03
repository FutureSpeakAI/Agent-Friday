"""A tool called by a local seat has 3 seconds, and a tool that scans returns
what it has found by then, marked partial, instead of making the seat wait.

The budget is a deadline tools read, never an interruption: a tool that does
not read it runs to completion, and a cloud seat's calls carry no budget."""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import tool_deadline as td


def test_the_local_budget_is_three_seconds():
    assert td.LOCAL_TOOL_BUDGET_S == 3.0


def test_with_no_budget_nothing_expires_and_a_tool_keeps_its_own_deadline():
    assert not td.active() and not td.expired()
    d = td.deadline(10.0)
    assert 9.0 < d - time.monotonic() <= 10.0


def test_a_budget_shortens_a_tool_s_own_deadline_and_expires():
    with td.budget(0.05):
        assert td.active()
        assert td.deadline(10.0) - time.monotonic() <= 0.05
        time.sleep(0.08)
        assert td.expired()
    assert not td.active() and not td.expired()


def test_search_files_returns_what_it_found_when_the_budget_runs_out(tmp_path, monkeypatch):
    from agent_friday.services import file_search as fs
    docs = tmp_path / "Documents"
    docs.mkdir()
    for i in range(5):
        (docs / f"note{i}.txt").write_text("x")
    monkeypatch.setattr(fs, "_configured_roots", lambda: {"documents": docs})
    monkeypatch.setattr(fs, "_home_roots", lambda: {"documents": docs})
    monkeypatch.setattr(fs, "_vault_root", lambda: tmp_path / ".friday" / "vault")
    assert fs.search_files(query="note")["truncated"] is False
    # A deadline already in the past: the first file is past the budget.
    with td.budget(-1.0):
        out = fs.search_files(query="note")
    assert out["truncated"] is True and "receipt" in out


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    from agent_friday.services.knowledge_graph import wiki_graph as wg
    root = tmp_path / "wiki"
    root.mkdir()
    for i in range(6):
        (root / f"atlas-{i}.md").write_text("Atlas page %d" % i, encoding="utf-8")
    monkeypatch.setattr(wg, "WIKI_DIR", root)
    return root


def test_search_wiki_returns_the_hits_found_before_the_budget_ran_out(wiki, monkeypatch):
    from agent_friday.services import agent
    calls = {"n": 0}

    def expired():
        calls["n"] += 1
        return calls["n"] > 2

    monkeypatch.setattr(td, "expired", expired)
    out = json.loads(agent._tool_search_wiki({"query": "atlas", "limit": 20}))
    assert len(out["hits"]) == 2
    assert out["partial"] is True and "time budget" in out["note"]


def test_search_wiki_with_no_hits_in_time_says_the_search_was_partial(wiki, monkeypatch):
    from agent_friday.services import agent
    monkeypatch.setattr(td, "expired", lambda: True)
    out = agent._tool_search_wiki({"query": "atlas"})
    assert "before the time budget ran out" in out


def test_search_wiki_without_a_budget_searches_everything(wiki):
    from agent_friday.services import agent
    out = json.loads(agent._tool_search_wiki({"query": "atlas", "limit": 20}))
    assert len(out["hits"]) == 6 and "partial" not in out


def _drive(monkeypatch, *, provider, seat=None):
    from agent_friday.services import agent as ag
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None, raising=False)
    seen = []

    def fake_execute(name, args, **kw):
        seen.append((td.active(), td.deadline(60.0) - time.monotonic()))
        return "ok"

    monkeypatch.setattr(ag, "_execute_tool", fake_execute)
    rounds = {"n": 0}

    def send_fn(convo, tools, **over):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "search_wiki", "arguments": json.dumps({"query": "q"})}}]},
                "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
        return {"choices": [{"message": {"content": "done."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

    tools = [{"type": "function", "function": {
        "name": "search_wiki", "parameters": {"type": "object", "properties": {}}}}]
    kw = {"seat": seat} if seat else {}
    text, _ = ag._oai_agentic_loop([{"role": "user", "content": "go"}], tools, send_fn,
                                   provider=provider, model="m", session_ctx={}, **kw)
    assert text == "done."
    return seen


def test_a_local_seat_s_tool_call_runs_under_the_three_second_budget(monkeypatch):
    seen = _drive(monkeypatch, provider="local")
    active, left = seen[0]
    assert active and 0 < left <= td.LOCAL_TOOL_BUDGET_S
    assert not td.active(), "the budget ends with the call"


def test_a_cloud_seat_s_tool_call_carries_no_budget(monkeypatch):
    seen = _drive(monkeypatch, provider="openai")
    assert seen[0][0] is False
