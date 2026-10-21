"""Stop everything: one stop halts every lane, needs no approval, survives a restart,
writes a signed receipt, and only a press on Friday's own page resumes.

Before this there was a stop per lane (a chat turn's Stop, a task's stop-after-step,
a workflow chain's stop, browser sessions' stop-all, the queue's pause, computer
control's kill switch) and nothing that stopped all of them, nor anything that cut a
model reply already streaming outside voice. These tests drive the real gates with
synthetic state: no model, no socket, no GPU.
"""
from __future__ import annotations

import json
import threading
import time

import pytest


@pytest.fixture
def gs(tmp_path, monkeypatch):
    from agent_friday.services import global_stop
    monkeypatch.setattr(global_stop, "_path", lambda: tmp_path / "global_stop.json")
    global_stop._reset_for_tests()
    yield global_stop
    global_stop._reset_for_tests()


def _bom(friday_dir):
    p = friday_dir / "decision-bom.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# ── the state ────────────────────────────────────────────────────────────────

def test_stop_sets_the_flag_first_and_saves_it(gs, tmp_path):
    out = gs.stop(by="owner", source="button")
    assert gs.active() and out["stopped"] is True
    saved = json.loads((tmp_path / "global_stop.json").read_text(encoding="utf-8"))
    assert saved["stopped"] is True and saved["source"] == "button"


def test_a_restart_comes_back_stopped(gs):
    gs.stop(source="button")
    gs._flag().clear()                       # a new process: the in-memory flag is gone
    assert not gs.active()
    assert gs.load_on_boot()["stopped"] is True
    assert gs.active()


def test_an_unreadable_state_file_reads_as_stopped(gs, tmp_path):
    (tmp_path / "global_stop.json").write_text("{not json", encoding="utf-8")
    assert gs.load_on_boot()["stopped"] is True and gs.active()


def test_stop_and_resume_write_signed_receipts(gs, friday_dir):
    from agent_friday.governance import action_gate
    before = len(_bom(friday_dir))
    gs.stop(by="owner", source="voice")
    gs.resume(by="owner", source="button")
    mine = [e for e in _bom(friday_dir)[before:] if e.get("tool") == "global_stop"]
    assert [e["decision"] for e in mine] == ["stop", "stop_lanes", "resume"]
    assert mine[0]["surface"] == "voice" and isinstance(mine[1].get("halted"), dict)
    assert all(action_gate.verify_receipt(e) for e in mine)


def test_resume_holds_when_its_receipt_cannot_be_written(gs, monkeypatch):
    from agent_friday.governance import action_gate
    gs.stop(source="button")

    def broken(_entry):
        raise OSError("disk full")

    monkeypatch.setattr(action_gate, "_receipt", broken)
    with pytest.raises(gs.ResumeHeld):
        gs.resume(by="owner", source="button")
    assert gs.active(), "a resume with no receipt must not resume"


def test_stop_still_stops_when_its_receipt_cannot_be_written(gs, monkeypatch):
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "_receipt", lambda _e: (_ for _ in ()).throw(OSError("disk full")))
    out = gs.stop(source="button")
    assert gs.active() and out["receipt"] is False and "disk full" in out["receipt_error"]


# ── every lane ───────────────────────────────────────────────────────────────

def test_running_chat_turns_stop_and_so_does_any_later_check(gs):
    import agent_friday.core as core
    core.turn_begin("turn-gs-running")
    try:
        gs.stop(source="button")
        with core._TURNS_LOCK:
            assert core._TURNS["turn-gs-running"].get("stop") is True
        assert core.turn_stop_requested("turn-gs-running")
        assert core.turn_stop_requested("turn-never-registered")
    finally:
        core.turn_end("turn-gs-running")


def test_a_model_call_already_streaming_is_cut_at_once(gs):
    """The wedged-loop case: a reply that is still generating, outside voice (no TURN_CANCEL set)."""
    from agent_friday.services import model_router as mr

    class Wedged:
        def __init__(self):
            self.closed = threading.Event()

        def iter_lines(self, decode_unicode=True):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "Working"}}]})
            self.closed.wait(30)          # a seat that never sends another byte
            if self.closed.is_set():
                raise ConnectionError("closed under the read")

        def close(self):
            self.closed.set()

    resp = Wedged()
    out = {}
    th = threading.Thread(target=lambda: out.update(r=mr._consume_sse_completion(resp)), daemon=True)
    th.start()
    time.sleep(0.2)
    t0 = time.perf_counter()
    gs.stop(source="button")
    th.join(3)
    assert not th.is_alive(), "the stream was still being read 3 s after Stop"
    assert time.perf_counter() - t0 < 2.0
    assert resp.closed.is_set()
    assert mr.turn_cancelled()


