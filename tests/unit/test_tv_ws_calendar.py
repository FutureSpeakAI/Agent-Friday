"""The Calendar workspace's agenda, as the owner's phone reads it (PW-G2, adapter 2).

Stephen, 2026-11-17: "Where are the workspaces? I just see cards stating what Friday can do." The Calendar workspace
had a verb list and nothing to draw. These tests hold the agenda (tv_gateway/ws_calendar.py) over a stand-in for the
desktop's calendar routes:

* the next two weeks come in time order, each row saying when, where and what day;
* attendee addresses, conference links and URLs in a description never leave, only how many are invited;
* a calendar the PC could not read is a 503, never a free week;
* an event id that is not an id never reaches the desktop;
* the rev moves when an event moves.
"""
import json
from datetime import date, timedelta
from urllib.parse import unquote

import pytest

from agent_friday.tv_gateway import pc_read, ws_calendar
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean

TODAY = date.today()


def at(days, hh, mm=0):
    return "%sT%02d:%02d:00-05:00" % ((TODAY + timedelta(days=days)).isoformat(), hh, mm)


class Desk:
    def __init__(self):
        self.calls, self.down, self.errors = [], set(), []
        self.events = [
            {"id": "ev2", "title": "Lunch with <b>Jere</b>", "start_time": at(1, 12), "end_time": at(1, 13), "location": "Cafe Roma",
             "attendees": ["jere@example.org", "me@example.org"], "type": "meeting",
             "description": "Bring the plan. Join https://meet.example.org/abc-def now\n\n- agenda one\n- agenda two"},
            {"id": "ev1", "title": "Standup", "start_time": at(0, 9), "end_time": at(0, 9, 15), "location": "", "attendees": [],
             "description": ""},
            {"id": "ev3", "title": "Holiday", "start_time": (TODAY + timedelta(days=2)).isoformat(), "all_day": True,
             "attendees": [], "description": ""},
            {"id": "a@b.c-1", "title": "Odd id", "start_time": at(3, 8), "end_time": at(3, 9), "attendees": []},
            {"title": "No id", "start_time": at(3, 10)},
        ]

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if any(path.startswith(d) for d in self.down):
            return 500, b"{}", "application/json"
        if path == "/api/calendar/range":
            return 200, json.dumps({"status": "ok", "events": self.events, "errors": self.errors}).encode(), "application/json"
        if path.startswith("/api/calendar/event/"):
            ident = unquote(path.removeprefix("/api/calendar/event/"))
            found = next((e for e in self.events if e.get("id") == ident), None)
            if found is None:
                return 404, b'{"status":"not_found"}', "application/json"
            return 200, json.dumps({"status": "ok", "event": found}).encode(), "application/json"
        return 404, b"{}", "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


def agenda(client, query="", dev=None):
    r = get(client, dev or paired(), "/tv/v1/workspaces/calendar/list" + query)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_the_agenda_is_the_next_two_weeks_in_time_order_with_the_contracts_fields(client, desk):
    got = agenda(client)
    assert [r["ref"] for r in got["rows"]] == ["event:ev1", "event:ev2", "event:ev3", "event:a@b.c-1"]
    assert got["rows"][0] == {"ref": "event:ev1", "kind": "event", "title": "Standup", "line": "09:00-09:15", "state": None,
                              "when": at(0, 9), "thumb": None, "lives_on": "pc", "sensitivity": "personal", "taint": None}
    assert desk.calls[0] == ("/api/calendar/range", {"start": TODAY.isoformat(),
                                                    "end": (TODAY + timedelta(days=14)).isoformat()})


def test_a_row_says_all_day_the_place_and_the_kind_and_has_plain_words_only(client, desk):
    rows = {r["ref"]: r for r in agenda(client)["rows"]}
    assert rows["event:ev3"]["line"] == "All day" and rows["event:ev3"]["when"] == TODAY.isoformat().replace(
        TODAY.isoformat(), (TODAY + timedelta(days=2)).isoformat()) + "T00:00:00"
    assert rows["event:ev2"]["line"] == "12:00-13:00 · Cafe Roma" and rows["event:ev2"]["state"] == "meeting"
    assert rows["event:ev2"]["title"] == "Lunch with Jere"


def test_an_event_with_no_usable_id_is_left_out_not_sent_broken(client, desk):
    assert "event:" not in " ".join(r["ref"] for r in agenda(client)["rows"] if r["title"] == "No id")
    assert not [r for r in agenda(client)["rows"] if r["title"] == "No id"]


def test_the_day_facet_counts_and_narrows(client, desk):
    got = agenda(client)
    days = {v["v"]: v["n"] for v in got["facets"][0]["values"]}
    assert got["facets"][0]["name"] == "day" and sum(days.values()) == 4 and days[TODAY.isoformat()] == 1
    one = agenda(client, "?facet.day=" + (TODAY + timedelta(days=1)).isoformat())
    assert [r["ref"] for r in one["rows"]] == ["event:ev2"] and one["total"] == 1


