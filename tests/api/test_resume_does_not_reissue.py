"""A crash after an outward tool finished, but before its result was saved, must not resume into
the same call being made again (NS-33.6-2 c: zero duplicate outward actions from retry).

The tool's result reaches the disk only with the round's checkpoint. The marker that said "a tool
is in flight" used to be dropped the moment the tool returned, so a crash in that gap left a
clean-looking checkpoint from BEFORE the call: an automatic resume asked the model again and it
made the same call a second time. Same harness as tests/api/test_task_resume.py; tools faked.
"""

import types

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import task_journal as tj
from agent_friday.services import task_resume as tr


class _Crash(RuntimeError):
    """Stands in for the process going away."""


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(ag, "_get_vault_control", lambda: None)
    monkeypatch.setattr(ag, "_seal_or_block", lambda payload, provider: payload)
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "meter", lambda *a, **k: 0.001)
    yield
    tj.reset_for_tests()


@pytest.fixture
def task():
    tid = "resume-reissue-0001"
    tj.index_put(tid, "midstream task", "running", 1_000_000.0)
    tj.write_state(tid, {"task_id": tid, "name": "midstream task",
                         "status": "running", "created": 1_000_000.0})
    tj.push_task(tid)
    try:
        yield tid
    finally:
        tj.pop_task()
        tj.delete(tid)
        tj.reset_for_tests()


def _client(monkeypatch, script):
    """`script`: per model call, a list of tool names to ask for in one turn, or None to finish."""
    calls = {"n": 0}

    class _Msgs:
        def create(self, **kw):
            i = calls["n"]
            calls["n"] += 1
            step = script[i] if i < len(script) else None
            if step == "crash":
                raise _Crash("the desktop server went away")
            usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
            txt = types.SimpleNamespace(type="text", text="step %d" % (i + 1))
            if step is None:
                return types.SimpleNamespace(content=[txt], stop_reason="end_turn", usage=usage)
            blocks = [types.SimpleNamespace(type="tool_use", id="t%d_%d" % (i, j), name=name,
                                            input={"q": i})
                      for j, name in enumerate(step)]
            return types.SimpleNamespace(content=[txt] + blocks, stop_reason="tool_use", usage=usage)

    monkeypatch.setattr(ag, "get_anthropic_client", lambda: types.SimpleNamespace(messages=_Msgs()))


def test_a_crash_after_an_outward_tool_finished_does_not_resume_into_running_it_again(monkeypatch, task):
    ran = []
    _client(monkeypatch, [["read_file"], ["write_file"]])
    monkeypatch.setattr(ag, "_execute_tool", lambda name, args, **kw: ran.append(name) or "result of " + name)
    real_ledger = ag._ledger_tool_call

    def ledger(name, *a, **kw):
        if name == "write_file":
            raise _Crash("died after write_file returned, before its result was saved")
        return real_ledger(name, *a, **kw)
    monkeypatch.setattr(ag, "_ledger_tool_call", ledger)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}], session_ctx={"task_id": task})
    assert ran == ["read_file", "write_file"]
    assert "result of write_file" not in str(tr.read(task)["convo"]), "the result was saved after all"

    v = tr.resumability(task)
    assert v["needs_confirmation"] is True, "a resume would make the same outward call again"
    assert "write_file" in v["reason"] and "twice" in v["reason"]

    ran_after = []
    _client(monkeypatch, [["write_file"], None])
    monkeypatch.setattr(ag, "_execute_tool", lambda name, args, **kw: ran_after.append(name) or "ok")
    monkeypatch.setattr(ag, "_ledger_tool_call", real_ledger)
    with pytest.raises(tr.ResumeRefused):
        tr.resume(task)
    assert ran_after == [], "the outward call was made a second time"


def test_a_read_only_tool_after_an_outward_one_in_the_same_turn_does_not_hide_it(monkeypatch, task):
    """Two calls in one turn: the outward one finished, then the read-only one was in flight. The
    marker used to name only the last (read-only) call, so the resume went ahead unasked."""
    ran = []
    _client(monkeypatch, [["write_file", "read_file"]])

    def _exec(name, args, **kw):
        if name == "read_file":
            raise _Crash("died with read_file in flight")
        ran.append(name)
        return "result of " + name
    monkeypatch.setattr(ag, "_execute_tool", _exec)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}], session_ctx={"task_id": task})
    assert ran == ["write_file"]
    v = tr.resumability(task)
    assert v["needs_confirmation"] is True and "write_file" in v["reason"]


def test_after_the_round_is_saved_nothing_is_held(monkeypatch, task):
    """Once the round's checkpoint holds the result, a later crash resumes without asking."""
    _client(monkeypatch, [["write_file"], ["read_file"], "crash"])
    monkeypatch.setattr(ag, "_execute_tool", lambda name, args, **kw: "result of " + name)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}], session_ctx={"task_id": task})
    blob = tr.read(task)
    assert blob["pending_tool"] is None and not blob.get("round_done")
    assert tr.resumability(task)["needs_confirmation"] is False