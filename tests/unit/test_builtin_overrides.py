"""The owner's edits to a built-in scheduled job are versioned, logged, reversible and kept across an app update
(services/builtin_overrides, services/scheduler, services/schedule_tools; Scheduler, item 28b). Synthetic jobs only."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import builtin_overrides as bo
from agent_friday.services import schedule_tools, scheduler


# ── the store on its own ──────────────────────────────────────────────────────

DEFAULTS = {"trigger": "daily", "spec": {"hour": 7, "minute": 0}, "enabled": True, "timeout_seconds": 1800,
            "retry": {"max": 2, "backoff_seconds": 300}, "notify": "on_complete", "task": {"kind": "builtin", "ref": "x"}}


def _rec(**kw):
    return dict(DEFAULTS, id="sch_x", source="builtin", **kw)


def test_an_edit_is_a_version_with_before_and_after_and_only_differences_from_the_default_are_the_owners(tmp_path):
    path = tmp_path / "o.json"
    entry = bo.record_edit("sch_x", _rec(), _rec(spec={"hour": 5, "minute": 30}), DEFAULTS, "owner", path)
    assert entry["version"] == 1 and entry["kind"] == "edit" and entry["by"] == "owner"
    assert entry["before"]["spec"] == {"hour": 7, "minute": 0} and entry["after"]["spec"] == {"hour": 5, "minute": 30}
    assert bo.owner_fields("sch_x", path) == {"spec": {"hour": 5, "minute": 30}}
    bo.record_edit("sch_x", _rec(spec={"hour": 5, "minute": 30}), _rec(spec={"hour": 5, "minute": 30}, enabled=False),
                   DEFAULTS, "owner", path)
    assert [e["version"] for e in bo.history("sch_x", path)] == [1, 2]
    assert set(bo.owner_fields("sch_x", path)) == {"spec", "enabled"}


def test_an_edit_that_changes_nothing_is_not_a_version(tmp_path):
    assert bo.record_edit("sch_x", _rec(), _rec(), DEFAULTS, "owner", tmp_path / "o.json") is None
    assert bo.history("sch_x", tmp_path / "o.json") == []


def test_restore_default_goes_back_to_the_shipped_fields_and_is_itself_a_version(tmp_path):
    path = tmp_path / "o.json"
    edited = _rec(spec={"hour": 5, "minute": 0}, enabled=False)
    bo.record_edit("sch_x", _rec(), edited, DEFAULTS, "owner", path)
    shipped = bo.restore("sch_x", edited, DEFAULTS, "owner", path)
    assert shipped["spec"] == {"hour": 7, "minute": 0} and shipped["enabled"] is True
    assert bo.owner_fields("sch_x", path) == {} and bo.history("sch_x", path)[-1]["kind"] == "restore_default"


def test_revert_steps_back_one_edit_at_a_time(tmp_path):
    path = tmp_path / "o.json"
    one = _rec(spec={"hour": 5, "minute": 0})
    two = _rec(spec={"hour": 4, "minute": 0})
    bo.record_edit("sch_x", _rec(), one, DEFAULTS, "owner", path)
    bo.record_edit("sch_x", one, two, DEFAULTS, "owner", path)
    assert bo.revert("sch_x", two, DEFAULTS, "owner", path)["spec"] == {"hour": 5, "minute": 0}
    assert bo.history("sch_x", path)[-1]["kind"] == "revert"
    assert bo.owner_fields("sch_x", path)["spec"] == {"hour": 5, "minute": 0}
    assert bo.revert("sch_x", one, DEFAULTS, "owner", path) is not None          # the first edit is still there to undo
    assert bo.revert("sch_none", one, DEFAULTS, "owner", path) is None


def test_an_edit_made_before_edits_were_versioned_is_adopted_as_the_owners_once(tmp_path):
    path = tmp_path / "o.json"
    assert bo.adopt("sch_x", _rec(), DEFAULTS, path) is False                       # equals the default: nothing to adopt
    legacy = _rec(spec={"hour": 5, "minute": 0})
    assert bo.adopt("sch_x", legacy, DEFAULTS, path) is True and bo.adopt("sch_x", legacy, DEFAULTS, path) is False
    assert bo.history("sch_x", path)[0]["kind"] == "adopted" and bo.owner_fields("sch_x", path)["spec"]["hour"] == 5


def test_reapply_puts_the_owners_fields_back_on_built_in_records_only(tmp_path):
    path = tmp_path / "o.json"
    bo.record_edit("sch_x", _rec(), _rec(spec={"hour": 5, "minute": 0}), DEFAULTS, "owner", path)
    seeded, mine = [_rec(), dict(_rec(), id="sch_y")], dict(_rec(), id="sch_x", source="user")
    assert bo.reapply(seeded + [mine], path) == 1
    assert seeded[0]["spec"]["hour"] == 5 and seeded[1]["spec"]["hour"] == 7 and mine["spec"]["hour"] == 7


# ── the scheduler on a scratch store ─────────────────────────────────────────────

@pytest.fixture
def sched(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(bo, "_file", lambda: tmp_path / "overrides.json")
    monkeypatch.setattr(scheduler, "BUILTIN_TASKS", {"sample_job": {
        "fn": lambda: None, "label": "Sample job", "default_trigger": "daily", "default_spec": {"hour": 7, "minute": 0},
        "notify": "on_complete", "weekday_only": None, "default_enabled": True}})
    ledger = []
    from agent_friday.services import action_ledger
    monkeypatch.setattr(action_ledger, "try_append", lambda kind, fields, **kw: ledger.append((kind, fields)) or True)
    scheduler._seed_and_reconcile()
    return ledger


def _stored():
    return scheduler.get_schedule("sch_sample_job")


def test_the_scheduler_seeds_the_job_and_an_owner_edit_is_a_logged_version(sched):
    assert _stored()["spec"]["hour"] == 7 and bo.history("sch_sample_job") == []
    scheduler.update_schedule("sch_sample_job", {"spec": {"hour": 5, "minute": 30}}, by="owner")
    [entry] = bo.history("sch_sample_job")
    assert entry["by"] == "owner" and entry["after"]["spec"] == {"hour": 5, "minute": 30}
    assert sched[-1] == ("builtin_schedule_edit", {"schedule": "sch_sample_job", "version": 1, "kind": "edit",
                                                    "by": "owner", "fields": ["spec"]})


def test_a_built_in_jobs_edited_schedule_survives_a_simulated_update(sched, monkeypatch):
    """The update ships a new default (09:00) and reseeds the record; the owner's 05:30 stays."""
    scheduler.update_schedule("sch_sample_job", {"spec": {"hour": 5, "minute": 30}}, by="owner")
    scheduler.update_schedule("sch_sample_job", {"enabled": False}, by="owner")
    monkeypatch.setitem(scheduler.BUILTIN_TASKS["sample_job"], "default_spec", {"hour": 9, "minute": 0})
    reseeded = scheduler._seed_record("sample_job", scheduler.BUILTIN_TASKS["sample_job"])     # what an update would write
    scheduler._write_store([reseeded])
    assert scheduler.get_schedule("sch_sample_job")["spec"]["hour"] == 9                      # the overwrite happened
    scheduler._seed_and_reconcile()                                                           # the next start
    kept = _stored()
    assert kept["spec"] == {"hour": 5, "minute": 30} and kept["enabled"] is False


