"""A lease gives back every seat it took, whatever the seat's role.

Offline. The arbiter's fakes come from test_residency_arbiter.
"""
from __future__ import annotations

import pytest

from agent_friday.services import context_budget
from agent_friday.services import residency_arbiter as ra
from tests import residency_fixtures as fx
from tests.unit.test_residency_arbiter import (
    FakeComfy, FakeLlama, FakeOllama, _AdoptingLlama, _mm_arbiter,
    _memory_manager_only_plan)

EMB = "qwen3-embedding:0.6b"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(context_budget, "injected_tokens",
                        lambda: (context_budget.MEASURED_INJECTED_TOKENS,
                                 "reference"))
    context_budget.reset_cache()
    fx.offline_machine(monkeypatch)
    # The HR5 system-volume floor stays in force; the verdict is fixed so the
    # test machine's own free space is not what is being tested.
    from agent_friday.services import machine_monitor as mm
    monkeypatch.setattr(mm, "last_sample", lambda *a, **k: {})
    monkeypatch.setattr(mm, "disk_system_verdict",
                        lambda *a, **k: {"status": "ok"})
    yield
    context_budget.reset_cache()


def _with_gpu_embedder(monkeypatch):
    real = ra.rp.plan

    def plan(profile, entries, overrides=None, **kw):
        p = real(profile, entries, overrides, **kw)
        p["seats"]["embedder"] = {
            "model_id": EMB, "device": "gpu:0", "status": "pinned",
            "num_ctx": 2048}
        return p

    monkeypatch.setattr(ra.rp, "plan", plan)


def _emb_arbiter(llama=None):
    return ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                      ollama=FakeOllama(), llama=llama or FakeLlama(),
                      comfy=FakeComfy(),
                      gguf_paths={"gemma4:12b": "/x/12b.gguf",
                                  "gemma4:e2b": "/x/e2b.gguf",
                                  EMB: "/x/emb.gguf"})


@pytest.mark.parametrize("kind", ["image_job", "heavy_turn"])
def test_a_gpu_embedder_comes_back_after_a_lease(monkeypatch, kind):
    _with_gpu_embedder(monkeypatch)
    a = _emb_arbiter()
    a.compute_plan()
    a.boot(measure_baseline=False)
    assert EMB in a.llama.procs
    out = a.grant(kind)
    assert out["ok"], out
    assert "embedder" in out["lease"]["displaced"]
    assert EMB not in a.llama.procs
    assert a.release()["ok"]
    assert EMB in a.llama.procs, \
        "the embedder was displaced by the lease and never reloaded"


def test_a_memory_manager_only_seat_comes_back_after_an_image_lease(
        monkeypatch):
    _memory_manager_only_plan(monkeypatch)
    a = _mm_arbiter()
    a.compute_plan()
    a.boot(measure_baseline=False)
    assert set(a.llama.procs) == {"gemma4:e4b"}
    out = a.grant("image_job")
    assert out["ok"], out
    assert "memory_manager" in out["lease"]["displaced"]
    assert "gemma4:e4b" not in a.llama.procs
    assert a.release()["ok"]
    assert set(a.llama.procs) == {"gemma4:e4b"}


def test_a_seat_that_will_not_reload_after_a_lease_is_reported(monkeypatch):
    _memory_manager_only_plan(monkeypatch)
    llama = FakeLlama()
    a = _mm_arbiter(llama)
    a.compute_plan()
    a.boot(measure_baseline=False)
    a.grant("image_job")
    llama.fail = True
    a.release()
    problems = a.status()["seat_problems"]
    assert problems["gemma4:e4b"]["role"] == "memory_manager"


def test_an_embedder_that_will_not_reload_is_reported(monkeypatch):
    _with_gpu_embedder(monkeypatch)
    llama = FakeLlama()
    a = _emb_arbiter(llama)
    a.compute_plan()
    a.boot(measure_baseline=False)
    a.grant("image_job")
    llama.fail = True
    a.release()
    assert a.status()["seat_problems"][EMB]["role"] == "embedder"


def test_an_adopted_seat_is_owned_and_reachable(monkeypatch):
    _memory_manager_only_plan(monkeypatch)
    llama = _AdoptingLlama("gemma4:e4b")
    a = _mm_arbiter(llama)
    a.compute_plan()
    a.boot(measure_baseline=False)
    monkeypatch.setattr(ra, "ARBITER", a)
    assert ra.owned_endpoint("gemma4:e4b") == "http://127.0.0.1:8090/v1"
