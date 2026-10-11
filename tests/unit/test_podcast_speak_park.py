"""The podcast speak stage parks the brain when local speech needs room, and
the brain always comes back.

On a one-model plan every role (interactive_brain, memory_manager, sidekick,
sidekick_heavy) is one model. The podcast_voice lease kept any model the
retained sidekick serves (R10), so it parked nothing, the room check failed
at once, and the episode waited "for room" with the brain still loaded. The
lease parks the brain's model, the stage waits (bounded) for the memory to
settle, a refusal is said plainly, every exit gives the lease back once with
the brain restored, and a brain left parked with nothing holding it down is
asked back after the episode speaks.

Offline: the real Arbiter's grant, heavy_job and release over fake backends.
"""
import re
import threading

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_render as render
from agent_friday.services import residency_arbiter as ra

BRAIN = "bonsai2:27b"
ONE_MODEL = {r: {"model_id": BRAIN, "status": "pinned", "device": "gpu"}
             for r in ("interactive_brain", "memory_manager", "sidekick", "sidekick_heavy")}


class _Llama:
    def __init__(self, live):
        self.procs = {m: ("proc", 8090) for m in live}
        self.evicted = []

    def evict(self, model_id):
        self.evicted.append(model_id)
        self.procs.pop(model_id, None)

    def evict_all(self):
        self.procs.clear()

    def verify(self, model_id):
        return {"ok": model_id in self.procs, "model_id": model_id}


class _Ollama:
    def resident(self):
        return {}

    def evict(self, model_id):
        pass


class _Comfy:
    reserve_vram_mib = 0

    def start(self):
        return 0.1

    def stop(self):
        pass


def _arbiter(monkeypatch):
    arb = object.__new__(ra.Arbiter)
    arb._lock = threading.RLock()
    arb.plan = {"seats": {r: dict(s) for r, s in ONE_MODEL.items()}, "refusals": []}
    arb.llama = _Llama({BRAIN})
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
    arb._write_heavy_receipt = lambda *a: None
    arb.loads = []

    def load(seat, role):
        arb.loads.append(role)
        arb.llama.procs[seat["model_id"]] = ("proc", 8090)
    arb._load_pinned = load
    arb.releases = []
    real_release = arb.release

    def release(kind=None):
        arb.releases.append(kind)
        return real_release(kind)
    arb.release = release
    monkeypatch.setattr(ra.Arbiter, "_build_hours_active", staticmethod(lambda: False))
    from agent_friday.services import hardware_profile, machine_monitor, stand_down
    monkeypatch.setattr(hardware_profile, "vram_headroom",
                        lambda reserve_mib=None, **k: {"ok": True, "total_mib": 12288})
    monkeypatch.setattr(machine_monitor, "last_sample", lambda: {})
    monkeypatch.setattr(machine_monitor, "disk_system_verdict", lambda s: {"status": "ok"})
    monkeypatch.setattr(stand_down, "is_active_now", lambda: False)
    monkeypatch.setattr(ra, "_restore_sleep", lambda s: None, raising=False)
    monkeypatch.setattr(ra, "get_arbiter", lambda: arb)
    return arb


@pytest.fixture()
def machine(monkeypatch):
    """No speaker running; host memory is short while the brain is loaded."""
    arb = _arbiter(monkeypatch)
    room = {"settle_polls": 0, "polls": 0}

    def headroom():
        if BRAIN in arb.llama.procs:
            return 1500                     # the brain holds the room the voice needs
        room["polls"] += 1
        return 1500 if room["polls"] <= room["settle_polls"] else 9000
    monkeypatch.setattr(render, "speaker_running", lambda: False)
    monkeypatch.setattr(render, "commit_headroom_mb", headroom)
    monkeypatch.setattr(render, "release_speaker", lambda: None)
    monkeypatch.setattr(render, "release_tts_gpu", lambda: None, raising=False)
    monkeypatch.setattr(pe, "_update", lambda eid, **kw: {"id": eid, **kw})
    monkeypatch.setattr(pe, "_room_sleep", lambda s: None, raising=False)
    monkeypatch.setattr(pe, "_in_background", lambda fn: fn(), raising=False)
    return arb, room


def _brain_back(arb):
    assert BRAIN in arb.llama.procs, "the brain is serving again"
    assert arb.lease is None and arb.state == ra.STATE_DEFAULT


def test_low_ram_with_a_parkable_brain_parks_it_then_speaks(machine):
    arb, _room = machine
    seen = {}

    def speak():
        seen["during"] = set(arb.llama.procs)
        return b"pcm", [0.0]
    assert pe._speak_with_room("e1", speak, 3) == (b"pcm", [0.0])
    assert BRAIN not in seen["during"], "the brain is parked while the episode is spoken"
    _brain_back(arb)
    assert arb.loads == ["interactive_brain"], "restored once, through the arbiter"


def test_the_stage_waits_for_the_parked_memory_to_settle(machine):
    arb, room = machine
    room["settle_polls"] = 3              # the brain's memory comes back a few seconds late
    assert pe._speak_with_room("e1", lambda: (b"pcm", [0.0]), 3) == (b"pcm", [0.0])
    assert room["polls"] == 4
    _brain_back(arb)


