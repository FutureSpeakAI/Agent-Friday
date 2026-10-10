"""The Local AI queue: one door for background local-model work.

Exercises seat_queue + seat_supervisor with the production gate
(services/background_gate.block_reason) over injected signals: a fake idle
clock, a fake interactive registry entry and a fake seat. No model, no
threads beyond the ones a test starts itself.

Owner's report this guards: four background jobs hit the local model at once
(voice-session wiki distillation among them) and slowed the whole machine.
"""
import threading
import time

import pytest

from agent_friday.services import background_gate as bg
from agent_friday.services import seat_queue as sq
from agent_friday.services import seat_supervisor as ss


class Signals:
    """Injected idle and interactive signals."""

    def __init__(self):
        self.os_idle = 0.0          # seconds since last input (None: unreadable)
        self.friday_idle = 0.0

    def install(self, monkeypatch):
        monkeypatch.setattr(bg, "OS_IDLE_READER", lambda: self.os_idle)
        monkeypatch.setattr(bg, "friday_idle_seconds", lambda: self.friday_idle)
        monkeypatch.setattr(bg, "idle_threshold_s", lambda: 600.0)
        monkeypatch.setattr(bg, "_touch_friday_activity", lambda: None)
        bg._INTERACTIVE.clear()

    def away(self, s=3600.0):
        self.os_idle = self.friday_idle = s

    def back(self):
        self.os_idle = self.friday_idle = 0.0


@pytest.fixture
def sig(monkeypatch):
    s = Signals()
    s.install(monkeypatch)
    yield s
    bg._INTERACTIVE.clear()


@pytest.fixture
def sup(monkeypatch, sig):
    started = []
    s = ss.SeatSupervisor(thread_factory=lambda rec: started.append(rec["id"]),
                          gate=bg.block_reason, sleep=lambda _s: time.sleep(0.005),
                          poll_seconds=0.005, brain_resolver=lambda: "bonsai2:27b")
    s.started = started
    monkeypatch.setattr(bg, "_SUPERVISOR_PROVIDER", lambda: s)
    return s


def _job(i, *, key=None, deferrable=False, seat="local/bonsai2:27b", kind="wiki_distill"):
    return {"id": i, "seat": seat, "kind": kind, "job_key": key,
            "label": "wiki notes from today's voice chat", "deferrable": deferrable}


def _distill_request(sup, i, session, transcript):
    """What _spawn_task does for one voice-distill trigger: merge into a
    queued job with the same key, else admit a new one."""
    key = "distill:voice:" + session
    existing = sup.find_queued_job(key, "local/bonsai2:27b")
    if existing:
        sup.merge(existing, prompt=transcript, job_digest=transcript)
        return existing
    if sup.already_ran(key, transcript):
        return None
    rec = _job(i, key=key, deferrable=True)
    rec.update(prompt=transcript, job_digest=transcript)
    sup.wire_spawn(rec)
    return i


# ── dedupe and the idle gate ─────────────────────────────────────────────────

def test_four_simultaneous_distill_triggers_for_one_session_make_one_job(sup, sig):
    ids = {_distill_request(sup, "t%d" % n, "s1", "transcript v%d" % n) for n in range(4)}
    assert ids == {"t0"}, "the triggers did not collapse into one job"
    snap = sup.snapshot()
    assert snap["count"] == 1 and sup.started == []
    assert snap["queued"][0]["why"] == bg.REASON_IDLE
    assert sup._records["t0"]["prompt"] == "transcript v3", "latest payload wins"
    # Starts only once the computer is idle.
    sup.pump()
    assert sup.started == []
    sig.away()
    sup.pump()
    assert sup.started == ["t0"]


def test_four_sessions_run_one_at_a_time(sup, sig):
    sig.away()
    for n in range(4):
        _distill_request(sup, "t%d" % n, "s%d" % n, "x%d" % n)
    assert sup.started == ["t0"]
    assert sup.snapshot()["count"] == 3
    for n in range(4):
        assert sup.started == ["t%d" % k for k in range(n + 1)]
        sup.on_task_end("t%d" % n)
    assert sup.snapshot()["count"] == 0


def test_the_same_content_does_not_run_twice(sup, sig):
    sig.away()
    _distill_request(sup, "a", "s1", "same")
    sup.on_task_end("a")
    assert _distill_request(sup, "b", "s1", "same") is None
    assert _distill_request(sup, "c", "s1", "longer now") == "c"


