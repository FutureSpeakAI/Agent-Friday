"""One source of truth for where routine background work runs.

The owner's decision: choosing Cloud only IS consent for News (the front
pages, briefings, the weekly digest and editorial), podcasts and the heartbeat
to use the cloud. Only a CHOSEN cloud_only counts: it is also the factory
default every fresh install seeds, so the consent needs the
`model_routing.mode_chosen` record an owner routing change writes. In every
other case (a local mode, cloud_preferred, an unchosen cloud_only, daily
creation) the jobs stay local, and reach the cloud only by the owner's
explicit `scheduled_cloud.allow` (daily creation and the heartbeat only) with
no local model serving.

`cloud_intent.background_route` is the one reader; the scheduler, the News
engine, the podcast writer and the local-only guard ask it. A local-only scope
opened for PRIVACY (compaction, meeting capture, a private podcast) is local
in every mode.

Every test runs on synthetic settings; no model, seat or provider is reached.
"""
from __future__ import annotations

import logging
from datetime import datetime

import pytest

from agent_friday.services import cloud_intent
from agent_friday.services import local_only_guard as g
from agent_friday.services import scheduled_cloud as sc

HAIKU = "claude-haiku-4-5-20251001"
JOB_MODEL = "claude-sonnet-5"

MODES = ("cloud_only", "cloud_preferred", "local_preferred", "local_only")


def _mode(monkeypatch, mode, *, chosen=True):
    """The owner's stored routing mode, as cloud_intent reads it, and (when
    ``chosen``) the record an owner routing change leaves for it."""
    monkeypatch.setattr(cloud_intent, "_stored_mode", lambda: mode)
    monkeypatch.setattr(cloud_intent, "_chosen_mode",
                        lambda: (mode or "") if chosen else "")


def _answer(monkeypatch, *, allow=False, follows=True, job_model=JOB_MODEL,
            heartbeat_model=HAIKU):
    cfg = sc.defaults()
    cfg.update(answered=allow, allow=allow, heartbeat_model=heartbeat_model,
               job_model=job_model, routine_follows_cloud_mode=follows)
    monkeypatch.setattr(sc, "settings", lambda: dict(cfg))
    return cfg


@pytest.fixture
def cloud_only(monkeypatch):
    _mode(monkeypatch, "cloud_only")
    _answer(monkeypatch, allow=False)


# ── the truth table ──────────────────────────────────────────────────────────

def _expected(mode, serving, allow, covered, released):
    if mode == "cloud_only" and released:
        return "cloud"
    if serving:
        return "local"
    if covered and allow:
        return "cloud"
    return "skip"


@pytest.mark.parametrize("job,covered,released", [
    ("sch_heartbeat", True, True),
    ("sch_daily_creation", True, False),     # not named in the decision
    ("sch_news_morning", False, True),
    ("sch_weekly_editorial", False, True),
    ("podcast", False, True),
    ("news", False, True)])
@pytest.mark.parametrize("allow", [False, True])
@pytest.mark.parametrize("serving", [False, True])
@pytest.mark.parametrize("mode", MODES)
def test_the_truth_table(monkeypatch, mode, serving, allow, job, covered, released):
    _mode(monkeypatch, mode)
    _answer(monkeypatch, allow=allow)
    r = cloud_intent.background_route(job, local_serving="seat:7b" if serving else False)
    want = _expected(mode, serving, allow, covered, released)
    assert r.route == want, (mode, serving, allow, job, r)
    assert r.reason, "every answer says why"
    if want == "cloud":
        assert r.model == (HAIKU if job == "sch_heartbeat" else JOB_MODEL)
    if want == "local":
        assert r.local_model == "seat:7b"


def test_a_chosen_cloud_only_never_probes_the_local_seat(monkeypatch):
    _mode(monkeypatch, "cloud_only")
    _answer(monkeypatch)
    r = cloud_intent.background_route(
        "news", local_serving=lambda: pytest.fail("cloud only probed the seat"))
    assert r.route == "cloud"


def test_cloud_preferred_is_not_the_consent(monkeypatch):
    """Red on 4bcb47f40: cloud_preferred released every routine job too."""
    _mode(monkeypatch, "cloud_preferred")
    _answer(monkeypatch, allow=False)
    assert cloud_intent.background_route("news", local_serving=False).route == "skip"
    assert cloud_intent.background_route("news", local_serving="seat:7b").route == "local"
    with g.local_only("Front Page", kind="routine", job="news") as scope:
        assert not scope.released and g.is_active()


