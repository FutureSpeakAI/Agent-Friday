"""A voice call on a short card is cleared by parking the brain, not refused for
being short; Friday's own GPU residents go first; the podcast render lets go of
the card when it ends; and a refusal names the programs that hold the card.

Live: the brain (8.8 GB) loaded beside Friday's own process and left 1113 MiB
free against a 1919 MiB display reserve. The voice_call lease exists to park
that brain, but the display-reserve precheck ran before the park and refused
the one action that would have made the room.
"""
import threading

import pytest

from agent_friday.services import hardware_profile as hwp
from agent_friday.services import machine_monitor
from agent_friday.services import residency_arbiter as ra

BRAIN = "bonsai2:27b"


def _seats(vram_mib):
    return {r: {"model_id": BRAIN, "status": "pinned", "device": "gpu",
                "vram_mib": vram_mib}
            for r in ("interactive_brain", "memory_manager", "sidekick",
                      "sidekick_heavy")}


class _Llama:
    def __init__(self, live):
        self.procs = {m: ("proc", 8090) for m in live}
        self.evicted = []

    def evict(self, model_id):
        self.evicted.append(model_id)
        self.procs.pop(model_id, None)


class _Ollama:
    def resident(self):
        return {}

    def evict(self, model_id):
        pass


def _arbiter(monkeypatch, brain_mib, free_mib, reserve=1919):
    arb = object.__new__(ra.Arbiter)
    arb._lock = threading.Lock()
    arb.plan = {"seats": _seats(brain_mib), "refusals": []}
    arb.llama = _Llama({BRAIN})
    arb.ollama = _Ollama()
    arb.lease = None
    arb.state = ra.STATE_DEFAULT
    arb.seat_problems = {}
    arb.profile = {}
    arb.records = []
    arb._record = lambda *a: arb.records.append(a)
    arb._entry = lambda model_id: {}
    arb.gpu_not_ours = lambda: None
    arb._gpu_embedder = lambda: None
    monkeypatch.setattr(ra.Arbiter, "_build_hours_active",
                        staticmethod(lambda: False))
    from agent_friday.services import headroom_contract
    monkeypatch.setattr(headroom_contract, "resolve_display_reserve",
                        lambda profile: {"mib": reserve})
    monkeypatch.setattr(
        hwp, "vram_headroom",
        lambda reserve_mib=None, **k: {
            "ok": free_mib >= reserve_mib, "total_mib": 12288,
            "used_mib": 12288 - free_mib, "free_mib": free_mib,
            "display_reserve_mib": reserve_mib,
            "shortfall_mib": max(0, reserve_mib - free_mib)})
    monkeypatch.setattr(machine_monitor, "last_sample", lambda: {})
    monkeypatch.setattr(machine_monitor, "disk_system_verdict",
                        lambda s: {"status": "ok"})
    return arb


# ── 1. the park is credited ─────────────────────────────────────────────────

def test_a_voice_call_on_a_short_card_is_granted_when_the_park_frees_enough(monkeypatch):
    # The live numbers: 1113 free, 1919 reserve, an 8.8 GB brain to park.
    arb = _arbiter(monkeypatch, brain_mib=8800, free_mib=1113)
    got = arb.grant("voice_call", ttl_s=120)
    assert got["ok"], got
    assert got["lease"]["displaced"] == ["interactive_brain"]
    assert BRAIN in arb.llama.evicted


def test_a_voice_call_is_still_refused_when_even_the_park_leaves_it_short(monkeypatch):
    monkeypatch.setattr(hwp, "foreign_vram_holders", lambda **k: [])
    arb = _arbiter(monkeypatch, brain_mib=500, free_mib=1113)
    got = arb.grant("voice_call", ttl_s=120)
    assert not got["ok"]
    assert got["refused"] == {"rule_id": "R-DISPLAY-RESERVE"}
    assert "not enough VRAM left for the desktop" in got["error"]
    assert not arb.llama.evicted, "a refused lease parks nothing"
    assert arb.state == ra.STATE_DEFAULT


def test_a_lease_that_frees_nothing_keeps_the_old_refusal(monkeypatch):
    monkeypatch.setattr(hwp, "foreign_vram_holders", lambda **k: [])
    arb = _arbiter(monkeypatch, brain_mib=8800, free_mib=1113)
    got = arb.grant("image_job", ttl_s=120)      # loads; its park is not credited
    assert not got["ok"] and got["refused"] == {"rule_id": "R-DISPLAY-RESERVE"}


# ── 4. the refusal names who holds the card ─────────────────────────────────

def test_the_refusal_names_the_programs_holding_the_card(monkeypatch):
    monkeypatch.setattr(hwp, "foreign_vram_holders",
                        lambda **k: [("Brave", 540), ("Claude", 223)])
    arb = _arbiter(monkeypatch, brain_mib=0, free_mib=1113)
    got = arb.grant("voice_call", ttl_s=120)
    assert not got["ok"]
    assert "Brave (540 MiB) and Claude (223 MiB) are using the card" in got["error"]


