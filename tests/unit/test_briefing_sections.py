"""Briefing sections: the section list, the Friday-to-Sunday family-outing rule, "Didn't run" lines instead of silence,
link checking, and the stored-with-its-time rule. Synthetic skills and calendar rows only."""
from datetime import datetime, timedelta

import pytest

from agent_friday import skill_registry
from agent_friday.services import briefing_sections as bsec
from agent_friday.services import trials

SUNDAY = datetime(2026, 11, 22, 9, 0)
MONDAY = datetime(2026, 11, 23, 9, 0)
FRIDAY = datetime(2026, 11, 20, 9, 0)
THURSDAY = datetime(2026, 11, 19, 9, 0)
ROWS = [{"title": "Quiz night", "start_time": "2026-11-25T19:00:00", "html_link": "https://calendar.example.test/e/quiz"},
        {"title": "Harbour Lights Festival", "start_time": "2026-12-12T12:00:00", "html_link": "https://calendar.example.test/e/lights"}]


class Skill:
    def __init__(self, name, body):
        self.name, self.body = name, body


@pytest.fixture
def world(monkeypatch, tmp_path):
    import agent_friday.paths as paths
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    state = {"skills": {"events-horizon": Skill("events-horizon", "Group window A in three."),
                        "family-outing": Skill("family-outing", "One outing for the family.")}, "queued": []}
    monkeypatch.setattr(skill_registry, "get_skill", lambda name, dirs=None: state["skills"].get(name))
    monkeypatch.setattr(trials, "candidates", lambda status=None: [{"subject": {"kind": "skill", "id": n}} for n in state["queued"]])
    return state


def model(text="- Quiz night is on Wednesday. [C1]\n- A made-up meetup."):
    box = {"prompts": []}

    def generate(prompt):
        box["prompts"].append(prompt)
        return text
    return generate, box


def test_the_default_list_is_events_daily_and_the_family_outing_friday_to_sunday():
    got = bsec.configured({})
    assert [(s.skill, s.days) for s in got] == [("events-horizon", "daily"), ("family-outing", "fri-sun")]


def test_the_family_outing_applies_friday_saturday_sunday_and_not_monday_to_thursday():
    outing = bsec.configured({})[1]
    days = [datetime(2026, 11, 16 + n, 9, 0) for n in range(7)]       # Monday 16th .. Sunday 22nd
    assert [bsec.applies(outing, d) for d in days] == [False, False, False, False, True, True, True]


def test_an_entry_that_is_not_well_formed_is_skipped_and_a_disabled_one_does_not_apply():
    got = bsec.configured({"briefing_sections": [{"skill": ""}, "junk", {"skill": "a", "enabled": False}, {"skill": "b"}]})
    assert [s.skill for s in got] == ["a", "b"] and not bsec.applies(got[0], SUNDAY) and bsec.applies(got[1], SUNDAY)


def test_a_sunday_run_has_the_events_section_with_both_windows_and_the_family_outing(world):
    generate, box = model()
    got = bsec.run_all(SUNDAY, ROWS, generate, specs=bsec.configured({}))
    assert [r.skill for r in got] == ["events-horizon", "family-outing"] and all(r.state == bsec.RAN for r in got)
    assert "WINDOW A: the next 7 days (2026-11-22 to 2026-11-29)" in box["prompts"][0]
    assert "WINDOW B: the next 6 months" in box["prompts"][0] and "Group window A in three." in box["prompts"][0]
    assert "One outing for the family." in box["prompts"][1]


def test_a_monday_run_has_the_events_section_but_no_family_outing(world):
    generate, _ = model()
    got = bsec.run_all(MONDAY, ROWS, generate, specs=bsec.configured({}))
    assert [r.skill for r in got] == ["events-horizon"]


def test_a_skill_awaiting_approval_says_so_in_one_line_and_the_model_is_not_called(world):
    world["skills"].pop("events-horizon")
    world["queued"].append("events-horizon")
    generate, box = model()
    got = bsec.run_all(MONDAY, ROWS, generate, specs=bsec.configured({}))
    assert box["prompts"] == []
    assert bsec.render(got) == "## Events\n_Didn't run: events-horizon awaiting approval._"


def test_a_missing_skill_says_it_is_not_installed(world):
    world["skills"].pop("events-horizon")
    got = bsec.run_all(MONDAY, ROWS, model()[0], specs=bsec.configured({}))
    assert "_Didn't run: events-horizon is not installed._" in bsec.render(got)


def test_a_model_error_or_an_empty_answer_says_why_it_did_not_run(world):
    def boom(prompt):
        raise RuntimeError("no provider")
    failed = bsec.run_all(MONDAY, ROWS, boom, specs=bsec.configured({}))[0]
    assert failed.state == bsec.DID_NOT_RUN and failed.reason == "failed (RuntimeError)"
    empty = bsec.run_all(MONDAY, ROWS, lambda p: "  ", specs=bsec.configured({}))[0]
    assert empty.reason == "returned nothing"


def test_an_item_with_no_calendar_id_is_dropped_and_counted_once_and_a_cited_one_gets_its_link(world):
    generate, _ = model()
    got = bsec.run_all(MONDAY, ROWS, generate, specs=bsec.configured({}))
    text = bsec.render(got)
    assert "https://calendar.example.test/e/quiz" in text and "made-up meetup" not in text
    assert text.count("dropped: no source") == 1 and "_1 item dropped: no source_" in text


def test_a_custom_heading_cannot_switch_the_link_check_off(world):
    specs = [bsec.Spec("events-horizon", "My Weekend Plans")]
    got = bsec.run_all(MONDAY, ROWS, model()[0], specs=specs)
    assert got[0].heading == "My Weekend Plans" and "made-up meetup" not in bsec.render(got)


def test_sections_made_today_are_reused_with_their_time_and_older_ones_are_regenerated(world):
    generate, box = model()
    made, _ = bsec.current(MONDAY, ROWS, generate, specs=bsec.configured({}))
    assert made == MONDAY and len(box["prompts"]) == 1
    again, results = bsec.current(MONDAY + timedelta(hours=2), ROWS, generate, specs=bsec.configured({}))
    assert again == MONDAY and len(box["prompts"]) == 1 and results[0].skill == "events-horizon"
    later, _ = bsec.current(MONDAY + timedelta(hours=7), ROWS, generate, specs=bsec.configured({}))
    assert later == MONDAY + timedelta(hours=7) and len(box["prompts"]) == 2


def test_yesterdays_sections_are_never_reused(world):
    generate, box = model()
    bsec.current(MONDAY, ROWS, generate, specs=bsec.configured({}))
    bsec.current(MONDAY + timedelta(days=1), ROWS, generate, specs=bsec.configured({}))
    assert len(box["prompts"]) == 2


def test_an_unreadable_store_means_regenerate_not_a_crash(world, tmp_path):
    (tmp_path / bsec.SECTIONS_FILE).write_text("{not json", encoding="utf-8")
    assert bsec.stored(MONDAY) is None


def test_the_stamp_says_when_it_was_made_and_age_is_in_plain_words():
    assert bsec.stamp(MONDAY) == "_Generated Monday 2026-11-23 09:00_"
    assert bsec.age_words(MONDAY, MONDAY + timedelta(minutes=30)) == "30 minutes ago"
    assert bsec.age_words(MONDAY, MONDAY + timedelta(hours=3)) == "3 hours ago"
