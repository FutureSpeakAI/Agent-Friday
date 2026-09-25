"""The Settings switch, and the safety property in its SHIPPED position.

Laya ships ON, a decision made with the eval in hand. That
moves the burden: the property that made the union safe to offer now has to
hold in the configuration that actually leaves the building, not in one a test
constructs for itself.

So `TestTheShippedConfiguration` forces nothing. No env var, no monkeypatched
backend name. It reads `DEFAULT_SETTINGS`, puts the gate in the state those
defaults describe, and asserts that enabling Laya removes no approval card the
keyword scan would have raised - under a model that says `soft` to everything,
which is what a corrupt or half-downloaded checkpoint looks like from outside.

The rest pins the switch itself:

  * three states, one definition of what each means (`laya_backend.MODES`),
    because the same mapping written in Python and again in two HTML files is
    a mapping that will disagree with itself;
  * a hand-edited settings file reports `custom` rather than being rounded to
    the nearest switch position;
  * flipping it ANNOUNCES itself through the same path a seat change uses - the
    user always knows which scanners are serving them;
  * and it never blocks: selecting Laya while the model is absent or still
    loading degrades to the keyword scan and says so, rather than stalling the
    gate or failing closed on the user's mail.
"""
from __future__ import annotations

import pytest

from agent_friday import core
from agent_friday.services import (approvals, decisions, dissent_gate,
                                   laya_backend, seat_transparency)

from tests.unit.test_laya_union_gate import (ADVERSARIES, CASE_TEXTS,
                                             _ExplodingAgent, _FakeAgent)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "log_path", lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    monkeypatch.delenv("FRIDAY_DECISION_BACKEND", raising=False)
    monkeypatch.delenv("FRIDAY_DECISION_SHADOW", raising=False)
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_agent", None)
    yield
    laya_backend._agent = None


def _settings_are(monkeypatch, settings: dict):
    """Put the process in the state a settings.json would put it in."""
    monkeypatch.setattr("agent_friday.core._load_settings",
                        lambda: dict(settings), raising=False)


# ═══════════════════════════════════════════════════════════════════════════
#  THE SHIPPED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

class TestTheShippedConfiguration:

    def test_the_defaults_really_do_ship_it_on(self):
        assert core.DEFAULT_SETTINGS["decision_backend"] == "laya-union"
        assert laya_backend.current_mode(core.DEFAULT_SETTINGS) == "on"

    def test_the_shipped_defaults_select_the_union_and_not_laya_alone(self, monkeypatch):
        """`laya` alone would REPLACE the keyword scan and give up the
        structural guarantee. It stays reachable by hand for evaluation; it
        must never be what ships."""
        _settings_are(monkeypatch, core.DEFAULT_SETTINGS)
        assert decisions.active_backend() == "laya-union"

    @pytest.mark.parametrize("adversary", sorted(ADVERSARIES))
    def test_no_card_is_lost_in_the_shipped_state(self, adversary, monkeypatch):
        """THE PROPERTY, re-verified where it now has to hold.

        Nothing here forces a backend. The gate is in the state
        DEFAULT_SETTINGS puts it in, and the comparison is against the
        keyword scan alone.
        """
        lost = []
        for text in CASE_TEXTS:
            monkeypatch.setattr(laya_backend, "_agent", None)
            _settings_are(monkeypatch, laya_backend.settings_for_mode("off"))
            want = approvals.classify(text)["gated"]

            monkeypatch.setattr(laya_backend, "_agent",
                                _FakeAgent(ADVERSARIES[adversary]))
            _settings_are(monkeypatch, core.DEFAULT_SETTINGS)
            got = approvals.classify(text)["gated"]

            if want and not got:
                lost.append(text)
        assert not lost, (
            "the SHIPPED configuration removed an approval card under "
            "adversary %r:\n  %s" % (adversary, "\n  ".join(lost)))


# ═══════════════════════════════════════════════════════════════════════════
#  IT MUST NOT BLOCK, AND MUST NOT FAIL CLOSED ON HIS MAIL
# ═══════════════════════════════════════════════════════════════════════════

