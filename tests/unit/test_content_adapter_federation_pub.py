"""Unit tests for the Friday Federation adapter (spec §4.13 / §13).

Everything is stubbed through the module's ``_services`` injection point —
no real ownership/marketplace/transport modules are touched (they would hit
real ~/.friday DBs). Covers: capabilities (full fidelity, analytics full,
native schedule/delete), the publish pipeline (register → listing with
license+price → CONTENT_OFFER announce → local URL/listing id), trust-graph
peer filtering, zero-peer tolerance, transport-unavailable degradation,
text-only staging, tags traveling on the listing+offer, §8.2 metrics
mapping (views/fetches/tips/purchases), delete, and clean error envelopes
when federation services are unavailable.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import platforms as preg                    # noqa: E402
from agent_friday.services.platforms import base as pbase              # noqa: E402
from agent_friday.services.platforms import federation_pub as fp       # noqa: E402
from agent_friday.services.platforms.federation_pub import (           # noqa: E402
    ERR_ASSET_REGISTRATION, ERR_BAD_EVENT, ERR_DELETE_FAILED,
    ERR_LISTING_FAILED, ERR_NOTHING_TO_PUBLISH, ERR_SERVICES_UNAVAILABLE,
    FederationAdapter, record_event)


# ── service doubles ───────────────────────────────────────────────────────────
class FakeOwnership:
    def __init__(self):
        self.assets = {}
        self.register_calls = []
        self.fail_register = False

    def register(self, file_path, manifest=None, *, title=None, auto_build=True):
        self.register_calls.append({"file_path": str(file_path), "title": title})
        if self.fail_register:
            return None
        rec = {
            "id": f"asset-{len(self.register_calls)}",
            "file_path": str(file_path),
            "content_hash": "sha256:deadbeef",
            "license": "all-rights-reserved",
            "title": title or Path(str(file_path)).stem,
            "media_type": "text/markdown",
        }
        self.assets[rec["id"]] = rec
        return rec

    def get_asset(self, asset_id):
        return self.assets.get(asset_id)


class FakeMarketplace:
    def __init__(self):
        self.listings = {}
        self.create_calls = []
        self.removed = []
        self.fail_create = False

    def create_listing(self, asset_id, price_mpsi=0, license_offered="CC-BY-4.0",
                       visibility="public", title=None, description=None,
                       preview_url=None, media_type=None):
        self.create_calls.append({
            "asset_id": asset_id, "price_mpsi": price_mpsi,
            "license_offered": license_offered, "visibility": visibility,
            "title": title, "description": description,
            "preview_url": preview_url, "media_type": media_type,
        })
        if self.fail_create:
            return None
        lid = f"lst-{len(self.create_calls)}"
        listing = {
            "id": lid, "asset_id": asset_id, "title": title,
            "description": description, "media_type": media_type or "",
            "preview_url": preview_url or "", "price_mpsi": int(price_mpsi),
            "currency": "PSI", "license_offered": license_offered,
            "visibility": visibility, "created_at": "2026-07-06T00:00:00Z",
        }
        self.listings[lid] = listing
        return listing

    def get_listing(self, lid):
        return self.listings.get(lid)

    def remove_listing(self, lid):
        self.removed.append(lid)
        return self.listings.pop(lid, None) is not None


class FakeFederation:
    def __init__(self, peers=None):
        self._peers = list(peers or [])

    def get_peers(self):
        return list(self._peers)


class FakeTransport:
    MSG_TYPES = {"CONTENT_OFFER": "content_offer", "HANDSHAKE": "handshake"}

    def __init__(self, fail_send=False):
        self.built = []
        self.sent = []
        self.fail_send = fail_send

    def build_message(self, msg_type, payload_dict, recipient_pubkey_hex):
        self.built.append({"msg_type": msg_type, "payload": payload_dict,
                           "recipient": recipient_pubkey_hex})
        return {"type": "federation_message", "msg_type": msg_type,
                "recipient_pubkey": recipient_pubkey_hex,
                "encrypted_payload": "<opaque>"}

    def send_to_peer(self, peer_endpoint, envelope_dict, timeout=15):
        self.sent.append({"endpoint": peer_endpoint, "envelope": envelope_dict})
        return {"ok": not self.fail_send}


def _peer(agent_id, score, endpoints=None):
    return {"agent_id": agent_id, "overall_score": score,
            "endpoints": json.dumps(endpoints or [f"https://{agent_id}.example"])}


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH",
                        tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    preg._reset_for_tests()
    fp._services.clear()
    yield
    fp._services.clear()
    preg._reset_for_tests()


@pytest.fixture
def stubs():
    """Inject the full stub constellation; returns the doubles."""
    own = FakeOwnership()
    mk = FakeMarketplace()
    fed = FakeFederation([_peer("aa11", 0.9), _peer("bb22", 0.8)])
    tx = FakeTransport()
    fp._services.update({"ownership": own, "marketplace": mk,
                         "federation": fed, "federation_transport": tx})
    return {"ownership": own, "marketplace": mk, "federation": fed,
            "transport": tx}


@pytest.fixture
def adapter():
    a = FederationAdapter()
    a.configure({})
    return a


def _publish(adapter, tmp_path, *, body="Sovereignty is a feature.",
             options=None, post=None, with_file=True):
    """prepare→publish round trip with a real media file when asked."""
    target = {"id": "tgt-1", "adapted_body": body, "format": "listing",
              "options": dict(options or {})}
    if with_file:
        media = tmp_path / "artifact.png"
        media.write_bytes(b"\x89PNG fake")
        target["adapted_assets"] = [{"kind": "image", "path": str(media)}]
    post = dict(post or {})
    post.setdefault("id", "post-1")
    prep = adapter.prepare(target, post)
    assert prep["ok"], prep
    return adapter.publish(prep["prepared"])


# ── capabilities ──────────────────────────────────────────────────────────────
def test_capabilities_full_fidelity(adapter):
    caps = adapter.capabilities()
    assert caps["char_limit"] is None          # no char limit — it is ours
    assert caps["title_limit"] is None
    assert caps["analytics"] == "full"
    # The marketplace has no scheduled_at: advertising native scheduling made
    # the publisher's arm-time delegation publish scheduled listings EARLY.
    assert caps["native_schedule"] is False
    assert caps["native_delete"] is True
    assert "listing" in caps["formats"]
    # deep copy — annotating a copy never corrupts the declaration
    caps["notes"].append("scribble")
    assert "scribble" not in adapter.capabilities()["notes"]


def test_identity_declaration(adapter):
    assert adapter.name == "federation"
    assert adapter.automation_tier == "api"
    assert adapter.default_daily_limit == 0    # unlimited on our own rails
    assert adapter.budget_would_exceed() is False


# ── publish pipeline ──────────────────────────────────────────────────────────
def test_publish_registers_asset_and_creates_listing(adapter, stubs, tmp_path):
    res = _publish(
        adapter, tmp_path,
        options={"price_mpsi": 2500, "visibility": "unlisted"},
        post={"title": "The Artifact", "license": {"terms": "CC-BY-4.0"},
              "tags": ["ai-generated"]})
    assert res["ok"], res

    # 1 — asset registered from the media file
    own = stubs["ownership"]
    assert len(own.register_calls) == 1
    assert own.register_calls[0]["file_path"].endswith("artifact.png")

    # 2 — listing carries license + price + visibility + adapted body
    call = stubs["marketplace"].create_calls[0]
    assert call["asset_id"] == "asset-1"
    assert call["price_mpsi"] == 2500
    assert call["license_offered"] == "CC-BY-4.0"
    assert call["visibility"] == "unlisted"
    assert call["description"] == "Sovereignty is a feature."
    assert call["title"] == "The Artifact"

    # 4 — post_url is the LOCAL listing URL; platform_post_id = listing id
    assert res["platform_post_id"] == "lst-1"
    assert res["post_url"] == "http://localhost:3000/api/marketplace/listing/lst-1"

    # budget bookkeeping recorded the publish
    assert adapter.rate_budget()["used"] == 1


def test_publish_announces_content_offer_to_trusted_peers(adapter, stubs, tmp_path):
    res = _publish(adapter, tmp_path,
                   post={"license": "CC0-1.0", "tags": ["nsfw", "satire"]})
    assert res["ok"]
    ann = res["raw"]["announce"]
    assert ann["attempted"] == 2 and ann["announced"] == 2 and ann["failed"] == 0

    tx = stubs["transport"]
    assert len(tx.built) == 2 and len(tx.sent) == 2
    first = tx.built[0]
    assert first["msg_type"] == "content_offer"       # MSG_TYPES["CONTENT_OFFER"]
    assert first["recipient"] == "aa11"
    offer = first["payload"]
    assert offer["listing_id"] == "lst-1"
    assert offer["license"] == "CC0-1.0"
    assert offer["price_mpsi"] == 0
    assert offer["listing_url"].endswith("/api/marketplace/listing/lst-1")
    # policy-pack tags travel on the offer (§4.13 moderation note)
    assert offer["tags"] == ["nsfw", "satire"]
    # …and on the local listing sidecar
    assert fp._read_stats("lst-1")["tags"] == ["nsfw", "satire"]


def test_publish_trust_graph_filters_low_score_peers(adapter, stubs, tmp_path):
    stubs["federation"]._peers = [_peer("good", 0.9), _peer("shady", 0.2)]
    res = _publish(adapter, tmp_path)
    ann = res["raw"]["announce"]
    assert ann["attempted"] == 1 and ann["announced"] == 1
    assert [b["recipient"] for b in stubs["transport"].built] == ["good"]


def test_publish_zero_peers_is_fine(adapter, stubs, tmp_path):
    stubs["federation"]._peers = []
    res = _publish(adapter, tmp_path)
    assert res["ok"] is True
    assert res["raw"]["announce"] == {"attempted": 0, "announced": 0, "failed": 0}


def test_publish_survives_send_failures(adapter, stubs, tmp_path):
    stubs["transport"].fail_send = True
    res = _publish(adapter, tmp_path)
    assert res["ok"] is True                      # listing still stands
    ann = res["raw"]["announce"]
    assert ann["announced"] == 0 and ann["failed"] == 2


def test_publish_without_transport_degrades_cleanly(adapter, tmp_path):
    # only ownership + marketplace available — announce is skipped, not fatal
    fp._services.update({"ownership": FakeOwnership(),
                         "marketplace": FakeMarketplace()})
    res = _publish(adapter, tmp_path)
    assert res["ok"] is True
    assert "skipped" in res["raw"]["announce"]


def test_publish_text_only_stages_body_for_registration(adapter, stubs, tmp_path):
    res = _publish(adapter, tmp_path, with_file=False,
                   post={"title": "Essay", "license": "CC-BY-SA-4.0"})
    assert res["ok"], res
    staged = Path(stubs["ownership"].register_calls[0]["file_path"])
    assert staged.exists()
    assert staged.parent == pbase.PLATFORMS_DIR / "federation_staging"
    text = staged.read_text(encoding="utf-8")
    assert "Essay" in text and "Sovereignty is a feature." in text
    assert stubs["marketplace"].create_calls[0]["license_offered"] == "CC-BY-SA-4.0"


def test_publish_reuses_existing_asset_id(adapter, stubs, tmp_path):
    own = stubs["ownership"]
    own.assets["asset-known"] = {"id": "asset-known", "content_hash": "sha256:k",
                                 "license": "CC-BY-4.0", "title": "Known",
                                 "media_type": "image/png"}
    res = _publish(adapter, tmp_path, with_file=False,
                   options={"asset_id": "asset-known"})
    assert res["ok"], res
    assert own.register_calls == []              # already registered — no rebuild
    assert stubs["marketplace"].create_calls[0]["asset_id"] == "asset-known"
    # license falls back to the asset's own terms
    assert stubs["marketplace"].create_calls[0]["license_offered"] == "CC-BY-4.0"


# ── error envelopes (never raise) ────────────────────────────────────────────
def test_publish_services_unavailable_clean_envelope(adapter, tmp_path):
    # no stubs injected + FRIDAY_TESTING → services resolve to None
    res = _publish(adapter, tmp_path)
    assert res == {"ok": False, "error": ERR_SERVICES_UNAVAILABLE}


def test_publish_nothing_to_publish(adapter, stubs):
    res = adapter.publish({"body": "   ", "assets": [], "options": {}})
    assert res["ok"] is False and res["error"] == ERR_NOTHING_TO_PUBLISH


def test_publish_registration_failure(adapter, stubs, tmp_path):
    stubs["ownership"].fail_register = True
    res = _publish(adapter, tmp_path)
    assert res["ok"] is False and res["error"] == ERR_ASSET_REGISTRATION


def test_publish_listing_failure(adapter, stubs, tmp_path):
    stubs["marketplace"].fail_create = True
    res = _publish(adapter, tmp_path)
    assert res["ok"] is False and res["error"] == ERR_LISTING_FAILED


def test_publish_never_raises_on_garbage(adapter, stubs):
    assert adapter.publish(None)["ok"] is False
    assert adapter.publish({"options": "not-a-dict"})["ok"] is False


# ── delete ────────────────────────────────────────────────────────────────────
def test_delete_removes_listing(adapter, stubs, tmp_path):
    res = _publish(adapter, tmp_path)
    lid = res["platform_post_id"]
    out = adapter.delete(lid)
    assert out == {"ok": True, "deleted": True}
    assert stubs["marketplace"].get_listing(lid) is None
    again = adapter.delete(lid)
    assert again["ok"] is False and again["error"] == ERR_DELETE_FAILED


def test_delete_without_services(adapter):
    res = adapter.delete("lst-1")
    assert res["ok"] is False and res["error"] == ERR_SERVICES_UNAVAILABLE


# ── engagement counters + §8.2 metrics mapping ───────────────────────────────
def test_record_event_and_metrics_mapping(adapter, stubs, tmp_path):
    res = _publish(adapter, tmp_path)
    lid = res["platform_post_id"]

    for _ in range(3):
        assert record_event(lid, "view")["ok"]
    assert record_event(lid, "fetch", n=2)["ok"]
    assert record_event(lid, "tip", amount_mpsi=500)["ok"]
    assert record_event(lid, "peer_message")["ok"]
    assert record_event(lid, "peer_relay")["ok"]
    assert adapter.record_event(lid, "new_peer")["ok"]   # adapter-level API too

    # completed purchases counted read-only from the marketplace DB
    db = tmp_path / "marketplace.db"
    con = sqlite3.connect(str(db))
    con.execute("""CREATE TABLE purchases (id TEXT, listing_id TEXT,
                   amount_mpsi INTEGER, status TEXT, completed_at TEXT)""")
    con.executemany(
        "INSERT INTO purchases VALUES (?,?,?,?,?)",
        [("p1", lid, 2500, "completed", "2026-07-06T01:00:00Z"),
         ("p2", lid, 2500, "completed", "2026-07-06T02:00:00Z"),
         ("p3", lid, 2500, "pending", None),
         ("p4", "other", 999, "completed", "2026-07-06T03:00:00Z")])
    con.commit()
    con.close()
    stubs["marketplace"].DB_PATH = db

    m = adapter.fetch_metrics(lid)
    assert m is not None
    # §8.2 Federation column mapping
    assert m["impressions"] == 3          # listing_views
    assert m["clicks"] == 2               # fetches
    assert m["likes"] == 1                # tips_count
    assert m["comments"] == 1             # peer_messages
    assert m["shares"] == 1               # peer_relays
    assert m["follows_gained"] == 1       # new_peers
    assert m["tips_mpsi"] == 500
    assert m["purchases"] == 2 and m["revenue_mpsi"] == 5000
    # unreportable metrics are ABSENT, not zero (§8.2: missing ≠ zero)
    for absent in ("saves", "video_views", "watch_time_s"):
        assert absent not in m


def test_fetch_metrics_unknown_listing_is_none(adapter, stubs):
    assert adapter.fetch_metrics("lst-does-not-exist") is None
    assert adapter.fetch_metrics("") is None


def test_fetch_metrics_zero_counters_for_fresh_listing(adapter, stubs, tmp_path):
    lid = _publish(adapter, tmp_path)["platform_post_id"]
    m = adapter.fetch_metrics(lid)
    assert m["impressions"] == 0 and m["likes"] == 0 and m["clicks"] == 0


def test_record_event_rejects_garbage(adapter):
    assert record_event("", "view")["error"] == ERR_BAD_EVENT
    assert record_event("lst-1", "explode")["error"] == ERR_BAD_EVENT


# ── status / account metrics ─────────────────────────────────────────────────
def test_status_reflects_identity(adapter):
    st = adapter.status()
    assert st["connected"] is False and st["account"] is None   # no provenance

    class FakeProv:
        @staticmethod
        def agent_id():
            return "ab" * 32

    fp._services["provenance"] = FakeProv()
    st = adapter.status()
    assert st["connected"] is True
    assert st["account"].startswith("ab" * 8)
    assert st["expires_at"] is None
    assert adapter.refresh() is True


def test_fetch_account_metrics_counts_peer_graph(adapter, stubs):
    stubs["federation"]._peers = [_peer("a", 0.9), _peer("b", 0.4), _peer("c", 0.6)]
    am = adapter.fetch_account_metrics()
    assert am == {"peers": 3, "trusted_peers": 2}


def test_fetch_account_metrics_none_without_federation(adapter):
    assert adapter.fetch_account_metrics() is None


# ── prepare enrichment ────────────────────────────────────────────────────────
def test_prepare_normalizes_federation_options(adapter):
    prep = adapter.prepare(
        {"adapted_body": "b", "format": "listing",
         "options": {"price_mpsi": "750", "visibility": "EVERYONE"}},
        {"title": "T", "license": {"terms": "CC-BY-4.0"}, "tags": ["x"]})
    assert prep["ok"]
    opts = prep["prepared"]["options"]
    assert opts["price_mpsi"] == 750
    assert opts["visibility"] == "public"         # invalid → safe default
    assert opts["license"] == "CC-BY-4.0"
    assert opts["tags"] == ["x"]
    assert prep["prepared"]["title"] == "T"


def test_prepare_never_raises_on_garbage(adapter):
    res = adapter.prepare(None, None)
    assert isinstance(res, dict) and "ok" in res


# ── registry integration ─────────────────────────────────────────────────────
def test_registry_resolves_federation_pub():
    a = preg.get_adapter("federation_pub")
    assert a is not None and a.name == "federation"
    assert preg.get_adapter("federation") is a    # alias
