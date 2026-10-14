"""Batch by model. A model that is loaded drains its jobs before anything asks
for a swap; a background job never forces a swap while a person waits; jobs on
the same unloaded model go together. Fake seats only: no model, no GPU."""
import threading
import time

import pytest
from turn_queue_helpers import fast, wait_until

from agent_friday.services import local_scheduler as ls
from agent_friday.services import turn_queue as tq
from agent_friday.services import work_queue as wq

SWAP_S = 36.0       # what a swap costs: the measured load, before the cold read

# (arrives at s, class, model, runs for s). The brain ("default") is pinned and
# always loaded; every other model is a swap when it is not the one loaded.
DAY = [
    (0, "background", "podcast-writer", 40),
    (5, "background", "kg-indexer", 20),
    (10, "background", "podcast-writer", 40),
    (12, "chat", "default", 15),
    (30, "background", "news-ranker", 25),
    (31, "background", "kg-indexer", 20),
    (35, "background", "podcast-writer", 40),
    (60, "user_job", "podcast-writer", 40),
    (70, "background", "default", 10),
    (75, "background", "news-ranker", 25),
    (80, "chat", "default", 20),
    (90, "background", "kg-indexer", 20),
    (95, "background", "default", 10),
    (120, "voice", "default", 8),
    (125, "background", "podcast-writer", 40),
    (130, "background", "news-ranker", 25),
    (140, "background", "kg-indexer", 20),
]


def run_day(policy):
    """One runner works through DAY; returns what the day cost."""
    pending = [ls.Job("j%d" % i, c, m, t) for i, (t, c, m, _r) in enumerate(DAY)]
    runs = {"j%d" % i: r for i, (_t, _c, _m, r) in enumerate(DAY)}
    now, loaded, sticky = 0.0, None, None
    swaps = forced_while_waiting = 0
    person_wait = []
    order = []
    while pending:
        waiting = [j for j in pending if j.enq <= now]
        if not waiting:
            now = min(j.enq for j in pending)
            continue

        def resident(m, loaded=loaded):
            return m == "default" or m == loaded
        if policy == "fifo":
            job = sorted(waiting, key=lambda j: j.enq)[0]
        else:
            job = ls.Planner.choose(waiting, resident, sticky)
        if not resident(job.model):
            swaps += 1
            if any(w.cls in ls.INTERACTIVE for w in waiting if w is not job):
                forced_while_waiting += 1
            now += SWAP_S
            loaded = sticky = job.model
        if job.cls in ls.INTERACTIVE:
            person_wait.append(now - job.enq)
        order.append(job.id)
        now += runs[job.id]
        pending.remove(job)
    return {"swaps": swaps, "forced_while_waiting": forced_while_waiting,
            "finished_at": now, "person_wait": person_wait, "order": order}


def test_batching_cuts_model_swaps_on_a_scripted_day():
    fifo = run_day("fifo")
    batched = run_day("batched")
    print("scripted day: swaps FIFO=%d batched=%d; finished at %.0f s vs %.0f s"
          % (fifo["swaps"], batched["swaps"], fifo["finished_at"], batched["finished_at"]))
    assert batched["swaps"] < fifo["swaps"]
    assert (fifo["swaps"], batched["swaps"]) == EXPECTED_SWAPS
    assert batched["finished_at"] < fifo["finished_at"], "fewer swaps must also finish the day sooner"


def test_a_swap_never_starts_while_a_person_waits():
    batched = run_day("batched")
    assert batched["forced_while_waiting"] == 0
    # FIFO has no such rule, and the script is built so that it breaks it.
    assert run_day("fifo")["forced_while_waiting"] > 0


def test_a_person_never_waits_longer_than_the_job_that_is_running():
    """The queue does not pre-empt, and it does not make a person wait behind a swap."""
    longest = max(r for _t, _c, _m, r in DAY)
    assert max(run_day("batched")["person_wait"]) <= longest + SWAP_S


EXPECTED_SWAPS = (11, 3)         # FIFO, batched: the numbers the report quotes


# ── the live gate, with fake seats ───────────────────────────────────────────
class FakeSeats:
    """A card that holds the brain plus one swapped-in model."""

    def __init__(self):
        self.loaded = None
        self.swaps = 0
        self.lock = threading.Lock()

    def is_loaded(self, model):
        return model in ("", "default") or model == self.loaded

    def use(self, model):
        with self.lock:
            if model not in ("", "default") and model != self.loaded:
                self.swaps += 1
                self.loaded = model


