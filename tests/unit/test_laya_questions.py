"""The question registry keeps the wordings that were measured to work.

tools/laya_question_eval.py scored candidate wordings against clear-cut cases
on this checkpoint. The voice questions are statements because as yes/no
choices they scored 6/11 and 10/15; `leaves_machine` stays out of the gate's
set because no wording beat 17/29. A change here should come with a new
measurement, and these tests are where that is noticed.
"""
from __future__ import annotations

from agent_friday.services import laya_questions as lq


def test_the_voice_questions_are_statements():
    assert lq.TOUCHES_PRIVATE["type"] == "noul"
    assert lq.DIRECT_COMMAND["type"] == "noul"
    assert lq.VOICE == ("touches_private", "direct_command")


def test_the_gate_shadow_does_not_ask_the_question_laya_cannot_answer():
    assert lq.GATE_SHADOW == ("severity", "changes_outside")
    assert "leaves_machine" in lq.QUESTIONS


def test_no_question_lands_in_the_distorted_bucket():
    for qid in lq.QUESTIONS:
        assert lq.bucket(qid) != "choice:11+", qid


def test_holds_reads_both_kinds_of_answer():
    assert lq.holds("direct_command", {"noul": 0.8}) is True
    assert lq.holds("direct_command", {"noul": 0.2}) is False
    assert lq.holds("severity", {"choice": "hard"}) is True
    assert lq.holds("severity", {"choice": "soft"}) is False
