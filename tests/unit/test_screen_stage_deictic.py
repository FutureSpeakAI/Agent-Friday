"""What "this" and "these" mean, in a fixed order, and when to ask instead (See & Touch §3.5)."""
from __future__ import annotations

import time

from agent_friday.services import screen_stage as ss
from tests.screen_fixtures import newsletter_stage, ref


def _stage(selected=(), cursor=None, open_=None, focus=None):
    st = ss.bound_stage(newsletter_stage(selected=selected))
    st["cursor"] = cursor
    st["open"], st["focus"] = open_, focus
    return st


def test_the_ticked_rows_come_first():
    out = ss.resolve_deictic(_stage(selected=[1, 2, 3], open_=ref(5)), "these")
    assert out["refs"] == [ref(1), ref(2), ref(3)] and out["rule"] == "selection" and not out["ask"]


def test_the_hand_cursor_is_this_when_nothing_is_ticked():
    cur = {"ref": ref(4), "state": "locked", "age_s": 1.0}
    out = ss.resolve_deictic(_stage(cursor=cur, open_=ref(5)), "this")
    assert out["refs"] == [ref(4)] and out["rule"] == "cursor"


def test_the_cursor_rule_expires_after_three_seconds():
    cur = {"ref": ref(4), "state": "locked", "age_s": ss.CURSOR_S + 0.5}
    out = ss.resolve_deictic(_stage(cursor=cur, open_=ref(5)), "this")
    assert out["refs"] == [ref(5)] and out["rule"] == "open"


def test_open_then_focus_then_the_last_pointed_set():
    assert ss.resolve_deictic(_stage(open_=ref(5), focus=ref(6)), "this")["rule"] == "open"
    assert ss.resolve_deictic(_stage(focus=ref(6)), "this")["rule"] == "focus"
    pointed = {"refs": [ref(2), ref(3)], "at": time.time() - 30}
    out = ss.resolve_deictic(_stage(), "these", pointed=pointed)
    assert out["refs"] == [ref(2), ref(3)] and out["rule"] == "pointed"
    old = {"refs": [ref(2)], "at": time.time() - ss.POINTED_S - 5}
    assert ss.resolve_deictic(_stage(), "these", pointed=old)["ask"]


def test_a_selection_and_a_hand_on_another_row_is_a_question_not_a_guess():
    cur = {"ref": ref(6), "state": "pinched", "age_s": 0.5}
    out = ss.resolve_deictic(_stage(selected=[1, 2, 3], cursor=cur), "these")
    assert out["refs"] == [] and "3 ticked" in out["ask"] and "hand" in out["ask"]


def test_a_hand_on_a_ticked_row_is_no_conflict():
    cur = {"ref": ref(2), "state": "locked", "age_s": 0.5}
    out = ss.resolve_deictic(_stage(selected=[1, 2], cursor=cur), "these")
    assert out["rule"] == "selection" and not out["ask"]


def test_this_over_a_multi_row_selection_says_the_count_back():
    out = ss.resolve_deictic(_stage(selected=[1, 2, 3]), "this")
    assert out["say_count"] is True and out["count"] == 3
    assert ss.resolve_deictic(_stage(selected=[1]), "this")["say_count"] is False


def test_nothing_to_go_on_asks():
    assert ss.resolve_deictic(_stage(), "this")["ask"]
    assert ss.resolve_deictic(None, "this")["ask"]