@pytest.fixture
def seats(monkeypatch, tmp_path):
    fast(monkeypatch)
    ls.reset_for_tests()
    tq.HELD.set(())                                 # no admission leaks in from another test
    tq.CURRENT.set(None)
    monkeypatch.setattr(ls, "metrics_path", lambda: tmp_path / "metrics.jsonl")
    fake = FakeSeats()
    ls.set_residency_probe(fake.is_loaded)
    ls.set_ram_probe(lambda: 64.0)
    monkeypatch.setattr(ls, "idle_window_s", lambda: 60.0)
    yield fake
    ls.reset_for_tests()
    tq.reset_for_tests()


def _owner_here():
    ls.mark_activity("chat")


def _owner_away():
    ls.mark_activity("chat", when=time.time() - 3600)


def _job(order, seats_, model, cls="background", hold=0.01, **kw):
    def run():
        with ls.admit("test.job", model=model, cls=cls, **kw):
            seats_.use(model)
            order.append(model)
            time.sleep(hold)
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def test_jobs_for_one_unloaded_model_go_together(seats):
    """Five jobs for two models, queued while you are here. When you leave, the
    model with more jobs goes first and each model loads once: two swaps. In
    arrival order it would have been four."""
    _owner_here()
    order, threads = [], []
    for model in ("big", "vision", "big", "vision", "vision"):
        threads.append(_job(order, seats, model))
        time.sleep(0.03)
    assert wait_until(lambda: len(tq.all_waiting()) == 5)
    assert all("idle" in t.blocked for t in tq.all_waiting()), "they wait for you to be away"
    assert seats.swaps == 0 and order == []
    _owner_away()
    for th in threads:
        th.join(10)
    assert sorted(order) == ["big", "big", "vision", "vision", "vision"]
    assert order == ["vision"] * 3 + ["big"] * 2, order
    assert seats.swaps == 2


def test_the_loaded_models_jobs_drain_before_a_swap(seats):
    """Jobs on the model that is already loaded go first, and a job for another
    one waits for them."""
    _owner_away()
    seats.loaded = "big"
    order, threads = [], []
    for _ in range(3):
        threads.append(_job(order, seats, "big", hold=0.15))
    time.sleep(0.08)
    threads.append(_job(order, seats, "vision"))
    assert wait_until(lambda: any("already loaded" in t.blocked for t in tq.all_waiting()))
    for th in threads:
        th.join(10)
    assert order == ["big", "big", "big", "vision"], order
    assert seats.swaps == 1


def test_a_background_job_never_forces_a_swap_while_a_chat_waits(seats):
    _owner_away()
    chat = tq.acquire("default", "chat", "chat-1")
    waiting_chat = []

    def second_chat():
        adm = tq.acquire("default", "chat", "chat-2")
        waiting_chat.append(adm)
    th = threading.Thread(target=second_chat, daemon=True)
    th.start()
    assert wait_until(lambda: tq.status_for("chat-2") and tq.status_for("chat-2")["state"] == "waiting")
    order = []
    job = _job(order, seats, "big")
    assert wait_until(lambda: any(t.kind == "test.job" and t.blocked for t in tq.all_waiting()))
    why = [t.blocked for t in tq.all_waiting() if t.kind == "test.job"][0]
    assert "chat" in why
    assert seats.swaps == 0 and order == []
    chat.release()
    assert wait_until(lambda: bool(waiting_chat))
    assert order == [], "the second chat is still ahead of the swap"
    waiting_chat[0].release()
    _owner_away()               # releasing a chat restarts the idle clock
    job.join(10)
    assert order == ["big"] and seats.swaps == 1


def test_a_job_you_asked_for_is_not_held_for_an_idle_window(seats):
    _owner_here()
    order = []
    th = _job(order, seats, "big", cls="user_job")
    th.join(5)
    assert order == ["big"]


def test_background_work_waits_for_you_to_be_away_even_on_a_loaded_model(seats):
    """The owner's rule (background_idle_minutes): background local-model work
    starts only once you have been away, loaded model or not."""
    _owner_here()
    order = []
    th = _job(order, seats, "default")
    assert wait_until(lambda: any("idle" in t.blocked for t in tq.all_waiting()))
    assert order == []
    _owner_away()
    th.join(5)
    assert order == ["default"]
    assert seats.swaps == 0


