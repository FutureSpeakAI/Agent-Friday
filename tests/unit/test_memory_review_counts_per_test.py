"""Turns one test records do not make a review due in the next one.

The memory review counts turns per process (services/memory_review). Counted
across tests, the tenth turn could land in an unrelated test and start a
review in the background, against whatever model that test had patched. The
two tests below run in order: the first records nine turns, the second one.
"""
from __future__ import annotations

from agent_friday.services import memory_review as mr


def test_nine_turns_in_one_test_start_nothing():
    assert mr.every_turns() == 10
    for i in range(9):
        assert mr.note_turn("message %d" % i, "reply", "shared-session", start=False) is False


def test_the_next_test_counts_its_own_turns_from_zero():
    assert mr.note_turn("one more", "reply", "shared-session", start=False) is False
