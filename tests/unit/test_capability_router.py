"""Unit tests for services.capability_router — the single capability→provider+model
resolver that drives the onboarding lock/unlock badges and graceful degradation."""
import agent_friday.core as core
from agent_friday.services import capability_router as cr


def test_route_table_covers_all_capabilities():
    tbl = cr.route_table()
    assert {r["capability"] for r in tbl} == set(cr.CAPABILITIES)
    for r in tbl:
        assert "available" in r and "model" in r and "provider" in r and "label" in r


def test_embedding_always_available():
    r = cr.resolve("embedding", {"capability_routing": dict(core.DEFAULT_SETTINGS["capability_routing"])})
    assert r["available"] is True
    assert r["unlock_hint"] is None


def test_local_capability_available_without_key():
    r = cr.resolve("local", {"capability_routing": {"local": {"provider": "ollama-local", "model": "x"}}})
    assert r["available"] is True


def test_unlock_hint_when_provider_unavailable():
    s = {"capability_routing": {"voice": {"provider": "nonexistent-provider", "model": "x"}}}
    r = cr.resolve("voice", s)
    assert r["available"] is False
    assert r["unlock_hint"] and "unlock" in r["unlock_hint"].lower()


def test_unlock_note_helper():
    s = {"capability_routing": {"voice": {"provider": "nonexistent-provider", "model": "x"}}}
    assert cr.unlock_note("voice", s)
    assert cr.unlock_note("embedding", s) is None
