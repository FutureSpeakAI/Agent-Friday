"""A News routine that is waiting, late or missed says so in News, with the reason.

The scheduler records the state (tests/unit/test_news_routine_waits_for_the_seat.py);
this is the screen the owner reads. The Front Page response carries the notices,
and both copies of the UI show them above the edition.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_friday.services import scheduler as s

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_store(friday_dir):
    for p in (s.SCHEDULES_FILE, s.RUNS_FILE):
        if p.exists():
            p.unlink()
    yield


def _news(sid="sch_news_morning", **fields):
    s.register_schedule({
        "id": sid, "name": "Morning news briefing", "trigger": "daily",
        "spec": {"hour": 7}, "task": {"kind": "builtin", "ref": "news_morning",
                                      "local_only": True}})
    s._patch_record(sid, **fields)


def test_a_waiting_routine_is_a_notice_with_its_reason():
    today = s._now_central().strftime("%Y-%m-%d")
    _news(last_status="waiting", last_run_date=today, last_run_ts=time.time(),
          catch_up={"date": today, "reason": "parked for build hours until 07:00",
                    "since": time.time() - 600})
    notes = s.news_routine_notices()
    assert [n["state"] for n in notes] == ["waiting"]
    assert "parked for build hours" in notes[0]["reason"]
    assert notes[0]["name"] == "Morning news briefing"


def test_a_missed_and_a_late_routine_are_notices():
    today = s._now_central().strftime("%Y-%m-%d")
    _news(last_status="missed", last_run_ts=time.time() - 3600,
          last_summary="Missed 2026-10-01: the local seat never came back")
    _news(sid="sch_afternoon_briefing", last_status="complete", last_run_date=today,
          last_run_ts=time.time(),
          last_summary="Late: ran at 08:10 after waiting since 07:00 (parked).")
    states = {n["id"]: n["state"] for n in s.news_routine_notices()}
    assert states == {"sch_news_morning": "missed", "sch_afternoon_briefing": "late"}


def test_an_on_time_routine_is_not_a_notice():
    today = s._now_central().strftime("%Y-%m-%d")
    _news(last_status="complete", last_run_date=today, last_run_ts=time.time(),
          last_summary="{\"id\": \"2026-10-02-morning\"}")
    assert s.news_routine_notices() == []


def test_the_front_page_response_carries_the_notices(client):
    today = s._now_central().strftime("%Y-%m-%d")
    _news(last_status="waiting", last_run_date=today, last_run_ts=time.time(),
          catch_up={"date": today, "reason": "the local seat is parked",
                    "since": time.time()})
    d = client.get("/api/news/front-page/latest").get_json()
    assert d["status"] == "ok"
    assert [r["state"] for r in d["routines"]] == ["waiting"]


@pytest.mark.parametrize("ui", ["index.html", "ui_parts/app.html"])
def test_both_uis_show_the_notices_above_the_edition(ui):
    text = (REPO / ui).read_text(encoding="utf-8")
    assert "setFpRoutines(d.routines" in text.replace(" ", ""), (
        f"{ui} does not keep the routine notices from the Front Page response")
    assert "fp-routine-notice" in text, f"{ui} does not render the routine notices"
    i = text.index("fp-routine-notice")
    assert ".reason" in text[i:i + 1200], f"{ui} renders a notice without its reason"
