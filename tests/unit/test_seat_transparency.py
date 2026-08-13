"""B2 — seat-change visibility (Incident 2's silent 10:16:59 flip).

Any change to seat-relevant settings keys must produce a persisted system
line and a notification. First observation seeds silently; no change, no
noise.
"""
from __future__ import annotations

from agent_friday import core
from agent_friday.services import seat_transparency as st


def _reset_state():
    try:
        st._STATE_FILE.unlink()
    except FileNotFoundError:
        pass


class TestEffectiveSeat:
    def test_local_only_mode_seats_the_local_model(self):
        # The truth the incident flip hid: orchestrator_model still said
        # claude-sonnet-5 while every turn went to gemma4:latest.
        model, seat = st.effective_seat({
            "orchestrator_model": "claude-sonnet-5",
            "model_routing": {"mode": "local_only",
                              "local_model": "gemma4:latest"},
        })
        assert (model, seat) == ("gemma4:latest", "local")

    def test_cloud_only_mode_seats_the_orchestrator(self):
        model, seat = st.effective_seat({
            "orchestrator_model": "claude-sonnet-5",
            "model_routing": {"mode": "cloud_only",
                              "local_model": "gemma4:latest"},
        })
        assert (model, seat) == ("claude-sonnet-5", "cloud")


class TestObserveSeats:
    def test_first_observation_seeds_silently(self):
        _reset_state()
        events = st.observe_seats({
            "orchestrator_model": "claude-sonnet-5",
            "model_routing": {"mode": "cloud_only", "local_model": "gemma4:latest"},
        })
        assert events == []

    def test_incident_flip_is_detected_and_persisted(self):
        _reset_state()
        base = {
            "orchestrator_model": "claude-sonnet-5",
            "model_routing": {"mode": "cloud_only", "local_model": "gemma4:latest"},
        }
        st.observe_seats(base)
        flipped = {
            "orchestrator_model": "claude-sonnet-5",   # unchanged — the trap
            "model_routing": {"mode": "local_only", "local_model": "gemma4:latest"},
        }
        hist_before = len(core.CHAT_HISTORY)
        events = st.observe_seats(flipped)
        assert len(events) == 1
        assert events[0]["key"] == "model_routing.mode"
        assert events[0]["old"] == "cloud_only"
        assert events[0]["new"] == "local_only"
        # Persisted system line in chat history, with the mode's meaning.
        assert len(core.CHAT_HISTORY) == hist_before + 1
        line = core.CHAT_HISTORY[-1]
        assert line["role"] == "system"
        assert line["kind"] == "seat_change"
        assert "local_only" in line["text"]
        assert "cloud orchestrator is not consulted" in line["text"]
        core.CHAT_HISTORY.pop()  # leave shared state clean

    def test_no_change_no_noise(self):
        _reset_state()
        s = {"orchestrator_model": "claude-sonnet-5",
             "model_routing": {"mode": "cloud_only", "local_model": "x"}}
        st.observe_seats(s)
        assert st.observe_seats(s) == []
        assert st.observe_seats(dict(s)) == []