class TestDegradation:
    """If the model is missing, corrupt, or mid-load, Friday must fall back to
    the keyword scanner and say so, never stall the gate and never fail closed
    on the user's mail. Each clause is a test."""

    def test_a_missing_model_behaves_exactly_like_today(self, monkeypatch):
        _settings_are(monkeypatch, core.DEFAULT_SETTINGS)
        for text in CASE_TEXTS:
            monkeypatch.setattr(laya_backend, "_agent", None)
            got = approvals.classify(text)["gated"]
            _settings_are(monkeypatch, laya_backend.settings_for_mode("off"))
            want = approvals.classify(text)["gated"]
            _settings_are(monkeypatch, core.DEFAULT_SETTINGS)
            assert got == want, "a missing model changed the verdict for %r" % text

    def test_a_corrupt_model_behaves_exactly_like_today(self, monkeypatch):
        for text in CASE_TEXTS:
            _settings_are(monkeypatch, laya_backend.settings_for_mode("off"))
            monkeypatch.setattr(laya_backend, "_agent", None)
            want = approvals.classify(text)["gated"]
            _settings_are(monkeypatch, core.DEFAULT_SETTINGS)
            monkeypatch.setattr(laya_backend, "_agent", _ExplodingAgent())
            assert approvals.classify(text)["gated"] == want

    def test_it_does_not_fail_closed_on_an_ordinary_internal_action(self, monkeypatch):
        """Failing CLOSED would mean every harmless request grows an approval
        card the moment the model is missing. That is not safety, it is an
        unusable assistant, and it is the other way this could go wrong."""
        _settings_are(monkeypatch, core.DEFAULT_SETTINGS)
        monkeypatch.setattr(laya_backend, "_agent", None)
        assert not approvals.classify("Summarise my notes from today")["gated"]
        assert not approvals.classify("Search my wiki for the pitch")["gated"]

    def test_an_unloaded_model_kicks_a_load_instead_of_waiting(self, monkeypatch):
        """However the switch got flipped - panel, file edit, CLI - the first
        decision starts the load in the BACKGROUND and is answered now. Without
        this, turning it on at runtime selects a model that never loads."""
        started = []
        monkeypatch.setattr(laya_backend, "start_warming",
                            lambda: started.append(True))
        monkeypatch.setattr(laya_backend, "_agent", None)
        monkeypatch.setattr(laya_backend, "_load_error", None)
        monkeypatch.setattr(laya_backend, "_loading", False)
        _settings_are(monkeypatch, core.DEFAULT_SETTINGS)

        approvals.classify("Send an email to the whole team")
        assert started, "selecting Laya did not kick a background load"

    def test_a_failed_load_is_not_retried_on_every_decision(self, monkeypatch):
        """One missing download must not become a thread per approval.

        Asserted on THREADS STARTED, not on calls to `start_warming`. The
        guard used to sit at this call site, keyed on `_load_error is None`,
        which also made a transient failure permanent - so it moved into
        `start_warming`, where the cooldown and the attempt budget live
        together. The call site may now ask on every decision; what must stay
        bounded is how often that turns into a real load.
        """
        import time as _t
        started = []

        class _CountingThread:
            def __init__(self, **kw):
                pass

            def start(self):
                started.append(True)

        monkeypatch.setattr(laya_backend.threading, "Thread", _CountingThread)
        monkeypatch.setattr(laya_backend, "_agent", None)
        monkeypatch.setattr(laya_backend, "_loading", False)
        monkeypatch.setattr(laya_backend, "_load_error", "OSError: no such file")
        monkeypatch.setattr(laya_backend, "_load_attempts", 1)
        monkeypatch.setattr(laya_backend, "_last_attempt_ts", _t.time())
        monkeypatch.delenv("FRIDAY_TESTING", raising=False)
        _settings_are(monkeypatch, core.DEFAULT_SETTINGS)

        for _ in range(5):
            approvals.classify("Send an email to the whole team")
        assert not started, (
            "a known-failed load started %d real loads inside the cooldown"
            % len(started))


# ═══════════════════════════════════════════════════════════════════════════
#  THE SWITCH
# ═══════════════════════════════════════════════════════════════════════════

