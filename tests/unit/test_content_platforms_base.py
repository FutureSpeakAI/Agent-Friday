"""Unit tests for services/platforms/ — adapter base contract + registry.

Covers the §4.1 base defaults (envelopes, prepare validation, credential
conventions, rate-budget bookkeeping), the §4.14 degradation-ladder helper,
and the registry/lifecycle in platforms/__init__.py (lazy singletons, tolerant
imports, config I/O, aggregate status).
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import platforms as preg               # noqa: E402
from agent_friday.services.platforms import base as pbase         # noqa: E402
from agent_friday.services.platforms.base import PlatformAdapter  # noqa: E402
from agent_friday.services.platforms.mock import MockPlatformAdapter  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    preg._reset_for_tests()
    yield
    preg._reset_for_tests()


@pytest.fixture
def audit_calls(monkeypatch):
    """Capture credential_store.audit_event calls without touching disk."""
    from agent_friday.services import credential_store
    calls = []
    monkeypatch.setattr(credential_store, "audit_event",
                        lambda category, event, **f: calls.append((category, event, f)))
    return calls


# ── base: capabilities + envelopes ───────────────────────────────────────────
def test_default_capabilities_shape_and_isolation():
    a = PlatformAdapter()
    caps = a.capabilities()
    for k in ("formats", "char_limit", "title_limit", "media", "thread",
              "native_schedule", "native_delete", "analytics", "hashtags_max",
              "notes"):
        assert k in caps, k
    # deep-copied: mutating the returned dict must not corrupt the declaration
    caps["formats"].append("mutant")
    caps["media"]["alt_text"] = "corrupted"
    fresh = a.capabilities()
    assert "mutant" not in fresh["formats"]
    assert fresh["media"]["alt_text"] is False


def test_base_publish_delete_metrics_defaults():
    a = PlatformAdapter()
    pub = a.publish({"body": "x"})
    assert pub == {"ok": False, "error": "not_implemented"}
    dele = a.delete("id-1")
    assert dele["ok"] is False and dele["error"] == "not_supported"
    assert a.fetch_metrics("id-1") is None
    assert a.fetch_account_metrics() is None
    assert a.connect_url("state123") is None
    assert isinstance(a.handle_callback({}), dict)


def test_prepare_valid_body_ok_envelope():
    a = PlatformAdapter()
    res = a.prepare({"adapted_body": "hello", "format": "post", "id": "tgt_1"},
                    {"id": "post_1"})
    assert res["ok"] is True
    assert isinstance(res["warnings"], list)
    p = res["prepared"]
    assert p["platform"] == "platform" and p["body"] == "hello"
    assert p["target_id"] == "tgt_1" and p["post_id"] == "post_1"


def test_prepare_rejects_over_limit_body_and_segments():
    a = PlatformAdapter()
    limit = a.capabilities()["char_limit"]
    res = a.prepare({"adapted_body": "x" * (limit + 1)}, {})
    assert res["ok"] is False and "char_limit" in res["error"]
    # a single over-long segment fails too
    res = a.prepare({"adapted_body": "short",
                     "segments": ["fine", "y" * (limit + 1)]}, {})
    assert res["ok"] is False and "segment" in res["error"]


def test_prepare_alt_text_warning_and_image_cap():
    a = MockPlatformAdapter()   # mock declares alt_text: True, images max 4
    imgs = [{"kind": "image", "filename": f"i{i}.png"} for i in range(3)]
    res = a.prepare({"adapted_body": "b", "adapted_assets": imgs}, {})
    assert res["ok"] is True
    assert any("alt text" in w for w in res["warnings"])
    too_many = [{"kind": "image", "alt_text": "a"} for _ in range(5)]
    res = a.prepare({"adapted_body": "b", "adapted_assets": too_many}, {})
    assert res["ok"] is False and "images" in res["error"]


def test_prepare_never_raises_on_garbage():
    a = PlatformAdapter()
    res = a.prepare(None, None)
    assert isinstance(res, dict) and "ok" in res
    res = a.prepare({"segments": [1, 2, {"x": 1}], "adapted_assets": "nope"}, {})
    assert isinstance(res, dict) and "ok" in res


# ── base: credential conventions ─────────────────────────────────────────────
def test_credentials_round_trip_and_status(audit_calls):
    a = PlatformAdapter()
    a.name = "testplat"
    assert a.load_credentials() is None
    assert a.status()["connected"] is False

    blob = {"account": "@friday", "scopes": ["write"], "expires_at": "2026-09-01T00:00:00Z"}
    res = a.save_credentials(blob)
    assert res["ok"] is True and res["protection"]
    assert a.load_credentials() == blob

    st = a.status()
    assert st["connected"] is True
    assert st["account"] == "@friday" and st["scopes"] == ["write"]
    # nothing tokenish in status
    assert "access_token" not in json.dumps(st)

    events = [e for (cat, e, f) in audit_calls if cat == "platform"]
    assert "credentials_stored" in events

    res = a.clear_credentials()
    assert res["ok"] is True and res["removed"] is True
    assert a.load_credentials() is None
    assert a.status()["connected"] is False
    events = [e for (cat, e, f) in audit_calls if cat == "platform"]
    assert "credentials_cleared" in events


def test_revoke_purges_and_audits(audit_calls):
    a = PlatformAdapter()
    a.name = "testplat"
    a.save_credentials({"account": "x"})
    assert a.revoke() is True
    assert a.load_credentials() is None
    assert any(e == "revoke" for (_c, e, _f) in audit_calls)


def test_simple_secret_uses_provider_key(monkeypatch, audit_calls):
    from agent_friday.services import credential_store
    stored = {}
    monkeypatch.setattr(credential_store, "set_provider_key",
                        lambda p, v: stored.__setitem__(p, v) or "vault")
    monkeypatch.setattr(credential_store, "get_provider_key", stored.get)
    monkeypatch.setattr(credential_store, "delete_provider_key",
                        lambda p: stored.pop(p, None) is not None)
    a = PlatformAdapter()
    a.name = "bluesky"
    res = a.set_simple_secret("app-password-value")
    assert res["ok"] is True
    assert stored["platform_bluesky"] == "app-password-value"
    assert a.simple_secret() == "app-password-value"
    assert a.has_credentials() is True
    a.clear_credentials()
    assert a.simple_secret() is None


# ── base: rate budget bookkeeping ────────────────────────────────────────────
def test_rate_budget_fresh_window_and_consume():
    a = MockPlatformAdapter()
    b = a.rate_budget()
    assert b["window"] == "day" and b["used"] == 0
    assert b["limit"] == a.default_daily_limit
    assert b["reset_at"] and pbase._parse_iso(b["reset_at"]) > pbase._now_utc()

    res = a.consume_budget(3)
    assert res["ok"] is True and res["used"] == 3
    assert a.rate_budget()["used"] == 3
    # persisted, not in-memory: a fresh instance sees the same window
    assert MockPlatformAdapter().rate_budget()["used"] == 3


def test_budget_would_exceed_and_config_limit():
    a = MockPlatformAdapter()
    a.configure({"daily_post_limit": 2})
    assert a.rate_budget()["limit"] == 2
    assert a.budget_would_exceed() is False
    a.consume_budget(2)
    assert a.budget_would_exceed() is True
    assert a.budget_would_exceed(0) is False
    # limit <= 0 = unlimited (open protocols)
    a.configure({"daily_post_limit": 0})
    assert a.budget_would_exceed(10_000) is False


def test_budget_window_rolls_at_reset():
    a = MockPlatformAdapter()
    a.consume_budget(5)
    # force the persisted window into the past
    state = pbase._read_budget_state()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["mock"]["reset_at"] = past
    pbase._write_budget_state(state)
    b = a.rate_budget()
    assert b["used"] == 0, "window should reset after reset_at passes"


# ── base: degradation ladder (§4.14) ─────────────────────────────────────────
def test_degradation_ladder_options_and_envelope():
    a = PlatformAdapter()   # automation_tier = "api"
    assert a.degradation_options() == ["api_constrained", "assisted_handoff", "clipboard"]
    env = a.degrade("TikTok app not audited")
    assert env["ok"] is False and env["degraded"] is True
    assert env["requires_user_choice"] is True
    assert env["declared_rung"] == "api"
    assert env["options"] == ["api_constrained", "assisted_handoff", "clipboard"]
    assert "not audited" in env["reason"]

    a.automation_tier = "clipboard"
    assert a.degradation_options() == []
    assert a.degrade("end of the ladder")["options"] == []


# ── registry: lifecycle ───────────────────────────────────────────────────────
def test_registry_mock_singleton_and_aliases():
    a = preg.get_adapter("mock")
    assert a is not None and a.name == "mock"
    assert preg.get_adapter("mock") is a          # lazy singleton
    assert preg.get_adapter("MOCK") is a          # normalized
    # aliases resolve to the module name
    assert preg._norm("twitter") == "x_twitter"
    assert preg._norm("x") == "x_twitter"
    assert preg._norm("federation") == "federation_pub"
    assert preg.get_adapter("definitely-not-a-platform") is None


def test_registry_declares_all_twelve_modules():
    expected = {"linkedin", "x_twitter", "instagram", "youtube", "tiktok",
                "bluesky", "mastodon", "reddit", "substack", "medium",
                "federation_pub", "mock"}
    assert set(preg.ADAPTER_MODULES) == expected


def test_registry_tolerates_missing_module(monkeypatch):
    monkeypatch.setitem(preg.ADAPTER_MODULES, "zz_missing", "zz_missing")
    assert preg.get_adapter("zz_missing") is None
    assert "import failed" in (preg.import_error("zz_missing") or "")
    st = preg.status()
    assert st["platforms"]["zz_missing"]["available"] is False


def test_registry_tolerates_module_without_adapter_class(monkeypatch):
    empty = types.ModuleType("agent_friday.services.platforms.zz_empty")
    monkeypatch.setitem(sys.modules, "agent_friday.services.platforms.zz_empty", empty)
    monkeypatch.setitem(preg.ADAPTER_MODULES, "zz_empty", "zz_empty")
    assert preg.get_adapter("zz_empty") is None
    assert "no PlatformAdapter subclass" in (preg.import_error("zz_empty") or "")


def test_registry_status_aggregates_every_declared_adapter():
    st = preg.status()
    assert set(st["platforms"]) == set(preg.ADAPTER_MODULES)
    for name, entry in st["platforms"].items():
        assert isinstance(entry, dict), name
        assert "available" in entry and "platform" in entry
    mock_st = st["platforms"]["mock"]
    assert mock_st["available"] is True and mock_st["connected"] is True
    assert "pause_all" in st


# ── registry: config ──────────────────────────────────────────────────────────
def test_config_round_trip_and_corruption_tolerance():
    cfg = preg.load_config()
    assert cfg == {"pause_all": False}
    cfg["pause_all"] = True
    assert preg.save_config(cfg)["ok"] is True
    assert preg.publishing_paused() is True
    # corrupted file → defaults, no raise
    preg.CONFIG_PATH.write_text("{not json", encoding="utf-8")
    assert preg.load_config() == {"pause_all": False}


def test_configure_platform_pushes_options_to_live_adapter():
    a = preg.get_adapter("mock")
    res = preg.configure_platform("mock", {"enabled": True, "daily_post_limit": 5})
    assert res["ok"] is True
    assert preg.load_config()["mock"]["daily_post_limit"] == 5
    assert a.rate_budget()["limit"] == 5


def test_configure_platform_unknown_and_sanitization():
    assert preg.configure_platform("myspace", {})["ok"] is False
    res = preg.configure_platform("mock", {"note": "x" * 9999, "nested": {"a": 1}})
    assert res["ok"] is True
    saved = preg.load_config()["mock"]
    assert len(saved["note"]) == 512
    assert saved["nested"] == {"a": 1}


def test_configure_platform_stores_secret_via_provider_key(monkeypatch):
    from agent_friday.services import credential_store
    stored = {}
    monkeypatch.setattr(credential_store, "set_provider_key",
                        lambda p, v: stored.__setitem__(p, v) or "vault")
    monkeypatch.setattr(credential_store, "audit_event", lambda *a, **k: None)
    res = preg.configure_platform("mastodon", {}, secret_value="paste-me")
    assert res["ok"] is True
    assert stored == {"platform_mastodon": "paste-me"}
