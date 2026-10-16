"""The heartbeat checks in code first, and calls a model only on change.

It used to spawn a full agent every run (the chat prompt plus tool schemas,
2-5 model calls, 100k-180k input tokens) to answer "NO CHANGE" most of the
time. Now (services/heartbeat.py) a deterministic pre-check reads Gmail since
the last OK run, the calendar window and finished tasks; nothing new means no
model call; something new means ONE slim call on the pinned model; a
connector that cannot be read holds the run as `connector_unavailable`.

Everything goes through the scheduler's own entry point, `_run_task`, with
the connectors and the model stubbed at their seams. On the old code that
entry point spawns an agent for every run, which these tests count as a model
call, so they fail on what they assert rather than on an import.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import local_only_guard as g
from agent_friday.services import scheduled_cloud as sc
from agent_friday.services import scheduler as s

HAIKU = "claude-haiku-4-5-20251001"
LOCAL = "synthetic-local:7b"
FULL_PROMPT_MARK = "== CAPABILITY DISCOVERY =="

_MAIL = {"gmail_id": "m-1", "sender": "Colleague <colleague@example.com>",
         "subject": "Review moved to Thursday", "snippet": "Can we move the review?",
         "timestamp": "2026-10-15T09:00:00", "unread": True}


def _heartbeat_rec():
    return {"id": "sch_heartbeat", "name": "Hourly heartbeat",
            "trigger": "interval", "spec": {"every_minutes": 60},
            "notify": "status",
            "task": {"kind": "agent_prompt", "workspace": "system",
                     "prompt": next(d["task"]["prompt"] for d in s._DEFAULT_AGENT_SCHEDULES
                                    if d["id"] == "sch_heartbeat"),
                     "tools": ["query_calendar", "find_calendar_events", "search_email"],
                     "local_only": True}}


class World:
    """The connectors, the model and the agent spawner, all recorded."""

    def __init__(self):
        self.mail = []
        self.events = []
        self.gmail_down = None
        self.model_calls = []
        self.spawned = []

    def merged_gmail(self, limit_per_account=15, days=None, query=None):
        if self.gmail_down:
            raise RuntimeError(self.gmail_down)
        return {"accounts": [{"id": "acct"}], "messages": [dict(m) for m in self.mail],
                "errors": []}

    def merged_calendar(self, days=2):
        return {"accounts": [{"id": "acct"}], "events": list(self.events), "errors": []}

    def generate_text(self, messages, system=None, **kw):
        self.model_calls.append({"messages": messages, "system": system or "",
                                 "model": kw.get("model"), "pin": g.pinned_model(),
                                 "local_only": g.is_active()})
        return "A colleague asked to move the review to Thursday."

    def spawn(self, name, prompt, **kw):
        # The old path: a full agent per run. Counted as a model call.
        self.spawned.append({"model": kw.get("model")})
        return "task-1"

    @property
    def calls(self):
        return len(self.model_calls) + len(self.spawned)


@pytest.fixture
def world(monkeypatch, tmp_path, friday_dir):
    from agent_friday.services import agent
    from agent_friday.services import google_accounts as ga
    from agent_friday.services import model_router as mr
    w = World()
    monkeypatch.setattr(ga, "has_accounts", lambda: True)
    monkeypatch.setattr(ga, "merged_gmail", w.merged_gmail)
    monkeypatch.setattr(ga, "merged_calendar", w.merged_calendar)
    monkeypatch.setattr(mr, "_generate_text", w.generate_text)
    monkeypatch.setattr(agent, "_spawn_task", w.spawn)
    monkeypatch.setattr(agent, "_task_snapshot",
                        lambda tid=None: [] if tid is None
                        else {"status": "complete", "result": "NO CHANGE"})
    # The cloud answer: allowed, pinned to the heartbeat model.
    cfg = sc.defaults()
    cfg.update(answered=True, allow=True, heartbeat_model=HAIKU)
    monkeypatch.setattr(sc, "settings", lambda: dict(cfg))
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: None)
    try:
        from agent_friday.services import heartbeat as hb
        monkeypatch.setattr(hb, "_state_path", lambda: tmp_path / "heartbeat_state.json")
    except ImportError:
        pass                   # the old code has no pre-check to point anywhere
    s._RUNNING.clear()
    return w


def _status_of(exc):
    return getattr(exc, "status", None)


# ── Nothing changed: no model at all ────────────────────────────────────────

def test_no_change_makes_zero_model_calls(world):
    result = s._run_task(_heartbeat_rec())
    assert world.calls == 0, "a heartbeat with nothing new still called a model"
    assert isinstance(result, dict) and result.get("outcome") == "no_change"
    assert result.get("changed") is False


# ── New mail: exactly one slim call, on the pinned model ────────────────────

def test_new_mail_makes_exactly_one_slim_call_on_the_pinned_model(world):
    world.mail = [_MAIL]
    result = s._run_task(_heartbeat_rec())

    assert world.spawned == [], "the heartbeat still spawned a full agent"
    assert len(world.model_calls) == 1
    call = world.model_calls[0]
    assert call["model"] == HAIKU and call["pin"] == HAIKU
    assert call["local_only"] is False
    # Slim: no chat prompt, no tool catalogue, small.
    assert FULL_PROMPT_MARK not in call["system"]
    assert "ACTION PERMISSION POLICY" not in call["system"]
    request = call["messages"][0]["content"]
    assert "Review moved to Thursday" in request
    assert (len(call["system"]) + len(request)) // 4 < 2500
    assert result["outcome"] == "reported" and result["changed"] is True


def test_mail_already_reported_is_not_reported_again(world):
    world.mail = [_MAIL]
    s._run_task(_heartbeat_rec())
    s._run_task(_heartbeat_rec())
    assert len(world.model_calls) == 1 and world.spawned == []


def test_a_serving_local_seat_takes_the_call(world, monkeypatch):
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: LOCAL)
    world.mail = [_MAIL]
    s._run_task(_heartbeat_rec())
    assert world.spawned == []
    assert [(c["model"], c["local_only"], c["pin"]) for c in world.model_calls] == [
        (LOCAL, True, "")]


# ── Held statuses ───────────────────────────────────────────────────────────

def test_a_connector_that_is_down_holds_the_run_with_no_call(world):
    world.gmail_down = "token expired"
    world.mail = [_MAIL]
    held = None
    try:
        s._run_task(_heartbeat_rec())
    except Exception as e:        # noqa: BLE001 -- the status is the assertion
        held = e
    assert _status_of(held) == "connector_unavailable", held
    assert "Gmail" in str(held) and "token expired" in str(held)
    assert world.calls == 0

    # Nothing was marked seen: the mail is reported once Gmail is back.
    world.gmail_down = None
    s._run_task(_heartbeat_rec())
    assert len(world.model_calls) == 1


def test_no_seat_and_no_cloud_permission_is_held_blocked(world, monkeypatch):
    cfg = sc.defaults()
    cfg.update(answered=True, allow=False)
    monkeypatch.setattr(sc, "settings", lambda: dict(cfg))
    world.mail = [_MAIL]
    held = None
    try:
        s._run_task(_heartbeat_rec())
    except Exception as e:        # noqa: BLE001
        held = e
    assert _status_of(held) == "blocked", held
    assert world.calls == 0


def test_budget_cap_holds_only_when_the_owner_set_a_cap(world, monkeypatch):
    from agent_friday.services import cost_meter, spend_guard
    # Decision (c): no cap ships on.
    b = cost_meter.get_budget()
    assert not b["hard_stop_daily_enabled"] and not b["hard_stop_monthly_enabled"]

    monkeypatch.setattr(spend_guard, "enabled", lambda: True)
    monkeypatch.setattr(spend_guard, "tripped",
                        lambda: {"period": "daily", "spend": 2.0, "limit": 1.0})
    world.mail = [_MAIL]
    held = None
    try:
        s._run_task(_heartbeat_rec())
    except Exception as e:        # noqa: BLE001
        held = e
    assert _status_of(held) == "budget_cap", held
    assert world.calls == 0


# ── The run log ─────────────────────────────────────────────────────────────

def _run_and_wait(rec):
    before = len(s.run_history(rec["id"], limit=100))
    s.dispatch(rec, manual=True)
    for _ in range(200):
        if len(s.run_history(rec["id"], limit=100)) > before:
            return s.run_history(rec["id"], limit=1)[0]
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_held_and_quiet_runs_are_recorded_in_the_run_log(world):
    for f in (s.SCHEDULES_FILE, s.RUNS_FILE):
        if f.exists():
            f.unlink()
    rec = s.register_schedule(_heartbeat_rec())

    quiet = _run_and_wait(rec)
    assert quiet["status"] == "complete" and quiet.get("outcome") == "no_change"

    world.gmail_down = "token expired"
    held = _run_and_wait(rec)
    assert held["status"] == "connector_unavailable"
    assert "Gmail" in held["summary"]
    assert world.calls == 0


# ── Owner decision (b): a chosen Cloud only mode is the consent ─────────────

@pytest.fixture
def own_settings(monkeypatch, tmp_path):
    """A settings.json of this test's own, read through the real loader."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setitem(core.NETWORK_STATE, "status", "online")
    core._invalidate_settings_cache()
    core._load_settings_raw()                 # seeds DEFAULT_SETTINGS (factory cloud_only)
    yield core
    core._invalidate_settings_cache()


