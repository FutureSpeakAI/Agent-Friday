"""Crash resume: a task that dies midstream is picked up, not re-run.

These tests simulate the real failure — the process disappears in the middle
of an agent loop — by raising ``_Crash`` out of the fake Anthropic client and
then starting a SECOND loop from whatever survived on disk. The second loop
gets a fresh fake client with a fresh call counter, so "the tool did not run
again" is measured, not asserted about a mock that was never reset.

The property under test is not "a file was written". It is:

  * the work already done is still in the transcript after the crash,
  * the tools that already completed are NOT executed a second time, and
  * a tool that was IN FLIGHT when the crash happened stops the resume and
    says why, instead of quietly re-running it.
"""

import time
import types

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import task_journal as tj
from agent_friday.services import task_resume as tr


class _Crash(RuntimeError):
    """Stands in for the process going away."""


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    """Isolate the journal. Without BASE_DIR_OVERRIDE these tests write real
    transcripts into ~/.friday/tasks/ on the maintainer's machine — the same
    class of leak that filled the disk three times (F47/F49/F51)."""
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


def test_the_journal_is_isolated(tmp_path):
    """A test that proves the guard above is doing something: every path this
    file writes must be under tmp_path, never the real ~/.friday."""
    assert str(tmp_path) in str(tj.tasks_dir())


@pytest.fixture
def task(monkeypatch):
    """A journalled task id, with the loop's task context pushed."""
    tid = "resume-0001"
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


def _fake_client(monkeypatch, script, ran):
    """`script` is a list of per-call behaviours: a tool name to call, the
    string 'crash', or None to finish the turn."""
    calls = {"n": 0}

    class _Msgs:
        def create(self, **kw):
            i = calls["n"]
            calls["n"] += 1
            step = script[i] if i < len(script) else None
            if step == "crash":
                raise _Crash("the desktop server went away")
            txt = types.SimpleNamespace(type="text", text=f"step {i + 1}")
            usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
            if step is None:
                return types.SimpleNamespace(content=[txt],
                                             stop_reason="end_turn", usage=usage)
            blk = types.SimpleNamespace(type="tool_use", id=f"t{i}",
                                        name=step, input={"q": i})
            return types.SimpleNamespace(content=[txt, blk],
                                         stop_reason="tool_use", usage=usage)

    monkeypatch.setattr(ag, "get_anthropic_client",
                        lambda: types.SimpleNamespace(messages=_Msgs()))

    def _exec(name, args, **kw):
        ran.append(name)
        return f"result of {name}"

    monkeypatch.setattr(ag, "_execute_tool", _exec)
    return calls


# ── the checkpoint ───────────────────────────────────────────────────────────

def test_the_transcript_survives_a_crash_midway(monkeypatch, task):
    ran = []
    _fake_client(monkeypatch, ["read_file", "search_wiki", "crash"], ran)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": task},
                              orb_label="midstream task")
    assert ran == ["read_file", "search_wiki"]

    blob = tr.read(task)
    assert blob is not None, "nothing was checkpointed"
    assert blob["iteration"] == 2
    # both completed tools and both of their results are in the saved convo
    text = str(blob["convo"])
    assert "result of read_file" in text and "result of search_wiki" in text
    assert blob["pending_tool"] is None, "no tool was in flight at the crash"


def test_the_checkpoint_is_always_a_transcript_the_api_would_accept(
        monkeypatch, task):
    """An inconsistent transcript is a 400 on resume, not a degraded resume.
    This is the property the checkpoint POSITION exists to guarantee."""
    ran = []
    _fake_client(monkeypatch, ["read_file", "crash"], ran)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": task})
    assert tr._consistent(tr.read(task)["convo"]) is True


def test_a_finished_turn_leaves_no_checkpoint(monkeypatch, task):
    """A stale transcript on disk would offer to redo work that is done."""
    ran = []
    _fake_client(monkeypatch, ["read_file", None], ran)
    text, _ = ag._call_claude_agent([{"role": "user", "content": "go"}],
                                    session_ctx={"task_id": task})
    assert text == "step 2"
    assert tr.read(task) is None


