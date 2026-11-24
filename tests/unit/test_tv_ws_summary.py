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


# ── after the security critic (CHANGES): taint, a failed manifest, held and undeclared, text, time ─────────────────

def test_an_outside_headline_keeps_its_taint(client, monkeypatch):
    monkeypatch.setattr(ws_news, "pages", lambda sensitivity: [dict(r, taint="outside:news") for r in NEWS])
    live = _live(Enrolled(client))
    assert live["news"]["taint"] == "outside:news", "the words are an outlet's, and the phone is told so"
    assert "taint" not in live["knowledge"], "Friday's own count carries no taint"


def test_a_manifest_that_cannot_be_read_drops_the_line_not_the_list(client, monkeypatch):
    from agent_friday.tv_gateway import ws_content
    real = ws_content._serving

    def broken(ws):
        if ws == "news":
            raise OSError("manifest unreadable")
        return real(ws)
    monkeypatch.setattr(ws_content, "_serving", broken)
    live = _live(Enrolled(client))
    assert live["news"] is None and live["knowledge"]["text"] == "3 pages"


def test_a_section_the_device_block_does_not_serve_says_nothing(client, monkeypatch):
    from agent_friday.tv_gateway import ws_content
    real = ws_content._serving
    monkeypatch.setattr(ws_content, "_serving", lambda ws: None if ws == "calendar" else real(ws))
    assert _live(Enrolled(client))["calendar"] is None


def test_the_rows_are_read_at_the_blocks_own_sensitivity(client, monkeypatch):
    from agent_friday.tv_gateway import ws_content
    seen = []
    monkeypatch.setattr(ws_knowledge, "pages", lambda sensitivity: seen.append(sensitivity) or [dict(r) for r in PAGES])
    _live(Enrolled(client))
    assert seen == [ws_content._serving("knowledge")[1]["sensitivity"]]


def test_a_link_hidden_by_invisible_characters_is_still_a_link(client, monkeypatch):
    sneaky = "Read h​tt⁠ps://example.invalid/x"
    monkeypatch.setattr(ws_news, "pages", lambda sensitivity: [{"ref": "news:9", "title": sneaky, "when": "x"}])
    assert _live(Enrolled(client))["news"] is None


def test_the_next_event_reads_the_pcs_own_clock(client, monkeypatch):
    # calendar times are the PC's local time with no offset; a past one is never "next", whatever the zone
    from datetime import datetime, timedelta
    now = datetime.now()
    rows = [{"ref": "event:a", "title": "Earlier today", "line": "a", "when": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")},
            {"ref": "event:c", "title": "Odd", "line": "c", "when": "not a time"},
            {"ref": "event:b", "title": "Soon", "line": "b", "when": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")}]
    monkeypatch.setattr(ws_calendar, "pages", lambda sensitivity: [dict(r) for r in rows])
    assert _live(Enrolled(client))["calendar"]["text"] == "Soon · b"


def test_a_slow_section_never_holds_the_list(client, monkeypatch):
    import threading
    import time
    gate = threading.Event()

    def slow(sensitivity):
        gate.wait(5)
        return [dict(r) for r in NEWS]
    monkeypatch.setattr(ws_news, "pages", slow)
    monkeypatch.setattr(ws_summary, "BUDGET_S", 0.2)
    t0 = time.monotonic()
    live = _live(Enrolled(client))
    assert time.monotonic() - t0 < 2.0, "the list answers inside its budget"
    assert live["news"] is None and live["knowledge"]["text"] == "3 pages"
    gate.set()
    ws_summary.settle(2.0)
    assert _live(Enrolled(client))["news"]["text"] == NEWS[0]["title"], "the slow line lands for the next read"


def test_a_read_that_breaks_never_wedges_its_workspace(client, monkeypatch):
    real = ws_summary._compute

    def breaks(ws, now):
        raise RuntimeError("outside the try")
    monkeypatch.setattr(ws_summary, "_compute", breaks)
    assert _live(Enrolled(client))["news"] is None
    ws_summary.settle(2.0)
    monkeypatch.setattr(ws_summary, "_compute", real)
    ws_summary.forget()
    assert _live(Enrolled(client))["news"]["text"] == NEWS[0]["title"], "the next read tries again"


def test_a_line_is_read_on_a_thread_that_never_holds_up_shutdown(client, monkeypatch):
    import threading
    seen = []
    monkeypatch.setattr(ws_news, "pages",
                        lambda sensitivity: seen.append(threading.current_thread().daemon) or [dict(r) for r in NEWS])
    _live(Enrolled(client))
    assert seen == [True]


def test_an_event_time_with_an_offset_is_that_instant(client, monkeypatch):
    rows = [{"ref": "event:a", "title": "Past", "line": "a", "when": "2000-01-01T09:00:00+05:00"},
            {"ref": "event:b", "title": "Ahead", "line": "b", "when": "2999-01-01T09:00:00-08:00"}]
    monkeypatch.setattr(ws_calendar, "pages", lambda sensitivity: [dict(r) for r in rows])
    assert _live(Enrolled(client))["calendar"]["text"] == "Ahead · b"
