"""A memory line the owner wrote saves with no card; a line built from a page Friday read still gets one.

Provenance attaches to the content, not to the session. A page browsed earlier in the conversation used to hold back
every memory-card add, including a line the owner wrote in their own words about something else, and the card named
whatever source the session had read last. Now a line is the owner's when its words come from the owner's own
messages and it does not draw on anything read. A line paraphrased from a page, or one the owner only said "sure" to,
keeps the card and names the page. Synthetic text only.
"""
from __future__ import annotations

import pytest

import agent_friday.services.agent as agent
from agent_friday.services import approvals, taint
from agent_friday.services import core_memory as cm

SID = "2026-11-22-mem"
PAGE = ("Synthetic museum guide. The museum opens at nine on weekdays and runs free guided tours every Sunday "
        "afternoon. The cafe inside serves soup and bread until four.")
OWNER_LINE = "Daughter's piano lesson is every Thursday at four."
OWNER_SAYS = "Please remember that my daughter's piano lesson is every Thursday at four."


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRIDAY_SANDBOX_ROOT", str(tmp_path))
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "browse_web", lambda inp: PAGE)
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    yield
    taint.reset()
    agent._PENDING_CONFIRMATIONS.clear()


def _turn(message):
    return agent.prepare_confirmation_ctx(SID, message, {"authenticated": True, "owner_surface": True})


def _browse_then_add(owner_message, line):
    ctx = _turn(owner_message)
    agent._execute_tool("browse_web", {"url": "https://guide.example.test/museum"}, session_ctx=ctx)
    return agent._execute_tool("update_core_memory", {"action": "add", "target": "user", "text": line}, session_ctx=ctx)


def _cards():
    return approvals.list_approvals(status="pending", kind="tainted_action")


def test_a_line_the_owner_wrote_saves_with_no_card_after_a_page_was_browsed():
    out = _browse_then_add(OWNER_SAYS, OWNER_LINE)
    assert _cards() == [] and "APPROVAL CARD RAISED" not in out, out
    assert any("piano" in e.lower() for e in cm.entries("user")), (out, cm.entries("user"))
    assert cm.pending() == [], "the line was queued for a keep instead of saved"


def test_a_line_built_from_the_page_text_still_gets_a_card_that_shows_the_page():
    out = _browse_then_add("Please remember what I should know about the museum",
                           "The museum opens at nine on weekdays and runs free guided tours every Sunday afternoon.")
    assert cm.entries("user") == [], "text from a page saved without asking"
    cards = _cards()
    assert cards and "APPROVAL CARD RAISED" in out, out
    warn = [f for f in cards[0]["provenance"]["flags"] if f["severity"] == "warn"]
    assert warn and "guide.example.test" in " ".join(f["source"] for f in warn), warn


def test_a_paraphrase_of_the_page_the_owner_only_said_sure_to_still_gets_a_card():
    out = _browse_then_add("sure", "Museum is open from nine on weekdays, with Sunday afternoon tours that cost nothing.")
    assert cm.entries("user") == [], "a paraphrase of a page saved on a bare yes"
    assert _cards() or cm.pending(), out


def test_a_page_the_owner_repeats_aloud_is_still_the_pages_line_and_keeps_the_card():
    said = "the museum opens at nine on weekdays and runs free guided tours every Sunday afternoon, remember that"
    out = _browse_then_add(said, "Museum opens at nine on weekdays and runs free guided tours every Sunday afternoon.")
    assert cm.entries("user") == [], out
    assert _cards() or cm.pending(), out
