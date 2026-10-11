"""The Arbiter's lease is queued, not refused, and does not stand the brain
down under work that outranks it. Real Arbiter state machine over fake
backends (the same fakes as test_residency_arbiter): no GPU, no model."""
from __future__ import annotations

import threading
import time

import pytest
from turn_queue_helpers import fast, wait_until

from agent_friday.services import context_budget
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import turn_queue as tq
from tests import residency_fixtures as fx


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    fast(monkeypatch)
    monkeypatch.setattr(context_budget, "injected_tokens",
                        lambda: (context_budget.MEASURED_INJECTED_TOKENS, "reference"))
    context_budget.reset_cache()
    fx.offline_machine(monkeypatch)
    if hasattr(tq, "HELD"):
        tq.HELD.set(())
        tq.CURRENT.set(None)
    yield
    context_budget.reset_cache()
    tq.reset_for_tests()


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
    name = "llama-server"

    def __init__(self):
        self.procs = {}

    def resident(self):
        return {m: 0 for m in self.procs}

    def load(self, model_id, num_ctx, *, gguf_path, port, n_cpu_moe=None, timeout=300):
        self.procs[model_id] = (object(), port)
        return 1.0

    def evict(self, model_id):
        self.procs.pop(model_id, None)

    def evict_all(self):
        self.procs.clear()

    def adopt_or_reap(self, wanted):
        return {"adopted": [], "reaped": []}


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


@pytest.fixture
def arb():
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1), ollama=FakeOllama(),
                   llama=FakeLlama(), comfy=FakeComfy(),
                   gguf_paths={"gemma4:12b": "/x/12b.gguf", "gemma4:e2b": "/x/e2b.gguf"})
    a.compute_plan()
    a.boot(measure_baseline=False)
    return a


def _ask_on_a_thread(arb, kind, out):
    th = threading.Thread(target=lambda: out.append(arb.grant(kind)), daemon=True)
    th.start()
    return th


def test_a_second_lease_waits_for_the_first_and_is_then_granted(arb):
    assert arb.grant("heavy_turn")["ok"]
    out = []
    th = _ask_on_a_thread(arb, "image_job", out)
    time.sleep(0.4)
    assert out == [], "the second request waits its turn instead of coming back refused"
    assert arb.lease["kind"] == "heavy_turn"
    arb.release()
    th.join(10)
    assert out and out[0]["ok"] is True
    assert arb.lease["kind"] == "image_job"
    arb.release()


def test_a_lease_does_not_stand_the_brain_down_under_a_running_chat(arb):
    assert "gemma4:12b" in arb.llama.procs
    chat = tq.acquire("gemma4:12b", "chat", "chat-1")
    out = []
    th = _ask_on_a_thread(arb, "image_job", out)
    time.sleep(0.5)
    assert arb.lease is None and "gemma4:12b" in arb.llama.procs, \
        "the brain was evicted under a chat turn that was still answering"
    assert out == []
    chat.release()
    assert wait_until(lambda: bool(out), timeout=10)
    assert out[0]["ok"] and "gemma4:12b" not in arb.llama.procs
    th.join(5)
    arb.release()
