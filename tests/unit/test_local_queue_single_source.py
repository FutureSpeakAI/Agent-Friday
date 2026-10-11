"""One queue, one indicator (RM-3b: 1.0.2's work queue absorbed into 1.1's scheduler).

The Local AI queue (seat_queue + seat_supervisor, with background_gate's rules)
and the local work scheduler (local_scheduler over turn_queue) are one thing:
the chip, the tray, GET /api/local-queue and the voice tools read the same
rows, the idle window is the owner's, a person's turn never queues behind a
paused job, and the two `queue_status` tools no longer share a name. Fake
seats and fake clocks; no model, no server."""
import threading
import time

import pytest
from turn_queue_helpers import fast, wait_until

from agent_friday.services import background_gate as bg
from agent_friday.services import local_scheduler as ls
from agent_friday.services import seat_supervisor as ss
from agent_friday.services import turn_queue as tq


@pytest.fixture
def world(monkeypatch):
    fast(monkeypatch)
    ls.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)
    ls.set_residency_probe(lambda m: True)
    ls.set_ram_probe(lambda: 64.0)
    away = [1e9]                                     # the OS idle clock, in seconds
    monkeypatch.setattr(bg, "OS_IDLE_READER", lambda: away[0])
    monkeypatch.setattr(bg, "idle_threshold_s", lambda: 600.0)
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [])
    sup = ss.SeatSupervisor(gate=bg.block_reason, sleep=lambda s: time.sleep(0.005),
                            poll_seconds=0.01)
    sup.thread_factory = lambda rec: None            # a worker is not needed to hold a seat
    monkeypatch.setattr(bg, "_SUPERVISOR_PROVIDER", lambda: sup)
    tq.queue_for("brain", 1)                         # pinned: one slot, whatever this PC serves
    tq.attach_seat_queue(sup.queue, on_idle=lambda s: sup.poke())
    yield sup, away
    ls.reset_for_tests()
    tq.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)


def _job(i, **kw):
    rec = {"id": i, "seat": "local/brain", "kind": "wiki_distill", "label": "wiki notes: " + i,
           "deferrable": False}
    rec.update(kw)
    return rec


def test_the_scheduler_and_the_queue_list_the_same_job_once(world):
    sup, _away = world
    sup.wire_spawn(_job("a"))
    sup.wire_spawn(_job("b"))
    rows = [j for j in ls.jobs() if j["id"] in ("a", "b")]
    assert sorted(j["id"] for j in rows) == ["a", "b"], "a job holding a ticket is not listed twice"
    snap = ls.queue_snapshot()
    assert [j["id"] for j in snap["running"]] == ["a"] and [j["id"] for j in snap["queued"]] == ["b"]
    assert snap["count"] == 1 and snap["queued"][0]["why"]


def test_agent_serves_the_schedulers_snapshot_to_the_chip_and_the_tray(world, monkeypatch):
    from agent_friday.services import agent
    sup, _away = world
    sup.wire_spawn(_job("a"))
    sup.wire_spawn(_job("b"))
    assert agent.local_queue_snapshot()["count"] == ls.queue_snapshot()["count"] == 1
    tip = ss.tray_tooltip(agent.local_queue_snapshot())
    assert tip and tip.startswith("Local AI: 1 task queued")


def test_other_scheduler_work_joins_the_same_list(world, monkeypatch):
    """A podcast waiting for the big brain is in the chip and the tray too."""
    sup, _away = world
    ls.register_source("podcast", lambda: [{
        "id": "podcast:1", "source": "podcast", "state": "waiting", "class": "background",
        "interactive": False, "label": "podcast", "kind": "podcast", "model": "", "needs": "the big brain",
        "reason": "waiting for you to be idle", "waited_s": 3.0, "cancellable": True}],
        lambda jid: jid == "podcast:1")
    snap = ls.queue_snapshot()
    assert [j["id"] for j in snap["queued"]] == ["podcast:1"]
    assert snap["queued"][0]["label"] == "podcast" and snap["queued"][0]["why"].endswith("idle")
    assert ls.cancel("podcast:1")["ok"]


def test_a_chat_is_not_a_background_job_in_the_list(world):
    sup, _away = world
    turn = tq.acquire("brain", "chat", "turn-1", title="Garden notes")
    assert ls.queue_snapshot()["jobs"] == []
    turn.release()


def test_the_idle_window_is_the_owners_not_the_schedulers(world, monkeypatch):
    monkeypatch.setattr(bg, "idle_threshold_s", lambda: 900.0)
    assert ls.idle_window_s() == 900.0
    settings_src = open(__import__("agent_friday.core", fromlist=["x"]).__file__, encoding="utf-8").read()
    assert '"background_idle_minutes": 10' in settings_src, "the owner's setting is still there"


