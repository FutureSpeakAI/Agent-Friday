"""Spoken filters become visible chips (screen_select op=filter): applied through each workspace's own
filter state, marked "by Friday", removable by the owner, and said back as the page confirmed them.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent, desktop_bus, screen_stage as ss
from tests.screen_fixtures import Page, newsletter_stage, report


@pytest.fixture(autouse=True)
def _clean():
    desktop_bus.reset()
    yield
    desktop_bus.reset()


def _filter(**kw):
    return agent._tool_screen_select(dict({"op": "filter"}, **kw))


def _page(monkeypatch, filters):
    return Page(monkeypatch, answer={"ok": True, "filters": filters})


def test_a_filter_is_sent_to_the_workspace_and_said_as_the_page_confirmed_it(monkeypatch):
    page = _page(monkeypatch, [{"key": "unread", "label": "Unread only", "by": "friday"}])
    out = _filter(workspace="messages", key="unread", value="1")
    assert out == "FILTER_OK filter set - now: Unread only (by Friday)", out
    assert page.sent[-1] == [{"type": "chips", "workspace": "messages", "set": [{"key": "unread", "value": "1"}], "remove": []}]


def test_an_empty_value_removes_the_chip(monkeypatch):
    page = _page(monkeypatch, [])
    out = _filter(workspace="messages", key="lane", value="")
    assert page.sent[-1][0]["remove"] == ["lane"] and page.sent[-1][0]["set"] == []
    assert out == "FILTER_OK removed lane - no filters on", out


def test_each_workspace_has_its_own_closed_set_of_filters(monkeypatch):
    page = _page(monkeypatch, [{"key": "sort", "label": "Newest", "by": "friday"}])
    assert _filter(workspace="news", key="sort", value="time").startswith("FILTER_OK")
    assert page.sent[-1][0]["workspace"] == "news" and page.sent[-1][0]["set"] == [{"key": "sort", "value": "time"}]
    assert _filter(workspace="news", key="sort", value="sideways").startswith("FILTER_FAIL")
    assert _filter(workspace="media", key="status", value="Draft").startswith("FILTER_OK")
    assert page.sent[-1][0]["set"] == [{"key": "status", "value": "draft"}]
    assert _filter(workspace="media", key="status", value="finished").startswith("FILTER_FAIL")
    assert _filter(workspace="library", key="folder", value="Contracts").startswith("FILTER_OK")
    assert page.sent[-1][0]["set"] == [{"key": "folder", "value": "Contracts"}], "free text keeps its case"
    assert _filter(workspace="messages", key="lane", value="finance").startswith("FILTER_OK")
    assert _filter(workspace="messages", key="lane", value="nonsense").startswith("FILTER_FAIL")


def test_a_key_the_workspace_has_no_filter_for_is_refused_before_anything_is_sent(monkeypatch):
    page = _page(monkeypatch, [])
    out = _filter(workspace="news", key="status", value="draft")
    assert out.startswith("FILTER_FAIL") and "category, sort" in out
    assert _filter(workspace="finance", key="x", value="y").startswith("FILTER_FAIL")
    assert page.sent == []


def test_a_page_that_did_not_confirm_is_a_failure(monkeypatch):
    Page(monkeypatch, answer={"ok": False, "reason": "News is not open"})
    assert _filter(workspace="news", key="sort", value="time") == "FILTER_FAIL: News is not open"
    monkeypatch.setattr(desktop_bus, "send", lambda *a, **k: {"delivered": False, "reason": "no Friday desktop page is open"})
    assert _filter(workspace="news", key="sort", value="time").startswith("FILTER_FAIL: no Friday desktop page")


def test_the_cloud_hears_that_a_filter_is_on_not_its_words(monkeypatch):
    _page(monkeypatch, [{"key": "q", "label": "Search: harbor legal invoice", "by": "friday"}])
    tok = agent._CURRENT_SURFACE.set("voice-live")
    try:
        out = _filter(workspace="messages", key="q", value="harbor legal invoice")
    finally:
        agent._CURRENT_SURFACE.reset(tok)
    assert out == "FILTER_OK I've set the q filter; 1 on.", out
    assert "harbor" not in out


def test_the_flat_voice_keys_work_like_the_nested_ones(monkeypatch):
    page = _page(monkeypatch, [{"key": "unread", "label": "Unread only", "by": "friday"}])
    assert agent._tool_screen_select({"op": "filter", "workspace": "messages", "key": "unread", "value": "1"}).startswith("FILTER_OK")
    assert page.sent[-1][0]["set"] == [{"key": "unread", "value": "1"}]


def test_the_stage_reports_who_set_each_filter_so_an_owner_removal_is_seen():
    st = newsletter_stage()
    st["filters"] = [{"key": "lane", "value": "finance", "label": "Finance", "by": "friday"},
                     {"key": "q", "value": "harbor", "label": "Search: harbor", "by": "owner"}]
    report(st)
    got = desktop_bus.stage("messages")["filters"]
    assert [(f["key"], f["by"]) for f in got] == [("lane", "friday"), ("q", "owner")]
    st["filters"] = [{"key": "q", "value": "harbor", "label": "Search: harbor", "by": "owner"}]     # the owner clicked x
    report(st)
    assert [f["key"] for f in desktop_bus.stage("messages")["filters"]] == ["q"]


def test_filter_requests_are_checked_by_one_pure_function():
    assert ss.filter_request("messages", "Lane", "Career") == {"ok": True, "key": "lane", "value": "career"}
    assert ss.filter_request("messages", "colour", "red")["ok"] is False
    assert ss.filter_request("nowhere", "a", "b")["ok"] is False
    assert ss.filter_request("media", "q", "x" * 200)["value"] == "x" * 80