def test_waits_while_active_starts_when_idle_and_yields_on_return(sup, sig):
    sup.wire_spawn(_job("j", deferrable=True))
    assert sup.started == []
    sig.away()
    sup.pump()
    assert sup.started == ["j"]
    # The user returns: the running job stops before its next model call.
    sig.back()
    released = threading.Event()

    def call():
        with bg.admitted_job("j"):
            bg.before_local_model_call("bonsai2:27b")
        released.set()
    th = threading.Thread(target=call, daemon=True)
    th.start()
    assert not released.wait(0.15), "a deferrable job kept going while the user was back"
    row = sup.snapshot()["running"][0]
    assert row["state"] == "paused" and row["why"] == bg.REASON_IDLE
    sig.away()
    assert released.wait(2.0)
    assert sup.snapshot()["running"][0]["state"] == "running"


def test_unreadable_os_idle_waits_until_the_drain_cap(sup, sig):
    sig.os_idle = None
    sig.friday_idle = 5 * 3600.0
    sup.wire_spawn(_job("j", deferrable=True))
    sup.pump()
    assert sup.started == [], "unknown idle time must count as not idle"
    sig.friday_idle = bg.DRAIN_CAP_S + 1
    sup.pump()
    assert sup.started == ["j"]


def test_run_now_bypasses_idle_only(sup, sig):
    sup.wire_spawn(_job("a", deferrable=True))
    sup.wire_spawn(_job("b", deferrable=True))
    bg.interactive_begin("v", "voice")
    assert sup.run_now("b")
    assert sup.started == [], "run now jumped an interactive turn"
    bg.interactive_end("v")
    sup.pump()
    assert sup.started == ["b"], "run now did not skip the idle wait"
    assert sup.snapshot()["queued"][0]["why"] == sq.SEAT_BUSY_REASON
    sup.on_task_end("b")
    assert sup.started == ["b"], "run now released the idle wait for another job"


def test_cancel_removes_a_queued_job(sup, sig):
    sup.wire_spawn(_job("a", deferrable=True))
    assert sup.cancel("a")
    sig.away()
    sup.pump()
    assert sup.started == [] and sup.snapshot()["count"] == 0


# ── interactive turns win ────────────────────────────────────────────────────

def test_a_job_never_starts_during_an_interactive_turn(sup, sig):
    sig.away()
    bg.interactive_begin("voice-session:x", "voice")
    sup.wire_spawn(_job("t", kind="scheduled"))       # time-bound: no idle wait
    sup.pump()
    assert sup.started == []
    assert sup.snapshot()["queued"][0]["why"] == bg.REASON_INTERACTIVE
    bg.interactive_end("voice-session:x")
    sup.pump()
    assert sup.started == ["t"]


def test_a_chat_turn_in_the_core_registry_blocks_start(sup, sig):
    from agent_friday import core
    sig.away()
    stop = threading.Event()

    def turn():
        core.turn_begin("turn-1")
        stop.wait(5)
        core.turn_end("turn-1")
    th = threading.Thread(target=turn, daemon=True)
    th.start()
    time.sleep(0.05)
    try:
        sup.wire_spawn(_job("t", kind="scheduled"))
        assert sup.started == []
        # ...but never on the turn that spawned it (it may be waiting on it).
        sup.wire_spawn(dict(_job("child", seat="local/other", kind="task"),
                            parent_turn="turn-1"))
        assert sup.started == ["child"]
    finally:
        stop.set()
        th.join(2)
    sup.pump()
    assert "t" in sup.started


def test_a_running_job_pauses_before_its_next_call_and_the_turn_does_not_wait(sup, sig):
    """Fake seat: one request at a time, 50 ms each. The turn's call is never
    gated; the job's next call waits until the turn ends."""
    seat_lock = threading.Lock()
    log = []

    def seat_call(who):
        bg.before_local_model_call("bonsai2:27b")
        with seat_lock:
            log.append(("start", who, time.monotonic()))
            time.sleep(0.05)
            log.append(("end", who, time.monotonic()))

    def baseline():
        t0 = time.monotonic()
        seat_call("turn")
        return time.monotonic() - t0
    base = min(baseline() for _ in range(3))

    sig.away()
    sup.wire_spawn(_job("job", deferrable=False, kind="scheduled"))
    assert sup.started == ["job"]
    turn_on = threading.Event()
    job_done = threading.Event()

    def job():
        with bg.admitted_job("job"):
            for _ in range(5):
                seat_call("job")
                turn_on.wait(0.01)
        job_done.set()
    jt = threading.Thread(target=job, daemon=True)
    jt.start()
    time.sleep(0.03)
    bg.interactive_begin("turn", "chat")
    turn_on.set()
    t0 = time.monotonic()
    seat_call("turn")                      # an interactive call: never gated
    took = time.monotonic() - t0
    time.sleep(0.12)
    starts_during = [e for e in log if e[0] == "start" and e[1] == "job"
                     and e[2] > t0 and e[2] < time.monotonic()]
    assert sup.snapshot()["running"][0]["why"] == bg.REASON_INTERACTIVE
    bg.interactive_end("turn")
    assert job_done.wait(3)
    # At most the one job call already in flight delays the turn.
    assert took <= base + 0.05 + 0.04, (took, base)
    assert starts_during == [], "the job started a new call during the turn"