def test_owner_idle_is_the_combined_signal_and_zero_while_a_person_is_here(world, monkeypatch):
    sup, away = world
    away[0] = 5.0                                    # five seconds of no keyboard
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 4000.0)
    assert ls.owner_idle_s() == 5.0, "the shorter of the two clocks"
    away[0] = 1e9
    assert ls.owner_idle_s() == 4000.0
    turn = tq.acquire("brain", "chat", "turn-1")
    assert ls.owner_idle_s() == 0.0
    turn.release()


def test_background_work_that_needs_a_swap_waits_for_the_owners_window(world, monkeypatch):
    sup, away = world
    ls.set_residency_probe(lambda m: m in ("", "default"))
    away[0] = 30.0                                   # back at the keyboard 30 s ago
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 30.0)
    t = tq.Ticket("probe", "background", "big", model="big", kind="task")
    assert ls.gate(t) == "waiting for you to be idle"
    away[0] = 1e9
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 1e9)
    assert ls.gate(t) == ""
    sched = tq.Ticket("probe2", "background", "big", model="big", kind="scheduled")
    away[0] = 30.0
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 30.0)
    assert ls.gate(sched) == "", "a scheduled job is time-bound and never waits for idle"


def test_one_background_job_at_a_time_across_seats(world, monkeypatch):
    sup, _away = world
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 1e9)          # the owner is away
    tq.queue_for("other", 1).background_started("job-a", label="first")
    t = tq.Ticket("job-b", "background", "brain", model="", kind="task")
    assert ls.gate(t) == "waiting for the background job that is already running"
    tq.queue_for("other").background_finished("job-a")
    assert ls.gate(t) == ""
    user = tq.Ticket("job-c", "user_job", "brain", model="", kind="task")
    tq.queue_for("other", 1).background_started("job-a", label="first")
    assert ls.gate(user) == "", "a job the owner asked for is not held by a background one"
    tq.queue_for("other").background_finished("job-a")


def test_a_task_the_owner_asked_for_is_not_held_for_idle_by_the_seat_bridge(world, monkeypatch):
    """Only a deferrable job waits for the window, and the queue applies that itself."""
    sup, away = world
    ls.set_residency_probe(lambda m: m in ("", "default"))
    away[0] = 1.0
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 1.0)
    assert sup.wire_spawn(_job("spawned", seat="local/big", deferrable=False)) == "running"
    assert sup.wire_spawn(_job("wiki", seat="local/vision", deferrable=True)) == "queued-for-seat"


def test_a_paused_job_lends_its_slot_so_the_chat_it_waits_for_is_not_queued_behind_it(world, monkeypatch):
    sup, _away = world
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [])
    assert sup.wire_spawn(_job("job")) == "running"
    log = []

    def chat():
        adm = tq.acquire("brain", "chat", "chat-1", title="x")
        log.append(("chat", adm.outcome, round(time.monotonic() - t0, 2)))
        time.sleep(0.15)
        adm.release()
        log.append(("chat-done",))

    def job_next_call():
        with tq.holding("local/brain", "background"):
            sup.wait_while_blocked("job")
            log.append(("job", "proceeds"))
    t0 = time.monotonic()
    th = threading.Thread(target=chat, daemon=True)
    th.start()
    assert wait_until(lambda: tq.status_for("chat-1") and tq.status_for("chat-1")["state"] == "waiting")
    jt = threading.Thread(target=job_next_call, daemon=True)
    jt.start()
    th.join(5)
    jt.join(5)
    kinds = [e[0] for e in log]
    assert kinds == ["chat", "chat-done", "job"], log
    assert sup.queue.get("job").get("slot_lent") is None, "the slot came back with the job"


def test_a_voice_turn_blocks_background_work_but_an_open_call_does_not(world, monkeypatch):
    sup, _away = world
    rec = {"seat_is_local": True, "deferrable": False}
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [bg.KIND_VOICE_CALL])
    assert bg.block_reason(rec) == ""
    turn = tq.acquire("brain", "voice", "voice-1")
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [])
    assert bg.block_reason(rec) == bg.REASON_INTERACTIVE, "a spoken turn in the scheduler's queue counts"
    turn.release()
    assert bg.block_reason(rec) == ""
    deferrable = {"seat_is_local": True, "deferrable": True}
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [bg.KIND_VOICE_CALL])
    assert bg.block_reason(deferrable) == bg.REASON_IDLE, "an open call is not idle"


