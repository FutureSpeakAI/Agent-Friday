"""A menu must never wait on the machine, and a card must never overstate it.

The model selector dropdown and the Intelligence settings menu must never time
out trying to read the machine.

Measured against a live server before the fix, the cost was not where one
would guess:

  * the model picker (`build_catalog`) cost **18.0s**, made up of
    `_tts_engines` 14.3s (it calls `kokoro_health()`, which imports torch on
    purpose -- 28.9s alone on a cold process), `_arbiter_seat_entries` 4.1s,
    `_friday_store_entries` 4.1s, the ComfyUI creative overlay 4.1s, the Ollama
    daemon probe 4.1s with no daemon, and `is_provider_available("nvidia-nemo")`
    2.8s per call. Every `_model_entries_for(provider)` was 0.00s: model
    discovery was never the problem.
  * Settings > Intelligence (`inference_health`) ran seven provider probes
    SERIALLY for **44.2s**, and recurred every 60s (`_PROBE_TTL_S`). Four were
    failures costing 4-12s each on connection timeouts; two were real billable
    inference calls.

After: 0.3s and 2.0s. These tests pin the properties that keep it that way, with
fake probes rather than the real ones -- a test that imported torch would be
measuring the bug instead of the fix.
"""

import time

import pytest

from agent_friday.services import machine_probe as mp
from agent_friday.services import swr_cache


@pytest.fixture(autouse=True)
def clean():
    swr_cache.invalidate("")
    yield
    swr_cache.invalidate("")


# ── the budget is a ceiling, not a target ───────────────────────────────────

def test_a_slow_probe_returns_unknown_instead_of_blocking():
    """The whole bug in one assertion: a probe that takes far longer than the
    budget must not make the caller wait for it."""
    def slow():
        time.sleep(30)
        return "eventually"

    t0 = time.time()
    value, at, state = mp.snapshot("t:slow", slow, fresh_for=60, budget=0.2,
                                   default="placeholder", allow_blocking=False)
    elapsed = time.time() - t0
    assert elapsed < 2.0, "waited %.1fs on a probe with a 0.2s budget" % elapsed
    assert state == mp.STATE_UNKNOWN
    assert value == "placeholder"
    assert at == 0.0, "an unread value must not carry a plausible timestamp"


def test_a_fast_probe_answers_inside_the_budget():
    """The budget must still pay off when the probe is quick, or it would be a
    pointless delay before a guaranteed placeholder."""
    value, at, state = mp.snapshot("t:fast", lambda: 42, fresh_for=60,
                                   budget=2.0, default=None)
    assert (value, state) == (42, mp.STATE_FRESH)
    assert at > 0


def test_later_callers_do_not_each_pay_the_budget():
    """Found by measurement: with the budget charged to every caller, a
    29-second import made EVERY menu open cost the full budget for 29 seconds.
    Only the caller that starts a probe may wait for it."""
    started = []

    def slow():
        started.append(1)
        time.sleep(30)
        return "x"

    mp.snapshot("t:once", slow, fresh_for=60, budget=0.2, default=None,
                allow_blocking=False)
    t0 = time.time()
    for _ in range(5):
        mp.snapshot("t:once", slow, fresh_for=60, budget=0.2, default=None,
                allow_blocking=False)
    elapsed = time.time() - t0
    assert elapsed < 0.3, "five later callers took %.2fs" % elapsed
    assert len(started) == 1, "spawned %d probes for one key" % len(started)


def test_a_stale_value_is_served_immediately_and_labelled():
    """Stale-but-labelled, never a spinner. The caller gets the last answer at
    once AND is told it is old, so a UI can say "as of 2m ago"."""
    mp.snapshot("t:stale", lambda: "first", fresh_for=60, budget=2.0)
    time.sleep(0.05)

    def slow_second():
        time.sleep(30)
        return "second"

    t0 = time.time()
    value, at, state = mp.snapshot("t:stale", slow_second, fresh_for=0.0,
                                   budget=5.0)
    assert time.time() - t0 < 1.0, "a stale read waited for the refresh"
    assert value == "first" and state == mp.STATE_STALE
    assert mp.age_note(at, state), "a stale reading must be able to say its age"


