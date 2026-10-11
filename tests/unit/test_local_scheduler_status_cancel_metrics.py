"""Honest status, cancel by id and by voice, and the numbers the scheduler keeps
(swaps per hour, the lowest free RAM per hour, queue wait per class, background
throughput). Fake seats, a temporary metrics file, no model."""
import json
import threading
import time

import pytest
from turn_queue_helpers import fast, wait_until

from agent_friday.services import local_scheduler as ls
from agent_friday.services import queue_tools
from agent_friday.services import turn_queue as tq
from agent_friday.services import work_queue as wq


@pytest.fixture(autouse=True)
def scheduler(monkeypatch, tmp_path):
    fast(monkeypatch)
    ls.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)
    monkeypatch.setattr(ls, "metrics_path", lambda: tmp_path / "scheduler" / "metrics.jsonl")
    # "the big brain" is the one model that is not loaded; the pinned brain always is.
    ls.set_residency_probe(lambda m: m in ("", "default"))
    ls.set_ram_probe(lambda: 64.0)
    monkeypatch.setattr(ls, "idle_window_s", lambda: 120.0)
    yield
    ls.reset_for_tests()
    tq.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)


def _queue_a_podcast(threads, outcome):
    """A podcast writer waits for the big brain while you are here."""
    ls.mark_activity("chat")

    def job():
        try:
            with ls.work("podcast", cls="background", needs="the big brain", quiet=False):
                with ls.admit("local_call.call", model="big-brain"):
                    outcome.append("ran")
        except ls.LocalWorkCancelled:
            outcome.append("cancelled")
    th = threading.Thread(target=job, daemon=True)
    th.start()
    threads.append(th)
    assert wait_until(lambda: any(t.blocked for t in tq.all_waiting()))


def test_the_chip_says_what_waits_and_why():
    threads, outcome = [], []
    _queue_a_podcast(threads, outcome)
    st = ls.status()
    assert st["chip"] == ("1 job waiting · next: podcast "
                          "(needs the big brain; waiting for you to be idle)")
    assert st["job_count"] == 1
    assert "podcast" in st["speech"] and "idle" in st["speech"]
    # Three waiting: the count moves, the next job stays the one that goes first.
    for _ in range(2):
        _queue_a_podcast(threads, outcome)
    assert wait_until(lambda: len(tq.all_waiting()) == 3)
    assert ls.status()["chip"].startswith("3 jobs waiting · next: podcast (needs the big brain;")
    ls.mark_activity("chat", when=time.time() - 3600)
    for th in threads:
        th.join(10)
    assert outcome == ["ran"] * 3
    assert ls.status()["chip"] == ""
    assert "free" in ls.status()["speech"]


def test_a_chat_that_waits_is_not_counted_as_a_job():
    holder = tq.acquire("default", "chat", "chat-1")
    box = []
    th = threading.Thread(target=lambda: box.append(tq.acquire("default", "chat", "chat-2")), daemon=True)
    th.start()
    assert wait_until(lambda: tq.status_for("chat-2") and tq.status_for("chat-2")["state"] == "waiting")
    st = ls.status()
    assert st["job_count"] == 0 and st["chip"] == ""
    assert any(j["interactive"] for j in st["waiting"])
    holder.release()
    th.join(5)
    box[0].release()


def test_cancel_takes_a_waiting_job_out_and_it_never_reaches_the_model():
    threads, outcome = [], []
    _queue_a_podcast(threads, outcome)
    job = [j for j in ls.jobs() if j["label"] == "podcast"][0]
    assert job["cancellable"] and job["state"] == "waiting"
    assert ls.cancel(job["id"])["ok"]
    threads[0].join(5)
    assert outcome == ["cancelled"]
    assert ls.cancel(job["id"])["ok"] is False, "it is gone"
    assert ls.status()["chip"] == ""


def test_cancel_by_voice_words_and_the_shared_text_tool():
    threads, outcome = [], []
    _queue_a_podcast(threads, outcome)
    said = queue_tools.handle_local_work({"action": "cancel", "job": "the podcast"})
    assert said.startswith("Nothing waiting matches"), "'the podcast' is not part of its name"
    said = queue_tools.handle_local_work({"action": "cancel", "job": "podcast"})
    assert "Cancelled podcast" in said and "never reached the model" in said
    threads[0].join(5)
    assert outcome == ["cancelled"]
    assert "Nothing waiting" in queue_tools.handle_local_work({"action": "cancel", "job": "podcast"})


