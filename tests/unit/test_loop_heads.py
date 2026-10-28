"""DM5: the loop no-progress head replays a finished turn in shadow.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md (DM5). It is
stop-only and never says a task is done; in shadow it changes nothing. On the
base branch there is no services/loop_heads.py: the fixture fails with that.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def lh(monkeypatch):
    try:
        mod = importlib.import_module("agent_friday.services.loop_heads")
    except ImportError as e:
        pytest.fail("services/loop_heads does not exist (%s)" % e)
    mod.clear_cache()
    monkeypatch.setattr(mod, "RUN_INLINE", True)
    monkeypatch.setattr(mod, "_off_record", lambda: False)
    monkeypatch.setattr(mod, "_is_write", lambda tool: str(tool) == "write_note")
    yield mod
    mod.clear_cache()


def _step(tool, result="", ok=True, error=None, **args):
    return {"tool": tool, "args": args, "ok": ok, "error": error, "result": result}


def test_new_information_every_step_is_progress(lh):
    steps = [_step("search", "Fact %d is here." % i, q=i) for i in range(10)]
    assert lh.first_stall(steps) is None


def test_four_steps_with_nothing_new_is_a_stall(lh):
    steps = [_step("search", "The bill is due Friday.", q=0)] + [_step("search", "The bill is due Friday.", q=i)
                                                               for i in range(1, 6)]
    assert lh.first_stall(steps) == 4


def test_the_same_error_three_times_is_a_stall_whatever_the_wording(lh):
    errs = ["Permission denied", "Access is denied (5)", "403 Forbidden", "Not allowed"]
    steps = [_step("read", "Opened it. It has 3 pages.", p=0)] + [_step("read", ok=False, error=e, p=i)
                                                                  for i, e in enumerate(errs, 1)]
    assert lh.first_stall(steps) == 3          # the third same-class error, inside a window of four


def test_a_write_in_the_window_keeps_a_loop_alive(lh):
    steps = [_step("search", "Same.", q=0)] * 3 + [_step("write_note", "Saved.", t=1)] + [_step("search", "Same.", q=0)]
    assert lh.first_stall(steps) is None


def test_a_trace_reads_an_error_result_as_a_failed_step(lh):
    s = lh.steps_from_trace([{"name": "read_file", "input": {"p": "x"}, "result": "Error: no such file"},
                             {"name": "read_file", "input": {"p": "y"}, "result": "hello"}])
    assert s[0]["ok"] is False and s[0]["error"].startswith("Error") and s[1]["ok"] is True


def _heads(monkeypatch, mode="shadow"):
    from agent_friday.services import decision_heads
    monkeypatch.setattr(decision_heads, "effective",
                        lambda hid: {"id": hid, "mode": mode, "thresholds": {"act": 0.5, "abstain": 0.2},
                                     "scorer": "rules", "budget_ms": 1000, "tag": "v1-test"})


def test_a_stalled_turn_gets_a_receipt_beside_loopguard_and_nothing_changes(lh, monkeypatch):
    from agent_friday.services import laya
    _heads(monkeypatch)
    got = []
    real = laya.ask
    monkeypatch.setattr(laya, "ask", lambda *a, **k: got.append(real(*a, **k)) or got[-1])
    trace = [{"name": "search", "input": {"q": i}, "result": "Nothing new about the bill."} for i in range(6)]
    before = [dict(t) for t in trace]
    assert lh.shadow_turn(trace, surface="test")
    assert trace == before
    a = got[0]["answers"]["loop.no_progress"]
    assert a["value"] == "stalled"
    rec = laya.receipt(got[0]["receipt_id"])
    assert "LoopGuard" in rec["questions"]["loop.no_progress"]["why"]
    assert "bill" not in str(rec)


def test_the_head_never_answers_done(lh, monkeypatch):
    from agent_friday.services import laya
    _heads(monkeypatch)
    for trace in ([{"name": "s", "input": {"q": i}, "result": "Fact %d." % i} for i in range(8)],
                  [{"name": "s", "input": {"q": 1}, "result": "x."}] * 8):
        r = laya.ask({"text": "", "surface": "background",
                      "opts": {"steps": lh.steps_from_trace(trace), "log": False}},
                     {"loop.no_progress": {"type": "choice", "preset": "loop.no_progress"}}, "test", 2000)
        assert r["answers"]["loop.no_progress"]["value"] in ("progressing", "stalled", "unsure")


def test_off_off_the_record_or_a_short_turn_does_nothing(lh, monkeypatch):
    trace = [{"name": "s", "input": {"q": i}, "result": "x."} for i in range(6)]
    _heads(monkeypatch, mode="off")
    assert not lh.shadow_turn(trace)
    lh.clear_cache()
    _heads(monkeypatch)
    assert not lh.shadow_turn(trace[:3])
    monkeypatch.setattr(lh, "_off_record", lambda: True)
    assert not lh.shadow_turn(trace)


def test_the_agent_loops_hand_their_trace_to_the_head_after_returning(monkeypatch):
    # A stand-in module: this fails on its assertion (not an import) where the hook is missing.
    import sys
    import types
    from agent_friday.services import agent
    seen = []
    stub = types.ModuleType("agent_friday.services.loop_heads")
    stub.shadow_turn = lambda trace, surface="agent", resumed=0: seen.append((len(trace), surface, resumed))
    monkeypatch.setitem(sys.modules, "agent_friday.services.loop_heads", stub)
    import agent_friday.services as _svc
    monkeypatch.setattr(_svc, "loop_heads", stub, raising=False)
    monkeypatch.setattr(agent, "_oai_agentic_loop_run", lambda *a, **k: ("done", [{"name": "s"}] * 5))
    assert agent._oai_agentic_loop([], [], None, provider="ollama", model="m", seat="local") == ("done",
                                                                                            [{"name": "s"}] * 5)
    monkeypatch.setattr(agent, "_call_claude_agent_run", lambda *a, **k: ("ok", [{"name": "t"}] * 4))
    assert agent._call_claude_agent([], resumed_tool_trace=[{"name": "t"}]) == ("ok", [{"name": "t"}] * 4)
    assert seen == [(5, "agent.local", 0), (4, "agent.claude", 1)]


def test_a_raising_head_never_reaches_the_agent(monkeypatch):
    import sys
    import types
    from agent_friday.services import agent
    stub = types.ModuleType("agent_friday.services.loop_heads")

    def boom(*a, **k):
        raise RuntimeError("head failed")
    stub.shadow_turn = boom
    monkeypatch.setitem(sys.modules, "agent_friday.services.loop_heads", stub)
    import agent_friday.services as _svc
    monkeypatch.setattr(_svc, "loop_heads", stub, raising=False)
    monkeypatch.setattr(agent, "_call_claude_agent_run", lambda *a, **k: ("ok", []))
    assert agent._call_claude_agent([]) == ("ok", [])


def test_only_a_known_tool_that_is_not_read_only_counts_as_a_write():
    lh = importlib.import_module("agent_friday.services.loop_heads")
    from agent_friday.services import agent, tool_catalogue
    assert lh._is_write("a_tool_no_table_knows") is False
    reads = [n for n, r in agent.TOOL_RINGS.items() if tool_catalogue.annotations(n).get("readOnlyHint")]
    writes = [n for n, r in agent.TOOL_RINGS.items() if not tool_catalogue.annotations(n).get("readOnlyHint")]
    assert reads and writes
    assert lh._is_write(reads[0]) is False and lh._is_write(writes[0]) is True


@pytest.mark.parametrize("result,ok,placeholder", [
    ("Error handling in Python uses try and except.", True, False),
    ("Failed: 0, passed 12", True, False),
    ("[GOVERNANCE DENY] that needs your approval", False, False),
    ("Error: no such file", False, False),
    ("INVALID ARGUMENTS: path is required", False, False),
    ({"error": "timed out"}, False, False),
    ("[image returned to model]", None, True),
])
def test_friday_failure_shapes_and_placeholders(result, ok, placeholder):
    lh = importlib.import_module("agent_friday.services.loop_heads")
    s = lh.steps_from_trace([{"name": "t", "input": {}, "result": result}])[0]
    assert bool(s.get("placeholder")) is placeholder
    if not placeholder:
        assert s["ok"] is ok


def test_resumed_rows_are_not_counted_again(lh, monkeypatch):
    _heads(monkeypatch)
    trace = [{"name": "s", "input": {"q": i}, "result": "x."} for i in range(6)]
    assert not lh.shadow_turn(trace, resumed=3)          # three new rows: under the window


def test_the_loop_head_ships_stop_only_in_shadow():
    from agent_friday.services import decision_heads as dh
    dh._reset_caches_for_tests()
    spec = dh.shipped("loop.no_progress")
    assert spec is not None, dh.problems().get("loop.no_progress")
    assert spec["direction"] == "stop_only" and spec["mode_default"] == "shadow"
    assert "done" not in spec["labels"] and "complete" not in spec["labels"]
    dh._reset_caches_for_tests()
