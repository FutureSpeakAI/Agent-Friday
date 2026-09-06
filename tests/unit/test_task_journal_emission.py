"""Task journal, phase 2 (docs/design/active/task-visibility.md TV3, TV4,
TV7): required emission at loop checkpoints, decisions written where the
code already makes them, reasoning capture as a real setting, heartbeats.

THE LOAD-BEARING TEST is the coverage pair below: each agentic loop is
driven with a fake provider for N tool rounds and the journal must contain
EXACTLY N+1 checkpoints, N+1 model calls, N tool calls and (with capture on)
N+1 reasoning events. A code path that advances an iteration without
emitting fails this file — a trace that is 80% complete would be trusted
and wrong, which is worse than no trace.
"""
from __future__ import annotations

import json
import time
import types

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj


TID = "emission-task-0001"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(ag, "_execute_tool", lambda name, inp, **kw: f"ok:{name}")
    monkeypatch.setattr(ag, "_get_vault_control", lambda: None)
    monkeypatch.setattr(ag, "_seal_or_block", lambda payload, provider: payload)
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)
    # Cost metering is not under test; keep it inert and return a known cost.
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "meter", lambda *a, **k: 0.0123)
    monkeypatch.setattr(cm, "record", lambda *a, **k: 0.0123)
    yield


def _settings(monkeypatch, **kw):
    base = {"retention_days": 0, "capture_reasoning": True, "encrypt_at_rest": False}
    base.update(kw)
    monkeypatch.setattr(tj, "settings", lambda: dict(base))


def _kinds(tid=TID):
    return [e["kind"] for e in tj.read(tid)]


# ── fake Anthropic client: N tool rounds, then a final answer ────────────────

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = types.SimpleNamespace(input_tokens=100, output_tokens=20)


def _fake_anthropic(n_tool_rounds, thinking=False):
    calls = {"n": 0}

    class _Messages:
        def create(self, **kwargs):
            calls["n"] += 1
            i = calls["n"]
            blocks = []
            if thinking:
                blocks.append(_Block(type="thinking", thinking=f"private thought {i}"))
            blocks.append(_Block(type="text", text=f"I will now do step {i}"))
            if i <= n_tool_rounds:
                blocks.append(_Block(type="tool_use", id=f"tu{i}", name="search_web",
                                     input={"query": f"q{i}"}))
                return _Resp(blocks, "tool_use")
            return _Resp(blocks, "end_turn")

    return types.SimpleNamespace(messages=_Messages()), calls


def _run_anthropic(monkeypatch, n_tool_rounds, thinking=False):
    client, calls = _fake_anthropic(n_tool_rounds, thinking)
    monkeypatch.setattr(ag, "get_anthropic_client", lambda: client)
    tj.push_task(TID)
    try:
        text, trace = ag._call_claude_agent([{"role": "user", "content": "go"}], system="s",
                                            session_ctx={"task_id": TID, "is_background_task": True})
    finally:
        tj.pop_task()
    return text, trace, calls["n"]


# ── fake OpenAI-compatible transport: N tool rounds, then a final answer ────

