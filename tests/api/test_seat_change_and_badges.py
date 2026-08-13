"""API acceptance for B1 (model badge persisted) and B2 (a direct
settings.json edit produces a visible system line next turn — the exact
acceptance criterion from the spec, mirroring the silent 10:16:59 flip).
"""
from __future__ import annotations

import json

import agent_friday.core as core
import agent_friday.routes.chat as chat_mod
from agent_friday.services import seat_transparency as st


def _stub(messages, **kwargs):
    return "All quiet.", []


def _force_settings_reread():
    core._SETTINGS_CACHE["value"] = None
    core._SETTINGS_CACHE["ts"] = 0.0


def _reset_seat_state():
    try:
        st._STATE_FILE.unlink()
    except FileNotFoundError:
        pass


class TestB1ModelBadge:
    def test_friday_msg_carries_model_and_seat_and_persists(self, client, monkeypatch):
        monkeypatch.setattr(chat_mod, "_call_claude_agent", _stub)
        resp = client.post("/api/chat", json={"message": "hello"})
        data = resp.get_json()
        assert data["friday_msg"].get("model"), "assistant message must carry the model id"
        assert data["friday_msg"].get("seat") in ("local", "cloud", "openai")
        assert data.get("model") == data["friday_msg"]["model"]
        # Survives reload — the badge is persisted, not client-session state.
        hist = client.get("/api/chat/history").get_json()["messages"]
        last_friday = [m for m in hist if m.get("role") == "friday"][-1]
        assert last_friday.get("model") == data["friday_msg"]["model"]
        assert last_friday.get("seat") == data["friday_msg"]["seat"]


class TestB2DirectFileEdit:
    def test_direct_settings_edit_produces_system_line_next_turn(self, client, monkeypatch):
        monkeypatch.setattr(chat_mod, "_call_claude_agent", _stub)
        _reset_seat_state()

        # Turn 1 seeds the observed-seat state.
        client.post("/api/chat", json={"message": "hi"})

        # Direct file edit — no API, no UI. The 10:16:59 mechanism.
        raw = json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8"))
        routing = raw.setdefault("model_routing", {})
        routing["mode"] = "local_only"
        routing["local_model"] = "gemma4:latest"
        core.SETTINGS_FILE.write_text(json.dumps(raw), encoding="utf-8")
        _force_settings_reread()

        # Next turn: visible system line + seat_events in the response.
        resp = client.post("/api/chat", json={"message": "hi again"})
        data = resp.get_json()
        assert data.get("seat_events"), "seat flip must surface in the response"
        keys = {e["key"] for e in data["seat_events"]}
        assert "model_routing.mode" in keys

        hist = client.get("/api/chat/history").get_json()["messages"]
        sys_lines = [m for m in hist if m.get("role") == "system"
                     and m.get("kind") == "seat_change"]
        assert sys_lines, "seat change must be persisted as a system line"
        assert "local_only" in sys_lines[-1]["text"]

        # Restore for other tests.
        routing["mode"] = "cloud_only"
        core.SETTINGS_FILE.write_text(json.dumps(raw), encoding="utf-8")
        _force_settings_reread()

    def test_system_lines_never_replayed_into_model_context(self, client, monkeypatch):
        captured = {}

        def capture(messages, **kwargs):
            captured["messages"] = messages
            return "ok", []

        # Plant a persisted system line, then chat.
        core.CHAT_HISTORY.append({
            "id": "sysline-test", "timestamp": "2099-01-01T00:00:00",
            "role": "system", "kind": "seat_change",
            "text": "⚙ Seat change: routing mode cloud_only → local_only.",
            "pinned": True,
        })
        monkeypatch.setattr(chat_mod, "_call_claude_agent", capture)
        client.post("/api/chat", json={"message": "hello"})
        flat = json.dumps(captured.get("messages", []))
        assert "Seat change" not in flat
        core.CHAT_HISTORY[:] = [m for m in core.CHAT_HISTORY
                                if m.get("id") != "sysline-test"]
