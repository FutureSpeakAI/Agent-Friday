"""A harness failure is not a verdict about a model.

On 2026-08-15 Stephen gated four models at once. They evicted each other from
VRAM, every case paid a cold reload against a flat 120s budget, and the store
recorded:

    gemma4:12b  structural 1/10   (9 of 10 cases: "timed out")
    gemma4:e2b  structural 4/10   — overwriting a standing GREEN

Re-run serially with an explicit num_ctx and a real budget, the same models
score 10/10. The models were never the problem. These tests pin the three
properties that make that impossible to repeat.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import model_seat_gate as gate


# ── telling the two apart ────────────────────────────────────────────────────

@pytest.mark.parametrize("err", [
    "timed out", "The read operation timed out", "connection refused",
    "<urlopen error [WinError 10061] ...>", "remote end closed connection",
])
def test_transport_failures_are_harness_errors(err):
    assert gate._is_harness_error(err) is True


@pytest.mark.parametrize("err", [
    "model returned prose instead of a tool call", "KeyError: 'tool_calls'",
])
def test_model_misbehaviour_is_not_a_harness_error(err):
    assert gate._is_harness_error(err) is False


# ── an inconclusive run never becomes a red ──────────────────────────────────

@pytest.fixture
def gate_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "GATE_DIR", tmp_path)
    return tmp_path


def _run(monkeypatch, behaviour):
    """behaviour(i) -> raises, or returns an OpenAI-shaped response."""
    calls = {"n": 0}

    def chat_fn(messages, model, tools=None, temperature=0.2, max_tokens=300):
        calls["n"] += 1
        return behaviour(calls["n"])

    monkeypatch.setattr(gate, "_gate_chat_fn",
                        lambda m, u, **kw: (chat_fn, "fake"))
    return gate.run_conformance_gate("fake:1b")


def _tool_reply(name):
    return {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": "{}"}}]}}]}


def test_a_timing_out_run_is_inconclusive_not_red(monkeypatch, gate_dir):
    def behaviour(i):
        raise TimeoutError("timed out")
    r = _run(monkeypatch, behaviour)
    assert r["inconclusive"] is True
    assert r["passed"] is None, "None means 'not measured', not 'failed'"
    assert r["harness_errors"] == 10


def test_an_inconclusive_run_does_not_overwrite_a_standing_verdict(
        monkeypatch, gate_dir):
    """This is the exact loss: e2b's green replaced by a red that measured
    nothing."""
    good = {"model": "fake:1b", "provider": "local", "passed": True,
            "score": "10/10"}
    (gate_dir / "local__fake_1b.json").write_text(json.dumps(good))

    def behaviour(i):
        raise TimeoutError("timed out")
    _run(monkeypatch, behaviour)

    still = gate.get_cached_status("fake:1b", "local")
    assert still["passed"] is True, "the standing green must survive"
    assert (gate_dir / "local__fake_1b.inconclusive.json").exists()


def test_an_inconclusive_run_reads_as_ungated_not_red(monkeypatch, gate_dir):
    def behaviour(i):
        raise TimeoutError("timed out")
    _run(monkeypatch, behaviour)
    assert gate.is_seat_green("fake:1b", "local") is False   # fail-closed
    st = gate.axis_status("fake:1b", "local")
    assert st["structural"] == "ungated", \
        "a red is a claim about the model; we did not measure one"


def test_a_genuine_failure_is_still_recorded_as_red(monkeypatch, gate_dir):
    """The gate must keep working — narrating instead of calling is a real
    failure and must not be excused as a harness problem."""
    def behaviour(i):
        return {"choices": [{"message": {
            "role": "assistant",
            "content": "I will search the web for today's top AI news."}}]}
    r = _run(monkeypatch, behaviour)
    assert r["inconclusive"] is False
    assert r["passed"] is False
    assert gate.axis_status("fake:1b", "local")["structural"] == "red"


def test_a_clean_pass_is_green(monkeypatch, gate_dir):
    names = [c["expect_tool"] for c in gate.CONFORMANCE_PROMPTS]

    def behaviour(i):
        return _tool_reply(names[i - 1])
    r = _run(monkeypatch, behaviour)
    assert r["passed"] is True and r["inconclusive"] is False


# ── progress streaming ───────────────────────────────────────────────────────

def test_progress_is_emitted_per_case(monkeypatch, gate_dir):
    """Zero log lines is why the orb showed '— waiting for activity —' for
    minutes while the gate was in fact working."""
    lines = []
    names = [c["expect_tool"] for c in gate.CONFORMANCE_PROMPTS]
    calls = {"n": 0}

    def chat_fn(messages, model, tools=None, temperature=0.2, max_tokens=300):
        calls["n"] += 1
        return _tool_reply(names[calls["n"] - 1])

    monkeypatch.setattr(gate, "_gate_chat_fn",
                        lambda m, u, **kw: (chat_fn, "fake"))
    gate.run_conformance_gate("fake:1b", on_progress=lines.append)
    assert len(lines) >= len(gate.CONFORMANCE_PROMPTS)
    assert any("[1/10]" in l for l in lines)
    assert any("GREEN" in l for l in lines)


def test_a_broken_progress_callback_cannot_break_the_gate(monkeypatch,
                                                          gate_dir):
    names = [c["expect_tool"] for c in gate.CONFORMANCE_PROMPTS]
    calls = {"n": 0}

    def chat_fn(messages, model, tools=None, temperature=0.2, max_tokens=300):
        calls["n"] += 1
        return _tool_reply(names[calls["n"] - 1])

    monkeypatch.setattr(gate, "_gate_chat_fn",
                        lambda m, u, **kw: (chat_fn, "fake"))

    def boom(_):
        raise RuntimeError("orb gone")
    assert gate.run_conformance_gate("fake:1b", on_progress=boom)["passed"]


# ── explicit context, real budget ────────────────────────────────────────────

def test_the_gate_pins_an_explicit_context():
    """Ollama's default for gemma4 is 262144, which spills 79% onto the CPU."""
    # 32768, not 8192: the 52-tool registry is ~8534 tokens, so a context
    # below the prompt truncates the tool definitions.
    assert gate.GATE_NUM_CTX == 32768
    assert gate.GATE_NUM_CTX >= gate.min_tool_context()
    assert gate.GATE_TIMEOUT_S >= 600


