"""The advisory pilot never loads, waits for, or authorizes a model action."""
import json
import threading

import pytest

from agent_friday.services import laya_backend as backend, laya_pilot as pilot


ENABLED = {"laya_pilot_enabled": True, "model_routing": {"mode": "local_only"}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    pilot._reset_for_tests()
    monkeypatch.setattr(backend, "_scoring", threading.BoundedSemaphore(2))
    monkeypatch.setattr(backend, "_agent", None)
    monkeypatch.setattr(backend, "_loading", False)
    monkeypatch.setattr(backend, "start_warming", lambda *a, **k: pytest.fail("cold load"))
    monkeypatch.setattr(pilot, "_off_record_now", lambda: False)
    yield
    pilot._reset_for_tests()


class Scorer:
    def __init__(self, choice="A", held=False):
        self.choice = choice
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.seen = []
        if not held:
            self.release.set()

    def predict(self, state, questions):
        self.seen.append((state, questions))
        self.entered.set()
        assert self.release.wait(2)
        self.finished.set()
        if "severity" in questions:
            return {"answers": {"severity": {"choice": "hard"}}}
        return {"answers": {"source": {"choice": self.choice}}}


def assisted(message="Find my notes", settings=None):
    # Alternate assignments are fixed before any prediction.
    control = pilot.start("control", settings or ENABLED)
    assert control.arm == "control"
    control.finish()
    ticket = pilot.start(message, settings or ENABLED)
    assert ticket.arm == "assisted"
    return ticket


def test_disabled_off_record_and_cold_never_load_or_score():
    assert pilot.start("private", {}) is None
    assert pilot.start("private", {**ENABLED, "off_record": True}) is None
    assert pilot.snapshot()["count"] == 0
    ticket = assisted()
    assert ticket.plan() is None
    ticket.finish()
    assert pilot.snapshot()["rows"][-1]["status"] == "cold"


def test_prediction_is_optional_immediate_and_frozen_at_dispatch(monkeypatch):
    scorer = Scorer(held=True)
    monkeypatch.setattr(backend, "_agent", scorer)
    ticket = assisted("latest request " + "x" * 5000)
    assert scorer.entered.wait(1)
    assert ticket.plan() is None
    ticket.mark_prepared()
    scorer.release.set()
    assert ticket._done.wait(1)
    assert ticket.plan() is None
    assert len(scorer.seen[0][0]) <= pilot.MAX_REQUEST_CHARS
    assert scorer.seen[0][0].startswith("latest request")
    ticket.finish()
    assert pilot.snapshot()["rows"][-1]["status"] == "not_ready"


@pytest.mark.parametrize("choice,expected", [("A", "knowledge"), ("B", "web"),
    ("C", "files"), ("D", "apps"), ("E", "none"), ("F", "mixed"), ("tool_delete", None)])
def test_validated_family_only(monkeypatch, choice, expected):
    scorer = Scorer(choice)
    monkeypatch.setattr(backend, "_agent", scorer)
    ticket = assisted()
    assert ticket._done.wait(1)
    assert ticket.plan() == expected
    ticket.mark_prepared()
    assert ticket.plan() == expected
    ticket.finish()


def test_timed_out_pilot_keeps_its_slot_and_leaves_one_for_approval(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(pilot.time, "monotonic", lambda: clock[0])
    scorer = Scorer(held=True)
    monkeypatch.setattr(backend, "_agent", scorer)
    ticket = assisted()
    assert scorer.entered.wait(1)
    clock[0] = pilot.SCORE_DEADLINE_S + 1
    assert ticket.plan() is None
    ticket.finish()
    assert backend.try_start_pilot_score("other", pilot.SOURCE_QUESTION, lambda *a: None) == "busy"
    lease = backend._reserve_scoring(pilot=False)
    assert lease is not None, "pilot consumed the reserved approval capacity"
    assert backend._reserve_scoring(pilot=False) is None
    lease()
    scorer.release.set()
    assert ticket._done.wait(1)
    assert ticket.plan() is None
    assert pilot.snapshot()["rows"][-1]["status"] == "timeout"


def test_approval_inflight_rejects_pilot_and_releases_after_timeout(monkeypatch):
    scorer = Scorer(held=True)
    monkeypatch.setattr(backend, "_agent", scorer)
    monkeypatch.setattr(backend, "_SCORE_TIMEOUT_S", 0.01)
    with pytest.raises(backend.LayaTooSlow):
        backend._answer_bounded("read notes")
    assert scorer.entered.is_set()
    ticket = assisted()
    assert ticket.plan() is None
    ticket.finish()
    assert pilot.snapshot()["rows"][-1]["status"] == "busy"
    scorer.release.set()
    assert scorer.finished.wait(1)


def test_direct_shadow_scoring_uses_shared_admission(monkeypatch):
    scorer = Scorer(held=True)
    monkeypatch.setattr(backend, "_agent", scorer)
    worker = threading.Thread(target=backend._answer, args=("read notes",))
    worker.start()
    try:
        assert scorer.entered.wait(1)
        assert backend.try_start_pilot_score("other", pilot.SOURCE_QUESTION, lambda *a: None) == "busy"
    finally:
        scorer.release.set()
        worker.join(2)


def test_prediction_error_is_a_fixed_status_without_raw_exception(monkeypatch):
    class Bad:
        def predict(self, *a):
            raise ValueError("private error content")
    monkeypatch.setattr(backend, "_agent", Bad())
    ticket = assisted("private prompt")
    assert ticket._done.wait(1)
    assert ticket.plan() is None
    ticket.finish(error="private error content")
    rendered = json.dumps(pilot.snapshot())
    assert "private" not in rendered
    assert pilot.snapshot()["rows"][-1]["status"] == "error"
    first = backend._reserve_scoring(pilot=False)
    second = backend._reserve_scoring(pilot=False)
    assert first is not None and second is not None
    first()
    second()


def test_metrics_idempotent_bounded_and_conversation_isolated(monkeypatch):
    monkeypatch.setattr(backend, "_agent", Scorer("D"))
    first = assisted("private first")
    assert first._done.wait(1)
    assert first.plan() == "apps"
    first.mark_prepared()
    first.increment("model_rounds")
    first.increment("loader_calls")
    first.finish(added_tools=2)
    first.finish(error=True)
    monkeypatch.setattr(backend, "_agent", Scorer("C"))
    second = assisted("private second")
    assert second._done.wait(1)
    assert second.plan() == "files"
    assert first.plan() == "apps"
    second.finish()
    rows = pilot.snapshot()["rows"]
    assert len(rows) == 4
    assert rows[1]["model_rounds"] == 1 and rows[1]["added_tools"] == 2
    assert rows[1]["outcome"] == "ok"
    assert rows[-1]["model_rounds"] == 0
    for _ in range(pilot.MAX_RECORDS + 2):
        pilot.start("private", ENABLED).finish()
    assert pilot.snapshot()["count"] == pilot.MAX_RECORDS
    assert "private" not in json.dumps(pilot.snapshot())


def test_off_record_activated_during_turn_suppresses_metrics(monkeypatch):
    ticket = pilot.start("private", ENABLED)
    monkeypatch.setattr(pilot, "_off_record_now", lambda: True)
    ticket.finish()
    assert pilot.snapshot()["count"] == 0


def test_thread_start_failure_returns_lease(monkeypatch):
    monkeypatch.setattr(backend, "_agent", Scorer())
    def broken_start(_self):
        raise RuntimeError("private error")
    monkeypatch.setattr(threading.Thread, "start", broken_start)
    assert backend.try_start_pilot_score("private", pilot.SOURCE_QUESTION, lambda *a: None) == "error"
    first = backend._reserve_scoring(pilot=False)
    second = backend._reserve_scoring(pilot=False)
    assert first is not None and second is not None
    first()
    second()


def test_ready_prediction_survives_slow_preparation(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(pilot.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(backend, "_agent", Scorer())
    ticket = assisted()
    assert ticket._done.wait(1)
    clock[0] = 10.0
    assert ticket.plan() == "knowledge"
    ticket.mark_prepared()
    assert ticket.plan() == "knowledge"


def test_latency_excludes_refusals_and_no_generation(monkeypatch):
    control = pilot.start("hello", ENABLED)
    control.finish(total_ms=1)
    assisted_ticket = pilot.start("hello", ENABLED)
    assisted_ticket.observe_execution("local")
    assisted_ticket.observe_execution("cloud")
    assisted_ticket.finish(model_rounds=2, total_ms=10, preparation_ms=3)
    refusal = pilot.start("hello", ENABLED)
    refusal.finish(model_rounds=1, total_ms=999, outcome="refused")
    groups = pilot.snapshot()["groups"]
    control_group = next(g for g in groups if g["arm"] == "control")
    assisted_group = next(g for g in groups if g["arm"] == "assisted")
    assert control_group["eligible_count"] == 0
    assert control_group["median_total_ms"] is None
    assert assisted_group["execution"] == "mixed"
    assert assisted_group["eligible_count"] == 1
    assert assisted_group["median_total_ms"] == 10


def test_concurrent_turns_do_not_share_plan_or_counters(monkeypatch):
    scorer = Scorer("D", held=True)
    monkeypatch.setattr(backend, "_agent", scorer)
    first = assisted("first")
    assert scorer.entered.wait(1)
    second = assisted("second")
    assert second.plan() is None
    second.increment("model_rounds", 3)
    second.finish()
    scorer.release.set()
    assert first._done.wait(1)
    assert first.plan() == "apps"
    first.increment("context_blocks")
    first.finish()
    rows = pilot.snapshot()["rows"]
    assert rows[-2]["status"] == "busy" and rows[-2]["model_rounds"] == 3
    assert rows[-1]["status"] == "ready" and rows[-1]["model_rounds"] == 0
    assert rows[-1]["applied"] is True and rows[-1]["context_blocks"] == 1


def test_ready_but_unused_plan_is_not_counted_as_applied(monkeypatch):
    monkeypatch.setattr(backend, "_agent", Scorer("C"))
    ticket = assisted()
    assert ticket._done.wait(1)
    assert ticket.plan() == "files"
    ticket.finish()
    row = pilot.snapshot()["rows"][-1]
    assert row["plan_ready"] is True
    assert row["applied"] is False
