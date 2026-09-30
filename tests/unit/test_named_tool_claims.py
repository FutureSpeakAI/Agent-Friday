"""Receipt checks distinguish observed actions from discussion of tools."""
import pytest

from agent_friday.services import tool_receipts as receipts


@pytest.fixture(autouse=True)
def receipt_book(monkeypatch):
    receipts.begin_turn()
    monkeypatch.setattr(receipts, "_known_tools", lambda: {
        "navigate_to", "search_wiki", "mcp_example_balance"})


@pytest.mark.parametrize("text", [
    "The desktop already opens specific wiki pages, since I can do that with navigate_to.",
    "I can use `navigate_to` to open a wiki page.",
    "I will call search_wiki once the tools are available.",
    "I did not call navigate_to; the search did not run.",
    "navigate_to did not run during this turn.",
    "If I called navigate_to, the workspace would open.",
    "Have I called navigate_to? No.",
    "In the previous turn I called navigate_to.",
    "Earlier I called navigate_to.",
    "navigate_to returned a result in the previous turn.",
    "I called navigate_to yesterday.",
    "For example: I called navigate_to.",
    "You said 'I called navigate_to'.",
    "Tool example:\n```text\nnavigate_to returned NAV_OK\n```",
    "Use search_wiki for local wiki searches.",
])
def test_discussing_a_tool_is_not_an_execution_claim(text):
    assert receipts.unbacked_claims(text) == []


@pytest.mark.parametrize("text, tool", [
    ("I called navigate_to.", "navigate_to"),
    ("I have just executed `navigate_to`.", "navigate_to"),
    ("navigate_to returned NAV_OK.", "navigate_to"),
    ("I searched using search_wiki.", "search_wiki"),
    ("The output from `example.balance` was 42.", "mcp_example_balance"),
    ("I can call navigate_to. I called navigate_to.", "navigate_to"),
    ("I did not call search_wiki; navigate_to returned NAV_OK.", "navigate_to"),
    ("navigate_to returned 'not found'.", "navigate_to"),
    ("I called navigate_to, but it did not return a result.", "navigate_to"),
    ("navigate_to was executed successfully.", "navigate_to"),
    ("I called navigate_to and can confirm the page opened.", "navigate_to"),
])
def test_unobserved_execution_claims_are_still_reported(text, tool):
    assert [c["tool"] for c in receipts.unbacked_claims(text)] == [tool]


@pytest.mark.parametrize("ok", [True, False])
def test_observed_success_or_failure_can_be_reported(ok):
    receipts.record("navigate_to", ok=ok)
    assert receipts.unbacked_claims("navigate_to returned a result.") == []
