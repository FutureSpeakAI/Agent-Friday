"""Friday never goes quiet without saying so first.

Stephen, 2026-08-15: "Friday should always warn the user when local inference
will (or might) cause her to go silent for any amount of time so they can
decide if cloud or scheduling for idle time would be better."

The failure being pinned here is not the pause — it is the UNANNOUNCED pause.
A 53-second wait you asked for is fine; 53 seconds of a machine that looks hung
is not, and neither is finding out afterwards that the cloud would have taken
four.
"""
from __future__ import annotations

import pytest

from agent_friday.services import pause_forecast as pf


class _FakeLlama:
    def __init__(self, procs=()):
        self.procs = {p: (None, 0) for p in procs}


class _FakeOllama:
    def __init__(self, models=()):
        self._m = {m: 1 for m in models}

    def resident(self):
        return dict(self._m)


class _FakeArbiter:
    def __init__(self, *, owned=(), daemon=(), plan=None, entries=None):
        self.llama = _FakeLlama(owned)
        self.ollama = _FakeOllama(daemon)
        self.plan = plan or {"seats": {}}
        self.entries = entries or []
        self.comfy = type("C", (), {"running": lambda self: False})()


@pytest.fixture
def arb(monkeypatch):
    def _install(a):
        monkeypatch.setattr(
            "agent_friday.services.residency_arbiter.get_arbiter", lambda: a)
        return a
    return _install


# ── the common case: the first message of a session ──────────────────────────

def test_an_unloaded_model_is_a_certain_pause_with_a_number(arb):
    arb(_FakeArbiter())
    f = pf.before_local_turn("gemma4:12b")
    assert f["will_pause"] is True
    assert f["confidence"] == pf.CERTAIN
    assert f["seconds"] > 3
    assert "not loaded" in f["why"]
    # And it says the pause is one-off, so nobody concludes Friday is slow.
    assert "after that are normal" in f["why"]


def test_a_model_in_a_process_we_own_promises_no_pause(arb):
    arb(_FakeArbiter(owned=["gemma4:12b"]))
    f = pf.before_local_turn("gemma4:12b")
    assert f["will_pause"] is False
    assert "cannot be taken away" in f["why"]


def test_a_loaded_daemon_model_does_not_warn_on_every_single_turn(arb, monkeypatch):
    """Reversed on 2026-08-18, deliberately, after it reached a real desk.

    This used to assert the opposite: a daemon-served model was a "might",
    because Ollama can evict without announcing it. That is true — and it is
    true before EVERY message, forever, so warning on it meant warning always.
    Stephen hit exactly that: a confirmation before every message he sent,
    which he had to scroll up to answer before anything would proceed.

    A prompt that fires every time is not a safety feature. It is noise, and
    noise is how the warning gets clicked through on the day it finally
    matters. If the daemon does evict the model, the NEXT forecast sees it
    cold and says so honestly — one warning, when there is really a wait.
    """
    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.DAEMON_SERVED",
        {"gemma4:e2b": "channel tool calls"}, raising=False)
    arb(_FakeArbiter(daemon=["gemma4:e2b"]))
    f = pf.before_local_turn("gemma4:e2b")
    assert f["will_pause"] is False
    assert "is loaded" in f["why"]


def test_a_model_that_just_answered_is_not_called_cold(arb, monkeypatch):
    """The residency plan is not the only witness to what is loaded.

    Stephen switched his chat seat; the setting changed and the plan did not,
    so his model appeared in no seat and no resident set. The forecaster read
    that as "cold" and announced a 30-second wait before every message, while
    that same model answered him at normal speed. A model that served a turn a
    moment ago is warm, whatever the plan believes.
    """
    arb(_FakeArbiter())
    monkeypatch.setattr(pf, "_served_recently", lambda mid, within_s=900.0: (True, 12.0))
    f = pf.before_local_turn("some-model-the-plan-never-heard-of")
    assert f["will_pause"] is False
    assert "warm" in f["why"]


def test_confidence_never_outruns_the_basis(arb, monkeypatch):
    """A guessed duration may not be announced as certain."""
    arb(_FakeArbiter())
    monkeypatch.setattr(pf, "_served_recently", lambda mid, within_s=900.0: (False, None))
    monkeypatch.setattr(pf, "RECORDED_COLD_LOAD_S", {})
    f = pf.before_local_turn("never-timed-here:1b")
    assert f["will_pause"] is True
    assert f["basis"] == pf.ROUGH_DEFAULT_BASIS
    assert f["confidence"] == pf.POSSIBLE


