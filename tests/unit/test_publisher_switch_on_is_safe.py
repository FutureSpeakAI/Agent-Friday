"""Switching the Content publisher on never overrides the owner, never loses
a schedule, and a model's pause is not the owner's off.

Arming a post switches the job that sends scheduled posts on while it is
still in its seeded-off state. That switch reads schedules.json, checks the
record and writes it in one step (scheduler.enable_if_seeded_off):

* a read that fails or races (get_schedule answering None once) never turns
  an owner's off back on;
* a read that comes back empty, or a schedules.json that exists but cannot
  be read, never makes the switch write a file holding only the publisher:
  every other schedule is still there afterwards;
* a missing publisher record is not seeded from here.

A pause made with the schedule_job tool is a model's call: from a background
or scheduled run it is refused for the Content publisher (hiding that posts
stopped going out is a visibility-reducing action); from the owner's chat it
is allowed, announced, and not treated as the owner's own off, so arming a
post switches the job on again. The owner's own off (the Workflows switch)
still stays off (tests/unit/test_content_publisher_job.py).

Hermetic: content store, platform registry, schedules and runs on tmp; the
in-memory mock platform; moderation and egress seams passing; notifications
captured.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import agent as agent_mod
from agent_friday.services import content_composer as cc
from agent_friday.services import content_pipeline as cpl
from agent_friday.services import platforms as preg
from agent_friday.services import publisher as pub
from agent_friday.services import schedule_tools as st
from agent_friday.services import scheduler as sch
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
    monkeypatch.setattr(pub, "kick", lambda: {"kicked": False})
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    notes = []
    monkeypatch.setattr(pub, "_notify",
                        lambda title, body="", **kw: notes.append(dict(kw, title=title, body=body)))
    preg._reset_for_tests()
    pub._RUNNING.clear()
    # Boot, as server.py does it: register the builtins, then seed schedules.json.
    assert pub.start()["ok"]
    sch._seed_and_reconcile()
    preg.get_adapter("mock").reset()
    yield {"notes": notes, "tmp": tmp_path}
    preg._reset_for_tests()
    pub._RUNNING.clear()


@pytest.fixture
def notes(env):
    return env["notes"]


def _armed_post() -> str:
    post = cpl.create_post(body=BODY, platforms=["mock"])["post"]
    assert cpl.schedule_post(post["id"], cpl.new_schedule_config(publish_at=_later()))["ok"]
    return post["id"]


def _two_more_schedules():
    for n in ("one", "two"):
        sch.register_schedule({"name": f"Synthetic job {n}", "trigger": "daily",
                               "spec": {"hour": 7, "minute": 0},
                               "task": {"kind": "agent_prompt", "prompt": "Say hello."}})


def _stored_ids():
    return sorted(r["id"] for r in json.loads(Path(sch.SCHEDULES_FILE).read_text(encoding="utf-8")))


# ── a failed or racing read never overrides the owner's off ────────────────

def test_a_failed_read_does_not_switch_the_owners_off_back_on(monkeypatch):
    sch.update_schedule(SID, {"enabled": True})
    sch.update_schedule(SID, {"enabled": False})            # the owner's Workflows switch
    rec = sch.get_schedule(SID)
    assert rec["enabled"] is False and rec.get("switched_at"), rec
    pid = _armed_post()

    real = sch.get_schedule
    calls = []

    def none_once(sid):
        calls.append(sid)
        return None if len(calls) == 1 else real(sid)
    monkeypatch.setattr(sch, "get_schedule", none_once)
    out = pub.ensure_publisher_running(pid)
    monkeypatch.setattr(sch, "get_schedule", real)

    assert sch.get_schedule(SID)["enabled"] is False, \
        "a read that failed once switched the owner's off back on"
    assert out["on"] is False and "won't go out" in (out["notice"] or ""), out


# ── a failed read never wipes schedules.json ────────────────────────────────

def test_an_empty_read_never_leaves_only_the_publisher_in_the_file(monkeypatch):
    _two_more_schedules()
    before = _stored_ids()
    assert len(before) >= 3 and SID in before, before
    pid = _armed_post()

    real = sch._read_store
    monkeypatch.setattr(sch, "_read_store", lambda: [])
    pub.ensure_publisher_running(pid)
    monkeypatch.setattr(sch, "_read_store", real)

    assert _stored_ids() == before, "switching the publisher on deleted other schedules"


def test_an_unreadable_schedules_file_is_never_written(monkeypatch):
    _two_more_schedules()
    path = Path(sch.SCHEDULES_FILE)
    before = path.read_bytes()
    pid = _armed_post()

    real_read = Path.read_text

    def in_use(self, *a, **k):
        if Path(self) == path:
            raise PermissionError("synthetic: the file is in use by another writer")
        return real_read(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", in_use)
    out = pub.ensure_publisher_running(pid)
    monkeypatch.setattr(Path, "read_text", real_read)

    assert path.read_bytes() == before, "a schedules.json that could not be read was rewritten"
    assert out["on"] is False and "couldn't read" in (out["notice"] or ""), out


def test_a_missing_publisher_record_is_not_seeded_from_here():
    recs = [r for r in json.loads(Path(sch.SCHEDULES_FILE).read_text(encoding="utf-8"))
            if r.get("id") != SID]
    Path(sch.SCHEDULES_FILE).write_text(json.dumps(recs), encoding="utf-8")
    before = _stored_ids()
    out = pub.ensure_publisher_running(_armed_post())
    assert _stored_ids() == before, "arming a post wrote a new publisher record"
    assert out["on"] is False and out["notice"], out


# ── a model's pause ─────────────────────────────────────────────────────────

def test_a_background_job_cannot_pause_the_content_publisher(monkeypatch):
    sch.update_schedule(SID, {"enabled": True})
    monkeypatch.setattr(agent_mod, "_workflow_caller_context",
                        lambda: {"conversation_id": None, "nested_execution": True,
                                 "task_id": "task-synthetic-0001", "schedule_id": "sch_synthetic"})
    with pytest.raises(ValueError):
        st.schedule_job({"action": "pause", "schedule_id": SID})
    assert sch.get_schedule(SID)["enabled"] is True, "a background run paused the Content publisher"


def test_a_pause_from_chat_is_announced_and_is_not_the_owners_off(monkeypatch, notes):
    sch.update_schedule(SID, {"enabled": True})
    monkeypatch.setattr(agent_mod, "_workflow_caller_context",
                        lambda: {"conversation_id": "conv-synthetic-0001", "nested_execution": False,
                                 "task_id": None, "schedule_id": None})
    out = json.loads(st.schedule_job({"action": "pause", "schedule_id": SID}))
    assert out.get("paused") is True, out
    assert sch.get_schedule(SID)["enabled"] is False
    said = [n for n in notes if "paused from a chat" in n["title"]]
    assert said and "won't go out" in said[0]["body"], notes
    assert said[0]["actions"][0]["target"] == {"workspace": "workflows"}

    pub.ensure_publisher_running(_armed_post())
    assert sch.get_schedule(SID)["enabled"] is True, \
        "a model's pause was kept as if the owner had switched the publisher off"