def test_age_note_never_dates_an_unread_value():
    assert mp.age_note(0.0, mp.STATE_UNKNOWN) == "not read yet"
    assert mp.age_note(time.time(), mp.STATE_FRESH) is None
    assert "ago" in mp.age_note(time.time() - 120, mp.STATE_STALE)


def test_a_failing_probe_does_not_poison_the_key_forever():
    """A probe that raises must leave the key retryable, not cached as broken."""
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("no daemon")

    for _ in range(3):
        v, _at, st = mp.snapshot("t:boom", boom, fresh_for=60, budget=1.0,
                                 default="none", allow_blocking=False)
        assert (v, st) == ("none", mp.STATE_UNKNOWN)
    assert len(calls) >= 1


# ── parallel probes with a hard per-probe ceiling ───────────────────────────

def test_probe_all_is_parallel_not_serial():
    """Seven probes at 4-12s each was 44 seconds. In parallel the sweep costs
    about one probe's worth of time."""
    probes = {"p%d" % i: (lambda: (time.sleep(0.4), "ok")[1]) for i in range(6)}
    t0 = time.time()
    got = mp.probe_all(probes, timeout=3.0)
    elapsed = time.time() - t0
    assert elapsed < 1.5, "six 0.4s probes took %.2fs — serial?" % elapsed
    assert all(r["ok"] for r in got.values())


def test_one_hanging_probe_cannot_hold_up_the_others():
    """ollama-local took 12.06s against a refused connection. It must not make
    the other six wait."""
    def hang():
        time.sleep(30)

    probes = {"hangs": hang, "quick": lambda: "here"}
    t0 = time.time()
    got = mp.probe_all(probes, timeout=0.5)
    elapsed = time.time() - t0
    assert elapsed < 2.0, "a hanging probe held the sweep for %.1fs" % elapsed
    assert got["hangs"]["timed_out"] is True
    assert got["hangs"]["ok"] is False
    assert got["quick"]["value"] == "here"


def test_a_raising_probe_is_a_verdict_not_a_crash():
    got = mp.probe_all({"bad": lambda: 1 / 0}, timeout=2.0)
    assert got["bad"]["ok"] is False
    assert got["bad"]["timed_out"] is False
    assert "ZeroDivisionError" in got["bad"]["error"]


# ── "did not find out" is not "down" ────────────────────────────────────────

def test_a_timed_out_provider_is_unknown_not_down(monkeypatch):
    """Reporting "Friday cannot currently think" because a probe was slow is
    the same class of error as passing a stale value off as live."""
    from agent_friday.services import provider_health as ph

    fake = [{"name": "slowcloud", "type": "anthropic", "enabled": True}]
    monkeypatch.setattr(ph, "_has_key", lambda p: True)

    class _Reg:
        def list_providers(self):
            return fake

    import agent_friday.services.provider_registry as pr
    monkeypatch.setattr(pr, "get_provider_registry", lambda: _Reg())
    monkeypatch.setattr(ph, "inference_probe",
                        lambda name, prov=None, use_cache=True: time.sleep(30))
    monkeypatch.setattr(mp, "DEFAULT_PROBE_TIMEOUT_S", 0.3)

    t0 = time.time()
    out = ph.inference_health()
    assert time.time() - t0 < 3.0
    assert out["status"] == "unknown", out
    assert out.get("unread") == ["slowcloud"]


def test_one_good_provider_still_reads_degraded_not_down(monkeypatch):
    from agent_friday.services import provider_health as ph

    provs = [{"name": "good", "type": "anthropic", "enabled": True},
             {"name": "slow", "type": "anthropic", "enabled": True}]
    monkeypatch.setattr(ph, "_has_key", lambda p: True)

    class _Reg:
        def list_providers(self):
            return provs

    import agent_friday.services.provider_registry as pr
    monkeypatch.setattr(pr, "get_provider_registry", lambda: _Reg())

    def probe(name, prov=None, use_cache=True):
        if name == "slow":
            time.sleep(30)
        return {"provider": name, "status": "ok", "detail": "fine"}

    monkeypatch.setattr(ph, "inference_probe", probe)
    monkeypatch.setattr(mp, "DEFAULT_PROBE_TIMEOUT_S", 0.3)
    out = ph.inference_health()
    assert out["status"] == "degraded"
    assert out.get("unread") == ["slow"]
