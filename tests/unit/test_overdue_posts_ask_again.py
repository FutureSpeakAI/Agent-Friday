"""A post approved for a time long past does not go out on that old approval.

Scheduled posts did not go out while the Content publisher job was off, so
the first time it is switched on (arming any post does that) it reaches every
due post at once, some of them weeks late. The owner approved "When: X", not
"whenever the job next runs": a target more than OVERDUE_GRACE_S (a day) late
does not go out on a decision made before the last day. Its card asks again,
saying it was due then and goes when approved; approving it then sends it.
A post only a few hours late still goes out on its approval, and the
switch-on notice says how many approved posts are due now and how many late
ones will ask again.

Hermetic: content store, platform registry, schedules and runs on tmp; the
in-memory mock platform; moderation and egress seams passing; approvals in
the real store; notifications captured.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

import agent_friday.server as friday_server
from agent_friday.governance import action_gate as ag
from agent_friday.services import approvals as ap
from agent_friday.services import content_composer as cc
from agent_friday.services import content_pipeline as cpl
from agent_friday.services import platforms as preg
from agent_friday.services import publisher as pub
from agent_friday.services import scheduler as sch
from agent_friday.services.platforms import base as pbase

SID = "sch_content_publisher"
LATE = "A synthetic post that was due a week ago."
RECENT = "A synthetic post that was due this morning."
NEXT = "A synthetic post for next week."


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(**delta):
    return _iso(datetime.now(timezone.utc) - timedelta(**delta))


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
    monkeypatch.setattr(pub, "kick", lambda: {"kicked": False})
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    notes = []
    monkeypatch.setattr(pub, "_notify",
                        lambda title, body="", **kw: notes.append(dict(kw, title=title, body=body)))
    preg._reset_for_tests()
    pub._RUNNING.clear()
    assert pub.start()["ok"]
    sch._seed_and_reconcile()
    yield {"notes": notes}
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


def _cards(post_id):
    return [r for r in ap.list_approvals(kind="governed_action")
            if (r.get("payload") or {}).get("post_id") == post_id]


def _scheduled_for(when, body):
    post = cpl.create_post(body=body, platforms=["mock"],
                           schedule=cpl.new_schedule_config(publish_at=when))["post"]
    assert cpl.schedule_post(post["id"])["ok"]
    return post


def _approved(post_id, decided_hours_ago):
    """The owner approved the post's card, `decided_hours_ago` hours ago."""
    pub.request_publish_approval(post_id)
    cards = _cards(post_id)
    assert len(cards) == 1, cards
    aid = cards[0]["approval_id"]
    ap.decide(aid, "approve", decided_by="owner")
    recs = ap._read_store()
    for r in recs:
        if r.get("approval_id") == aid:
            r["decided_at"] = time.time() - decided_hours_ago * 3600
    ap._write_store(recs)
    return aid


def _arm_another(client):
    res = client.post("/api/content/posts", json={"title": "", "body": NEXT, "platforms": ["mock"]})
    pid = res.get_json()["post"]["id"]
    later = _iso(datetime.now(timezone.utc) + timedelta(days=7))
    out = client.post(f"/api/content/posts/{pid}/schedule", json={"publish_at": later}).get_json()
    assert out["ok"], out
    return pid


def test_an_approved_post_a_week_late_asks_again_instead_of_going_out(client, adapter, notes):
    late = _scheduled_for(_ago(days=7), LATE)
    old_card = _approved(late["id"], decided_hours_ago=8 * 24)
    assert sch.get_schedule(SID)["enabled"] is False, "the publisher ships switched off"

    _arm_another(client)                                     # switches the publisher on
    assert sch.get_schedule(SID)["enabled"] is True
    pub.tick()

    assert adapter.publish_calls == 0, "a week-late post went out on a week-old approval"
    target = cpl.get_post(late["id"])["post"]["targets"][0]
    assert target["status"] == "PENDING", target
    fresh = [c for c in _cards(late["id"]) if c["approval_id"] != old_card]
    assert len(fresh) == 1 and fresh[0]["status"] == "pending", _cards(late["id"])
    assert "did not go out then" in fresh[0]["description"], fresh[0]["description"]
    assert any(n["title"] == "A late post asks you again" for n in notes), notes
    on = [n for n in notes if n["title"] == "Content publisher turned on"]
    assert on and "more than a day late will ask you again" in on[0]["body"], on

    # Approving the new card sends it, once.
    ap.decide(fresh[0]["approval_id"], "approve", decided_by="owner")
    pub.tick()
    assert adapter.publish_calls == 1
    assert adapter.published[0]["body"] == LATE


def test_an_approved_post_a_few_hours_late_goes_out_and_the_notice_counts_it(client, adapter, notes):
    recent = _scheduled_for(_ago(hours=3), RECENT)
    _approved(recent["id"], decided_hours_ago=4)

    _arm_another(client)
    on = [n for n in notes if n["title"] == "Content publisher turned on"]
    assert on and "1 post you already approved is due now" in on[0]["body"], on

    pub.tick()
    assert adapter.publish_calls == 1, "a post a few hours late was held back"
    assert adapter.published[0]["body"] == RECENT
    assert len(_cards(recent["id"])) == 1, "a post within the grace asked again"
