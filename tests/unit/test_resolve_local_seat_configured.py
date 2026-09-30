"""The local model the owner configured counts as "a local seat serving".

`scheduler._resolve_local_seat` answers "which local model is serving right
now?" for every local-only job and for podcast writing. It asked the reasoning
seat (often a cloud model) and the Ollama daemon's inventory. A seat served by
llama.cpp on its own port while the daemon is not running, named in
`model_routing.local_model`, was invisible: the answer was "none" while the
model was answering on 127.0.0.1.
"""
from __future__ import annotations

from agent_friday.services.scheduler import _resolve_local_seat as REAL_RESOLVE


def _setup(monkeypatch, settings, serving):
    import agent_friday.core as core
    from agent_friday.services import local_call, local_seats
    monkeypatch.setattr(core, "_load_settings", lambda: settings)
    monkeypatch.setattr(local_seats, "installed", lambda force=False: [])
    monkeypatch.setattr(local_call, "describe_dispatch", lambda m: (
        {"route": "seat", "model": m} if m in serving else {"route": "unreachable", "model": m}))


def test_the_configured_local_model_is_found_when_it_serves(monkeypatch):
    _setup(monkeypatch, {
        "capability_routing": {"reasoning": {"provider": "anthropic", "model": "claude-sonnet-5-5"}},
        "model_routing": {"mode": "local_preferred", "local_model": "bonsai2:27b"}},
        serving={"bonsai2:27b"})
    assert REAL_RESOLVE() == "bonsai2:27b"


def test_a_configured_local_model_that_is_not_serving_is_not_claimed(monkeypatch):
    _setup(monkeypatch, {"model_routing": {"local_model": "bonsai2:27b"}}, serving=set())
    assert REAL_RESOLVE() is None


def test_the_reasoning_seat_still_comes_first(monkeypatch):
    _setup(monkeypatch, {
        "capability_routing": {"reasoning": {"provider": "llama-cpp-local", "model": "gemma4:12b"}},
        "model_routing": {"local_model": "bonsai2:27b"}},
        serving={"gemma4:12b", "bonsai2:27b"})
    assert REAL_RESOLVE() == "gemma4:12b"
