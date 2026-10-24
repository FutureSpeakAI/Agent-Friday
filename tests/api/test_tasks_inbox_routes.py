"""The phone list's settings routes (routes/tasks_inbox.py).

Turning the list on, sending more text to Google and deleting the list are a press in
Friday's own window on this PC: never another device, a forwarded page, a script or a body
that claims a label. Turning it off and sending less are open. Each refusal test sends what a
browser elsewhere would send through a forward that strips its headers (page metadata and the
session token, no desk key). On the old tree the routes do not exist, so each test fails by
assertion on the status code.
"""
from __future__ import annotations

import importlib

import pytest

from tests.desk_press_support import desk_headers, page_headers
from tests.tasks_inbox_support import FakeTasks


@pytest.fixture
def ti(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    try:
        mod = importlib.import_module("agent_friday.services.tasks_inbox")
    except ImportError:
        pytest.fail("there is no phone list: services/tasks_inbox does not exist")
    fake = FakeTasks()
    monkeypatch.setattr(mod, "_key", lambda: b"k" * 32)
    monkeypatch.setattr(mod, "_service", lambda account_id: fake)
    monkeypatch.setattr(mod, "writable_accounts",
                        lambda: [{"id": "acct1", "email": "owner@example.com", "label": "Me"}])
    monkeypatch.setattr(mod, "_checkpoint", lambda action, aid: True)
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    mod.fake = fake
    return mod


def _enable(client, headers, level="short"):
    return client.post("/api/tasks-inbox/enable", json={"account_id": "acct1", "text_level": level},
                       headers=headers)


def test_turning_it_on_from_another_device_is_refused(client, ti):
    r = _enable(client, page_headers())
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get("code") == "desk_proof_required"
    assert ti.fake.lists == {} and not ti.config().get("enabled")


def test_turning_it_on_with_no_page_at_all_is_refused(client, ti):
    r = client.post("/api/tasks-inbox/enable", json={"account_id": "acct1", "text_level": "short"})
    assert r.status_code in (401, 403)
    assert ti.fake.lists == {}


def test_a_press_on_this_pc_turns_it_on(client, ti):
    r = _enable(client, desk_headers())
    assert r.status_code == 200, r.get_json()
    assert ti.config()["enabled"] and len(ti.fake.lists) == 1


def test_more_text_to_google_needs_the_press_and_less_does_not(client, ti):
    assert _enable(client, desk_headers(), "short").status_code == 200
    r = client.post("/api/tasks-inbox/level", json={"text_level": "full"}, headers=page_headers())
    assert r.status_code == 403 and ti.config()["text_level"] == "short"
    r = client.post("/api/tasks-inbox/level", json={"text_level": "minimal"}, headers=page_headers())
    assert r.status_code == 200 and ti.config()["text_level"] == "minimal"
    r = client.post("/api/tasks-inbox/level", json={"text_level": "full"}, headers=desk_headers())
    assert r.status_code == 200 and ti.config()["text_level"] == "full"


def test_turning_it_off_is_open_and_deleting_the_list_needs_the_press(client, ti):
    assert _enable(client, desk_headers()).status_code == 200
    r = client.post("/api/tasks-inbox/disable", json={"remove_list": True}, headers=page_headers())
    assert r.status_code == 403 and len(ti.fake.lists) == 1
    r = client.post("/api/tasks-inbox/disable", json={"remove_list": False}, headers=page_headers())
    assert r.status_code == 200 and not ti.config().get("enabled")
    assert len(ti.fake.lists) == 1


def test_the_page_reads_what_a_tick_does(client, ti):
    r = client.get("/api/tasks-inbox", headers=page_headers())
    assert r.status_code == 200
    body = r.get_json()
    assert body["enabled"] is False and body["text_level"] is None
    assert "approves nothing" in body["honest_limit"]
    assert any("Nothing is approved" in w for w in body["what_a_tick_does"])
