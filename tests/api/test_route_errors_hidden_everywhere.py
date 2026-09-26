"""Exception text never reaches the browser, however many hands it passes
through first.

`routes/_errors.public_result` swaps text a service marked with
`exception_text`; formatting, slicing or `str()` of a marked value drops the
mark, and a plain `str(e)` never had one. Each case here makes a real code path
fail with INTERNAL and checks the route's body does not carry it (and, where
the model reads the same value, that the model still does).
"""
from __future__ import annotations

import pytest

from agent_friday.user_errors import ExceptionText, UserFacingValueError

INTERNAL = "SECRET-INTERNAL-/path/detail"  # pragma: allowlist secret


def _boom(*_a, **_k):
    raise RuntimeError(INTERNAL)


def _hidden(resp):
    raw = resp.get_data(as_text=True)
    assert "SECRET-INTERNAL" not in raw, raw[:400]
    assert "Traceback" not in raw
    return resp.get_json()


# ── web search health and the canary ─────────────────────────────────────────

def _broken_search(monkeypatch):
    from agent_friday.services import web_search as ws
    monkeypatch.setattr(ws, "_wigolo_ready", lambda: False)
    monkeypatch.setattr(ws, "_firecrawl_ready", lambda: False)
    monkeypatch.setattr(ws, "brave_key", lambda: "k")
    monkeypatch.setattr(ws, "_gate_search_query", lambda q, p: (q, ""))
    monkeypatch.setattr(ws, "_brave", _boom)
    monkeypatch.setattr(ws, "_duckduckgo", _boom)
    return ws


def test_a_failed_search_keeps_its_text_marked_through_the_note_and_the_health(monkeypatch):
    ws = _broken_search(monkeypatch)
    out = ws.search("anything")
    assert isinstance(out["detail"], ExceptionText) and INTERNAL in out["detail"]
    health = ws.health_state()
    assert isinstance(health["detail"], ExceptionText) and INTERNAL in health["detail"]


def test_the_search_backend_route_hides_a_failed_search(client, monkeypatch):
    ws = _broken_search(monkeypatch)
    ws._canary_cache.update({"ts": 0.0, "ok": None, "detail": ""})
    ws.search("anything")
    data = _hidden(client.get("/api/research/search-backend"))
    assert data["error_id"]


# ── provider probes ──────────────────────────────────────────────────────────

def test_an_ollama_probe_failure_is_marked(monkeypatch):
    from agent_friday.routing.ollama_manager import OllamaManager
    mgr = OllamaManager("http://127.0.0.1:9")
    monkeypatch.setattr(mgr, "_post", _boom)
    out = mgr.probe_generate("m")
    assert isinstance(out["error"], ExceptionText) and INTERNAL in out["error"]


def test_a_machine_probe_failure_is_marked():
    from agent_friday.services import machine_probe
    out = machine_probe.probe_all({"x": _boom}, timeout=5)
    assert isinstance(out["x"]["error"], ExceptionText)


def test_the_provider_test_route_hides_an_ollama_failure(client, monkeypatch):
    from agent_friday.routing import ollama_manager
    from agent_friday.services.provider_registry import get_provider_registry
    name = next((p["name"] for p in get_provider_registry().list_providers()
                 if p.get("type") == "ollama"), None)
    if name is None:
        pytest.skip("no ollama provider registered")
    monkeypatch.setattr(ollama_manager, "get_manager", _boom)
    data = _hidden(client.post("/api/providers/%s/test" % name, json={}))
    assert data["error_id"]


def test_full_health_hides_a_failing_block(client, monkeypatch):
    from agent_friday.services import local_voice, provider_health
    monkeypatch.setattr(provider_health, "check_all", _boom)
    monkeypatch.setattr(local_voice, "local_voice_health", _boom)
    data = _hidden(client.get("/api/health/full"))
    assert data["providers"]["error"] == data["local_voice"]["error"]
    assert data["error_id"] in data["providers"]["error"]


def test_boot_health_hides_a_failing_subsystem(client, monkeypatch):
    from agent_friday.services import health_check, memory_dreaming
    monkeypatch.setattr(memory_dreaming, "_connect", _boom)
    ok, detail = health_check.check_memory_db()
    assert not ok and isinstance(detail, ExceptionText) and INTERNAL in detail
    _hidden(client.get("/api/health"))


# ── routes that printed str(e) ───────────────────────────────────────────────

def test_saving_a_distribution_hides_an_internal_value_error(client, monkeypatch):
    from agent_friday.routes import platform
    monkeypatch.setattr(platform.distributions, "save_distro", _raiser(ValueError))
    resp = client.post("/api/distros", json={"name": "mine"})
    assert resp.status_code == 400
    assert _hidden(resp)["error_id"]


