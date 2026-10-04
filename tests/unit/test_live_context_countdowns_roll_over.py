"""The live context's countdowns name each date's next occurrence, every year."""
from datetime import date

from agent_friday.services import voice_engine as ve


def test_new_year_is_counted_down_from_the_year_it_is_in():
    assert ve._upcoming_countdowns(date(2031, 11, 3)) == ["- New Year: 59 days away (2032-01-01)"]


def test_the_summer_dates_come_round_again():
    lines = ve._upcoming_countdowns(date(2029, 5, 1))
    assert lines == ["- Summer Solstice: 51 days away (2029-06-21)",
                     "- Independence Day: 64 days away (2029-07-04)"]


def test_nothing_far_off_is_listed():
    assert ve._upcoming_countdowns(date(2030, 2, 1)) == []