def test_a_plain_chat_turn_is_not_checkpointed(monkeypatch):
    """No task id, no checkpoint: writing a whole transcript to disk for a
    turn the user is watching buys nothing and costs a disk write per tool."""
    ran = []
    _fake_client(monkeypatch, ["read_file", None], ran)
    ag._call_claude_agent([{"role": "user", "content": "go"}], session_ctx=None)
    assert tr.read("resume-0001") is None


# ── the resume ───────────────────────────────────────────────────────────────

def test_resume_continues_the_task_without_rerunning_finished_tools(
        monkeypatch, task):
    """The whole point. Two tools completed before the crash; the resumed run
    must not touch them, and must return the task's real answer."""
    ran = []
    _fake_client(monkeypatch, ["read_file", "search_wiki", "crash"], ran)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": task},
                              orb_label="midstream task")
    assert ran == ["read_file", "search_wiki"]

    # ── process restarts ──
    ran_after = []
    _fake_client(monkeypatch, ["write_file", None], ran_after)
    text, trace = tr.resume(task)

    assert ran_after == ["write_file"], \
        "a tool that had already completed was executed again"
    assert text == "step 2"
    # the receipt covers the whole task, not just the part after the crash
    assert [t["name"] for t in trace] == ["read_file", "search_wiki", "write_file"]


def test_the_resumed_run_sees_the_work_that_was_already_done(monkeypatch, task):
    """Resume is only worth anything if the model gets the earlier results."""
    ran = []
    _fake_client(monkeypatch, ["read_file", "crash"], ran)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": task})

    seen = {}

    class _Msgs:
        def create(self, **kw):
            seen["messages"] = kw["messages"]
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="done")],
                stop_reason="end_turn",
                usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))

    monkeypatch.setattr(ag, "get_anthropic_client",
                        lambda: types.SimpleNamespace(messages=_Msgs()))
    tr.resume(task)
    assert "result of read_file" in str(seen["messages"])


def test_resume_is_refused_when_there_is_nothing_to_resume():
    with pytest.raises(tr.ResumeRefused) as e:
        tr.resume("never-existed")
    assert "no checkpoint" in str(e.value)


# ── the in-flight tool: the thing that must NOT be guessed ───────────────────

def test_a_side_effecting_tool_in_flight_blocks_the_resume(monkeypatch, task):
    """The process died between 'dispatch' and 'got the result'. Nothing on
    disk can say whether the side effect landed, so resume must stop and say
    so rather than re-run it."""
    ran = []
    _fake_client(monkeypatch, ["read_file", "write_file"], ran)

    real_exec = ag._execute_tool

    def _exec(name, args, **kw):
        if name == "write_file":
            raise _Crash("died with write_file in flight")
        return real_exec(name, args, **kw)

    monkeypatch.setattr(ag, "_execute_tool", _exec)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": task})

    blob = tr.read(task)
    assert blob["pending_tool"]["name"] == "write_file"

    v = tr.resumability(task)
    assert v["needs_confirmation"] is True
    assert "write_file" in v["reason"] and "twice" in v["reason"]
    with pytest.raises(tr.ResumeRefused) as e:
        tr.resume(task)
    assert "write_file" in str(e.value)


def test_a_read_only_tool_in_flight_does_not_block(monkeypatch, task):
    """Ring 0 is a pure read. Running it twice is the same as running it once,
    so there is nothing to ask about and nothing to gate."""
    ran = []
    _fake_client(monkeypatch, ["search_wiki", "read_file"], ran)

    def _exec(name, args, **kw):
        if name == "read_file":
            raise _Crash("died with read_file in flight")
        ran.append(name)
        return f"result of {name}"

    monkeypatch.setattr(ag, "_execute_tool", _exec)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": task})

    assert tr.read(task)["pending_tool"]["name"] == "read_file"
    v = tr.resumability(task)
    assert v["needs_confirmation"] is False
    assert "changes nothing" in v["reason"]


