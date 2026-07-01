"""Unit tests for services/channels — manager funnel + adapters."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services.channels import manager  # noqa: E402
from agent_friday.services.channels.telegram_bridge import TelegramBridge  # noqa: E402
from agent_friday.services.channels.discord_bridge import DiscordBridge  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(manager, "CONFIG_PATH", tmp_path / "channels.json")
    monkeypatch.setattr(manager, "_ADAPTERS", {})
    yield


def test_default_config_disabled():
    cfg = manager.load_config()
    assert cfg["enabled"] is False
    assert "telegram" in cfg and "discord" in cfg


def test_config_roundtrip():
    cfg = manager.load_config()
    cfg["enabled"] = True
    cfg["telegram"]["allowlist"] = ["123"]
    assert manager.save_config(cfg)["ok"] is True
    again = manager.load_config()
    assert again["enabled"] is True
    assert again["telegram"]["allowlist"] == ["123"]


def test_configure_channel_stores_token(monkeypatch):
    from agent_friday.services import credential_store
    stored = {}
    monkeypatch.setattr(credential_store, "set_provider_key",
                        lambda p, k: stored.__setitem__(p, k) or "plaintext")
    res = manager.configure_channel("telegram", {"enabled": True, "allowlist": ["55"]},
                                    token="secret-bot-token")  # pragma: allowlist secret
    assert res["ok"] is True
    assert stored["channel_telegram"] == "secret-bot-token"
    assert manager.load_config()["telegram"]["allowlist"] == ["55"]


def test_configure_rejects_unknown_channel():
    assert manager.configure_channel("myspace", {})["ok"] is False


def test_handle_incoming_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(manager, "_run_agent", lambda t: "should not run")
    assert manager.handle_incoming("telegram", "1", "hi") is None


def test_handle_incoming_not_allowlisted_returns_none(monkeypatch):
    cfg = manager.load_config()
    cfg["enabled"] = True
    cfg["telegram"]["enabled"] = True
    cfg["telegram"]["allowlist"] = ["999"]
    manager.save_config(cfg)
    monkeypatch.setattr(manager, "_run_agent", lambda t: "nope")
    assert manager.handle_incoming("telegram", "1", "hi") is None


def test_handle_incoming_funnels_to_agent_and_gates(monkeypatch):
    cfg = manager.load_config()
    cfg["enabled"] = True
    cfg["telegram"]["enabled"] = True
    cfg["telegram"]["allowlist"] = ["1"]
    manager.save_config(cfg)
    monkeypatch.setattr(manager, "_run_agent", lambda t: f"echo: {t}")
    reply = manager.handle_incoming("telegram", "1", "hello world")
    assert reply == "echo: hello world"  # benign text passes the egress gate


def test_gate_reply_withholds_when_empty(monkeypatch):
    from agent_friday.services import egress_gate
    monkeypatch.setattr(egress_gate, "seal_outbound",
                        lambda payload, provider: {"messages": [{"role": "assistant", "content": ""}]})
    out = manager.gate_reply("something private", "telegram")
    assert "withheld" in out.lower()


def test_telegram_parse_updates_pure():
    payload = {"result": [
        {"update_id": 1, "message": {"text": "hi", "chat": {"id": 42}}},
        {"update_id": 2, "message": {"chat": {"id": 43}}},  # no text → skipped
        {"update_id": 3, "edited_message": {"text": "edit", "chat": {"id": 44}}},
    ]}
    pairs = TelegramBridge.parse_updates(payload)
    assert pairs == [{"chat_id": "42", "text": "hi"}, {"chat_id": "44", "text": "edit"}]


def test_telegram_status_shape():
    t = TelegramBridge()
    st = t.status()
    assert st["name"] == "telegram"
    assert st["running"] is False


def test_discord_missing_dep_graceful(monkeypatch):
    d = DiscordBridge()
    # Force the dependency probe False regardless of environment.
    monkeypatch.setattr(d, "dependency_ok", lambda: False)
    res = d.start()
    assert res["ok"] is False
    assert res["error"] == "missing_dependency"


def test_start_channel_requires_enabled():
    # channels master switch is off by default → start refused
    res = manager.start_channel("telegram")
    assert res["ok"] is False


def test_handle_incoming_error_path_is_generic(monkeypatch):
    # Regression: a raw agent exception (may embed vault paths / PII) must NOT
    # be sent to the external channel.
    cfg = manager.load_config()
    cfg["enabled"] = True
    cfg["telegram"]["enabled"] = True
    cfg["telegram"]["allowlist"] = ["1"]
    manager.save_config(cfg)

    def boom(text):
        raise RuntimeError("secret C:/Users/x/.friday/vault/key leaked here")
    monkeypatch.setattr(manager, "_run_agent", boom)
    reply = manager.handle_incoming("telegram", "1", "hi")
    assert "secret" not in reply and "vault" not in reply
    assert "internal error" in reply.lower()


def test_gate_reply_backstop_default_denies(monkeypatch):
    # Regression: on a double failure (gate raises AND classifier raises) the
    # backstop must WITHHOLD, not leak ungated text.
    from agent_friday.services import egress_gate, sensitivity_classifier as sc

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(egress_gate, "seal_outbound", boom)
    monkeypatch.setattr(sc, "classify", boom)
    out = manager.gate_reply("some private reply", "telegram")
    assert "withheld" in out.lower()


def test_gate_reply_backstop_allows_public(monkeypatch):
    from agent_friday.services import egress_gate, sensitivity_classifier as sc

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(egress_gate, "seal_outbound", boom)
    monkeypatch.setattr(sc, "classify", lambda t: sc.Tier.PUBLIC)
    out = manager.gate_reply("hello world", "telegram")
    assert out == "hello world"
