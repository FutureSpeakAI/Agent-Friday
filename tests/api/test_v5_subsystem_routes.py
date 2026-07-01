"""API-route tests for the v5 subsystems: learning loop, user model, memory
dreaming, soul, orchestrator, work-log, health, models, and settings.

Requests originate from 127.0.0.1 (the api conftest client), which Friday's auth
treats as the trusted local user — so routes are reachable without a login.
Response SHAPES and status codes are asserted; the model seam is stubbed by the
autouse _no_real_llm fixture.
"""
from __future__ import annotations

import pytest


# ── Health & models ───────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        assert "uptime_seconds" in body
        assert "vault" in body
        assert "governance" in body

    def test_health_reports_ring_counts(self, client):
        body = client.get("/api/health").get_json()
        assert "tool_counts_by_ring" in body["governance"]


class TestModelsCatalog:
    def test_models_catalog_shape(self, client):
        r = client.get("/api/models")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        assert "roles" in body and "models" in body and "providers" in body
        assert "selected" in body


# ── Settings — GET/POST, atomic persistence, delta merge ─────────────────────

class TestSettings:
    def test_get_settings(self, client):
        r = client.get("/api/settings")
        assert r.status_code == 200
        assert "settings" in r.get_json()

    def test_post_settings_persists_delta(self, client):
        r = client.post("/api/settings", json={"settings": {"agent_name": "TEST-BOT"}})
        assert r.status_code == 200
        assert r.get_json()["settings"]["agent_name"] == "TEST-BOT"
        # Read-back confirms persistence.
        assert client.get("/api/settings").get_json()["settings"]["agent_name"] == "TEST-BOT"

    def test_post_non_string_personality_rejected(self, client):
        r = client.post("/api/settings", json={"personality": {"not": "a string"}})
        assert r.status_code == 400


# ── Learning loop routes ──────────────────────────────────────────────────────

class TestLearningRoutes:
    def test_state(self, client):
        r = client.get("/api/learning/state")
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_skills(self, client):
        r = client.get("/api/learning/skills")
        assert r.status_code == 200
        assert "skills" in r.get_json()

    def test_observe_requires_fields(self, client):
        r = client.post("/api/learning/observe", json={"prompt": "x"})
        assert r.status_code == 400

    def test_observe_records(self, client):
        r = client.post("/api/learning/observe",
                        json={"task_type": "code", "success": True, "approach": "a"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_epoch(self, client):
        r = client.post("/api/learning/epoch")
        assert r.status_code == 200


# ── User model routes ─────────────────────────────────────────────────────────

class TestUserModelRoutes:
    def test_get_profile(self, client):
        r = client.get("/api/user-model")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True and "profile" in body

    def test_add_fact_requires_text(self, client):
        r = client.post("/api/user-model/fact", json={"category": "preference"})
        assert r.status_code == 400

    def test_add_fact(self, client):
        r = client.post("/api/user-model/fact",
                        json={"category": "preference", "text": "prefers dark mode"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_forget(self, client):
        r = client.post("/api/user-model/forget", json={})
        assert r.status_code == 200


# ── Memory dreaming routes ────────────────────────────────────────────────────

class TestDreamingRoutes:
    def test_state(self, client):
        r = client.get("/api/memory/dream/state")
        assert r.status_code == 200

    def test_list_dreams(self, client):
        r = client.get("/api/memory/dreams?n=3")
        assert r.status_code == 200
        assert "dreams" in r.get_json()

    def test_run_dream_invalid_day(self, client):
        r = client.post("/api/memory/dream", json={"day": "../evil"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is False  # validation rejects traversal day


# ── Soul routes ───────────────────────────────────────────────────────────────

class TestSoulRoutes:
    @pytest.fixture(autouse=True)
    def _restore_soul(self):
        # These route tests write to the REAL ~/.friday/SOUL.md (routes use the
        # module's live path). _load_agent_personality() prioritizes SOUL.md, so
        # leaving a file behind would shadow the personality store for OTHER API
        # tests (e.g. the wiki personality round-trip). Snapshot + restore.
        from agent_friday.services import soul
        existed = soul.SOUL_FILE.exists()
        prior = soul.SOUL_FILE.read_bytes() if existed else None
        try:
            yield
        finally:
            try:
                if prior is not None:
                    soul.SOUL_FILE.write_bytes(prior)
                elif soul.SOUL_FILE.exists():
                    soul.SOUL_FILE.unlink()
                soul._invalidate()
            except Exception:
                pass

    def test_get_soul(self, client):
        r = client.get("/api/soul")
        assert r.status_code == 200
        body = r.get_json()
        assert "text" in body and "default" in body

    def test_post_empty_soul_rejected(self, client):
        r = client.post("/api/soul", json={"text": ""})
        assert r.status_code == 400

    def test_post_and_reset_soul(self, client):
        assert client.post("/api/soul", json={"text": "# Custom\npersona body"}).status_code == 200
        assert client.post("/api/soul/reset").status_code == 200

    def test_history(self, client):
        r = client.get("/api/soul/history")
        assert r.status_code == 200
        assert "versions" in r.get_json()


# ── Orchestrator & work-log routes ────────────────────────────────────────────

class TestOrchestratorRoutes:
    def test_status(self, client):
        r = client.get("/api/orchestrator/status")
        assert r.status_code == 200

    def test_workers_list(self, client):
        r = client.get("/api/orchestrator/workers")
        assert r.status_code == 200

    def test_unknown_worker_result(self, client):
        r = client.get("/api/orchestrator/workers/no-such-worker-id")
        assert r.status_code in (200, 404)


class TestWorkLogRoutes:
    def test_get_log(self, client):
        r = client.get("/api/work-log")
        assert r.status_code == 200

    def test_missing_entry(self, client):
        r = client.get("/api/work-log/no-such-work-id")
        assert r.status_code in (200, 404)