def test_daily_creation_is_not_released_by_cloud_only(monkeypatch, caplog):
    """Red on 4bcb47f40: daily creation (not named in the decision) went to
    the cloud under Cloud only."""
    _mode(monkeypatch, "cloud_only")
    _answer(monkeypatch, allow=False)
    assert cloud_intent.background_route("sch_daily_creation",
                                         local_serving="seat:7b").route == "local"
    assert cloud_intent.background_route("sch_daily_creation",
                                         local_serving=False).route == "skip"
    _answer(monkeypatch, allow=True)          # its explicit opt-in still works
    r = cloud_intent.background_route("sch_daily_creation", local_serving=False)
    assert r.route == "cloud" and r.model == JOB_MODEL


# ── an empty cloud model never releases a job unpinned ───────────────────────

@pytest.mark.parametrize("job", ["news", "sch_heartbeat"])
def test_no_cloud_model_set_is_a_skip_that_says_so(monkeypatch, job):
    """Red on 4bcb47f40: the route was cloud with model "" and the scope was
    released with no pin."""
    _mode(monkeypatch, "cloud_only")
    _answer(monkeypatch, job_model="", heartbeat_model="  ")
    r = cloud_intent.background_route(job, local_serving=False)
    assert r.route == "skip" and r.model == ""
    assert "no cloud model is set" in r.reason
    with g.local_only("Routine", kind="routine", job=job) as scope:
        assert not scope.released and g.is_active() and g.pinned_model() == ""


def test_the_opt_in_with_no_model_is_a_skip_too(monkeypatch):
    _mode(monkeypatch, "local_preferred")
    _answer(monkeypatch, allow=True, heartbeat_model="")
    r = cloud_intent.background_route("sch_heartbeat", local_serving=False)
    assert r.route == "skip" and "no cloud model is set" in r.reason


def test_the_guard_never_releases_without_a_pin(monkeypatch):
    """Whatever the reader says, a routine scope is released only with a
    non-empty model to pin the run to."""
    monkeypatch.setattr(cloud_intent, "background_route",
                        lambda *a, **k: cloud_intent.BackgroundRoute(
                            "cloud", "synthetic", "cloud_only", ""))
    with g.local_only("Routine", kind="routine", job="news") as scope:
        assert not scope.released and g.is_active()
        with pytest.raises(g.CloudRefused):
            g.refuse_if_active("anthropic", "claude-opus-5-5")


def test_routine_follows_cloud_mode_false_keeps_the_old_local_rule(monkeypatch):
    _mode(monkeypatch, "cloud_only")
    _answer(monkeypatch, follows=False)
    assert cloud_intent.background_route("news", local_serving=False).route == "skip"
    assert cloud_intent.background_route("news", local_serving="seat:7b").route == "local"


def test_the_reader_ignores_an_open_local_only_scope(monkeypatch):
    """The owner's mode, not the guard-shadowed one: inside a scope
    `routing_mode()` says local_only, which is the scope's effect."""
    _mode(monkeypatch, "cloud_only")
    _answer(monkeypatch)
    with g.local_only("context compaction"):
        assert cloud_intent.routing_mode() == "local_only"
        assert cloud_intent.background_route("news", local_serving=False).route == "cloud"


def test_the_spending_panel_says_the_mode_sends_them_to_the_cloud(monkeypatch, cloud_only):
    assert sc.view()["cloud_by_mode"] is True
    _mode(monkeypatch, "local_only")
    assert sc.view()["cloud_by_mode"] is False


# ── the local-only guard: routine scopes released, private ones never ────────

def test_a_news_run_under_cloud_only_does_not_refuse_the_cloud(cloud_only, caplog):
    """Red on main: news_engine.local_news_run is hard-coded local-only, so
    every cloud call a News run makes under Cloud only is refused."""
    from agent_friday.services import news_engine as ne
    caplog.set_level(logging.INFO, logger="friday.local_only")
    with ne.local_news_run("Front Page"):
        assert not g.is_active()
        g.refuse_if_active("anthropic", "claude-opus-5-5")          # must not raise
        tok = cloud_intent.require("anthropic", "Front Page", "claude-opus-5-5")
        assert tok.intent == "scheduled_cloud"
        # Pinned to the routine job model: no escalation to a dearer one.
        assert g.pinned_model() == JOB_MODEL
        assert g.apply_pin("anthropic", "claude-opus-5-5") == JOB_MODEL
    assert not g.is_active() and g.pinned_model() == ""
    assert any("released" in r.getMessage() and "Front Page" in r.getMessage()
               for r in caplog.records), "the release is not in friday.log"