def test_a_pause_too_short_to_mention_is_not_mentioned(arb, monkeypatch):
    monkeypatch.setattr(pf, "RECORDED_COLD_LOAD_S", {"tiny:1b": 0.5})
    arb(_FakeArbiter())
    assert pf.before_local_turn("tiny:1b")["will_pause"] is False


# ── every warning carries the choice ─────────────────────────────────────────

def test_every_warning_offers_the_three_options_stephen_approved(arb):
    arb(_FakeArbiter())
    for f in (pf.before_local_turn("gemma4:12b"), pf.before_heavy_lease(),
              pf.before_image()):
        assert [o["id"] for o in f["options"]] == \
            ["now_local", "now_cloud", "when_away"]


def test_vault_work_shows_the_cloud_option_disabled_with_its_reason(arb):
    arb(_FakeArbiter())
    f = pf.before_local_turn("gemma4:12b", vault=True)
    cloud = [o for o in f["options"] if o["id"] == "now_cloud"][0]
    assert cloud["unavailable"] is True
    assert "never leaves this machine" in cloud["detail"]


def test_an_unconfigured_cloud_is_shown_unavailable_not_hidden(arb):
    arb(_FakeArbiter())
    f = pf.before_local_turn("gemma4:12b", cloud_ok=False)
    cloud = [o for o in f["options"] if o["id"] == "now_cloud"][0]
    assert cloud["unavailable"] is True


# ── the bigger silences ──────────────────────────────────────────────────────

def test_a_heavy_lease_names_what_goes_away_and_what_stays(arb):
    arb(_FakeArbiter(plan={"seats": {
        "heavy_hitter": {"model_id": "gemma4:26b"},
        "interactive_brain": {"model_id": "gemma4:12b"}}}))
    f = pf.before_heavy_lease()
    assert f["will_pause"] is True
    assert "gemma4:12b" in f["why"], "say which model stands down"
    assert "sidekick" in f["why"], "and which one keeps answering"
    assert f["stays_awake"] == ["sidekick"]


def test_an_image_with_a_cold_engine_is_only_POSSIBLE(arb):
    """The cold start varies enormously — 93s warm against ~180s cold. A
    single confident number would be a guess wearing a suit."""
    arb(_FakeArbiter())
    f = pf.before_image()
    assert f["confidence"] == pf.POSSIBLE
    assert "varies" in f["why"]


def test_a_drain_explains_why_the_jobs_were_batched(arb, tmp_path,
                                                    monkeypatch):
    from agent_friday.services import work_queue as wq
    monkeypatch.setattr(wq, "queue_path", lambda: tmp_path / "q.json")
    for i in range(3):
        wq.enqueue("job %d" % i, "x", cls="heavy", disposition="when_away",
                   est_s_local=40)
    arb(_FakeArbiter(plan={"seats": {"heavy_hitter": {
        "model_id": "gemma4:26b"}}}))
    f = pf.before_drain("heavy")
    assert f["items"] == 3
    assert "loads once for the whole batch" in f["why"]


def test_an_empty_queue_is_not_a_warning(arb, tmp_path, monkeypatch):
    from agent_friday.services import work_queue as wq
    monkeypatch.setattr(wq, "queue_path", lambda: tmp_path / "q.json")
    arb(_FakeArbiter())
    assert pf.before_drain("heavy")["will_pause"] is False


# ── it must work when residency does not ─────────────────────────────────────

def test_a_forecast_still_happens_with_no_arbiter_at_all(monkeypatch):
    """That is the case where a pause is most likely and least predictable, so
    failing to forecast would be exactly backwards."""
    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.get_arbiter", lambda: None)
    monkeypatch.setattr(pf, "_residency", lambda: (set(), None))
    f = pf.before_local_turn("gemma4:26b")
    assert f["will_pause"] is True and f["seconds"] > 3


def test_every_estimate_says_where_the_number_came_from(arb):
    arb(_FakeArbiter())
    for f in (pf.before_local_turn("gemma4:26b"), pf.before_heavy_lease(),
              pf.before_image()):
        assert f["basis"], "an unattributed number is not a claim"


def test_a_measured_number_beats_a_recorded_one(arb):
    a = _FakeArbiter(entries=[{"model_id": "gemma4:26b",
                               "measured": [{"cold_load_s": 41.0}]}])
    arb(a)
    f = pf.before_local_turn("gemma4:26b")
    assert f["seconds"] == 41.0
    assert "measured on this machine" in f["basis"]
