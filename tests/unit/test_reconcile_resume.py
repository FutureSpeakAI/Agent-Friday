"""Boot reconciliation now that services/task_resume exists.

``reconcile_tasks`` must not say, unconditionally, "It was a free-form run, so
I cannot pick it up mid-way — say the word and I will start it again." The
agent loop checkpoints its transcript, and a restored transcript carries every
completed tool's result with it, so resuming re-buys nothing.

The cost being protected is the token spend lost to partially completed
tasks, local and cloud both. So the thing under test is not "a message
changed" — it is that the three cases get three different, honest answers:

  * mid-flight WITH a checkpoint  -> offer to resume (or resume, if opted in)
  * mid-flight with a tool IN FLIGHT -> offer, and name what re-running risks
  * mid-flight with NO checkpoint -> the original message, unchanged

and a fourth case: a task that was still WAITING for a busy local seat. Those
never started, so nothing was spent on them and re-queuing is lossless — but
``task_journal.reconcile_on_boot`` only looks at ``running``/``queued``, so an
unhandled ``queued-for-seat`` record is not even marked interrupted. It keeps a
status saying it is about to start, forever, with nothing left in the process
that could start it.
"""

import time

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import reconcile as rc


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    reports = []
    monkeypatch.setattr(rc, "_report",
                        lambda cid, text, meta=None: reports.append(
                            {"conversation_id": cid, "text": text,
                             "meta": meta or {}}))
    yield reports
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


def _task(tid, status="running", **extra):
    rec = {"id": tid, "task_id": tid, "name": f"task {tid}",
           "status": status, "prompt": "do the thing",
           "conversation_id": "conv-1", "created": time.time()}
    rec.update(extra)
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = rec
    return rec


def _verdict(monkeypatch, **fields):
    base = {"resumable": False, "reason": "no checkpoint was written",
            "iteration": 0, "needs_confirmation": False}
    base.update(fields)
    monkeypatch.setattr(rc, "_resumability", lambda tid: base)
    return base


# ── mid-flight work ──────────────────────────────────────────────────────────

def test_no_checkpoint_still_gets_the_honest_old_message(monkeypatch, _clean):
    """The original sentence was right for this case and must survive."""
    _task("t1")
    _verdict(monkeypatch)
    out = rc.reconcile_tasks()
    assert out["interrupted"] == ["t1"] and out["resumable"] == []
    msg = _clean[0]
    assert "cannot pick it up mid-way" in msg["text"]
    assert msg["meta"]["resumable"] is False


def test_a_checkpointed_task_is_offered_not_written_off(monkeypatch, _clean):
    """The change that matters: work already paid for is not re-bought.
    (The offer wording; with auto-resume on it is picked up instead -- see
    the auto tests below.)"""
    from agent_friday.services import task_resume as tr
    monkeypatch.setattr(tr, "auto_enabled", lambda: False)
    _task("t2")
    _verdict(monkeypatch, resumable=True, iteration=7,
             reason="7 step(s) of work are saved and will not be redone.")
    out = rc.reconcile_tasks()
    assert out["resumable"] == ["t2"]
    text = _clean[0]["text"]
    assert "cannot pick it up" not in text
    assert "step 7" in text and "pick it up where it stopped" in text
    with ag.TASKS_LOCK:
        rec = ag.TASKS["t2"]
    assert rec["resumable"] is True
    assert "can be resumed" in rec["status_reason"]


def test_a_tool_in_flight_is_named_as_a_risk_not_hidden(monkeypatch, _clean):
    """A side effect that may or may not have landed is the one thing the
    user must be told before they say yes."""
    _task("t3")
    _verdict(monkeypatch, resumable=True, iteration=4, needs_confirmation=True,
             reason=("'send_email' was running when the process stopped, and "
                     "nothing on disk can say whether it finished. Resuming "
                     "re-runs it."))
    rc.reconcile_tasks()
    msg = _clean[0]
    assert "send_email" in msg["text"] and "re-run that tool" in msg["text"]
    assert msg["meta"]["needs_confirmation"] is True


def test_auto_resume_turned_off_only_offers(monkeypatch, _clean):
    started = []
    monkeypatch.setattr(rc, "_resume_in_background", lambda tid: started.append(tid))
    from agent_friday.services import task_resume as tr
    monkeypatch.setattr(tr, "auto_enabled", lambda: False)
    _task("t4")
    _verdict(monkeypatch, resumable=True, iteration=2, reason="saved.")
    rc.reconcile_tasks()
    assert started == [], "nothing may resume itself without being asked"
    assert "Say the word" in _clean[0]["text"]


def test_auto_resume_on_picks_it_up(monkeypatch, _clean):
    started = []
    monkeypatch.setattr(rc, "_resume_in_background", lambda tid: started.append(tid))
    from agent_friday.services import task_resume as tr
    monkeypatch.setattr(tr, "auto_enabled", lambda: True)
    _task("t5")
    _verdict(monkeypatch, resumable=True, iteration=9, reason="saved.")
    rc.reconcile_tasks()
    assert started == ["t5"]
    assert _clean[0]["meta"]["kind"] == "resume_notice"
    assert "nothing already done gets redone" in _clean[0]["text"]


