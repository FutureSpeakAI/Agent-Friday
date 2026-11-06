"""A lease whose release raises is still given back.

Arbiter.release() used to leave self.lease SET when anything inside it raised
(the image job's comfy.stop(), the restore). The card was then held by a lease
nobody owned: the scheduler's tick held every GPU job behind it ("the
podcast_voice job holds the GPU"), restore_unheld_seats answered "lease ...
holds the seats" so no seat came back, and only the lease's expiry (a speaking
job's budget plus a minute, up to hours) or a restart ended it.

Offline. The arbiter's fakes come from test_residency_arbiter.
"""
from __future__ import annotations

import pytest

from agent_friday.services import context_budget
from agent_friday.services import residency_arbiter as ra
from tests import residency_fixtures as fx
from tests.unit.test_residency_arbiter import (
    FakeComfy, FakeLlama, FakeOllama, _mm_arbiter, _memory_manager_only_plan)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(context_budget, "injected_tokens",
                        lambda: (context_budget.MEASURED_INJECTED_TOKENS,
                                 "reference"))
    context_budget.reset_cache()
    fx.offline_machine(monkeypatch)
    from agent_friday.services import machine_monitor as mm
    monkeypatch.setattr(mm, "last_sample", lambda *a, **k: {})
    monkeypatch.setattr(mm, "disk_system_verdict",
                        lambda *a, **k: {"status": "ok"})
    yield
    context_budget.reset_cache()


class _ComfyThatWillNotStop(FakeComfy):
    def stop(self):
        raise RuntimeError("comfy would not stop")


def _held_arbiter(monkeypatch):
    """An arbiter holding an image lease whose release will raise."""
    _memory_manager_only_plan(monkeypatch)
    a = _mm_arbiter()
    a.comfy = _ComfyThatWillNotStop()
    a.compute_plan()
    a.boot(measure_baseline=False)
    out = a.grant("image_job")
    assert out["ok"], out
    assert a.lease and a.lease["kind"] == "image_job"
    return a


def test_a_release_that_raises_still_gives_the_lease_back(monkeypatch):
    a = _held_arbiter(monkeypatch)
    out = a.release()
    assert out["ok"] is False and "would not stop" in out["error"], out
    assert a.lease is None, (
        "the release raised and the lease stayed held: the scheduler would "
        "hold every GPU job behind it until it expired")
    assert a.state == ra.STATE_DEGRADED


def test_the_seats_can_be_restored_after_a_release_that_raised(monkeypatch):
    a = _held_arbiter(monkeypatch)
    a.release()
    out = a.restore_unheld_seats("test")
    assert "holds the seats" not in str(out.get("note")), (
        "a stranded lease made the restore answer 'lease ... holds the "
        "seats', so the brain was never brought back: %r" % (out,))


def test_a_lease_that_raised_on_release_does_not_hold_the_scheduler(monkeypatch):
    a = _held_arbiter(monkeypatch)
    monkeypatch.setattr(ra, "ARBITER", a)
    assert ra.exclusive_lease() is not None          # held, as it should be
    a.release()
    assert ra.exclusive_lease() is None, (
        "exclusive_lease() still reports the lease the failed release dropped")


def test_expiry_reclaims_a_lease_whose_release_raised(monkeypatch):
    a = _held_arbiter(monkeypatch)
    a.lease["expires_at"] = 0
    a.expire_if_due()
    assert a.lease is None
