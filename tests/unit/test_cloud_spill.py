"""Asking before spending, when the one local seat is busy.

The maintainer: "I want Friday to know when the local model is occupied and
ask the user, either with UI or in chat, if it's ok to assign a task out to a
cloud model when local seat is busy."

Two of his standing rules shape what "ask" is allowed to mean, and both are
easy to violate while looking like you obeyed:

  * never silently substitute one model for another — so the card names the
    seat it is waiting for AND the one it would move to, and an approval that
    arrives after the task already started locally must NOT run it twice;
  * never nag or block — so the task is not parked pending an answer. It
    keeps its place, it still starts locally when the seat frees, and the
    question is withdrawn at that point rather than left to be answered into
    a world that has moved on.

The tests are written against those two, not against "a card was created".
"""

import pytest

from agent_friday.services import cloud_spill as cs


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(cs, "_settings", lambda: {
        "capability_routing": {"subagent": {"model": "claude-sonnet-5"}},
    })
    monkeypatch.setattr(cs, "spend_context",
                        lambda: {"today_usd": 81.29, "advisory_tokens": 4_000_000,
                                 "hard_stop": None})


def _queued(**extra):
    rec = {"id": "t-1", "task_id": "t-1", "name": "Refine news filters",
           "status": "queued-for-seat", "seat": "local/bonsai2:27b",
           "seat_is_local": True, "model": "bonsai2:27b",
           "prompt": "do it", "conversation_id": "conv-1"}
    rec.update(extra)
    return rec


def _gate(monkeypatch, calls):
    import agent_friday.services.approvals as ap
    monkeypatch.setattr(ap, "gate_action",
                        lambda **kw: calls.append(kw) or
                        {"status": "pending",
                         "approval": {"approval_id": "ap-1", "payload": kw.get("payload")}})
    return calls


# ── when to ask ──────────────────────────────────────────────────────────────

def test_a_short_wait_is_not_worth_interrupting_anyone_for():
    assert cs.should_offer(_queued(), wait_s=5) is False


def test_a_real_wait_on_the_busy_local_seat_is(monkeypatch):
    assert cs.should_offer(_queued(), wait_s=120) is True


def test_a_task_that_is_not_waiting_is_never_asked_about():
    assert cs.should_offer(_queued(status="running"), wait_s=600) is False


def test_a_cloud_task_is_never_asked_about():
    """There is nothing to consent to: it was already going to cost money and
    nothing is blocking it."""
    assert cs.should_offer(_queued(seat_is_local=False), wait_s=600) is False


def test_it_asks_once_and_then_never_again(monkeypatch):
    """The supervisor calls this on every pass. Without this the user gets a
    card every 30 seconds for as long as the queue exists."""
    calls = _gate(monkeypatch, [])
    rec = _queued()
    assert cs.offer(rec, wait_s=120) is not None
    assert cs.offer(rec, wait_s=150) is None
    assert cs.offer(rec, wait_s=900) is None
    assert len(calls) == 1


def test_with_no_cloud_seat_configured_there_is_nothing_to_offer(monkeypatch):
    monkeypatch.setattr(cs, "_settings", lambda: {"capability_routing": {}})
    assert cs.should_offer(_queued(), wait_s=600) is False


def test_the_ask_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(cs, "enabled", lambda: False)
    assert cs.should_offer(_queued(), wait_s=600) is False


# ── what the card says ───────────────────────────────────────────────────────

def test_the_card_names_both_models_and_the_money(monkeypatch):
    """Transparency: he always knows which model is serving him, and a spend
    decision without the spend on it is not a decision."""
    calls = _gate(monkeypatch, [])
    cs.offer(_queued(), wait_s=180)
    kw = calls[0]
    assert "bonsai2:27b" in kw["description"]
    assert "claude-sonnet-5" in kw["description"]
    assert "claude-sonnet-5" in kw["title"]
    assert "$81.29" in kw["description"]
    assert kw["force_gate"] is True, "a spend decision is his, not the policy table's"
    assert kw["payload"]["cloud_model"] == "claude-sonnet-5"


def test_the_card_says_doing_nothing_is_fine(monkeypatch):
    """Never nag: the default has to be visible ON the question, or the
    question reads as a demand."""
    calls = _gate(monkeypatch, [])
    cs.offer(_queued(), wait_s=180)
    body = calls[0]["description"]
    assert "Doing nothing is fine" in body
    assert "keeps its place in the queue" in body


def test_an_unreadable_cost_still_asks_but_says_so(monkeypatch):
    calls = _gate(monkeypatch, [])
    monkeypatch.setattr(cs, "spend_context",
                        lambda: {"today_usd": None, "hard_stop": None})
    cs.offer(_queued(), wait_s=180)
    assert "treat the cost as unknown" in calls[0]["description"]