def test_the_wait_for_room_is_bounded(machine, monkeypatch):
    arb, room = machine
    room["settle_polls"] = 10 ** 6        # never settles
    clock = {"t": 0.0}
    monkeypatch.setattr(pe.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(pe, "_room_sleep", lambda s: clock.__setitem__("t", clock["t"] + s),
                        raising=False)
    with pytest.raises(render.RenderError) as e:
        pe._speak_with_room("e1", lambda: pytest.fail("never spoken without room"), 3)
    assert e.value.code == "low_memory"
    assert "still short with the main model set aside" in str(e.value)
    assert clock["t"] <= pe.ROOM_SETTLE_S + pe.ROOM_POLL_S
    _brain_back(arb)
    assert len(arb.releases) == 1, arb.releases


@pytest.mark.parametrize("refusal,said", [
    ("display", "the graphics card has no room to spare for the display"),
    ("held", "another job is using the local models"),
])
def test_a_refused_park_says_why(machine, monkeypatch, refusal, said):
    arb, _room = machine
    if refusal == "display":
        from agent_friday.services import hardware_profile
        monkeypatch.setattr(hardware_profile, "vram_headroom", lambda reserve_mib=None, **k: {
            "ok": False, "total_mib": 12288, "free_mib": 100,
            "display_reserve_mib": 1024, "shortfall_mib": 999999})
        monkeypatch.setattr(hardware_profile, "foreign_vram_holders", lambda *a, **k: [],
                            raising=False)
    else:
        arb.lease = {"kind": "voice_call", "displaced": [], "expires_at": 1e18}
    with pytest.raises(render.RenderError) as e:
        pe._speak_with_room("e1", lambda: pytest.fail("never spoken without room"), 3)
    assert e.value.code == "low_memory" and e.value.code in pe.RETRYABLE
    assert said in str(e.value), str(e.value)
    assert not re.search(r"\d", str(e.value)), "the owner reads a sentence, not figures"
    assert BRAIN not in arb.llama.evicted, "a refused lease parks nothing"


@pytest.mark.parametrize("outcome", ["spoken", "voice_failed", "memory_error", "cancelled"])
def test_every_exit_gives_the_lease_back_once_and_restores_the_brain(machine, outcome):
    arb, room = machine
    room["settle_polls"] = 2
    polls = {"n": 0}

    def stop():
        polls["n"] += 1
        return outcome == "cancelled" and polls["n"] >= 2

    def speak():
        if outcome == "voice_failed":
            raise render.RenderError("voice_failed", "a line failed")
        if outcome == "memory_error":
            raise MemoryError()
        return b"pcm", [0.0]
    if outcome == "spoken":
        assert pe._speak_then_release("e1", speak, None, 3, should_stop=stop) == (b"pcm", [0.0])
    else:
        with pytest.raises(render.RenderError) as e:
            pe._speak_then_release("e1", speak, None, 3, should_stop=stop)
        want = {"voice_failed": "voice_failed", "memory_error": "low_memory",
                "cancelled": "cancelled"}[outcome]
        assert e.value.code == want, (outcome, e.value.code, str(e.value))
    assert len(arb.releases) == 1, arb.releases
    assert arb.loads == ["interactive_brain"]
    _brain_back(arb)


def test_a_lease_still_held_when_the_job_call_fails_is_given_back(machine, monkeypatch):
    arb, _room = machine
    real = arb.heavy_job

    def broken(kind, job, **kw):
        arb.grant(kind, ttl_s=60)
        raise RuntimeError("the job thread could not start")
    arb.heavy_job = broken
    with pytest.raises(RuntimeError):
        pe._speak_with_room("e1", lambda: (b"pcm", [0.0]), 3)
    assert arb.releases == ["podcast_voice"]
    _brain_back(arb)
    arb.heavy_job = real


def test_a_brain_left_parked_with_nothing_holding_it_is_asked_back(machine):
    arb, _room = machine
    arb.llama.procs.clear()               # parked by hand (release-for-quit), no lease
    assert pe._speak_then_release("e1", lambda: (b"pcm", [0.0]), None, 3) == (b"pcm", [0.0])
    _brain_back(arb)
    assert any(r[0] == "restore-unheld" for r in arb.records)


@pytest.mark.parametrize("holder", ["stand_down", "lease"])
def test_a_brain_held_down_on_purpose_stays_down(machine, monkeypatch, holder):
    arb, _room = machine
    arb.llama.procs.clear()
    if holder == "stand_down":
        from agent_friday.services import stand_down
        monkeypatch.setattr(stand_down, "is_active_now", lambda: True)
    else:
        arb.lease = {"kind": "bench_job", "displaced": [], "expires_at": 1e18}
    assert pe._speak_then_release("e1", lambda: (b"pcm", [0.0]), None, 3) == (b"pcm", [0.0])
    assert BRAIN not in arb.llama.procs and arb.loads == []