def test_a_private_scope_still_refuses_under_cloud_only(cloud_only):
    with g.local_only("context compaction"):
        assert g.is_active()
        with pytest.raises(g.CloudRefused):
            g.refuse_if_active("anthropic", "claude-opus-5-5")
        with pytest.raises(cloud_intent.NoCloudIntent):
            cloud_intent.require("anthropic", "context compaction", "claude-opus-5-5")


def test_a_routine_scope_inside_a_private_one_stays_local(cloud_only):
    with g.local_only("meeting capture"):
        with g.local_only("Podcast", kind="routine", job="podcast") as scope:
            assert not scope.released and g.is_active()
            with pytest.raises(g.CloudRefused):
                g.refuse_if_active("anthropic", "claude-opus-5-5")


def test_a_routine_scope_stays_local_in_a_local_mode(monkeypatch):
    _mode(monkeypatch, "local_preferred")
    _answer(monkeypatch, allow=True)
    with g.local_only("Podcast", kind="routine", job="podcast") as scope:
        assert not scope.released and g.is_active()


def test_the_read_aloud_keeps_this_pcs_voice_under_cloud_only(cloud_only):
    from agent_friday.services import news_engine as ne
    with ne.local_news_run("Front Page read-aloud", kind="private"):
        assert g.is_active()


# ── the News engine ──────────────────────────────────────────────────────────

def test_a_news_routine_under_cloud_only_with_no_seat_writes_on_the_cloud(
        monkeypatch, tmp_path, cloud_only):
    """Red on main: the routine runs inside the local-only guard and its
    editorial is refused."""
    import agent_friday.core as core
    from agent_friday.services import news_engine as ne
    from agent_friday.services import scheduler

    class _Stop(Exception):
        pass

    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ne, "FRONT_PAGES_DIR", tmp_path / "front_pages")
    monkeypatch.setattr(ne, "WEEKLY_DIGESTS_DIR", tmp_path / "front_pages" / "weekly")
    monkeypatch.setattr(ne, "EDITORIALS_DIR", tmp_path / "editorials")
    monkeypatch.setattr(ne, "_notif_engine", None)
    monkeypatch.setattr(scheduler, "_resolve_local_seat", lambda: None)
    seen = []

    def probe(*a, **k):
        seen.append((g.is_active(), g.pinned_model()))
        raise _Stop()
    monkeypatch.setattr(ne, "_gather_weekly_editions", probe)
    with pytest.raises(_Stop):
        ne._generate_weekly_digest()
    assert seen == [(False, JOB_MODEL)]


# ── the scheduler ────────────────────────────────────────────────────────────

def _probe_builtin(monkeypatch, ref="mt_probe"):
    from agent_friday.services import scheduler as s
    seen = {}

    def fn():
        seen["local_only"] = g.is_active()
        seen["pin"] = g.pinned_model()
        g.refuse_if_active("anthropic", "claude-opus-5-5")
        return {"ok": True}
    monkeypatch.setitem(s.BUILTIN_TASKS, ref, {"fn": fn, "label": "Front Page"})
    return seen


def test_a_news_builtin_under_cloud_only_runs_on_the_cloud_not_waiting(monkeypatch, cloud_only):
    """Red on main: the scheduler skips (SeatWaiting) a local_only routine
    when no local seat serves, whatever the routing mode."""
    from agent_friday.services import scheduler as s
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: None)
    seen = _probe_builtin(monkeypatch)
    out = s._run_task({"id": "sch_news_morning", "name": "Morning news",
                       "task": {"kind": "builtin", "ref": "mt_probe", "local_only": True}})
    assert out == {"ok": True}
    assert seen == {"local_only": False, "pin": JOB_MODEL}


