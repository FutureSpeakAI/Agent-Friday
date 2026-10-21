"""The second security review's conditions on the scheduled-posts fix.

* A Content publisher switch-on whose write fails (os.replace refused on
  Windows while schedules.json is held open) leaves the job off, and the
  arming call says the post won't go out until it is on, instead of saying
  nothing (the screen would then read "Scheduled").
* A late post's old decision stays old: a remembered decision made more than
  a day ago does not send a target three days late, nor does a series'
  remembered decision send a late occurrence; each asks again.
* Re-timing a late post (post now) does not make its old approval count:
  freshness is judged against the due time the card was raised for.
* Raising the cards for a late post whose approval is too old does not
  report it as approved, so the model is not told the owner's decision
  already covers it.

Hermetic: content store, platform registry, schedules and runs on tmp; the
in-memory mock platform; moderation and egress seams passing; approvals in
the real store; notifications captured.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import approvals as ap
from agent_friday.services import content_composer as cc
from agent_friday.services import content_pipeline as cpl
from agent_friday.services import platforms as preg
from agent_friday.services import publisher as pub
from agent_friday.services import scheduler as sch
from agent_friday.services.platforms import base as pbase

SID = "sch_content_publisher"
BODY = "A synthetic post about the spring release notes."
LATE = "A synthetic post that was due some days ago."
SERIES = "A synthetic weekly tip that repeats."


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(**delta):
    return _iso(datetime.now(timezone.utc) - timedelta(**delta))


def _later(**delta):
    return _iso(datetime.now(timezone.utc) + timedelta(**delta))


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


def _cards(post_id):
    return [r for r in ap.list_approvals(kind="governed_action")
            if (r.get("payload") or {}).get("post_id") == post_id]


def _scheduled_for(when, body, **sched):
    post = cpl.create_post(body=body, platforms=["mock"],
                           schedule=cpl.new_schedule_config(publish_at=when, **sched))["post"]
    assert cpl.schedule_post(post["id"])["ok"]
    return cpl.get_post(post["id"])["post"]


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


def _remember(tid, days_ago, **fields):
    """A remembered publish decision for `tid`, made `days_ago` days ago."""
    pub._auth_update(tid, **fields)
    data = pub._auth_read()
    data[tid]["at"] = time.time() - days_ago * 86400
    pub._auth_path().write_text(json.dumps(data), encoding="utf-8")


def _sha(target, post):
    return pub._text_sha(pub._final_texts(target, post))


# ── a switch-on that cannot be saved says so ────────────────────────────────

def test_a_switch_on_that_cannot_be_written_leaves_a_notice_and_the_job_off(monkeypatch, notes):
    post = cpl.create_post(body=BODY, platforms=["mock"])["post"]
    assert cpl.schedule_post(post["id"], cpl.new_schedule_config(publish_at=_later(hours=6)))["ok"]
    assert sch.get_schedule(SID)["enabled"] is False, "the publisher ships switched off"

    def refused(records):
        raise PermissionError("synthetic: os.replace refused, schedules.json is open elsewhere")
    monkeypatch.setattr(sch, "_write_store", refused)
    out = pub.ensure_publisher_running(post["id"])

    assert sch.get_schedule(SID)["enabled"] is False
    assert out["on"] is False and out["auto_enabled"] is False, out
    assert out["notice"], "a switch-on that failed to save left no notice: the screen says Scheduled"
    assert "won't go out" in out["notice"] and "Workflows" in out["notice"], out
    assert out["action"] == {"label": "Open Workflows", "target": {"workspace": "workflows"}}, out


# ── an old remembered decision does not send a late post ───────────────────

def test_a_remembered_decision_two_days_old_does_not_send_a_post_three_days_late(adapter):
    post = _scheduled_for(_ago(days=3), LATE)
    t = post["targets"][0]
    _remember(t["id"], 2, sha=_sha(t, post), media_sha="", via="apr_synthetic0001")

    pub.tick()

    assert adapter.publish_calls == 0, "a post three days late went out on a two-day-old decision"
    pending = [c for c in _cards(post["id"]) if c["status"] == "pending"]
    assert len(pending) == 1, _cards(post["id"])
    assert "did not go out then" in pending[0]["description"], pending[0]["description"]


def test_a_series_decision_two_days_old_does_not_send_a_late_occurrence(adapter):
    parent = cpl.create_post(body=SERIES, platforms=["mock"],
                             schedule=cpl.new_schedule_config(publish_at=_ago(days=10),
                                                              recurrence="daily"))["post"]
    parent = cpl.get_post(parent["id"])["post"]              # left a draft: never claimed
    clone = cpl.create_post(body=SERIES, platforms=["mock"],
                            schedule=cpl.new_schedule_config(publish_at=_ago(days=3),
                                                             recurrence="daily"),
                            source={"kind": "recurrence", "ref": parent["id"]})["post"]
    assert cpl.schedule_post(clone["id"])["ok"]
    clone = cpl.get_post(clone["id"])["post"]
    pt = parent["targets"][0]
    assert _sha(pt, parent) == _sha(clone["targets"][0], clone)
    _remember(pt["id"], 2, sha=_sha(pt, parent), media_sha="", via="apr_synthetic0002",
              series=pub._series(clone))

    pub.tick()

    assert adapter.publish_calls == 0, \
        "a series occurrence three days late went out on the series' two-day-old decision"
    pending = [c for c in _cards(clone["id"]) if c["status"] == "pending"]
    assert len(pending) == 1, _cards(clone["id"])
    assert "did not go out then" in pending[0]["description"], pending[0]["description"]


# ── posting a late post now does not revive its old approval ───────────────

def test_posting_a_late_post_now_does_not_make_its_old_approval_count(adapter):
    late = _scheduled_for(_ago(days=8), LATE)
    old_card = _approved(late["id"], decided_hours_ago=8 * 24)
    assert sch.get_schedule(SID)["enabled"] is False, "the publisher ships switched off"

    assert cpl.publish_now(late["id"])["ok"]                 # publish_at becomes now
    pub.tick()

    assert adapter.publish_calls == 0, "posting now sent the post on an eight-day-old approval"
    fresh = [c for c in _cards(late["id"]) if c["approval_id"] != old_card]
    assert len(fresh) == 1 and fresh[0]["status"] == "pending", _cards(late["id"])

    # Approving the card that asks now sends it, once.
    ap.decide(fresh[0]["approval_id"], "approve", decided_by="owner")
    pub.tick()
    assert adapter.publish_calls == 1
    assert adapter.published[0]["body"] == LATE


# ── an approval too old to count is not reported as approved ───────────────

def test_raising_cards_for_a_late_post_with_an_old_approval_does_not_say_approved():
    from agent_friday.services import agent as agent_mod
    late = _scheduled_for(_ago(days=8), LATE)
    _approved(late["id"], decided_hours_ago=8 * 24)

    out = pub.request_publish_approval(late["id"])

    rows = out.get("targets") or []
    assert rows and all(r.get("state") != "approved" for r in rows), rows
    note = agent_mod._content_approval_note(out)
    assert "already covers" not in note, note