def _owner_chooses(core, mode):
    """What the Settings mode control and setup do: an owner routing change,
    which leaves the model_routing.mode_chosen record."""
    core._save_settings({"model_routing": {"mode": mode}}, owner_routing_change=True)


def test_cloud_only_sends_the_one_slim_call_to_the_heartbeat_model(world, monkeypatch,
                                                                   own_settings):
    """Cloud only the owner CHOSE, no Settings > Spending answer, a local seat
    even serving: the single slim call goes to the pinned heartbeat model,
    through the one mode reader (cloud_intent.background_route)."""
    _owner_chooses(own_settings, "cloud_only")
    cfg = sc.defaults()
    cfg.update(answered=False, allow=False, heartbeat_model=HAIKU)
    monkeypatch.setattr(sc, "settings", lambda: dict(cfg))
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: LOCAL)
    world.mail = [_MAIL]
    s._run_task(_heartbeat_rec())
    assert world.spawned == []
    assert [(c["model"], c["local_only"], c["pin"]) for c in world.model_calls] == [
        (HAIKU, False, HAIKU)]


def test_a_local_mode_without_a_seat_or_an_answer_is_held(world, monkeypatch):
    from agent_friday.services import cloud_intent
    monkeypatch.setattr(cloud_intent, "_stored_mode", lambda: "local_only")
    cfg = sc.defaults()
    cfg.update(answered=False, allow=False, heartbeat_model=HAIKU)
    monkeypatch.setattr(sc, "settings", lambda: dict(cfg))
    world.mail = [_MAIL]
    held = None
    try:
        s._run_task(_heartbeat_rec())
    except Exception as e:        # noqa: BLE001
        held = e
    assert _status_of(held) == "blocked", held
    assert "local" in str(held)
    assert world.calls == 0


