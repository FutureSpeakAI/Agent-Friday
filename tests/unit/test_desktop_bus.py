"""The command channel to Friday's own desktop pages (services/desktop_bus).

What matters is that a tool which opens something on screen reports what the
page confirmed rather than what was sent: no page means an honest "nothing to
show it in", a silent page means "sent but not confirmed", and a command goes
to the desktop the user is looking at, never to a tab or a stale page.
"""
import threading
import time

import pytest

from agent_friday.services import desktop_bus as bus


@pytest.fixture(autouse=True)
def _clean():
    bus.reset()
    yield
    bus.reset()


def _page(cid, kind="desktop", focused=False, visible=True, connect=True, **state):
    q = bus.subscribe(cid, kind) if connect else None
    bus.report_state(cid, dict(kind=kind, focused=focused, visible=visible, **state))
    return q


def _answer_next(q, payload, delay=0.05):
    """Play the page: take the next command off its queue and acknowledge it."""
    got = {}

    def run():
        cmd = q.get(timeout=5)
        got["cmd"] = cmd
        time.sleep(delay)
        bus.ack(cmd["id"], payload)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return got, th


def test_no_page_is_an_honest_refusal():
    r = bus.send([{"type": "navigate", "workspace": "news"}], timeout=0.05)
    assert r == {"delivered": False,
                 "reason": "no Friday desktop page is open to show it in"}


def test_a_page_that_only_reports_is_not_sent_commands():
    _page("tab1", kind="tab", connect=False, focused=True)
    assert bus.pick_client() is None
    assert bus.send([{"type": "navigate", "workspace": "news"}], timeout=0.05)["delivered"] is False


def test_the_command_reaches_the_page_and_its_answer_comes_back():
    q = _page("desk1")
    got, th = _answer_next(q, {"opened": True, "matched": True, "label": "News"})
    r = bus.send([{"type": "navigate", "workspace": "news", "tab": "feed"}],
                 verify={"workspace": "news", "key": "tab", "value": "feed"}, timeout=3)
    th.join(2)
    assert r["delivered"] and r["acked"]
    assert r["ack"] == {"opened": True, "matched": True, "label": "News"}
    assert got["cmd"]["type"] == "command"
    assert got["cmd"]["actions"] == [{"type": "navigate", "workspace": "news", "tab": "feed"}]
    assert got["cmd"]["verify"] == {"workspace": "news", "key": "tab", "value": "feed"}


def test_a_silent_page_is_sent_but_not_confirmed():
    q = _page("desk1")
    t0 = time.time()
    r = bus.send([{"type": "navigate", "workspace": "news"}], timeout=0.2)
    assert r["delivered"] is True and r["acked"] is False and r["ack"] == {}
    assert time.time() - t0 < 1.0
    assert q.get_nowait()["type"] == "command"


def test_an_answer_to_an_unknown_command_is_refused():
    assert bus.ack("c0-0", {"opened": True}) is False


def test_the_focused_desktop_wins_over_a_newer_unfocused_one():
    _page("front", focused=True)
    time.sleep(0.01)
    _page("behind", focused=False)
    assert bus.pick_client()["id"] == "front"


def test_a_desktop_wins_over_a_connected_tab():
    _page("tab1", kind="tab", focused=True)
    _page("desk1", kind="desktop", focused=False)
    assert bus.pick_client()["id"] == "desk1"


def test_a_page_that_stopped_reporting_is_not_picked(monkeypatch):
    _page("old")
    later = time.time() + bus.STALE_AFTER_S + 5
    assert bus.pick_client(now=later) is None


def test_a_closed_page_is_forgotten():
    _page("desk1", focused=True, open=[{"workspace": "news", "label": "News"}])
    bus.report_state("desk1", {"kind": "desktop", "closed": True})
    assert bus.pick_client() is None
    assert bus.state() == {"known": False, "pages": 0}


def test_a_disconnected_stream_takes_no_commands():
    q = _page("desk1")
    bus.unsubscribe("desk1", q)
    assert bus.pick_client() is None


def test_a_reconnect_replaces_the_old_stream():
    old = _page("desk1")
    new = bus.subscribe("desk1", "desktop")
    bus.unsubscribe("desk1", old)          # the old stream's cleanup, late
    assert bus.pick_client()["queue"] is new


def test_the_manifest_is_kept_until_a_page_sends_another():
    want = bus.report_state("desk1", {"kind": "desktop"})
    assert want is True, "a page with no manifest on record must be asked for one"
    m = {"workspaces": {"news": {"label": "News", "sections": [{"id": "feed"}]}}}
    assert bus.report_state("desk1", {"kind": "desktop", "manifest": m}) is False
    assert bus.report_state("desk1", {"kind": "desktop"}) is False
    assert bus.manifest() == m
    assert "manifest" not in bus.state()


def test_the_newest_manifest_wins():
    bus.report_state("a", {"kind": "desktop", "manifest": {"workspaces": {"x": {}}}})
    time.sleep(0.01)
    bus.report_state("b", {"kind": "tab", "manifest": {"workspaces": {"y": {}}}})
    assert bus.manifest() == {"workspaces": {"y": {}}}


def test_state_describes_the_page_the_user_is_on():
    _page("desk1", focused=True,
          open=[{"workspace": "knowledge", "label": "Knowledge", "section": "pages"},
                {"workspace": "news", "label": "News", "section": "feed"}],
          focused_window={"workspace": "knowledge", "label": "Knowledge",
                          "detail": "view=pages, path=concepts/bootstrap.md"},
          chat={"open": True, "conversation": "main"})
    _page("tab1", kind="tab", connect=False,
          focused_window={"workspace": "messages", "label": "Messages"})
    _page("chat1", kind="chat", connect=False)
    st = bus.state()
    assert st["known"] and st["page"] == "desktop" and st["pages"] == 3
    assert st["commands"] is True
    assert [w["workspace"] for w in st["open"]] == ["knowledge", "news"]
    assert st["focused"]["detail"] == "view=pages, path=concepts/bootstrap.md"
    assert st["chat"] == {"open": True, "conversation": "main"}
    assert st["tabs"] == ["Messages"]
    assert st["chat_window"] is True


def test_state_with_only_a_tab_says_nothing_can_be_opened():
    _page("tab1", kind="tab", connect=False, focused=True,
          focused_window={"workspace": "messages", "label": "Messages"})
    st = bus.state()
    assert st["page"] == "tab" and st["commands"] is False