def _fake_send(n_tool_rounds, reasoning_field=False):
    calls = {"n": 0}

    def send(convo, tools):
        calls["n"] += 1
        i = calls["n"]
        msg = {"role": "assistant", "content": f"Round {i} thinking aloud"}
        if reasoning_field:
            msg["reasoning_content"] = f"hidden chain {i}"
        if i <= n_tool_rounds:
            msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                                  "function": {"name": "search_web", "arguments": json.dumps({"query": f"q{i}"})}}]
        return {"choices": [{"message": msg, "finish_reason": "tool_calls" if i <= n_tool_rounds else "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10}}

    return send, calls


def _run_oai(n_tool_rounds, reasoning_field=False, provider="local", model="gemma4:12b"):
    send, calls = _fake_send(n_tool_rounds, reasoning_field)
    tools = [{"type": "function", "function": {"name": "search_web", "parameters": {"type": "object"}}}]
    tj.push_task(TID)
    try:
        text, trace = ag._oai_agentic_loop([{"role": "user", "content": "go"}], tools, send,
                                           provider=provider, model=model,
                                           session_ctx={"task_id": TID, "is_background_task": True})
    finally:
        tj.pop_task()
    return text, trace, calls["n"]


# ═══ Coverage: real iterations against real checkpoints ═════════════════════

@pytest.mark.parametrize("n", [0, 1, 3])
def test_anthropic_loop_emits_exactly_one_checkpoint_and_model_call_per_iteration(monkeypatch, n):
    _settings(monkeypatch)
    text, trace, iterations = _run_anthropic(monkeypatch, n)
    assert iterations == n + 1 and len(trace) == n
    kinds = _kinds()
    assert kinds.count("checkpoint") == iterations, kinds
    assert kinds.count("model_call") == iterations, kinds
    assert kinds.count("tool_call") == n, kinds
    assert kinds.count("reasoning") == iterations, kinds
    # Order within an iteration: checkpoint before the call, call before tools.
    first_cp = kinds.index("checkpoint"); first_mc = kinds.index("model_call")
    assert first_cp < first_mc
    if n:
        assert kinds.index("tool_call") > first_mc
    mc = [e for e in tj.read(TID) if e["kind"] == "model_call"]
    assert all(e["provider"] == "anthropic" and e["seat"] == "cloud" and e["tokens_in"] == 100
               and e["cost_usd"] == 0.0123 for e in mc)
    assert [e["iteration"] for e in mc] == list(range(1, iterations + 1))
    tc = [e for e in tj.read(TID) if e["kind"] == "tool_call"]
    assert all(e["name"] == "search_web" and e["ok"] is True and "q" in e["args"] for e in tc)


@pytest.mark.parametrize("n", [0, 1, 3])
def test_openai_loop_emits_exactly_one_checkpoint_and_model_call_per_round(n, monkeypatch):
    _settings(monkeypatch)
    text, trace, rounds = _run_oai(n)
    assert rounds == n + 1 and len(trace) == n
    kinds = _kinds()
    assert kinds.count("checkpoint") == rounds, kinds
    assert kinds.count("model_call") == rounds, kinds
    assert kinds.count("tool_call") == n, kinds
    assert kinds.count("reasoning") == rounds, kinds
    mc = [e for e in tj.read(TID) if e["kind"] == "model_call"]
    assert all(e["provider"] == "local" and e["seat"] == "local" and e["model"] == "gemma4:12b" for e in mc)
    assert [e["iteration"] for e in mc] == list(range(1, rounds + 1))


def test_openai_loop_empty_reply_retry_is_a_counted_round(monkeypatch):
    """The loop's own retry-on-empty is an iteration like any other: it must
    produce a checkpoint and a model_call, not a silent gap."""
    _settings(monkeypatch)
    calls = {"n": 0}

    def send(convo, tools):
        calls["n"] += 1
        content = "" if calls["n"] == 1 else "here is the answer"
        return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    tools = [{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}]
    tj.push_task(TID)
    try:
        ag._oai_agentic_loop([{"role": "user", "content": "go"}], tools, send, provider="local",
                             model="m", session_ctx={"task_id": TID})
    finally:
        tj.pop_task()
    assert calls["n"] == 2
    assert _kinds().count("checkpoint") == 2 and _kinds().count("model_call") == 2


def test_vault_denied_tool_call_is_still_a_tool_call_event(monkeypatch):
    """The zero-trust deny path executes no tool but IS a decision the reader
    must see; it goes through the same choke point."""
    _settings(monkeypatch)

    class _Deny:
        def check_action(self, *a, **k):
            return False, "TIER_3 vault content", 3
    monkeypatch.setattr(ag, "_get_vault_control", lambda: _Deny())
    monkeypatch.setattr(ag, "VaultAccessControl", object)
    _run_oai(1)
    tc = [e for e in tj.read(TID) if e["kind"] == "tool_call"]
    assert len(tc) == 1 and tc[0]["ok"] is False and "VAULT" in tc[0]["result_summary"]


def test_nothing_is_emitted_outside_a_task(monkeypatch, tmp_path):
    """An interactive chat turn has no task; the emitters are no-ops rather
    than inventing a journal for it."""
    _settings(monkeypatch)
    send, _ = _fake_send(1)
    tools = [{"type": "function", "function": {"name": "search_web", "parameters": {"type": "object"}}}]
    assert tj.current_task() is None
    ag._oai_agentic_loop([{"role": "user", "content": "go"}], tools, send, provider="local",
                         model="m", session_ctx={"authenticated": True})
    assert not any(p.is_dir() for p in (tmp_path / "tasks").glob("*")) if (tmp_path / "tasks").exists() else True


# ═══ Reasoning capture: on by default, and the OFF path is exercised ════════

def test_reasoning_capture_on_records_prose_and_provider_thinking(monkeypatch):
    _settings(monkeypatch, capture_reasoning=True)
    _run_anthropic(monkeypatch, 1, thinking=True)
    rs = [e for e in tj.read(TID) if e["kind"] == "reasoning"]
    assert len(rs) == 2
    assert rs[0]["text"] == "I will now do step 1" and rs[0]["thinking"] == "private thought 1"
    tj.delete(TID); tj.reset_for_tests()
    _run_oai(1, reasoning_field=True)
    rs = [e for e in tj.read(TID) if e["kind"] == "reasoning"]
    assert len(rs) == 2 and rs[1]["text"] == "Round 2 thinking aloud" and rs[1]["thinking"] == "hidden chain 2"


def test_reasoning_capture_off_writes_none_of_the_prose_anywhere(monkeypatch):
    """The setting must change behaviour on the real path, not merely be read
    back: with capture off, the model's words appear nowhere in the journal
    bytes — not as a reasoning event, not inside a checkpoint or a state."""
    _settings(monkeypatch, capture_reasoning=False)
    _run_anthropic(monkeypatch, 2, thinking=True)
    _run_oai(2, reasoning_field=True)
    kinds = _kinds()
    assert kinds.count("reasoning") == 0, kinds
    assert kinds.count("checkpoint") == 6 and kinds.count("model_call") == 6, "everything else still emits"
    raw = (tj.task_dir(TID) / "journal.jsonl").read_bytes()
    for needle in (b"I will now do step", b"private thought", b"thinking aloud", b"hidden chain"):
        assert needle not in raw, needle
    st = tj.read_state(TID) or {}
    assert "private thought" not in json.dumps(st)


def test_capture_reasoning_default_is_on():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["task_journal"]["capture_reasoning"] is True


# ═══ Decisions: written where the code already decides ══════════════════════

def test_gate_decision_is_written_at_the_seal_chokepoint(monkeypatch):
    _settings(monkeypatch)
    from agent_friday.services import model_router as mr
    import agent_friday.services.egress_gate as eg
    monkeypatch.setattr(eg, "gate_operational", lambda: True)
    from agent_friday.services import spend_guard as sg
    monkeypatch.setattr(sg, "check", lambda *a, **k: None)
    tj.push_task(TID)
    try:
        monkeypatch.setattr(eg, "seal_outbound", lambda payload, provider, **k: payload)
        mr._seal_or_block({"messages": [{"role": "user", "content": "public"}]}, "anthropic")
        monkeypatch.setattr(eg, "seal_outbound",
                            lambda payload, provider, **k: dict(payload, messages=[{"role": "user", "content": "[REDACTED]"}]))
        mr._seal_or_block({"messages": [{"role": "user", "content": "my ssn is …"}]}, "anthropic")
    finally:
        tj.pop_task()
    ds = [e for e in tj.read(TID) if e["kind"] == "decision" and e["point"] == "gate"]
    assert [d["chosen"] for d in ds] == ["allowed", "redacted"]
    assert "messages" in ds[1]["reason"]


def test_spend_cap_and_approval_decisions_are_written(monkeypatch, tmp_path):
    _settings(monkeypatch)
    from agent_friday.services import spend_guard as sg, cost_meter as cm, approvals
    monkeypatch.setattr(sg, "HALT_LOG", tmp_path / "halts.jsonl")
    monkeypatch.setattr(cm, "_load_settings", lambda: {"cost_budget": {"hard_stop_monthly": 1.0, "hard_stop_monthly_enabled": True}})
    monkeypatch.setattr(cm, "_rolling_spend", lambda: (0.0, 5.0))
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    tj.push_task(TID)
    try:
        with pytest.raises(sg.SpendCapReached):
            sg.check("anthropic", what="anthropic model call")
        approvals.gate_action(kind="connector_auth", subject_type="connector", subject_id="google",
                              title="Connect Google", action_description="open oauth", force_gate=True)
    finally:
        tj.pop_task()
    pts = {e["point"]: e for e in tj.read(TID) if e["kind"] == "decision"}
    assert pts["spend_cap"]["chosen"] == "refused" and "$5.00" in pts["spend_cap"]["reason"]
    assert pts["approval"]["chosen"] == "pending" and "connector_auth" in pts["approval"]["reason"]


def test_seat_select_and_ladder_fallback_decisions(monkeypatch):
    _settings(monkeypatch)
    from agent_friday.routing import model_router as rmr

    class _Router:
        def route(self, messages, task_context=None):
            return {"provider": "cloud", "model": "claude-sonnet-5", "reason": "cloud_only mode"}
    monkeypatch.setattr(rmr, "get_router", lambda cfg=None: _Router())
    monkeypatch.setattr(ag, "_load_settings", lambda: {"model_routing": {"mode": "smart"}})
    from agent_friday.services import model_router as smr
    monkeypatch.setattr(smr, "_mode_filtered_attempts", lambda attempts, cfg, vault_access=False: attempts)
    monkeypatch.setattr(smr, "_health_order", lambda attempts, name: attempts)
    # first leg (cloud) fails, second (openai) answers
    monkeypatch.setattr(ag, "_call_claude_agent", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("seat down")))
    monkeypatch.setattr(ag, "_call_openai", lambda *a, **k: ("answer from openai", []))
    monkeypatch.setattr(ag, "get_anthropic_client", lambda: object())
    tj.push_task(TID)
    try:
        text, _ = ag._generate_agent([{"role": "user", "content": "go"}], session_ctx={"task_id": TID})
    finally:
        tj.pop_task()
    assert text == "answer from openai"
    ds = [e for e in tj.read(TID) if e["kind"] == "decision"]
    seat = next(d for d in ds if d["point"] == "seat_select")
    assert seat["chosen"] == "cloud/claude-sonnet-5" and "cloud_only" in seat["reason"]
    fb = next(d for d in ds if d["point"] == "ladder_fallback")
    assert fb["chosen"] == "next: openai" and "seat down" in fb["reason"]


def test_chain_advance_and_retry_decisions(monkeypatch, tmp_path):
    _settings(monkeypatch)
    monkeypatch.setattr(ag, "WORKFLOWS_DIR", tmp_path / "wf")
    (tmp_path / "wf").mkdir()
    (tmp_path / "wf" / "demo.json").write_text(json.dumps({"name": "demo", "steps": [
        {"name": "one", "prompt": "p1", "retries": 1}, {"name": "two", "prompt": "p2"}]}), encoding="utf-8")
    spawned = []
    monkeypatch.setattr(ag, "_spawn_task", lambda name, prompt, **kw: spawned.append((name, kw)) or "next-id")
    with ag.TASKS_LOCK:
        ag.TASKS[TID] = {"task_id": TID, "name": "one", "chain": "demo", "chain_step": 0,
                         "chain_retry": 0, "status": "complete", "log": [], "result": "", "created": time.time()}
    try:
        ag._advance_task_chain(TID, "did step one")
        ag._retry_chain_step(TID, "provider 500")
        with ag.TASKS_LOCK:
            ag.TASKS[TID]["chain_retry"] = 1
        ag._retry_chain_step(TID, "provider 500 again")
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop(TID, None)
    ds = [(e["point"], e["chosen"]) for e in tj.read(TID) if e["kind"] == "decision"]
    assert ("chain_advance", "step 2/2: two") in ds
    assert ("retry", "retry 1/1") in ds and ("retry", "halted") in ds


# ═══ Heartbeat (TV7) ════════════════════════════════════════════════════════

def test_heartbeat_refreshes_last_seen_while_running_and_stops_when_terminal(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(tj, "HEARTBEAT_STATE_S", 0.05)
    monkeypatch.setattr(tj, "HEARTBEAT_JOURNAL_S", 0.1)
    tj.write_state(TID, {"task_id": TID, "name": "hb", "status": "running", "created": time.time()})
    hb = tj.Heartbeat(TID).start()
    time.sleep(0.5)
    seen1 = tj.last_seen(TID)
    assert seen1 and time.time() - seen1 < 0.3
    assert _kinds().count("heartbeat") >= 2
    st = tj.read_state(TID); st["status"] = "complete"; tj.write_state(TID, st)
    time.sleep(0.2)
    hb.stop()
    n_after = _kinds().count("heartbeat")
    time.sleep(0.3)
    assert _kinds().count("heartbeat") == n_after, "no heartbeats once the task is terminal"


def test_worker_pushes_its_task_and_pops_it(monkeypatch):
    """The emitters in the gate/spend/approval modules rely on the worker
    having made its task current; prove the push/pop pair around a run."""
    _settings(monkeypatch)
    seen = {}
    def fake_generate(messages, system=None, **kw):
        seen["current"] = tj.current_task()
        return "done", []
    monkeypatch.setattr(ag, "_generate_agent", fake_generate)
    monkeypatch.setattr(ag, "_get_friday_system_prompt", lambda *a, **k: "s")
    monkeypatch.setattr(ag, "_predict_route_provider", lambda **kw: "cloud")
    monkeypatch.setattr(ag, "_gated_vault_control", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_evaluate_output", lambda *a, **k: "GRADE: PASS\nREASON: fine")
    monkeypatch.setattr(ag, "_report_task_completion", lambda *a, **k: None)
    # NOTE: the worker's evaluator gate imports `_vault_local_only` from
    # agent_friday.core, where no such name exists (it lives in
    # services/model_router), so the gate fails closed and the evaluator is
    # skipped on every task. Pre-existing; not fixed in this phase because
    # re-enabling it turns a cloud call per task back on. The journal now
    # says so instead of claiming the task was vault-protected — this
    # assertion pins the truthful reason and will need updating when the
    # import is repaired.
    with ag.TASKS_LOCK:
        ag.TASKS[TID] = {"task_id": TID, "name": "t", "prompt": "p", "status": "queued",
                         "created": time.time(), "log": [], "result": ""}
    try:
        ag._task_worker(TID, "t", "p")
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop(TID, None)
    assert seen["current"] == TID
    assert tj.current_task() is None
    ev = next(e for e in tj.read(TID) if e["kind"] == "decision" and e["point"] == "evaluate")
    assert ev["chosen"] == "skipped"
    assert "could not determine the vault policy" in ev["reason"] and "ImportError" in ev["reason"], ev
