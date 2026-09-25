"""A long turn is not a dead turn.

The chat UI must not release the composer on a clock ("That turn has been
running for over fifteen minutes with no reply..."). A healthy turn can run
25 tool calls over five minutes and longer runs are routine, so a fixed
timeout calls live work dead.

The timeout cannot simply be deleted: it exists for the silent hang, where the
process stays alive and friday.log goes completely dark. That is a genuinely
different condition and it can arrive in ninety seconds.

So the server answers the question instead. These tests pin the distinction
that a stopwatch cannot make:

  * a turn whose worker thread is alive is ``working`` — never released,
  * one that is alive but has reported nothing is ``quiet`` — still not
    released, just described,
  * one whose thread is gone is ``gone`` — the only honest release,
  * and the hang watchdog's own verdict is carried through rather than a
    second threshold being invented next to it.
"""

import threading
import time

import pytest

import agent_friday.core as core
from agent_friday.services import hang_watchdog as hw


@pytest.fixture(autouse=True)
def _clean_turns():
    with core._TURNS_LOCK:
        saved = dict(core._TURNS)
        core._TURNS.clear()
    yield
    with core._TURNS_LOCK:
        core._TURNS.clear()
        core._TURNS.update(saved)


def _in_thread(fn):
    """Run fn on a worker thread and hand back (thread, release, done)."""
    release = threading.Event()
    ready = threading.Event()

    def _run():
        fn()
        ready.set()
        release.wait(5)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    ready.wait(5)
    return t, release


# ── the three states ─────────────────────────────────────────────────────────

def test_a_turn_whose_worker_is_alive_is_working():
    """The property the whole change rests on: alive means keep the chat."""
    t, release = _in_thread(lambda: core.turn_begin("t-alive", "conv-1"))
    try:
        v = core.turn_liveness("t-alive")
        assert v["state"] == "working" and v["alive"] is True
        assert v["conversation_id"] == "conv-1"
    finally:
        release.set()
        t.join(5)


def test_elapsed_time_alone_never_changes_the_verdict():
    """The exact defect: fifteen minutes of healthy work read as failure.
    A turn that started an hour ago and is still running is still `working`."""
    t, release = _in_thread(lambda: core.turn_begin("t-old"))
    try:
        with core._TURNS_LOCK:
            core._TURNS["t-old"]["started"] -= 3600
            core._TURNS["t-old"]["last_progress"] = time.time()
        v = core.turn_liveness("t-old")
        assert v["state"] == "working"
        assert v["elapsed_s"] > 3500       # an hour in, and still working
    finally:
        release.set()
        t.join(5)


def test_a_quiet_turn_is_described_not_released():
    """One model call can legitimately run minutes with nothing to report.
    `quiet` is a fact the UI may show; it is never a verdict."""
    t, release = _in_thread(lambda: core.turn_begin("t-quiet"))
    try:
        with core._TURNS_LOCK:
            core._TURNS["t-quiet"]["last_progress"] = (
                time.time() - core.TURN_QUIET_AFTER_S - 30)
        v = core.turn_liveness("t-quiet")
        assert v["state"] == "quiet"
        assert v["alive"] is True, "quiet must still be alive, or the UI lets go"
        assert v["quiet_for_s"] > core.TURN_QUIET_AFTER_S
    finally:
        release.set()
        t.join(5)


def test_a_turn_whose_worker_died_is_gone():
    """The only honest release. The thread ended without answering."""
    t, release = _in_thread(lambda: core.turn_begin("t-dead"))
    release.set()
    t.join(5)
    assert core.turn_liveness("t-dead")["state"] == "gone"


def test_an_unknown_turn_is_gone_not_working():
    assert core.turn_liveness("never-started")["state"] == "gone"


# ── how progress gets recorded ───────────────────────────────────────────────

def test_every_orb_update_pets_the_turn_on_that_thread():
    """No call site knows this exists. The agent loops already call
    process_update every iteration and for every tool, and that is what keeps
    a long turn alive — which is why this works on BOTH loops without either
    of them being touched."""
    def _work():
        core.turn_begin("t-pet")
        with core._TURNS_LOCK:
            core._TURNS["t-pet"]["last_progress"] = time.time() - 600
        core.process_register("orb-pet", label="Reasoning…", model="m")
        core.process_update("orb-pet", label="search_web…",
                            step={"type": "tool", "name": "search_web"})

    t, release = _in_thread(_work)
    try:
        v = core.turn_liveness("t-pet")
        assert v["state"] == "working"
        assert v["quiet_for_s"] < 5, "an orb update did not count as progress"
        assert v["label"] == "search_web…"
        assert v["progress_events"] >= 2
    finally:
        release.set()
        t.join(5)
        core.process_remove("orb-pet")


