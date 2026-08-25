"""Connector-backed providers populate their catalogue on boot.

Higgsfield enumerates over its MCP connector, not an HTTP /models endpoint.
`refresh_all_stale` only ever handled `discovery.mode == "api"`, so the
Higgsfield catalogue was never refreshed by the background sweep: after a
restart the cache stayed empty and the picker showed no Higgsfield rows until
somebody POSTed /api/models/refresh by hand.

These tests pin that the sweep is DECLARATIVE (a descriptor says how it
enumerates; the sweep obeys) and that a connector which is not up yet is
retried inside the boot window instead of an hour later.
"""
from __future__ import annotations

import pytest

import agent_friday.services.model_discovery as md


class _Registry:
    def __init__(self, providers):
        self._p = providers

    def get_enabled_providers(self):
        return self._p


@pytest.fixture
def registry(monkeypatch):
    def _install(providers):
        import agent_friday.services.provider_registry as pr
        monkeypatch.setattr(pr, "get_provider_registry",
                            lambda: _Registry(providers))
        return providers
    return _install


HF = {"name": "higgsfield", "type": "higgsfield",
      "discovery": {"mode": "mcp", "module": "higgsfield_catalog"}}


# ── The sweep now covers connector-backed providers ──────────────────────────

def test_mcp_provider_is_swept(registry, monkeypatch):
    registry([HF])
    monkeypatch.setattr(md, "read_cache", lambda n: None)     # never fetched
    called = {}

    def _refresh():
        called["hit"] = True
        return {"status": "refreshed", "provider": "higgsfield", "count": 9}

    import agent_friday.services.higgsfield_catalog as hc
    monkeypatch.setattr(hc, "refresh", _refresh)
    out = md.refresh_all_stale()
    assert called.get("hit") is True
    assert out[0]["status"] == "refreshed"


def test_a_fresh_cache_is_not_refetched(registry, monkeypatch):
    registry([HF])
    monkeypatch.setattr(md, "read_cache", lambda n: {"fetched_at": 1e12})
    monkeypatch.setattr(md, "cache_is_stale", lambda blob: False)
    import agent_friday.services.higgsfield_catalog as hc
    monkeypatch.setattr(hc, "refresh",
                        lambda: pytest.fail("refetched a fresh cache"))
    assert md.refresh_all_stale() == []


def test_api_providers_still_use_the_http_path(registry, monkeypatch):
    api_prov = {"name": "openrouter", "discovery": {"mode": "api"}}
    registry([api_prov])
    monkeypatch.setattr(md, "read_cache", lambda n: None)
    monkeypatch.setattr(md, "refresh_models",
                        lambda prov: {"status": "refreshed", "provider": "openrouter"})
    out = md.refresh_all_stale()
    assert out[0]["provider"] == "openrouter"


def test_provider_with_no_discovery_mode_is_skipped(registry, monkeypatch):
    registry([{"name": "local-comfyui", "type": "comfyui"}])
    monkeypatch.setattr(md, "read_cache", lambda n: None)
    assert md.refresh_all_stale() == []


# ── The module indirection is bounded ────────────────────────────────────────

def test_a_bogus_module_name_is_refused_not_imported(registry, monkeypatch):
    registry([{"name": "x", "discovery": {"mode": "mcp",
                                          "module": "os.path; rm -rf /"}}])
    monkeypatch.setattr(md, "read_cache", lambda n: None)
    out = md.refresh_all_stale()
    assert out[0]["status"] == "error"
    assert "discovery module" in out[0]["error"]


def test_a_missing_module_is_an_error_not_a_crash(registry, monkeypatch):
    registry([{"name": "x", "discovery": {"mode": "mcp",
                                          "module": "no_such_module"}}])
    monkeypatch.setattr(md, "read_cache", lambda n: None)
    out = md.refresh_all_stale()
    assert out[0]["status"] == "error"


def test_one_failing_provider_does_not_kill_the_sweep(registry, monkeypatch):
    api_prov = {"name": "openrouter", "discovery": {"mode": "api"}}
    registry([{"name": "x", "discovery": {"mode": "mcp", "module": "nope"}},
              api_prov])
    monkeypatch.setattr(md, "read_cache", lambda n: None)
    monkeypatch.setattr(md, "refresh_models",
                        lambda prov: {"status": "refreshed",
                                      "provider": "openrouter"})
    out = md.refresh_all_stale()
    assert [r["status"] for r in out] == ["error", "refreshed"]


# ── Boot-window retry ────────────────────────────────────────────────────────

UNAVAILABLE = [{"status": "unavailable", "provider": "higgsfield"}]
REFRESHED = [{"status": "refreshed", "provider": "higgsfield"}]


def test_connector_not_up_yet_is_retried_inside_the_boot_window():
    """The connector starts asynchronously, so the first sweep at +3s finds
    nothing. Waiting an hour would leave the picker empty all session."""
    assert md.next_sweep_delay(UNAVAILABLE, 1) == md._BOOT_RETRY_INTERVAL_S
    assert md.next_sweep_delay(UNAVAILABLE, 5) == md._BOOT_RETRY_INTERVAL_S


def test_the_retry_window_is_bounded():
    """It gives up retrying fast rather than hammering a dead connector."""
    assert md.next_sweep_delay(UNAVAILABLE, md._BOOT_RETRY_ATTEMPTS) == 3600.0
    # Five minutes of grace: enough for connector startup plus OAuth refresh.
    assert md._BOOT_RETRY_ATTEMPTS * md._BOOT_RETRY_INTERVAL_S == 300


def test_a_successful_sweep_settles_into_hourly():
    assert md.next_sweep_delay(REFRESHED, 1) == 3600.0


def test_a_real_error_is_not_retried_fast():
    """`error` means broken, not slow — a fast retry neither fixes it nor
    tells anyone, it just burns the connector."""
    assert md.next_sweep_delay([{"status": "error"}], 1) == 3600.0


def test_an_empty_sweep_settles_into_hourly():
    assert md.next_sweep_delay([], 1) == 3600.0
    assert md.next_sweep_delay(None, 1) == 3600.0


def test_one_provider_still_coming_up_holds_the_fast_cadence():
    mixed = [{"status": "refreshed"}, {"status": "unavailable"}]
    assert md.next_sweep_delay(mixed, 1) == md._BOOT_RETRY_INTERVAL_S
