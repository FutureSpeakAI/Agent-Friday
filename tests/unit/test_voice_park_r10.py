"""The voice-call lease parks the brain even on a one-model plan; R10 holds
for every other lease; a release that cannot bring the brain back says so.

Live, every role (interactive_brain, memory_manager, sidekick,
sidekick_heavy) was bonsai2:27b: one process. `_evict_pinned` keeps any
model the retained sidekick serves (R10, "keep e2b awake so Friday is
always alive"), so the voice_call grant displaced nothing and the call's
voice never fitted. During a local call Friday is not mute (the voice front
is a live local model), so for that lease only the brain's model goes.
"""
import threading

import pytest

from agent_friday.services import residency_arbiter as ra

BRAIN = "bonsai2:27b"
ONE_MODEL = {r: {"model_id": BRAIN, "status": "pinned", "device": "gpu"}
             for r in ("interactive_brain", "memory_manager", "sidekick", "sidekick_heavy")}
TWO_MODELS = dict(ONE_MODEL, sidekick={"model_id": "gemma4:e2b", "status": "pinned",
                                       "device": "gpu"})


class _Llama:
    def __init__(self, live):
        self.procs = {m: ("proc", 8090) for m in live}
        self.evicted = []

    def evict(self, model_id):
        self.evicted.append(model_id)
        self.procs.pop(model_id, None)


class _Ollama:
    def resident(self):
        return []

    def evict(self, model_id):
        pass


class _Comfy:
    reserve_vram_mib = 0

    def start(self):
        return 0.1

    def stop(self):
        pass


def _arbiter(seats, monkeypatch, load_ok=True, load_raises=False):
    arb = object.__new__(ra.Arbiter)
    arb._lock = threading.Lock()
    arb.plan = {"seats": {r: dict(s) for r, s in seats.items()}, "refusals": []}
    live = {s["model_id"] for s in seats.values()}
    arb.llama = _Llama(live)
    arb.ollama = _Ollama()
    arb.comfy = _Comfy()
    arb.lease = None
    arb.state = ra.STATE_DEFAULT
    arb.seat_problems = {}
    arb.profile = {}
    arb.records = []
    arb._record = lambda *a: arb.records.append(a)
    arb._entry = lambda model_id: {}
    arb.gpu_not_ours = lambda: None
    arb._gpu_embedder = lambda: None
    arb.loads = []

    def load(seat, role):
        arb.loads.append(role)
        if load_raises:
            raise RuntimeError("llama-server exited: out of memory")
        if load_ok:
            arb.llama.procs[seat["model_id"]] = ("proc", 8090)
    arb._load_pinned = load
    monkeypatch.setattr(ra.Arbiter, "_build_hours_active", staticmethod(lambda: False))
    # The grant's display and disk checks read this machine: keep them benign.
    from agent_friday.services import hardware_profile, machine_monitor
    monkeypatch.setattr(hardware_profile, "vram_headroom",
                        lambda reserve_mib=None, **k: {"ok": True, "total_mib": 12288})
    monkeypatch.setattr(machine_monitor, "last_sample", lambda: {})
    monkeypatch.setattr(machine_monitor, "disk_system_verdict", lambda s: {"status": "ok"})
    monkeypatch.setattr(ra, "_restore_sleep", lambda s: arb.records.append(("sleep", s)),
                        raising=False)
    return arb


def test_a_voice_call_parks_the_brain_on_a_one_model_plan(monkeypatch):
    arb = _arbiter(ONE_MODEL, monkeypatch)
    got = arb.grant("voice_call", ttl_s=120)
    assert got["ok"], got
    assert got["lease"]["displaced"] == ["interactive_brain"]
    assert arb.llama.evicted == [BRAIN] and BRAIN not in arb.llama.procs


@pytest.mark.parametrize("kind", ["image_job", "bench_job", "podcast_voice"])
def test_every_other_lease_keeps_r10(monkeypatch, kind):
    arb = _arbiter(ONE_MODEL, monkeypatch)
    got = arb.grant(kind, ttl_s=120)
    assert got["ok"], got
    assert BRAIN not in arb.llama.evicted and BRAIN in arb.llama.procs, (kind, got)


def test_the_sidekick_role_is_never_displaced_by_name(monkeypatch):
    # Two models: the brain goes for the call, the separate sidekick stays.
    arb = _arbiter(TWO_MODELS, monkeypatch)
    got = arb.grant("voice_call", ttl_s=120)
    assert got["lease"]["displaced"] == ["interactive_brain"]
    assert "gemma4:e2b" in arb.llama.procs


def test_release_restores_the_brain_through_restore_pinned(monkeypatch):
    arb = _arbiter(ONE_MODEL, monkeypatch)
    arb.grant("voice_call", ttl_s=120)
    res = arb.release(kind="voice_call")
    assert res["ok"] and arb.loads == ["interactive_brain"] and BRAIN in arb.llama.procs
    assert arb.lease is None and arb.state == ra.STATE_DEFAULT and not arb.seat_problems


@pytest.mark.parametrize("how", ["raises", "not_serving"])
def test_a_failed_restore_is_retried_and_reported(monkeypatch, how):
    arb = _arbiter(ONE_MODEL, monkeypatch, load_ok=False, load_raises=(how == "raises"))
    arb.grant("voice_call", ttl_s=120)
    res = arb.release(kind="voice_call")
    assert res["ok"] is False and BRAIN in res["error"], res
    assert arb.loads == ["interactive_brain"] * ra.Arbiter.RESTORE_TRIES
    assert [r for r in arb.records if r[0] == "sleep"] == [("sleep", 1.0), ("sleep", 2.0)]
    assert arb.seat_problems[BRAIN]["role"] == "interactive_brain"
    assert "not restored after a lease" in arb.seat_problems[BRAIN]["reason"]
    assert arb.state == ra.STATE_DEGRADED and arb.lease is None


def test_a_restore_that_succeeds_on_the_second_try_is_ok(monkeypatch):
    arb = _arbiter(ONE_MODEL, monkeypatch, load_ok=False)
    arb.grant("voice_call", ttl_s=120)
    tries = []

    def load(seat, role):
        tries.append(role)
        if len(tries) == 2:
            arb.llama.procs[seat["model_id"]] = ("proc", 8090)
    arb._load_pinned = load
    assert arb.release(kind="voice_call")["ok"] and len(tries) == 2