def test_cancel_with_two_waiting_asks_which_one():
    threads, outcome = [], []
    _queue_a_podcast(threads, outcome)

    def other():
        with ls.work("knowledge graph", cls="background", quiet=True):
            with ls.admit("local_call.call", model="big-brain"):
                outcome.append("kg ran")
    th = threading.Thread(target=other, daemon=True)
    th.start()
    threads.append(th)
    assert wait_until(lambda: len(tq.all_waiting()) == 2)
    said = queue_tools.handle_local_work({"action": "cancel"})
    assert said.startswith("Which one?") and "podcast" in said and "knowledge graph" in said
    queue_tools.handle_local_work({"action": "cancel", "job": "knowledge"})
    queue_tools.handle_local_work({"action": "cancel", "job": "podcast"})
    for t in threads:
        t.join(5)
    assert "ran" not in outcome and "kg ran" not in outcome


def test_status_by_voice_is_the_same_sentence_as_the_chip():
    threads, outcome = [], []
    _queue_a_podcast(threads, outcome)
    said = queue_tools.handle_local_work({})
    assert said == ls.status()["speech"]
    assert queue_tools.handle_local_work({"action": "status"}) == said
    ls.cancel_by_words("podcast")
    threads[0].join(5)


def test_the_tool_is_shared_text_and_not_declared_to_the_voice_front():
    from agent_friday.services import voice_engine
    names = {t["name"] for t in queue_tools.TOOLS}
    assert "local_work" in names and queue_tools.RINGS["local_work"] == 1
    assert "local_work" not in voice_engine._VOICE_SHARED_TOOLS, "the voice front's contract is full"
    from agent_friday.governance import action_gate
    with open(action_gate.__file__, encoding="utf-8") as fh:
        assert '"local_work"' in fh.read(), "the gate knows it as Friday's own queue"


def test_other_sources_show_up_and_cancel_through_their_own_hand():
    cancelled = []
    ls.register_source(
        "tasks",
        lambda: [{"id": "task:1", "source": "task", "state": "waiting", "class": "background",
                  "interactive": False, "label": "a research task", "kind": "task", "model": "",
                  "needs": "the local brain", "reason": "waiting for the local brain to be free",
                  "waited_s": 12.0, "cancellable": True}],
        lambda jid: cancelled.append(jid) or True)
    st = ls.status()
    assert st["chip"] == ("1 job waiting · next: a research task "
                          "(needs the local brain; waiting for the local brain to be free)")
    assert ls.cancel("task:1")["ok"] and cancelled == ["task:1"]


# ── metrics ──────────────────────────────────────────────────────────────────
def test_summary_computes_swaps_ram_waits_and_throughput():
    now = 1_000_000.0
    ev = [{"t": now - 100, "ev": "swap"}, {"t": now - 200, "ev": "swap"},
          {"t": now - 7300, "ev": "swap"}, {"t": now - 99999, "ev": "swap"},       # the last is out of range
          {"t": now - 50, "ev": "ram", "free_gb": 9.1}, {"t": now - 60, "ev": "ram", "free_gb": 6.4},
          {"t": now - 4000, "ev": "ram", "free_gb": 3.9},
          {"t": now - 10, "ev": "job_done", "cls": "background"},
          {"t": now - 20, "ev": "job_done", "cls": "background"},
          {"t": now - 30, "ev": "job_done", "cls": "user_job"},
          {"t": now - 5, "ev": "failopen"}]
    ev += [{"t": now - i, "ev": "wait", "cls": "chat", "seconds": s}
           for i, s in enumerate([0.0, 0.0, 0.0, 0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 20.0], start=1)]
    s = ls.summarize(ev, hours=24, now=now)
    assert s["swaps"] == 3 and s["swaps_per_hour"] == 0.12
    assert s["min_free_ram_gb"] == 3.9 and len(s["min_free_ram_gb_per_hour"]) == 2
    assert s["background_jobs_done"] == 2 and s["background_jobs_per_day"] == 2.0
    assert s["unadmitted_calls"] == 1
    assert s["wait_s"]["chat"] == {"n": 10, "p50": 2.0, "p95": 20.0}
    assert ls.summarize([], hours=24, now=now)["min_free_ram_gb"] is None


