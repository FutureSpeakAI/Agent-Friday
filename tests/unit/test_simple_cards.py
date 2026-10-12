"""Hide the cards, show the cards: Simple's Home cards by voice and by chat.

The Laya router turns the phrase into a set_chat_tray call carrying `cards`;
the tool sends it to the page in front, and the page's answer decides what the
tool says. The tool's declared schema (and so the voice contract) is unchanged:
`cards` is accepted by the handler and sent by the router, never advertised.
"""
import pathlib
import threading

import pytest

from agent_friday.services import agent, desktop_bus
from agent_friday.services import laya_router as lr
from agent_friday.services import voice_engine as ve

REPO = pathlib.Path(__file__).resolve().parents[2]

HIDE = ["hide the cards", "Please hide the dynamic cards", "close all the cards", "hide my cards", "tuck away the cards",
        "get rid of the cards"]
SHOW = ["show the cards", "show me the cards again", "bring the cards back", "open the cards", "unhide the cards",
        "bring up the cards"]
NOT_THE_CARDS = ["hide the chat", "don't hide the cards", "what cards do I have", "show me the weather", "open news",
                 "hide the dock", "do you hide cards in poker", "discard the cards"]


@pytest.mark.parametrize("phrase", HIDE)
def test_the_router_hides_the_cards_through_the_chat_tray_tool(phrase):
    route = lr._tier0(phrase)
    assert route is not None and route.decision == "tool" and route.tool == "set_chat_tray", phrase
    assert route.args == {"cards": "hide"} and route.layer == "rule"


@pytest.mark.parametrize("phrase", SHOW)
def test_the_router_shows_the_cards_through_the_chat_tray_tool(phrase):
    route = lr._tier0(phrase)
    assert route is not None and route.decision == "tool" and route.tool == "set_chat_tray", phrase
    assert route.args == {"cards": "show"}


def test_the_router_hides_and_shows_the_cards_through_the_chat_tray_tool():
    for phrase in HIDE + SHOW:
        route = lr._tier0(phrase)
        assert route and route.tool == "set_chat_tray" and route.args["cards"] in ("hide", "show"), phrase


@pytest.mark.parametrize("phrase", NOT_THE_CARDS)
def test_other_phrases_do_not_touch_the_cards(phrase):
    route = lr._tier0(phrase)
    assert route is None or not (route.tool == "set_chat_tray" and route.args.get("cards")), (phrase, route)


def test_the_cards_phrases_are_not_taken_for_a_change_to_the_owners_things():
    for phrase in HIDE + SHOW:
        assert not lr._has_write_intent(phrase), phrase


def test_the_voice_contract_gains_no_tool_and_no_argument_for_it():
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "set_chat_tray")
    assert sorted(spec[2]) == ["side", "size", "visible"]
    assert ve._voice_tool_names().count("set_chat_tray") == 1
    tool = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "set_chat_tray")
    assert "cards" not in tool["input_schema"]["properties"]


# ── the tool ────────────────────────────────────────────────────────────────

@pytest.fixture
def pages():
    desktop_bus.reset()
    yield
    desktop_bus.reset()


def _ask(inp, ack):
    """Run the tool while a page answers with `ack`; return (text, message sent)."""
    q = desktop_bus.subscribe("page-1", "chat")
    out = {}
    thread = threading.Thread(target=lambda: out.update(text=agent._tool_set_chat_tray(inp)))
    thread.start()
    message = q.get(timeout=5)
    assert desktop_bus.ack(message["id"], ack)
    thread.join(10)
    return out["text"], message


def test_hiding_the_cards_goes_to_the_page_and_says_what_the_page_did(pages):
    text, sent = _ask({"cards": "hide"}, {"applied": True, "cards": "hidden"})
    assert sent["type"] == "chat_tray" and sent["cards"] == "hide" and "visible" not in sent and "size" not in sent
    assert text == "CHAT_OK — the cards are hidden; Friday and the chat share the screen."


def test_showing_the_cards_says_they_are_back(pages):
    text, sent = _ask({"cards": "show"}, {"applied": True, "cards": "shown"})
    assert sent["cards"] == "show"
    assert text == "CHAT_OK — the cards are back beside Friday."


def test_in_classic_the_tool_says_there_are_no_cards_and_claims_nothing(pages):
    text, _ = _ask({"cards": "hide"}, {"applied": False, "cards_reason": "classic"})
    assert text.startswith("CHAT_FAIL") and "Classic" in text


def test_a_page_that_could_not_change_them_is_not_reported_as_having_done_so(pages):
    text, _ = _ask({"cards": "hide"}, {"applied": False, "cards_reason": "loading"})
    assert text.startswith("CHAT_FAIL") and "hidden" not in text


def test_without_a_page_nothing_changes_and_the_tool_says_so(pages):
    assert agent._tool_set_chat_tray({"cards": "hide"}).startswith("CHAT_NOT_APPLIED")


def test_an_unknown_value_is_refused_before_anything_is_sent(pages):
    q = desktop_bus.subscribe("page-1", "chat")
    assert agent._tool_set_chat_tray({"cards": "sideways"}).startswith("CHAT_FAIL")
    assert q.empty()


def test_the_chat_tray_asks_still_work_beside_it(pages):
    assert agent._tool_set_chat_tray({}).startswith("CHAT_FAIL")
    text, sent = _ask({"visible": False}, {"applied": True, "shown": False, "side": "right"})
    assert "cards" not in sent and text.startswith("CHAT_OK — the chat is hidden")


# ── the pages and the guide ──────────────────────────────────────────────────

def test_the_user_guide_names_the_control_and_the_phrases():
    text = (REPO / "docs" / "user-guide" / "settings.md").read_text(encoding="utf-8")
    assert "Hide cards" in text and "hide the cards" in text and "show the cards" in text