def test_an_explicit_confirmation_lets_the_gated_resume_through(monkeypatch,
                                                                task):
    """The gate is a question, not a wall. Someone who knows the email did not
    go out must be able to say so."""
    ran = []
    _fake_client(monkeypatch, ["read_file", "write_file"], ran)

    def _exec(name, args, **kw):
        if name == "write_file":
            raise _Crash("died in flight")
        ran.append(name)
        return f"result of {name}"

    monkeypatch.setattr(ag, "_execute_tool", _exec)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": task})

    after = []
    _fake_client(monkeypatch, [None], after)
    text, _ = tr.resume(task, confirm_pending=True)
    assert text == "step 1"


# ── the guards ───────────────────────────────────────────────────────────────

def test_a_task_that_keeps_crashing_stops_being_offered(monkeypatch, task):
    """A task that kills the process kills it again on resume. Without this,
    opt-in auto-resume is a boot loop that bills on every pass."""
    ran = []
    _fake_client(monkeypatch, ["read_file", "crash"], ran)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": task})
    for _ in range(tr.MAX_ATTEMPTS):
        tr._bump_attempts(task)
    v = tr.resumability(task)
    assert v["resumable"] is False and "times without finishing" in v["reason"]


def test_a_transcript_over_the_cap_is_not_written(monkeypatch, task):
    monkeypatch.setattr(tr, "MAX_BLOB_BYTES", 200)
    assert tr.checkpoint(task, convo=[{"role": "user", "content": "x" * 5000}],
                         iteration=1) is False
    assert tr.read(task) is None


def test_checkpointing_off_writes_nothing(monkeypatch, task):
    monkeypatch.setattr(tr, "enabled", lambda: False)
    assert tr.checkpoint(task, convo=[{"role": "user", "content": "x"}],
                         iteration=1) is False
    assert tr.read(task) is None


def test_auto_resume_is_on_by_default(monkeypatch):
    """Long work keeps going across a restart without a person asking for it.
    The crash-loop guard is MAX_ATTEMPTS (above), not a default of off, and a
    step that is not safe to repeat still waits for a person."""
    monkeypatch.setattr(tr, "_settings", lambda: {})
    assert tr.auto_enabled() is True
    assert tr.enabled() is True
    monkeypatch.setattr(tr, "_settings", lambda: {"task_resume_auto": False})
    assert tr.auto_enabled() is False


def test_a_journal_delete_takes_the_checkpoint_with_it(monkeypatch, task):
    tr.checkpoint(task, convo=[{"role": "user", "content": "x"}], iteration=1)
    assert tr.read(task) is not None
    tj.delete(task)
    assert tr.read(task) is None


# ── boot ─────────────────────────────────────────────────────────────────────

def test_boot_lists_only_interrupted_tasks_that_can_actually_resume(
        monkeypatch, task):
    ran = []
    _fake_client(monkeypatch, ["read_file", "crash"], ran)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": task},
                              orb_label="midstream task")
    # a second task that was running but never checkpointed
    tj.index_put("resume-0002", "no checkpoint", "running", 1_000_000.0)
    tj.write_state("resume-0002", {"task_id": "resume-0002",
                                   "name": "no checkpoint", "status": "running"})
    try:
        tj.reconcile_on_boot()
        found = {r["task_id"] for r in tr.resumable_after_boot()}
        assert task in found
        assert "resume-0002" not in found, \
            "a task with no saved transcript cannot be resumed"
    finally:
        tj.delete("resume-0002")


# ── the routes the UI actually calls ────────────────────────────────────────

def _crash_a_task(monkeypatch, tid, mid_flight=None):
    """Leave a real checkpoint on disk for `tid`, optionally with a tool
    caught in flight."""
    ran = []
    script = ["read_file", mid_flight or "crash"]
    _fake_client(monkeypatch, script, ran)
    if mid_flight:
        def _exec(name, args, **kw):
            if name == mid_flight:
                raise _Crash("died in flight")
            ran.append(name)
            return f"result of {name}"
        monkeypatch.setattr(ag, "_execute_tool", _exec)
    with pytest.raises(_Crash):
        ag._call_claude_agent([{"role": "user", "content": "go"}],
                              session_ctx={"task_id": tid})