def test_an_owners_own_local_only_job_stays_local_under_cloud_only(monkeypatch, cloud_only):
    from agent_friday.services import build_hours
    from agent_friday.services import scheduler as s
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: None)
    monkeypatch.setattr(build_hours, "is_active", lambda *a, **k: False)
    seen = _probe_builtin(monkeypatch, "mt_probe2")
    with pytest.raises(s.SkippedRun):
        s._run_task({"id": "sch_mine", "name": "Mine",
                     "task": {"kind": "builtin", "ref": "mt_probe2", "local_only": True}})
    assert seen.get("local_only") is True


def test_a_catch_up_under_cloud_only_does_not_wait_for_the_seat(monkeypatch, cloud_only):
    """Red on main: a waiting run is due only when a local seat serves."""
    from agent_friday.services import build_hours
    from agent_friday.services import scheduler as s
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: None)
    monkeypatch.setattr(build_hours, "is_active", lambda *a, **k: False)
    now = s._now_central()
    rec = {"id": "sch_news_morning", "enabled": True, "trigger": "daily",
           "spec": {"hour": 0},
           "catch_up": {"date": now.strftime("%Y-%m-%d"), "reason": "no seat",
                        "since": now.timestamp() - 600},
           "task": {"kind": "builtin", "ref": "news_morning", "local_only": True}}
    assert s._is_due_on_schedule(rec, now) is True


def test_a_catch_up_in_a_local_mode_still_waits_for_the_seat(monkeypatch):
    from agent_friday.services import build_hours
    from agent_friday.services import scheduler as s
    _mode(monkeypatch, "local_preferred")
    _answer(monkeypatch)
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: None)
    monkeypatch.setattr(build_hours, "is_active", lambda *a, **k: False)
    now = s._now_central()
    rec = {"id": "sch_news_morning", "enabled": True, "trigger": "daily",
           "spec": {"hour": 0},
           "catch_up": {"date": now.strftime("%Y-%m-%d"), "reason": "no seat",
                        "since": now.timestamp() - 600},
           "task": {"kind": "builtin", "ref": "news_morning", "local_only": True}}
    assert s._is_due_on_schedule(rec, now) is False


def _heartbeat():
    return {"id": "sch_heartbeat", "name": "Hourly heartbeat",
            "trigger": "interval", "spec": {"every_minutes": 60},
            "notify": "status",
            "task": {"kind": "agent_prompt", "prompt": "check", "local_only": True,
                     "tools": ["query_calendar"]}}


def test_the_heartbeat_under_cloud_only_runs_on_its_cloud_model(monkeypatch, cloud_only):
    """Red on main: with no local seat and no Settings > Spending answer the
    heartbeat is a PausedNoLocalModel skip, though the owner chose Cloud only."""
    from agent_friday.services import agent
    from agent_friday.services import scheduler as s
    # The full-agent path (heartbeat.precheck off). The pre-check path's seat
    # under Cloud only is pinned in test_heartbeat_precheck.py.
    try:
        from agent_friday.services import heartbeat as hb
        monkeypatch.setattr(hb, "_settings", lambda: {"precheck": False,
                                                      "calendar_lookahead_minutes": 120})
    except ImportError:
        pass
    monkeypatch.setattr(s, "_resolve_local_seat", lambda: None)
    seen = {}

    def spawn(name, prompt, **kw):
        seen.update(model=kw.get("model"), pin=g.pinned_model(), local_only=g.is_active())
        return "task-1"
    monkeypatch.setattr(agent, "_spawn_task", spawn)
    monkeypatch.setattr(agent, "_task_snapshot",
                        lambda tid: {"status": "complete", "result": "NO CHANGE"})
    s._run_task(_heartbeat())
    assert seen == {"model": HAIKU, "pin": HAIKU, "local_only": False}


def test_the_cloud_heartbeat_keeps_its_cloud_cadence_under_cloud_only(monkeypatch, cloud_only):
    from agent_friday.services import scheduler as s
    noon = datetime(2026, 9, 25, 12, 0)
    rec = dict(_heartbeat(), enabled=True, last_run_ts=noon.timestamp() - 61 * 60)
    assert not s._is_due_on_schedule(rec, noon), "an hourly cloud heartbeat"
    rec["last_run_ts"] = noon.timestamp() - 241 * 60
    assert s._is_due_on_schedule(rec, noon)


# ── podcasts ─────────────────────────────────────────────────────────────────

@pytest.fixture
def pod_home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    return tmp_path


