"""Free-form arguments in the Laya router (DA-1a): Laya chooses, generators compose.

  * A fuzzy date ("tomorrow afternoon", "next Tuesday") is resolved by the calendar quick-add
    parser, deterministically; a date the parser cannot place makes the router ask. No model.
  * Composed text (an email body, a reply, a note) is never written by the router or the head.
    A compose, send or reply request is ``defer`` with ``handoff="compose_text"``; only choices
    the candidates offered (a recipient) travel with it, never words.
"""
import json
from datetime import datetime

import pytest

from agent_friday.services import laya_router as lr
from agent_friday.services import laya_router_head as lh

# Friday 2026-10-09, mid-morning.
NOW = datetime(2026, 10, 9, 10, 30)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(lr, "_now", lambda: NOW)
    monkeypatch.setattr(lr, "_today", lambda: NOW.date())     # the calendar template's own clock


# ── fuzzy dates ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,date,part", [
    ("what's on tomorrow afternoon", "2026-10-10", "afternoon"),
    ("what do I have next Tuesday", "2026-10-13", ""),
    ("anything on the calendar this evening, today", "2026-10-09", "evening"),
    ("am I free on Friday morning", "2026-10-09", "morning"),
    ("what's happening on 2026-11-02", "2026-11-02", ""),
])
def test_a_fuzzy_date_is_resolved_by_the_quickadd_parser(clock, text, date, part):
    w = lr.resolve_when(text, now=NOW)
    assert (w.state, w.date, w.part) == ("date", date, part)


@pytest.mark.parametrize("text", [
    "what's on sometime after the holidays", "what do I have next week", "anything coming up soon",
    "what's on later"])
def test_a_date_the_parser_cannot_place_is_unparsable_never_guessed(text):
    assert lr.resolve_when(text, now=NOW).state == "unparsable"


def test_words_with_no_date_in_them_resolve_to_none():
    assert lr.resolve_when("what is in my inbox", now=NOW).state == "none"


def test_a_tool_that_declares_a_date_argument_gets_the_resolved_date(clock, monkeypatch):
    monkeypatch.setitem(lr.DATE_ARGS, "query_calendar", ("date", "part"))
    r = lr.route("what's on tomorrow afternoon", log=False)
    assert (r.decision, r.tool) == ("tool", "query_calendar")
    # the declared date argument and part of the day, beside the template's own declared `day`
    assert r.args == {"day": "2026-10-10", "date": "2026-10-10", "part": "afternoon"}


def test_an_unparsable_date_for_such_a_tool_asks_instead_of_guessing(clock, monkeypatch):
    monkeypatch.setitem(lr.DATE_ARGS, "query_calendar", ("date", "part"))
    r = lr.route("what's on my calendar sometime after the holidays", log=False)
    assert r.decision == "ask" and r.tool == "query_calendar" and r.args == {}
    assert "day" in r.question.lower()


_ISO_DAY = r"^\d{4}-\d{2}-\d{2}"


def _date_props(tool):
    """The arguments the governed schema of ``tool`` declares with a date format: the only ones the
    verifier sanity-checks as dates (call_verifier._is_date_prop), for chat and voice alike (both run
    through agent._execute_tool, which reads this schema)."""
    from agent_friday.services import agent
    props = (agent._schema_for_tool(tool) or {}).get("properties") or {}
    return {k for k, spec in props.items() if isinstance(spec, dict) and spec.get("format") in ("date", "date-time")}


def test_no_date_is_ever_passed_to_a_tool_whose_schema_declares_none(clock):
    """A date reaches a tool only through an argument its governed schema declares with a date format
    (the verifier only checks dates where a schema declares one). query_calendar's schema declares
    ``day`` (the day the words name, which the router passes), and nothing else."""
    import re
    assert lr.DATE_ARGS == {}
    assert _date_props("query_calendar") == {"day"}
    r = lr.route("what's on tomorrow afternoon", log=False)
    assert (r.decision, r.tool, r.args) == ("tool", "query_calendar", {"day": "2026-10-10"})
    # every router template, on words that name a day: a date-shaped value only under a declared date argument
    for name, (_label, template, _req) in lr.TOOLS.items():
        for text in ("what's on tomorrow afternoon", "search for the plan due on Friday",
                     "any emails from tomorrow", "what about Monday"):
            dated = {k for k, v in template(text).items() if isinstance(v, str) and re.match(_ISO_DAY, v)}
            assert dated <= _date_props(name), (name, text, dated)


# ── composed text is never the router's ─────────────────────────────────────

