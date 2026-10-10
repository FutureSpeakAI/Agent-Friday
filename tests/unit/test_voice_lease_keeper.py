"""The voice-call lease outlives a cold start.

The lease TTL is 120 s and only the call heartbeat (after the session is up)
renewed it. A start whose ear, mouth and front loads took longer let the lease
lapse; the scheduler's expiry tick then released it, the brain came back
mid-start and the front was refused its room. A keeper renews from the grant
until the lease is given back, bounded so a hung start still frees the brain.
"""
import threading
import time

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import hardware_profile, machine_monitor
from agent_friday.services import residency_arbiter as ra

BRAIN = "bonsai2:27b"
SEATS = {r: {"model_id": BRAIN, "status": "pinned", "device": "gpu"}
         for r in ("interactive_brain", "memory_manager", "sidekick",
                   "sidekick_heavy")}


class _Llama:
    def __init__(self):
        self.procs = {BRAIN: ("proc", 8090)}

    def evict(self, model_id):
        self.procs.pop(model_id, None)


class _Ollama:
    def resident(self):
        return {}

    def evict(self, model_id):
        pass


@pytest.fixture()
def arb(monkeypatch):
    a = object.__new__(ra.Arbiter)
    a._lock = threading.Lock()
    a.plan = {"seats": {r: dict(s) for r, s in SEATS.items()}, "refusals": []}
    a.llama, a.ollama = _Llama(), _Ollama()
    a.lease, a.state = None, ra.STATE_DEFAULT
    a.seat_problems, a.profile, a.records = {}, {}, []
    a._record = lambda *x: a.records.append(x)
    a._entry = lambda m: {}
    a.gpu_not_ours = lambda: None
    a._gpu_embedder = lambda: None
    a.loads = []
    a._load_pinned = lambda seat, role: (
        a.loads.append(role), a.llama.procs.__setitem__(seat["model_id"], ("p", 1)))
    monkeypatch.setattr(ra.Arbiter, "_build_hours_active", staticmethod(lambda: False))
    monkeypatch.setattr(hardware_profile, "vram_headroom",
                        lambda reserve_mib=None, **k: {"ok": True, "total_mib": 12288})
    monkeypatch.setattr(machine_monitor, "last_sample", lambda: {})
    monkeypatch.setattr(machine_monitor, "disk_system_verdict", lambda s: {"status": "ok"})
    monkeypatch.setattr(ra, "get_arbiter", lambda: a)
    from agent_friday.services import voice_workers
    monkeypatch.setattr(voice_workers, "release_own_gpu", lambda r="": {})
    monkeypatch.setattr(voice_workers, "release_gpu_engines", lambda r="": [])
    monkeypatch.setattr(rv, "_VOICE_LEASE_HOLDERS", set())
    monkeypatch.setattr(rv, "_voice_lease_ttl", lambda s: 0.4)
    monkeypatch.setattr(rv, "VOICE_LEASE_KEEP_EVERY_S", 0.05, raising=False)
    yield a
    rv._voice_lease_keeper_stop() if hasattr(rv, "_voice_lease_keeper_stop") else None


def _keepers():
    return [t for t in threading.enumerate() if t.name == "voice-lease-keeper"]


def test_a_start_that_outlasts_the_ttl_keeps_the_lease_and_the_brain_parked(arb):
    rv._voice_lease_take({}, "h1")
    time.sleep(1.0)                      # stage loads, 2.5x the TTL
    assert arb.expire_if_due() == {"ok": True, "note": "not due"}
    assert arb.lease and arb.lease["kind"] == "voice_call"
    assert BRAIN not in arb.llama.procs and arb.loads == []   # brain not restored
    rv._voice_lease_give("h1")
    assert arb.lease is None and arb.loads == ["interactive_brain"]


def test_a_start_that_fails_gives_the_lease_back_and_the_keeper_stops(arb):
    rv._voice_lease_take({}, "h1")
    assert len(_keepers()) == 1
    rv._voice_lease_give("h1")           # every failure path ends here
    assert arb.lease is None
    assert _keepers() == []


def test_a_refused_grant_starts_no_keeper(arb, monkeypatch):
    monkeypatch.setattr(arb, "grant", lambda *a, **k: {"ok": False, "error": "no"})
    with pytest.raises(RuntimeError):
        rv._voice_lease_take({}, "h1")
    assert _keepers() == []


def test_a_hung_start_still_lets_the_brain_come_back(arb, monkeypatch):
    monkeypatch.setattr(rv, "VOICE_LEASE_KEEP_MAX_S", 0.3, raising=False)
    rv._voice_lease_take({}, "h1")
    time.sleep(1.2)                      # past the bound plus one TTL
    assert _keepers() == []              # the keeper stopped on its own
    res = arb.expire_if_due()
    assert res.get("ok") and arb.lease is None and arb.loads == ["interactive_brain"]


def test_two_calls_share_one_keeper_that_stops_with_the_last(arb):
    rv._voice_lease_take({}, "a")
    rv._voice_lease_take({}, "b")
    assert len(_keepers()) == 1
    rv._voice_lease_give("a")
    assert len(_keepers()) == 1
    rv._voice_lease_give("b")
    assert _keepers() == []
