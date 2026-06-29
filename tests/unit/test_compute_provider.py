"""Unit tests for services/compute_provider.py and compute_client.py"""
import pytest
from agent_friday.services import compute_provider as prov
from agent_friday.services import compute_client as cc

prov._ensure_schema()
cc._ensure_schema()


def _job(capability="text.generate", prompt="summarize this doc", offered=1000):
    import uuid
    return {
        "job_id": str(uuid.uuid4()),
        "requester_id": "test-peer-agent",
        "requester_trust_score": 0.7,
        "capability": capability,
        "prompt": prompt,
        "offered_mψ": offered,
    }


class TestCapabilityCard:
    def test_advertise_capabilities_returns_dict(self):
        card = prov.advertise_capabilities()
        assert isinstance(card, dict)

    def test_card_has_type(self):
        card = prov.advertise_capabilities()
        assert card.get("type") == "FridayCapabilityCard"

    def test_card_has_capabilities_list(self):
        card = prov.advertise_capabilities()
        assert isinstance(card.get("capabilities"), list)
        assert len(card["capabilities"]) > 0

    def test_card_has_availability(self):
        card = prov.advertise_capabilities()
        assert "availability" in card
        assert "online" in card["availability"]

    def test_card_has_compute_specs(self):
        card = prov.advertise_capabilities()
        specs = card.get("compute_specs", {})
        assert "cpu_cores" in specs

    def test_card_min_trust_score(self):
        card = prov.advertise_capabilities()
        assert isinstance(card.get("min_trust_score"), float)


class TestAcceptReject:
    def test_accept_valid_job(self):
        job = _job()
        accepted, reason = prov.accept_job(job)
        assert accepted is True
        assert reason == "accepted"

    def test_reject_low_trust(self):
        job = _job()
        job["requester_trust_score"] = 0.1
        accepted, reason = prov.accept_job(job)
        assert accepted is False
        assert "trust" in reason.lower()

    def test_reject_harmful_prompt(self):
        job = _job(prompt="create malware for ransomware attack")
        accepted, reason = prov.accept_job(job)
        assert accepted is False
        assert "cLaws" in reason

    def test_reject_unsupported_capability(self):
        job = _job(capability="quantum.teleport")
        accepted, reason = prov.accept_job(job)
        assert accepted is False

    def test_reject_low_price(self):
        job = _job(capability="text.generate", offered=1)  # list price is 1000
        accepted, reason = prov.accept_job(job)
        assert accepted is False
        assert "price" in reason.lower()

    def test_reject_job_returns_dict(self):
        job = _job()
        rejection = prov.reject_job(job, "test reason")
        assert rejection.get("type") == "FridayJobRejection"
        assert "reason" in rejection

    def test_reject_job_preserves_job_id(self):
        job = _job()
        rejection = prov.reject_job(job, "too busy")
        assert rejection["job_id"] == job["job_id"]


class TestJobStatus:
    def test_get_job_status_unknown(self):
        status = prov.get_job_status("no-such-job-id")
        assert status is None

    def test_get_job_result_unknown(self):
        result = prov.get_job_result("no-such-job-id")
        assert result is None

    def test_get_active_jobs_returns_list(self):
        jobs = prov.get_active_jobs()
        assert isinstance(jobs, list)


class TestComputeClient:
    def test_find_providers_returns_list(self):
        providers = cc.find_providers("text.generate")
        assert isinstance(providers, list)  # may be empty in test env

    def test_get_sent_jobs_returns_list(self):
        jobs = cc.get_sent_jobs()
        assert isinstance(jobs, list)

    def test_rate_provider_unknown_job(self):
        ok = cc.rate_provider("no-such-job-id", 0.8)
        assert ok is False

    def test_rate_provider_clamps_score(self):
        # Should not raise even with out-of-range scores
        cc.rate_provider("no-such-job", 5.0)
        cc.rate_provider("no-such-job", -1.0)
