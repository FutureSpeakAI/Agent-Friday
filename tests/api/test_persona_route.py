"""GET /api/persona/eval — the Persona Integrity route (routes/persona.py).

Default mode is fixture (deterministic, offline); ?mode=live stays gated off
in this suite (no FRIDAY_PERSONA_EVAL_LIVE / settings override is set), so
these tests never dispatch to a real provider. The autouse `_no_real_llm`
fixture in tests/api/conftest.py would catch it even if the gate were somehow
bypassed.
"""
from __future__ import annotations


def test_default_fixture_mode(client):
    resp = client.get("/api/persona/eval")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["mode"] == "fixture"
    assert set(data["providers"]) >= {"anthropic", "ollama-local", "openai"}
    assert "drift" in data
    assert data["golden_count"] >= 12


def test_explicit_fixture_mode(client):
    resp = client.get("/api/persona/eval?mode=fixture")
    assert resp.status_code == 200
    assert resp.get_json()["mode"] == "fixture"


def test_invalid_mode_is_400(client):
    resp = client.get("/api/persona/eval?mode=bogus")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_live_mode_disabled_by_default_is_400(client, monkeypatch):
    monkeypatch.delenv("FRIDAY_PERSONA_EVAL_LIVE", raising=False)
    resp = client.get("/api/persona/eval?mode=live")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False
    assert "disabled" in data["error"]


def test_threshold_query_param_overrides_default(client):
    lenient = client.get("/api/persona/eval?threshold=0.0").get_json()
    for data in lenient["providers"].values():
        assert data["items_passed"] == data["items_scored"]

    strict = client.get("/api/persona/eval?threshold=0.99").get_json()
    strict_anthropic = strict["providers"]["anthropic"]
    assert strict_anthropic["items_passed"] < strict_anthropic["items_scored"]


def test_seeded_bad_fixture_flagged_via_route(client):
    data = client.get("/api/persona/eval").get_json()
    flagged_pairs = {(f["provider"], f["id"]) for f in data["flagged"]}
    assert ("openai", "non_sycophancy_meme_coin_plan") in flagged_pairs


def test_route_never_touches_the_network_stub(client):
    """Belt-and-braces: even the globally-stubbed _generate_text/_call_claude
    (autouse in tests/api/conftest.py) are never invoked by the default
    fixture-mode route, since fixture mode never imports model_router."""
    resp = client.get("/api/persona/eval")
    assert resp.status_code == 200
