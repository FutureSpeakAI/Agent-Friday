"""Behaviour-preserving checks for the hook-chain refactor of _execute_tool.

The pre-refactor gate sequence (confirmation → governance → sandbox → log →
PII) is now a registered hook chain; these assert the externally-observable
behaviour is unchanged.
"""
from agent_friday.services import agent


def test_unknown_tool():
    """An unresolvable tool name must fail loudly, nameably, and classifiably.

    The old message was a bare "Unknown tool: x" — true, but a dead end: it
    said the call failed without saying what would have worked, and a dead end
    is where invented tool results came from (a seat reported "SUCCESS:
    Balance retrieved" from a call it never made). The contract is now that
    the message states nothing ran, names the tool, and tells the model not to
    describe an outcome.
    """
    out = agent._execute_tool("nope_not_a_tool", {})
    assert "nope_not_a_tool" in out
    assert "nothing ran" in out.lower()
    # And it must still CLASSIFY as an error — a failure whose prefix is not in
    # _TOOL_ERROR_SENTINELS is silently graded 'ok' by _tool_call_status.
    assert agent._tool_call_status(out) == "error"


def test_misnamed_tool_resolves_to_the_real_one():
    """A model that embellishes a tool name still reaches the right tool.

    Observed: the seat called `mcp_higgsfield_get_balance` when the registered
    name was `mcp_higgsfield_balance`. Resolution happens only when exactly one
    registered tool matches, so an ambiguous guess still fails rather than
    running something nobody asked for.
    """
    agent.CLAUDE_TOOL_HANDLERS["zz_probe_widget"] = lambda _inp: "ran"
    try:
        assert agent._resolve_tool_name("zz_get_probe_widget")[0] == "zz_probe_widget"
        assert agent._resolve_tool_name("zz.probe.widget")[0] == "zz_probe_widget"
        assert agent._resolve_tool_name("zz_probe_nonexistent")[0] is None
    finally:
        agent.CLAUDE_TOOL_HANDLERS.pop("zz_probe_widget", None)


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
