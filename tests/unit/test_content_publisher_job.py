"""The job that sends scheduled posts is switched on when a post needs it,
and stays off when the owner switched it off.

The Content publisher (scheduler builtin `content_publisher`) ships switched
off. Scheduling a post for a platform with a connected account switches it on
through the scheduler's own update, the one the Workflows switch makes, so
Workflows lists it as on, says it was switched on automatically, and one
notification says so. Switching it on approves and sends nothing: the post
still waits for its card.

When the owner has switched the job off, nothing switches it back on: the
answer to the schedule call says the post won't go out and points at
Workflows, and a notification says the same. With no connected account the
job is not switched on, and the answer says why the post won't go out.

Hermetic: content store, platform registry, schedules and runs on tmp; the
in-memory mock platform; moderation and egress seams passing; notifications
captured.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import agent_friday.server as friday_server
from agent_friday.governance import action_gate as ag
from agent_friday.services import agent as agent_mod
from agent_friday.services import approvals as ap
from agent_friday.services import content_composer as cc
from agent_friday.services import content_pipeline as cpl
from agent_friday.services import platforms as preg
from agent_friday.services import publisher as pub
from agent_friday.services import scheduler as sch
from agent_friday.services import workflow_overview as wo
from agent_friday.services.platforms import base as pbase

SID = "sch_content_publisher"
BODY = "A synthetic post about the new release notes."


def _later(hours=6):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    monkeypatch.setattr(cc, "VOICE_CARDS_DIR", tmp_path / "voice_cards")
    monkeypatch.setattr(cc, "adapt", lambda post, platforms=None, **kw: {"ok": True})
    monkeypatch.setattr(sch, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(sch, "RUNS_FILE", tmp_path / "schedule_runs.jsonl")
    monkeypatch.setattr(sch, "BUILTIN_TASKS", {})
    monkeypatch.setattr(pub, "_moderation_scan", lambda text: {"ok": True, "blocked": False})
    monkeypatch.setattr(pub, "_gate", lambda text, provider, field: text)
    monkeypatch.setattr(pub, "_earn_publish_psi", lambda post, target: None)
    monkeypatch.setattr(pub, "content_enabled", lambda: True)
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    notes = []
    monkeypatch.setattr(pub, "_notify",
                        lambda title, body="", **kw: notes.append(dict(kw, title=title, body=body)))
    preg._reset_for_tests()
    pub._RUNNING.clear()
    # Boot, as server.py does it: register the builtins, then seed schedules.json.
    assert pub.start()["ok"]
    sch._seed_and_reconcile()
    yield {"notes": notes, "tmp": tmp_path}
    preg._reset_for_tests()
    pub._RUNNING.clear()


@pytest.fixture
def notes(env):
    return env["notes"]


@pytest.fixture
def adapter():
    a = preg.get_adapter("mock")
    a.reset()
    return a


@pytest.fixture
def client():
    friday_server.app.config.update(TESTING=True)
    return friday_server.app.test_client()


def _draft(client, platforms=("mock",)):
    res = client.post("/api/content/posts", json={"title": "", "body": BODY,
                                                  "platforms": list(platforms)})
    data = res.get_json()
    assert data["ok"], data
    return data["post"]


def _schedule(client, post_id):
    res = client.post(f"/api/content/posts/{post_id}/schedule",
                      json={"publish_at": _later()})
    data = res.get_json()
    assert data["ok"], data
    return data


def _publisher_row():
    return {r["id"]: r for r in sch.list_schedules()}[SID]


def test_scheduling_for_a_connected_account_switches_the_publisher_on(client, adapter, notes):
    assert sch.get_schedule(SID)["enabled"] is False, "the publisher ships switched off"
    post = _draft(client)
    out = _schedule(client, post["id"])

    rec = sch.get_schedule(SID)
    assert rec["enabled"] is True, "scheduling a post left the Content publisher off"
    row = _publisher_row()
    assert row["enabled"] is True and row["status"] == "scheduled", row
    assert out["publisher"]["on"] is True and out["publisher"]["auto_enabled"] is True
    assert not out["publisher"]["notice"], out["publisher"]

    said = [n for n in notes if n["title"] == "Content publisher turned on"]
    assert len(said) == 1, notes
    assert "switch it off in Workflows" in said[0]["body"]
    assert said[0]["actions"][0]["target"] == {"workspace": "workflows"}

    # A second scheduled post finds it on: no second notice.
    _schedule(client, _draft(client)["id"])
    assert len([n for n in notes if n["title"] == "Content publisher turned on"]) == 1


def test_switching_it_on_approves_and_sends_nothing(client, adapter):
    post = _draft(client)
    res = client.post(f"/api/content/posts/{post['id']}/publish-now", json={}).get_json()
    assert res["ok"], res
    assert sch.get_schedule(SID)["enabled"] is True
    cards = [r for r in ap.list_approvals(kind="governed_action")
             if (r.get("payload") or {}).get("post_id") == post["id"]]
    assert len(cards) == 1 and cards[0]["status"] == "pending", cards
    pub.tick()
    assert adapter.publish_calls == 0, "switching the job on sent a post nobody approved"
    assert ap.get_approval(cards[0]["approval_id"])["status"] == "pending"


def test_workflows_lists_it_on_and_says_why(client, adapter, env, monkeypatch):
    monkeypatch.setattr(agent_mod, "WORKFLOWS_DIR", env["tmp"] / "workflows")
    _schedule(client, _draft(client)["id"])
    routines = {r["schedule_id"]: r for r in wo.overview()["routines"]}
    row = routines[SID]
    assert row["enabled"] is True
    assert "automatically" in row["note"] and "a post was scheduled" in row["note"], row


def test_owner_switched_off_stays_off_and_the_answer_says_so(client, adapter, notes):
    # The owner's Workflows switch: on, then off (PATCH /api/schedules/<id>).
    sch.update_schedule(SID, {"enabled": True})
    sch.update_schedule(SID, {"enabled": False})
    out = _schedule(client, _draft(client)["id"])

    assert sch.get_schedule(SID)["enabled"] is False, "the owner's off was overridden"
    notice = out["publisher"]["notice"] or ""
    assert "won't go out" in notice and "Workflows" in notice, out["publisher"]
    assert out["publisher"]["action"]["target"] == {"workspace": "workflows"}
    assert any("won't go out" in n["body"] and n["actions"][0]["target"] == {"workspace": "workflows"}
               for n in notes), notes


def test_switched_off_after_it_was_switched_on_for_the_owner_stays_off(client, adapter):
    _schedule(client, _draft(client)["id"])
    assert sch.get_schedule(SID)["enabled"] is True
    sch.update_schedule(SID, {"enabled": False})          # the owner's switch
    out = _schedule(client, _draft(client)["id"])
    assert sch.get_schedule(SID)["enabled"] is False
    assert "won't go out" in (out["publisher"]["notice"] or "")


def test_no_connected_account_leaves_it_off_and_says_why(client, adapter):
    adapter.revoke()                                       # not connected
    out = _schedule(client, _draft(client)["id"])
    assert sch.get_schedule(SID)["enabled"] is False
    notice = out["publisher"]["notice"] or ""
    assert "won't go out" in notice and "connected account" in notice, out["publisher"]


def test_the_master_switch_is_named_when_publishing_is_off(client, adapter, monkeypatch):
    monkeypatch.setattr(pub, "content_enabled", lambda: False)
    out = _schedule(client, _draft(client)["id"])
    assert "publishing is switched off" in (out["publisher"]["notice"] or ""), out["publisher"]


def test_the_schedule_tool_switches_it_on_too(adapter):
    post = cpl.create_post(body=BODY, platforms=["mock"])["post"]
    raw = agent_mod.CLAUDE_TOOL_HANDLERS["content_schedule_post"](
        {"post_id": post["id"], "optimal_time": True})
    out = json.loads(raw)
    assert out.get("status") == "ok", out
    assert sch.get_schedule(SID)["enabled"] is True
