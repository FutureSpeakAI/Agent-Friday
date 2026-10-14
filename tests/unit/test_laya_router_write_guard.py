"""Router mutation guard: a write intent anywhere in the turn is a defer.

Written from the failure shape, not from any sealed set: a compound that ends
in a change ("look it up and put it on the calendar") must never run its read
half alone, and a message verb at the front ("email Sam that ...") is a send.
The negative controls pin that ordinary reads, idioms and fragments stay put.
Tier 0 only: no model, no encoder.
"""
import pytest

from agent_friday.services import laya_router as lr


def r0(text):
    return lr._tier0(" ".join(text.split()))


@pytest.mark.parametrize("text", [
    "look up the dentist's hours and put them on my calendar",
    "check my email and then reply to the landlord",
    "find the lease in my files and send it to the agent",
    "what's on my calendar tomorrow, then move the 9 o'clock to noon",
    "search the web for a good ramen place and book a table",
    "read my unread mail and archive the newsletters",
    "what's the news and also remind me to call the plumber",
    "find the invoice and forward it to accounts",
    "see what the weather is, then text my wife that I'm leaving early",
    "show me my schedule and add a dentist visit on Friday",
])
def test_a_compound_with_a_later_write_is_a_defer(text):
    r = r0(text)
    assert r is not None and r.decision == "defer", (text, r)
    assert not r.tool


@pytest.mark.parametrize("text", [
    "email my sister that the party moved to Sunday",
    "e-mail the team about the outage",
    "tell my brother I'll be there at six",
    "ping Dana when the build is done",
    "notify the group that lunch is cancelled",
    "let my mother know we landed",
    "label the receipts from last month as taxes",
    "please email my landlord the lease",
    "I need to email the school about Monday",
])
def test_a_message_verb_at_the_front_is_a_defer(text):
    r = r0(text)
    assert r is not None and r.decision == "defer", (text, r)


def test_a_compose_request_in_a_later_clause_hands_off_to_the_writer():
    assert r0("find the lease and email it to the agent").handoff == "compose_text"
    assert r0("email my sister that the party moved").handoff == "compose_text"


def test_the_route_entry_point_defers_a_compound_before_any_action_rule():
    # media_play would otherwise read "play jazz and delete my old mail" as play.
    r = lr.route("play some jazz and delete my old mail", budget_ms=50)
    assert r.decision == "defer"
    r = lr.route("look up the dentist's hours and put them on my calendar", budget_ms=50)
    assert r.decision == "defer"


@pytest.mark.parametrize("text,tool", [
    ("check my email and tell me what's urgent", None),
    ("what's on my calendar and tell me the time", "query_calendar"),
    ("did anyone email me about the invoice", "search_email"),
    ("search the web for salt and pepper recipes", "search_web"),
    ("what did I tell you about email", "search_past_conversations"),
])
def test_ordinary_reads_with_write_looking_words_still_run(text, tool):
    r = r0(text)
    if tool:
        assert r is not None and r.decision == "tool" and r.tool == tool, (text, r)
    else:
        assert r is None or r.decision != "defer", (text, r)


@pytest.mark.parametrize("text", ["move on", "make sense?", "cancel that", "set?",
                                  "show me my text messages", "email is exhausting"])
def test_idioms_and_fragments_are_not_writes(text):
    assert not lr._has_write_intent(text)


# ── how the guard sits with the chain's sealed rules (route generation merge) ──

def test_a_bare_yes_or_no_to_a_waiting_card_comes_before_the_guard_even_on_the_degraded_path():
    for word, decision in (("yes", "approve"), ("no", "decline")):
        for fn in (lambda t: lr.rules_route(t, pending_card="ap_1"),
                   lambda t: lr.route(t, pending_card="ap_1", log=False, budget_ms=50)):
            r = fn(word)
            assert (r.tool, r.args.get("decision")) == ("answer_card", decision), (word, r)


def test_the_degraded_path_runs_the_same_guard_and_defers_a_later_write():
    r = lr.rules_route("play some jazz and delete my old mail")
    assert r.decision == "defer" and not r.tool
    r = lr.rules_route("email my sister that the party moved")
    assert r.decision == "defer" and r.handoff == "compose_text"


def test_a_single_clause_action_rule_is_kept_whatever_the_guard_would_say():
    # "cancel the task" starts with a change verb, but it is Friday's own task rule: the chain ran it.
    for fn in (lr.rules_route, lambda t: lr.route(t, log=False, budget_ms=50)):
        r = fn("cancel the background task")
        assert r.decision == "tool" and r.tool == "task_control", r


def test_an_untrusted_caller_never_reaches_the_screen_rungs(monkeypatch):
    def boom(*a, **k):
        raise _ScreenRead("an untrusted turn read the owner's screen")
    monkeypatch.setattr(lr, "point_act_route", boom)
    monkeypatch.setattr(lr, "_resolver_route", boom)
    for text in ("archive these", "open the second one", "open the budget spreadsheet"):
        lr.route(text, log=False, budget_ms=50)                     # default trusted=False
        lr.rules_route(text)


def test_a_trusted_change_carries_its_rows_on_a_defer_and_never_a_tool(monkeypatch):
    seen = {}

    def fake(text, *, stage, pointed, quiet):
        seen["quiet"] = quiet
        return lr.Route("defer", args={"intent": "archive", "refs": ["email:t1"]}, layer="point_act")
    monkeypatch.setattr(lr, "point_act_route", fake)
    r = lr.route("archive these", trusted=True, quiet=True, log=False, budget_ms=50)
    assert r.decision == "defer" and not r.tool and r.args["refs"] == ["email:t1"] and seen["quiet"] is True
    # a rung that answers a change with a TOOL is ignored: the guard's defer stands
    monkeypatch.setattr(lr, "point_act_route", lambda *a, **k: lr.Route("tool", tool="screen_select", layer="point_act"))
    r = lr.route("archive these", trusted=True, log=False, budget_ms=50)
    assert r.decision == "defer" and not r.tool and "refs" not in r.args


class _ScreenRead(BaseException):
    """Not an Exception: the router swallows those, which would hide a red test."""
