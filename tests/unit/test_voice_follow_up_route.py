"""An elliptical follow-up after a tool turn is routed, never free-generated.

Live: "What's on my calendar today?" ran query_calendar; the next turn,
"What about tomorrow?", matched no rule, so the routed front (which has no
tools) answered from its own imagination about the owner's day. A follow-up
that drops its subject re-uses the previous turn's tool with the new
argument; with no previous tool it is handed to the deeper mind.
"""
import pytest

from agent_friday.routes import voice as rv

HELD = ["query_calendar", "check_email", "search_email", "search_news",
        "delegate_to_friday"]


@pytest.fixture(autouse=True)
def _no_encoder(monkeypatch):
    # Tier 1 unavailable: the worst case, where only the rules can place a turn.
    from agent_friday.services import laya2_encoder
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)
    from agent_friday.services import laya_router
    monkeypatch.setattr(laya_router, "_write", lambda *a, **k: None)


def _turn(ts, text):
    seen = {}

    def seat_turn(user_text, on_delta, plan, run_tool, card_line):
        seen["plan"] = plan
        return "ok"
    rv._run_routed_front_turn(text, lambda d: None,
                              front={"contract": {"names": HELD}},
                              tool_session=ts, seat_turn=seat_turn,
                              send=lambda o: None, session=None)
    return seen["plan"]


def test_what_about_tomorrow_after_the_calendar_reruns_the_calendar():
    ts = {}
    first = _turn(ts, "What's on my calendar today?")
    assert first["tool"] == "query_calendar"
    second = _turn(ts, "What about tomorrow?")
    assert second["tool"] == "query_calendar", second
    assert second["receipt"]["ran"] == "query_calendar"


def test_any_from_a_name_after_checking_email_searches_that_sender():
    ts = {}
    assert _turn(ts, "Check my email")["tool"] == "check_email"
    plan = _turn(ts, "Any from Marlowe?")
    assert plan["tool"] == "search_email", plan
    assert plan["args"] == {"query": "Marlowe"}


def test_a_follow_up_with_no_previous_tool_goes_to_the_deeper_mind():
    plan = _turn({}, "What about tomorrow?")
    assert plan["tool"] == "delegate_to_friday", plan
    assert plan["receipt"]["handed_to"] == "delegate_to_friday"


def test_a_full_request_is_not_taken_for_a_follow_up():
    ts = {}
    _turn(ts, "What's on my calendar today?")
    plan = _turn(ts, "Check my email")
    assert plan["tool"] == "check_email"


def test_the_previous_tool_goes_stale_after_a_few_turns():
    ts = {}
    _turn(ts, "What's on my calendar today?")
    for _ in range(4):
        _turn(ts, "Thanks, that is lovely.")
    plan = _turn(ts, "What about tomorrow?")
    assert plan["tool"] == "delegate_to_friday", plan
