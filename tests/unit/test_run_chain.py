"""Arbiter.run_chain — executing a ChainPlan stage by stage (headroom.md
§6.5, §12 Phase 3.3).

Offline: `machine_monitor.sample`/`verdict` are monkeypatched so this suite
never shells out to nvidia-smi/PowerShell, matching the sibling
test_residency_arbiter.py's own "Offline" rule.
"""
from __future__ import annotations

import pytest

from agent_friday.services import machine_monitor as mm
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import residency_catalog as rc
from agent_friday.services import residency_policy as rp
from tests import residency_fixtures as fx


class FakeOllama:
    name = "ollama"

    def __init__(self):
        self._res = {}

    def resident(self):
        return dict(self._res)

    def load(self, model_id, num_ctx, keep_alive="15m", think=False):
        self._res[model_id] = 1000

    def evict(self, model_id):
        self._res.pop(model_id, None)

    def evict_all(self):
        for m in list(self._res):
            self.evict(m)


class FakeLlama:
    name = "llama-server"

    def __init__(self):
        self.procs = {}

    def resident(self):
        return {m: 0 for m in self.procs}

    def load(self, model_id, num_ctx, *, gguf_path, port, n_cpu_moe=None,
             timeout=300):
        self.procs[model_id] = (object(), port)
        return 1.0

    def evict(self, model_id):
        self.procs.pop(model_id, None)

    def evict_all(self):
        self.procs.clear()


class FakeComfy:
    def __init__(self):
        self.started = False

    def running(self):
        return self.started

    def start(self, timeout=300):
        self.started = True
        return 3.0

    def stop(self):
        self.started = False


def _ok_verdict(status="ok"):
    return {"display": {"status": status, "basis": "measured",
                        "explanation": "test fixture"},
           "disk_system": {"status": "ok", "basis": "measured"},
           "vram_slack": {"status": "unknown", "basis": "unknown"},
           "ram_available": {"status": "unknown", "basis": "unknown"},
           "thrash": {"status": "ok", "basis": "measured"}}


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "store_path", lambda: tmp_path / "m.json")
    rc.reset_cache()


@pytest.fixture
def arb(monkeypatch):
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(), comfy=FakeComfy(),
                   gguf_paths={"gemma4:e4b": "/x/e4b.gguf",
                               "gemma4:e2b": "/x/e2b.gguf"})
    a.compute_plan()
    monkeypatch.setattr(mm, "sample", lambda **kw: {"gpus": [],
                                                    "disk_system_free_mib":
                                                    50000})
    monkeypatch.setattr(mm, "verdict", lambda *a, **k: _ok_verdict())
    return a


def _plan(role_sequence=("stt", "interactive_brain", "image", "tts")):
    stages = [{"role": r, "units": 1} for r in role_sequence]
    resident = {
        "interactive_brain": {"model_id": "gemma4:e4b", "device": "gpu:0",
                              "vram_mib": 3081},
        "sidekick": {"model_id": "gemma4:e2b", "device": "gpu:0",
                    "vram_mib": 1811},
    }
    fp = rc.make_footprint(modality="image", device="gpu", basis="measured",
                           vram_mib=1000, load_s=1.0, work_s_per_unit=1.0,
                           unit="image", measured_at="2026-09-04")
    rc.record_footprint("z-image-turbo-fp8", rc.profile_fingerprint(fx.P1), fp)
    return rp.plan_chain(fx.P1, fx.catalog(fx.P1), stages, resident=resident)


def test_run_chain_calls_on_stage_for_every_stage(arb):
    plan = _plan()
    seen = []

    def on_stage(stage):
        seen.append(stage["role"])
        return {"seconds": 0.1}

    result = arb.run_chain(plan, on_stage)
    assert seen == ["stt", "interactive_brain", "image", "tts"]
    assert [r["status"] for r in result["results"]] == \
        ["ran", "ran", "ran", "ran"]


def test_run_chain_grants_and_releases_around_a_leased_stage(arb):
    plan = _plan()
    holds = []

    def on_stage(stage):
        holds.append((stage["role"], arb.lease["kind"] if arb.lease
                      else None))
        return {"seconds": 0.1, "vram_mib": 1000}

    arb.run_chain(plan, on_stage)
    # The image stage ran WITH a lease held; everything else ran without one.
    held = dict(holds)
    assert held["image"] == "image_job"
    assert held["stt"] is None
    assert held["interactive_brain"] is None
    assert held["tts"] is None
    assert arb.lease is None    # released by the time run_chain returns


def test_run_chain_records_a_real_measurement_for_the_leased_stage(arb):
    plan = _plan()

    def on_stage(stage):
        return {"seconds": 12.3, "vram_mib": 9999}

    arb.run_chain(plan, on_stage)
    fp = rc.footprint("z-image-turbo-fp8", fx.P1)
    assert fp["basis"] == "measured"
    assert fp["vram_mib"] == 9999
    assert fp["work_s_per_unit"] == 12.3


def test_run_chain_stops_before_a_display_reserve_breach(arb, monkeypatch):
    plan = _plan()
    calls = {"n": 0}

    def flaky_verdict(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:      # breach right at the image stage boundary
            return _ok_verdict(status="breached")
        return _ok_verdict()

    monkeypatch.setattr(mm, "verdict", flaky_verdict)
    seen = []

    def on_stage(stage):
        seen.append(stage["role"])
        return {"seconds": 0.1}

    result = arb.run_chain(plan, on_stage)
    # stt and interactive_brain ran; image never did.
    assert seen == ["stt", "interactive_brain"]
    statuses = [r["status"] for r in result["results"]]
    assert statuses[-1] == "breached"
    assert "proposal_id" in result["results"][-1]


def test_run_chain_releases_before_raising_the_three_way_mid_lease(
        arb, monkeypatch):
    """A breach discovered while a lease IS held (e.g. the boundary check
    before the stage AFTER a leased one) still releases first."""
    plan = _plan()
    calls = {"n": 0}

    def flaky_verdict(*a, **k):
        calls["n"] += 1
        if calls["n"] == 4:      # breach right before tts, after the image
            return _ok_verdict(status="breached")
        return _ok_verdict()

    monkeypatch.setattr(mm, "verdict", flaky_verdict)

    def on_stage(stage):
        return {"seconds": 0.1, "vram_mib": 500}

    result = arb.run_chain(plan, on_stage)
    assert arb.lease is None
    assert result["results"][-1]["status"] == "breached"


def test_run_chain_honours_cancellation(arb):
    plan = _plan()
    chain_id = "chain-under-test"
    calls = []

    def on_stage(stage):
        calls.append(stage["role"])
        if stage["role"] == "interactive_brain":
            ra.request_chain_cancel(chain_id)
        return {"seconds": 0.1}

    result = arb.run_chain(plan, on_stage, chain_id=chain_id)
    # stt and interactive_brain ran; the cancel flag stopped it before image.
    assert calls == ["stt", "interactive_brain"]
    assert result["results"][-1]["status"] == "cancelled"
    assert not ra.is_chain_cancelled(chain_id)   # cleared on exit


def test_run_chain_refusal_stops_the_chain(arb, monkeypatch):
    plan = _plan()
    monkeypatch.setattr(arb, "grant", lambda *a, **k: {"ok": False,
                                                       "error": "no room"})

    def on_stage(stage):
        return {"seconds": 0.1}

    result = arb.run_chain(plan, on_stage)
    roles_ran = [r["role"] for r in result["results"] if r["status"] == "ran"]
    assert "image" not in roles_ran
    assert result["results"][-1]["status"] == "refused"
