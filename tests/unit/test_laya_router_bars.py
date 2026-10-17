"""Per-action confidence bars in the router (Settings > Laya, Slice 1).

These tests import only the router, so on code without bars they fail on behaviour (the bar is ignored
and the guess asks), not on a missing module. The bars file is written by hand here, in the form
services/laya_settings writes it.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import laya_router as lr

RANKED = [("query_calendar", 0.50, 0.90), ("check_email", 0.30, 0.50), ("get_briefing", 0.10, 0.20)]
TEXT = "the thing on thursday"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_friday.core.FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(lr, "_score", lambda text: list(RANKED))
    monkeypatch.setattr(lr, "_sealed_rules", lambda *a, **k: None)
    from agent_friday.services import laya2_encoder
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    try:
        from agent_friday.services import laya_settings
        laya_settings._cache.update(checked=0.0, mtime=None, path=None, data=None)
        monkeypatch.setattr("agent_friday.services.laya.mutation_class",
                            lambda tool: "read" if tool in ("query_calendar", "check_email", "get_briefing") else "write")
    except ImportError:
        pass
    yield tmp_path
    lr._PROFILE.bars = None
    lr._PROFILE.pid = None


def _bars_file(home, bars, mode):
    p = home / "laya" / "action_bars.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mode": mode, "bars": bars}), encoding="utf-8")


def test_a_guess_below_the_shipped_score_asks_without_bars(home):
    r = lr._tier1(TEXT)
    assert (r.decision, r.tool) == ("ask", "query_calendar")


def test_a_lower_bar_for_a_read_lets_the_same_guess_act(home):
    lr._PROFILE.bars = {"query_calendar": 0.45}
    r = lr._tier1(TEXT)
    assert (r.decision, r.tool) == ("tool", "query_calendar")


def test_a_higher_bar_makes_a_confident_guess_ask(home):
    ranked = [("query_calendar", 0.70, 0.9), ("check_email", 0.20, 0.5), ("get_briefing", 0.1, 0.2)]
    assert lr._decide(ranked, TEXT).decision == "tool"
    r = lr._decide(ranked, TEXT, bars={"query_calendar": 0.80})
    assert (r.decision, r.tool) == ("ask", "query_calendar")


def test_learning_that_raised_the_act_score_still_binds_over_a_lower_bar(home):
    ranked = [("query_calendar", 0.60, 0.9), ("check_email", 0.20, 0.5), ("get_briefing", 0.1, 0.2)]
    r = lr._decide(ranked, TEXT, act_threshold=0.70, bars={"query_calendar": 0.45})
    assert r.decision == "ask"


def test_no_bar_moves_the_margin_rule(home):
    ranked = [("query_calendar", 0.50, 0.9), ("check_email", 0.45, 0.5), ("get_briefing", 0.1, 0.2)]
    assert lr._decide(ranked, TEXT, bars={"query_calendar": 0.40}).decision == "ask"


def test_no_bar_makes_an_outward_search_run_on_a_guess(home):
    ranked = [("search_web", 0.90, 0.9), ("check_email", 0.10, 0.5), ("get_briefing", 0.05, 0.2)]
    r = lr._decide(ranked, "who won last night", bars={"search_web": 0.40})
    assert r.decision == "ask"


def test_route_uses_the_owners_bars_only_when_they_are_set_to_act(home):
    _bars_file(home, {"query_calendar": 0.45}, "shadow")
    r = lr.route(TEXT, log=False, head_mode="off", budget_ms=2000)
    assert r.decision == "ask", "bars in shadow must change nothing on a live turn"
    _bars_file(home, {"query_calendar": 0.45}, "act")
    try:
        from agent_friday.services import laya_settings
        laya_settings._cache.update(checked=0.0, mtime=None, path=None, data=None)
    except ImportError:
        pass
    r = lr.route(TEXT, log=False, head_mode="off", budget_ms=2000)
    assert (r.decision, r.tool) == ("tool", "query_calendar")


def test_a_hand_edited_lower_bar_on_an_action_that_changes_something_never_reaches_the_router(home):
    ranked = [("set_chat_tray", 0.50, 0.9), ("check_email", 0.20, 0.5), ("get_briefing", 0.1, 0.2)]
    import agent_friday.services.laya_router as router
    router._score = lambda text: list(ranked)       # restored by monkeypatch of _score in the fixture's teardown
    _bars_file(home, {"set_chat_tray": 0.40}, "act")
    r = lr.route("hide the thing", log=False, head_mode="off", budget_ms=2000)
    assert r.decision != "tool"