def test_progress_on_another_thread_does_not_pet_this_turn():
    """A test that could pass for the wrong reason otherwise: the petting is
    thread-scoped, so a busy background task must not keep a dead chat turn
    looking alive."""
    t, release = _in_thread(lambda: core.turn_begin("t-scope"))
    try:
        with core._TURNS_LOCK:
            core._TURNS["t-scope"]["last_progress"] = time.time() - 600
        core.process_register("orb-other", label="someone else's work")
        core.process_update("orb-other", label="still going")
        assert core.turn_liveness("t-scope")["quiet_for_s"] > 500
    finally:
        release.set()
        t.join(5)
        core.process_remove("orb-other")


def test_ending_a_turn_releases_it():
    def _work():
        core.turn_begin("t-end")
        core.turn_end()

    t, release = _in_thread(_work)
    try:
        assert core.turn_liveness("t-end")["state"] == "gone"
    finally:
        release.set()
        t.join(5)


def test_the_registry_does_not_grow_without_bound():
    """A turn whose thread died mid-flight would otherwise sit here forever.
    Dead threads are evicted; live ones are not, because expiring a live turn
    on a clock is the bug this file is about."""
    t, release = _in_thread(lambda: core.turn_begin("t-live"))
    try:
        for i in range(80):
            d, r = _in_thread(lambda i=i: core.turn_begin(f"t-dead-{i}"))
            r.set()
            d.join(5)
        core.turn_begin("t-trigger")
        with core._TURNS_LOCK:
            n = len(core._TURNS)
        assert n < 80, "dead turns were never evicted"
        assert core.turn_liveness("t-live")["state"] == "working", \
            "a LIVE turn was evicted to make room"
    finally:
        release.set()
        t.join(5)


# ── the hang signal comes from the watchdog, not a second stopwatch ─────────

def test_the_watchdog_reports_a_stall_it_can_actually_see():
    wd = hw.HangWatchdog(stall_threshold_s=10)
    wd._started = True
    wd.pet()
    assert wd.status()["stalled"] is False
    wd._last_beat = time.time() - 60
    st = wd.status()
    assert st["stalled"] is True and st["stale_for_s"] >= 60
    assert st["threshold_s"] == 10


def test_an_unarmed_watchdog_does_not_claim_health(monkeypatch):
    """"We are not looking" is not "nothing is wrong"."""
    monkeypatch.setattr(hw, "_default", None)
    st = hw.status()
    assert st["armed"] is False and st["stalled"] is False
    assert "not armed" in st["reason"]


# ── the endpoint the UI calls ────────────────────────────────────────────────

def test_the_liveness_endpoint_answers_working_for_a_live_turn(client):
    t, release = _in_thread(lambda: core.turn_begin("t-http", "conv-http"))
    try:
        body = client.get("/api/chat/turn/t-http/liveness").get_json()
        assert body["state"] == "working"
        assert body["turn_id"] == "t-http"
        assert "hang" in body, "the UI must not have to invent a hang threshold"
    finally:
        release.set()
        t.join(5)


def test_the_liveness_endpoint_answers_gone_for_an_unknown_turn(client):
    body = client.get("/api/chat/turn/nope/liveness").get_json()
    assert body["state"] == "gone" and body["alive"] is False


def test_a_chat_turn_registers_and_releases_itself(client, monkeypatch):
    """The seam, end to end: the route must register the turn it was given so
    the poll can find it, and must release it afterwards so a finished turn
    never reads as alive."""
    seen = {}
    import agent_friday.routes.chat as chat_routes

    def _fake(*a, **kw):
        seen["live"] = core.turn_liveness("t-route")
        raise RuntimeError("stop here — registration is what is under test")

    monkeypatch.setattr(chat_routes, "_conv_context", _fake, raising=False)
    client.post("/api/chat", json={"message": "hello", "turn_id": "t-route"})
    assert seen.get("live", {}).get("state") == "working", \
        "the route never registered the turn it was handed"
    assert core.turn_liveness("t-route")["state"] == "gone", \
        "the route never released the turn"


def test_a_streamed_turn_is_visible_to_the_liveness_poll(client, monkeypatch):
    """The seam the UI actually uses.

    Chat does not post to /api/chat. It posts to /api/chat/stream, which runs
    chat() on a worker thread and delivers the reply as it is written. The
    liveness poll therefore has to find the turn registered by THAT path --
    and the poll is the only thing standing between a six-minute tool run and
    the composer being released underneath it. A six-minute turn with tool
    calls throughout can stream its whole reply correctly and still be
    declared dead if the poll cannot see it.
    """
    seen = {}
    import agent_friday.routes.chat as chat_routes

    def _fake(*a, **kw):
        # Asked from inside the turn, exactly as the poll asks it from outside.
        seen["live"] = core.turn_liveness("t-stream")
        raise RuntimeError("stop here -- registration is what is under test")

    monkeypatch.setattr(chat_routes, "_conv_context", _fake)
    r = client.post("/api/chat/stream",
                    json={"message": "hello", "turn_id": "t-stream"})
    r.get_data()          # drain the SSE body so the worker thread finishes
    assert seen, "the streamed turn never reached the point under test"
    assert seen["live"]["state"] == "working", (
        "a turn sent the way the UI sends every turn is invisible to the "
        "liveness poll, so the poll releases the chat on a healthy turn")
