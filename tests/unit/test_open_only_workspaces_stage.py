"""P7: the workspaces that were open-only now report a stage: Calendar events, Workflows, People, Career, Trust,
Sites, System approvals, the Chat Hub's conversations, and (counts and kinds only) Health, Finance and Family.

Each row carries data-fr-ref; static/workspace_stages.js gives the workspace its stage; the server keeps it to
the same contract as every list (bounded, allow-listed facets, memory only); a private workspace's rows reach
Friday as a kind and a position, never a title.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_friday.services import agent, approvals as ap, desktop_bus, screen_stage as ss
from agent_friday.services import desktop_targets as dt
from tests.screen_fixtures import Page, report

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "workspace_stages.js").read_text(encoding="utf-8")
#: window id -> (ref kind, a facet a row may carry)
MAP = dict(re.findall(r"^\s{4}([a-z]+): \{ prefix: '([a-z]+):'", JS, re.M))


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    desktop_bus.reset()
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    yield
    desktop_bus.reset()


def test_the_registrar_covers_every_open_only_workspace():
    assert set(MAP) == {"calendar", "chat", "workflows", "contacts", "career", "trust", "futurespeak", "system",
                        "health", "finance", "family"}, MAP


def test_every_registered_workspace_is_allow_listed_by_the_server():
    for ws, kind in MAP.items():
        assert ws in ss.STAGE_FACETS and ws in ss.NOUNS, ws
        assert kind in ss.ROW_KINDS, (ws, kind)
        assert ss.bound_stage({"workspace": ws, "items": [{"ref": kind + ":x", "n": 1}]})["items"][0]["ref"] == kind + ":x"


def test_every_workspace_row_is_marked_in_both_pages():
    for page in ("index.html", "ui_parts/app.html"):
        text = (ROOT / page).read_text(encoding="utf-8")
        for ws, kind in MAP.items():
            if ws == "chat" and page == "ui_parts/app.html":
                continue                                    # the Chat Hub sidebar is written in index.html only
            assert "fridayRow('%s'" % kind in text, (page, ws, kind)
        assert "function fridayRow(" in text and "function fridayWhen(" in text, page
    assert "workspace_stages.js" in (ROOT / "index.html").read_text(encoding="utf-8")
    assert "workspace_stages.js" in (ROOT / "ui_parts/styles_and_scene.html").read_text(encoding="utf-8")


def test_a_private_workspace_never_sends_a_title_or_an_unlisted_facet():
    raw = {"workspace": "health", "items": [
        {"ref": "health:med-0", "n": 1, "title": "Metformin 500 mg", "who": "Dr Rao",
         "facets": {"kind": "medication", "dose": "500 mg", "next": "2026-11-02"}}]}
    it = ss.bound_stage(raw)["items"][0]
    assert it["title"] == "" and it["who"] == "" and it["facets"] == {"kind": "medication"}
    fin = ss.bound_stage({"workspace": "finance", "items": [{"ref": "fin:pos-0", "title": "AAPL 120 sh", "facets": {"kind": "position", "ticker": "AAPL"}}]})
    assert fin["items"][0]["title"] == "" and fin["items"][0]["facets"] == {"kind": "position"}
    fam = ss.bound_stage({"workspace": "family", "items": [{"ref": "fam:cd-0", "title": "Mia's birthday"}]})
    assert fam["items"][0]["title"] == ""


def test_a_workspace_that_may_name_its_rows_keeps_the_title_and_drops_other_facets():
    it = ss.bound_stage({"workspace": "calendar", "items": [{"ref": "event:abc", "n": 1, "title": "Dentist",
                         "facets": {"kind": "normal", "when": "today", "location": "12 Elm St"}}]})["items"][0]
    assert it["title"] == "Dentist" and it["facets"] == {"kind": "normal", "when": "today"}


def test_a_ref_of_an_unknown_kind_is_not_a_row():
    assert ss.bound_stage({"workspace": "calendar", "items": [{"ref": "btn:send", "n": 1}]})["items"] == []


def _cal_stage():
    items = [{"ref": "event:e%d" % i, "n": i, "facets": {"kind": "normal", "when": "today" if i < 3 else "tomorrow"}, "title": "Event %d" % i}
             for i in range(1, 5)]
    return {"workspace": "calendar", "rev": 1, "items": items, "loaded": 4, "selection": {"refs": []}, "filters": []}


def test_friday_can_point_at_the_events_of_today(monkeypatch):
    report(_cal_stage())
    page = Page(monkeypatch, answer={"ok": True, "count": 2})
    out = agent._tool_screen_select({"op": "point", "workspace": "calendar", "match": {"when": "today"}})
    sent = page.sent[-1][0]
    assert out.startswith("POINT_OK 2 marked"), out
    assert sent["refs"] == ["event:e1", "event:e2"] and sent["workspace"] == "calendar"


def test_friday_can_tick_events_and_it_changes_nothing(monkeypatch):
    report(_cal_stage())
    page = Page(monkeypatch, answer={"ok": True, "applied": 2, "missing": 0, "accepted": 2, "count": 2})
    out = agent._tool_screen_select({"op": "select", "workspace": "calendar", "scope": "screen", "match": {"when": "today"}})
    assert out.startswith("SELECT_OK"), out
    assert page.types() == ["select"] and ap.list_approvals() == []


def test_a_cloud_voice_hears_counts_of_events_never_a_title(monkeypatch):
    monkeypatch.setattr(agent, "_cloud_voice", lambda: True)
    report(_cal_stage())
    Page(monkeypatch, answer={"ok": True, "count": 2})
    out = agent._tool_screen_select({"op": "point", "workspace": "calendar", "match": {"when": "today"}})
    assert "Event 1" not in out and "events" in out


def test_navigate_to_an_event_carries_its_id_and_is_confirmed_by_the_page():
    r = dt.resolve_calendar(query="tomorrow", id="event:abc123")
    assert r["ok"] and r["target"]["event"] == "abc123" and "date" in r["target"]
    assert r["verify"] == {"workspace": "calendar", "key": "event", "value": "abc123"}
    assert dt.resolve_calendar(id="event:")["ok"] is False
    assert dt.resolve_calendar(id="2026-10-02")["target"] == {"workspace": "calendar", "date": "2026-10-02"}


def test_the_calendar_page_accepts_and_reports_the_event():
    for page in ("index.html", "ui_parts/app.html"):
        text = (ROOT / page).read_text(encoding="utf-8")
        assert "'meeting_id','event']" in text.replace(" ", "") or "'meeting_id', 'event']" in text, page
        assert "setShownEvent(String(t.event))" in text and "shownEvent" in text, page
    assert "kind === 'chat' ? 'chat'" in (ROOT / "index.html").read_text(encoding="utf-8")


def test_the_marked_rows_carry_ids_that_are_url_safe():
    text = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "encodeURIComponent(String(id == null ? '' : id)).slice(0, 150)" in text
