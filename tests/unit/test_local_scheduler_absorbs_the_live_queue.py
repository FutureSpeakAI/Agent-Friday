"""The scheduler sits on the Local AI queue that is live: work a person is
waiting on (services/visible_jobs, ``foreground``) is a job they asked for and
never waits for their own turn; an unlabelled background call becomes a
deferrable visible job in the same queue; the rows the chip shows come from one
place. Fake seats and fake clocks; no model, no server."""
import threading
import time

import pytest
from turn_queue_helpers import fast, wait_until

from agent_friday.services import background_gate as bg
from agent_friday.services import local_scheduler as ls
from agent_friday.services import seat_supervisor as ss
from agent_friday.services import turn_queue as tq
from agent_friday.services import visible_jobs as vj


@pytest.fixture
def world(monkeypatch):
    fast(monkeypatch)
    ls.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)
    ls.set_residency_probe(lambda m: True)
    ls.set_ram_probe(lambda: 64.0)
    away = [1e9]
    monkeypatch.setattr(bg, "OS_IDLE_READER", lambda: away[0])
    monkeypatch.setattr(bg, "idle_threshold_s", lambda: 600.0)
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: away[0])
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [])
    sup = ss.SeatSupervisor(gate=bg.block_reason, sleep=lambda s: time.sleep(0.005),
                            poll_seconds=0.01)
    sup.thread_factory = lambda rec: None
    monkeypatch.setattr(bg, "_SUPERVISOR_PROVIDER", lambda: sup)
    tq.queue_for("brain", 1)
    tq.attach_seat_queue(sup.queue, on_idle=lambda s: sup.poke())
    yield sup, away
    ls.reset_for_tests()
    tq.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)


def _job(i, **kw):
    rec = {"id": i, "seat": "local/brain", "kind": "workspace_job", "label": "a workspace job: " + i,
           "deferrable": False}
    rec.update(kw)
    return rec


def test_work_a_person_waits_on_is_a_job_they_asked_for_and_never_waits_for_their_own_turn(world):
    sup, _away = world
    turn = tq.acquire("brain", "chat", "turn-1", title="x")            # their own chat is under way
    assert sup.wire_spawn(_job("button", foreground=True)) == "running", \
        "the button they pressed inside their own turn is not held for that turn"
    held = {t.id: t.cls for t in tq.queue_for("brain").running_tickets()}
    assert held["button"] == "user_job" and held["turn-1"] == "chat"
    row = [j for j in ls.jobs() if j["id"] == "button"][0]
    assert row["class"] == "user_job" and row["source"] == "task", "one row, from the queue's own record"
    sup.on_task_end("button")
    assert "button" not in {t.id for t in tq.queue_for("brain").running_tickets()}
    turn.release()


def test_deferrable_work_keeps_the_1_0_2_rules_even_when_it_is_a_visible_job(world):
    sup, _away = world
    turn = tq.acquire("brain", "chat", "turn-1", title="x")
    assert sup.wire_spawn(_job("wiki", deferrable=True)) == "queued-for-seat"
    assert sup.queue.get("wiki")["wait_reason"] == bg.REASON_INTERACTIVE
    turn.release()


def test_an_unlabelled_background_call_becomes_a_deferrable_visible_job(world, monkeypatch):
    sup, away = world
    monkeypatch.setattr(ls, "WRAP_BACKGROUND", [True])
    away[0] = 5.0
    seen = {}
    got = []

    def call():
        with ls.admit("local_call.call", model="default", cls="background"):
            cur = vj.current()
            seen["job"] = cur
            seen["queue_ids"] = [r["id"] for r in sup.snapshot()["running"]]
            got.append("ran")
    th = threading.Thread(target=call, daemon=True)
    th.start()
    assert wait_until(lambda: ls.queue_snapshot()["count"] == 1)
    waiting = ls.queue_snapshot()["queued"][0]
    assert waiting["deferrable"] and waiting["why"] == bg.REASON_IDLE
    away[0] = 1e9
    th.join(10)
    assert got == ["ran"]
    job = seen["job"]
    assert job is not None and job.kind == "background_call", "a visible job names it"
    assert job.deferrable and job.id in seen["queue_ids"], "the card and the queue row are one id"
    assert vj.current() is None and ls.queue_snapshot()["jobs"] == [], "both end with the call"


def test_the_setting_turns_the_wrapping_off(world, monkeypatch):
    sup, away = world
    monkeypatch.setattr(ls, "WRAP_BACKGROUND", [True])
    real = ls._setting
    monkeypatch.setattr(ls, "_setting", lambda k, d: False if k == "local_scheduler_wrap_background" else real(k, d))
    away[0] = 1e9
    g = ls.admit("local_call.call", model="default", cls="background")
    assert g.how == "admitted", "a plain ticket at the scheduler, no queue job"
    g.release()