def test_a_gated_task_never_auto_resumes(monkeypatch, _clean):
    """Opting into auto-resume is not opting into re-sending an email."""
    started = []
    monkeypatch.setattr(rc, "_resume_in_background", lambda tid: started.append(tid))
    from agent_friday.services import task_resume as tr
    monkeypatch.setattr(tr, "auto_enabled", lambda: True)
    _task("t6")
    _verdict(monkeypatch, resumable=True, iteration=3, needs_confirmation=True,
             reason="'write_file' was running ... Resuming re-runs it.")
    rc.reconcile_tasks()
    assert started == [], "auto-resume jumped a human gate"


def test_a_broken_resume_layer_still_lets_the_task_be_marked(monkeypatch,
                                                              _clean):
    """Reconciliation's job is to make sure a stopped job SAYS it stopped.
    A resume subsystem that is broken must degrade that to the old message,
    never to silence."""
    import agent_friday.services.task_resume as tr
    monkeypatch.setattr(tr, "resumability",
                        lambda tid: (_ for _ in ()).throw(RuntimeError("boom")))
    _task("t7")
    out = rc.reconcile_tasks()
    assert out["interrupted"] == ["t7"] and out["resumable"] == []
    assert "cannot pick it up mid-way" in _clean[0]["text"]


def test_the_real_resumability_helper_swallows_its_own_failures(monkeypatch):
    """_resumability is the boundary, and it must never raise into boot."""
    import agent_friday.services.task_resume as tr
    monkeypatch.setattr(tr, "resumability",
                        lambda tid: (_ for _ in ()).throw(RuntimeError("x")))
    v = rc._resumability("anything")
    assert v["resumable"] is False


# ── work that never started ──────────────────────────────────────────────────

def test_a_task_waiting_for_a_busy_seat_is_re_queued(monkeypatch, _clean):
    """It never ran, so nothing was spent and re-queuing is lossless."""
    spawned = {}

    def _fake_spawn(name, prompt, **kw):
        spawned["name"], spawned["prompt"], spawned["kw"] = name, prompt, kw
        return "new-1"

    monkeypatch.setattr(ag, "_spawn_task", _fake_spawn)
    _task("q1", status="queued-for-seat", model="bonsai2:27b")
    out = rc.readmit_queued()
    assert out["readmitted"] == [{"task_id": "q1", "new_task_id": "new-1"}]
    assert spawned["prompt"] == "do the thing"
    assert spawned["kw"]["model"] == "bonsai2:27b"
    with ag.TASKS_LOCK:
        rec = ag.TASKS["q1"]
    assert rec["status"] == "superseded"
    assert "nothing was lost" in rec["status_reason"]
    assert "put it back in the queue" in _clean[0]["text"]


def test_a_queued_task_with_no_prompt_is_marked_not_silently_kept(monkeypatch,
                                                                  _clean):
    """The failure this whole function exists to stop is a record that says
    'about to start' with nothing left that could start it."""
    monkeypatch.setattr(ag, "_spawn_task",
                        lambda *a, **k: pytest.fail("must not spawn"))
    _task("q2", status="queued-for-seat", prompt="   ")
    out = rc.readmit_queued()
    assert out["failed"] == ["q2"]
    with ag.TASKS_LOCK:
        assert ag.TASKS["q2"]["status"] == "interrupted"


def test_only_queued_for_seat_tasks_are_re_queued(monkeypatch, _clean):
    """A running task's re-admission is the checkpoint path, not this one;
    re-spawning it here would duplicate its side effects."""
    monkeypatch.setattr(ag, "_spawn_task",
                        lambda *a, **k: pytest.fail("must not spawn"))
    _task("r1", status="running")
    _task("c1", status="complete")
    assert rc.readmit_queued() == {"readmitted": [], "failed": []}


def test_a_spawn_failure_does_not_take_the_others_with_it(monkeypatch, _clean):
    calls = {"n": 0}

    def _spawn(name, prompt, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dispatcher is down")
        return "new-2"

    monkeypatch.setattr(ag, "_spawn_task", _spawn)
    _task("q3", status="queued-for-seat")
    _task("q4", status="queued-for-seat")
    out = rc.readmit_queued()
    assert len(out["failed"]) == 1 and len(out["readmitted"]) == 1


def test_boot_runs_the_queue_pass_after_the_mid_flight_pass(monkeypatch):
    """Order matters: re-queuing spawns NEW tasks, and a task spawned by this
    boot must not then be swept up as a casualty of the previous one."""
    order = []
    monkeypatch.setattr(rc, "reconcile_research", lambda resume=True: order.append("research") or {})
    monkeypatch.setattr(rc, "reconcile_tasks", lambda: order.append("tasks") or {})
    monkeypatch.setattr(rc, "readmit_queued", lambda: order.append("queued") or {})
    rc.run_at_boot()
    assert order.index("tasks") < order.index("queued")
