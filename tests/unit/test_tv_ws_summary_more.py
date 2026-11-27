"""More workspace rows that say one live thing (UI-CRITIC round 3, R3.1: "only two of eleven rows say anything").

The rule is #1154's: a line is written only from the rows the workspace's own section already serves to this phone (same
read right, same sensitivity, same device block). Workspaces that serve no section to a phone (messages, family, health,
finance...) keep no line; this file does not reach past that gate. A line that is too long ends at a whole word
(R3.1.3: never a word cut in half). Synthetic data only.
"""
import time

import pytest

from agent_friday.tv_gateway import ws_code, ws_crew, ws_futurespeak, ws_news, ws_summary
from tests.unit.test_tv_pc_approve_decide import Enrolled, _clean, client
from tests.unit.test_tv_ws_summary import _live, _rows

_FIXTURES = (_clean, client, _rows)    # pytest fixtures, imported by name so the tests below can ask for them


def _code(*states):
    return [{"ref": "repo:r%d" % i, "title": "r%d" % i, "line": "main", "state": s, "when": None} for i, s in enumerate(states)]


def _crew(*states):
    return [{"ref": "agent:a%d" % i, "title": "Agent %d" % i, "line": "", "state": s, "when": None} for i, s in enumerate(states)]


# a fixed midday on the PC's own clock, so no test crosses midnight or a clock change while it runs
NOON = time.mktime((2026, 6, 17, 12, 0, 0, 0, 0, -1))


def _stamp(days_ago: int) -> str:
    """Midday on the PC's calendar, days_ago calendar days before NOON (not days_ago * 24 h: a clock change sits between)."""
    d = time.localtime(NOON)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.mktime((d.tm_year, d.tm_mon, d.tm_mday - days_ago, 12, 0, 0, 0, 0, -1))))


@pytest.fixture(autouse=True)
def _more(monkeypatch):
    monkeypatch.setattr(ws_summary, "_clock", lambda: NOON)
    monkeypatch.setattr(ws_code, "pages", lambda sensitivity: _code("clean", "changed", "clean", "changed", "error"))
    monkeypatch.setattr(ws_crew, "pages", lambda sensitivity: _crew("active", "active", "suspended", "active"))
    monkeypatch.setattr(ws_futurespeak, "pages", lambda sensitivity: [
        {"ref": "project:Older", "title": "Older", "line": "", "state": None, "when": _stamp(9)},
        {"ref": "project:Kites", "title": "Kites", "line": "", "state": None, "when": _stamp(0)},
        {"ref": "project:Never", "title": "Never", "line": "", "state": None, "when": None}])


def test_code_says_how_many_repos_have_changes(client):
    assert ws_summary._repos(_code("clean", "changed", "clean", "changed", "error"))[0] == "5 repos · 2 with changes"
    assert ws_summary._repos(_code("clean", "clean"))[0] == "2 repos, all clean"
    assert ws_summary._repos(_code("changed"))[0] == "1 repo · 1 with changes"
    assert ws_summary._repos(_code("clean", "error"))[0] == "2 repos · 1 unreadable", "a repo that can't be read is never called clean"
    assert ws_summary._repos([]) is None


def test_crew_says_who_is_active(client):
    assert ws_summary._agents(_crew("active", "active", "suspended", "active"))[0] == "3 active · 1 suspended"
    assert ws_summary._agents(_crew("active"))[0] == "1 active"
    assert ws_summary._agents(_crew(None, None))[0] == "2 agents"
    assert ws_summary._agents([]) is None


def test_futurespeak_names_the_last_project_changed(client):
    say = ws_summary._last_change
    assert say([{"title": "Older", "when": _stamp(9)}, {"title": "Kites", "when": _stamp(0)}])[0] == "Kites · changed today"
    assert say([{"title": "Kites", "when": _stamp(1)}])[0] == "Kites · changed yesterday"
    nine = time.localtime(time.mktime(time.localtime(NOON)[:2] + (time.localtime(NOON).tm_mday - 9, 12, 0, 0, 0, 0, -1)))
    assert say([{"title": "Older", "when": _stamp(9)}])[0] == "Older · changed %d %s" % (nine.tm_mday, time.strftime("%b", nine))
    assert say([{"title": "Never", "when": None}]) is None, "no date, nothing to say"


def test_a_project_date_that_cannot_be_right_never_names_the_project(client):
    say = ws_summary._last_change
    ahead = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOON + 3 * 86400))
    assert say([{"title": "Kites", "when": _stamp(1)}, {"title": "Forged", "when": ahead}])[0] == "Kites · changed yesterday", "a future date wins nothing"
    assert say([{"title": "Kites", "when": _stamp(1)}, {"title": "Far", "when": "9999-12-31T00:00:00+00:00"}])[0] == "Kites · changed yesterday"
    year_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOON - 400 * 86400))
    then = time.localtime(NOON - 400 * 86400)
    assert say([{"title": "Old", "when": year_ago}])[0] == "Old · changed %d %s %d" % (then.tm_mday, time.strftime("%b", then), then.tm_year)


def test_a_long_project_name_gives_way_before_its_day(client):
    long = "Kites " + "and more kites " * 8
    text = _plain_line(ws_summary._last_change([{"title": long, "when": _stamp(0)}])[0])
    assert len(text) <= ws_summary.TEXT_MAX and text.endswith("· changed today"), text


def _plain_line(raw):
    return ws_summary._plain(raw)


def test_the_new_lines_reach_the_list_through_the_same_gate(client):
    live = _live(Enrolled(client))
    for ws, text in (("code", "5 repos · 2 with changes · 1 unreadable"), ("crew", "3 active · 1 suspended"), ("futurespeak", "Kites · changed today")):
        assert live[ws] is not None and live[ws]["text"] == text, ws


def test_workspaces_with_no_section_for_the_phone_still_say_nothing(client):
    from agent_friday.tv_gateway import ws_content
    live = _live(Enrolled(client))
    for ws in ("messages", "family", "health", "finance", "career", "sites"):
        assert ws_content.serving(ws) is None, "%s serves the phone no section, so there is nothing to make a line from" % ws
        assert ws not in ws_summary.SAYS and live.get(ws) is None, ws


def test_a_line_too_long_ends_at_a_whole_word(client, monkeypatch):
    words = "A family saved this old riverside home and its history runs much deeper than anyone in the street knew"
    monkeypatch.setattr(ws_news, "pages", lambda sensitivity: [{"ref": "news:9", "title": words, "when": "x"}])
    text = _live(Enrolled(client))["news"]["text"]
    assert len(text) <= ws_summary.TEXT_MAX and text.endswith("…")
    kept = text[:-1]
    assert words.startswith(kept) and words[len(kept)] == " ", "cut between words, never inside one"
    one = "x" * 200
    assert ws_summary._plain(one).endswith("…") and len(ws_summary._plain(one)) == ws_summary.TEXT_MAX, "one long word still fits"
    short = ws_summary._plain("a " + "y" * 200)
    assert len(short) == ws_summary.TEXT_MAX, "a tiny first word before a very long one is not all that is kept"
    sep = ws_summary._plain("x" * 70 + " · " + "z" * 30)
    assert not sep.rstrip("…").rstrip().endswith("·"), "never ends on the separator"
