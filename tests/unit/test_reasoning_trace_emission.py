"""Every agentic loop writes its reasoning into the active reasoning trace.

These drive the real loops with fake providers, the way an interactive chat
turn runs them: NO background task id. Before reasoning traces, a chat turn's
reasoning went nowhere -- the task journal only records under a task -- so
the tray had nothing to show while Bonsai2 was visibly reasoning.
"""
from __future__ import annotations

import json
import threading
import types

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import model_router as mr
from agent_friday.services import reasoning_trace as rt


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    import agent_friday.services.file_grants as fg
    fg._SIGNING_KEY_CACHE.clear()
    rt._reset_for_tests()
    monkeypatch.setattr(rt, "BASE_DIR_OVERRIDE", tmp_path / "traces")
    monkeypatch.setattr(rt, "settings", lambda: {"capture": True, "retention_days": 0})
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(ag, "_execute_tool", lambda name, inp, **kw: f"ok:{name}")
    monkeypatch.setattr(ag, "_get_vault_control", lambda: None)
    monkeypatch.setattr(ag, "_seal_or_block", lambda payload, provider: payload)
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "meter", lambda *a, **k: 0.0)
    monkeypatch.setattr(cm, "record", lambda *a, **k: 0.0)
    yield
    rt._reset_for_tests()


def _types(tid):
    return [e["type"] for e in rt.live_trace(tid)["events"]]


# ── OpenAI-format loop (Ollama / llama-server / OpenRouter) ─────────────────

