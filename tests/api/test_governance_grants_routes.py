"""The Grants screen's routes: list, create and revoke scoped, expiring grants
for scheduled jobs, and the list of actions a grant can cover.

A scheduled job's outward action waits on an approval card unless a grant
names that job and that action (governance/action_gate.py). Without a way to
make one, every scheduled job that changes something would stall.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate


@pytest.fixture(autouse=True)
def _grants_home(tmp_path, monkeypatch):
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: tmp_path)


def test_create_list_and_revoke(client):
    r = client.post("/api/governance/grants", json={
        "scope": "sch_weekly_digest", "tools": ["create_calendar_event"],
        "expires_in_seconds": 86400, "max_uses": 3})
    assert r.status_code == 201, r.get_data(as_text=True)
    g = r.get_json()["grant"]
    assert g["scope"] == "sch_weekly_digest" and g["uses_left"] == 3

    listed = client.get("/api/governance/grants").get_json()["grants"]
    assert [x["grant_id"] for x in listed] == [g["grant_id"]]

    assert client.delete(f"/api/governance/grants/{g['grant_id']}").get_json()["revoked"] is True
    assert client.get("/api/governance/grants").get_json()["grants"] == []


@pytest.mark.parametrize("body", [
    {"scope": "", "tools": ["run_command"], "expires_in_seconds": 60},
    {"scope": "sch_x", "tools": [], "expires_in_seconds": 60},
    {"scope": "sch_x", "tools": ["run_command"], "expires_in_seconds": 0},
    {"scope": "sch_x", "tools": ["run_command"], "expires_in_seconds": 60, "max_uses": 0},
])
def test_a_grant_must_be_scoped_named_and_expiring(client, body):
    assert client.post("/api/governance/grants", json=body).status_code == 400


def test_outward_tools_are_what_the_gate_would_hold(client):
    names = {t["name"] for t in client.get("/api/governance/outward-tools").get_json()["tools"]}
    assert {"create_calendar_event", "run_command", "content_schedule_post"} <= names
    assert "draft_email" not in names, "every email keeps its own card"
    assert "search_web" not in names and "read_file" not in names
