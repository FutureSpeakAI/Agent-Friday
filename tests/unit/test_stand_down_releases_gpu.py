""""I need my machine" lets go of the GPU, and a restart does not grab it back.

Measured on 2026-09-25: Friday was stood down, its llama-server seat kept
running (stand-down released the GPU through adopt_or_reap(set()), which
deliberately spares any seat a local provider descriptor routes to -- Friday's
own :8090 seat is exactly that), and when the server restarted 20 seconds
later, boot() re-pinned the seat with nothing asking whether Friday was stood
down. Resume, for its part, never reloaded anything.
"""
from __future__ import annotations

import pytest

from agent_friday.services import context_budget
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import stand_down as sd
from tests import residency_fixtures as fx
from tests.unit.test_residency_arbiter import FakeLlama, FakeOllama
from tests.unit.test_run_chain import FakeComfy


class SparingLlama(FakeLlama):
    """adopt_or_reap as the real backend behaves for a routed-to seat: it
    adopts it and reaps nothing."""

    def adopt_or_reap(self, wanted):
        return {"adopted": [], "reaped": []}


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(context_budget, "injected_tokens",
                        lambda: (context_budget.MEASURED_INJECTED_TOKENS, "reference"))
    context_budget.reset_cache()
    fx.offline_machine(monkeypatch)
    sd._invalidate()
    # Reclaiming runs on a thread in the product; inline here.
    monkeypatch.setattr(sd, "_in_background", lambda fn: fn(), raising=False)
    from agent_friday.services import arbiter as res_arb
    monkeypatch.setattr(res_arb, "held", lambda resource=None: [])
    yield
    sd._invalidate()
    context_budget.reset_cache()


@pytest.fixture
def arb(monkeypatch):
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=SparingLlama(), comfy=FakeComfy(),
                   gguf_paths={"gemma4:12b": "/x/12b.gguf", "gemma4:e2b": "/x/e2b.gguf"})
    a.compute_plan()
    monkeypatch.setattr(ra, "ARBITER", a)
    return a


def test_standing_down_stops_the_seats_friday_owns(arb):
    arb.boot(measure_baseline=False)
    assert arb.llama.procs, "the fixture should start with seats loaded"
    sd.stand_down(requested_by="test")
    assert arb.llama.procs == {}
    assert arb.ollama.resident() == {}


def test_a_restart_while_stood_down_loads_nothing(arb):
    sd.stand_down(requested_by="test")
    arb.boot(measure_baseline=False)
    assert arb.llama.procs == {}
    assert not [c for c in arb.ollama.calls if c[0] == "load"]
    assert arb.state == ra.STATE_DEFAULT
    assert any(t["action"] == "held" for t in arb.transitions)


def test_a_restart_while_stood_down_lets_go_of_a_seat_it_adopted(arb):
    arb.llama.procs["gemma4:12b"] = (object(), 8090)       # left by the old process
    sd.stand_down(requested_by="test")
    arb.boot(measure_baseline=False)
    assert arb.llama.procs == {}


def test_a_lease_is_refused_while_stood_down(arb):
    arb.boot(measure_baseline=False)
    sd.stand_down(requested_by="test")
    r = arb.grant("image_job")
    assert r["ok"] is False and "stood down" in r["error"]


def test_resume_brings_the_seats_back(arb):
    sd.stand_down(requested_by="test")
    arb.boot(measure_baseline=False)
    assert arb.llama.procs == {}
    sd.resume(requested_by="test")
    assert set(arb.llama.procs) == {"gemma4:12b", "gemma4:e2b"}


def test_an_expired_stand_down_window_brings_the_seats_back(arb, monkeypatch):
    sd.stand_down(requested_by="test", hours=1)
    arb.boot(measure_baseline=False)
    assert arb.llama.procs == {}
    import time
    monkeypatch.setattr(time, "time", lambda: 10 ** 11)
    assert sd.is_stood_down() is False
    assert set(arb.llama.procs) == {"gemma4:12b", "gemma4:e2b"}


def test_a_foreign_gpu_hold_keeps_the_seats_off_the_card(arb, monkeypatch):
    from agent_friday.services import arbiter as res_arb
    monkeypatch.setattr(res_arb, "held", lambda resource=None: [
        {"resource": "gpu_exclusive", "holder_kind": "foreign", "holder": "training",
         "amount": 1, "state": "held"}])
    arb.boot(measure_baseline=False)
    assert arb.llama.procs == {}
    reason = arb.gpu_not_ours()
    assert reason and "training" in reason


def test_working_normally_nothing_is_held(arb):
    arb.boot(measure_baseline=False)
    assert set(arb.llama.procs) == {"gemma4:12b", "gemma4:e2b"}
    assert arb.gpu_not_ours() is None