def test_time_bound_background_work_does_not_wait_for_idle(seats):
    """A schedule that is due, an episode someone asked for: marked time-bound."""
    _owner_here()
    order = []

    def run():
        with ls.work("a schedule", cls="background", deferrable=False):
            with ls.admit("test.job", model="default"):
                order.append("default")
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(5)
    assert order == ["default"]


def test_a_voice_turn_anywhere_holds_background_work_and_an_open_call_does_not(seats, monkeypatch):
    from agent_friday.services import background_gate as bg
    _owner_away()
    order = []
    bg._INTERACTIVE.clear()
    bg.interactive_begin("voice-turn:1", "voice")
    try:
        th = _job(order, seats, "default")
        assert wait_until(lambda: any("chat" in t.blocked for t in tq.all_waiting()))
        assert order == []
    finally:
        bg.interactive_end("voice-turn:1")
    _owner_away()                    # a turn's end is the owner being here: the idle clock starts again
    th.join(5)
    assert order == ["default"]
    # An open call is the owner being here: it keeps deferrable work waiting for
    # idle, and holds nothing that is time-bound.
    bg.interactive_begin("voice-session:1", bg.KIND_VOICE_CALL)
    try:
        def timed():
            with ls.work("a schedule", cls="background", deferrable=False):
                with ls.admit("test.job", model="default"):
                    order.append("time-bound")
        t2 = threading.Thread(target=timed, daemon=True)
        t2.start()
        t2.join(5)
        assert order[-1] == "time-bound"
        th2 = _job(order, seats, "default")
        assert wait_until(lambda: any("idle" in t.blocked for t in tq.all_waiting()))
    finally:
        bg.interactive_end("voice-session:1")
    _owner_away()
    th2.join(5)
    bg._INTERACTIVE.clear()


def test_the_idle_window_runs_off_real_activity(seats, monkeypatch):
    """Not the time since the process started: the time since you were last here."""
    monkeypatch.setattr(ls, "idle_window_s", lambda: 0.4)
    wq._LAST_ACTIVITY[0] = time.time() - 3600          # nothing for an hour...
    ls.mark_activity("voice")                          # ...then a voice turn
    t0 = time.monotonic()
    order = []
    th = _job(order, seats, "big")
    assert wait_until(lambda: any(t.blocked for t in tq.all_waiting()))
    assert "idle" in [t.blocked for t in tq.all_waiting()][0]
    th.join(10)
    assert order == ["big"]
    assert time.monotonic() - t0 >= 0.3, "it ran before the idle window had passed"


def test_memory_floor_blocks_a_load_and_says_so(seats):
    _owner_away()
    free = [3.0]
    ls.set_ram_probe(lambda: free[0])
    order = []
    th = _job(order, seats, "big")
    assert wait_until(lambda: any("memory" in t.blocked for t in tq.all_waiting()))
    why = [t.blocked for t in tq.all_waiting()][0]
    assert "3.0 GB free" in why and "4.0 GB" in why
    assert order == []
    assert "waiting for memory" in ls.status()["chip"]
    # A model that is already loaded is not a load: it runs at 3 GB.
    ok = []
    _job(ok, seats, "default").join(5)
    assert ok == ["default"]
    free[0] = 12.0
    th.join(10)
    assert order == ["big"]


def test_the_memory_floor_counts_what_the_load_needs(seats, monkeypatch):
    monkeypatch.setattr(ls, "model_need_gb", lambda m: 20.0 if m == "big" else 1.0)
    _owner_away()
    ls.set_ram_probe(lambda: 22.0)                 # 22 - 20 < 4: too tight for big, fine for small
    assert "waiting for memory" in ls.memory_block(20.0)
    assert ls.memory_block(1.0) == ""


def test_a_stuck_wait_goes_ahead_and_is_counted(seats):
    """A call that waits behind a slot its own caller holds must not hang for ever."""
    hold = tq.acquire("default", "chat", "chat-1")
    box = []

    def stuck():                      # another thread: it is not inside the chat turn
        t0 = time.monotonic()
        g = ls.admit("test.stuck", model="default", cls="user_job", timeout_s=0.3)
        box.append((g.how, time.monotonic() - t0))
        g.release()
    th = threading.Thread(target=stuck, daemon=True)
    th.start()
    th.join(5)
    assert box and box[0][0] == "failopen" and box[0][1] < 3
    hold.release()
    assert ls.summarize(ls.read_events())["unadmitted_calls"] == 1