def test_a_job_edited_before_versions_existed_is_not_overwritten_by_an_update(sched, monkeypatch):
    records = scheduler._read_store()
    records[0]["spec"] = {"hour": 4, "minute": 0}                    # an edit from the old days, no history anywhere
    scheduler._write_store(records)
    scheduler._seed_and_reconcile()
    assert bo.history("sch_sample_job")[0]["kind"] == "adopted"
    monkeypatch.setitem(scheduler.BUILTIN_TASKS["sample_job"], "default_spec", {"hour": 9, "minute": 0})
    scheduler._write_store([scheduler._seed_record("sample_job", scheduler.BUILTIN_TASKS["sample_job"])])
    scheduler._seed_and_reconcile()
    assert _stored()["spec"]["hour"] == 4


def test_restore_default_works_and_can_be_undone(sched):
    scheduler.update_schedule("sch_sample_job", {"spec": {"hour": 5, "minute": 30}, "enabled": False}, by="owner")
    restored = scheduler.restore_builtin_default("sch_sample_job", by="owner")
    assert restored["spec"] == {"hour": 7, "minute": 0} and restored["enabled"] is True
    assert bo.history("sch_sample_job")[-1]["kind"] == "restore_default" and not bo.has_edits("sch_sample_job")
    assert sched[-1][1]["kind"] == "restore_default"
    back = scheduler.revert_builtin_edit("sch_sample_job", by="owner")                  # undo the restore: the edit is back
    assert back["spec"] == {"hour": 5, "minute": 30} and back["enabled"] is False
    again = scheduler.revert_builtin_edit("sch_sample_job", by="owner")                 # then undo the edit itself
    assert again["spec"] == {"hour": 7, "minute": 0} and again["enabled"] is True
    assert scheduler.restore_builtin_default("sch_not_a_builtin") is None and scheduler.revert_builtin_edit("nope") is None