def test_the_factory_cloud_only_is_not_consent(world, monkeypatch, own_settings):
    """A fresh install's seeded cloud_only, never chosen, with a local seat
    serving: the call stays on the local seat."""
    cfg = sc.defaults()
    cfg.update(answered=False, allow=False, heartbeat_model=HAIKU)
    monkeypatch.setattr(sc, "settings", lambda: dict(cfg))
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: LOCAL)
    world.mail = [_MAIL]
    s._run_task(_heartbeat_rec())
    assert [(c["model"], c["local_only"], c["pin"]) for c in world.model_calls] == [
        (LOCAL, True, "")]


# ── Finished tasks: no result text to a cloud model ────────────────────────

_PRIVATE_TEXT = "summary of the synthetic captured meeting"


def _finished_task(monkeypatch):
    from agent_friday.services import agent
    task = {"name": "Meeting capture", "status": "complete", "ended": time.time(),
            "description": "meeting capture", "result": _PRIVATE_TEXT}
    monkeypatch.setattr(agent, "_task_snapshot",
                        lambda tid=None: [dict(task)] if tid is None
                        else {"status": "complete", "result": "NO CHANGE"})


def test_a_cloud_call_gets_finished_tasks_by_name_and_status_only(world, monkeypatch):
    """Red on e8c0b8c2a: the result text of a task that may have run in a
    private local-only scope went into the cloud call."""
    _finished_task(monkeypatch)
    s._run_task(_heartbeat_rec())                 # no seat, cloud allowed: cloud
    assert len(world.model_calls) == 1
    call = world.model_calls[0]
    assert call["model"] == HAIKU and call["local_only"] is False
    request = call["messages"][0]["content"]
    assert "Meeting capture (complete)" in request
    assert _PRIVATE_TEXT not in request and _PRIVATE_TEXT not in call["system"]


def test_a_local_call_still_gets_finished_task_results(world, monkeypatch):
    _finished_task(monkeypatch)
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: LOCAL)
    s._run_task(_heartbeat_rec())
    assert len(world.model_calls) == 1
    call = world.model_calls[0]
    assert call["model"] == LOCAL and call["local_only"] is True
    assert _PRIVATE_TEXT in call["messages"][0]["content"]