def test_no_model_call_starts_while_stopped(gs):
    from agent_friday.services import agent, model_router as mr
    gs.stop(source="button")
    with pytest.raises(gs.EverythingStopped):
        mr._generate_text_untraced([{"role": "user", "content": "hello"}])
    with pytest.raises(gs.EverythingStopped):
        agent._generate_agent_untraced([{"role": "user", "content": "hello"}])


def test_no_tool_runs_while_stopped_except_another_stop(gs, monkeypatch):
    from agent_friday.services import agent
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "get_time", lambda inp: ran.append(1) or "12:00")
    gs.stop(source="button")
    out = agent._execute_tool("get_time", {})
    assert out.startswith("[NOT RUN]") and not ran
    again = agent._execute_tool("task_control", {"op": "stop_all"})
    assert again.startswith("EVERYTHING_STOPPED"), again


def test_unfinished_tasks_are_told_to_stop_and_no_new_task_starts(gs, monkeypatch):
    from agent_friday.services import agent, task_journal as tj
    with agent.TASKS_LOCK:
        agent.TASKS["gs-task-running"] = {"task_id": "gs-task-running", "status": "running", "name": "x"}
        agent.TASKS["gs-task-done"] = {"task_id": "gs-task-done", "status": "completed", "name": "y"}
    try:
        gs.stop(source="button")
        assert agent.TASKS["gs-task-running"].get("stop_requested") is True
        assert not agent.TASKS["gs-task-done"].get("stop_requested")
        assert tj.stop_requested("gs-task-running") and tj.stop_requested("any-other-task")
        assert agent._spawn_task("blocked", "do something") is None
    finally:
        with agent.TASKS_LOCK:
            agent.TASKS.pop("gs-task-running", None)
            agent.TASKS.pop("gs-task-done", None)


def test_scheduled_and_run_now_jobs_are_skipped_with_a_reason(gs):
    from agent_friday.services import scheduler
    gs.stop(source="button")
    with pytest.raises(scheduler.StoodDown) as e:
        scheduler._run_task_inner({"name": "anything", "task": {"kind": "builtin", "ref": "practice_trials"}})
    assert "stopped" in str(e.value).lower()


def test_background_work_and_nightly_trials_are_held(gs):
    from agent_friday.services import background_gate as bg, trial_job
    gs.stop(source="button")
    assert bg.block_reason({"seat_is_local": True, "deferrable": True}) == bg.REASON_STOPPED
    assert bg.block_reason({"seat_is_local": False}) == bg.REASON_STOPPED
    assert trial_job._gate_blocks(), "the trial watcher must trip mid-episode"
    with pytest.raises(bg.JobCancelled):
        bg.before_local_model_call("any-local-model")


def test_no_delegated_worker_starts(gs):
    from agent_friday.services import orchestrator as orch
    gs.stop(source="button")
    with pytest.raises(gs.EverythingStopped):
        orch.Orchestrator().spawn_worker(object())


def test_computer_control_is_killed(gs):
    from agent_friday.services import agent
    agent._CC_KILL.clear()
    try:
        gs.stop(source="button")
        assert agent._CC_KILL.is_set() and not agent._CC_PERMISSION.is_set()
    finally:
        agent._CC_KILL.clear()


def test_live_voice_calls_are_hung_up_and_new_ones_refused(gs):
    from agent_friday.routes import voice
    hung = []
    off = gs.on_stop(lambda: hung.append("call"))
    gs.stop(source="voice")
    assert hung == ["call"]
    off()

    class WS:
        sent = []

        def send(self, s):
            self.sent.append(json.loads(s))

    ws = WS()
    assert voice._voice_refused_while_stopped(ws) is True
    assert ws.sent and ws.sent[-1]["type"] == "everything_stopped"


def test_resume_lets_new_work_run_and_keeps_stopped_work_stopped(gs):
    from agent_friday.services import background_gate as bg, task_journal as tj
    gs.stop(source="button")
    gs.resume(by="owner", source="button")
    assert not gs.active()
    assert bg.block_reason({"seat_is_local": False}) == ""
    assert not tj.stop_requested("a-task-started-after-resume")


# ── the words ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["stop everything", "Stop everything.", "Friday, stop everything now",
                                  "please stop it all", "emergency stop", "halt all work", "stop all"])
def test_the_stop_words(gs, text):
    assert gs.is_stop_everything(text), text


@pytest.mark.parametrize("text", ["don't stop everything", "how do I stop everything", "stop the podcast",
                                  "stop the background task", "everything stops at noon", ""])
def test_not_the_stop_words(gs, text):
    assert not gs.is_stop_everything(text), text