def test_the_scheduler_writes_its_own_events_to_a_local_file_and_reads_them_back():
    ls.mark_activity("chat", when=time.time() - 3600)
    with ls.admit("t.a", model="big-brain", cls="background"):
        pass
    with ls.admit("t.b", model="default", cls="chat"):
        pass
    ls._LAST_RAM_SAMPLE[0] = 0.0          # the admissions above already took this minute's sample
    ls.sample_ram(5.5)
    lines = [json.loads(x) for x in ls.metrics_path().read_text(encoding="utf-8").splitlines()]
    kinds = [x["ev"] for x in lines]
    assert kinds.count("swap") == 1 and kinds.count("wait") == 2 and "job_done" in kinds and "ram" in kinds
    s = ls.metrics_summary(1)
    assert s["swaps"] == 1 and s["background_jobs_done"] == 1
    assert set(s["wait_s"]) == {"background", "chat"}
    assert s["min_free_ram_gb"] == 5.5


def test_the_metrics_file_is_capped(monkeypatch):
    monkeypatch.setattr(ls, "METRICS_MAX_BYTES", 4000)
    for i in range(400):
        ls.emit("wait", cls="chat", seconds=0.1, n=i)
    size = ls.metrics_path().stat().st_size
    assert size < 8000
    assert json.loads(ls.metrics_path().read_text(encoding="utf-8").splitlines()[-1])["n"] == 399


def test_metrics_failures_never_raise(monkeypatch):
    monkeypatch.setattr(ls, "metrics_path", lambda: (_ for _ in ()).throw(OSError("disk")))
    ls.emit("swap", model="x")          # does not raise
    assert ls.read_events() == []


def test_work_queue_idle_clock_is_fed_by_the_marks():
    wq._LAST_ACTIVITY[0] = time.time() - 5000
    assert wq.idle_seconds() > 4000
    ls.mark_activity("ui")
    assert wq.idle_seconds() < 5
    assert "ui" in ls.last_activity()


def test_a_podcast_waiting_for_its_turn_is_listed_once_and_can_be_taken_out(monkeypatch):
    """An episode's writing is a job of the Local AI queue (podcast_engine.queue_job),
    so that stage is the queue's own row; the scheduler lists the episode only
    while it waits for stand-down or the GPU, and while it speaks."""
    from agent_friday.services import podcast_engine as pe
    cancelled = []
    eps = [{"id": "e1", "status": "queued", "created_at": time.time() - 30},
           {"id": "e2", "status": "speaking", "created_at": time.time() - 90},
           {"id": "e3", "status": "writing", "created_at": time.time() - 60}]
    monkeypatch.setattr(pe, "pending", lambda: list(eps))
    monkeypatch.setattr(pe, "_gate_reason", lambda e: "the GPU is held by other work")
    monkeypatch.setattr(pe, "cancel", lambda eid: cancelled.append(eid) or {"id": eid})
    monkeypatch.setattr(ls, "_supervisor_snapshot", lambda: {"running": [{"id": "x", "kind": pe.QUEUE_KIND}]})
    monkeypatch.setattr(ls, "_podcast_seat", lambda: "big-brain")
    ls._PODCAST_MEMO[0], ls._PODCAST_MEMO[1] = 0.0, []
    rows = {j["id"]: j for j in ls._podcast_jobs()}
    assert set(rows) == {"podcast:e1", "podcast:e2"}, "the writing stage is the queue's row, not listed twice"
    assert rows["podcast:e1"]["state"] == "waiting" and rows["podcast:e1"]["reason"] == "the GPU is held by other work"
    assert rows["podcast:e1"]["cancellable"] and not rows["podcast:e2"]["cancellable"]
    assert rows["podcast:e2"]["needs"] == "the voice"
    assert ls.cancel("podcast:e1")["ok"] and cancelled == ["e1"]


def test_local_work_is_an_on_demand_text_tool_outside_the_always_on_catalogue():
    from agent_friday.services import agent
    assert agent.CLAUDE_TOOL_HANDLERS["local_work"] is queue_tools.handle_local_work
    assert agent.TOOL_RINGS["local_work"] == 1
    assert "local_work" not in {t["name"] for t in agent.CLAUDE_TOOLS}, "not in the always-on catalogue"
    assert "local_work" in {t["name"] for t in agent.WORKSPACE_TOOLS["on_demand"]}
    from agent_friday.governance import action_gate
    assert action_gate.classify("local_work", {})[0] == action_gate.INTERNAL


def test_the_voice_contract_does_not_grow():
    from agent_friday.services import voice_engine
    contract = voice_engine.build_voice_tool_contract()
    assert "local_work" not in contract["names"]
    assert contract["fits"] and contract["tokens"] <= 9700, contract["tokens"]
