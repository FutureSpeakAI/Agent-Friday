"""A local voice turn's provider is the seat that answers it, not the
orchestrator setting (local voice spec P0, defect 6d).

``_build_voice_system_prompt`` derived the provider from
``orchestrator_model``, a cloud id on most machines, so a turn answered by the
on-device seat was labelled "cloud": the vault's zero-trust check judged a
local model as a cloud one, and the prompt was gated as if it would leave the
machine. The provider now follows the resolved local seat, and the route pins
the turn to that seat so the label stays true.
"""
import inspect

from agent_friday.services.prompt_cache import VOLATILE_MARKER


def _wire(monkeypatch, rv):
    monkeypatch.setattr(rv, "_get_friday_system_prompt",
                        lambda **kw: "== FRIDAY ==\n" + VOLATILE_MARKER + "\nNow\n")
    monkeypatch.setattr(rv, "_build_session_continuity_block", lambda: "")
    monkeypatch.setattr(rv, "_build_emotional_tone_block", lambda: "")
    monkeypatch.setattr(rv, "_vault_local_only", lambda: False)
    monkeypatch.setattr(rv, "_vault_cloud_fallback", lambda: "warn")


def test_a_cloud_orchestrator_does_not_label_a_local_seat_turn_cloud(monkeypatch):
    import agent_friday.routes.voice as rv
    _wire(monkeypatch, rv)
    monkeypatch.setattr("agent_friday.services.local_seats.resolve",
                        lambda role, configured=None: "bonsai2:27b")
    _p, meta = rv._build_voice_system_prompt(
        {"orchestrator_model": "claude-opus-5-5"}, description="")
    assert meta["provider"] == "local", (
        "the on-device seat answers this turn; the orchestrator setting must not "
        "relabel it as cloud")
    assert meta["is_local_brain"] is True and meta["seat"] == "bonsai2:27b"


def test_an_explicit_seat_wins(monkeypatch):
    import agent_friday.routes.voice as rv
    _wire(monkeypatch, rv)
    _p, meta = rv._build_voice_system_prompt(
        {"orchestrator_model": "claude-opus-5-5"}, description="", seat="qwen3-4b")
    assert meta["provider"] == "local" and meta["seat"] == "qwen3-4b"


def test_the_local_handler_pins_the_turn_and_builds_the_prompt_for_its_seat():
    import agent_friday.routes.voice as rv
    src = inspect.getsource(rv)
    assert "_build_voice_system_prompt(settings, seat=_brain)" in src
    assert src.count('"pin_to_seat": True') >= 2, (
        "both the session's turns and its prefix warm must be pinned to the seat")
