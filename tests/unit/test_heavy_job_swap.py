"""The deterministic swap for a heavy local job (Arbiter.heavy_job).

Any job that needs the card to itself must: record the resident seat, evict
it, run, confirm it finished (output files present or a recorded failure),
evict the job's model, restore the previous seat, and verify the restore with
a real completion. Failure paths restore too. During build hours the previous
seat is the parked state and nothing relaunches it.

Offline: fakes for every backend, the GPU and the displays, as in
test_residency_arbiter.py.
"""
from __future__ import annotations

import datetime as dt
import json
import time

import pytest

from agent_friday.services import build_hours
from agent_friday.services import context_budget
from agent_friday.services import residency_arbiter as ra
from tests import residency_fixtures as fx


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(context_budget, "injected_tokens",
                        lambda: (context_budget.MEASURED_INJECTED_TOKENS, "reference"))
    context_budget.reset_cache()
    fx.offline_machine(monkeypatch)
    monkeypatch.delenv("FRIDAY_BUILD_HOURS_FLAG", raising=False)
    yield
    context_budget.reset_cache()


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
        self._res.clear()


class FakeLlama:
    """A llama-server backend that remembers every load and can be asked
    for a completion, which is what the restore verification needs."""
    name = "llama-server"

    def __init__(self):
        self.procs = {}
        self.loads = []
        self.verified = []
        self.answers = True

    def resident(self):
        return {m: 0 for m in self.procs}

    def load(self, model_id, num_ctx, *, gguf_path, port, n_cpu_moe=None,
             timeout=300, **kw):
        self.loads.append(model_id)
        self.procs[model_id] = (object(), port)
        return 1.0

    def evict(self, model_id):
        self.procs.pop(model_id, None)

    def evict_all(self):
        self.procs.clear()

    def adopt_or_reap(self, wanted):
        return {"adopted": [], "reaped": []}

    def verify(self, model_id, timeout=120):
        self.verified.append(model_id)
        if model_id not in self.procs:
            return {"ok": False, "model_id": model_id,
                    "error": "no process is serving it"}
        return {"ok": self.answers, "model_id": model_id, "answer": "OK",
                "latency_s": 0.01}


class FakeComfy:
    def __init__(self):
        self.started = False
        self.starts = 0
        self.stops = 0
        self.reserve_vram_mib = None

    def running(self):
        return self.started

    def start(self, timeout=300):
        self.started = True
        self.starts += 1
        return 3.0

    def stop(self):
        if self.started:
            self.stops += 1
        self.started = False


@pytest.fixture
def arb():
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(), comfy=FakeComfy(),
                   gguf_paths={"gemma4:12b": "/x/12b.gguf",
                               "gemma4:e2b": "/x/e2b.gguf"})
    a.compute_plan()
    a.boot(measure_baseline=False)
    return a


def _steps(out):
    return [s["step"] for s in json.load(open(out["receipt"], encoding="utf-8"))["steps"]]


# ── a successful job ─────────────────────────────────────────────────────────

def test_a_successful_job_evicts_runs_confirms_restores_and_verifies(arb, tmp_path):
    f = tmp_path / "friday_local_00001_.png"
    seen = {}

    def job():
        # The brain is gone and the image engine is up while the job runs.
        seen["during"] = (set(arb.llama.procs), arb.comfy.started)
        f.write_bytes(b"\x89PNG" + b"x" * 64)
        return {"files": [str(f)]}

    out = arb.heavy_job("image_job", job, timeout_s=5, job_id="image-t1")
    assert out["ok"] is True and out["status"] == "ok"
    assert seen["during"] == ({"gemma4:e2b"}, True), \
        "the brain stands down and ComfyUI is running while the job runs"
    assert arb.comfy.started is False, "the job model is evicted afterwards"
    assert set(arb.llama.procs) == {"gemma4:12b", "gemma4:e2b"}, \
        "the previous seat is back"
    assert "gemma4:12b" in arb.llama.verified, \
        "the restore is verified with a real completion on the brain"
    assert out["restored"] is True and out["verified"] is True
    assert out["previous"]["seats"]["interactive_brain"] == "gemma4:12b"
    assert arb.lease is None and arb.state == ra.STATE_DEFAULT
    assert _steps(out) == ["record-previous", "evict-and-grant", "run",
                           "confirm", "evict-job-model-and-restore", "verify"]


def test_a_job_whose_output_file_is_missing_is_not_a_success(arb, tmp_path):
    out = arb.heavy_job("image_job", lambda: {"files": [str(tmp_path / "nope.png")]},
                        timeout_s=5, job_id="image-t2")
    assert out["ok"] is False and out["status"] == "failed"
    assert "completion not confirmed" in out["error"]
    assert set(arb.llama.procs) == {"gemma4:12b", "gemma4:e2b"}
    assert "gemma4:12b" in arb.llama.verified


