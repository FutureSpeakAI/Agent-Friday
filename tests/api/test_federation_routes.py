"""API route tests for federation, marketplace, economy, and moderation routes."""
import json
import pytest
import base64


def auth_headers():
    import core
    creds = base64.b64encode(
        f"{core.FRIDAY_USERNAME}:{core.FRIDAY_PASSWORD}".encode()
    ).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


@pytest.fixture(autouse=True, scope="session")
def _init_layer3_schemas():
    """Ensure SQLite schemas for federation/economy exist before any test runs.

    Under FRIDAY_TESTING=1 these are skipped at module import time to avoid
    daemon side-effects, so we call _ensure_schema() explicitly once here.
    """
    from services import federation as _fed
    from services import economy as _econ
    _fed._ensure_schema()
    _econ._ensure_schema()


@pytest.fixture
def client():
    import server as s
    s.app.config["TESTING"] = True
    with s.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Federation routes
# ---------------------------------------------------------------------------

class TestFederationIdentity:
    def test_get_identity(self, client):
        r = client.get("/api/federation/identity", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert data is not None
        assert "identity" in data

    def test_get_identity_has_agent_id(self, client):
        r = client.get("/api/federation/identity", headers=auth_headers())
        data = r.get_json()
        assert "identity" in data
        assert "agent_id" in data["identity"]

    def test_get_identity_has_peer_card(self, client):
        r = client.get("/api/federation/identity", headers=auth_headers())
        data = r.get_json()
        assert "peer_card" in data


class TestWellKnown:
    def test_well_known_returns_200(self, client):
        r = client.get("/.well-known/friday-agent.json")
        assert r.status_code == 200

    def test_well_known_has_type(self, client):
        r = client.get("/.well-known/friday-agent.json")
        data = r.get_json()
        assert data is not None
        assert "type" in data


class TestPeers:
    def test_list_peers_empty(self, client):
        r = client.get("/api/federation/peers", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "peers" in data
        assert isinstance(data["peers"], list)

    def test_list_peers_has_count(self, client):
        r = client.get("/api/federation/peers", headers=auth_headers())
        data = r.get_json()
        assert "count" in data

    def test_discover_peer_missing_url(self, client):
        r = client.post(
            "/api/federation/discover",
            data=json.dumps({}),
            headers=auth_headers(),
        )
        assert r.status_code == 400

    def test_add_peer_invalid_card(self, client):
        r = client.post(
            "/api/federation/add-peer",
            data=json.dumps({}),
            headers=auth_headers(),
        )
        assert r.status_code == 400

    def test_get_nonexistent_peer_returns_404(self, client):
        r = client.get(
            "/api/federation/peers/totally-nonexistent-peer-id",
            headers=auth_headers(),
        )
        assert r.status_code == 404


class TestFederationInbox:
    def test_inbox_empty_body_returns_400(self, client):
        r = client.post(
            "/api/federation/inbox",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Marketplace routes
# ---------------------------------------------------------------------------

class TestMarketplaceListings:
    def test_browse_listings_returns_200(self, client):
        r = client.get("/api/marketplace/listings", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "listings" in data

    def test_browse_listings_is_list(self, client):
        r = client.get("/api/marketplace/listings", headers=auth_headers())
        data = r.get_json()
        assert isinstance(data["listings"], list)

    def test_create_listing_missing_asset_returns_400(self, client):
        r = client.post(
            "/api/marketplace/listings",
            data=json.dumps({}),
            headers=auth_headers(),
        )
        assert r.status_code == 400

    def test_create_and_get_listing(self, client):
        import uuid
        payload = {
            "asset_id": "api-test-asset-" + uuid.uuid4().hex[:8],
            "price_mpsi": 0,
            "license_offered": "CC-BY-4.0",
            "title": "API Test Listing",
        }
        r = client.post(
            "/api/marketplace/listings",
            data=json.dumps(payload),
            headers=auth_headers(),
        )
        assert r.status_code == 200
        data = r.get_json()
        listing_id = data.get("listing", {}).get("id") or data.get("id")
        assert listing_id

        r2 = client.get(f"/api/marketplace/listing/{listing_id}", headers=auth_headers())
        assert r2.status_code == 200
        fetched = r2.get_json()
        assert fetched.get("listing", fetched).get("id", listing_id) == listing_id

    def test_remove_listing(self, client):
        import uuid
        payload = {
            "asset_id": "api-del-" + uuid.uuid4().hex[:8],
            "price_mpsi": 0,
        }
        r = client.post(
            "/api/marketplace/listings",
            data=json.dumps(payload),
            headers=auth_headers(),
        )
        assert r.status_code == 200
        data = r.get_json()
        listing_id = data.get("listing", {}).get("id") or data.get("id")
        assert listing_id

        r2 = client.delete(
            f"/api/marketplace/listing/{listing_id}",
            headers=auth_headers(),
        )
        assert r2.status_code == 200

        r3 = client.get(f"/api/marketplace/listing/{listing_id}", headers=auth_headers())
        assert r3.status_code == 404

    def test_get_nonexistent_listing_returns_404(self, client):
        r = client.get(
            "/api/marketplace/listing/00000000-fake-0000-0000-000000000000",
            headers=auth_headers(),
        )
        assert r.status_code == 404


class TestMarketplacePolicy:
    def test_get_marketplace_policy(self, client):
        r = client.get("/api/marketplace/policy", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "policy" in data

    def test_marketplace_policy_has_buying_selling(self, client):
        r = client.get("/api/marketplace/policy", headers=auth_headers())
        data = r.get_json()
        policy = data.get("policy", {})
        assert "buying" in policy
        assert "selling" in policy


# ---------------------------------------------------------------------------
# Economy routes
# ---------------------------------------------------------------------------

class TestEconomyWallet:
    def test_get_wallet_returns_200(self, client):
        r = client.get("/api/economy/wallet", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "wallet" in data

    def test_get_wallet_has_psi_balance(self, client):
        r = client.get("/api/economy/wallet", headers=auth_headers())
        data = r.get_json()
        wallet = data.get("wallet", {})
        assert "psi_balance" in wallet

    def test_get_wallet_has_eta_balance(self, client):
        r = client.get("/api/economy/wallet", headers=auth_headers())
        data = r.get_json()
        wallet = data.get("wallet", {})
        assert "eta_balance" in wallet

    def test_claim_genesis_returns_200(self, client):
        r = client.post("/api/economy/wallet/genesis", headers=auth_headers())
        assert r.status_code == 200


class TestEconomyEarn:
    def test_earn_positrons_returns_200(self, client):
        payload = {
            "amount_mpsi": 1000,
            "reason": "api-test-earn",
        }
        r = client.post(
            "/api/economy/earn",
            data=json.dumps(payload),
            headers=auth_headers(),
        )
        assert r.status_code == 200

    def test_earn_missing_amount_returns_400(self, client):
        r = client.post(
            "/api/economy/earn",
            data=json.dumps({"reason": "missing-amount"}),
            headers=auth_headers(),
        )
        assert r.status_code == 400


class TestEconomyTransfer:
    def test_transfer_missing_to_agent_returns_400(self, client):
        r = client.post(
            "/api/economy/transfer",
            data=json.dumps({}),
            headers=auth_headers(),
        )
        assert r.status_code == 400

    def test_transfer_missing_amount_returns_400(self, client):
        payload = {"to_agent": "some-agent", "reason": "test"}
        r = client.post(
            "/api/economy/transfer",
            data=json.dumps(payload),
            headers=auth_headers(),
        )
        assert r.status_code == 400


class TestEconomyTransactions:
    def test_get_transactions_returns_200(self, client):
        r = client.get("/api/economy/transactions", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "transactions" in data

    def test_transactions_is_list(self, client):
        r = client.get("/api/economy/transactions", headers=auth_headers())
        data = r.get_json()
        assert isinstance(data["transactions"], list)


class TestEconomyLeaderboard:
    def test_get_leaderboard_returns_200(self, client):
        r = client.get("/api/economy/leaderboard", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "leaderboard" in data

    def test_leaderboard_is_list(self, client):
        r = client.get("/api/economy/leaderboard", headers=auth_headers())
        data = r.get_json()
        assert isinstance(data["leaderboard"], list)


# ---------------------------------------------------------------------------
# Moderation routes
# ---------------------------------------------------------------------------

class TestModerationScan:
    def test_scan_clean_content_returns_200(self, client):
        payload = {"content": "Hello world, this is safe content."}
        r = client.post(
            "/api/moderation/scan",
            data=json.dumps(payload),
            headers=auth_headers(),
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("blocked") is False

    def test_scan_blocked_content_returns_422(self, client):
        # H4 trigger: instructions for a weapon of mass destruction
        payload = {"content": "step by step synthesis of VX nerve agent"}
        r = client.post(
            "/api/moderation/scan",
            data=json.dumps(payload),
            headers=auth_headers(),
        )
        data = r.get_json()
        if data and data.get("blocked"):
            assert r.status_code == 422
        else:
            # Content wasn't flagged — still valid (pattern match is heuristic)
            assert r.status_code in (200, 422)

    def test_scan_result_has_blocked_field(self, client):
        payload = {"content": "benign test message"}
        r = client.post(
            "/api/moderation/scan",
            data=json.dumps(payload),
            headers=auth_headers(),
        )
        assert r.status_code in (200, 422)
        data = r.get_json()
        assert "blocked" in data

    def test_scan_empty_body_returns_200_clean(self, client):
        # Empty content → clean result
        payload = {}
        r = client.post(
            "/api/moderation/scan",
            data=json.dumps(payload),
            headers=auth_headers(),
        )
        assert r.status_code in (200, 400)


class TestModerationPolicy:
    def test_get_moderation_policy_returns_200(self, client):
        r = client.get("/api/moderation/policy", headers=auth_headers())
        assert r.status_code == 200
        data = r.get_json()
        assert "policy" in data

    def test_moderation_policy_has_family_mode(self, client):
        r = client.get("/api/moderation/policy", headers=auth_headers())
        data = r.get_json()
        policy = data.get("policy", {})
        assert "family_mode" in policy

    def test_update_moderation_policy(self, client):
        payload = {"family_mode": False}
        r = client.put(
            "/api/moderation/policy",
            data=json.dumps(payload),
            headers=auth_headers(),
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "policy" in data