def test_foreign_holders_read_the_counter_and_leave_friday_out(monkeypatch):
    import os
    me = os.getpid()
    monkeypatch.setattr(hwp, "_gpu_process_dedicated_mib",
                        lambda: {me: 1200, 101: 540, 102: 223, 103: 900, 104: 3})
    monkeypatch.setattr(hwp, "_own_process_ids", lambda: {me, 103})
    monkeypatch.setattr(hwp, "_process_names",
                        lambda pids: {101: "brave.exe", 102: "Claude.exe",
                                      104: "tiny.exe"})
    got = hwp.foreign_vram_holders(limit=3)
    assert got == [("Brave", 540), ("Claude", 223)]    # own pids and crumbs out
    assert hwp.holders_sentence(got) == (
        "Brave (540 MiB) and Claude (223 MiB) are using the card")


def test_foreign_holders_never_raise(monkeypatch):
    def boom():
        raise OSError("no counter")
    monkeypatch.setattr(hwp, "_gpu_process_dedicated_mib", boom)
    assert hwp.foreign_vram_holders() == []


def test_the_counter_reader_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(hwp.sys, "platform", "linux")
    assert hwp._gpu_process_dedicated_mib() == {}


def test_counter_instance_names_are_summed_per_pid():
    rows = {"pid_10_luid_0x0_0x1_phys_0": 100 * 2 ** 20,
            "pid_10_luid_0x0_0x1_phys_1": 50 * 2 ** 20,
            "pid_11_luid_0x0_0x1_phys_0": 7 * 2 ** 20,
            "garbage": 5}
    assert hwp._sum_counter_by_pid(rows) == {10: 150, 11: 7}


# ── 2. voice start clears Friday's own GPU residents before the grant ───────

def test_voice_start_releases_its_own_gpu_residents_before_the_grant(monkeypatch):
    from agent_friday.routes import voice as rv
    from agent_friday.services import local_voice, voice_workers
    order = []

    class Arb:
        def grant(self, kind, ttl_s=0):
            order.append("grant")
            return {"ok": True, "lease": {"displaced": []}}

    class Engine:
        def release_gpu(self, reason=""):
            order.append("in-process tts/asr")
            return ["tts"]

    monkeypatch.setattr(ra, "get_arbiter", lambda: Arb())
    monkeypatch.setattr(voice_workers, "release_gpu_engines",
                        lambda reason="": order.append("workers") or ["mouth"])
    monkeypatch.setattr(local_voice, "_engine", Engine())
    monkeypatch.setattr(hwp, "empty_cuda_cache",
                        lambda: order.append("empty_cache") or True)
    monkeypatch.setattr(rv, "_VOICE_LEASE_HOLDERS", set())
    rv._voice_lease_take({}, "h1")
    assert order[-1] == "grant"
    assert {"workers", "in-process tts/asr", "empty_cache"} <= set(order[:-1]), order


def test_empty_cuda_cache_never_imports_torch(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    real_import = __import__

    def guard(name, *a, **k):
        if name == "torch":
            raise AssertionError("torch imported just to empty a cache")
        return real_import(name, *a, **k)
    monkeypatch.setattr("builtins.__import__", guard)
    assert hwp.empty_cuda_cache() is False


def test_empty_cuda_cache_empties_when_torch_is_loaded(monkeypatch):
    import sys
    import types
    calls = []
    cuda = types.SimpleNamespace(is_available=lambda: True,
                                 empty_cache=lambda: calls.append("empty"))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=cuda))
    assert hwp.empty_cuda_cache() is True and calls == ["empty"]


def test_the_engine_lets_go_of_a_cuda_voice_and_keeps_a_cpu_one():
    from agent_friday.services import local_voice
    eng = local_voice.LocalVoiceEngine()

    class T:
        def __init__(self, dev):
            self._device = dev
            self.unloaded = False

        def unload(self):
            self.unloaded = True
    gpu, cpu = T("cuda"), T("cpu")
    eng._tts, eng._asr, eng._ready = gpu, cpu, True
    assert eng.release_gpu("test") == ["tts"]
    assert eng._tts is None and gpu.unloaded and eng._asr is cpu
    eng._tts = cpu
    assert eng.release_gpu("test") == [] and eng._tts is cpu


# ── 3. the podcast render lets go of the card when it ends ──────────────────

@pytest.mark.parametrize("fails", [False, True])
def test_the_podcast_render_releases_the_tts_gpu_in_a_finally(monkeypatch, fails):
    from agent_friday.services import podcast_engine as pe
    from agent_friday.services import podcast_render as render
    released = []
    monkeypatch.setattr(render, "release_tts_gpu",
                        lambda: released.append(True), raising=False)

    def run():
        if fails:
            raise render.RenderError("voice_failed", "a line failed")
        return ("pcm", ["t"])
    if fails:
        with pytest.raises(render.RenderError):
            pe._speak_then_release("e1", run, lambda t, v: b"", 3)
    else:
        assert pe._speak_then_release("e1", run, lambda t, v: b"", 3) == ("pcm", ["t"])
    assert released == [True]


def test_release_tts_gpu_ends_the_speaker_and_empties_the_cache(monkeypatch):
    from agent_friday.services import podcast_render as render
    calls = []
    monkeypatch.setattr(render, "release_speaker", lambda: calls.append("speaker"))
    monkeypatch.setattr(hwp, "empty_cuda_cache", lambda: calls.append("cache"))
    render.release_tts_gpu()
    assert calls == ["speaker", "cache"]