def test_an_owners_eight_oclock_daily_creation_is_not_moved_to_idle_by_the_update(sched, monkeypatch):
    """The migration of the shipped 08:00 default to 'once a day while away' leaves a job the owner has touched."""
    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "daily_creation", {
        "fn": lambda: None, "label": "Daily creation", "default_trigger": "daily", "default_spec": {"hour": 7, "minute": 0},
        "notify": "on_complete", "weekday_only": None, "default_enabled": True})
    scheduler._seed_and_reconcile()
    scheduler.update_schedule("sch_daily_creation", {"spec": {"hour": 8, "minute": 0}}, by="owner")
    scheduler._seed_and_reconcile()
    kept = scheduler.get_schedule("sch_daily_creation")
    assert kept["trigger"] == "daily" and kept["spec"]["hour"] == 8


def test_an_automatic_switch_is_not_an_owner_edit(sched):
    scheduler.update_schedule("sch_sample_job", {"enabled": False}, by="auto")
    assert bo.history("sch_sample_job") == []


# ── the model's tool ───────────────────────────────────────────────────────────────────

@pytest.fixture
def owner_chat(monkeypatch):
    monkeypatch.setattr(schedule_tools, "_caller_may_schedule", lambda: True)


def test_a_model_may_change_a_built_ins_schedule_without_a_card_and_it_is_a_version(sched, owner_chat):
    cls, _why = schedule_tools.classify({"action": "edit", "schedule_id": "sch_sample_job", "spec": {"hour": 6}})
    assert cls == "internal"
    out = json.loads(schedule_tools.schedule_job({"action": "edit", "schedule_id": "sch_sample_job",
                                                  "trigger": "daily", "spec": {"hour": 6, "minute": 15}}))
    assert out["edited"] is True and _stored()["spec"] == {"hour": 6, "minute": 15}
    assert bo.history("sch_sample_job")[-1]["by"] == "tool"


def test_a_model_changing_a_built_ins_prompt_or_action_needs_an_approval_card(sched, owner_chat):
    cls, why = schedule_tools.classify({"action": "edit", "schedule_id": "sch_sample_job", "prompt": "do something else"})
    assert cls == "outward" and "built-in" in why and "owner" in why
    cls, _ = schedule_tools.classify({"action": "edit", "schedule_id": "sch_sample_job", "task": {"kind": "x"}})
    assert cls == "outward"


def test_a_prompt_edit_of_the_owners_own_job_asks_only_when_it_would_send(sched, owner_chat):
    scheduler.register_schedule({"name": "Mine", "trigger": "daily", "spec": {"hour": 8, "minute": 0},
                                 "task": {"kind": "agent_prompt", "prompt": "summarise the news"}, "enabled": True})
    mine = next(r for r in scheduler.list_schedules() if r["name"] == "Mine")
    cls, _ = schedule_tools.classify({"action": "edit", "schedule_id": mine["id"], "prompt": "summarise my day"})
    assert cls == "internal"
    cls, _ = schedule_tools.classify({"action": "edit", "schedule_id": mine["id"], "prompt": "email the summary to Sam"})
    assert cls == "outward"


def test_a_model_can_restore_and_revert_a_built_in_but_a_background_run_cannot_edit(sched, owner_chat, monkeypatch):
    scheduler.update_schedule("sch_sample_job", {"spec": {"hour": 5, "minute": 0}}, by="owner")
    out = json.loads(schedule_tools.schedule_job({"action": "restore_default", "schedule_id": "sch_sample_job"}))
    assert out["restored"] is True and _stored()["spec"]["hour"] == 7
    with pytest.raises(ValueError, match="no earlier edit"):
        schedule_tools.schedule_job({"action": "revert", "schedule_id": "sch_sample_job"})      # nothing left to step back to
    with pytest.raises(ValueError, match="no schedule"):
        schedule_tools.schedule_job({"action": "revert", "schedule_id": "sch_nothing_here"})
    monkeypatch.setattr(schedule_tools, "_caller_may_schedule", lambda: False)
    with pytest.raises(ValueError, match="cannot change schedules"):
        schedule_tools.schedule_job({"action": "edit", "schedule_id": "sch_sample_job", "spec": {"hour": 3}})