def test_a_distribution_name_that_is_not_a_file_name_is_refused_in_words(client):
    resp = client.post("/api/distros", json={"name": "../escape"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid distribution name"


def test_enriching_a_calendar_event_hides_an_internal_value_error(client, monkeypatch):
    from agent_friday.routes import calendar
    monkeypatch.setattr(calendar, "_enrich_calendar_event", _raiser(ValueError))
    resp = client.post("/api/calendar/enrich", json={"event_id": "e1", "research": "r"})
    assert resp.status_code == 400
    assert _hidden(resp)["error_id"]


def test_an_event_id_that_is_not_a_file_name_is_refused_in_words(client):
    resp = client.post("/api/calendar/enrich", json={"event_id": "a/b", "research": "r"})
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "invalid event id"


def test_compression_stats_hide_a_failing_compressor(client, monkeypatch):
    from agent_friday.routes import context
    monkeypatch.setattr(context, "_get_context_compressor", _boom)
    data = _hidden(client.get("/api/context/compression-stats"))
    assert data["error_id"] in data["compression"]["error"]


def test_the_vault_posture_summary_is_marked(monkeypatch):
    from agent_friday.privacy import vault_policy
    from agent_friday.routes import intelligence
    monkeypatch.setattr(vault_policy, "status", _boom)
    summary = intelligence._vault_policy_status()["summary"]
    assert isinstance(summary, ExceptionText) and INTERNAL in summary


def test_voice_setup_hides_a_local_voice_failure(client, monkeypatch):
    from agent_friday.routes import voice
    from agent_friday.services import local_voice
    monkeypatch.setattr(voice, "_resolve_voice_engine", lambda *a, **k: {"engine": "local"})
    monkeypatch.setattr(local_voice, "local_voice_health", _boom)
    data = _hidden(client.get("/api/voice/setup/status"))
    assert data["error_id"]


def test_voice_setup_hides_googles_answer_but_still_says_the_key_was_rejected(client, monkeypatch):
    from agent_friday.routes import voice
    monkeypatch.setattr(voice, "_resolve_voice_engine", lambda *a, **k: {"engine": "gemini"})
    monkeypatch.setattr(voice, "resolve_gemini_key", lambda *a, **k: {
        "key": "k", "valid": False, "source": "settings.json",
        "detail": ExceptionText("HTTP 400: " + INTERNAL)})
    data = _hidden(client.get("/api/voice/setup/status"))
    key = next(s for s in data["steps"] if s["id"] == "key")
    assert key["status"] == "invalid"
    assert "was rejected by Google" in key["detail"] and "(error " in key["detail"]


# ── chat: the fallback chain, tool errors ────────────────────────────────────

def test_the_fallback_chain_keeps_the_mark_the_legs_put_on_it():
    from agent_friday.routes._errors import public_result
    from agent_friday.services import attribution
    attribution.reset()
    attribution.note_fallback(ExceptionText("local (m): " + INTERNAL))
    attribution.note_fallback("cloud (m): empty response")
    chain = attribution.fallback_chain()
    assert INTERNAL in chain[0]                      # the model path
    shown = public_result(chain, "A model in the fallback chain failed")
    assert INTERNAL not in shown[0] and shown[1] == "cloud (m): empty response"
    attribution.reset()


def test_a_tool_that_raises_gives_the_model_the_text_and_marks_it(monkeypatch):
    from agent_friday.governance import action_gate
    from agent_friday.services import agent
    # A local read, so the governance and approval hooks let it run.
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "zz_boom_probe", _boom)
    monkeypatch.setitem(agent.TOOL_RINGS, "zz_boom_probe", 0)
    monkeypatch.setattr(action_gate, "INTERNAL_TOOLS",
                        action_gate.INTERNAL_TOOLS | {"zz_boom_probe"})
    out = agent._execute_tool("zz_boom_probe", {})
    assert out.startswith("Tool error (zz_boom_probe): ")
    assert INTERNAL in out and isinstance(out, ExceptionText)


# ── services whose results routes return ─────────────────────────────────────

def test_the_phone_ingress_port_failure_is_marked(monkeypatch):
    from agent_friday.phone import config, ingress, service

    def _busy(_port):
        raise OSError(INTERNAL)
    monkeypatch.setattr(config, "load", lambda: {"enabled": True, "ingress_port": 3011})
    monkeypatch.setattr(config, "get_secret", lambda _n: "t")
    monkeypatch.setattr(ingress, "start", _busy)
    out = service.apply_enabled_state()
    assert isinstance(out["error"], ExceptionText)


def test_a_peer_endpoint_that_does_not_parse_is_refused_without_the_parser_text():
    from agent_friday.services.web_safety import check_peer_endpoint
    ok, why = check_peer_endpoint("http://[::1")
    assert not ok and why == "the endpoint could not be parsed"


def test_the_publisher_kick_failure_is_marked(monkeypatch):
    from agent_friday.services import publisher
    monkeypatch.setattr(publisher, "_testing", lambda: False)
    monkeypatch.setattr(publisher.threading, "Thread", _boom)
    out = publisher.kick()
    assert isinstance(out["reason"], ExceptionText)


def test_a_pipeline_id_that_is_not_a_file_name_is_refused_in_words():
    from agent_friday.services import creative_pipeline as cp
    out = cp.register_pipeline({"id": "../x", "stages": [
        {"name": "s", "instruction": "i", "output_key": "o"}]})
    assert out == {"status": "error", "message": "invalid pipeline id"}


def test_paths_refusals_are_written_for_the_user(tmp_path):
    from agent_friday.paths import contained, safe_name
    with pytest.raises(UserFacingValueError):
        safe_name("a/b")
    with pytest.raises(ValueError):
        contained(tmp_path, "../x")


def _raiser(exc_type):
    def _r(*_a, **_k):
        raise exc_type(INTERNAL)
    return _r
