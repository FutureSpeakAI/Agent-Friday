"""GET /api/countdowns answers with the owner's own countdowns
(services/countdowns.py): no generic holidays, the personal kind for the
Family workspace, a limit, and the sources that could not be read.
Fictional names only."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest


def _day(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


@pytest.fixture
def world(monkeypatch, tmp_path):
    from agent_friday import core
    from agent_friday.services import calendar_engine as ce
    from agent_friday.services import goals, misc_engine
    from agent_friday.services import relationship_memory as rm
    from agent_friday.services.knowledge_graph import store as kg_store
    state = {"cal": [], "todos": []}
    monkeypatch.setattr(ce, "_fetch_calendar_range", lambda s, e: [dict(x) for x in state["cal"]])
    monkeypatch.setattr(ce, "_load_local_events", lambda: [])
    monkeypatch.setattr(misc_engine, "_load_todos", lambda: [dict(x) for x in state["todos"]])
    monkeypatch.setattr(rm, "list_follow_ups", lambda status="open": [])
    monkeypatch.setattr(goals, "list_goals", lambda **kw: [])
    (tmp_path / "wiki").mkdir()
    monkeypatch.setattr(core, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(kg_store, "KG_DIR", tmp_path / "kg")
    return state


def test_there_are_no_generic_holidays(client, world):
    data = client.get("/api/countdowns").get_json()
    assert data["status"] == "ok" and data["countdowns"] == [] and data["failed"] == []
    body = client.get("/api/countdowns").get_data(as_text=True)
    for holiday in ("Independence Day", "New Year", "Summer Solstice"):
        assert holiday not in body


def test_the_owners_items_with_when_and_why(client, world):
    soon = (datetime.now() + timedelta(days=2)).replace(microsecond=0)
    world["cal"] = [
        {"id": "e1", "title": "Design review", "start_time": soon.isoformat(), "source": "google"},
        {"id": "e2", "title": "Dana's birthday", "start_time": _day(12), "all_day": True,
         "source": "google"},
    ]
    world["todos"] = [{"id": "t1", "title": "Renew the lease", "deadline": _day(3),
                       "status": "approved"}]
    items = client.get("/api/countdowns").get_json()["countdowns"]
    assert [c["label"] for c in items] == ["Design review", "Renew the lease", "Dana's birthday"]
    assert [c["why"] for c in items] == ["from your calendar", "a deadline you set",
                                         "from your calendar"]
    assert items[2]["short"] == "in 12 days" and items[2]["date"] == _day(12)
    personal = client.get("/api/countdowns?kind=personal").get_json()["countdowns"]
    assert [c["label"] for c in personal] == ["Dana's birthday"]
    assert len(client.get("/api/countdowns?limit=1").get_json()["countdowns"]) == 1


def test_a_source_that_fails_is_named(client, world, monkeypatch):
    from agent_friday.services import misc_engine

    def broken():
        raise OSError("unreadable")

    monkeypatch.setattr(misc_engine, "_load_todos", broken)
    data = client.get("/api/countdowns").get_json()
    assert data["status"] == "ok" and data["failed"] == ["to-dos"]
