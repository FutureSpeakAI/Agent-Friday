"""The Workflows screen's routes: draft, save, list, remove.

A draft saves nothing; a saved scheduled workflow comes back as one item with
its timing in words; removing it removes its schedule too.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import scheduler as sch


@pytest.fixture
def stores(friday_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(ag, "WORKFLOWS_DIR", tmp_path / "workflows")
    for f in (sch.SCHEDULES_FILE, sch.RUNS_FILE):
        if f.exists():
            f.unlink()
    yield


def test_draft_then_save_then_list_then_remove(client, stores, patch_app):
    patch_app("_generate_text", lambda *a, **k: "demo mode, no JSON here")
    r = client.post("/api/workflows/draft",
                    json={"text": "Every weekday at 7:30am, check the council agendas "
                                  "and email me what changed"})
    assert r.status_code == 200
    d = r.get_json()["draft"]
    assert d["when_text"] == "Every weekday at 7:30 AM"
    assert d["asks_first"] == ["send email"]
    assert client.get("/api/workflows/overview").get_json()["workflows"] == []

    r = client.post("/api/workflows/save", json=d)
    assert r.status_code == 200, r.get_json()
    saved = r.get_json()
    items = client.get("/api/workflows/overview").get_json()["workflows"]
    assert len(items) == 1 and items[0]["when_text"] == "Every weekday at 7:30 AM"

    r = client.post("/api/workflows/remove",
                    json={"slug": saved["slug"], "schedule_id": saved["schedule_id"]})
    assert r.status_code == 200
    assert client.get("/api/workflows/overview").get_json()["workflows"] == []
    assert sch.list_schedules() == []


def test_bad_requests_say_what_is_wrong(client, stores):
    r = client.post("/api/workflows/draft", json={"text": ""})
    assert r.status_code == 400 and "Describe" in r.get_json()["message"]
    r = client.post("/api/workflows/save", json={"name": "x", "steps": []})
    assert r.status_code == 400 and "step" in r.get_json()["message"]
    r = client.post("/api/workflows/remove", json={"slug": "nope"})
    assert r.status_code == 404


def test_the_overview_counts_waiting_approvals(client, stores, monkeypatch):
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "list_approvals",
                        lambda status=None, **k: [{"approval_id": "a1"}] if status == "pending" else [])
    assert client.get("/api/workflows/overview").get_json()["pending_approvals"] == 1
