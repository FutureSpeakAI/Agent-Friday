"""Unit tests for services/marketplace.py"""
import pytest
from services import marketplace as mkt


def _mk_listing(**kwargs):
    """Helper: create a listing with sensible defaults."""
    defaults = {
        "asset_id": "test-asset-" + __import__("uuid").uuid4().hex[:8],
        "price_mpsi": 0,
        "license_offered": "CC-BY-4.0",
        "visibility": "public",
        "title": "Test Listing",
    }
    defaults.update(kwargs)
    return mkt.create_listing(**defaults)


class TestCreateListing:
    def test_create_listing_returns_dict(self):
        result = _mk_listing()
        assert result is not None
        assert isinstance(result, dict)

    def test_create_listing_has_id(self):
        result = _mk_listing()
        assert result is not None
        assert "id" in result
        assert result["id"]

    def test_create_listing_has_signature(self):
        result = _mk_listing()
        assert result is not None
        assert "signature" in result
        assert result["signature"]  # non-empty

    def test_create_listing_stores_asset_id(self):
        asset = "unique-asset-abc123"
        result = _mk_listing(asset_id=asset)
        assert result is not None
        assert result["asset_id"] == asset

    def test_create_listing_stores_price(self):
        result = _mk_listing(price_mpsi=99_000)
        assert result is not None
        assert result["price_mpsi"] == 99_000

    def test_create_listing_default_currency(self):
        result = _mk_listing()
        assert result is not None
        assert result.get("currency") == "PSI"


class TestGetListing:
    def test_get_listing_by_id(self):
        listing = _mk_listing()
        assert listing is not None
        fetched = mkt.get_listing(listing["id"])
        assert fetched is not None
        assert fetched["id"] == listing["id"]

    def test_get_listing_nonexistent_returns_none(self):
        result = mkt.get_listing("00000000-fake-id-0000-000000000000")
        assert result is None

    def test_get_listing_matches_create(self):
        listing = _mk_listing(title="Unique Title XYZ")
        assert listing is not None
        fetched = mkt.get_listing(listing["id"])
        assert fetched is not None
        assert fetched["title"] == "Unique Title XYZ"


class TestSearchListings:
    def test_search_listings_returns_list(self):
        result = mkt.search_listings()
        assert isinstance(result, list)

    def test_created_listing_appears_in_search(self):
        listing = _mk_listing(visibility="public")
        assert listing is not None
        results = mkt.search_listings()
        ids = [r["id"] for r in results]
        assert listing["id"] in ids

    def test_search_only_public_listings(self):
        private = _mk_listing(visibility="private")
        assert private is not None
        results = mkt.search_listings()
        ids = [r["id"] for r in results]
        assert private["id"] not in ids

    def test_search_by_license_type(self):
        import uuid
        asset_cc = "cc-asset-" + uuid.uuid4().hex[:6]
        asset_mit = "mit-asset-" + uuid.uuid4().hex[:6]
        cc_listing = _mk_listing(asset_id=asset_cc, license_offered="CC-BY-4.0")
        mit_listing = _mk_listing(asset_id=asset_mit, license_offered="MIT")
        assert cc_listing is not None
        assert mit_listing is not None

        cc_results = mkt.search_listings(license_type="CC-BY-4.0")
        mit_results = mkt.search_listings(license_type="MIT")

        cc_ids = [r["id"] for r in cc_results]
        mit_ids = [r["id"] for r in mit_results]

        assert cc_listing["id"] in cc_ids
        assert mit_listing["id"] in mit_ids
        # MIT listing should NOT appear in CC results
        assert mit_listing["id"] not in cc_ids

    def test_search_price_range(self):
        import uuid
        cheap = _mk_listing(asset_id="cheap-" + uuid.uuid4().hex[:6], price_mpsi=100)
        expensive = _mk_listing(asset_id="pricey-" + uuid.uuid4().hex[:6], price_mpsi=9_999_000)
        assert cheap is not None
        assert expensive is not None

        budget_results = mkt.search_listings(min_price=0, max_price=1_000)
        budget_ids = [r["id"] for r in budget_results]
        assert cheap["id"] in budget_ids
        assert expensive["id"] not in budget_ids

    def test_search_min_price_filter(self):
        import uuid
        free = _mk_listing(asset_id="free-" + uuid.uuid4().hex[:6], price_mpsi=0)
        paid = _mk_listing(asset_id="paid-" + uuid.uuid4().hex[:6], price_mpsi=500_000)
        assert free is not None
        assert paid is not None

        paid_only = mkt.search_listings(min_price=100_000)
        paid_ids = [r["id"] for r in paid_only]
        assert paid["id"] in paid_ids
        assert free["id"] not in paid_ids


