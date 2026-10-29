"""Message channels show what they can do, can be removed, and test only the owner's own chat
(BD-06, NS-19.7-3).

Before: Telegram and Discord had no screen at all; their routes answered any signed-in session
(no local, same-origin or press check), a test message went to whatever chat id a request
named, and there was no way to delete a channel's token. Now one card in Settings lists every
channel (Telegram, Discord, phone) with what it can and cannot do and who can reach it; every
change needs a press on this PC; a test goes only to a chat on the channel's allowlist; and
"Turn off & remove" stops the channel, switches it off and deletes its token, saying how to
revoke the token at the service too. Synthetic values only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import credential_store as cs
from agent_friday.services import keystore as ks
from agent_friday.services.channels import manager
from tests.desk_press_support import desk_headers

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "123456:synthetic-bot-token-" + "x" * 12  # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday import core
    h = tmp_path / ".friday"
    monkeypatch.setattr(core, "FRIDAY_DIR", h)
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    monkeypatch.setattr(cs, "_SECURITY_DIR", h / "security")
    monkeypatch.setattr(cs, "_CRED_AUDIT_LOG", h / "security" / "credential_audit.jsonl")
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", h / "providers" / "keys")
    monkeypatch.setattr(ks, "KEYSTORE_PATH", h / "security" / "keystore.json")
    monkeypatch.setattr(manager, "CONFIG_PATH", h / "channels.json")
    monkeypatch.setattr(manager, "FRIDAY_DIR", h, raising=False)
    ks._reset_cache_for_tests()
    manager._ADAPTERS.clear()
    yield h
    manager._ADAPTERS.clear()
    ks._reset_cache_for_tests()


def _overview(client):
    r = client.get("/api/channels/overview")
    assert r.status_code == 200, "there is no channels overview (%s)" % r.status_code
    return {c["channel"]: c for c in r.get_json()["channels"]}


def test_every_channel_says_what_it_can_do_and_who_can_reach_it(client, home):
    manager.configure_channel("telegram", {"enabled": True, "allowlist": ["5550001"]}, token=TOKEN)
    rows = _overview(client)
    assert {"telegram", "discord", "phone"} <= set(rows)
    tg = rows["telegram"]
    assert tg["can"] and tg["cannot"] and "1 chat" in tg["reach"]
    assert "BotFather" in tg["remove"]
    assert "nobody" in rows["discord"]["reach"]
    body = json.dumps(rows)
    assert TOKEN not in body and "5550001" not in body, "the overview showed a token or a chat id"


def test_channel_changes_need_a_press_on_this_pc(client, home):
    for path, payload in (("/api/channels/enable", {"enabled": True}),
                          ("/api/channels/telegram/configure", {"token": TOKEN}),
                          ("/api/channels/telegram/test", {}),
                          ("/api/channels/telegram/revoke", {})):
        r = client.post(path, json=payload)
        assert r.status_code == 403, "%s changed without a press on this PC" % path
    assert cs.provider_key_status("channel_telegram") != "connected"


def test_a_test_message_goes_only_to_a_chat_on_the_allowlist(client, home, monkeypatch):
    manager.configure_channel("telegram", {"enabled": True, "allowlist": ["5550001"]}, token=TOKEN)
    sent = []
    a = manager.get_adapter("telegram")
    monkeypatch.setattr(a, "send", lambda chat, text: sent.append(chat) or {"ok": True})
    r = client.post("/api/channels/telegram/test", json={"chat_id": "9999999"}, headers=desk_headers())
    assert r.get_json().get("ok") is False and sent == [], "a test went to a chat not on the allowlist"
    r = client.post("/api/channels/telegram/test", json={}, headers=desk_headers())
    assert r.get_json().get("ok") is True and sent == ["5550001"]


def test_turn_off_and_remove_deletes_the_token_and_says_how_to_revoke_it_at_the_service(client, home):
    manager.configure_channel("telegram", {"enabled": True, "allowlist": ["5550001"]}, token=TOKEN)
    assert cs.provider_key_status("channel_telegram") == "connected"
    r = client.post("/api/channels/telegram/revoke", json={}, headers=desk_headers())
    d = r.get_json()
    assert r.status_code == 200 and d.get("ok") is True and d.get("token_deleted") is True
    assert "BotFather" in d.get("at_the_service", "")
    assert cs.provider_key_status("channel_telegram") != "connected"
    assert manager.load_config()["telegram"]["enabled"] is False
    assert any(e.get("event") == "revoked" for e in cs.read_audit(category="channel"))


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_settings_shows_the_channels(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    assert "ChannelsCard" in text and "/api/channels/overview" in text, \
        "Settings has no place that shows the message channels"


def test_the_generic_key_route_cannot_change_a_channel_token(client, home):
    r = client.post("/api/providers/channel_telegram/key", json={"key": TOKEN})
    assert r.status_code == 403 and cs.provider_key_status("channel_telegram") == "missing"
    manager.configure_channel("telegram", {"enabled": True, "allowlist": ["5550001"]}, token=TOKEN)
    r = client.delete("/api/providers/channel_telegram/key")
    assert r.status_code == 403 and cs.provider_key_status("channel_telegram") == "connected"


def test_a_switched_off_phone_claims_nothing(client, home):
    phone = _overview(client)["phone"]
    assert phone["on"] is False and phone["can"] == []
