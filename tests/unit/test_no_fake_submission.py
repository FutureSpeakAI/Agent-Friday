"""No path in the job pipeline reports a submission it did not make.

The application engine used to carry a pluggable submitter whose default only
pretended to submit, a status that started out as "submitted", and a
placeholder route that answered "Would apply to ...". Submitting an
application is irreversible and public-facing; Friday prepares the materials
and the owner submits. These tests hold the engine to that; the routes are
held in tests/api/test_jobs_routes.py and test_people_workspace_routes.py.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from agent_friday.seed.data.job_tracker_schema import JobListing, JobTracker
from agent_friday.seed.skills.application_engine import engine

LONG_COVER = " ".join(["word"] * 250)


def _tracker(tmp_path):
    t = JobTracker(tmp_path / "jobs.json")
    job = JobListing(title="Staff Widget Engineer", company="Example Widgets Co",
                     source_url="https://boards.greenhouse.io/examplewidgets/jobs/1",
                     salary_max=200000)
    t.add_job(job)
    return t, job.job_id


def _run(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(engine, "_record_to_skillopt", lambda *a, **k: None)
    monkeypatch.setattr(engine, "VARIANT_STATE_PATH", tmp_path / "bandit.json")
    t, jid = _tracker(tmp_path)
    out = engine.apply_to_job(
        job_id=jid, tracker=t,
        resume_builder=lambda listing, v, c: {"variant": v, "path": "",
                                              "must_include": ["x"]},
        cover_drafter=lambda listing, r, c: LONG_COVER,
        notifier=lambda p: None, **kw)
    return t, out


def test_the_engine_has_no_submitter():
    assert "submitter" not in inspect.signature(engine.apply_to_job).parameters
    assert not hasattr(engine, "_default_submitter")


def test_a_run_that_passes_every_gate_is_prepared_not_submitted(tmp_path, monkeypatch):
    t, out = _run(tmp_path, monkeypatch, dry_run=False)
    assert out["status"] == "prepared", out["quality_gates_failed"]
    assert out["submitted"] is False
    assert out["submit_result"]["submitted"] is False
    assert "submit it yourself" in out["next_step"]
    assert t.get_application(out["application_id"]).stage != "applied"


def test_no_engine_status_claims_a_submission():
    """Every status string the engine can return, read from its source."""
    tree = ast.parse(Path(engine.__file__).read_text(encoding="utf-8"))
    statuses = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "status" for t in node.targets):
            for c in ast.walk(node.value):
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    statuses.add(c.value)
    assert statuses, "found no status assignments; the scan is not scanning"
    assert not {s for s in statuses if "submit" in s.lower() or s == "applied"}, statuses


def test_a_sensitive_field_in_the_plan_is_left_for_the_owner(tmp_path, monkeypatch):
    cfg = engine.load_config()
    cfg["ats"]["field_maps"]["greenhouse"]["gender"] = "select[name='gender']"
    cfg["ats"]["field_maps"]["greenhouse"]["veteran_status"] = "select[name='veteran']"
    t, out = _run(tmp_path, monkeypatch, config=cfg,
                  candidate={"gender": "guessed", "veteran_status": "guessed",
                             "email": "candidate@example.com"})
    plan = {row["field"]: row for row in out["field_plan"]}
    assert plan["gender"]["value_preview"] == "" and plan["gender"]["owner_answers"]
    assert plan["veteran_status"]["value_preview"] == ""
    assert plan["email"]["value_preview"] == "candidate@example.com"
