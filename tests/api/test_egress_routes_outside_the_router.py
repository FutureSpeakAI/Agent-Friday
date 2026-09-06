"""Route-level outbound paths found past the gate in the 2026-09-06 audit.

- /api/federation/send and /api/federation/settings/sync handed caller and
  user text to a peer with no classification.
- /api/chat/send sent images to Gemini regardless of Local-only mode and
  wrote no ledger row, while its sibling /api/chat did both.

All red on main @ 9d329fe. Network is stubbed throughout.
"""
from __future__ import annotations

import base64
import sys
import types

import pytest

SSN = "my SSN is 123-45-6789"  # pragma: allowlist secret


@pytest.fixture
def redacting_gate(monkeypatch):
    from agent_friday.services import egress_gate as eg
    calls = []

    def fake_gate_text(text, provider, field="prompt", log_path=None):
        calls.append((provider, field))
        return "[REDACTED]" if "123-45-6789" in str(text) else text  # pragma: allowlist secret
    monkeypatch.setattr(eg, "gate_text", fake_gate_text)
    return calls


@pytest.fixture
def transport_spy(monkeypatch):
    from agent_friday.services import federation_transport as t
    sent = []
    monkeypatch.setattr(t, "build_message", lambda msg_type, payload, pub: {"msg_type": msg_type, "payload": payload})
    monkeypatch.setattr(t, "send_to_peer", lambda endpoint, env, timeout=15: sent.append((endpoint, env)) or {"ok": True})
    return sent


def test_federation_send_refuses_a_payload_the_gate_would_alter(client, redacting_gate, transport_spy):
    r = client.post("/api/federation/send", json={
        "endpoint": "http://peer.invalid", "recipient_pubkey": "ab" * 32,
        "msg_type": "CONTENT_TRANSFER", "payload": {"text": SSN, "nested": [{"note": "fine"}]}})
    assert r.status_code == 403 and "egress gate" in r.get_json()["error"]
    assert transport_spy == [], "the envelope left despite the gate"
    assert ("federation", "federation.CONTENT_TRANSFER.text") in redacting_gate


def test_federation_send_passes_a_clean_payload(client, redacting_gate, transport_spy):
    r = client.post("/api/federation/send", json={
        "endpoint": "http://peer.invalid", "recipient_pubkey": "ab" * 32,
        "msg_type": "PING", "payload": {"text": "hello"}})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert transport_spy and transport_spy[0][1]["payload"] == {"text": "hello"}


def test_federation_settings_sync_gates_free_text_settings(client, monkeypatch, redacting_gate, transport_spy):
    import agent_friday.core as core
    from agent_friday.services import federation as fed
    monkeypatch.setattr(core, "_load_settings_raw", lambda: {"voice_style_prompt": "speak like " + SSN, "temperature": 0.7})
    monkeypatch.setattr(fed, "get_peers", lambda: [{"agent_id": "p1", "public_key_hex": "ab" * 32, "endpoint": "http://peer.invalid"}])
    r = client.post("/api/federation/settings/sync", json={})
    assert r.status_code == 403 and transport_spy == []
    assert ("federation", "settings_sync.voice_style_prompt") in redacting_gate


def test_federation_settings_sync_sends_when_clean(client, monkeypatch, redacting_gate, transport_spy):
    import agent_friday.core as core
    from agent_friday.services import federation as fed
    monkeypatch.setattr(core, "_load_settings_raw", lambda: {"voice_style_prompt": "warm and brief", "temperature": 0.7})
    monkeypatch.setattr(fed, "get_peers", lambda: [{"agent_id": "p1", "public_key_hex": "ab" * 32, "endpoint": "http://peer.invalid"}])
    r = client.post("/api/federation/settings/sync", json={})
    assert r.status_code == 200 and r.get_json()["sent"] == 1


# ── /api/chat/send vision ───────────────────────────────────────────────────

@pytest.fixture
def fake_genai(monkeypatch):
    calls = []

    class _Models:
        def generate_content(self, **kw):
            calls.append(kw)
            return types.SimpleNamespace(text="a desk")

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = _Client
    gtypes = types.ModuleType("google.genai.types")
    gtypes.Part = types.SimpleNamespace(from_bytes=lambda data, mime_type: ("part", mime_type))
    genai_mod.types = gtypes
    google_pkg = types.ModuleType("google")
    google_pkg.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", gtypes)
    return calls


@pytest.fixture
def ledger_spy(monkeypatch):
    from agent_friday.services import egress_gate as eg
    rows = []
    monkeypatch.setattr(eg, "record_binary_egress", lambda provider, kind, action=None, reason="", byte_len=0, **kw: rows.append((provider, kind, action)))
    return rows


def _png_b64():
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()


def test_chat_send_keeps_the_image_on_the_machine_in_local_only(client, monkeypatch, fake_genai, ledger_spy):
    import agent_friday.routes.chat as chat
    monkeypatch.setattr(chat, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})
    monkeypatch.setattr(chat.core, "GEMINI_API_KEY", "k", raising=False)
    client.post("/api/chat/send", json={"message": "what is on my screen", "includeVision": True, "screenshot": _png_b64()})
    assert fake_genai == [], "the image went to Gemini under Local only"
    assert ("gemini", "vision_image", "block") in ledger_spy


def test_chat_send_records_every_image_that_leaves(client, monkeypatch, fake_genai, ledger_spy):
    import agent_friday.routes.chat as chat
    monkeypatch.setattr(chat, "_load_settings", lambda: {"model_routing": {"mode": "smart"}})
    monkeypatch.setattr(chat.core, "GEMINI_API_KEY", "k", raising=False)
    client.post("/api/chat/send", json={"message": "what is on my screen", "includeVision": True, "screenshot": _png_b64()})
    assert len(fake_genai) == 1
    assert ("gemini", "vision_image", "allow") in ledger_spy