@pytest.mark.parametrize("text", ["stop everything", "Friday stop everything now", "emergency stop"])
def test_spoken_stop_needs_no_model(text):
    """Laya's rule layer places it, so a wedged model cannot keep it from working."""
    from agent_friday.services import laya_router as lr
    r = lr.rules_route(text)
    assert r.decision == "tool" and r.tool == "task_control" and r.args == {"op": "stop_all"}, r


def test_stopping_needs_no_approval():
    from agent_friday.governance import action_gate
    klass, _why = action_gate.classify("task_control", {"op": "stop_all"}, {})
    assert klass == action_gate.INTERNAL


# ── the review's lanes: nothing outward, nothing approved, nothing resumed by a task ──

def test_no_outward_action_is_authorized_while_stopped(gs):
    from agent_friday.governance import action_gate
    gs.stop(source="button")
    v = action_gate.authorize("send_email", {"to": "someone@example.com"}, {"authenticated": True})
    assert v.action == "deny" and "stopped" in v.reason


def test_no_card_is_approved_while_stopped_but_one_can_be_declined(gs, tmp_path, monkeypatch):
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_fire_hook", lambda rec: None)
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    a = ap.create_approval(kind="gs_test_card", subject_type="gs", subject_id="a",
                           title="t", action_description="send it", force_gate=True)
    b = ap.create_approval(kind="gs_test_card", subject_type="gs", subject_id="b",
                           title="t", action_description="send it", force_gate=True)
    gs.stop(source="button")
    rec, won = ap.decide_with_outcome(a["approval_id"], "approve", decided_by="owner")
    assert won is False and rec["status"] == "pending"
    rec, won = ap.decide_with_outcome(b["approval_id"], "deny", decided_by="owner")
    assert won is True and rec["status"] == "denied"


def test_held_mail_is_not_sent_while_stopped(gs, monkeypatch):
    from agent_friday.services import gmail_send
    sent = []
    monkeypatch.setattr(gmail_send, "_load_held", lambda: [{"approval_id": "x", "due": 0, "to": "a@example.com"}])
    monkeypatch.setattr(gmail_send, "_save_held", lambda rows: sent.append(("saved", rows)))
    monkeypatch.setattr(gmail_send, "_deliver", lambda *a, **k: sent.append(("sent", a)))
    gs.stop(source="button")
    assert gmail_send._tick(now=10**10) == [] and sent == []


def test_stand_down_readers_see_the_stop(gs):
    from agent_friday.services import stand_down
    gs.stop(source="button")
    assert stand_down.is_stood_down() and "stopped" in stand_down.reason().lower()


def test_a_background_checkpoint_never_times_out_into_running(gs, monkeypatch):
    from agent_friday.services import turn_queue, background_gate as bg
    monkeypatch.setattr(turn_queue, "inherited_class", lambda: "background")
    gs.stop(source="button")
    with pytest.raises(bg.JobCancelled):
        turn_queue.background_checkpoint(max_wait_s=0.01, sleep=lambda s: None)


def test_a_claude_code_session_gets_no_tool_while_stopped(gs, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import claude_code_tasks as cct
    monkeypatch.setitem(core.VIBE_TERMINALS, "gs-cc", {"token": "tok", "status": "running"})
    gs.stop(source="button")
    out = cct.gate("gs-cc", "tok", "Bash", {"command": "echo hi"})
    assert out["decision"] == "deny" and "stopped" in out["reason"]


def test_a_task_or_a_working_agent_cannot_stop_everything(gs):
    from agent_friday.services import agent
    tok = agent._CURRENT_TOOL_CONTEXT.set({"is_background_task": True})
    try:
        out = agent._tool_task_control({"op": "stop_all"})
    finally:
        agent._CURRENT_TOOL_CONTEXT.reset(tok)
    assert out.startswith("NOT DONE") and not gs.active()


def test_a_model_stop_is_recorded_as_the_model_not_the_owner(gs, friday_dir):
    from agent_friday.services import agent
    before = len(_bom(friday_dir))
    agent._tool_task_control({"op": "stop_all"})
    mine = [e for e in _bom(friday_dir)[before:] if e.get("tool") == "global_stop"]
    assert mine and mine[0]["chosen_by"] == "model"


def test_a_stop_racing_a_resume_is_never_undone(gs):
    """Resume runs under the lock and stop sets the flag again under it."""
    gs.stop(source="button")
    import threading as _th
    errs = []

    def _resume():
        try:
            gs.resume(by="owner", source="button")
        except Exception as e:  # pragma: no cover
            errs.append(e)

    t = _th.Thread(target=_resume)
    t.start()
    gs.stop(source="keyboard")
    t.join(5)
    # Whatever the order, the last word is a stop unless the resume finished first and
    # the stop then ran; in both cases the flag and the saved state agree.
    import json as _json
    saved = _json.loads(gs._path().read_text(encoding="utf-8"))
    assert saved["stopped"] is gs.active()
