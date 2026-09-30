"""The budgeted fast path: several questions, one pass, a cache and a budget.

`laya_runtime.ask` is what voice, the reflex path and the gate's shadow call.
Its contract, each clause a test:

  * several typed questions go to the model in ONE predict call;
  * an answer is remembered per checkpoint, question and argument SHAPE, so
    the same tool asked about another repository is answered from memory,
    but anything with free text in its arguments is keyed exactly;
  * a budget: an answer later than `budget_ms` is not waited for, and the
    caller gets status "missing" with the reason, never a silent default;
  * a missing model or a busy scorer is "missing" too, with its reason.

And the engine choice: "auto" takes the ONNX artifact only once it exists and
has passed its agreement check against fp32.

No model is loaded; the agent is faked at the `predict` boundary.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from agent_friday.services import laya_backend, laya_runtime


class _Agent:
    def __init__(self, seconds=0.0):
        self.seconds = seconds
        self.calls = []

    def predict(self, text, questions):
        self.calls.append((text, sorted(questions)))
        if self.seconds:
            time.sleep(self.seconds)
        return {"answers": {q: {"choice": "yes", "confidence": 0.9} for q in questions}}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(laya_backend, "_agent", None)
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(laya_backend._MAX_SCORING))
    monkeypatch.setattr(laya_runtime, "artifacts_dir", lambda: tmp_path / "laya-onnx")
    monkeypatch.setattr(laya_runtime, "checkpoint_revision", lambda agent=None: "rev1")
    laya_runtime.clear_cache()
    yield
    laya_runtime.clear_cache()


def test_several_questions_are_one_pass(monkeypatch):
    agent = _Agent()
    monkeypatch.setattr(laya_backend, "_agent", agent)
    r = laya_runtime.ask("turn the volume down", ["touches_private", "direct_command"],
                         budget_ms=1000)
    assert r["status"] == "ok"
    assert set(r["answers"]) == {"touches_private", "direct_command"}
    assert len(agent.calls) == 1


def test_the_same_tool_with_other_ids_is_answered_from_memory(monkeypatch):
    agent = _Agent()
    monkeypatch.setattr(laya_backend, "_agent", agent)
    laya_runtime.ask('mcp_github_get_issue {"repo": "a/b", "n": 1}', ["severity"], budget_ms=1000)
    r = laya_runtime.ask('mcp_github_get_issue {"repo": "c/d", "n": 7}', ["severity"], budget_ms=1000)
    assert r["cached"] == ["severity"]
    assert len(agent.calls) == 1


def test_free_text_arguments_are_keyed_exactly(monkeypatch):
    agent = _Agent()
    monkeypatch.setattr(laya_backend, "_agent", agent)
    laya_runtime.ask('send_message {"text": "see you at noon"}', ["severity"], budget_ms=1000)
    laya_runtime.ask('send_message {"text": "wire the money now"}', ["severity"], budget_ms=1000)
    assert len(agent.calls) == 2


def test_only_the_questions_not_remembered_are_asked(monkeypatch):
    agent = _Agent()
    monkeypatch.setattr(laya_backend, "_agent", agent)
    laya_runtime.ask("pause", ["direct_command"], budget_ms=1000)
    laya_runtime.ask("pause", ["direct_command", "touches_private"], budget_ms=1000)
    assert agent.calls[1][1] == ["touches_private"]


def test_over_budget_is_missing_with_its_reason(monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", _Agent(seconds=0.5))
    t = time.monotonic()
    r = laya_runtime.ask("play some jazz", ["direct_command"], budget_ms=50)
    assert time.monotonic() - t < 0.4
    assert r["status"] == "missing" and "over budget" in r["reason"]


def test_a_missing_model_is_missing_not_a_guess():
    r = laya_runtime.ask("play some jazz", ["direct_command"], budget_ms=50)
    assert r["status"] == "missing" and r["reason"] == "laya not loaded"
    assert r["answers"] == {}


def test_a_busy_scorer_is_missing(monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", _Agent())
    held = [laya_backend._reserve_scoring(pilot=False) for _ in range(laya_backend._MAX_SCORING)]
    try:
        r = laya_runtime.ask("pause", ["direct_command"], budget_ms=200)
    finally:
        for release in held:
            release()
    assert r["status"] == "missing" and r["reason"] == "laya busy"


def test_auto_takes_onnx_only_once_it_agreed_with_fp32(tmp_path):
    d = laya_runtime.artifacts_dir()
    assert laya_runtime.choose_engine("auto") == "torch-fp32"
    d.mkdir(parents=True)
    (d / laya_runtime.INT8_NAME).write_bytes(b"x")
    (d / laya_runtime.MANIFEST_NAME).write_text(json.dumps({"agreement_ok": False}))
    assert laya_runtime.choose_engine("auto") == "torch-fp32"
    laya_runtime.record_agreement(True, {"choices_differ": 0})
    assert laya_runtime.choose_engine("auto") == "onnx-int8"
    assert laya_runtime.choose_engine("torch-int8") == "torch-int8"


def test_an_unchecked_artifact_refuses_to_load(tmp_path):
    d = laya_runtime.artifacts_dir()
    d.mkdir(parents=True)
    (d / laya_runtime.INT8_NAME).write_bytes(b"x")
    with pytest.raises(RuntimeError):
        laya_runtime.apply_engine(object(), "onnx-int8", threads=1)


def test_a_broken_fast_engine_falls_back_to_fp32(monkeypatch):
    applied = []

    def _apply(agent, engine, threads=None):
        applied.append(engine)
        if engine != "torch-fp32":
            raise RuntimeError("fixture: engine cannot run")
        agent._friday_engine = engine
        return agent

    class _Bare:
        pass

    monkeypatch.setattr(laya_runtime, "choose_engine", lambda s: "onnx-int8")
    monkeypatch.setattr(laya_runtime, "apply_engine", _apply)
    agent = _Bare()
    laya_backend._use_engine(agent)
    assert applied == ["onnx-int8", "torch-fp32"]
    assert agent._friday_engine == "torch-fp32"