class TestTheThreeStates:

    @pytest.mark.parametrize("mode", ["off", "shadow", "on"])
    def test_a_mode_round_trips(self, mode):
        assert laya_backend.current_mode(
            laya_backend.settings_for_mode(mode)) == mode

    def test_shadow_decides_nothing(self, monkeypatch):
        """The whole point of the middle position."""
        _settings_are(monkeypatch, laya_backend.settings_for_mode("shadow"))
        assert decisions.active_backend() == "keyword"
        assert decisions.shadow_backend() == "laya"

        monkeypatch.setattr(laya_backend, "_agent",
                            _FakeAgent(ADVERSARIES["inverts_the_incumbent"]))
        for text in CASE_TEXTS:
            got = approvals.classify(text)["gated"]
            _settings_are(monkeypatch, laya_backend.settings_for_mode("off"))
            want = approvals.classify(text)["gated"]
            _settings_are(monkeypatch, laya_backend.settings_for_mode("shadow"))
            assert got == want, "shadow changed the verdict for %r" % text

    def test_off_is_the_old_behaviour_exactly(self, monkeypatch):
        _settings_are(monkeypatch, laya_backend.settings_for_mode("off"))
        assert decisions.active_backend() == "keyword"
        assert decisions.shadow_backend() is None

    def test_a_hand_edited_state_reports_custom_rather_than_rounding(self):
        """`decision_backend: "laya"` is a real state a person can type. The
        panel must say so instead of lighting up a button that misdescribes
        what the file contains."""
        assert laya_backend.current_mode(
            {"decision_backend": "laya", "decision_shadow": ""}) == "custom"
        assert laya_backend.current_mode(
            {"decision_backend": "laya-union",
             "decision_shadow": "laya"}) == "custom"

    def test_every_mode_names_a_registered_backend(self):
        """A switch position that selects an unregistered name would fall back
        to keyword loudly - safe, but the panel would show ON while the
        keyword scan decided."""
        laya_backend.register()
        available = set(decisions.available_backends())
        for mode, delta in laya_backend.MODES.items():
            for key in ("decision_backend", "decision_shadow"):
                name = delta[key]
                if name:
                    assert name in available, (
                        "mode %r selects unregistered backend %r" % (mode, name))

    def test_an_unregistered_name_cannot_take_the_gate_offline(self, monkeypatch):
        _settings_are(monkeypatch, {"decision_backend": "nonsense-typo"})
        assert decisions.active_backend() == "keyword"
        assert approvals.classify("Send an email to the whole team")["gated"]


# ═══════════════════════════════════════════════════════════════════════════
#  FLIPPING IT IS VISIBLE
# ═══════════════════════════════════════════════════════════════════════════

class TestTheChangeAnnouncesItself:
    """The user always knows which scanners are serving them, so a state
    change is announced the same way seat changes are, never silent."""

    @pytest.fixture(autouse=True)
    def _state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(seat_transparency, "_STATE_FILE",
                            tmp_path / "seat_state.json")
        monkeypatch.setattr(core, "CHAT_HISTORY", [])
        monkeypatch.setattr(core, "_save_chat_history", lambda h: None)
        yield

    def _observe(self, mode):
        base = {"orchestrator_model": "claude-sonnet-5", "model_routing": {}}
        return seat_transparency.observe_seats(
            dict(base, **laya_backend.settings_for_mode(mode)))

    def test_turning_it_on_produces_an_event(self):
        assert self._observe("off") == []          # first observation seeds
        events = self._observe("on")
        assert events, "flipping the approval gate announced nothing"
        keys = {e["key"] for e in events}
        assert "decision_backend" in keys

    def test_the_line_says_what_it_now_does(self):
        self._observe("off")
        events = self._observe("on")
        text = " ".join(e["text"] for e in events)
        assert "Approval gate change" in text, text
        assert "EITHER" in text, (
            "the announcement does not say what the new state actually does: %s"
            % text)

    def test_it_is_not_announced_as_a_seat_change(self):
        """A user reads "Seat change" as a model swap. What moved is what
        guards their outbound mail."""
        self._observe("off")
        events = self._observe("on")
        assert not any(e["text"].startswith("Seat change") for e in events)

    def test_turning_it_off_is_announced_too(self):
        self._observe("on")
        events = self._observe("off")
        assert events, "turning the second scanner OFF was silent"

    def test_an_unchanged_setting_says_nothing(self):
        self._observe("on")
        assert self._observe("on") == []


