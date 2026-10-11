"""The trusted rungs (point-and-act, the item resolver) never change a decision Friday's own rules make.

The owner's own voice and the verified local page route with ``trusted=True``. Point-and-act reads the
screen, so it runs only for words that point AT the screen ("these", "the second one", "the ones from X").
A description to look up ("the email from Sarah", "my unread emails") keeps the read rule it always had,
with or without a list on screen. Tier 0 only: no model, no encoder.
"""
import time

import pytest

from agent_friday.services import laya_router as lr
from agent_friday.services import point_act as pa
from agent_friday.services import screen_stage as ss

NOW = time.time()


class ScreenRead(BaseException):
    """Not an Exception: a rung that swallows `except Exception` cannot hide it from the test."""


def _stage():
    return ss.bound_stage({"workspace": "messages", "rev": 1, "items": [
        {"ref": "mail:a@x:t1", "n": 1, "title": "Invoice from Sarah", "facets": {"from": "Sarah"}},
        {"ref": "mail:a@x:t2", "n": 2, "title": "Re: the invoice", "facets": {"from": "Mara"}},
        {"ref": "mail:a@x:t3", "n": 3, "title": "News about Ukraine", "facets": {"from": "News"}}]}, now=NOW)


def _decision(r):
    return (r.decision, r.tool, r.args, r.layer, r.handoff)


READS = [
    ("show me the email from Sarah", "search_email"),
    ("pull up the email about the invoice", "search_email"),
    ("show me my unread emails", "check_email"),
]


@pytest.mark.parametrize("text,tool", READS)
def test_a_read_phrase_decides_the_same_on_the_trusted_path_with_a_list_on_screen(text, tool, monkeypatch):
    base = lr.route(text, log=False, budget_ms=50, trusted=False)
    assert base.tool == tool, (text, base)
    monkeypatch.setattr(pa, "plan", lambda *a, **k: (_ for _ in ()).throw(ScreenRead("read the screen")))
    got = lr.route(text, log=False, budget_ms=50, trusted=True, stage=_stage(), pointed=None)
    assert _decision(got) == _decision(base)


@pytest.mark.parametrize("text", ["show me the email from Sarah", "pull up the email about the invoice",
                                  "show me my unread emails", "show me the news about Ukraine",
                                  "pick up where we left off"])
def test_a_description_is_not_referential(text):
    assert not pa.is_referential(pa.parse(text))


@pytest.mark.parametrize("text", ["open the second one", "select these", "archive these", "show me the first three",
                                  "star the ones from Mara", "all of them", "tick that"])
def test_words_that_point_at_the_screen_are_referential(text):
    assert pa.is_referential(pa.parse(text)), text


def test_a_pointing_phrase_still_reaches_the_screen_on_the_trusted_path():
    r = lr.route("open the second one", log=False, budget_ms=50, trusted=True, stage=_stage(), pointed=None)
    assert (r.decision, r.tool, r.layer) == ("tool", "navigate_to", "point_act")
    assert r.args.get("id") == "t2"


def test_an_untrusted_turn_never_sees_the_stage():
    r = lr.route("open the second one", log=False, budget_ms=50, trusted=False, stage=_stage(), pointed=None)
    assert r.layer != "point_act"


# ── a change on Laya-resolved rows ────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["star all of them", "label all of them as taxes", "mark all of them as read", "mark all of them as unread",
                                  "unstar all of them"])
def test_a_change_that_runs_without_a_card_carries_no_rows(text):
    r = lr.route(text, log=False, budget_ms=50, trusted=True, stage=_stage(), pointed=None)
    assert r.decision == "defer" and not r.tool
    assert "refs" not in r.args and "intent" not in r.args


@pytest.mark.parametrize("text", ["archive all of them", "trash all of them", "move all of them to travel"])
def test_a_change_that_always_raises_a_card_carries_its_rows(text):
    r = lr.route(text, log=False, budget_ms=50, trusted=True, stage=_stage(), pointed=None)
    assert r.decision == "defer" and not r.tool
    assert r.args.get("refs") and r.args["intent"] in pa.CARDED_INTENTS


def test_what_a_model_pointed_at_is_never_what_these_means_for_a_change(monkeypatch):
    seen = {}
    real = pa.plan

    def spy(text, **kw):
        seen.update(kw)
        return real(text, **kw)
    monkeypatch.setattr(pa, "plan", spy)
    lr.route("archive all of them", log=False, budget_ms=50, trusted=True, stage=_stage(), pointed={"refs": ["mail:a@x:t3"], "at": NOW})
    assert seen["pointed"] is None


