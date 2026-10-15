"""The scheduled-cloud estimate comes from costs.db when this PC has history.

The per-run token constants in services/scheduled_cloud.py were off by 2-4x
on a real install (field install report, Part 2 #12). With at least
MEASURE_MIN_RUNS recorded runs of a job in the last MEASURE_DAYS days, the
estimate now uses what those runs actually sent and received (basis
"measured"); otherwise the documented constants (basis "assumed"). Either way
it is labelled an estimate.

Red on origin/main: the estimate ignores recorded runs entirely.
"""
from __future__ import annotations

import pytest

from agent_friday.services import cost_meter as cm
from agent_friday.services import scheduled_cloud as sc


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "DB_PATH", tmp_path / "costs.db")
    monkeypatch.setattr(cm, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(cm, "_CONN", None)
    monkeypatch.setattr(cm, "_BUFFER", [])
    yield
    try:
        if cm._CONN is not None:
            cm._CONN.close()
    except Exception:
        pass


def _runs(sid, n, input_tokens, output_tokens, calls_per_run=2):
    for i in range(n):
        for _ in range(calls_per_run):
            cm.record("anthropic", "claude-sonnet-5", input_tokens // calls_per_run,
                      output_tokens // calls_per_run, kind="scheduled",
                      schedule_id=sid, run_id=f"run-{sid}-{i}")
    cm.flush()


def _job(est, sid):
    return next(j for j in est["jobs"] if j["id"] == sid)


def test_recorded_runs_replace_the_constants():
    _runs("sch_heartbeat", 4, input_tokens=2_400, output_tokens=60)
    est = sc.estimate(sc.defaults(), records=[])
    hb = _job(est, "sch_heartbeat")
    assert hb["basis"] == "measured"
    assert hb["runs_measured"] == 4
    assert hb["input_tokens_per_run"] == 2_400
    assert hb["output_tokens_per_run"] == 60
    # The constants would have said ~3x the system prompt.
    assert hb["input_tokens_per_run"] < sc.SYSTEM_PROMPT_TOKENS
    assert est["is_estimate"] is True


def test_too_few_runs_fall_back_to_the_documented_constants():
    _runs("sch_heartbeat", sc.MEASURE_MIN_RUNS - 1, input_tokens=2_400, output_tokens=60)
    hb = _job(sc.estimate(sc.defaults(), records=[]), "sch_heartbeat")
    assert hb["basis"] == "assumed"
    assert hb["input_tokens_per_run"] == sc.per_run_tokens("sch_heartbeat")["input"]


def test_measured_tokens_are_priced_at_the_chosen_model():
    _runs("sch_daily_creation", 3, input_tokens=10_000, output_tokens=2_000)
    cheap = _job(sc.estimate(sc.defaults(), records=[]), "sch_daily_creation")
    dear = _job(sc.estimate(dict(sc.defaults(), job_model="claude-opus-5-5"), records=[]),
                "sch_daily_creation")
    assert cheap["basis"] == dear["basis"] == "measured"
    assert dear["usd_per_run"] > cheap["usd_per_run"]


def test_other_schedules_and_unrun_rows_do_not_leak_in():
    _runs("sch_some_other_job", 5, input_tokens=99_000, output_tokens=9_000)
    cm.record("anthropic", "claude-sonnet-5", 50_000, 1, schedule_id="sch_heartbeat")  # no run id
    cm.flush()
    assert sc.measured_per_run("sch_heartbeat") is None