def test_the_cloud_writer_budget_has_room(monkeypatch):
    from agent_friday.services import podcast_engine as pe
    for length in pe.LENGTHS:
        for stage in ("outline", "chapter", "revise"):
            local = pe.WRITER_BUDGET[length][stage]
            assert pe.cloud_writer_tokens(local) >= max(3 * local, 8000)


def test_a_podcast_under_cloud_only_is_written_by_the_cloud_with_room(
        monkeypatch, pod_home, cloud_only, caplog):
    """Red on main: the status said "the local model is writing" whatever
    wrote it, the writer only ever called the local seat, and an outline's
    cloud JSON was cut off at 2,000 tokens with nothing in the log."""
    from agent_friday.services import local_call, model_router, scheduler
    from agent_friday.services import podcast_engine as pe
    from agent_friday.services import podcast_render as render
    caplog.set_level(logging.WARNING, logger="friday.podcast")
    monkeypatch.setattr(scheduler, "_resolve_local_seat", lambda: None)
    monkeypatch.setattr(local_call, "call_json",
                        lambda *a, **k: pytest.fail("the local seat was called"))
    sent = {}

    def generate(messages, system=None, model=None, max_tokens=None, **kw):
        sent.update(model=model, max_tokens=max_tokens, pin=g.pinned_model(),
                    local_only=g.is_active())
        return '{"title": "A day", "chapters": [{"title": "Cold open", "sour'
    monkeypatch.setattr(model_router, "_generate_text", generate)

    ep = pe.create([{"kind": "url", "url": "https://news.example/a"}], origin="routine")
    assert ep.get("privacy") != "private"
    monkeypatch.setattr(pe, "_gather", lambda e: [
        {"sid": "S1", "title": "Council passes budget", "kind": "url",
         "text": "The vote was 7-2.", "url": "https://news.example/a", "private": False}])
    seen = {}

    def write_script(e, docs, progress=None, stop=None):
        seen["stage"] = pe.load(e["id"])["stage_detail"]
        pe._llm_json("system", "plan it",
                     max_tokens=pe.WRITER_BUDGET["standard"]["outline"])
    monkeypatch.setattr(pe, "write_script", write_script)

    with pytest.raises(render.RenderError) as exc:
        pe._write(ep["id"], ep, "", lambda: False)
    assert seen["stage"] == "the cloud model is writing"
    assert sent["max_tokens"] >= 8000
    assert sent["pin"] == JOB_MODEL and sent["local_only"] is False
    assert exc.value.code == "writer_failed"
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("not usable JSON" in m and "chars" in m for m in warned), warned


def test_a_private_podcast_is_written_locally_under_cloud_only(monkeypatch, cloud_only):
    from agent_friday.services import podcast_engine as pe
    assert pe.writer_route({"privacy": "private"}) is None
    assert pe.writer_is_cloud(pe.writer_route({"privacy": "public"}))


def test_the_private_summary_stays_local_under_cloud_only(monkeypatch, cloud_only):
    """`_llm_json` outside an episode's writer scope (the private summary,
    Discuss) is the local seat in every mode."""
    from agent_friday.services import local_call, scheduler
    from agent_friday.services import podcast_engine as pe
    monkeypatch.setattr(scheduler, "_resolve_local_seat", lambda: "seat:7b")
    seen = []

    def call_json(system, user, model, **kw):
        seen.append((model, g.is_active()))
        return {"ok": True}
    monkeypatch.setattr(local_call, "call_json", call_json)
    out, model = pe._llm_json("s", "u")
    assert out == {"ok": True} and seen == [("seat:7b", True)]


@pytest.mark.parametrize("blank", ["", None])
def test_an_unchosen_mode_is_not_consent(monkeypatch, blank):
    """A blank mode reads as cloud_only elsewhere, but nobody chose it, and the
    consent is the choosing (owner decision 2026-10-15): the routine jobs keep
    the local-mode rows until setup asks."""
    _mode(monkeypatch, blank)
    _answer(monkeypatch, allow=False)
    assert cloud_intent.background_route("news", local_serving=False).route == "skip"
    assert cloud_intent.background_route("news", local_serving="seat:7b").route == "local"
    _answer(monkeypatch, allow=True)
    assert cloud_intent.background_route("sch_heartbeat", local_serving=False).route == "cloud"


# ── consent needs the owner's CHOICE, read through the real settings path ────

