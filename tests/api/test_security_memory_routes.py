"""API tests for security, memory, governance, integrity, and epistemic routes.

Route groups under test:
  Memory:     GET  /api/memory/stats
              GET  /api/memory/ledger
              GET  /api/memory/health
              POST /api/memory/search
              POST /api/memory/rollback
              POST /api/memory/quarantine
  Governance: GET  /api/governance/privilege-log
              POST /api/governance/elevate
  Integrity:  GET  /api/integrity
              POST /api/integrity/verify
  Epistemic:  GET  /api/epistemic
  Security:   GET  /api/security/behavioral-report
              GET  /api/security/behavioral-history
              GET  /api/security/risk-score

Vault routes are all MCP-only (no HTTP routes exist) - not tested here.

The AUTOUSE _no_real_llm fixture from tests/api/conftest.py stubs all LLM
calls. Flask test_client requests originate from 127.0.0.1, which the
login_required decorator treats as loopback-trusted, so all routes are
reachable without session authentication.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from tests.conftest import CANNED_TEXT  # noqa: F401


# -----------------------------------------------------------------------
#  MEMORY /api/memory/stats
# -----------------------------------------------------------------------


class TestMemoryStats:
    """GET /api/memory/stats"""

    def test_returns_200(self, client):
        assert client.get("/api/memory/stats").status_code == 200

    def test_top_level_keys(self, client):
        data = client.get("/api/memory/stats").get_json()
        assert data["status"] == "ok"
        for key in ("working", "episodic", "semantic", "total", "episodes"):
            assert key in data, f"missing key: {key}"

    def test_conversations_key_present(self, client):
        data = client.get("/api/memory/stats").get_json()
        assert "conversations" in data
        assert isinstance(data["conversations"], dict)

    def test_counts_are_non_negative_integers(self, client):
        data = client.get("/api/memory/stats").get_json()
        for key in ("working", "episodic", "semantic", "total"):
            assert isinstance(data[key], int)
            assert data[key] >= 0


# -----------------------------------------------------------------------
#  MEMORY /api/memory/ledger
# -----------------------------------------------------------------------


class TestMemoryLedger:
    """GET /api/memory/ledger"""

    def test_returns_200_or_501(self, client):
        assert client.get("/api/memory/ledger").status_code in (200, 501)

    def test_shape_when_available(self, client):
        resp = client.get("/api/memory/ledger")
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["entries"], list)
        assert "chain_valid" in data
        assert isinstance(data["chain_entries"], int)

    def test_since_param_accepted(self, client):
        resp = client.get(f"/api/memory/ledger?since={time.time() - 3600}")
        assert resp.status_code in (200, 501)

    def test_limit_param_accepted(self, client):
        assert client.get("/api/memory/ledger?limit=10").status_code in (200, 501)


# -----------------------------------------------------------------------
#  MEMORY /api/memory/health
# -----------------------------------------------------------------------


class TestMemoryHealth:
    """GET /api/memory/health"""

    def test_returns_200_or_501(self, client):
        assert client.get("/api/memory/health").status_code in (200, 501)

    def test_shape_when_available(self, client):
        resp = client.get("/api/memory/health")
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        data = resp.get_json()
        assert data["status"] == "ok"
        for key in ("total_memories", "quarantined", "active",
                    "ledger_entries", "chain_valid"):
            assert key in data, f"missing key: {key}"

    def test_counts_are_integers(self, client):
        resp = client.get("/api/memory/health")
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        data = resp.get_json()
        for key in ("total_memories", "quarantined", "active", "ledger_entries"):
            assert isinstance(data[key], int) and data[key] >= 0

    def test_chain_valid_is_bool(self, client):
        resp = client.get("/api/memory/health")
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert isinstance(resp.get_json()["chain_valid"], bool)


# -----------------------------------------------------------------------
#  MEMORY /api/memory/search
# -----------------------------------------------------------------------


class TestMemorySearch:
    """POST /api/memory/search"""

    def test_happy_path_returns_200(self, client):
        assert client.post("/api/memory/search",
                           json={"query": "test query"}).status_code == 200

    def test_happy_path_shape(self, client):
        data = client.post("/api/memory/search",
                           json={"query": "hello world"}).get_json()
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_with_n_param(self, client):
        assert client.post("/api/memory/search",
                           json={"query": "something", "n": 3}).status_code == 200

    def test_missing_query_returns_400(self, client):
        resp = client.post("/api/memory/search", json={})
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_empty_query_returns_400(self, client):
        assert client.post("/api/memory/search",
                           json={"query": ""}).status_code == 400

    def test_whitespace_query_returns_400(self, client):
        assert client.post("/api/memory/search",
                           json={"query": "   "}).status_code == 400

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/memory/search", data="{bad json",
                           content_type="application/json")
        assert resp.status_code < 500

    def test_no_body_not_500(self, client):
        assert client.post("/api/memory/search").status_code < 500

    def test_available_key_is_bool(self, client):
        data = client.post("/api/memory/search",
                           json={"query": "probe"}).get_json()
        if data.get("status") == "ok":
            assert isinstance(data["available"], bool)


# -----------------------------------------------------------------------
#  MEMORY /api/memory/rollback
# -----------------------------------------------------------------------


class TestMemoryRollback:
    """POST /api/memory/rollback"""

    def test_missing_timestamp_returns_400(self, client):
        resp = client.post("/api/memory/rollback", json={})
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_non_numeric_timestamp_returns_400(self, client):
        resp = client.post("/api/memory/rollback",
                           json={"timestamp": "not-a-number"})
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert resp.status_code == 400

    def test_future_timestamp_shape(self, client):
        """Rolling back to a future timestamp removes nothing but must succeed."""
        resp = client.post("/api/memory/rollback",
                           json={"timestamp": time.time() + 9999})
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["rolled_back_keys"], list)
        assert isinstance(data["count"], int)

    def test_past_timestamp_shape(self, client):
        resp = client.post("/api/memory/rollback",
                           json={"timestamp": time.time() - 100})
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "cutoff_ts" in data
        assert isinstance(data["cutoff_iso"], str)

    def test_malformed_json_not_500(self, client):
        assert client.post("/api/memory/rollback", data="{broken",
                           content_type="application/json").status_code < 500

    def test_no_body_not_500(self, client):
        resp = client.post("/api/memory/rollback")
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert resp.status_code < 500


# -----------------------------------------------------------------------
#  MEMORY /api/memory/quarantine
# -----------------------------------------------------------------------


class TestMemoryQuarantine:
    """POST /api/memory/quarantine"""

    def test_missing_source_and_key_returns_400(self, client):
        resp = client.post("/api/memory/quarantine", json={})
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_quarantine_by_source_id_shape(self, client):
        resp = client.post("/api/memory/quarantine",
                           json={"source_id": "synthetic-source-abc",
                                 "reason": "pytest-test"})
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["quarantined_keys"], list)
        assert isinstance(data["count"], int)
        assert data["source_id"] == "synthetic-source-abc"
        assert data["reason"] == "pytest-test"

    def test_quarantine_by_key_shape(self, client):
        resp = client.post("/api/memory/quarantine",
                           json={"key": "synthetic-key-xyz"})
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "specific_key" in data

    def test_reason_optional(self, client):
        """reason field is optional; server supplies default 'manual_quarantine'."""
        resp = client.post("/api/memory/quarantine",
                           json={"source_id": "probe-source"})
        if resp.status_code == 501:
            pytest.skip("cognitive_memory module not available")
        assert resp.status_code == 200

    def test_malformed_json_not_500(self, client):
        assert client.post("/api/memory/quarantine", data="{broken",
                           content_type="application/json").status_code < 500

    def test_write_then_health_still_200(self, client):
        """After a quarantine call the health endpoint must still respond."""
        client.post("/api/memory/quarantine",
                    json={"source_id": "probe-health"})
        assert client.get("/api/memory/health").status_code in (200, 501)


# -----------------------------------------------------------------------
#  GOVERNANCE /api/governance/privilege-log
# -----------------------------------------------------------------------


class TestGovernancePrivilegeLog:
    """GET /api/governance/privilege-log"""

    def test_returns_200_or_501(self, client):
        assert client.get("/api/governance/privilege-log").status_code in (200, 501)

    def test_shape_when_available(self, client):
        resp = client.get("/api/governance/privilege-log")
        if resp.status_code == 501:
            pytest.skip("dynamic_rings module not available")
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["entries"], list)
        assert isinstance(data["count"], int)

    def test_count_matches_entries_length(self, client):
        resp = client.get("/api/governance/privilege-log")
        if resp.status_code == 501:
            pytest.skip("dynamic_rings module not available")
        data = resp.get_json()
        assert data["count"] == len(data["entries"])

    def test_since_param_accepted(self, client):
        resp = client.get(
            f"/api/governance/privilege-log?since={time.time() - 3600}")
        assert resp.status_code in (200, 501)

    def test_limit_param_accepted(self, client):
        assert client.get(
            "/api/governance/privilege-log?limit=5").status_code in (200, 501)


# -----------------------------------------------------------------------
#  GOVERNANCE /api/governance/elevate
# -----------------------------------------------------------------------


class TestGovernanceElevate:
    """POST /api/governance/elevate"""

    def test_missing_tool_returns_400(self, client):
        resp = client.post("/api/governance/elevate", json={"ring": 0})
        if resp.status_code == 501:
            pytest.skip("dynamic_rings module not available")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_empty_tool_returns_400(self, client):
        resp = client.post("/api/governance/elevate",
                           json={"tool": "", "ring": 0})
        if resp.status_code == 501:
            pytest.skip("dynamic_rings module not available")
        assert resp.status_code == 400

    def test_happy_path_ring0_shape(self, client):
        resp = client.post("/api/governance/elevate",
                           json={"tool": "read_file", "ring": 0,
                                 "reason": "pytest read test"})
        if resp.status_code == 501:
            pytest.skip("dynamic_rings module not available")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["tool"] == "read_file"
        assert isinstance(data["ring"], int)
        assert "granted" in data
        assert "ts" in data

    def test_full_payload_shape(self, client):
        resp = client.post("/api/governance/elevate",
                           json={"tool": "write_file",
                                 "ring": 1,
                                 "reason": "test elevation",
                                 "task_id": "task-pytest-001",
                                 "user_confirmed": True})
        if resp.status_code == 501:
            pytest.skip("dynamic_rings module not available")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["task_id"] == "task-pytest-001"
        assert "hmac" in data

    def test_elevate_count_non_negative(self, client):
        """After elevation the privilege-log count must be >= 0."""
        client.post("/api/governance/elevate",
                    json={"tool": "probe_log_tool", "ring": 0})
        log = client.get("/api/governance/privilege-log")
        if log.status_code == 501:
            pytest.skip("dynamic_rings module not available")
        assert log.get_json()["count"] >= 0

    def test_no_body_not_500(self, client):
        resp = client.post("/api/governance/elevate")
        if resp.status_code == 501:
            pytest.skip("dynamic_rings module not available")
        assert resp.status_code < 500

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/governance/elevate",
                           data="{bad json",
                           content_type="application/json")
        if resp.status_code == 501:
            pytest.skip("dynamic_rings module not available")
        assert resp.status_code < 500


# -----------------------------------------------------------------------
#  INTEGRITY /api/integrity
# -----------------------------------------------------------------------


class TestIntegrityGet:
    """GET /api/integrity"""

    def test_returns_200_or_501(self, client):
        assert client.get("/api/integrity").status_code in (200, 501)

    def test_shape_when_available(self, client):
        resp = client.get("/api/integrity")
        if resp.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["manifest"], dict)

    def test_manifest_top_level_keys(self, client):
        resp = client.get("/api/integrity")
        if resp.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        manifest = resp.get_json()["manifest"]
        for key in ("claws_hash", "claws_hmac", "ed25519_pubkey", "ed25519_sig",
                    "tool_manifest", "model_manifest", "generated_at", "version"):
            assert key in manifest, f"manifest missing key: {key}"

    def test_tool_manifest_is_list(self, client):
        resp = client.get("/api/integrity")
        if resp.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        assert isinstance(resp.get_json()["manifest"]["tool_manifest"], list)

    def test_generated_at_is_nonempty_string(self, client):
        resp = client.get("/api/integrity")
        if resp.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        ga = resp.get_json()["manifest"]["generated_at"]
        assert isinstance(ga, str) and len(ga) > 0

    def test_successive_calls_same_keys(self, client):
        """Two successive GETs must return the same manifest key set."""
        r1 = client.get("/api/integrity")
        r2 = client.get("/api/integrity")
        if r1.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        assert set(r1.get_json()["manifest"]) == set(r2.get_json()["manifest"])


# -----------------------------------------------------------------------
#  INTEGRITY /api/integrity/verify
# -----------------------------------------------------------------------


class TestIntegrityVerify:
    """POST /api/integrity/verify"""

    def test_missing_manifest_returns_400(self, client):
        resp = client.post("/api/integrity/verify", json={})
        if resp.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_null_manifest_returns_400(self, client):
        resp = client.post("/api/integrity/verify", json={"manifest": None})
        if resp.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        assert resp.status_code == 400

    def test_valid_manifest_returns_200(self, client):
        fresh = client.get("/api/integrity")
        if fresh.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        manifest = fresh.get_json()["manifest"]
        assert client.post("/api/integrity/verify",
                           json={"manifest": manifest}).status_code == 200

    def test_valid_manifest_verify_shape(self, client):
        fresh = client.get("/api/integrity")
        if fresh.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        data = client.post("/api/integrity/verify",
                           json={"manifest": fresh.get_json()["manifest"]}).get_json()
        assert data["status"] == "ok"
        assert "valid" in data
        assert "checks" in data
        assert isinstance(data["checks"], (list, dict))

    def test_tampered_manifest_not_valid(self, client):
        """A manifest with a corrupted hash must not verify as valid and must not 500."""
        fresh = client.get("/api/integrity")
        if fresh.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        manifest = dict(fresh.get_json()["manifest"])
        manifest["claws_hash"] = "0" * 64
        resp = client.post("/api/integrity/verify", json={"manifest": manifest})
        assert resp.status_code < 500
        data = resp.get_json()
        assert data["status"] in ("ok", "error")
        if data["status"] == "ok":
            assert data["valid"] is False

    def test_malformed_json_not_500(self, client):
        assert client.post("/api/integrity/verify", data="{broken",
                           content_type="application/json").status_code < 500

    def test_no_body_not_500(self, client):
        resp = client.post("/api/integrity/verify")
        if resp.status_code == 501:
            pytest.skip("proof_of_integrity module not available")
        assert resp.status_code < 500


# -----------------------------------------------------------------------
#  EPISTEMIC /api/epistemic
# -----------------------------------------------------------------------


class TestEpistemic:
    """GET /api/epistemic"""

    def test_returns_200(self, client):
        assert client.get("/api/epistemic").status_code == 200

    def test_status_ok(self, client):
        assert client.get("/api/epistemic").get_json()["status"] == "ok"

    def test_top_level_keys(self, client):
        data = client.get("/api/epistemic").get_json()
        assert "overall_score" in data
        assert "total_turns_scored" in data
        assert "dimensions" in data

    def test_dimensions_is_dict(self, client):
        assert isinstance(
            client.get("/api/epistemic").get_json()["dimensions"], dict)

    def test_dimension_keys_present(self, client):
        dims = client.get("/api/epistemic").get_json()["dimensions"]
        for key in ("information_gain", "pushback_rate",
                    "socratic_ratio", "independence_fostering"):
            assert key in dims, f"missing dimension: {key}"

    def test_overall_score_is_numeric(self, client):
        data = client.get("/api/epistemic").get_json()
        assert isinstance(data["overall_score"], (int, float))

    def test_total_turns_is_non_negative_int(self, client):
        data = client.get("/api/epistemic").get_json()
        assert isinstance(data["total_turns_scored"], int)
        assert data["total_turns_scored"] >= 0

    def test_dimension_values_are_numeric(self, client):
        dims = client.get("/api/epistemic").get_json()["dimensions"]
        for k, v in dims.items():
            assert isinstance(v, (int, float)), f"dimension {k} is not numeric"

    def test_idempotent_keys(self, client):
        r1 = client.get("/api/epistemic").get_json()
        r2 = client.get("/api/epistemic").get_json()
        assert set(r1.keys()) == set(r2.keys())
        assert set(r1["dimensions"].keys()) == set(r2["dimensions"].keys())


# -----------------------------------------------------------------------
#  SECURITY /api/security/behavioral-report
# -----------------------------------------------------------------------


class TestBehavioralReport:
    """GET /api/security/behavioral-report

    Note: this route is registered TWICE in server.py (once without
    @login_required around line 9145, once with it around line 9424).
    Flask's first-registered handler wins; that one spreads report keys
    at the top level rather than nesting under 'report'.
    """

    def test_returns_200(self, client):
        assert client.get("/api/security/behavioral-report").status_code == 200

    def test_status_key_present(self, client):
        data = client.get("/api/security/behavioral-report").get_json()
        assert "status" in data
        # "no_data" is the valid status when no agent loops have run yet
        assert data["status"] in ("ok", "unavailable", "error", "no_data")

    def test_response_is_dict(self, client):
        data = client.get("/api/security/behavioral-report").get_json()
        assert isinstance(data, dict) and len(data) >= 1

    def test_shape_when_available(self, client):
        data = client.get("/api/security/behavioral-report").get_json()
        if data["status"] == "unavailable":
            pytest.skip("behavioral_monitor not loaded")
        # First-registered handler (no login_required) spreads report at top level.
        # "no_data" is returned when no agent loops have been scored yet.
        assert data["status"] in ("ok", "no_data")


# -----------------------------------------------------------------------
#  SECURITY /api/security/behavioral-history
# -----------------------------------------------------------------------


class TestBehavioralHistory:
    """GET /api/security/behavioral-history"""

    def test_returns_200(self, client):
        assert client.get("/api/security/behavioral-history").status_code == 200

    def test_status_key(self, client):
        data = client.get("/api/security/behavioral-history").get_json()
        assert data["status"] in ("ok", "unavailable")

    def test_shape_when_available(self, client):
        data = client.get("/api/security/behavioral-history").get_json()
        if data["status"] == "unavailable":
            pytest.skip("behavioral_monitor not loaded")
        assert isinstance(data["count"], int) and data["count"] >= 0
        assert isinstance(data["sessions"], list)

    def test_unavailable_has_count_zero(self, client):
        data = client.get("/api/security/behavioral-history").get_json()
        if data["status"] != "unavailable":
            pytest.skip("behavioral_monitor IS loaded")
        assert data.get("count") == 0

    def test_average_composite_numeric_when_available(self, client):
        data = client.get("/api/security/behavioral-history").get_json()
        if data["status"] == "unavailable":
            pytest.skip("behavioral_monitor not loaded")
        assert isinstance(data.get("average_composite"), (int, float))


# -----------------------------------------------------------------------
#  SECURITY /api/security/risk-score
# -----------------------------------------------------------------------


class TestBehavioralRiskScore:
    """GET /api/security/risk-score"""

    def test_returns_200(self, client):
        assert client.get("/api/security/risk-score").status_code == 200

    def test_status_key(self, client):
        data = client.get("/api/security/risk-score").get_json()
        assert data["status"] in ("ok", "unavailable")

    def test_shape_when_available(self, client):
        data = client.get("/api/security/risk-score").get_json()
        if data["status"] == "unavailable":
            pytest.skip("behavioral_monitor not loaded")
        assert "composite" in data and "risk_level" in data

    def test_composite_is_numeric_when_available(self, client):
        data = client.get("/api/security/risk-score").get_json()
        if data["status"] == "unavailable":
            pytest.skip("behavioral_monitor not loaded")
        assert isinstance(data["composite"], (int, float))

    def test_risk_level_is_nonempty_string_when_available(self, client):
        data = client.get("/api/security/risk-score").get_json()
        if data["status"] == "unavailable":
            pytest.skip("behavioral_monitor not loaded")
        assert isinstance(data["risk_level"], str) and len(data["risk_level"]) > 0

    def test_unavailable_has_zero_composite(self, client):
        data = client.get("/api/security/risk-score").get_json()
        if data["status"] != "unavailable":
            pytest.skip("behavioral_monitor IS loaded")
        assert data.get("composite") == 0.0
        assert data.get("risk_level") == "none"


# -----------------------------------------------------------------------
#  CROSS-CUTTING ROBUSTNESS
# -----------------------------------------------------------------------


class TestRobustness:
    """No route in this group must 500 on bad input."""

    @pytest.mark.parametrize("path", [
        "/api/memory/stats",
        "/api/memory/health",
        "/api/memory/ledger",
        "/api/governance/privilege-log",
        "/api/integrity",
        "/api/epistemic",
        "/api/security/behavioral-report",
        "/api/security/behavioral-history",
        "/api/security/risk-score",
    ])
    def test_get_routes_never_500(self, client, path):
        resp = client.get(path)
        assert resp.status_code < 500, (
            f"{path} returned {resp.status_code}: {resp.data[:200]}"
        )

    @pytest.mark.parametrize("path", [
        "/api/memory/search",
        "/api/memory/rollback",
        "/api/memory/quarantine",
        "/api/governance/elevate",
        "/api/integrity/verify",
    ])
    def test_post_routes_empty_body_not_500(self, client, path):
        resp = client.post(path, json={})
        assert resp.status_code < 500, (
            f"{path} empty body returned {resp.status_code}: {resp.data[:200]}"
        )

    @pytest.mark.parametrize("path", [
        "/api/memory/search",
        "/api/memory/rollback",
        "/api/memory/quarantine",
        "/api/governance/elevate",
        "/api/integrity/verify",
    ])
    def test_post_routes_broken_json_not_500(self, client, path):
        resp = client.post(path, data="{not valid json",
                           content_type="application/json")
        assert resp.status_code < 500, (
            f"{path} broken JSON returned {resp.status_code}: {resp.data[:200]}"
        )

    @pytest.mark.parametrize("path", [
        "/api/memory/search",
        "/api/memory/rollback",
        "/api/memory/quarantine",
        "/api/governance/elevate",
        "/api/integrity/verify",
    ])
    def test_post_routes_wrong_content_type_not_500(self, client, path):
        resp = client.post(path, data="hello")
        assert resp.status_code < 500, (
            f"{path} wrong content-type returned {resp.status_code}"
        )

    def test_all_get_routes_return_json(self, client):
        paths = [
            "/api/memory/stats",
            "/api/memory/health",
            "/api/memory/ledger",
            "/api/governance/privilege-log",
            "/api/integrity",
            "/api/epistemic",
            "/api/security/behavioral-report",
            "/api/security/behavioral-history",
            "/api/security/risk-score",
        ]
        for path in paths:
            data = client.get(path).get_json()
            assert data is not None, f"{path} did not return JSON"
            assert isinstance(data, dict), f"{path} returned non-dict JSON"