def test_the_resumable_endpoint_reports_the_saved_work(client, monkeypatch,
                                                       task):
    _crash_a_task(monkeypatch, task)
    r = client.get(f"/api/tasks/{task}/resumable")
    assert r.status_code == 200
    body = r.get_json()
    assert body["resumable"] is True and body["iteration"] == 1
    assert body["needs_confirmation"] is False
    assert "will" in body["reason"]


def test_the_resumable_endpoint_is_honest_when_there_is_nothing(client, task):
    body = client.get(f"/api/tasks/{task}/resumable").get_json()
    assert body["resumable"] is False
    assert body["reason"] == "no checkpoint was written"


def test_resume_refuses_with_409_and_a_reason_when_a_tool_was_in_flight(
        client, monkeypatch, task):
    """The UI must get the reason, not a bare failure — this 409 is what
    turns into 'send_email was running; resuming re-runs it'."""
    _crash_a_task(monkeypatch, task, mid_flight="write_file")
    r = client.post(f"/api/tasks/{task}/resume", json={})
    assert r.status_code == 409
    body = r.get_json()
    assert body["needs_confirmation"] is True
    assert "write_file" in body["reason"] and "twice" in body["reason"]


def test_resume_with_confirmation_is_accepted(client, monkeypatch, task):
    """The route's job here is the GATE, not the loop — the loop is covered
    above. tr.resume is stubbed so the worker thread this spawns cannot
    outlive the monkeypatch and reach the real Anthropic client with real
    money; a daemon thread racing a fixture teardown is not a thing to leave
    in a test suite."""
    _crash_a_task(monkeypatch, task, mid_flight="write_file")
    seen = {}
    monkeypatch.setattr(tr, "resume",
                        lambda tid, **kw: seen.update(tid=tid, **kw) or ("done", []))
    r = client.post(f"/api/tasks/{task}/resume", json={"confirm_pending": True})
    assert r.status_code == 200
    assert r.get_json()["resuming"] is True
    for _ in range(100):
        if seen:
            break
        time.sleep(0.01)
    assert seen == {"tid": task, "confirm_pending": True}


def test_resume_409s_when_there_is_no_checkpoint(client, task):
    r = client.post(f"/api/tasks/{task}/resume", json={})
    assert r.status_code == 409
    assert r.get_json()["reason"] == "no checkpoint was written"


def test_a_checkpoint_that_cannot_be_decrypted_is_not_called_missing(task):
    """Rotating the vault passphrase already left 20+ older task records on
    this machine failing with "GCM auth tag mismatch". Reporting that as "no
    checkpoint was written" would describe lost work as absent work."""
    tr.checkpoint(task, convo=[{"role": "user", "content": "x"}], iteration=3)
    path = tj.task_dir(task) / tr.BLOB
    path.write_bytes(b"AGF1" + bytes(64))       # present, undecryptable
    assert tr.read(task) is None
    v = tr.resumability(task)
    assert v["resumable"] is False
    assert v["unreadable"] is True
    assert "cannot be read" in v["reason"] and "passphrase" in v["reason"]


def test_a_checkpoint_from_another_loop_is_refused_by_name(task):
    """Only the Anthropic loop can be re-entered today. Feeding an
    OpenAI-shaped transcript to _call_claude_agent would be both a silent
    model substitution and a malformed payload: the two shapes disagree about
    how a tool call is linked to its result."""
    tr.checkpoint(task, convo=[{"role": "user", "content": "x"}], iteration=5,
                  loop="openai")
    v = tr.resumability(task)
    assert v["resumable"] is False
    assert "openai" in v["reason"] and "cannot be re-entered yet" in v["reason"]
    with pytest.raises(tr.ResumeRefused):
        tr.resume(task)
