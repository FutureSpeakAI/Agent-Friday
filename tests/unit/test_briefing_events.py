"""The events section: two windows, calendar entries numbered so a briefing can cite them, and the Sunday case."""
from datetime import datetime

from agent_friday.services import briefing_events as be

SUNDAY = datetime(2026, 11, 22, 9, 0)


def row(title, day, url="https://calendar.example.test/e/" ):
    return {"title": title, "start_time": day + "T10:00:00", "html_link": url + title.lower().replace(" ", "-")}


def test_the_two_windows_are_the_next_seven_days_and_the_six_months_after():
    (a_from, a_to), (b_from, b_to) = be.windows(SUNDAY)
    assert (a_from.isoformat(), a_to.isoformat()) == ("2026-11-22", "2026-11-29")
    assert b_from.isoformat() == "2026-11-30" and b_to.isoformat() == "2027-05-24"


def test_a_sunday_run_has_both_windows():
    got = be.build(SUNDAY, [row("Harbour Lights Festival", "2026-12-12"), row("Quiz night", "2026-11-25")])
    assert SUNDAY.strftime("%A") == "Sunday"
    assert "WINDOW A: the next 7 days (2026-11-22 to 2026-11-29)" in got.text
    assert "WINDOW B: the next 6 months (2026-11-30 to 2027-05-24)" in got.text


def test_each_entry_is_numbered_in_date_order_and_put_in_the_window_its_date_falls_in():
    got = be.build(SUNDAY, [row("Harbour Lights Festival", "2026-12-12"), row("Quiz night", "2026-11-25"),
                            row("Past thing", "2026-11-01"), row("Far away", "2028-01-01")])
    assert [(c["id"], c["title"], c["window"]) for c in got.calendar] == [
        ("C1", "Quiz night", "A"), ("C2", "Harbour Lights Festival", "B")]
    a_part, b_part = got.text.split("WINDOW B")
    assert "[C1] 2026-11-25: Quiz night" in a_part and "[C2] 2026-12-12: Harbour Lights Festival" in b_part
    assert "Past thing" not in got.text and "Far away" not in got.text


def test_numbering_can_continue_after_entries_already_numbered():
    got = be.build(SUNDAY, [row("Quiz night", "2026-11-25")], first_index=5)
    assert got.calendar[0]["id"] == "C5"


def test_the_link_comes_from_the_entry_and_is_empty_when_the_entry_has_none():
    got = be.build(SUNDAY, [row("Quiz night", "2026-11-25"), {"title": "No link", "start_time": "2026-11-26T09:00:00"}])
    assert got.calendar[0]["url"].startswith("https://calendar.example.test/e/")
    assert got.calendar[1]["url"] == ""


def test_an_empty_window_says_so_instead_of_being_padded():
    got = be.build(SUNDAY, [])
    assert got.text.count("(no calendar entries in this window)") == 2 and got.calendar == []


def test_rows_that_are_not_entries_or_have_no_usable_date_are_ignored():
    got = be.build(SUNDAY, ["junk", None, {"title": "No date"}, {"title": "Bad", "start_time": "soon"}])
    assert got.calendar == []
