"""Built-in scheduled jobs on a PC with no local model: the owner's cloud choice.

The morning news, the evening front page, the afternoon briefing, daily
creation and the heartbeat are local-only. With a local seat serving they run
there, whatever the owner answered. With none serving they are skipped, unless
the owner allowed them onto a cloud model (`scheduled_cloud`), in which case
each run is pinned to exactly that model and metered like any cloud call.
Skipped runs raise one status entry, not a failure notice per run.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from agent_friday.services import local_only_guard as g
from agent_friday.services import scheduled_cloud as sc
from agent_friday.services import scheduler as s

HAIKU = "claude-haiku-4-5-20251001"


def _reset_answer():
    import agent_friday.core as core
    core._save_settings({"scheduled_cloud": sc.defaults()})


@pytest.fixture(autouse=True)
def _clean_store(friday_dir):
    for f in (s.SCHEDULES_FILE, s.RUNS_FILE):
        if f.exists():
            f.unlink()
    s._RUNNING.clear()
    _reset_answer()          # the temp home's settings outlive a test
    yield
    s._RUNNING.clear()
    _reset_answer()


def _consent(monkeypatch, *, allow, answered=None, **extra):
    cfg = sc.defaults()
    cfg.update(answered=allow if answered is None else answered, allow=allow, **extra)
    monkeypatch.setattr(sc, "settings", lambda: dict(cfg))
    return cfg


def _no_local_seat(monkeypatch):
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: None)


def _local_seat(monkeypatch, name="bonsai2:27b"):
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: name)


def _probe(monkeypatch, ref="sc_probe"):
    seen = {}

    def fn():
        seen["local_only"] = g.is_active()
        seen["pin"] = g.pinned_model()
        return {"changed": False, "summary": "probed"}
    monkeypatch.setitem(s.BUILTIN_TASKS, ref, {"fn": fn, "label": "Probe"})
    return seen


def _builtin(sid="sch_news_morning", ref="sc_probe"):
    return {"id": sid, "name": "Morning news",
            "task": {"kind": "builtin", "ref": ref, "local_only": True}}


def _heartbeat():
    return {"id": "sch_heartbeat", "name": "Hourly heartbeat",
            "trigger": "interval", "spec": {"every_minutes": 60},
            "notify": "status",
            "task": {"kind": "agent_prompt", "prompt": "check", "local_only": True,
                     "tools": ["query_calendar"]}}


def _capture_spawn(monkeypatch):
    from agent_friday.services import agent
    seen = {}

    def spawn(name, prompt, **kw):
        seen["model"] = kw.get("model")
        seen["pin"] = g.pinned_model()
        seen["local_only"] = g.is_active()
        return "task-1"
    monkeypatch.setattr(agent, "_spawn_task", spawn)
    monkeypatch.setattr(agent, "_task_snapshot",
                        lambda tid: {"status": "complete", "result": "NO CHANGE"})
    return seen


# ── The scheduler's decision ─────────────────────────────────────────────────

def test_a_serving_local_seat_wins_even_when_cloud_is_allowed(monkeypatch):
    _consent(monkeypatch, allow=True)
    _local_seat(monkeypatch)
    seen = _probe(monkeypatch)
    s._run_task(_builtin())
    assert seen == {"local_only": True, "pin": ""}


def test_a_serving_local_seat_runs_the_heartbeat_locally(monkeypatch):
    _consent(monkeypatch, allow=True)
    _local_seat(monkeypatch, "bonsai2:27b")
    seen = _capture_spawn(monkeypatch)
    s._run_task(_heartbeat())
    assert seen == {"model": "bonsai2:27b", "pin": "", "local_only": True}


def test_no_local_seat_and_allowed_runs_a_job_on_the_job_model(monkeypatch):
    _consent(monkeypatch, allow=True, job_model="claude-sonnet-5",
             heartbeat_model=HAIKU)
    _no_local_seat(monkeypatch)
    seen = _probe(monkeypatch)
    s._run_task(_builtin())
    # Pinned to the job model, and NOT inside the local-only guard.
    assert seen == {"local_only": False, "pin": "claude-sonnet-5"}


def test_no_local_seat_and_allowed_runs_the_heartbeat_on_the_heartbeat_model(monkeypatch):
    _consent(monkeypatch, allow=True, job_model="claude-sonnet-5",
             heartbeat_model=HAIKU)
    _no_local_seat(monkeypatch)
    seen = _capture_spawn(monkeypatch)
    s._run_task(_heartbeat())
    assert seen == {"model": HAIKU, "pin": HAIKU, "local_only": False}


def test_a_user_schedule_is_not_covered_by_the_answer(monkeypatch):
    """The answer covers the five built-in jobs it names, nothing else."""
    _consent(monkeypatch, allow=True)
    _no_local_seat(monkeypatch)
    _capture_spawn(monkeypatch)
    rec = dict(_heartbeat(), id="sch_mine")
    with pytest.raises(s.SkippedRun) as exc:
        s._run_task(rec)
    assert not isinstance(exc.value, s.PausedNoLocalModel)


@pytest.mark.parametrize("answered", [False, True])
def test_no_local_seat_and_not_allowed_is_a_paused_skip(monkeypatch, answered):
    _consent(monkeypatch, allow=False, answered=answered)
    _no_local_seat(monkeypatch)
    notices = []
    monkeypatch.setattr(sc, "notify_paused", lambda *a, **k: notices.append(1))
    spawned = _capture_spawn(monkeypatch)
    with pytest.raises(s.PausedNoLocalModel):
        s._run_task(_heartbeat())
    assert spawned == {}, "a paused heartbeat still spawned a task"

    def refuses():
        g.refuse_if_active("anthropic", "claude-opus-5-5")
    monkeypatch.setitem(s.BUILTIN_TASKS, "sc_refuse", {"fn": refuses, "label": "News"})
    with pytest.raises(s.PausedNoLocalModel):
        s._run_task(_builtin(ref="sc_refuse"))
    assert len(notices) == 2


def _run_and_wait(rec):
    before = len(s.run_history(rec["id"], limit=100))
    s.dispatch(rec, manual=True)
    for _ in range(100):
        if len(s.run_history(rec["id"], limit=100)) > before:
            return s.run_history(rec["id"], limit=1)[0]
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_paused_runs_leave_one_status_entry_and_no_failure_notices(monkeypatch):
    import agent_friday.notifications_engine as ne
    _consent(monkeypatch, allow=False, answered=False)
    _no_local_seat(monkeypatch)
    failures = []
    monkeypatch.setattr(s, "_notify_run", lambda *a, **k: failures.append(a))
    rec = s.register_schedule(_heartbeat())
    for _ in range(3):
        run = _run_and_wait(rec)
        assert run["status"] == "skipped", run
    assert failures == [], "a paused run was reported as a failure"
    stored = s.get_schedule("sch_heartbeat")
    assert stored["last_status"] == "skipped" and not stored.get("retry_pending")
    entries = [n for n in ne.list_notifications(limit=500)
               if n.get("dedupe_key") == sc.PAUSED_NOTICE_KEY]
    assert len(entries) == 1, entries
    assert "no local model" in entries[0]["body"]
    assert "Settings > Spending" in entries[0]["body"]
    assert entries[0]["read"] is True        # never bumps the unread badge


def test_allowing_cloud_clears_the_paused_notice(monkeypatch):
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(sc, "_known_model", lambda m: True)
    sc.notify_paused()
    sc.save({"allow": True})
    assert not [n for n in ne.list_notifications(limit=500)
                if n.get("dedupe_key") == sc.PAUSED_NOTICE_KEY]


# ── The heartbeat's cloud cadence ────────────────────────────────────────────

def test_the_cloud_heartbeat_waits_for_its_cloud_cadence(monkeypatch):
    _consent(monkeypatch, allow=True, heartbeat_every_minutes=240,
             heartbeat_from_hour=8, heartbeat_to_hour=20)
    _no_local_seat(monkeypatch)
    noon = datetime(2026, 9, 25, 12, 0)
    rec = dict(_heartbeat(), enabled=True)
    rec["last_run_ts"] = noon.timestamp() - 61 * 60
    assert not s._is_due(rec, noon), "ran hourly in the cloud"
    rec["last_run_ts"] = noon.timestamp() - 241 * 60
    assert s._is_due(rec, noon)
    night = datetime(2026, 9, 25, 22, 0)
    rec["last_run_ts"] = night.timestamp() - 600 * 60
    assert not s._is_due(rec, night), "the cloud heartbeat ran outside daytime"


def test_the_local_heartbeat_keeps_its_own_interval(monkeypatch):
    _consent(monkeypatch, allow=True)
    _local_seat(monkeypatch)
    noon = datetime(2026, 9, 25, 12, 0)
    rec = dict(_heartbeat(), enabled=True, last_run_ts=noon.timestamp() - 61 * 60)
    assert s._is_due(rec, noon)


# ── The heartbeat ships local-only ───────────────────────────────────────────

def test_the_seeded_heartbeat_is_local_only():
    s._seed_default_agent_schedules()
    assert s.get_schedule("sch_heartbeat")["task"]["local_only"] is True


def test_an_existing_heartbeat_without_an_answer_becomes_local_only():
    old = {k: v for k, v in _heartbeat().items()}
    old["task"] = {k: v for k, v in old["task"].items() if k != "local_only"}
    s._write_store([s._normalize_record(old, source="builtin")])
    s._seed_default_agent_schedules()
    assert s.get_schedule("sch_heartbeat")["task"]["local_only"] is True


def test_an_explicit_cloud_heartbeat_is_left_alone():
    rec = _heartbeat()
    rec["task"]["local_only"] = False
    s._write_store([s._normalize_record(rec, source="builtin")])
    s._seed_default_agent_schedules()
    assert s.get_schedule("sch_heartbeat")["task"]["local_only"] is False


# ── The pin at the transports ────────────────────────────────────────────────

def test_call_claude_sends_the_pinned_model_and_meters_it(monkeypatch, offline_calls):
    from agent_friday.services import cost_meter, model_router as mr
    metered = []
    monkeypatch.setattr(cost_meter, "meter",
                        lambda provider, model, usage, **k: metered.append(model))
    with g.cloud_pinned(HAIKU, "Morning news"):
        mr._call_claude([{"role": "user", "content": "hi"}], model="claude-opus-5-5")
    assert offline_calls["anthropic"][-1]["model"] == HAIKU
    assert metered == [HAIKU]


def test_call_claude_is_unchanged_without_a_pin(offline_calls):
    from agent_friday.services import model_router as mr
    mr._call_claude([{"role": "user", "content": "hi"}], model="claude-opus-5-5")
    assert offline_calls["anthropic"][-1]["model"] == "claude-opus-5-5"


def test_openrouter_gets_the_gateway_spelling_at_the_same_price():
    from agent_friday.services import cost_meter
    with g.cloud_pinned(HAIKU):
        m = g.apply_pin("openrouter", "anthropic/claude-opus-5.5")
    assert m == "anthropic/claude-haiku-4.5"
    assert cost_meter.price_for(m) == cost_meter.PRICING["claude-haiku-4-5"]


def test_a_provider_that_cannot_serve_the_pin_is_refused():
    with g.cloud_pinned(HAIKU, "Heartbeat"):
        with pytest.raises(g.CloudRefused) as exc:
            g.apply_pin("openai", "gpt-4o")
        assert g.apply_pin("ollama-local", "bonsai2:27b") == "bonsai2:27b"
    assert "Heartbeat" in str(exc.value)
    assert g.apply_pin("openai", "gpt-4o") == "gpt-4o"      # no pin, no change


def test_the_pin_reaches_the_spawned_task_thread(monkeypatch):
    from agent_friday.services import agent
    with g.cloud_pinned(HAIKU, "Heartbeat"):
        tid = agent._spawn_task("pin probe", "x", runner=lambda t: {
            "status": "complete", "result": ""})
    assert agent.TASKS[tid]["cloud_pin"] == {"model": HAIKU, "label": "Heartbeat"}

    seen = {}
    monkeypatch.setattr(agent, "_task_worker_untraced",
                        lambda *a, **k: seen.setdefault("pin", g.pinned_model()))
    agent._task_worker(tid, "pin probe", "x")
    assert seen["pin"] == HAIKU
    assert g.pinned_model() == ""


# ── The estimate ─────────────────────────────────────────────────────────────

def test_the_settings_default_is_unanswered_and_not_allowed():
    import agent_friday.core as core
    from agent_friday.services import cost_meter
    d = core.DEFAULT_SETTINGS["scheduled_cloud"]
    assert d["answered"] is False and d["allow"] is False and d["at"] is None
    assert d["heartbeat_model"] == HAIKU and d["job_model"] == HAIKU
    assert HAIKU in cost_meter.PRICING
    assert d["heartbeat_every_minutes"] >= 120
    cfg = sc.settings()
    assert cfg["answered"] is False and cfg["allow"] is False


def test_allow_without_an_answer_is_not_allowed(monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"scheduled_cloud": {"allow": True}})
    assert sc.settings()["allow"] is False


def test_the_estimate_prices_each_job_from_its_cadence_and_model():
    from agent_friday.services import cost_meter
    cfg = sc.defaults()
    cfg.update(answered=True, allow=True)
    est = sc.estimate(cfg, records=[])
    jobs = {j["id"]: j for j in est["jobs"]}
    assert set(jobs) == {sid for sid, _ in sc.JOBS}
    p = cost_meter.PRICING[HAIKU]
    fp = jobs["sch_news_morning"]
    assert fp["input_tokens_per_run"] == sc.FRONT_PAGE_PROMPT_TOKENS
    assert fp["usd_per_run"] == pytest.approx(
        sc.FRONT_PAGE_PROMPT_TOKENS / 1000 * p["in"] + 2000 / 1000 * p["out"], abs=1e-4)
    assert fp["runs_per_month"] == pytest.approx(sc.DAYS_PER_MONTH)
    hb = jobs["sch_heartbeat"]
    # 08:00-20:00 every 4 hours: 3 runs a day, not 24.
    assert est["heartbeat_runs_per_day"] == 3
    assert hb["runs_per_month"] == pytest.approx(3 * sc.DAYS_PER_MONTH, abs=0.1)
    # The heartbeat's payload includes its real tool schemas.
    assert hb["input_tokens_per_run"] > sc.HEARTBEAT_ROUNDS * sc.SYSTEM_PROMPT_TOKENS
    assert est["total_usd_per_month"] == pytest.approx(
        sum(j["usd_per_month"] for j in est["jobs"]), abs=0.05)
    assert all(j["model_label"] == "Claude Haiku 4.5" for j in est["jobs"])
    assert 0 < est["total_usd_per_month"] < 20


def test_a_dearer_model_and_a_faster_heartbeat_cost_more():
    base = sc.defaults()
    cheap = sc.estimate(base, records=[])["total_usd_per_month"]
    dear = sc.estimate(dict(base, job_model="claude-opus-5-5"),
                       records=[])["total_usd_per_month"]
    busy = sc.estimate(dict(base, heartbeat_every_minutes=60),
                       records=[])["total_usd_per_month"]
    assert dear > cheap and busy > cheap


def test_a_job_switched_off_is_listed_but_not_counted():
    cfg = sc.defaults()
    off = [{"id": "sch_daily_creation", "enabled": False, "trigger": "idle_daily"}]
    est = sc.estimate(cfg, records=off)
    job = next(j for j in est["jobs"] if j["id"] == "sch_daily_creation")
    assert job["enabled"] is False
    assert est["total_usd_per_month"] == pytest.approx(
        sum(j["usd_per_month"] for j in est["jobs"] if j["enabled"]), abs=0.05)


# ── Settings > Spending ──────────────────────────────────────────────────────

def test_the_spending_route_shows_the_answer_models_and_estimate(client):
    resp = client.get("/api/costs/scheduled-cloud")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    d = resp.get_json()
    assert d["status"] == "ok"
    assert d["settings"]["answered"] is False and d["settings"]["allow"] is False
    assert len(d["estimate"]["jobs"]) == 5
    assert "total_usd_per_month" in d["estimate"]
    assert 240 in d["cadences"]


def test_the_spending_route_records_the_answer_without_touching_the_rest(client):
    from agent_friday.core import _load_settings_raw
    before = dict(_load_settings_raw())
    resp = client.post("/api/costs/scheduled-cloud", data=json.dumps({"allow": True}),
                       content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    d = resp.get_json()
    assert d["settings"]["answered"] is True and d["settings"]["allow"] is True
    assert d["settings"]["at"]
    after = _load_settings_raw()
    assert after["scheduled_cloud"]["allow"] is True
    for key in before:
        if key != "scheduled_cloud":
            assert after[key] == before[key], key

    resp = client.post("/api/costs/scheduled-cloud",
                       data=json.dumps({"heartbeat_every_minutes": 120}),
                       content_type="application/json")
    d = resp.get_json()
    assert d["settings"]["heartbeat_every_minutes"] == 120
    assert d["settings"]["allow"] is True, "a cadence change dropped the answer"
    assert d["estimate"]["heartbeat_runs_per_day"] == 6

    resp = client.post("/api/costs/scheduled-cloud", data=json.dumps({"allow": False}),
                       content_type="application/json")
    assert resp.get_json()["settings"]["allow"] is False


@pytest.mark.parametrize("body", [{"heartbeat_every_minutes": 7},
                                  {"job_model": "no-such-model"}])
def test_the_spending_route_refuses_nonsense(client, body):
    resp = client.post("/api/costs/scheduled-cloud", data=json.dumps(body),
                       content_type="application/json")
    assert resp.status_code == 400
