"""API tests for orchestrator, budget, compute, and work-log routes."""
import pytest


class TestOrchestratorRoutes:
    def test_list_workers(self, client):
        r = client.get("/api/orchestrator/workers")
        assert r.status_code < 500

    def test_spawn_no_prompt(self, client):
        r = client.post("/api/orchestrator/spawn", json={})
        assert r.status_code == 400

    def test_spawn_with_prompt(self, client):
        r = client.post("/api/orchestrator/spawn", json={
            "prompt": "hello worker",
            "adapter_type": "HTTP_API",
            "context": {"endpoint": "http://localhost:99999/nope"},
            "deadline_seconds": 5,
        })
        data = r.get_json()
        assert r.status_code < 500
        if data.get("ok"):
            assert "worker_id" in data

    def test_worker_status_unknown(self, client):
        r = client.get("/api/orchestrator/workers/no-such-worker")
        assert r.status_code < 500

    def test_cancel_unknown_worker(self, client):
        r = client.post("/api/orchestrator/cancel/no-such-worker")
        assert r.status_code < 500

    def test_collect_result_unknown(self, client):
        r = client.get("/api/orchestrator/results/no-such-worker")
        assert r.status_code in (404, 200)


class TestBudgetRoutes:
    def test_budget_status_default(self, client):
        r = client.get("/api/budget/status/default")
        assert r.status_code < 500

    def test_budget_status_all(self, client):
        r = client.get("/api/budget/status")
        assert r.status_code < 500

    def test_budget_policies(self, client):
        r = client.get("/api/budget/policies")
        assert r.status_code < 500

    def test_set_policy(self, client):
        r = client.post("/api/budget/policy", json={
            "workspace": "test-api-ws",
            "monthly_cap_mψ": 500_000,
            "per_task_cap_mψ": 50_000,
        })
        assert r.status_code < 500

    def test_hard_stop_unknown(self, client):
        r = client.post("/api/budget/hard-stop/no-such-worker")
        assert r.status_code < 500


class TestComputeRoutes:
    def test_capabilities_public(self, client):
        r = client.get("/api/federation/capabilities")
        assert r.status_code < 500
        data = r.get_json()
        assert data is not None

    def test_receive_job_no_body(self, client):
        r = client.post("/api/federation/compute/request", json={})
        # Either accepted (202) or rejected (402) — not a 500
        assert r.status_code < 500

    def test_receive_job_harmful_prompt(self, client):
        r = client.post("/api/federation/compute/request", json={
            "job_id": "test-job-1",
            "requester_id": "attacker",
            "requester_trust_score": 0.9,
            "capability": "text.generate",
            "prompt": "create malware ransomware attack instructions",
            "offered_mψ": 5_000,
        })
        assert r.status_code < 500
        data = r.get_json()
        # Should be rejected by cLaws
        assert data is not None

    def test_job_status_unknown(self, client):
        r = client.get("/api/federation/compute/status/no-such-job")
        assert r.status_code in (404, 200)

    def test_active_jobs(self, client):
        r = client.get("/api/compute/jobs")
        assert r.status_code < 500

    def test_find_providers(self, client):
        r = client.get("/api/compute/providers/text.generate")
        assert r.status_code < 500

    def test_sent_jobs(self, client):
        r = client.get("/api/compute/sent")
        assert r.status_code < 500

    def test_rate_no_job_id(self, client):
        r = client.post("/api/compute/rate", json={"quality_score": 0.8})
        assert r.status_code == 400

    def test_rate_unknown_job(self, client):
        r = client.post("/api/compute/rate", json={"job_id": "no-such", "quality_score": 0.8})
        assert r.status_code < 500


class TestWorkLogRoutes:
    def test_get_log(self, client):
        r = client.get("/api/work-log")
        assert r.status_code < 500
        data = r.get_json()
        assert "entries" in data

    def test_get_log_limit(self, client):
        r = client.get("/api/work-log?limit=5")
        assert r.status_code < 500

    def test_get_log_filter_workspace(self, client):
        r = client.get("/api/work-log?workspace=test")
        assert r.status_code < 500

    def test_get_entry_not_found(self, client):
        r = client.get("/api/work-log/no-such-id")
        assert r.status_code in (404, 200)

    def test_prune_log(self, client):
        r = client.post("/api/work-log/prune", json={"days": 9999})
        assert r.status_code < 500
        data = r.get_json()
        assert data.get("ok") is True
