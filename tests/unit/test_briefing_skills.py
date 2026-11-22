"""The briefing applies two skills and never falls back silently when one is not there. Synthetic skills only."""
import pytest

from agent_friday import skill_registry
from agent_friday.services import briefing_skills as bs
from agent_friday.services import trials


class Skill:
    def __init__(self, name, body):
        self.name, self.body = name, body


@pytest.fixture
def world(monkeypatch):
    state = {"skills": {}, "queued": [], "read_fails": False, "queue_fails": False}

    def get_skill(name, dirs=None):
        if state["read_fails"]:
            raise OSError("could not read")
        return state["skills"].get(name)

    def candidates(status=None):
        if state["queue_fails"]:
            raise OSError("queue unreadable")
        return [{"subject": {"kind": "skill", "id": n}} for n in state["queued"]]
    monkeypatch.setattr(skill_registry, "get_skill", get_skill)
    monkeypatch.setattr(trials, "candidates", candidates)
    return state


def test_an_installed_skill_hands_over_its_instructions_and_has_no_notice(world):
    world["skills"]["events-horizon"] = Skill("events-horizon", "Two windows. No link, no item.")
    got = bs.load("events-horizon")
    assert got.installed and got.state == bs.INSTALLED and got.text == "Two windows. No link, no item."
    assert got.notice == "" and "No link, no item." in bs.instructions(got, "## The skill")


def test_a_skill_saved_for_testing_but_not_approved_is_called_waiting_and_says_so(world):
    world["queued"] = ["events-horizon"]
    got = bs.load("events-horizon")
    assert got.state == bs.WAITING and not got.installed and got.text == ""
    assert "events-horizon" in got.notice and "queued for testing" in got.notice and "without it" in got.notice


def test_a_skill_that_is_nowhere_is_missing_and_says_so(world):
    got = bs.load("events-horizon")
    assert got.state == bs.MISSING and "is not installed" in got.notice


def test_a_skill_that_cannot_be_read_or_has_no_text_is_unreadable_and_says_so(world):
    world["read_fails"] = True
    assert bs.load("daily-briefing-format").state == bs.UNREADABLE
    world["read_fails"] = False
    world["skills"]["daily-briefing-format"] = Skill("daily-briefing-format", "   ")
    got = bs.load("daily-briefing-format")
    assert got.state == bs.UNREADABLE and "could not be read" in got.notice


def test_an_unreadable_queue_is_reported_as_missing_not_as_a_crash(world):
    world["queue_fails"] = True
    assert bs.load("events-horizon").state == bs.MISSING


def test_a_queued_skill_with_another_name_does_not_make_this_one_waiting(world):
    world["queued"] = ["something-else"]
    assert bs.load("events-horizon").state == bs.MISSING


def test_the_notices_cover_only_the_skills_that_are_not_installed(world):
    world["skills"]["daily-briefing-format"] = Skill("daily-briefing-format", "Format text.")
    world["queued"] = ["events-horizon"]
    lines = bs.notices([bs.load("daily-briefing-format"), bs.load("events-horizon")]).splitlines()
    assert len(lines) == 1 and "events-horizon" in lines[0]
    assert bs.notices([bs.load("daily-briefing-format")]) == ""
