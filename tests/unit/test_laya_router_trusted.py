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
    monkeypatch.setattr(pa, "plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read the screen")))
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
