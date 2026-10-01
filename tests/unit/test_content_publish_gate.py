"""Every way a post can leave for a platform waits on the owner.

A post reaches another platform only after an approval card that shows its
exact words, its media and where it goes, or a still-valid scoped grant the
owner made for the job that scheduled it. That holds on every path: the
Content workspace's "post now", "schedule" and "release", a re-armed failed
post, the model's content tools, native platform scheduling, recurring
series and retries. Approving publishes exactly what the card showed, once;
declining publishes nothing. Saving a draft or previewing raises no card.

Hermetic: content store and platform registry on tmp, the in-memory mock
adapter as the platform, moderation and egress seams passing, the cLaws
integrity check stubbed intact. Approvals use the real store.
"""
from __future__ import annotations

import json
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
from agent_friday.services.platforms import base as pbase

BODY = "Gate proof: these exact words, and nothing else."
PAST = "2026-07-01T09:00:00Z"


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_plus(seconds=0):
    return _iso(datetime.now(timezone.utc) + timedelta(seconds=seconds))


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    monkeypatch.setattr(cc, "VOICE_CARDS_DIR", tmp_path / "voice_cards")
    # The composer would call a model; the tools' adaptation leaves the body.
    monkeypatch.setattr(cc, "adapt", lambda post, platforms=None, **kw: {"ok": True})
    preg._reset_for_tests()
    monkeypatch.setattr(pub, "_moderation_scan", lambda text: {"ok": True, "blocked": False})
    monkeypatch.setattr(pub, "_gate", lambda text, provider, field: text)
    monkeypatch.setattr(pub, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(pub, "_earn_publish_psi", lambda post, target: None)
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    pub._RUNNING.clear()
    yield
    preg._reset_for_tests()
    pub._RUNNING.clear()


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
    """Publish cards raised for this post, newest first."""
    return [r for r in ap.list_approvals(kind="governed_action")
            if (r.get("payload") or {}).get("post_id") == post_id]


def _draft(client, body=BODY, platforms=("mock",)):
    res = client.post("/api/content/posts", json={
        "title": "", "body": body, "platforms": list(platforms)})
    data = res.get_json()
    assert data["ok"], data
    return data["post"]


def _decide(post_id, decision):
    cards = _cards(post_id)
    assert cards, "no card was raised"
    ap.decide(cards[0]["approval_id"], decision, decided_by="owner")
    return cards[0]


def _target(post_id):
    return cpl.get_post(post_id)["post"]["targets"][0]


# ── Post now ────────────────────────────────────────────────────────────────

def test_post_now_raises_a_card_and_sends_nothing_without_approval(client, adapter):
    post = _draft(client)
    res = client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    assert res.get_json()["ok"]
    cards = _cards(post["id"])
    assert len(cards) == 1, "post-now raised no approval card"
    card = cards[0]
    assert card["status"] == "pending"
    assert BODY in card["description"], card["description"]
    assert "Mock Platform" in card["description"] or "mock" in card["title"].lower()

    pub.tick()
    assert adapter.publish_calls == 0, "post-now reached the platform with no decision"
    assert len(_cards(post["id"])) == 1, "the dispatch raised a second card"


def test_approving_post_now_publishes_exactly_the_shown_text_once(client, adapter):
    post = _draft(client)
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    pub.tick()
    assert adapter.publish_calls == 0
    card = _decide(post["id"], "approve")

    pub.tick()
    assert adapter.publish_calls == 1
    sent = adapter.published[0]
    assert sent["body"] == BODY and sent["body"] in card["description"]
    assert _target(post["id"])["status"] == "CONFIRMED"

    pub.tick(now=_now_plus(3600))
    assert adapter.publish_calls == 1, "one approval published twice"


def test_declining_post_now_publishes_nothing(client, adapter):
    post = _draft(client)
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    _decide(post["id"], "deny")
    pub.tick()
    pub.tick(now=_now_plus(3600))
    assert adapter.publish_calls == 0
    assert _target(post["id"])["status"] == "FAILED"


def test_post_now_through_the_store_alone_still_waits_for_a_card(adapter):
    """Any caller that arms a post without the route still meets the gate at
    the door to the platform."""
    post = cpl.create_post(body=BODY, platforms=["mock"])["post"]
    assert cpl.publish_now(post["id"])["ok"]
    out = pub.tick()
    assert adapter.publish_calls == 0, out
    assert out["outcomes"] == {"awaiting_approval": 1}, out
    assert len(_cards(post["id"])) == 1
    _decide(post["id"], "approve")
    pub.tick()
    assert adapter.publish_calls == 1


# ── Schedule ────────────────────────────────────────────────────────────────

def test_scheduling_raises_the_card_when_the_owner_schedules(client, adapter):
    post = _draft(client)
    res = client.post(f"/api/content/posts/{post['id']}/schedule",
                      json={"publish_at": PAST})
    assert res.get_json()["ok"], res.get_json()
    assert len(_cards(post["id"])) == 1, "scheduling raised no card"
    pub.tick()
    assert adapter.publish_calls == 0, "a scheduled post went out with no decision"
    _decide(post["id"], "approve")
    pub.tick()
    assert adapter.publish_calls == 1
    assert adapter.published[0]["body"] == BODY


def test_rescheduling_an_approved_post_asks_nothing_new(client, adapter):
    post = _draft(client)
    later = _now_plus(7200)
    client.post(f"/api/content/posts/{post['id']}/schedule", json={"publish_at": later})
    _decide(post["id"], "approve")
    client.post(f"/api/content/posts/{post['id']}/schedule",
                json={"publish_at": _now_plus(10800)})
    assert len(_cards(post["id"])) == 1


def test_an_edit_after_approval_needs_a_new_card(client, adapter):
    post = _draft(client)
    client.post(f"/api/content/posts/{post['id']}/schedule", json={"publish_at": PAST})
    _decide(post["id"], "approve")
    tid = _target(post["id"])["id"]
    assert cpl.update_target(tid, {"adapted_body": "Different words."})["ok"]
    pub.tick()
    assert adapter.publish_calls == 0, "approved words were swapped and sent"
    assert any("Different words." in c["description"] for c in _cards(post["id"]))


# ── Release of a held post ──────────────────────────────────────────────────

def test_releasing_a_held_post_does_not_publish_without_a_card(client, adapter, monkeypatch):
    post = _draft(client)
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    monkeypatch.setattr(pub, "_gate", lambda text, provider, field: "[held]")
    pub.tick()
    assert _target(post["id"])["status"] == "HELD"
    for c in _cards(post["id"]):           # nobody decided the first card
        assert c["status"] == "pending"
    res = client.post(f"/api/content/posts/{post['id']}/release", json={"ack": True})
    assert res.get_json()["ok"]
    pub.tick()
    assert adapter.publish_calls == 0, "a released post went out with no card"
    _decide(post["id"], "approve")
    pub.tick()
    assert adapter.publish_calls == 1


# ── Re-armed failures and retries ───────────────────────────────────────────

def test_a_rearmed_failed_post_asks_again(adapter):
    post = cpl.create_post(body=BODY, platforms=["mock"],
                           schedule=cpl.new_schedule_config(publish_at=PAST))["post"]
    cpl.schedule_post(post["id"])
    tid = cpl.claim_due_targets()["targets"][0]["id"]
    assert cpl.set_target_status(tid, "FAILED", error="boom")["ok"]
    assert cpl.rearm_post(post["id"], publish_at=PAST)["ok"]
    pub.tick()
    assert adapter.publish_calls == 0, "a re-armed post went out with no decision"
    assert _cards(post["id"])


def test_a_transient_retry_of_an_approved_post_needs_no_second_card(adapter):
    post = cpl.create_post(body=BODY, platforms=["mock"],
                           schedule=cpl.new_schedule_config(publish_at=PAST))["post"]
    cpl.schedule_post(post["id"])
    pub.tick()
    _decide(post["id"], "approve")
    adapter.configure({"publish_error": "rate_limited"})
    adapter.set_failures(publish=1)
    out = pub.tick()
    assert out["outcomes"] == {"retrying": 1}, out
    out = pub.tick(now=_now_plus(pub.RETRY_BACKOFF_S * 4 + 60))
    assert out["outcomes"] == {"confirmed": 1}, out
    assert len(adapter.published) == 1
    assert len(_cards(post["id"])) == 1, "a retry of approved words asked again"


def test_a_permanent_failure_spends_the_approval(adapter):
    post = cpl.create_post(body=BODY, platforms=["mock"],
                           schedule=cpl.new_schedule_config(publish_at=PAST))["post"]
    cpl.schedule_post(post["id"])
    pub.tick()
    _decide(post["id"], "approve")
    adapter.configure({"publish_error": "validation_error"})
    adapter.set_failures(publish=1)
    pub.tick()
    assert _target(post["id"])["status"] == "FAILED"
    assert cpl.rearm_post(post["id"], publish_at=PAST)["ok"]
    out = pub.tick()
    assert out["outcomes"] == {"awaiting_approval": 1}, out
    assert len(adapter.published) == 0


# ── Native platform scheduling ──────────────────────────────────────────────

def test_native_schedule_delegation_waits_for_a_card(adapter):
    adapter.configure({"capabilities": {"native_schedule": True}})
    when = _now_plus(3 * 3600)
    post = cpl.create_post(body=BODY, platforms=["mock"],
                           schedule=cpl.new_schedule_config(publish_at=when))["post"]
    cpl.schedule_post(post["id"])
    pub.tick()
    assert adapter.publish_calls == 0, "the platform was handed the post with no decision"
    assert _cards(post["id"])


# ── The model's tools ───────────────────────────────────────────────────────

def _tool(name, inp):
    from agent_friday.services import agent as agent_mod
    return json.loads(agent_mod.CLAUDE_TOOL_HANDLERS[name](inp))


def test_the_create_post_tool_schedules_behind_a_card(adapter):
    out = _tool("content_create_post", {"body": BODY, "platforms": ["mock"],
                                        "publish_at": PAST})
    assert out["status"] == "ok", out
    pid = out["post_id"]
    assert len(_cards(pid)) == 1, "the tool scheduled a post and raised no card"
    pub.tick()
    assert adapter.publish_calls == 0
    _decide(pid, "approve")
    pub.tick()
    assert adapter.publish_calls == 1 and adapter.published[0]["body"] == BODY


def test_the_schedule_tool_schedules_behind_a_card(adapter):
    post = cpl.create_post(body=BODY, platforms=["mock"])["post"]
    out = _tool("content_schedule_post", {"post_id": post["id"], "publish_at": PAST})
    assert out["status"] == "ok", out
    assert len(_cards(post["id"])) == 1
    pub.tick()
    assert adapter.publish_calls == 0
    _decide(post["id"], "deny")
    pub.tick()
    assert adapter.publish_calls == 0


def test_a_scoped_grant_that_covered_the_scheduling_still_covers_the_publish(adapter):
    g = ag.create_grant(tools=["content_schedule_post"], scope="job-1",
                        expires_in_seconds=3600)
    post = cpl.create_post(body=BODY, platforms=["mock"])["post"]
    tok = ag.DECIDED.set(g["grant_id"])
    try:
        _tool("content_schedule_post", {"post_id": post["id"], "publish_at": PAST})
    finally:
        ag.DECIDED.reset(tok)
    assert _cards(post["id"]) == []
    pub.tick()
    assert adapter.publish_calls == 1


def test_a_revoked_grant_covers_nothing(adapter):
    g = ag.create_grant(tools=["content_schedule_post"], scope="job-2",
                        expires_in_seconds=3600)
    post = cpl.create_post(body=BODY, platforms=["mock"])["post"]
    tok = ag.DECIDED.set(g["grant_id"])
    try:
        _tool("content_schedule_post", {"post_id": post["id"], "publish_at": PAST})
    finally:
        ag.DECIDED.reset(tok)
    ag.revoke_grant(g["grant_id"])
    pub.tick()
    assert adapter.publish_calls == 0
    assert _cards(post["id"])


# ── Recurring series ────────────────────────────────────────────────────────

def test_a_recurring_post_names_the_series_on_its_card_and_repeats_unchanged_words(adapter):
    parent = cpl.create_post(body=BODY, platforms=["mock"],
                             schedule=cpl.new_schedule_config(
                                 publish_at=PAST, recurrence="daily"))["post"]
    cpl.schedule_post(parent["id"])
    pub.tick(now="2026-07-01T09:05:00Z")
    assert adapter.publish_calls == 0
    card = _decide(parent["id"], "approve")
    assert "daily" in card["description"].lower()
    pub.tick(now="2026-07-01T09:06:00Z")
    assert adapter.publish_calls == 1
    pub.tick(now="2026-07-02T09:05:00Z")
    assert adapter.publish_calls == 2, "the approved series did not repeat"


# ── No avoidable cards ──────────────────────────────────────────────────────

def test_saving_a_draft_and_previewing_raise_no_card(client):
    post = _draft(client)
    client.post("/api/content/preview", json={"body": BODY, "platforms": ["mock"]})
    client.patch(f"/api/content/posts/{post['id']}", json={"body": BODY + " (edited)"})
    assert _cards(post["id"]) == []
