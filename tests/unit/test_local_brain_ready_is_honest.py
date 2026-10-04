"""Local voice readiness asks whether the brain seat is SERVING, not whether
a name resolves (local voice spec P0).

``local_seats.resolve("brain")`` returns the configured default on an empty
inventory, which is right for routing and wrong for readiness: session-info
said "ready" while the seat was parked for build hours, crashed or
unreachable, and the session then had nothing to think with. Ready now means
the resolved seat has a live endpoint; parked (build hours) and missing are
told apart in the copy.
"""
import pytest

import agent_friday.routes.voice as rv
from agent_friday.services import build_hours, local_seats


@pytest.fixture
def seat(monkeypatch):
    st = {"serving": {}, "build_hours": False}
    monkeypatch.setattr(local_seats, "resolve", lambda role, configured=None: "bonsai2:27b")
    monkeypatch.setattr(local_seats, "serving", lambda: dict(st["serving"]))
    monkeypatch.setattr(build_hours, "is_active", lambda *a, **k: st["build_hours"])
    # Readiness without a voice front: the brain is what answers.
    monkeypatch.setattr("agent_friday.services.voice_front.installed", lambda m: False)
    return st


def test_a_parked_seat_is_not_ready_and_the_copy_names_build_hours(seat):
    seat["build_hours"] = True
    assert rv._local_brain_ready() is False, (
        "a seat parked for build hours was reported ready")
    why = rv._local_brain_state()["why"]
    assert "build hours" in why and "bonsai2:27b" in why


def test_a_missing_seat_is_not_ready_and_does_not_blame_build_hours(seat):
    assert rv._local_brain_ready() is False
    why = rv._local_brain_state()["why"]
    assert "not running" in why and "build hours" not in why


def test_a_serving_seat_is_ready(seat):
    seat["serving"] = {"bonsai2:27b": "http://127.0.0.1:8099"}
    assert rv._local_brain_ready() is True
    assert rv._local_brain_state()["why"] == ""


def test_session_info_carries_the_reason(seat, monkeypatch):
    seat["build_hours"] = True

    class _Eng:
        def available(self):
            return True

        def models_ready(self):
            return True

        def resolve_tier(self, s):
            return "cpu"
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: _Eng())
    monkeypatch.setattr(rv, "_network_status", lambda: {"offline": True})
    out = rv._resolve_voice_engine({"voice_engine": "local"})
    assert out["engine"] == "local" and out["models_ready"] is False
    assert out["brain_ready"] is False and "build hours" in out["brain_why"]
