"""A hosted model has nothing to load, so it gets no load warning.

Observed 2026-08-30 with `openrouter/auto` seated as the everyday model:
sending a message raised "This might pause for 30s — openrouter/auto is not
loaded, so the first reply has to wait for it", in front of a cloud call that
answered in about a second. The id matched no residency entry, no recorded
load and no Ollama tag, so the forecast fell through to its flat 30s guess.
"""
from __future__ import annotations

import pytest

from agent_friday.services import pause_forecast as pf


@pytest.fixture(autouse=True)
def _no_local_stack(monkeypatch):
    """No residency plan, no Ollama — the state a cloud-only user is in."""
    monkeypatch.setattr(pf, "_residency", lambda: (set(), None))
    monkeypatch.setattr(pf, "_served_recently", lambda m: (False, None))


def test_the_auto_router_does_not_warn_about_loading(monkeypatch):
    monkeypatch.setattr(pf, "_looks_local", lambda mid, arb: False)
    f = pf.before_local_turn("openrouter/auto")
    assert f["will_pause"] is False
    assert "network" in f["why"]


def test_any_hosted_id_is_treated_the_same(monkeypatch):
    monkeypatch.setattr(pf, "_looks_local", lambda mid, arb: False)
    assert pf.before_local_turn("anthropic/claude-sonnet-5")["will_pause"] is False


def test_a_real_local_model_still_gets_its_warning(monkeypatch):
    """The feature this module exists for must survive the fix."""
    monkeypatch.setattr(pf, "_looks_local", lambda mid, arb: True)
    monkeypatch.setattr(pf, "_load_estimate",
                        lambda mid, arb: (42.0, "measured on this machine"))
    f = pf.before_local_turn("qwen3:32b")
    assert f["will_pause"] is True
    assert f["seconds"] == 42.0


def test_looks_local_says_no_when_nothing_knows_the_id(monkeypatch):
    """The evidence check itself: no arbiter entry, no recorded load, and an
    Ollama daemon that does not list it."""
    monkeypatch.setattr(pf, "RECORDED_COLD_LOAD_S", {})

    def _boom(*a, **k):
        raise OSError("no daemon")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert pf._looks_local("openrouter/auto", None) is False
