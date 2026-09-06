"""Intrusion response — headroom.md §7, §12 Phase 5.

Only the rows this phase answers (§7's table, D1-independent):
  * display reserve breached | anything -> cancel in-flight render, release
    every lease, evict leased seats, keep the retained sidekick only if it
    still fits.
  * disk system-volume floor breached | anything -> refuse every load
    (Arbiter.grant()'s R-DISK-SYSTEM) and every fetch
    (routes.skills.ollama_pull).
  * thrash signature breached | holding a lease -> mark the leased model's
    footprint degraded, sample attached, so `residency_policy.verdicts()`'s
    `runs_well` reflects it next time.

`vram_slack`/`ram_available` breaches are D1-gated and intentionally not
exercised here — `machine_monitor.verdict()` itself never reports them as
anything but `basis: "unknown"`.

Offline: `machine_monitor.sample`/`verdict` and `hardware_profile.
vram_headroom` are monkeypatched, matching the sibling
test_run_chain.py / test_headroom_contract.py's own "Offline" rule — no
real nvidia-smi/PowerShell/ComfyUI calls.
"""
from __future__ import annotations

import pytest

from agent_friday import core
from agent_friday.services import hardware_profile as hwp
from agent_friday.services import local_image as li
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


def _verdict(display="ok", disk_system="ok", thrash="ok", explanation=""):
    return {
        "display": {"status": display, "basis": "measured",
                   "explanation": explanation or "display %s" % display},
        "disk_system": {"status": disk_system, "basis": "measured",
                       "explanation": explanation or "disk %s" % disk_system},
        "vram_slack": {"status": "unknown", "basis": "unknown"},
        "ram_available": {"status": "unknown", "basis": "unknown"},
        "thrash": {"status": thrash, "basis": "measured",
                  "explanation": explanation or "thrash %s" % thrash},
    }


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "store_path", lambda: tmp_path / "m.json")
    rc.reset_cache()


@pytest.fixture(autouse=True)
def _reset_monitor_state():
    """Process-global state (`_last_sample`, the thrash history) must not
    leak between tests -- the same discipline test_machine_monitor.py's own
    `_reset_module_state` fixture applies."""
    mm.reset_last_sample_for_tests()
    mm.reset_thrash_history_for_tests()
    yield
    mm.reset_last_sample_for_tests()
    mm.reset_thrash_history_for_tests()


@pytest.fixture(autouse=True)
def clean_processes():
    with core.PROCESSES_LOCK:
        core.PROCESSES.clear()
    yield
    with core.PROCESSES_LOCK:
        core.PROCESSES.clear()