def test_num_ctx_forces_the_native_endpoint(monkeypatch):
    """`options` is Ollama-NATIVE; /v1/chat/completions accepts the request and
    silently discards it.

    VERIFIED on the live daemon 2026-08-15:
        /v1/chat/completions  options.num_ctx=8192 -> ollama ps says 131072
        /api/chat             options.num_ctx=8192 -> ollama ps says   8192

    So a caller asking for a context must reach the native endpoint, or the
    'explicit context' is a claim the wire does not support. This is not
    hypothetical: the gate was still running the 26b at 262144 with 79% of it
    on the CPU *after* the num_ctx fix, because it went to /v1 first.
    """
    from agent_friday.routing import ollama_manager as om
    mgr = om.OllamaManager.__new__(om.OllamaManager)
    mgr.base_url = "http://localhost:11434"
    seen = {}

    def _post(path, body, timeout=30):
        seen["path"] = path
        seen["options"] = body.get("options")
        seen["tools"] = body.get("tools")
        return {"message": {"content": "ok"}}

    mgr._post = _post

    def _boom(*a, **k):
        raise AssertionError("must not use /v1 when num_ctx is requested")
    monkeypatch.setattr(om.urllib.request, "urlopen", _boom)

    out = mgr.chat_completion([{"role": "user", "content": "hi"}], "m:1b",
                              tools=[{"type": "function"}], num_ctx=8192)
    assert seen["path"] == "/api/chat"
    assert seen["options"]["num_ctx"] == 8192
    assert seen["tools"], "the native path must still carry tools"
    assert out["choices"][0]["message"]["content"] == "ok"


# ── the fallback seat must still exist ───────────────────────────────────────

def test_last_known_green_skips_an_uninstalled_model(monkeypatch, gate_dir):
    """A green record is evidence a model once behaved, not that it is still
    on the machine. qwen3.6-35b-a3b-iq4nl was decommissioned and kept being
    handed out as the fallback seat."""
    for name, model in (("local__gone_35b.json", "gone:35b"),
                        ("local__here_2b.json", "here:2b")):
        (gate_dir / name).write_text(json.dumps(
            {"model": model, "provider": "local", "passed": True,
             "timestamp": 100 if model == "gone:35b" else 50}))
    monkeypatch.setattr(gate, "_installed_local_models",
                        lambda: {"here:2b"})
    assert gate.get_last_known_green("local") == "here:2b"


def test_unverifiable_inventory_does_not_refuse_a_fallback(monkeypatch,
                                                           gate_dir):
    """A daemon outage must not turn into 'no local seat exists'."""
    (gate_dir / "local__x_2b.json").write_text(json.dumps(
        {"model": "x:2b", "provider": "local", "passed": True,
         "timestamp": 10}))
    monkeypatch.setattr(gate, "_installed_local_models", lambda: None)
    assert gate.get_last_known_green("local") == "x:2b"
