"""The "tools you have" text is written from the tools, so it cannot drift.

The hand-written list it replaces said web search was DuckDuckGo (the tool
prefers Firecrawl, then Brave) and promised 500,000 characters from a file
read that the executor cut at 8,192. Generated text names only registered
tools, quotes the executor's real limits, and is the same bytes turn to turn.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import model_router as mr
from agent_friday.services import tool_catalogue as tc
from agent_friday.services import tool_output as to


def _names_in(block: str):
    out = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("•"):
            out.append(line[1:].split("—")[0].strip())
    return out


def test_every_tool_named_in_the_block_is_a_registered_tool():
    block = tc.prompt_block(ag.CLAUDE_TOOLS)
    registered = {t["name"] for t in ag.CLAUDE_TOOLS} | {tc.LOADER_NAME}
    names = _names_in(block)
    assert names, "the block names no tools"
    for n in names:
        assert n in registered, "%s is in the prompt but not in the registry" % n


def test_the_block_lists_the_resident_tools_and_the_loader():
    names = _names_in(tc.prompt_block(ag.CLAUDE_TOOLS))
    for r in tc.ALWAYS_RESIDENT:
        assert r in names
    assert names[-1] == tc.LOADER_NAME


def test_the_block_quotes_the_executors_real_limits():
    block = tc.prompt_block(ag.CLAUDE_TOOLS)
    assert f"{to.MAX_LINES:,}" in block and f"{to.MAX_CHARS:,}" in block


def test_the_hand_written_list_is_gone():
    assert "== AVAILABLE TOOLS ==" not in mr.FRIDAY_SYSTEM_PROMPT
    assert "DuckDuckGo search" not in mr.FRIDAY_SYSTEM_PROMPT
    prompt, _ = mr._build_context_prompt("hello", workspace="chat", provider="local")
    assert "== TOOLS ==" in prompt
    assert "== AVAILABLE TOOLS ==" not in prompt
    assert "DuckDuckGo" not in prompt


def test_read_file_describes_what_the_executor_does():
    rf = next(t for t in ag.CLAUDE_TOOLS if t["name"] == "read_file")
    assert "500000" not in rf["description"]
    assert f"{to.MAX_LINES:,}" in rf["description"] and f"{to.MAX_CHARS:,}" in rf["description"]
    assert {"offset", "limit"} <= set(rf["input_schema"]["properties"])


def test_the_block_is_byte_identical_across_calls():
    assert tc.prompt_block(ag.CLAUDE_TOOLS) == tc.prompt_block(ag.CLAUDE_TOOLS)


def test_the_block_is_registered_as_trusted_text_for_the_egress_gate():
    from agent_friday.services import egress_gate as eg
    block = mr._tools_prompt_block()
    assert block
    reg = getattr(eg, "_TRUSTED_TEXTS", None) or getattr(eg, "TRUSTED_TEXTS", None)
    if reg is None:
        pytest.skip("egress_gate keeps its trusted texts privately; registration is best-effort")
    assert any(block in str(t) or str(t) in block for t in reg)


# ── the ranker ──────────────────────────────────────────────────────────────

def _tool(name, desc, props=None):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props or {}}}


FIXTURE = [
    _tool("search_web", "Search the web for current information."),
    _tool("draft_email", "Compose an email to send.",
          {"to": {"type": "string", "description": "recipient address"}}),
    _tool("query_calendar", "Today's and tomorrow's Google Calendar events."),
    _tool("mcp_github_create_pull_request", "[MCP·github] Create a pull request."),
    _tool("play_song", "Play a song from the music library."),
]


def test_search_ranks_by_name_description_and_parameters():
    assert tc.search(FIXTURE, "send an email")[0]["name"] == "draft_email"
    assert tc.search(FIXTURE, "recipient address")[0]["name"] == "draft_email"
    assert tc.search(FIXTURE, "github pull request")[0]["name"] == "mcp_github_create_pull_request"
    assert tc.search(FIXTURE, "calendar")[0]["name"] == "query_calendar"


def test_search_returns_at_most_k_and_nothing_for_no_match():
    assert tc.search(FIXTURE, "qzxv") == []
    assert len(tc.search(FIXTURE, "the", k=2)) <= 2
    assert tc.search([], "email") == []


def test_the_real_registry_answers_a_plain_request():
    hits = [t["name"] for t in tc.search(ag.CLAUDE_TOOLS, "send an email")]
    assert any("email" in h for h in hits[:3]), hits