def test_no_attendee_address_conference_link_or_url_leaves_anywhere(client, desk):
    dev = paired()
    texts = [get(client, dev, "/tv/v1/workspaces/calendar/list").get_data(as_text=True),
             get(client, dev, "/tv/v1/workspaces/calendar/search?q=lunch").get_data(as_text=True),
             get(client, dev, "/tv/v1/workspaces/calendar/item/event:ev2").get_data(as_text=True)]
    for text in texts:
        for leak in ("jere@example.org", "me@example.org", "meet.example.org", "https", "<b>"):
            assert leak not in text, leak


def test_an_item_has_when_where_how_many_are_invited_and_the_plain_description(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces/calendar/item/event:ev2").get_json()
    assert got["title"] == "Lunch with Jere" and got["kind"] == "event" and got["next"] is None
    assert got["meta"] == [{"k": "When", "v": "12:00-13:00"}, {"k": "Day", "v": (TODAY + timedelta(days=1)).isoformat()},
                           {"k": "Where", "v": "Cafe Roma"}, {"k": "Invited", "v": "2 people"}]
    assert [b["t"] for b in got["blocks"]] == ["para", "list"] and got["blocks"][0]["text"] == "Bring the plan. Join now"
    assert "calendar.meeting_prep" in got["verbs"] and got["sources"] == []


def test_an_event_id_with_an_at_sign_reaches_the_desktop_whole(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces/calendar/item/event:a@b.c-1")
    assert got.status_code == 200 and desk.calls[-1][0] == "/api/calendar/event/a%40b.c-1"


@pytest.mark.parametrize("ref", ["event:../x", "event:a/b", "event:", "page:x.md", "event:a b", "event:..", "event:" + "x" * 201])
def test_an_id_that_is_not_an_event_id_never_reaches_the_desktop(client, desk, ref):
    assert get(client, paired(), "/tv/v1/workspaces/calendar/item/" + ref).status_code == 404
    assert not [c for c in desk.calls if c[0].startswith("/api/calendar/event/")]


def test_an_event_that_is_not_there_is_404(client, desk):
    assert get(client, paired(), "/tv/v1/workspaces/calendar/item/event:nope").status_code == 404


def test_search_looks_over_sixty_two_days_by_title_and_place(client, desk):
    dev = paired()
    by_title = get(client, dev, "/tv/v1/workspaces/calendar/search?q=standup").get_json()
    by_place = get(client, dev, "/tv/v1/workspaces/calendar/search?q=roma").get_json()
    assert [r["ref"] for r in by_title["rows"]] == ["event:ev1"] and [r["ref"] for r in by_place["rows"]] == ["event:ev2"]
    assert desk.calls[0][1]["end"] == (TODAY + timedelta(days=62)).isoformat()


def test_a_calendar_that_could_not_be_read_is_a_503_never_a_free_week(client, desk):
    dev = paired()
    desk.errors, desk.events = ["google: unreachable"], []
    r = get(client, dev, "/tv/v1/workspaces/calendar/list")
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable"
    desk.down.add("/api/calendar/")
    assert get(client, dev, "/tv/v1/workspaces/calendar/list").status_code == 503
    assert get(client, dev, "/tv/v1/workspaces/calendar/item/event:ev1").status_code == 503


def test_some_calendars_failing_still_shows_the_events_that_were_read(client, desk):
    desk.errors = ["one calendar failed"]
    assert len(agenda(client)["rows"]) == 4


def test_the_rev_moves_when_an_event_moves_and_changes_says_so(client, desk):
    dev = paired()
    rev = agenda(client)["rev"]
    assert rev == agenda(client)["rev"]
    assert get(client, dev, "/tv/v1/workspaces/calendar/changes?since=" + rev).get_json()["changed"] is False
    desk.events[1]["start_time"] = at(0, 10)
    again = get(client, dev, "/tv/v1/workspaces/calendar/changes?since=" + rev).get_json()
    assert again["changed"] is True and again["rev"] != rev


def test_only_an_owner_phone_or_tablet_with_the_right_may_read_the_agenda(client, desk):
    for path in ("list", "search?q=a", "item/event:ev1", "changes"):
        assert get(client, paired("tv"), "/tv/v1/workspaces/calendar/" + path).status_code == 403, path
        assert get(client, paired(right=False), "/tv/v1/workspaces/calendar/" + path).status_code == 403, path


def test_the_workspace_detail_sends_the_calendar_block_and_where_its_buttons_sit(client, desk):
    from tests.unit.test_tv_gateway import auth, open_session
    got = client.get("/tv/v1/workspaces/calendar", headers=auth(open_session(client, paired()))).get_json()
    placed = {v["id"]: v["placed"] for v in got["verbs"]}
    assert got["device"]["sections"]["agenda"]["view"] == "timeline"
    assert placed["calendar.quick_add"] == ["new"] and placed["calendar.move_event"] == ["item"]
    assert placed["calendar.view"] == []
