"""Behaviour-preserving checks for the hook-chain refactor of _execute_tool.

The pre-refactor gate sequence (confirmation → governance → sandbox → log →
PII) is now a registered hook chain; these assert the externally-observable
behaviour is unchanged.
"""
from services import agent


def test_unknown_tool():
    assert agent._execute_tool("nope_not_a_tool", {}) == "Unknown tool: nope_not_a_tool"


def test_governance_denies_unauthenticated_ring2():
    out = agent._execute_tool("search_web", {"query": "x"},
                              session_ctx={"authenticated": False})
    assert out.startswith("[GOVERNANCE DENY]")


def test_confirmation_gate_blocks_until_approved():
    # write_file requires confirmation in an interactive (session_id) chat.
    out = agent._execute_tool("write_file", {"path": "x.txt", "content": "hi"},
                              session_ctx={"session_id": "sess-1"})
    assert "[CONFIRMATION REQUIRED]" in out


def test_background_task_bypasses_confirmation():
    # A scheduled/background task never waits for an interactive yes — so it
    # passes the confirmation gate (and is then governed normally). With an
    # authenticated background ctx, write_file (ring 1) executes its handler.
    out = agent._execute_tool("write_file", {"path": "", "content": ""},
                              session_ctx={"is_background_task": True,
                                           "session_id": "sess-2"})
    assert "[CONFIRMATION REQUIRED]" not in out
