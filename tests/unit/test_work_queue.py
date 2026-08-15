"""The work queue — batching, and the promises it has to keep.

The number under all of this: waking gemma4:26b costs 53.5 s before its first
token, measured. Running six items in one lease costs that once; running them
separately costs it six times. These tests pin the batching, the priority
order, and the two things the queue must never do — lose parked work across a
restart, or accept a disposition it cannot honour.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import work_queue as wq


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(wq, "queue_dir", lambda: tmp_path / "work_queue")
    monkeypatch.setattr(wq, "queue_path",
                        lambda: tmp_path / "work_queue" / "queue.json")
    wq.mark_active()
    yield


class _FakeArbiter:
    """Counts leases. One drain must take exactly one."""

    def __init__(self, ok=True):
        self.ok, self.grants, self.releases = ok, [], 0

    def grant(self, kind, ttl_s=300):
        self.grants.append(kind)
        if not self.ok:
            return {"ok": False, "error": "no heavy seat"}
        return {"ok": True, "lease": {"kind": kind}, "transition_s": 53.5}

    def release(self):
        self.releases += 1
        return {"ok": True}


# ── the point of the module ──────────────────────────────────────────────────

def test_a_drain_takes_one_lease_for_the_whole_batch():
    """Six items, one wake. This IS the 53-second rule."""
    for i in range(6):
        wq.enqueue("job %d" % i, "do the thing", cls="heavy",
                   disposition="now_local")
    arb = _FakeArbiter()
    out = wq.drain("heavy", lambda it: ("ok", "gemma4:26b"), arbiter=arb)
    assert out["ran"] == 6
    assert arb.grants == ["heavy_turn"], "one lease, not one per item"
    assert arb.releases == 1


def test_the_drain_reports_the_saving_rather_than_claiming_it():
    for i in range(4):
        wq.enqueue("job %d" % i, "x", cls="heavy", disposition="now_local")
    out = wq.drain("heavy", lambda it: ("ok", "s"), arbiter=_FakeArbiter())
    assert out["load_s"] == 53.5
    assert out["amortised_load_s"] == pytest.approx(13.38, abs=0.02)
    # Three items rode in free on the first item's wake.
    assert out["load_s_saved"] == pytest.approx(160.5)


def test_one_failing_item_does_not_end_the_batch():
    """Dropping the lease on the first error makes everything behind it pay
    the wake cost again."""
    for i in range(4):
        wq.enqueue("job %d" % i, "x", cls="heavy", disposition="now_local")

    def runner(it):
        if it["title"] == "job 1":
            raise RuntimeError("boom")
        return "ok", "seat"

    out = wq.drain("heavy", runner, arbiter=_FakeArbiter())
    assert out["ran"] == 4 and len(out["done"]) == 3 and len(out["failed"]) == 1
    failed = wq.get(out["failed"][0])
    assert failed["status"] == "failed" and "boom" in failed["error"]


# ── away ─────────────────────────────────────────────────────────────────────

def test_away_work_waits_until_the_machine_is_actually_quiet():
    wq.enqueue("later", "x", cls="heavy", disposition="when_away")
    wq.mark_active()
    arb = _FakeArbiter()
    out = wq.drain("heavy", lambda it: ("ok", "s"), arbiter=arb)
    assert out["skipped"] is True
    assert arb.grants == [], "no lease may be taken while someone is here"
    assert "idle for" in out["why"]


def test_away_work_runs_once_the_machine_has_been_quiet():
    wq.enqueue("later", "x", cls="heavy", disposition="when_away")
    wq.mark_active(time.time() - (wq.AWAY_AFTER_S + 5))
    out = wq.drain("heavy", lambda it: ("ok", "s"), arbiter=_FakeArbiter())
    assert out["ran"] == 1


def test_work_asked_for_now_does_not_wait_for_anything():
    wq.enqueue("now", "x", cls="heavy", disposition="now_local")
    wq.mark_active()
    out = wq.drain("heavy", lambda it: ("ok", "s"), arbiter=_FakeArbiter())
    assert out["ran"] == 1


def test_a_still_queue_explains_itself_instead_of_looking_broken():
    """"Nothing happened" and "three things are waiting for you to leave" look
    identical from outside unless the queue says which it is."""
    assert "nothing queued" in wq.batch_ready("heavy")["why"]
    wq.enqueue("later", "x", cls="heavy", disposition="when_away")
    wq.mark_active()
    r = wq.batch_ready("heavy")
    assert r["ready"] is False
    assert "waiting until you are away" in r["why"]


def test_batching_holds_out_for_a_minimum_worth_waking_for():
    wq.enqueue("one", "x", cls="heavy", disposition="when_away")
    wq.mark_active(time.time() - (wq.AWAY_AFTER_S + 5))
    r = wq.batch_ready("heavy", min_items=3)
    assert r["ready"] is False and "batching until there are 3" in r["why"]


# ── the promises ─────────────────────────────────────────────────────────────

def test_parked_work_survives_a_restart():
    """"I'll do this while you're away" has to outlive the process or it is a
    lie with good intentions."""
    item = wq.enqueue("survive me", "x", cls="heavy", disposition="when_away")
    wq._LAST_ACTIVITY[0] = 0            # simulate a fresh process
    again = wq.get(item["id"])
    assert again is not None and again["status"] == "queued"


def test_vault_work_cannot_be_queued_for_the_cloud():
    """The router would force it local anyway, so accepting the label would
    make the queue say one thing and the system do another."""
    with pytest.raises(ValueError) as e:
        wq.enqueue("read the vault", "x", cls="heavy",
                   disposition="now_cloud", touches_vault=True)
    assert "never leaves the machine" in str(e.value)


def test_an_unknown_class_or_disposition_is_refused_not_coerced():
    with pytest.raises(ValueError):
        wq.enqueue("x", "y", cls="enormous")
    with pytest.raises(ValueError):
        wq.enqueue("x", "y", disposition="whenever")


# ── ordering ─────────────────────────────────────────────────────────────────

def test_pending_is_ordered_by_latency_class_then_age():
    wq.enqueue("bg", "x", cls="background", disposition="now_local")
    wq.enqueue("quick", "x", cls="reflex", disposition="now_local")
    wq.enqueue("chat", "x", cls="interactive", disposition="now_local")
    assert [i["cls"] for i in wq.pending()] == \
        ["reflex", "interactive", "background"]


def test_classes_that_run_on_pinned_seats_need_no_lease():
    """That is what pinning is for."""
    assert wq.CLASS_LEASE["interactive"] is None
    assert wq.CLASS_LEASE["reflex"] is None
    assert wq.CLASS_LEASE["heavy"] == "heavy_turn"
    assert wq.CLASS_LEASE["image"] == "image_job"


def test_a_refused_lease_runs_nothing_rather_than_running_it_anyway():
    wq.enqueue("job", "x", cls="heavy", disposition="now_local")
    out = wq.drain("heavy", lambda it: ("ok", "s"),
                   arbiter=_FakeArbiter(ok=False))
    assert out["ran"] == 0 and "lease refused" in out["why"]
    assert wq.get(wq.pending("heavy")[0]["id"])["status"] == "queued"


def test_cancel_only_applies_to_work_that_has_not_started():
    it = wq.enqueue("job", "x", cls="heavy", disposition="when_away")
    assert wq.cancel(it["id"]) is True
    assert wq.cancel(it["id"]) is False