# ═══════════════════════════════════════════════════════════════════════════
#  A FAILED LOAD MUST NOT BE PERMANENT
# ═══════════════════════════════════════════════════════════════════════════

class TestTheLoadRetries:
    """A transient failure once disabled the gate for the life of the process.

    The machine rebooted, Friday autostarted, and the boot-time load raised
    `ImportError: cannot import name 'AutoTokenizer' from 'transformers'`. A
    fresh interpreter imported it fine forty seconds later - a boot race,
    nothing more. But `_load_error` was set and every retry path was guarded
    on it being None, so the gate ran keyword-only until someone restarted.

    A missing checkpoint SHOULD stay refused; retrying per approval is a
    thread per decision. A transient one has to heal by itself, because the
    alternative is a feature that is off and says so only to whoever reads a
    status line.
    """

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        monkeypatch.setattr(laya_backend, "_agent", None)
        monkeypatch.setattr(laya_backend, "_loading", False)
        monkeypatch.setattr(laya_backend, "_load_error", None)
        monkeypatch.setattr(laya_backend, "_load_attempts", 0)
        monkeypatch.setattr(laya_backend, "_last_attempt_ts", 0.0)
        monkeypatch.delenv("FRIDAY_TESTING", raising=False)
        started = []
        monkeypatch.setattr(laya_backend.threading, "Thread",
                            lambda **kw: _FakeThread(started))
        self.started = started
        yield

    def test_a_failed_load_is_retried_after_the_cooldown(self, monkeypatch):
        monkeypatch.setattr(laya_backend, "_load_error", "ImportError: boom")
        monkeypatch.setattr(laya_backend, "_load_attempts", 1)
        monkeypatch.setattr(laya_backend, "_last_attempt_ts", 0.0)  # long ago
        laya_backend.start_warming()
        assert self.started, (
            "a failed load was never retried - one boot-time blip would "
            "disable the gate until a restart")

    def test_it_is_not_retried_during_the_cooldown(self, monkeypatch):
        import time as _t
        monkeypatch.setattr(laya_backend, "_load_error", "ImportError: boom")
        monkeypatch.setattr(laya_backend, "_load_attempts", 1)
        monkeypatch.setattr(laya_backend, "_last_attempt_ts", _t.time())
        laya_backend.start_warming()
        assert not self.started, "retried immediately - that is a thread per approval"

    def test_it_gives_up_after_the_attempt_budget(self, monkeypatch):
        monkeypatch.setattr(laya_backend, "_load_error", "OSError: no checkpoint")
        monkeypatch.setattr(laya_backend, "_load_attempts",
                            laya_backend._MAX_LOAD_ATTEMPTS)
        monkeypatch.setattr(laya_backend, "_last_attempt_ts", 0.0)
        laya_backend.start_warming()
        assert not self.started, (
            "a genuinely absent model kept being retried forever")

    def test_force_ignores_both_the_cooldown_and_the_budget(self, monkeypatch):
        import time as _t
        monkeypatch.setattr(laya_backend, "_load_error", "OSError: no checkpoint")
        monkeypatch.setattr(laya_backend, "_load_attempts",
                            laya_backend._MAX_LOAD_ATTEMPTS + 9)
        monkeypatch.setattr(laya_backend, "_last_attempt_ts", _t.time())
        laya_backend.start_warming(force=True)
        assert self.started, (
            "the operator retry could not get past its own backoff")

    def test_a_successful_load_clears_the_error(self, monkeypatch):
        """Otherwise the panel keeps reporting a failure that has been fixed."""
        monkeypatch.setattr(laya_backend, "_load_error", "ImportError: boom")
        monkeypatch.setattr(laya_backend.threading, "Thread", _real_thread())

        class _Stub:
            def predict(self, *a, **k):
                return {"answers": {"severity": {"choice": "hard"}}}

        monkeypatch.setattr(laya_backend, "_load_now",
                            lambda: _apply(laya_backend, _Stub()))
        laya_backend._load_now()
        assert laya_backend._load_error is None
        assert laya_backend.is_ready()


class _FakeThread:
    def __init__(self, sink):
        self._sink = sink

    def start(self):
        self._sink.append(True)


def _real_thread():
    import threading as _th
    return _th.Thread


def _apply(mod, agent):
    mod._agent = agent
    mod._load_error = None
    return agent