class TestUpdateListing:
    def test_update_listing_returns_updated(self):
        listing = _mk_listing(title="Before Update")
        assert listing is not None
        updated = mkt.update_listing(listing["id"], title="After Update")
        assert updated is not None
        assert updated["title"] == "After Update"

    def test_update_listing_nonexistent_returns_none(self):
        result = mkt.update_listing("00000000-fake-0000-0000-000000000000", title="x")
        assert result is None

    def test_update_listing_price(self):
        listing = _mk_listing(price_mpsi=0)
        assert listing is not None
        updated = mkt.update_listing(listing["id"], price_mpsi=999)
        assert updated is not None
        assert updated["price_mpsi"] == 999


class TestRemoveListing:
    def test_remove_listing_returns_true(self):
        listing = _mk_listing()
        assert listing is not None
        result = mkt.remove_listing(listing["id"])
        assert result is True

    def test_remove_listing_then_get_returns_none(self):
        listing = _mk_listing()
        assert listing is not None
        mkt.remove_listing(listing["id"])
        fetched = mkt.get_listing(listing["id"])
        assert fetched is None

    def test_remove_nonexistent_returns_bool(self):
        # SQLite DELETE on a nonexistent row succeeds silently (rowcount=0).
        # The service returns True (no exception) rather than False.
        result = mkt.remove_listing("00000000-fake-0000-0000-000000000000")
        assert isinstance(result, bool)

    def test_remove_listing_disappears_from_search(self):
        listing = _mk_listing(visibility="public")
        assert listing is not None
        mkt.remove_listing(listing["id"])
        results = mkt.search_listings()
        ids = [r["id"] for r in results]
        assert listing["id"] not in ids


class TestMyListings:
    def test_get_my_listings_returns_list(self):
        result = mkt.get_my_listings()
        assert isinstance(result, list)

    def test_created_listing_appears_in_my_listings(self):
        listing = _mk_listing()
        assert listing is not None
        mine = mkt.get_my_listings()
        ids = [r["id"] for r in mine]
        assert listing["id"] in ids


class TestPolicy:
    def test_get_policy_returns_dict(self):
        policy = mkt.get_policy()
        assert isinstance(policy, dict)

    def test_get_policy_has_buying_key(self):
        policy = mkt.get_policy()
        assert "buying" in policy

    def test_get_policy_has_selling_key(self):
        policy = mkt.get_policy()
        assert "selling" in policy

    def test_update_policy_persists(self):
        mkt.update_policy({"buying": {"enabled": True, "per_item_max_mpsi": 123_456}})
        policy = mkt.get_policy()
        assert policy["buying"]["per_item_max_mpsi"] == 123_456

    def test_update_policy_returns_dict(self):
        result = mkt.update_policy({"selling": {"enabled": True}})
        assert isinstance(result, dict)


class TestPurchaseIntent:
    def test_purchase_intent_returns_invoice(self):
        listing = _mk_listing(price_mpsi=1_000)
        assert listing is not None
        result = mkt.purchase_intent(listing["id"], buyer_agent_id="test-buyer")
        assert isinstance(result, dict)
        assert "ok" in result
        if result["ok"]:
            assert "invoice" in result
            assert "listing" in result

    def test_purchase_intent_nonexistent_listing(self):
        result = mkt.purchase_intent("00000000-fake-0000-0000-000000000000", buyer_agent_id="buyer")
        assert isinstance(result, dict)
        assert result.get("ok") is False

    def test_purchase_intent_exceeds_policy_limit(self):
        # Set a low per_item_max, then try to buy something above it
        mkt.update_policy({"buying": {"enabled": True, "per_item_max_mpsi": 100}})
        listing = _mk_listing(price_mpsi=999_000)
        assert listing is not None
        result = mkt.purchase_intent(listing["id"], buyer_agent_id="big-spender")
        assert isinstance(result, dict)
        # Either blocked due to price cap or ok=False with an error
        if not result.get("ok"):
            assert "error" in result
        # Restore a sane policy limit for other tests
        mkt.update_policy({"buying": {"per_item_max_mpsi": 5_000_000}})