@pytest.fixture
def arb(monkeypatch):
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(), comfy=FakeComfy(),
                   gguf_paths={"gemma4:e4b": "/x/e4b.gguf",
                               "gemma4:e2b": "/x/e2b.gguf"})
    a.compute_plan()
    # No real GPU probing: the display-reserve gate in grant() is skipped
    # whenever `total_mib` is absent (the same "no GPU detected" branch
    # `vram_headroom` itself returns), so tests aimed at OTHER rows do not
    # also have to fabricate a passing VRAM figure.
    monkeypatch.setattr(hwp, "vram_headroom", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(mm, "sample", lambda **kw: {"gpus": [],
                                                    "disk_system_free_mib":
                                                    50000})
    monkeypatch.setattr(mm, "verdict",
                        lambda *a, **k: _verdict())
    return a


# ── display reserve breach: cancel the render, release every lease ─────────

def test_display_breach_cancels_registered_render_and_releases(
        arb, monkeypatch):
    granted = arb.grant("image_job")
    assert granted["ok"] is True
    assert arb.comfy.started is True

    pid = "image-abc123"
    with core.PROCESSES_LOCK:
        core.PROCESSES[pid] = {"id": pid, "status": "running"}

    cancelled_ids = []
    monkeypatch.setattr(li, "request_cancel",
                        lambda job_id: cancelled_ids.append(job_id) or True)
    monkeypatch.setattr(li, "interrupt_comfy", lambda *a, **k: True)

    out = arb.respond_to_monitor(
        {"gpus": []}, _verdict(display="breached",
                               explanation="322 MiB free, short by 2,238"))

    assert out["action"] == "display_breach"
    assert cancelled_ids == [pid]
    assert out["cancelled_render"] == [pid]
    with core.PROCESSES_LOCK:
        assert core.PROCESSES[pid]["status"] == "cancelled"
    assert arb.lease is None
    assert arb.comfy.started is False


def test_display_breach_with_no_render_still_releases_the_lease(
        arb, monkeypatch):
    """§7: 'anything' -- a heavy_turn lease with no render in flight is
    still released on a display-reserve breach."""
    monkeypatch.setattr(li, "request_cancel", lambda job_id: True)
    monkeypatch.setattr(li, "interrupt_comfy", lambda *a, **k: True)
    arb.plan["seats"]["heavy_hitter"] = {"model_id": "gemma4:e4b",
                                         "num_ctx": 8192,
                                         "device": "gpu:0"}
    granted = arb.grant("heavy_turn")
    assert granted["ok"] is True

    out = arb.respond_to_monitor(
        {"gpus": []}, _verdict(display="breached"))
    assert out["cancelled_render"] == []
    assert arb.lease is None


def test_display_breach_does_nothing_with_no_lease_held(arb):
    """respond_to_monitor is a no-op with nothing of ours at risk."""
    assert arb.respond_to_monitor(
        {"gpus": []}, _verdict(display="breached")) is None


def test_retained_sidekick_evicted_only_if_still_short_after_release(
        arb, monkeypatch):
    """§7's own line: 'Keep the retained sidekick only if it still fits
    inside the reserve; otherwise it goes too.' Releasing our own lease
    clears the breach here -- the retained seat stays."""
    arb.plan["seats"]["sidekick"] = {"model_id": "gemma4:e2b",
                                     "num_ctx": 8192, "device": "gpu:0"}
    arb.ollama.load("gemma4:e2b", 8192)
    granted = arb.grant("image_job")
    assert granted["ok"] is True

    out = arb.respond_to_monitor({"gpus": []}, _verdict(display="breached"))
    assert out["evicted_retained"] == []
    assert "gemma4:e2b" in arb.ollama.resident()


def test_retained_sidekick_evicted_when_still_short_after_release(
        arb, monkeypatch):
    """A foreign tenant, not our own lease, is the cause -- releasing does
    not clear it, so the retained seat is not exempt (§7)."""
    arb.plan["seats"]["sidekick"] = {"model_id": "gemma4:e2b",
                                     "num_ctx": 8192, "device": "gpu:0"}
    arb.ollama.load("gemma4:e2b", 8192)
    granted = arb.grant("image_job")
    assert granted["ok"] is True

    # Every verdict from here on -- including the post-release recheck --
    # keeps reporting the breach.
    monkeypatch.setattr(mm, "verdict",
                        lambda *a, **k: _verdict(display="breached"))
    out = arb.respond_to_monitor({"gpus": []}, _verdict(display="breached"))
    assert out["evicted_retained"] == ["gemma4:e2b"]
    assert "gemma4:e2b" not in arb.ollama.resident()


# ── disk system-volume floor: refuse every load ─────────────────────────────

def test_grant_refuses_a_new_load_on_a_disk_system_breach(arb, monkeypatch):
    """`grant()`'s R-DISK-SYSTEM check calls `disk_system_verdict()`
    directly (not the mocked `verdict()`, to avoid stealing a "tick" from
    whatever else is counting calls to it -- see the fix for
    test_run_chain's own call-count regression), so this drives it through
    a real low `disk_system_free_mib` on the sample instead."""
    arb.plan["seats"]["heavy_hitter"] = {"model_id": "gemma4:e4b",
                                         "num_ctx": 8192,
                                         "device": "gpu:0"}
    monkeypatch.setattr(mm, "sample",
                        lambda **kw: {"gpus": [],
                                     "disk_system_free_mib": 512})
    result = arb.grant("heavy_turn")
    assert result["ok"] is False
    assert result["refused"]["rule_id"] == "R-DISK-SYSTEM"
    assert "512" in result["error"]
    assert arb.lease is None


def test_grant_still_passes_when_disk_system_is_healthy(arb):
    arb.plan["seats"]["heavy_hitter"] = {"model_id": "gemma4:e4b",
                                         "num_ctx": 8192,
                                         "device": "gpu:0"}
    result = arb.grant("heavy_turn")
    assert result["ok"] is True
    assert result.get("refused", {}).get("rule_id") != "R-DISK-SYSTEM"
    arb.release()


# ── thrash signature breached while leased: mark the footprint degraded ────

def test_thrash_breach_marks_the_leased_models_footprint_degraded(arb):
    fp = rc.make_footprint(modality="image", device="gpu", basis="measured",
                           vram_mib=1000, measured_at="2026-09-04")
    rc.record_footprint("z-image-turbo-fp8", rc.profile_fingerprint(fx.P1), fp)

    granted = arb.grant("image_job")
    assert granted["ok"] is True
    assert arb.lease["model_id"] == "z-image-turbo-fp8"

    sample_ = {"gpus": [{"index": 0, "util_pct": 100, "power_w": 51,
                        "power_limit_w": 200}]}
    out = arb.respond_to_monitor(
        sample_, _verdict(thrash="breached",
                          explanation="100% util, 51 of 200W"))
    assert out["action"] == "thrash_breach"
    assert out["model_id"] == "z-image-turbo-fp8"

    marked = rc.thrash_degraded("z-image-turbo-fp8",
                                rc.profile_fingerprint(fx.P1))
    assert marked is not None
    assert marked["sample"] == sample_
    assert "51 of 200W" in marked["explanation"]

    # §5.2: runs_well reflects it on the NEXT verdicts() call.
    v = rp.verdicts({"model_id": "z-image-turbo-fp8"}, fx.P1)
    assert v["runs_well"]["status"] == "degraded"
    assert "thrash" in v["runs_well"]["explanation"].lower()
    # Never a refusal on this axis (§5.2's table has no `refused` value
    # for runs_well).
    assert v["runs_well"]["status"] != "refused"


def test_thrash_breach_is_debounced_not_re_recorded_every_tick(arb):
    fp = rc.make_footprint(modality="image", device="gpu", basis="measured",
                           vram_mib=1000, measured_at="2026-09-04")
    rc.record_footprint("z-image-turbo-fp8", rc.profile_fingerprint(fx.P1), fp)
    arb.grant("image_job")

    first = arb.respond_to_monitor({"gpus": []}, _verdict(thrash="breached"))
    second = arb.respond_to_monitor({"gpus": []}, _verdict(thrash="breached"))
    assert first is not None
    assert second is None    # debounced -- same 5-minute window


def test_thrash_breach_with_no_lease_is_a_no_op(arb):
    assert arb.respond_to_monitor({"gpus": []},
                                  _verdict(thrash="breached")) is None