def test_a_work_stretch_marked_time_bound_is_not_wrapped(world, monkeypatch):
    sup, away = world
    monkeypatch.setattr(ls, "WRAP_BACKGROUND", [True])
    away[0] = 5.0
    with ls.work("a schedule that is due", cls="background", deferrable=False):
        g = ls.admit("local_call.call", model="default")
    assert g.how == "admitted"
    g.release()


def test_a_cancelled_wrapped_call_is_a_cancelled_wait(world, monkeypatch):
    sup, away = world
    monkeypatch.setattr(ls, "WRAP_BACKGROUND", [True])
    away[0] = 5.0
    out = []

    def call():
        try:
            with ls.admit("local_call.call", model="default", cls="background"):
                out.append("reached the model")
        except ls.LocalWorkCancelled:
            out.append("cancelled")
    th = threading.Thread(target=call, daemon=True)
    th.start()
    assert wait_until(lambda: ls.queue_snapshot()["count"] == 1)
    assert sup.cancel(ls.queue_snapshot()["queued"][0]["id"])
    th.join(10)
    assert out == ["cancelled"]
    assert ls.queue_snapshot()["jobs"] == []


def test_a_call_made_at_a_transport_is_a_model_call_row_in_the_chip(world):
    sup, away = world
    away[0] = 1e9
    ls.set_residency_probe(lambda m: m in ("", "default"))
    big = tq.acquire("default", "user_job", "job-1", model="default", kind="local_call.call", label="a research note")
    waiting = []
    th = threading.Thread(target=lambda: waiting.append(
        tq.acquire("default", "user_job", "job-2", model="default", kind="local_call.call", label="another note")),
        daemon=True)
    th.start()
    assert wait_until(lambda: tq.status_for("job-2") and tq.status_for("job-2")["state"] == "waiting")
    snap = ls.queue_snapshot()
    rows = {r["id"]: r for r in snap["jobs"]}
    assert rows["job-1"]["kind"] == "model_call" and rows["job-1"]["state"] == "running"
    assert rows["job-2"]["kind"] == "model_call" and rows["job-2"]["state"] == "queued"
    assert rows["job-2"]["label"] == "another note" and rows["job-2"]["cancellable"]
    big.release()
    th.join(5)
    waiting[0].release()


def test_the_gate_waits_for_a_registered_voice_turn_that_never_admitted(world, monkeypatch):
    """Live's own registrations count: the voice turn (background_gate.interactive_begin)."""
    sup, away = world
    away[0] = 1e9
    monkeypatch.setattr(bg, "interactive_kinds", lambda: ["voice"])
    t = tq.Ticket("probe", "background", "brain", model="", kind="task")
    assert ls.gate(t) == "waiting for your chat to finish"
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [bg.KIND_VOICE_CALL])
    assert ls.gate(t) == "waiting for you to be idle", "an open call is not a turn, but it is not idle"
    sched = tq.Ticket("probe2", "background", "brain", model="", kind="scheduled")
    assert ls.gate(sched) == "", "and a schedule that is due is not held by an open call"


def test_the_seat_queue_probe_ignores_the_job_that_is_about_to_yield_its_own_seat(world, monkeypatch):
    sup, away = world
    away[0] = 1e9
    tq.queue_for("brain").background_started("running", label="first")
    probe = tq.Ticket(tq.PROBE_ID, "background", "brain", model="", kind="task")
    assert ls.gate(probe) == "", "one job to a seat is the seat queue's rule; the gate only counts other seats"
    other = tq.Ticket("another-ticket", "background", "brain", model="", kind="task")
    assert ls.gate(other) == "waiting for the background job that is already running"
    tq.queue_for("brain").background_finished("running")


def test_the_chip_words_the_new_rows_in_both_ui_files():
    """The scheduler's rows (a call at a transport, a waiting lease, an unclaimed
    background call) use the chip that is there; a row that cannot be cancelled
    shows no Cancel. Both pages carry the same words (index.html and its mirror)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for name in ("index.html", "ui_parts/app.html"):
        text = (root / name).read_text(encoding="utf-8")
        assert "background_call: 'background AI work'" in text, name
        assert "model_call: 'model call'" in text and "gpu_lease: 'GPU turn'" in text, name
        assert "j.cancellable !== false && " in text, name
        assert text.count("data-testid\": \"local-ai-queue-chip\"") == 1, "one indicator, not two"
        assert "local-work-chip" not in text, "no second chip"
