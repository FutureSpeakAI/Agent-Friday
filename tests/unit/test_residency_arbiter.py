"""Arbiter — state machine, refusals, rollback, and lease bookkeeping.

Offline. The live cycles (real loads, real ComfyUI, real timings) are in
tests/integration/test_residency_live.py and are opt-in.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import residency_arbiter as ra
from tests import residency_fixtures as fx


class FakeOllama:
    name = "ollama"

    def __init__(self):
        self._res = {}
        self.calls = []

    def resident(self):
        return dict(self._res)

    def load(self, model_id, num_ctx, keep_alive="15m", think=False):
        self.calls.append(("load", model_id, num_ctx))
        self._res[model_id] = 1000

    def evict(self, model_id):
        self.calls.append(("evict", model_id))
        self._res.pop(model_id, None)

    def evict_all(self):
        for m in list(self._res):
            self.evict(m)


class FakeLlama:
    name = "llama-server"

    def __init__(self, fail=False):
        self.procs = {}
        self.fail = fail

    def resident(self):
        return {m: 0 for m in self.procs}

    def load(self, model_id, num_ctx, *, gguf_path, port, n_cpu_moe=None,
             timeout=300):
        if self.fail:
            raise ra.TransitionError("boom")
        self.procs[model_id] = (object(), port)
        return 1.0

    def evict(self, model_id):
        self.procs.pop(model_id, None)

    def evict_all(self):
        self.procs.clear()


class FakeComfy:
    def __init__(self, fail=False):
        self.started = False
        self.fail = fail

    def running(self):
        return self.started

    def start(self, timeout=300):
        if self.fail:
            raise ra.TransitionError("comfy failed")
        self.started = True
        return 3.0

    def stop(self):
        self.started = False


@pytest.fixture
def arb():
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(), comfy=FakeComfy(),
                   gguf_paths={"gemma4:12b": "/x/12b.gguf",
                               "gemma4:e2b": "/x/e2b.gguf"})
    a.compute_plan()
    return a


# ── boot ─────────────────────────────────────────────────────────────────────

def test_boot_reaches_the_default_state(arb):
    arb.boot(measure_baseline=False)
    assert arb.state == ra.STATE_DEFAULT


def test_boot_pins_seats_as_owned_processes_not_daemon_requests(arb):
    """R9: Ollama evicted a 'pinned' model at every num_ctx tried."""
    arb.boot(measure_baseline=False)
    # The brain is ours; the sidekick stays on the daemon by decision, not by
    # failure — DAEMON_SERVED records why (its <|channel> tool calls are only
    # parsed there). Both are seated; only one is a process we own.
    assert set(arb.llama.procs) == {"gemma4:12b"}
    assert "gemma4:e2b" in arb.ollama.resident()


def test_boot_adopts_an_already_resident_seat_instead_of_reloading(arb):
    arb.ollama._res["gemma4:12b"] = 8001
    arb.boot(measure_baseline=False)
    actions = [(t["action"], t["model_id"]) for t in arb.transitions]
    assert ("adopt", "gemma4:12b") in actions


def test_a_backend_that_cannot_hold_a_model_degrades_the_pin_not_the_boot():
    """Measured case: Ollama stores gemma4:e2b across several blobs, so a
    single blob path gives llama-server "wrong number of tensors; expected
    2012, got 601". Taking the whole boot down for that would leave the machine
    with NO seats, which is strictly worse than one unenforced pin."""
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(fail=True),
                   comfy=FakeComfy(),
                   gguf_paths={"gemma4:12b": "/x/12b.gguf"})
    a.compute_plan()
    a.boot(measure_baseline=False)
    assert a.state == ra.STATE_DEFAULT
    seat = a.plan["seats"]["interactive_brain"]
    assert "MAY BE EVICTED" in seat["pin_unenforced"]
    assert "boom" in seat["pin_unenforced"], "the real reason is carried"
    assert "gemma4:12b" in a.ollama.resident()


def test_boot_failure_rolls_back_and_degrades():
    """When BOTH backends refuse, there is nothing to degrade to."""
    class DeadOllama(FakeOllama):
        def load(self, model_id, num_ctx, keep_alive="15m", think=False):
            raise RuntimeError("ollama is down too")

    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=DeadOllama(), llama=FakeLlama(fail=True),
                   comfy=FakeComfy(),
                   gguf_paths={"gemma4:12b": "/x/12b.gguf"})
    a.compute_plan()
    with pytest.raises(ra.TransitionError):
        a.boot(measure_baseline=False)
    assert a.state == ra.STATE_DEGRADED


def test_a_seat_without_a_gguf_says_its_pin_is_unenforced(arb):
    """Never claim a pin the backend will not honour."""
    arb.gguf_paths = {}
    arb.boot(measure_baseline=False)
    seat = arb.plan["seats"]["interactive_brain"]
    assert "MAY BE EVICTED" in seat["pin_unenforced"]


# ── heavy lease ──────────────────────────────────────────────────────────────

def test_heavy_lease_displaces_the_brain_but_not_the_sidekick(arb):
    """R10. Stephen, 2026-08-15: "keep e2b awake so Friday is always alive."

    A heavy lease takes the brain — that is the cost of depth on one card. It
    does not take the sidekick, so Friday keeps answering at 166 tok/s while
    the 26b works.
    """
    arb.boot(measure_baseline=False)
    out = arb.grant("heavy_turn")
    assert out["ok"] is True
    assert arb.state == ra.STATE_LEASED
    assert "interactive_brain" in out["lease"]["displaced"]
    assert "sidekick" not in out["lease"]["displaced"]
    assert "gemma4:12b" not in arb.llama.procs, "the brain should stand down"
    assert "gemma4:e2b" in arb.ollama.resident(), "the sidekick must not"


def test_a_retained_seat_is_not_reloaded_on_release(arb):
    """Reloading it would evict it first — a cold start and a silent window."""
    arb.boot(measure_baseline=False)
    before = dict(arb.ollama.resident()).get("gemma4:e2b")
    arb.grant("heavy_turn")
    arb.release()
    assert dict(arb.ollama.resident()).get("gemma4:e2b") == before,         "the sidekick was reloaded, so it went away and came back"


def test_release_restores_the_pinned_seats(arb):
    arb.boot(measure_baseline=False)
    arb.grant("heavy_turn")
    out = arb.release()
    assert out["ok"] is True
    assert arb.state == ra.STATE_DEFAULT
    assert set(arb.llama.procs) == {"gemma4:12b"}, "the brain comes back"
    assert "gemma4:e2b" in arb.ollama.resident(), "the sidekick never left"


def test_two_leases_cannot_be_held_at_once(arb):
    """Transitions are serial; a racing residency layer exhausts VRAM."""
    arb.boot(measure_baseline=False)
    arb.grant("heavy_turn")
    second = arb.grant("image_job")
    assert second["ok"] is False
    assert "already held" in second["error"]


def test_transition_timings_are_recorded(arb):
    arb.boot(measure_baseline=False)
    arb.grant("heavy_turn")
    assert any(t["action"] == "grant" for t in arb.transitions)
    assert all(isinstance(t["seconds"], float) for t in arb.transitions)


def test_an_expired_lease_is_reclaimed(arb):
    """A crashed holder must not strand the GPU."""
    arb.boot(measure_baseline=False)
    arb.grant("heavy_turn", ttl_s=0)
    time.sleep(0.01)
    assert arb.expire_if_due()["ok"] is True
    assert arb.lease is None
    assert arb.state == ra.STATE_DEFAULT


# ── image lease ──────────────────────────────────────────────────────────────

def test_image_lease_starts_comfyui_and_clears_the_gpu(arb):
    arb.boot(measure_baseline=False)
    out = arb.grant("image_job")
    assert out["ok"] is True
    assert arb.comfy.started is True
    # R10: exclusive of everything EXCEPT the sidekick. An image job used to
    # take the whole card, which left Friday unable to answer while a picture
    # rendered — the machine looked hung rather than busy.
    assert arb.llama.procs == {}, "an image lease takes the owned seats"
    assert set(arb.ollama.resident()) == {"gemma4:e2b"},         "R10: except the sidekick, which keeps Friday answering"


def test_image_release_stops_comfyui_and_hands_the_gpu_back(arb):
    arb.boot(measure_baseline=False)
    arb.grant("image_job")
    arb.release()
    assert arb.comfy.started is False
    assert set(arb.llama.procs) == {"gemma4:12b"}
    assert "gemma4:e2b" in arb.ollama.resident()


def test_a_failed_image_start_rolls_back(arb):
    arb.comfy = FakeComfy(fail=True)
    arb.boot(measure_baseline=False)
    out = arb.grant("image_job")
    assert out["ok"] is False
    assert out["rolled_back"] is True
    assert arb.lease is None


# ── refusals ─────────────────────────────────────────────────────────────────

def test_low_ram_refuses_the_load_and_states_why():
    tiny = dict(fx.P1, ram={"total_mib": 8192, "available_mib": 2000})
    a = ra.Arbiter(profile=tiny, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(), comfy=FakeComfy())
    a.compute_plan()
    entry = [e for e in a.entries if e["model_id"] == "gemma4:26b"][0]
    out = a.admit(entry)
    assert out["ok"] is False
    assert out["rule_id"] == "R2"
    assert "hard ceiling" in out["explanation"]


def test_a_model_already_on_disk_is_not_charged_for_its_artifact_again():
    """The disk cost of a LOAD is pagefile growth, not the artifact.

    Charging the 17 GB artifact refused a heavy lease that is perfectly fine:
    the file is already there, and what actually grows is pagefile, which
    tracks the resident set (Phase A A7 measured 27.7 -> 7.0 GB free with a
    29 GB model resident, recovering on unload).
    """
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(), comfy=FakeComfy())
    a.compute_plan()
    entry = [e for e in a.entries if e["model_id"] == "gemma4:26b"][0]
    assert round(entry["artifact_bytes"] / 1048576) > fx.P1["disk"]["free_mib"] \
        - 10240, "fixture must have too little disk for the ARTIFACT"
    assert a.admit(entry)["ok"] is True, "but the load itself is admissible"


def test_low_disk_refuses_the_load_and_names_the_pagefile():
    cramped = dict(fx.P1, disk={"free_mib": 4096, "read_mib_s": 427.0})
    a = ra.Arbiter(profile=cramped, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(), comfy=FakeComfy())
    a.compute_plan()
    entry = [e for e in a.entries if e["model_id"] == "gemma4:26b"][0]
    out = a.admit(entry)
    assert out["ok"] is False
    assert out["rule_id"] == "R8"
    assert "pagefile" in out["explanation"]


def test_a_refused_heavy_lease_leaves_the_arbiter_usable():
    tiny = dict(fx.P1, ram={"total_mib": 8192, "available_mib": 2000})
    a = ra.Arbiter(profile=tiny, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(), comfy=FakeComfy(),
                   gguf_paths={"gemma4:12b": "/x/12b.gguf"})
    a.compute_plan()
    a.boot(measure_baseline=False)
    out = a.grant("heavy_turn")
    assert out["ok"] is False
    assert a.state == ra.STATE_DEFAULT, "a refusal is not a failure"
    assert a.lease is None


# ── timeouts derive from the estimator ───────────────────────────────────────

def test_timeouts_scale_with_the_load_estimate(arb):
    """A 55 s cold load must not share a timeout with a 21 s one."""
    small = arb.timeout_for({"est_load_s": 20.0})
    big = arb.timeout_for({"est_load_s": 55.0})
    assert big > small


def test_timeout_has_a_floor_for_unmeasured_models(arb):
    assert arb.timeout_for({}) >= ra.TIMEOUT_FLOOR_S


# ── status ───────────────────────────────────────────────────────────────────

def test_status_reports_state_lease_and_residency(arb):
    arb.boot(measure_baseline=False)
    st = arb.status()
    assert st["state"] == ra.STATE_DEFAULT
    assert st["lease"] is None
    assert st["plan_seats"]["interactive_brain"] == "gemma4:12b"
    assert "gemma4:12b" in st["resident_llama_server"]