def test_calls_inside_a_queue_job_do_not_queue_behind_the_job(world):
    sup, _away = world
    with bg.background_job(kind="scheduled", key="k", label="a job", deferrable=False):
        bg.before_local_model_call("big")                  # admitted by the queue, once
        g = ls.admit("local_call.call", model="big")
        assert g.nested, "a second admission would wait for the slot the job itself holds"
        g.release()
        again = ls.admit("local_call.call", model="big")
        assert again.nested


def test_cancel_by_id_reaches_the_queues_own_jobs(world, monkeypatch):
    sup, _away = world
    sup.wire_spawn(_job("a"))
    sup.wire_spawn(_job("b"))
    from agent_friday.services import agent
    monkeypatch.setattr(agent, "_seat_supervisor", lambda: sup)
    assert agent.local_queue_cancel("b") is True
    assert [j["id"] for j in ls.queue_snapshot()["queued"]] == []
    assert agent.local_queue_cancel("nothing") is False


def test_run_now_is_the_queues(world):
    sup, away = world
    away[0] = 1.0
    sup.wire_spawn(_job("a"))
    sup.wire_spawn(_job("w", deferrable=True))
    assert ls.run_now("w") is True
    assert sup.queue.get("w").get("run_now") is True
    assert ls.run_now("missing") is False


def test_the_two_queue_tools_have_two_names():
    """queue_status is the owner-facing read-out (voice, ring 0); local_work is
    the text tool that can also take a waiting job out of the line."""
    from agent_friday.services import agent, queue_tools, voice_engine as ve
    from agent_friday.governance import action_gate
    assert [t["name"] for t in queue_tools.TOOLS] == ["local_work"]
    assert agent.CLAUDE_TOOL_HANDLERS["queue_status"] is agent._tool_queue_status
    assert agent.CLAUDE_TOOL_HANDLERS["local_work"] is queue_tools.handle_local_work
    names = ve._voice_tool_names()
    assert "queue_status" in names and "local_work" not in names
    for name in ("queue_status", "local_work"):
        assert action_gate.classify(name, {})[0] == action_gate.INTERNAL, name
    assert agent.TOOL_RINGS["queue_status"] == 0 and agent.TOOL_RINGS["local_work"] == 1


def test_the_voice_contract_still_fits():
    from agent_friday.services import voice_engine as ve
    c = ve.build_voice_tool_contract()
    assert "queue_status" in set(c["names"])
    assert c["fits"] and c["tokens"] <= 9700, c["tokens"]


def test_queue_status_speaks_what_the_chip_shows(world):
    from agent_friday.services import agent
    sup, _away = world
    assert agent._tool_queue_status({}) == "Nothing is waiting for the local AI."
    sup.wire_spawn(_job("a", label="the first"))
    sup.wire_spawn(_job("b", label="the second"))
    said = agent._tool_queue_status({})
    assert "the first" in said and "the second" in said


def test_an_unlabelled_background_call_while_you_are_here_waits_and_shows_in_the_queue(world, monkeypatch):
    """The owner's rule: background local-model work runs only from the queue and
    only when idle, even when the model is loaded."""
    sup, away = world
    monkeypatch.setattr(ls, "WRAP_BACKGROUND", [True])
    monkeypatch.setattr(bg, "idle_threshold_s", lambda: 600.0)
    away[0] = 5.0                                    # at the keyboard
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 5.0)
    ls.set_residency_probe(lambda m: True)           # the model is loaded
    got = []

    def call():
        with ls.admit("local_call.call", model="default", cls="background"):
            got.append("ran")
    th = threading.Thread(target=call, daemon=True)
    th.start()
    assert wait_until(lambda: ls.queue_snapshot()["count"] == 1)
    row = ls.queue_snapshot()["queued"][0]
    assert row["deferrable"] and row["why"] == "waits for idle" and got == []
    away[0] = 1e9                                    # the owner leaves
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 1e9)
    assert wait_until(lambda: got == ["ran"], timeout=5), "it did not start once idle"
    th.join(3)
    assert ls.queue_snapshot()["jobs"] == [], "the job ended with the call"


def test_time_bound_and_nested_calls_are_not_wrapped(world, monkeypatch):
    sup, away = world
    monkeypatch.setattr(ls, "WRAP_BACKGROUND", [True])
    away[0] = 5.0
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 5.0)
    g = ls.admit("local_call.call", model="default", cls="user_job")
    assert g.how == "admitted"
    g.release()
    with bg.background_job(kind="scheduled", key="k", label="a job", deferrable=False):
        bg.before_local_model_call("default")
        assert ls.admit("local_call.call", model="default", cls="background").nested