# ── what the deeper mind is handed (routes/voice._deferred_request) ───────────────

def _defer(intent):
    return lr.Route("defer", args={"intent": intent, "refs": ["mail:owner@example.test:t1"], "workspace": "messages",
                                   "count": 1}, layer="point_act")


@pytest.mark.parametrize("intent", ["star", "unstar", "label", "mark_read", "mark_unread", "mark"])
def test_rows_for_a_change_that_runs_without_a_card_never_go_to_the_deeper_mind(intent):
    import agent_friday.routes.voice as rv
    assert rv._deferred_request("do it to these", _defer(intent), local=True) == "do it to these"


def test_rows_for_a_carded_change_go_only_to_a_local_deeper_mind():
    import agent_friday.routes.voice as rv
    assert "mail:owner@example.test:t1" in rv._deferred_request("archive these", _defer("archive"), local=True)
    assert rv._deferred_request("archive these", _defer("archive"), local=False) == "archive these"


# ── the trusted rungs are bounded and never queue ─────────────────────────────────────

def test_a_rung_over_budget_answers_none_and_the_next_turn_skips_it_instead_of_queueing():
    import threading
    go, calls = threading.Event(), []

    def slow(text):
        calls.append(text)
        go.wait(5)
        return "late"
    t0 = time.perf_counter()
    assert lr._bounded("t_slow", slow, "a", 50) is None
    assert time.perf_counter() - t0 < 1.0
    assert lr._bounded("t_slow", slow, "b", 50) is None          # still busy: skipped at once, not run
    assert calls == ["a"]
    go.set()
    end = time.time() + 3
    while ("voice", "t_slow") in lr._rung_busy and time.time() < end:
        time.sleep(0.01)
    assert lr._bounded("t_slow", lambda t: t.upper(), "c", 500) == "C"


def test_a_hanging_stage_read_never_holds_the_turn_and_the_sealed_decision_stands(monkeypatch):
    import threading
    go = threading.Event()
    monkeypatch.setattr(lr, "POINT_BUDGET_MS", 50)
    monkeypatch.setattr(lr, "point_act_route", lambda *a, **k: (go.wait(5), None)[1])
    base = lr.route("check my email", log=False, budget_ms=50)
    t0 = time.perf_counter()
    got = lr.route("check my email", log=False, budget_ms=50, trusted=True)
    took = time.perf_counter() - t0
    go.set()
    assert took < 1.5 and _decision(got) == _decision(base)


def test_a_hanging_resolver_never_holds_the_turn(monkeypatch):
    import threading
    from agent_friday.services import laya_resolver
    go = threading.Event()
    monkeypatch.setattr(lr, "RESOLVER_BUDGET_MS", 50)
    monkeypatch.setattr(laya_resolver, "resolve", lambda text: (go.wait(5), None)[1])
    t0 = time.perf_counter()
    lr.route("open the budget spreadsheet", log=False, budget_ms=50, trusted=True, stage=None, pointed=None)
    assert time.perf_counter() - t0 < 1.5
    go.set()


# ── point-and-act and the router agree on what a change is ───────────────────────────

CHANGE_WORDS = ["archive", "star", "unstar", "label", "tag", "categorize", "move", "put", "mark", "trash", "delete",
                "remove", "bin", "junk", "spam", "favorite", "favourite", "restore", "undelete"]


@pytest.mark.parametrize("word", CHANGE_WORDS)
def test_every_change_verb_point_and_act_parses_is_a_write_intent_to_the_router(word):
    text = "%s these" % word
    p = pa.parse(text)
    assert p is not None and p.verb in pa.WRITE_VERBS, (text, p)
    assert lr._has_write_intent(text), text


def test_file_is_not_a_change_verb_and_bare_nouns_are_not_writes():
    p = pa.parse("file a bug report")
    assert p is None or p.verb not in pa.WRITE_VERBS
    for text in ("star wars is on tonight", "spam folder is full", "bin collection is tomorrow"):
        assert not lr._has_write_intent(text), text


def test_marking_as_spam_is_a_spam_change():
    p = pa.parse("mark the second one as spam")
    assert p.verb == "mark" and pa._intent(p.verb, p.dest) == "spam"
    assert "spam" in pa.CARDED_INTENTS