# ── inline jobs, cloud seats, seats ──────────────────────────────────────────

def test_inline_work_is_admitted_at_its_first_local_call(sup, sig):
    sig.away()
    sup.wire_spawn(_job("busy", kind="task"))         # seat taken
    got = threading.Event()

    def builtin():
        with bg.background_job(kind="scheduled", key="schedule:x",
                               label="Afternoon briefing", deferrable=False):
            bg.before_local_model_call("bonsai2:27b")
            got.set()
    th = threading.Thread(target=builtin, daemon=True)
    th.start()
    assert not got.wait(0.1), "inline work overlapped the running job"
    assert [r["label"] for r in sup.snapshot()["queued"]] == ["Afternoon briefing"]
    sup.on_task_end("busy")
    assert got.wait(2)
    th.join(2)
    assert sup.snapshot()["count"] == 0 and sup.snapshot()["running"] == []


def test_inline_work_that_never_reaches_a_local_model_never_queues(sup, sig):
    with bg.background_job(kind="scheduled", key="schedule:y",
                           label="Repo sync", deferrable=True):
        pass
    assert sup.snapshot()["jobs"] == []


def test_cloud_seats_are_never_queued(sup, sig):
    bg.interactive_begin("v", "voice")
    sup.wire_spawn(_job("c", seat="cloud/claude", deferrable=True))
    assert sup.started == ["c"]
    assert sup.snapshot()["jobs"] == []


def test_local_default_and_the_brain_id_are_one_seat(sup, sig):
    sig.away()
    sup.wire_spawn(_job("a", seat="local/default", kind="task"))
    sup.wire_spawn(_job("b", seat="local/bonsai2:27b", kind="task"))
    assert sup.started == ["a"]


def test_nonblocking_admission_skips_a_busy_seat(sup, sig):
    sig.away()
    sup.wire_spawn(_job("a", kind="task"))
    assert sup.admit_inline({"kind": "prefix_warm", "seat": "local/bonsai2:27b",
                             "seat_is_local": True}, wait=False) is None
    assert sup.snapshot()["count"] == 0


def test_a_queue_with_no_gate_is_plain_fifo():
    q = sq.SeatQueue()
    for i in "abc":
        q.submit({"id": i, "seat": "local/x", "seat_is_local": True})
    q.complete("a")
    assert q.get("b")["status"] == "running" and q.get("c")["status"] == sq.QUEUED


def test_labels_are_plain_words_never_content(sup, sig):
    sup.wire_spawn({"id": "t", "seat": "local/bonsai2:27b", "kind": "task",
                    "prompt": "SECRET transcript text", "deferrable": True})
    row = sup.snapshot()["queued"][0]
    assert row["label"] == "a background task"
    assert "SECRET" not in repr(sup.snapshot())


# ── the idle signal ──────────────────────────────────────────────────────────

def test_idle_is_the_minimum_of_the_signals(sig):
    sig.os_idle, sig.friday_idle = 3600.0, 30.0
    st = bg.idle_state()
    assert st["idle_s"] == 30.0 and not st["idle"]
    sig.os_idle, sig.friday_idle = 30.0, 3600.0
    assert bg.idle_state()["idle_s"] == 30.0


def test_the_os_reader_returns_seconds_or_none():
    v = bg._read_os_idle_s()
    assert v is None or v >= 0.0


def test_the_idle_setting_is_clamped_with_a_default():
    assert bg.idle_minutes_setting({}) == bg.DEFAULT_IDLE_MINUTES == 10
    assert bg.idle_minutes_setting({"background_idle_minutes": 30}) == 30
    assert bg.idle_minutes_setting({"background_idle_minutes": 0}) == bg.MIN_IDLE_MINUTES
    assert bg.idle_minutes_setting({"background_idle_minutes": "x"}) == 10