@pytest.mark.parametrize("text", [
    "send an email to my sister saying I'll be late", "reply to my mom and tell her I'll call tonight",
    "please send the report to the team", "draft a note to the landlord about the leaking tap",
    "write an email to the contractor asking for a quote",
    "reply to the landlord and say I'll pay Friday",
    "text Sam that I'm running late", "forward that to my boss with a short note",
    "respond to the invitation and say yes"])
def test_a_compose_send_or_reply_request_is_deferred_to_a_generator_with_no_text(text):
    r = lr.route(text, log=False)
    assert r.decision == "defer" and r.tool is None
    assert r.args == {} and r.chosen == {}
    assert r.handoff == "compose_text"


@pytest.mark.parametrize("text", ["delete the email from the spammer", "archive these messages",
                                  "cancel my three o'clock", "turn off the notifications"])
def test_a_change_that_is_not_composed_text_is_deferred_without_the_compose_handoff(text):
    r = lr.route(text, log=False)
    assert r.decision == "defer" and r.handoff == ""


def test_a_route_carrying_a_composed_argument_is_turned_into_a_defer():
    for key in sorted(lr.COMPOSED_ARGS):
        bad = lr.Route("tool", tool="search_email", args={"query": "x", key: "Dear Sam, ..."}, layer="head")
        out = lr._guard_composed(bad)
        assert out.decision == "defer" and out.args == {} and out.handoff == "compose_text", key


def test_a_route_naming_a_tool_the_router_does_not_hold_is_turned_into_a_defer():
    out = lr._guard_composed(lr.Route("tool", tool="send_email", args={}))
    assert out.decision == "defer"


def test_no_router_tool_template_can_produce_a_composed_argument():
    for name, (_, template, _req) in lr.TOOLS.items():
        for text in ("search for the quarterly plan", "any urgent emails", "pause the podcast",
                     "stop the research task called budget", "slow down"):
            produced = set(template(text))
            assert not (produced & lr.COMPOSED_ARGS), (name, produced)


# ── ... including from the trained head ─────────────────────────────────────

def _head_env(tmp_path, monkeypatch, answer):
    import hashlib
    from agent_friday.services import laya2_encoder
    d = tmp_path / "tool_head" / "owner"
    (d / "model").mkdir(parents=True)
    (d / "model" / lh.CONFIG_NAME).write_text("{}", encoding="utf-8")
    (d / "catalogue.json").write_text("[]", encoding="utf-8")
    (d / lh.RECEIPT_NAME).write_text(json.dumps({
        "v": 1, "certified": True, "threshold": 0.9,
        "config_sha256": hashlib.sha256(b"{}").hexdigest()}), encoding="utf-8")
    monkeypatch.setattr(lh, "root_dir", lambda: tmp_path / "tool_head")
    monkeypatch.setattr(lh, "RECEIPT_TTL_S", 0.0)
    monkeypatch.setattr(lh, "_loader", lambda p: (lambda case: dict(answer)))
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    lh.reset()


def test_a_head_that_proposes_a_write_with_a_body_defers_and_carries_no_text(tmp_path, monkeypatch):
    _head_env(tmp_path, monkeypatch, {"operation": "send_email", "score": 0.99,
                                      "args": {"to": "sam", "body": "Dear Sam, I will be late."}})
    r = lr.route("can you let sam know about the delay", head_mode="on", log=False, budget_ms=5000)
    assert r.decision == "defer" and r.tool is None and r.args == {}
    assert "Dear" not in json.dumps(r.as_dict()) and r.chosen == {}


def test_a_head_pick_among_offered_recipients_travels_as_a_choice_never_as_text(tmp_path, monkeypatch):
    _head_env(tmp_path, monkeypatch, {"operation": "send_email", "score": 0.99,
                                      "args": {"to": "sam@example.test", "body": "free text",
                                               "subject": "hello"}})
    monkeypatch.setattr(lh, "candidate_provider",
                        lambda text, tool, arg: ["sam@example.test", "ann@example.test"] if arg == "to" else [])
    r = lr.route("can you let the person from the project know about the delay", head_mode="on", log=False,
                 budget_ms=5000)
    assert r.decision == "defer"
    assert r.chosen == {"to": "sam@example.test"}            # offered this turn, so chosen
    assert "free text" not in json.dumps(r.as_dict()) and "hello" not in json.dumps(r.as_dict())


def test_a_recipient_the_provider_never_offered_is_not_carried(tmp_path, monkeypatch):
    _head_env(tmp_path, monkeypatch, {"operation": "send_email", "score": 0.99,
                                      "args": {"to": "stranger@example.test"}})
    monkeypatch.setattr(lh, "candidate_provider", lambda text, tool, arg: ["sam@example.test"])
    r = lr.route("can you let the person from the project know about the delay", head_mode="on", log=False,
                 budget_ms=5000)
    assert r.decision == "defer" and r.chosen == {}
