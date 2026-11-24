"""A scheduled run cut off by a stop or crash leaves one recovery receipt and is not run again (BD-16 C3
gap: "scheduler jobs"). Synthetic schedules only; nothing is sent."""
from __future__ import annotations

import time

import pytest

from agent_friday.services import recovery_receipts as rr
from agent_friday.services import scheduler as s
from tests import ledger_support


@pytest.fixture(autouse=True)
def clean(friday_dir, monkeypatch):
    monkeypatch.setattr(s, "_setup_finished", lambda: True)
    for f in (s.SCHEDULES_FILE, s.RUNS_FILE):
        if f.exists():
            f.unlink()
    s._RUNNING.clear()
    rr._reset_for_tests()
    yield
    rr._reset_for_tests()


def _job(ref, fn):
    s.register_builtin_task(ref, fn, label=ref, default_trigger="daily", default_spec={"hour": 0})
    return s.register_schedule({"id": "sch_" + ref, "name": "Morning brief", "trigger": "daily",
                                "spec": {"hour": 0}, "task": {"kind": "builtin", "ref": ref}})


def _mine(sid):
    return [d for d in ledger_support.decisions() if d.get("subject") == "schedule:" + sid]


def _wait_history(sid):
    for _ in range(100):
        if s.run_history(sid, limit=1):
            return
        time.sleep(0.05)
    raise AssertionError("the run never finished")


def _stored(sid):
    return next(r for r in s._read_store() if r["id"] == sid)


def test_a_run_that_finishes_is_not_in_flight_and_gets_no_receipt():
    _job("t_done", lambda: {"changed": True, "summary": "ok"})
    s.dispatch(s.get_schedule("sch_t_done"), manual=True)
    _wait_history("sch_t_done")
    assert _stored("sch_t_done")["in_flight"] is False
    assert s.recover_interrupted_runs() == 0 and _mine("sch_t_done") == []


def test_a_run_cut_off_leaves_one_receipt_then_is_cleared():
    rec = _job("t_cut", lambda: None)
    started = time.time() - 120
    s._patch_record("sch_t_cut", **s._run_mark(rec, s._now_central(), started))
    assert _stored("sch_t_cut")["in_flight"] is True
    assert s.recover_interrupted_runs() == 1
    rr._reset_for_tests()                                  # a later start reads the ledger afresh
    assert s.recover_interrupted_runs() == 0
    [r] = _mine("sch_t_cut")
    assert r["decision"] == "recovered" and r["may_have_acted"] is True and r["repeated"] is False
    assert r["next"] == "not_repeated_owner_told" and r["what"] == "Morning brief"
    assert _stored("sch_t_cut")["in_flight"] is False


def test_a_record_from_before_the_mark_is_not_called_interrupted():
    _job("t_old", lambda: None)
    assert "in_flight" not in _stored("sch_t_old")
    assert s.recover_interrupted_runs() == 0 and _mine("sch_t_old") == []


def test_a_crash_between_the_receipt_and_the_clear_writes_no_second_receipt():
    """The dedupe is for exactly this: the receipt is written, the mark is not cleared (a crash, a locked file), and
    the next start finds the mark again."""
    rec = _job("t_crash", lambda: None)
    s._patch_record("sch_t_crash", **s._run_mark(rec, s._now_central(), time.time() - 120))
    real = s._patch_record

    def failing(sid, **fields):
        if fields.get("in_flight") is False:
            raise OSError("the store is locked")
        return real(sid, **fields)

    s._patch_record = failing
    try:
        assert s.recover_interrupted_runs() == 1
    finally:
        s._patch_record = real
    assert _stored("sch_t_crash")["in_flight"] is True          # not cleared: the crash case
    rr._reset_for_tests()                                       # a new process reads the ledger afresh
    assert s.recover_interrupted_runs() == 0
    assert len(_mine("sch_t_crash")) == 1
    assert _stored("sch_t_crash")["in_flight"] is False


def test_one_record_that_cannot_be_cleared_does_not_stop_the_others_being_recorded():
    a = _job("t_a", lambda: None)
    b = _job("t_b", lambda: None)
    for rec in (a, b):
        s._patch_record(rec["id"], **s._run_mark(rec, s._now_central(), time.time() - 120))
    real = s._patch_record

    def failing(sid, **fields):
        if fields.get("in_flight") is False and sid == "sch_t_a":
            raise OSError("the store is locked")
        return real(sid, **fields)

    s._patch_record = failing
    try:
        assert s.recover_interrupted_runs() == 2
    finally:
        s._patch_record = real
    assert len(_mine("sch_t_a")) == 1 and len(_mine("sch_t_b")) == 1


def test_a_finished_run_whose_mark_cannot_be_cleared_says_so_in_the_log(caplog):
    rec = _job("t_log", lambda: None)
    real = s._patch_record

    def failing(sid, **fields):
        if fields.get("in_flight") is False:
            raise OSError("the store is locked")
        return real(sid, **fields)

    s._patch_record = failing
    try:
        import logging
        with caplog.at_level(logging.WARNING):
            s.dispatch(rec)
            _wait_history("sch_t_log")
    finally:
        s._patch_record = real
    assert "in-flight mark" in caplog.text and "OSError" in caplog.text