def test_a_resolver_command_for_a_tool_the_router_does_not_hold_is_no_route(monkeypatch):
    from agent_friday.services import laya_resolver
    res = laya_resolver.Resolution("command", "Opening.", kind="doc",
                                   command={"tool": "library_show", "input": {"id": "d1"}})
    monkeypatch.setattr(laya_resolver, "resolve", lambda text: res)
    r = lr.route("open the lease document", log=False, budget_ms=50, trusted=True, stage=None, pointed=None)
    assert r.layer != "resolver" and r.handoff != "compose_text"


@pytest.mark.parametrize("text", ["select the five from Mara", "show me the two from Mara", "tick the three unread"])
def test_a_count_of_rows_is_referential(text):
    assert pa.is_referential(pa.parse(text)), text


# ── a pointing word is a pointing word only as the HEAD of the object ─────────────────

DESCRIPTIONS = [
    ("pull up the email about this week's invoice", "search_email"),
    ("show me the email from Sarah about that meeting", "search_email"),
    ("show me all the emails", None),
    ("show me the emails from this morning", None),
    ("show me the email about that meeting", None),
    ("pull up the invoice from this week", None),
    ("show me all the news about Ukraine", None),
]


@pytest.mark.parametrize("text,tool", DESCRIPTIONS)
def test_a_pointing_word_inside_a_description_does_not_point(text, tool, monkeypatch):
    assert not pa.is_referential(pa.parse(text)), text
    base = lr.route(text, log=False, budget_ms=50, trusted=False)
    if tool:
        assert base.tool == tool, (text, base)
    monkeypatch.setattr(pa, "plan", lambda *a, **k: (_ for _ in ()).throw(ScreenRead("read the screen")))
    got = lr.route(text, log=False, budget_ms=50, trusted=True, stage=_stage(), pointed=None)
    assert _decision(got) == _decision(base)


@pytest.mark.parametrize("text", [
    "open these", "open this one", "open that email", "select them", "tick the first one", "open the first",
    "star the ones from Mara", "select the unread ones", "tick three of them", "all of them", "select everything",
    "tick all of the ones on screen", "select all of these", "open the one from Dana", "show me the two from Mara",
    "open those", "select those emails"])
def test_a_pointing_word_as_the_head_of_the_object_points(text):
    assert pa.is_referential(pa.parse(text)), text


@pytest.mark.parametrize("text", ["select all the emails", "show me all the emails", "tick all the stories"])
def test_all_the_x_alone_is_not_pointing(text):
    assert not pa.is_referential(pa.parse(text)), text


def test_a_hung_rung_is_tried_again_after_the_cooldown_and_voice_and_chat_do_not_share_it(monkeypatch):
    import threading
    go = threading.Event()
    monkeypatch.setattr(lr, "RUNG_COOLDOWN_S", 0.2)
    calls = []

    def hang(text):
        calls.append(text)
        go.wait(5)
        return "late"
    assert lr._bounded("t_cool", hang, "a", 30, surface="voice") is None
    assert lr._bounded("t_cool", hang, "b", 30, surface="voice") is None        # still within the cool-down
    assert calls == ["a"]
    assert lr._bounded("t_cool", lambda t: "chat:" + t, "c", 500, surface="chat") == "chat:c"   # its own state
    time.sleep(0.25)
    assert lr._bounded("t_cool", lambda t: "again:" + t, "d", 500, surface="voice") == "again:d"
    go.set()


def test_the_rung_counters_see_a_timeout_and_a_skip():
    import threading
    go = threading.Event()
    lr.rung_stats.update(timeout=0, skipped=0)
    lr._bounded("t_stat", lambda t: go.wait(5), "a", 30)
    lr._bounded("t_stat", lambda t: None, "b", 30)
    assert lr.rung_stats == {"timeout": 1, "skipped": 1}
    go.set()


def test_the_oracle_can_widen_the_rung_budgets_for_one_call_only(monkeypatch):
    import inspect
    assert "rung_budget_ms" in inspect.signature(lr.route).parameters
    assert lr.POINT_BUDGET_MS == 200 and lr.RESOLVER_BUDGET_MS == 300         # production budgets are not touched
    seen = []
    monkeypatch.setattr(lr, "_bounded", lambda name, fn, text, budget, surface="voice": seen.append((name, budget)))
    lr.route("open the second one", log=False, budget_ms=50, trusted=True, stage=_stage(), pointed=None, rung_budget_ms=9000)
    lr.route("open the second one", log=False, budget_ms=50, trusted=True, stage=_stage(), pointed=None)
    assert seen[0][1] == 9000 and seen[-1][1] in (200, 300)