@pytest.fixture
def real_settings(tmp_path, monkeypatch):
    """A fresh install in a temp home: the real loader seeds DEFAULT_SETTINGS
    (model_routing.mode "cloud_only") into settings.json on first read."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setitem(core.NETWORK_STATE, "status", "online")
    core._invalidate_settings_cache()
    yield core
    core._invalidate_settings_cache()


def test_a_fresh_install_is_not_consent(real_settings):
    """Red on 4bcb47f40: the seeded factory cloud_only is not "unset", so
    every fresh install counted as consent without anyone choosing."""
    core = real_settings
    seeded = core._load_settings_raw()
    assert (core.SETTINGS_FILE).exists()
    assert seeded["model_routing"]["mode"] == "cloud_only"
    assert "mode_chosen" not in seeded["model_routing"]
    assert "mode_chosen" not in core.DEFAULT_SETTINGS["model_routing"]
    assert cloud_intent.mode_was_chosen() is False
    r = cloud_intent.background_route("news", local_serving=False)
    assert r.route == "skip" and "not one you chose" in r.reason, r.reason
    assert cloud_intent.background_route("news", local_serving="seat:7b").route == "local"
    with g.local_only("Front Page", kind="routine", job="news") as scope:
        assert not scope.released and g.is_active()


def test_the_owner_choosing_cloud_only_is_consent(real_settings):
    core = real_settings
    core._load_settings_raw()                               # seed the defaults
    core._save_settings({"model_routing": {"mode": "cloud_only"}},
                        owner_routing_change=True)
    mr = core._load_settings_raw()["model_routing"]
    assert mr["mode_chosen"] == "cloud_only" and mr.get("mode_chosen_at")
    assert cloud_intent.mode_was_chosen() is True
    r = cloud_intent.background_route("news", local_serving="seat:7b")
    assert r.route == "cloud" and r.model == core.DEFAULT_SETTINGS["scheduled_cloud"]["job_model"]
    with g.local_only("Front Page", kind="routine", job="news") as scope:
        assert scope.released and not g.is_active() and g.pinned_model() == r.model
    # Daily creation is not part of the consent.
    assert cloud_intent.background_route("sch_daily_creation",
                                         local_serving="seat:7b").route == "local"


def test_only_an_owner_routing_change_writes_the_record(real_settings):
    core = real_settings
    core._load_settings_raw()
    # A generic save (no owner action) that carries the record has it stripped.
    core._save_settings({"model_routing": {"mode_chosen": "cloud_only",
                                           "mode_chosen_at": "2026-01-01T00:00:00"}})
    assert "mode_chosen" not in core._load_settings_raw()["model_routing"]
    assert cloud_intent.mode_was_chosen() is False
    # The owner chooses a local mode; the record names that mode, not cloud_only.
    core._save_settings({"model_routing": {"mode": "local_preferred",
                                           "mode_chosen": "cloud_only"}},
                        owner_routing_change=True)
    assert core._load_settings_raw()["model_routing"]["mode_chosen"] == "local_preferred"
    assert cloud_intent.background_route("news", local_serving=False).route == "skip"
    # A later unrelated save keeps the record (model_routing is deep-merged).
    core._save_settings({"model_routing": {"vault_local_only": True}})
    assert core._load_settings_raw()["model_routing"]["mode_chosen"] == "local_preferred"


def test_a_mode_the_owner_did_not_choose_is_not_consent(real_settings):
    """A settings file that says cloud_only over a record of another choice
    (a hand edit, a restore) is not the owner choosing cloud_only."""
    import json
    core = real_settings
    data = dict(core.DEFAULT_SETTINGS)
    data["model_routing"] = dict(data["model_routing"], mode="cloud_only",
                                 mode_chosen="local_only")
    core.SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")
    core._invalidate_settings_cache()
    assert cloud_intent.mode_was_chosen() is False
    assert cloud_intent.background_route("news", local_serving=False).route == "skip"


def test_a_degraded_settings_read_is_not_consent(real_settings):
    core = real_settings
    core._load_settings_raw()
    core._save_settings({"model_routing": {"mode": "cloud_only"}},
                        owner_routing_change=True)
    assert cloud_intent.mode_was_chosen() is True
    core.SETTINGS_FILE.write_text("{ not json", encoding="utf-8")
    core._invalidate_settings_cache()
    assert core._is_degraded_settings(core._load_settings_raw())
    assert cloud_intent.mode_was_chosen() is False
    assert cloud_intent.background_route("news", local_serving=False).route == "skip"
