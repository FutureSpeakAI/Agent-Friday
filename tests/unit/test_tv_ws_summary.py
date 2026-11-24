"""A live line on each workspace row (UI-CRITIC Q6, Stephen's answer; contract draft 3, section 4 addition).

    GET /tv/v1/workspaces?live=1   each row may carry live: {text, as_of}

Rules held here: the line is written by the PC from the same adapter rows the workspace's own list serves (so it is
behind the same read right and the same sensitivity); it is asked for, never sent by default (a home-screen widget
that reads the plain list never picks one up); it is one plain line, at most 80 characters, and a row holding a URL
never becomes one; a workspace with nothing to say, or whose section cannot be read, has no `live` and the list still
answers; the line is dated with when it was true and is not recomputed on every read. Synthetic data only.
"""
import pytest

from agent_friday.tv_gateway import ws_calendar, ws_knowledge, ws_news, ws_summary
from tests.unit.test_tv_pc_approve_decide import Enrolled, _clean, client
from tests.unit.test_tv_pc_read import get, paired

_FIXTURES = (_clean, client)    # pytest fixtures, imported by name so the tests below can ask for them

NEWS = [{"ref": "news:2", "title": "Kite festival moves to Saturday", "when": "2026-11-24T09:00:00Z"},
        {"ref": "news:1", "title": "Older story", "when": "2026-11-23T09:00:00Z"}]
PAGES = [{"ref": "page:a/one.md", "title": "one", "line": "a", "when": "2026-11-21T10:00:00Z"},
         {"ref": "page:b/two.md", "title": "two", "line": "b", "when": "2026-11-20T10:00:00Z"},
         {"ref": "page:b/three.md", "title": "three", "line": "b", "when": None}]
EVENTS = [{"ref": "event:past", "title": "Already happened", "line": "9:00", "when": "2000-01-01T09:00:00Z"},
          {"ref": "event:next", "title": "Dentist", "line": "Thu 4:30 pm", "when": "2999-01-01T16:30:00Z"},
          {"ref": "event:later", "title": "Later thing", "line": "Fri", "when": "2999-01-02T10:00:00Z"}]


@pytest.fixture(autouse=True)
def _rows(monkeypatch):
    ws_summary.forget()
    monkeypatch.setattr(ws_news, "pages", lambda sensitivity: [dict(r) for r in NEWS])
    monkeypatch.setattr(ws_knowledge, "pages", lambda sensitivity: [dict(r) for r in PAGES])
    monkeypatch.setattr(ws_calendar, "pages", lambda sensitivity: [dict(r) for r in EVENTS])
    yield
    ws_summary.forget()


def _live(dev, path="/tv/v1/workspaces?live=1"):
    r = dev.get(path)
    assert r.status_code == 200, r.get_json()
    return {w["id"]: w.get("live") for w in r.get_json()["workspaces"]}


def test_each_served_workspace_says_one_live_thing(client):
    live = _live(Enrolled(client))
    assert live["news"]["text"] == "Kite festival moves to Saturday", "the newest headline"
    assert live["calendar"]["text"] == "Dentist · Thu 4:30 pm", "the next event, never a past one"
    assert live["knowledge"]["text"] == "3 pages"
    for ws, line in live.items():
        if line is not None:
            assert set(line) == {"text", "as_of"} and line["as_of"].endswith("Z"), ws


def test_the_line_is_only_sent_when_asked(client):
    # the plain list is what a widget reads; it never carries workspace content
    dev = Enrolled(client)
    assert all(v is None for v in _live(dev, "/tv/v1/workspaces").values())
    assert all(v is None for v in _live(dev, "/tv/v1/workspaces?live=0").values())


def test_the_line_sits_behind_the_same_read_right(client):
    assert client.get("/tv/v1/workspaces?live=1").status_code in (401, 403)
    r = get(client, paired(right=False), "/tv/v1/workspaces?live=1")
    assert r.status_code == 403 and r.get_json()["code"] == "needs_right"


def test_a_workspace_with_nothing_to_say_has_no_line(client, monkeypatch):
    monkeypatch.setattr(ws_news, "pages", lambda sensitivity: [])
    monkeypatch.setattr(ws_calendar, "pages", lambda sensitivity: [dict(EVENTS[0])])
    live = _live(Enrolled(client))
    assert live["news"] is None and live["calendar"] is None, "no 'No data', no blurb in its place"
    assert live["knowledge"]["text"] == "3 pages"
    assert live.get("recipes") is None, "a workspace no adapter serves says nothing"


def test_a_section_that_cannot_be_read_drops_its_line_not_the_list(client, monkeypatch):
    def down(sensitivity):
        raise ws_news.Unavailable("feed")
    monkeypatch.setattr(ws_news, "pages", down)

    def broken(sensitivity):
        raise RuntimeError("anything else")
    monkeypatch.setattr(ws_calendar, "pages", broken)
    live = _live(Enrolled(client))
    assert live["news"] is None and live["calendar"] is None
    assert live["knowledge"]["text"] == "3 pages"


def test_the_line_is_plain_short_and_never_a_link(client, monkeypatch):
    monkeypatch.setattr(ws_news, "pages", lambda sensitivity: [
        {"ref": "news:9", "title": "See https://example.invalid/x now", "when": "2026-11-24T10:00:00Z"}])
    assert _live(Enrolled(client))["news"] is None, "a headline holding a URL is not shown"
    ws_summary.forget()
    long = "A " + "very " * 40 + "long headline\nwith\ta break‮"
    monkeypatch.setattr(ws_news, "pages", lambda sensitivity: [{"ref": "news:9", "title": long, "when": "x"}])
    text = _live(Enrolled(client))["news"]["text"]
    assert len(text) <= ws_summary.TEXT_MAX and text.endswith("…")
    assert not any(c in text for c in "\n\t‮")


def test_the_line_is_dated_and_kept_for_a_while(client, monkeypatch):
    calls = []

    def counted(sensitivity):
        calls.append(1)
        return [dict(r) for r in NEWS]
    monkeypatch.setattr(ws_news, "pages", counted)
    dev = Enrolled(client)
    first = _live(dev)["news"]
    second = _live(dev)["news"]
    assert first == second and len(calls) == 1, "read once per TTL, the same as_of until then"
    monkeypatch.setattr(ws_summary, "_clock", lambda: ws_summary._clock_real() + ws_summary.TTL_S + 1)
    _live(dev)
    assert len(calls) == 2
