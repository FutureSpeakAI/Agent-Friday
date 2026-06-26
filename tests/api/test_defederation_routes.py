"""API route tests for defederation and content policy endpoints."""
import base64
import json
import pytest


def auth_headers():
    import core
    creds = base64.b64encode(
        f"{core.FRIDAY_USERNAME}:{core.FRIDAY_PASSWORD}".encode()
    ).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


@pytest.fixture(autouse=True, scope="session")
def _init_schemas():
    from services import defederation
    from services import content_policies
    defederation._ensure_schema()
    content_policies._ensure_schema()


@pytest.fixture
def client():
    import server as s
    s.app.config["TESTING"] = True
    with s.app.test_client() as c:
        yield c


def _evidence():
    return [{"content_hash": "abc123", "timestamp": "2026-06-26T00:00:00Z",
             "violation_type": "test"}]


# ─────────────────────────────────────────────────────────────────────────────
#  DEFEDERATION: Assessments
# ─────────────────────────────────────────────────────────────────────────────

class TestAssessmentRoutes:
    def test_list_assessments_requires_param(self, client):
        r = client.get("/api/defederation/assessments", headers=auth_headers())
        assert r.status_code == 400

    def test_list_assessments_by_agent(self, client):
        r = client.get(
            "/api/defederation/assessments?agent_pubkey=some_agent",
            headers=auth_headers()
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "assessments" in data

    def test_list_assessments_by_assessor(self, client):
        r = client.get(
            "/api/defederation/assessments?assessor_pubkey=some_assessor",
            headers=auth_headers()
        )
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data.get("assessments"), list)

    def test_create_assessment_success(self, client):
        payload = {
            "agent_pubkey": "route_target_001",
            "evidence": _evidence(),
            "harm_category": "coordinated_harassment",
            "severity_score": 0.5,
            "recommendation": "MONITOR",
            "reasoning": "test reasoning from route test",
        }
        r = client.post("/api/defederation/assess",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert "assessment" in data
        assert "consensus" in data

    def test_create_assessment_missing_agent(self, client):
        payload = {
            "evidence": _evidence(),
            "harm_category": "H1",
            "severity_score": 0.5,
            "recommendation": "MONITOR",
            "reasoning": "no agent",
        }
        r = client.post("/api/defederation/assess",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 400

    def test_create_assessment_empty_evidence(self, client):
        payload = {
            "agent_pubkey": "route_target_002",
            "evidence": [],
            "harm_category": "H1",
            "severity_score": 0.5,
            "recommendation": "MONITOR",
            "reasoning": "no evidence",
        }
        r = client.post("/api/defederation/assess",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 400

    def test_create_assessment_invalid_harm_category(self, client):
        payload = {
            "agent_pubkey": "route_target_003",
            "evidence": _evidence(),
            "harm_category": "political_disagreement",
            "severity_score": 0.5,
            "recommendation": "DEFEDERATE",
            "reasoning": "wrong politics",
        }
        r = client.post("/api/defederation/assess",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 400
        data = r.get_json()
        assert "valid_categories" in data

    def test_create_assessment_invalid_recommendation(self, client):
        payload = {
            "agent_pubkey": "route_target_004",
            "evidence": _evidence(),
            "harm_category": "H2",
            "severity_score": 0.5,
            "recommendation": "BAN_FOREVER",
            "reasoning": "bad rec",
        }
        r = client.post("/api/defederation/assess",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 400

    def test_get_consensus(self, client):
        r = client.get("/api/defederation/consensus/some_test_pubkey",
                       headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "consensus" in data
        assert "agent_pubkey" in data

    def test_get_consensus_for_created_agent(self, client):
        agent = "consensus_route_test_agent"
        payload = {
            "agent_pubkey": agent,
            "evidence": _evidence(),
            "harm_category": "H3",
            "severity_score": 0.6,
            "recommendation": "RESTRICT",
            "reasoning": "consensus route test",
        }
        client.post("/api/defederation/assess",
                    data=json.dumps(payload), headers=auth_headers())
        r = client.get(f"/api/defederation/consensus/{agent}", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert data["consensus"]["agent_pubkey"] == agent
        assert data["consensus"]["status"] in ("MONITOR", "RESTRICT", "DEFEDERATE", "CLEAN")

    def test_withdraw_missing_returns_404(self, client):
        r = client.post("/api/defederation/withdraw/nonexistent-id-000",
                        data=json.dumps({}), headers=auth_headers())
        assert r.status_code == 404

    def test_withdraw_own_assessment(self, client):
        # Create then immediately withdraw
        from services import federation as fed
        identity = fed.get_identity()
        agent_id = identity.get("agent_id", "local")
        payload = {
            "agent_pubkey": "withdraw_route_target",
            "evidence": _evidence(),
            "harm_category": "H4",
            "severity_score": 0.8,
            "recommendation": "MONITOR",
            "reasoning": "will be withdrawn",
        }
        r = client.post("/api/defederation/assess",
                        data=json.dumps(payload), headers=auth_headers())
        data = r.get_json()
        assessment_id = data["assessment"]["id"]

        wr = client.post(
            f"/api/defederation/withdraw/{assessment_id}",
            data=json.dumps({"assessor_pubkey": agent_id}),
            headers=auth_headers()
        )
        assert wr.status_code == 200
        wd = wr.get_json()
        assert wd["ok"] is True
        assert wd["assessment"]["withdrawn_at"] is not None

    def test_detect_patterns(self, client):
        r = client.get("/api/defederation/patterns/test_agent_patterns",
                       headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "patterns" in data
        assert "harassment" in data["patterns"]
        assert "radicalization" in data["patterns"]
        assert "epistemic_manipulation" in data["patterns"]

    def test_sockpuppet_check_requires_min_2(self, client):
        r = client.post("/api/defederation/sockpuppet-check",
                        data=json.dumps({"agent_pubkeys": ["only_one"]}),
                        headers=auth_headers())
        assert r.status_code == 400

    def test_sockpuppet_check_success(self, client):
        r = client.post("/api/defederation/sockpuppet-check",
                        data=json.dumps({"agent_pubkeys": ["agent_a", "agent_b"]}),
                        headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "score" in data
        assert "clusters" in data


# ─────────────────────────────────────────────────────────────────────────────
#  CONTENT POLICIES: Pack Management
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyRoutes:
    def test_available_packs(self, client):
        r = client.get("/api/policies/available", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "packs" in data
        assert data["count"] >= 4

    def test_available_packs_have_subscribed_field(self, client):
        r = client.get("/api/policies/available", headers=auth_headers())
        data = r.get_json()
        for pack in data["packs"]:
            assert "subscribed" in pack

    def test_asimov_standard_in_available(self, client):
        r = client.get("/api/policies/available", headers=auth_headers())
        data = r.get_json()
        ids = [p["pack_id"] for p in data["packs"]]
        assert "asimov-standard" in ids

    def test_subscribed_packs(self, client):
        r = client.get("/api/policies/subscribed", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "packs" in data
        assert data["count"] >= 1

    def test_asimov_standard_always_in_subscribed(self, client):
        r = client.get("/api/policies/subscribed", headers=auth_headers())
        data = r.get_json()
        ids = [p["pack_id"] for p in data["packs"]]
        assert "asimov-standard" in ids

    def test_subscribe_valid_pack(self, client):
        r = client.post("/api/policies/subscribe",
                        data=json.dumps({"pack_id": "journalism"}),
                        headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True

    def test_subscribe_missing_pack_id(self, client):
        r = client.post("/api/policies/subscribe",
                        data=json.dumps({}), headers=auth_headers())
        assert r.status_code == 400

    def test_subscribe_nonexistent_pack(self, client):
        r = client.post("/api/policies/subscribe",
                        data=json.dumps({"pack_id": "pack-does-not-exist-abc123"}),
                        headers=auth_headers())
        assert r.status_code == 400

    def test_unsubscribe_asimov_standard_returns_403(self, client):
        r = client.delete("/api/policies/unsubscribe/asimov-standard",
                          headers=auth_headers())
        assert r.status_code == 403

    def test_unsubscribe_valid_pack(self, client):
        client.post("/api/policies/subscribe",
                    data=json.dumps({"pack_id": "creator-commons"}),
                    headers=auth_headers())
        r = client.delete("/api/policies/unsubscribe/creator-commons",
                          headers=auth_headers())
        assert r.status_code == 200

    def test_create_pack_success(self, client):
        payload = {
            "name": "Route Test Pack",
            "description": "Created via API",
            "rules": [
                {"category": "gore", "action": "BLOCK",
                 "severity_threshold": 0.7, "description": "Block gore"},
            ],
        }
        r = client.post("/api/policies/create",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 201
        data = r.get_json()
        assert data["ok"] is True
        assert "pack" in data
        assert data["pack"]["name"] == "Route Test Pack"

    def test_create_pack_missing_name(self, client):
        payload = {
            "description": "No name",
            "rules": [{"category": "x", "action": "BLOCK",
                       "severity_threshold": 0.0, "description": "y"}],
        }
        r = client.post("/api/policies/create",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 400

    def test_create_pack_missing_rules(self, client):
        payload = {"name": "No Rules Pack"}
        r = client.post("/api/policies/create",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 400

    def test_evaluate_content_clean(self, client):
        payload = {"title": "Beautiful sunset", "description": "Nice photo"}
        r = client.post("/api/policies/evaluate",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert data["blocked"] is False

    def test_evaluate_content_nsfw_clean_subscriptions(self, client):
        # With only asimov-standard subscribed (baseline), NSFW is not blocked
        from services import content_policies as cp
        packs_before = [p["pack_id"] for p in cp.get_subscribed_packs()]
        # Unsubscribe family-safe if currently subscribed
        cp.unsubscribe("family-safe")
        payload = {"categories": ["nsfw"], "nsfw": True}
        r = client.post("/api/policies/evaluate",
                        data=json.dumps(payload), headers=auth_headers())
        data = r.get_json()
        assert r.status_code == 200
        # Restore
        if "family-safe" in packs_before:
            cp.subscribe("family-safe")

    def test_evaluate_blocked_returns_422(self, client):
        # H1 content should always be blocked
        payload = {
            "title": "csam",
            "description": "child sexual abuse material"
        }
        r = client.post("/api/policies/evaluate",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code == 422
        data = r.get_json()
        assert data["blocked"] is True

    def test_evaluate_with_content_wrapper(self, client):
        payload = {
            "content": {"title": "hello", "description": "world"}
        }
        r = client.post("/api/policies/evaluate",
                        data=json.dumps(payload), headers=auth_headers())
        assert r.status_code in (200, 422)
        data = r.get_json()
        assert "blocked" in data


# ─────────────────────────────────────────────────────────────────────────────
#  ROUTE REGISTRATION SMOKE TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteRegistration:
    def test_assessments_endpoint_exists(self, client):
        r = client.get("/api/defederation/assessments?agent_pubkey=x",
                       headers=auth_headers())
        assert r.status_code != 404

    def test_assess_endpoint_exists(self, client):
        r = client.post("/api/defederation/assess",
                        data=json.dumps({}), headers=auth_headers())
        assert r.status_code != 404

    def test_consensus_endpoint_exists(self, client):
        r = client.get("/api/defederation/consensus/testkey",
                       headers=auth_headers())
        assert r.status_code != 404

    def test_policies_available_endpoint_exists(self, client):
        r = client.get("/api/policies/available", headers=auth_headers())
        assert r.status_code != 404

    def test_policies_subscribed_endpoint_exists(self, client):
        r = client.get("/api/policies/subscribed", headers=auth_headers())
        assert r.status_code != 404

    def test_policies_evaluate_endpoint_exists(self, client):
        r = client.post("/api/policies/evaluate",
                        data=json.dumps({"title": "test"}),
                        headers=auth_headers())
        assert r.status_code != 404

    def test_patterns_endpoint_exists(self, client):
        r = client.get("/api/defederation/patterns/test_agent",
                       headers=auth_headers())
        assert r.status_code != 404

    def test_sockpuppet_endpoint_exists(self, client):
        r = client.post("/api/defederation/sockpuppet-check",
                        data=json.dumps({"agent_pubkeys": ["a", "b"]}),
                        headers=auth_headers())
        assert r.status_code != 404