def test_a_failure_to_ask_is_never_a_decision_to_spend(monkeypatch):
    import agent_friday.services.approvals as ap

    def _boom(**kw):
        raise RuntimeError("approvals store is unwritable")

    monkeypatch.setattr(ap, "gate_action", _boom)
    rec = _queued()
    assert cs.offer(rec, wait_s=180) is None
    assert "cloud_spill_asked" not in rec, \
        "a failed ask must not mark the task as asked"


# ── what the answer does ─────────────────────────────────────────────────────

def _card(status="approved", task_id="t-1", cloud="claude-sonnet-5"):
    return {"kind": cs.KIND, "status": status, "approval_id": "ap-1",
            "payload": {"task_id": task_id, "cloud_model": cloud}}


def test_approval_dispatches_the_task_on_the_named_model(monkeypatch):
    import agent_friday.services.agent as ag
    spawned = {}
    monkeypatch.setattr(ag, "_spawn_task",
                        lambda name, prompt, **kw: spawned.update(
                            name=name, prompt=prompt, **kw) or "new-9")
    monkeypatch.setattr(ag, "_seat_supervisor", lambda: type(
        "S", (), {"on_task_end": staticmethod(lambda *a, **k: None)})())
    with ag.TASKS_LOCK:
        ag.TASKS["t-1"] = _queued()
    try:
        cs.on_decision(_card())
        assert spawned["model"] == "claude-sonnet-5"
        assert spawned["prompt"] == "do it"
        with ag.TASKS_LOCK:
            assert ag.TASKS["t-1"]["status"] == "superseded"
            assert "claude-sonnet-5" in ag.TASKS["t-1"]["status_reason"]
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop("t-1", None)


def test_a_yes_that_arrives_too_late_does_not_run_the_task_twice(monkeypatch):
    """The task is never blocked on the answer, so it can start locally while
    the card is still open. Acting on a stale yes would pay a cloud provider
    to redo work already running — and would be the silent duplicate the whole
    design is trying to avoid."""
    import agent_friday.services.agent as ag
    monkeypatch.setattr(ag, "_spawn_task",
                        lambda *a, **k: pytest.fail("ran a task twice"))
    with ag.TASKS_LOCK:
        ag.TASKS["t-1"] = _queued(status="running")
    try:
        cs.on_decision(_card())
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop("t-1", None)


def test_a_no_changes_nothing_at_all(monkeypatch):
    """Saying no is not a cancellation: it stays queued and still runs
    locally. That asymmetry is what makes the question safe to ask."""
    import agent_friday.services.agent as ag
    monkeypatch.setattr(ag, "_spawn_task",
                        lambda *a, **k: pytest.fail("denied card dispatched"))
    with ag.TASKS_LOCK:
        ag.TASKS["t-1"] = _queued()
    try:
        cs.on_decision(_card(status="denied"))
        with ag.TASKS_LOCK:
            assert ag.TASKS["t-1"]["status"] == "queued-for-seat"
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop("t-1", None)


def test_another_kind_of_card_is_ignored(monkeypatch):
    import agent_friday.services.agent as ag
    monkeypatch.setattr(ag, "_spawn_task",
                        lambda *a, **k: pytest.fail("acted on a foreign card"))
    cs.on_decision({**_card(), "kind": "wiki_update"})


def test_approval_unparks_the_local_worker_before_dispatching(monkeypatch):
    """If the seat frees a microsecond after the yes, local promotion must not
    find a thread to start for a task that is about to run on the cloud."""
    import agent_friday.services.agent as ag
    monkeypatch.setattr(ag, "_spawn_task", lambda *a, **k: "new-9")
    monkeypatch.setattr(ag, "_seat_supervisor", lambda: type(
        "S", (), {"on_task_end": staticmethod(lambda *a, **k: None)})())
    with ag._PENDING_TASK_THREADS_LOCK:
        ag._PENDING_TASK_THREADS["t-1"] = object()
    with ag.TASKS_LOCK:
        ag.TASKS["t-1"] = _queued()
    try:
        cs.on_decision(_card())
        with ag._PENDING_TASK_THREADS_LOCK:
            assert "t-1" not in ag._PENDING_TASK_THREADS
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop("t-1", None)
        with ag._PENDING_TASK_THREADS_LOCK:
            ag._PENDING_TASK_THREADS.pop("t-1", None)


def test_the_hook_registration_is_idempotent(monkeypatch):
    registered = []
    import agent_friday.services.approvals as ap
    monkeypatch.setattr(ap, "register_decision_hook",
                        lambda kind, fn: registered.append(kind))
    monkeypatch.setattr(cs, "_REGISTERED", False)
    cs.register()
    cs.register()
    assert registered == [cs.KIND]