def _send(n_tool_rounds, reasoning=True, local=True):
    calls = {"n": 0}

    def send(convo, tools):
        calls["n"] += 1
        i = calls["n"]
        msg = {"role": "assistant", "content": f"Checking step {i}" if i <= n_tool_rounds else "All done."}
        if reasoning:
            msg["reasoning_content"] = f"chain of thought {i}"
        if i <= n_tool_rounds:
            msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                                  "function": {"name": "search_web",
                                               "arguments": json.dumps({"query": f"q{i}"})}}]
        return {"choices": [{"message": msg,
                             "finish_reason": "tool_calls" if i <= n_tool_rounds else "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10},
                "_reasoning_local": local}
    return send


def test_local_loop_records_reasoning_and_tools_in_order_for_a_chat_turn():
    tid = rt.start("chat", "what's new", parent_id=None)
    with rt.activate(tid):
        text, _ = ag._oai_agentic_loop([{"role": "user", "content": "go"}], [{"type": "function"}],
                                       _send(2), provider="local", model="bonsai2:27b",
                                       session_ctx={})
    assert text == "All done."
    evs = rt.live_trace(tid)["events"]
    seq = [e["type"] for e in evs if e["type"] in ("reasoning", "tool_call", "tool_result")]
    assert seq == ["reasoning", "tool_call", "tool_result"] * 2 + ["reasoning"]
    reasons = [e for e in evs if e["type"] == "reasoning"]
    assert [r["text"] for r in reasons] == ["chain of thought 1", "chain of thought 2", "chain of thought 3"]
    assert all(r["source"] == rt.SOURCE_FULL for r in reasons)
    calls = [e for e in evs if e["type"] == "tool_call"]
    assert json.loads(calls[0]["args"]) == {"query": "q1"}
    results = [e for e in evs if e["type"] == "tool_result"]
    assert results[0]["summary"] == "ok:search_web" and results[0]["call_id"] == calls[0]["call_id"]
    assert any(e["type"] == "note" and e["text"] == "Checking step 1" for e in evs)
    assert rt.live_trace(tid)["tokens"]["in"] == 150


def test_cloud_round_without_reasoning_is_labelled_not_exposed():
    tid = rt.start("chat", "x", parent_id=None)
    with rt.activate(tid):
        ag._oai_agentic_loop([{"role": "user", "content": "go"}], None,
                             _send(0, reasoning=False, local=False),
                             provider="openai", model="openai/gpt-5", session_ctx={})
    marks = [e for e in rt.live_trace(tid)["events"] if e["type"] == "source_marker"]
    assert marks and marks[0]["label"] == "reasoning not exposed by provider"
    assert not any(e["type"] == "reasoning" for e in rt.live_trace(tid)["events"])


def test_cloud_reasoning_text_is_labelled_as_returned_by_provider():
    tid = rt.start("chat", "x", parent_id=None)
    with rt.activate(tid):
        ag._oai_agentic_loop([{"role": "user", "content": "go"}], None,
                             _send(0, reasoning=True, local=False),
                             provider="openai", model="deepseek/r1", session_ctx={})
    r = [e for e in rt.live_trace(tid)["events"] if e["type"] == "reasoning"][0]
    assert r["source"] == rt.SOURCE_PROVIDER


def test_streamed_reasoning_reaches_the_trace_live_and_is_not_duplicated():
    frames = [
        {"choices": [{"delta": {"reasoning_content": "Let me "}}]},
        {"choices": [{"delta": {"reasoning_content": "think."}}]},
        {"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]},
    ]
    lines = ["data: " + json.dumps(f) for f in frames] + ["data: [DONE]"]
    seen_live = []

    class _R:
        encoding = "utf-8"

        def iter_lines(self, decode_unicode=True):
            for i, ln in enumerate(lines):
                if i == 2:   # before the answer arrives, the reasoning is already in the trace
                    seen_live.append("".join(e.get("text", "") for e in rt.feed(0)["events"]
                                             if e["type"] == "reasoning_delta"))
                yield ln

    tid = rt.start("chat", "x", parent_id=None)
    with rt.activate(tid):
        resp = mr._consume_sse_completion(_R(), reasoning_source=rt.SOURCE_FULL)
        resp["_reasoning_local"] = True
        msg = resp["choices"][0]["message"]
        rt.after_oai_round(resp, msg, model="bonsai2:27b", local=True)
    assert seen_live == ["Let me think."]
    assert msg["content"] == "Hi" and msg["reasoning_content"] == "Let me think."
    reasons = [e for e in rt.live_trace(tid)["events"] if e["type"] == "reasoning"]
    assert len(reasons) == 1 and reasons[0]["text"] == "Let me think."


def test_ollama_native_thinking_field_is_kept(monkeypatch):
    from agent_friday.routing import ollama_manager as om
    mgr = om.OllamaManager.__new__(om.OllamaManager)
    mgr.base_url = "http://127.0.0.1:11434"
    monkeypatch.setattr(mgr, "_post", lambda path, body, timeout=30: {
        "message": {"content": "answer", "thinking": "native thinking"},
        "prompt_eval_count": 3, "eval_count": 4}, raising=False)
    import agent_friday.services.gpu_headroom as gh
    monkeypatch.setattr(gh, "display_at_risk", lambda: {"at_risk": False})
    out = mgr.chat_completion([{"role": "user", "content": "x"}], "qwen3:4b")
    msg = out["choices"][0]["message"]
    assert msg["content"] == "answer" and msg["reasoning_content"] == "native thinking"


# ── Anthropic loop ──────────────────────────────────────────────────────────

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fake_client(blocks_for_round, seen_kwargs):
    calls = {"n": 0}

    class _Messages:
        def create(self, **kwargs):
            seen_kwargs.append(kwargs)
            calls["n"] += 1
            blocks, stop = blocks_for_round(calls["n"])
            return types.SimpleNamespace(content=blocks, stop_reason=stop,
                                         usage=types.SimpleNamespace(input_tokens=10, output_tokens=5))
    return types.SimpleNamespace(messages=_Messages())


def _claude_turn(monkeypatch, model, blocks_for_round):
    seen = []
    monkeypatch.setattr(ag, "get_anthropic_client", lambda: _fake_client(blocks_for_round, seen))
    tid = rt.start("chat", "x", parent_id=None)
    with rt.activate(tid):
        ag._call_claude_agent([{"role": "user", "content": "go"}], system="s", model=model,
                              session_ctx={})
    return tid, seen


def test_claude_5_is_asked_for_its_reasoning_summary_and_it_is_recorded(monkeypatch):
    def rounds(i):
        if i == 1:
            return ([_Block(type="thinking", thinking="summary of step one", signature="sig"),
                     _Block(type="text", text="Looking it up."),
                     _Block(type="tool_use", id="tu1", name="search_web", input={"query": "q"})],
                    "tool_use")
        return ([_Block(type="thinking", thinking="", signature="sig"),
                 _Block(type="text", text="Done.")], "end_turn")
    tid, seen = _claude_turn(monkeypatch, "claude-opus-5-5", rounds)
    assert seen[0]["thinking"] == {"type": "adaptive", "display": "summarized"}
    evs = rt.live_trace(tid)["events"]
    kinds = [e["type"] for e in evs if e["type"] in ("reasoning", "tool_call", "tool_result", "source_marker")]
    assert kinds == ["reasoning", "tool_call", "tool_result", "source_marker"]
    assert evs[0]["source"] == rt.SOURCE_SUMMARY and evs[0]["text"] == "summary of step one"
    marker = [e for e in evs if e["type"] == "source_marker"][0]
    assert marker["label"] == "reasoning not exposed by provider"


def test_redacted_thinking_is_labelled_and_its_payload_not_stored(monkeypatch):
    def rounds(i):
        return ([_Block(type="redacted_thinking", data="ENCRYPTED-BLOB"),
                 _Block(type="text", text="ok")], "end_turn")
    tid, _ = _claude_turn(monkeypatch, "claude-sonnet-5", rounds)
    evs = rt.live_trace(tid)["events"]
    assert [e["source"] for e in evs if e["type"] == "source_marker"] == [rt.SOURCE_REDACTED]
    assert "ENCRYPTED-BLOB" not in json.dumps(evs)


def test_older_claude_is_not_switched_into_thinking(monkeypatch):
    def rounds(i):
        return ([_Block(type="text", text="ok")], "end_turn")
    tid, seen = _claude_turn(monkeypatch, "claude-opus-4-8", rounds)
    assert "thinking" not in seen[0]
    marker = [e for e in rt.live_trace(tid)["events"] if e["type"] == "source_marker"][0]
    assert marker["source"] == rt.SOURCE_NONE


# ── subagents nest under the turn that spawned them ─────────────────────────

def test_spawned_subagent_trace_nests_under_the_chat_turn(monkeypatch):
    monkeypatch.setattr(ag, "_task_worker_untraced",
                        lambda task_id, *a, **k: rt.reasoning("subagent thinking"))
    turn = rt.start("chat", "plan my week", parent_id=None)
    with rt.activate(turn):
        task_id = ag._spawn_task("Research flights", "find flights", description="research")
    for t in list(threading.enumerate()):
        if t.name.startswith("task-" + task_id[:8]):
            t.join(10)
    rec = ag.TASKS[task_id]
    assert rec["parent_trace_id"] == turn
    child = rt.live_trace(rec["trace_id"])
    assert child["parent_id"] == turn and child["kind"] == "subagent" and child["task_id"] == task_id
    assert [e["text"] for e in child["events"] if e["type"] == "reasoning"] == ["subagent thinking"]
    tree = rt.get_tree(rec["trace_id"])["tree"]
    assert tree["trace_id"] == turn and tree["children"][0]["trace_id"] == rec["trace_id"]


def test_generate_text_opens_a_background_trace_when_none_is_active(monkeypatch):
    seen = {}

    def fake(*a, **k):
        seen["trace"] = rt.current()
        rt.reasoning("front page thinking")
        return "text"
    monkeypatch.setattr(mr, "_generate_text_untraced", fake)
    assert mr._generate_text([{"role": "user", "content": "x"}], orb_label="📰 Front Page") == "text"
    tr = rt.live_trace(seen["trace"])
    assert tr["kind"] == "background" and tr["label"] == "📰 Front Page" and tr["status"] == "complete"
    assert rt.verify()["records"] == 1
