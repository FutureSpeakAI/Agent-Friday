"""API-layer exemplar: exercises the three route flavours the rest of the API
suite follows — PURE (compute), FILE (disk roundtrip in the isolated home), and
LLM (mocked via the autouse stub). Uses the Flask test_client; no live server."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from tests.conftest import CANNED_TEXT


# ── PURE ──────────────────────────────────────────────────────────────────────
class TestPureRoutes:
    def test_vibe_code_presets(self, client):
        resp = client.get("/api/vibe-code/presets")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert any(p["name"] == "Security Audit" for p in data["presets"])

    def test_model_stats_shape(self, client):
        resp = client.get("/api/model-stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "mode" in data


# ── FILE roundtrip (writes land under the isolated temp home) ─────────────────
class TestTodosRoundtrip:
    def test_add_then_list(self, client):
        add = client.post("/api/todos", json={"title": "Write more tests"})
        assert add.status_code == 200
        listed = client.get("/api/todos").get_json()
        titles = [t.get("title") for t in listed["todos"]]
        assert "Write more tests" in titles

    def test_empty_title_rejected(self, client):
        resp = client.post("/api/todos", json={"title": "   "})
        assert resp.status_code == 400

    def test_complete_and_delete_lifecycle(self, client):
        tid = client.post("/api/todos", json={"title": "ephemeral"}).get_json()["todo"]["id"]
        assert client.post(f"/api/todos/{tid}/complete").status_code == 200
        assert client.delete(f"/api/todos/{tid}").status_code == 200
        remaining = [t["id"] for t in client.get("/api/todos").get_json()["todos"]]
        assert tid not in remaining


# ── LLM route (autouse stub → no paid call) ───────────────────────────────────
class TestChatPipeline:
    def test_chat_returns_response(self, client):
        resp = client.post("/api/chat", json={"message": "Tell me something interesting."})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "response" in data
        # The reply came from the stubbed model, never the network.
        assert isinstance(data["response"], str) and data["response"]

    def test_chat_appends_history(self, client):
        client.post("/api/chat", json={"message": "remember this turn"})
        hist = client.get("/api/chat/history").get_json()
        # history endpoint returns the in-memory CHAT_HISTORY
        blob = str(hist)
        assert "remember this turn" in blob

    def test_chat_empty_message_is_handled(self, client):
        resp = client.post("/api/chat", json={"message": ""})
        # Must not 500 — empty input is a normal edge case.
        assert resp.status_code < 500


# ── Negative / robustness ─────────────────────────────────────────────────────
class TestRobustness:
    def test_unknown_route_404(self, client):
        assert client.get("/api/this-does-not-exist").status_code == 404

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/todos", data="{not json",
                           content_type="application/json")
        assert resp.status_code < 500


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