# ── a failed job ─────────────────────────────────────────────────────────────

def test_a_failed_job_still_restores_and_verifies_the_previous_seat(arb):
    def job():
        raise RuntimeError("ComfyUI ended the prompt without producing an image")

    out = arb.heavy_job("image_job", job, timeout_s=5, job_id="image-t3")
    assert out["ok"] is False and out["status"] == "failed"
    assert "RuntimeError" in out["error"]
    assert isinstance(out["exception"], RuntimeError)
    assert arb.comfy.started is False
    assert set(arb.llama.procs) == {"gemma4:12b", "gemma4:e2b"}
    assert "gemma4:12b" in arb.llama.verified
    assert out["restored"] is True and out["verified"] is True
    assert arb.lease is None and arb.state == ra.STATE_DEFAULT
    rec = json.load(open(out["receipt"], encoding="utf-8"))
    assert rec["status"] == "failed" and rec["error"].startswith("RuntimeError")


# ── a job that hangs ─────────────────────────────────────────────────────────

def test_a_job_that_hangs_past_its_timeout_is_cancelled_and_the_seat_comes_back(arb):
    cancelled = []

    def job():
        time.sleep(3)

    out = arb.heavy_job("image_job", job, timeout_s=0.2, job_id="image-t4",
                        cancel=lambda: cancelled.append(True))
    assert out["status"] == "timeout" and out["ok"] is False
    assert cancelled == [True], "the job's own cancel hook is called"
    assert arb.comfy.started is False, "ComfyUI is stopped under the hung job"
    assert set(arb.llama.procs) == {"gemma4:12b", "gemma4:e2b"}
    assert "gemma4:12b" in arb.llama.verified
    assert arb.lease is None and arb.state == ra.STATE_DEFAULT


# ── build hours ──────────────────────────────────────────────────────────────

@pytest.fixture
def build_hours_on(tmp_path, monkeypatch):
    flag = tmp_path / "BUILD_HOURS"
    end = (dt.datetime.now() + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    flag.write_text("start: 2026-01-01T00:00\nend:   %s\n" % end, encoding="utf-8")
    monkeypatch.setenv("FRIDAY_BUILD_HOURS_FLAG", str(flag))
    assert build_hours.is_active()
    return flag


def test_during_build_hours_the_previous_seat_is_the_parked_state(build_hours_on, tmp_path):
    """The daemon parked the brain; a job must not relaunch it afterwards."""
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=FakeOllama(), llama=FakeLlama(), comfy=FakeComfy(),
                   gguf_paths={"gemma4:12b": "/x/12b.gguf",
                               "gemma4:e2b": "/x/e2b.gguf"})
    a.compute_plan()
    a.boot(measure_baseline=False)
    assert a.llama.loads == [], "boot does not relaunch a parked seat"
    f = tmp_path / "out.png"
    f.write_bytes(b"x" * 10)
    out = a.heavy_job("image_job", lambda: {"files": [str(f)]}, timeout_s=5,
                      job_id="image-t5")
    assert out["ok"] is True
    assert out["previous"]["parked"] is True
    assert a.llama.loads == [], "nothing relaunches the brain during build hours"
    assert out["verify"]["mode"] == "parked" and out["verified"] is True
    assert a.lease is None and a.state == ra.STATE_DEFAULT
    assert "parked" in [t["action"] for t in a.transitions]


def test_outside_build_hours_release_restores_as_before(arb):
    arb.grant("image_job")
    assert set(arb.llama.procs) == {"gemma4:e2b"}
    arb.release()
    assert set(arb.llama.procs) == {"gemma4:12b", "gemma4:e2b"}


# ── the ComfyUI reserve ──────────────────────────────────────────────────────

def test_comfyui_is_told_the_display_reserve(monkeypatch, tmp_path):
    """ComfyUI's dynamic VRAM loading fills the card to within a few hundred
    MiB; the monitor then sees a display-reserve breach and the render is
    cancelled at its first sampling step. ComfyUI must keep the same reserve
    the monitor enforces."""
    b = ra.ComfyUIBackend(root=tmp_path)
    assert "--reserve-vram" not in b.launch_args()
    b.reserve_vram_mib = 2560
    args = b.launch_args()
    assert args[args.index("--reserve-vram") + 1] == "2.50"


def test_grant_hands_the_reserve_to_comfyui(arb):
    arb.grant("image_job")
    assert arb.comfy.reserve_vram_mib and arb.comfy.reserve_vram_mib >= 256
