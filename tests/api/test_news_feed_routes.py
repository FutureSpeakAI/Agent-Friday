"""API tests for the news-feed, sources, source-trust, federation, and briefings
route groups.

Network is hermetically stubbed:
  * _rss_results  → returns a small synthetic article list (no feedparser/HTTP)
  * _brave_results → returns [] (no Brave API key needed)
  * _gather_front_page_pool → returns the same synthetic list
The autouse _no_real_llm fixture in tests/api/conftest.py covers all LLM calls.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from tests.conftest import CANNED_TEXT  # noqa: F401

# ─────────────────────────── synthetic article pool ───────────────────────────
_NOW_TS = time.time()

_FAKE_ARTICLES = [
    {
        "title": "AI breakthrough shakes up the tech world",
        "snippet": "Researchers claim a new architecture outperforms all benchmarks.",
        "url": "https://example.com/ai-breakthrough",
        "source": "example.com",
        "ts": _NOW_TS - 3600,
    },
    {
        "title": "Markets dip on rate-hike fears",
        "snippet": "The Dow fell two percent amid inflation concerns.",
        "url": "https://fake.test/markets-dip",
        "source": "fake.test",
        "ts": _NOW_TS - 7200,
    },
    {
        "title": "Climate summit yields new accord",
        "snippet": "Delegates agreed on a binding carbon-reduction target.",
        "url": "https://news.example.com/climate-summit",
        "source": "news.example.com",
        "ts": _NOW_TS - 1800,
    },
]


@pytest.fixture(autouse=True)
def _stub_network(monkeypatch, server_module):
    """Patch the two RSS / Brave fetchers so no real HTTP is made."""
    monkeypatch.setattr(server_module, "_rss_results",
                        lambda *a, **kw: list(_FAKE_ARTICLES), raising=False)
    monkeypatch.setattr(server_module, "_brave_results",
                        lambda *a, **kw: [], raising=False)

    # _gather_front_page_pool returns (pool, stats_dict)
    monkeypatch.setattr(server_module, "_gather_front_page_pool",
                        lambda *a, **kw: (list(_FAKE_ARTICLES), {}), raising=False)


# ═══════════════════════════════════════════════════════════════════════════════
#  1.  NEWS FEED
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewsFeed:
    def test_get_returns_200_and_expected_keys(self, client):
        resp = client.get("/api/news/feed")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "items" in data
        assert "total" in data
        assert "banned" in data
        assert "boosted" in data
        assert isinstance(data["items"], list)

    def test_limit_per_param_accepted(self, client):
        resp = client.get("/api/news/feed?limit_per=2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_invalid_limit_per_falls_back(self, client):
        # Non-numeric limit_per should not 500
        resp = client.get("/api/news/feed?limit_per=notanumber")
        assert resp.status_code == 200

    def test_categories_filter_accepted(self, client):
        resp = client.get("/api/news/feed?categories=AI%2FTech")
        assert resp.status_code == 200

    def test_unknown_category_ignored(self, client):
        # Unknown category name should not 500; silently discards it
        resp = client.get("/api/news/feed?categories=NonExistentCat")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
#  2.  READ LATER
# ═══════════════════════════════════════════════════════════════════════════════

class TestReadLater:
    def test_get_empty_list(self, client):
        resp = client.get("/api/news/read-later")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["items"], list)

    def test_save_article_roundtrip(self, client):
        payload = {
            "url": "https://example.com/some-article",
            "title": "Some Article",
            "source": "example.com",
            "snippet": "A great read.",
            "category": "AI/Tech",
        }
        post_resp = client.post("/api/news/read-later", json=payload)
        assert post_resp.status_code == 200
        post_data = post_resp.get_json()
        assert post_data["status"] == "ok"
        assert post_data["item"]["url"] == payload["url"]

        # Should now appear in GET
        get_resp = client.get("/api/news/read-later")
        urls = [a["url"] for a in get_resp.get_json()["items"]]
        assert payload["url"] in urls

    def test_save_missing_url_is_400(self, client):
        resp = client.post("/api/news/read-later", json={"title": "No URL"})
        assert resp.status_code == 400

    def test_delete_by_url(self, client):
        url = "https://fake.test/deleteme"
        client.post("/api/news/read-later",
                    json={"url": url, "title": "Delete Me"})
        del_resp = client.delete("/api/news/read-later", json={"url": url})
        assert del_resp.status_code == 200
        remaining = [a["url"] for a in del_resp.get_json()["items"]]
        assert url not in remaining

    def test_delete_missing_url_is_400(self, client):
        resp = client.delete("/api/news/read-later", json={})
        assert resp.status_code == 400

    def test_clear_all(self, client):
        client.post("/api/news/read-later",
                    json={"url": "https://example.com/a", "title": "A"})
        resp = client.delete("/api/news/read-later?clear=1")
        assert resp.status_code == 200
        assert resp.get_json()["items"] == []

    def test_dedup_by_url(self, client):
        payload = {"url": "https://example.com/dedup", "title": "Dedup Test"}
        client.post("/api/news/read-later", json=payload)
        client.post("/api/news/read-later", json=payload)
        items = client.get("/api/news/read-later").get_json()["items"]
        count = sum(1 for a in items if a["url"] == payload["url"])
        assert count == 1


# ═══════════════════════════════════════════════════════════════════════════════
#  3.  ARCHIVE + ARCHIVE STATS
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewsArchive:
    def test_archive_returns_expected_shape(self, client):
        resp = client.get("/api/news/archive")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "items" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data
        assert "has_more" in data

    def test_archive_pagination_params(self, client):
        resp = client.get("/api/news/archive?offset=0&limit=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] == 5

    def test_archive_invalid_offset_falls_back(self, client):
        resp = client.get("/api/news/archive?offset=notanumber")
        assert resp.status_code == 200

    def test_archive_sort_source(self, client):
        resp = client.get("/api/news/archive?sort=source")
        assert resp.status_code == 200

    def test_archive_stats_returns_expected_keys(self, client):
        resp = client.get("/api/news/archive/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "total" in data
        assert "date_range" in data
        assert "by_category" in data
        assert "by_source" in data


# ═══════════════════════════════════════════════════════════════════════════════
#  4.  SOURCE STATS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSourceStats:
    def test_get_returns_expected_shape(self, client):
        resp = client.get("/api/news/source-stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "sources" in data
        assert "categories" in data
        assert "most_engaged" in data
        assert "least_engaged" in data
        assert "total_sources" in data

    def test_post_records_event(self, client):
        payload = {"source": "example.com", "action": "click", "category": "AI/Tech"}
        resp = client.post("/api/news/source-stats", json=payload)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_post_unknown_action_does_not_500(self, client):
        resp = client.post("/api/news/source-stats",
                           json={"source": "example.com", "action": "unknown_action"})
        assert resp.status_code == 200

    def test_post_empty_body_does_not_500(self, client):
        resp = client.post("/api/news/source-stats", json={})
        assert resp.status_code == 200

    def test_malformed_json_does_not_500(self, client):
        resp = client.post("/api/news/source-stats", data="{not json",
                           content_type="application/json")
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════════════════════
#  5.  NEWS CLUSTERS
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewsClusters:
    def test_clusters_returns_expected_shape(self, client):
        resp = client.get("/api/news/clusters")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "clusters" in data
        assert "total" in data
        assert "generated_at" in data
        assert isinstance(data["clusters"], list)


# ═══════════════════════════════════════════════════════════════════════════════
#  6.  WIKI CONNECTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewsWikiConnections:
    def test_returns_200_with_title(self, client):
        resp = client.get("/api/news/wiki-connections?title=AI+research&snippet=test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "matches" in data
        assert "count" in data

    def test_returns_200_empty_params(self, client):
        resp = client.get("/api/news/wiki-connections")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["matches"] == []

    def test_article_id_lookup(self, client):
        # article_id not in live feed → graceful empty result
        resp = client.get("/api/news/wiki-connections?article_id=nonexistent-id-xyz")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
#  7.  ANNOTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewsAnnotations:
    _ARTICLE_ID = "https://example.com/annotate-test-article"

    def test_get_all_empty(self, client):
        resp = client.get("/api/news/annotations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "annotations" in data
        assert "annotated_ids" in data

    def test_post_creates_annotation(self, client):
        resp = client.post("/api/news/annotate", json={
            "article_id": self._ARTICLE_ID,
            "text": "Interesting perspective on AI.",
            "article_title": "AI Test Article",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "annotation" in data
        assert data["annotation"]["text"] == "Interesting perspective on AI."

    def test_annotation_appears_in_list(self, client):
        aid = "https://fake.test/list-test"
        client.post("/api/news/annotate", json={
            "article_id": aid, "text": "Listed annotation"})
        resp = client.get("/api/news/annotations")
        texts = [a["text"] for a in resp.get_json()["annotations"]]
        assert "Listed annotation" in texts

    def test_annotation_by_article_id(self, client):
        aid = "https://fake.test/per-article"
        client.post("/api/news/annotate",
                    json={"article_id": aid, "text": "Per-article note"})
        resp = client.get(f"/api/news/annotations/{aid}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        notes = [n["text"] for n in data["annotations"]]
        assert "Per-article note" in notes

    def test_post_missing_article_id_is_400(self, client):
        resp = client.post("/api/news/annotate", json={"text": "no article"})
        assert resp.status_code == 400

    def test_post_missing_text_is_400(self, client):
        resp = client.post("/api/news/annotate",
                           json={"article_id": self._ARTICLE_ID, "text": ""})
        assert resp.status_code == 400

    def test_delete_annotation(self, client):
        aid = "https://fake.test/delete-annotation"
        client.post("/api/news/annotate",
                    json={"article_id": aid, "text": "To be deleted"})
        del_resp = client.delete("/api/news/annotate", json={"article_id": aid})
        assert del_resp.status_code == 200
        assert del_resp.get_json()["status"] == "ok"

    def test_delete_missing_article_id_is_400(self, client):
        resp = client.delete("/api/news/annotate", json={})
        assert resp.status_code == 400

    def test_malformed_json_does_not_500(self, client):
        resp = client.post("/api/news/annotate", data="{not json",
                           content_type="application/json")
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════════════════════
#  8.  FRONT PAGE
# ═══════════════════════════════════════════════════════════════════════════════

class TestFrontPage:
    def test_latest_when_no_editions(self, client):
        resp = client.get("/api/news/front-page/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        # edition can be null when none generated yet
        assert "edition" in data

    def test_front_pages_list_returns_list(self, client):
        resp = client.get("/api/news/front-pages")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["editions"], list)

    def test_front_page_get_missing_id_returns_404(self, client):
        resp = client.get("/api/news/front-page/2099-01-01-morning")
        assert resp.status_code == 404

    # NOTE: POST /api/news/front-page/generate is an LLM + news-fetch background
    # job. The LLM stub covers the text generation, but _generate_front_page also
    # calls _gather_front_page_pool (stubbed above) and writes files.
    # We test it returns a valid JSON response rather than 500.
    def test_generate_returns_ok_or_error_json(self, client):
        resp = client.post("/api/news/front-page/generate", json={})
        # Should be 200 or a structured error — never an uncaught exception
        data = resp.get_json()
        assert data is not None
        assert "status" in data

    def test_generate_bad_slot_still_handles_gracefully(self, client):
        resp = client.post("/api/news/front-page/generate",
                           json={"slot": "not_a_real_slot"})
        data = resp.get_json()
        assert data is not None
        assert "status" in data


# ═══════════════════════════════════════════════════════════════════════════════
#  9.  EDITORIALS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEditorials:
    def test_editorial_latest_when_none(self, client):
        resp = client.get("/api/news/editorial/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "editorial" in data

    def test_editorials_list_returns_list(self, client):
        resp = client.get("/api/news/editorials")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["editorials"], list)

    def test_editorial_get_missing_week_is_404(self, client):
        resp = client.get("/api/news/editorial/1970-W01")
        assert resp.status_code == 404

    # NOTE: POST /api/news/editorial/generate is an LLM job; stub is active.
    def test_editorial_generate_returns_json(self, client):
        resp = client.post("/api/news/editorial/generate", json={})
        data = resp.get_json()
        assert data is not None
        assert "status" in data


# ═══════════════════════════════════════════════════════════════════════════════
#  10.  FRONT PAGE WEEKLY
# ═══════════════════════════════════════════════════════════════════════════════

class TestWeeklyDigest:
    def test_weekly_latest_when_none(self, client):
        resp = client.get("/api/news/front-page/weekly/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "digest" in data

    # NOTE: POST /api/news/front-page/weekly/generate calls _generate_weekly_digest
    # which internally calls _generate_text (stubbed) plus the archive helpers.
    def test_weekly_generate_returns_json(self, client):
        resp = client.post("/api/news/front-page/weekly/generate", json={})
        data = resp.get_json()
        assert data is not None
        assert "status" in data


# ═══════════════════════════════════════════════════════════════════════════════
#  11.  SHARE-TO-DRAFT
# ═══════════════════════════════════════════════════════════════════════════════

class TestShareToDraft:
    def test_post_creates_seed(self, client):
        payload = {
            "article_title": "The Future of AI",
            "article_url": "https://example.com/future-of-ai",
            "article_snippet": "AI is changing everything.",
        }
        resp = client.post("/api/news/share-to-draft", json=payload)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "draft_id" in data
        assert "seed" in data
        draft_id = data["draft_id"]

        # Round-trip: fetch the seed back
        get_resp = client.get(f"/api/news/share-to-draft/{draft_id}")
        assert get_resp.status_code == 200
        seed = get_resp.get_json()["seed"]
        assert seed["article_title"] == payload["article_title"]

    def test_post_missing_title_and_url_is_400(self, client):
        resp = client.post("/api/news/share-to-draft",
                           json={"article_snippet": "snippet only"})
        assert resp.status_code == 400

    def test_get_nonexistent_draft_is_404(self, client):
        resp = client.get("/api/news/share-to-draft/nonexistentid123")
        assert resp.status_code == 404

    def test_url_only_is_accepted(self, client):
        resp = client.post("/api/news/share-to-draft",
                           json={"article_url": "https://example.com/url-only"})
        assert resp.status_code == 200

    def test_malformed_json_does_not_500(self, client):
        resp = client.post("/api/news/share-to-draft", data="{not json",
                           content_type="application/json")
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════════════════════
#  12.  SOURCES (preferences / ban / boost)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSourcesPreferences:
    def test_get_preferences_shape(self, client):
        resp = client.get("/api/sources/preferences")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "banned" in data
        assert "boosted" in data


class TestSourcesBan:
    def test_ban_adds_source(self, client):
        resp = client.post("/api/sources/ban",
                           json={"source": "badnews.example.com"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "badnews.example.com" in data["banned"]

    def test_ban_missing_source_is_400(self, client):
        resp = client.post("/api/sources/ban", json={})
        assert resp.status_code == 400

    def test_ban_removes_from_boost(self, client):
        # Boost first, then ban — the source should disappear from boosted
        client.post("/api/sources/boost",
                    json={"source": "overlap.example.com"})
        resp = client.post("/api/sources/ban",
                           json={"source": "overlap.example.com"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "overlap.example.com" not in data["boosted"]

    def test_unban_removes_source(self, client):
        # ban then delete
        client.post("/api/sources/ban", json={"source": "unban.example.com"})
        resp = client.delete("/api/sources/ban",
                             json={"source": "unban.example.com"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "unban.example.com" not in data["banned"]

    def test_unban_missing_source_is_400(self, client):
        resp = client.delete("/api/sources/ban", json={})
        assert resp.status_code == 400

    def test_malformed_json_does_not_500(self, client):
        resp = client.post("/api/sources/ban", data="{not json",
                           content_type="application/json")
        assert resp.status_code < 500


class TestSourcesBoost:
    def test_boost_adds_source(self, client):
        resp = client.post("/api/sources/boost",
                           json={"source": "trusted.example.com"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "trusted.example.com" in data["boosted"]

    def test_boost_missing_source_is_400(self, client):
        resp = client.post("/api/sources/boost", json={})
        assert resp.status_code == 400

    def test_boost_removes_from_ban(self, client):
        client.post("/api/sources/ban",
                    json={"source": "flip.example.com"})
        resp = client.post("/api/sources/boost",
                           json={"source": "flip.example.com"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "flip.example.com" not in data["banned"]

    def test_unboost_removes_source(self, client):
        client.post("/api/sources/boost",
                    json={"source": "unboost.example.com"})
        resp = client.delete("/api/sources/boost",
                             json={"source": "unboost.example.com"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "unboost.example.com" not in data["boosted"]

    def test_unboost_missing_source_is_400(self, client):
        resp = client.delete("/api/sources/boost", json={})
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
#  13.  SOURCE TRUST GRAPH
#  Routes are gated on _HAS_TRUST_GRAPHS: return 200 or 501, never 500.
# ═══════════════════════════════════════════════════════════════════════════════

class TestSourceTrust:
    def test_get_all_reachable(self, client, assert_reachable):
        resp = client.get("/api/source-trust")
        assert assert_reachable(resp), f"/api/source-trust returned {resp.status_code}"
        # When available it returns ok; when unavailable it returns 501 — both are fine.
        data = resp.get_json()
        assert "status" in data

    def test_leaderboard_reachable(self, client, assert_reachable):
        resp = client.get("/api/source-trust/leaderboard")
        assert assert_reachable(resp)

    def test_leaderboard_custom_limit(self, client, assert_reachable):
        resp = client.get("/api/source-trust/leaderboard?limit=10")
        assert assert_reachable(resp)

    def test_observe_missing_domain_is_400_or_501(self, client):
        resp = client.post("/api/source-trust/observe",
                           json={"dimension": "accuracy"})
        # 400 when trust graph is available; 501 when it isn't
        assert resp.status_code in (400, 501)

    def test_observe_missing_dimension_is_400_or_501(self, client):
        resp = client.post("/api/source-trust/observe",
                           json={"domain": "example.com"})
        assert resp.status_code in (400, 501)

    def test_observe_valid_payload_reachable(self, client, assert_reachable):
        resp = client.post("/api/source-trust/observe", json={
            "domain": "example.com",
            "dimension": "accuracy",
            "signal": 0.8,
            "type": "user_observation",
        })
        # 200 (accepted) or 400 (invalid dimension) or 501 (unavailable) — not 500
        assert resp.status_code < 500

    def test_single_domain_not_found_or_501(self, client):
        resp = client.get("/api/source-trust/nonexistent.fake.test")
        assert resp.status_code in (404, 501)

    def test_malformed_json_does_not_500(self, client):
        resp = client.post("/api/source-trust/observe", data="{not json",
                           content_type="application/json")
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════════════════════
#  14.  FEDERATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestFederation:
    def test_attestations_reachable(self, client, assert_reachable):
        resp = client.get("/api/federation/attestations")
        assert assert_reachable(resp)
        data = resp.get_json()
        assert "status" in data

    def test_trust_scores_reachable(self, client, assert_reachable):
        resp = client.get("/api/federation/trust-scores")
        assert assert_reachable(resp)

    def test_sign_missing_domain_is_400_or_501(self, client):
        resp = client.post("/api/federation/attestations/sign",
                           json={"observation": {"type": "accuracy"}})
        assert resp.status_code in (400, 501)

    def test_sign_missing_observation_type_is_400_or_501(self, client):
        resp = client.post("/api/federation/attestations/sign",
                           json={"source_domain": "example.com", "observation": {}})
        assert resp.status_code in (400, 501)

    def test_sign_valid_payload_does_not_500(self, client):
        resp = client.post("/api/federation/attestations/sign", json={
            "source_domain": "example.com",
            "observation": {"type": "accuracy", "claim": "reliable", "evidence": ""},
        })
        # 200 (signed), 501 (no Ed25519 key / unavailable), not 500
        assert resp.status_code < 500

    def test_import_empty_dict_is_ok_or_501(self, client):
        # {} becomes [{}] → should accept/reject cleanly
        resp = client.post("/api/federation/attestations/import", json={})
        assert resp.status_code < 500
        data = resp.get_json()
        assert "status" in data

    def test_import_list_payload(self, client):
        resp = client.post("/api/federation/attestations/import",
                           json={"attestations": []})
        assert resp.status_code < 500

    def test_malformed_json_does_not_500(self, client):
        resp = client.post("/api/federation/attestations/sign", data="{not json",
                           content_type="application/json")
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════════════════════
#  15.  BRIEFINGS
# ═══════════════════════════════════════════════════════════════════════════════

class TestBriefings:
    def test_list_briefings_returns_shape(self, client):
        resp = client.get("/api/briefings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "briefings" in data
        assert "total" in data
        assert isinstance(data["briefings"], list)

    def test_briefing_status_returns_connectors(self, client):
        resp = client.get("/api/briefing/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "connectors" in data
        assert isinstance(data["connectors"], list)
        assert "google_connected" in data

    def test_briefing_preferences_get_shape(self, client):
        resp = client.get("/api/briefing/preferences")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "preferences" in data
        assert "categories" in data

    def test_briefing_preferences_post_roundtrip(self, client):
        prefs_payload = {"section_order": ["News", "Calendar", "Email"]}
        resp = client.post("/api/briefing/preferences", json=prefs_payload)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "preferences" in data

    def test_briefing_preferences_post_empty_body(self, client):
        resp = client.post("/api/briefing/preferences", json={})
        assert resp.status_code == 200

    def test_briefing_preferences_malformed_json_not_500(self, client):
        resp = client.post("/api/briefing/preferences", data="{not json",
                           content_type="application/json")
        assert resp.status_code < 500

    def test_get_briefing_nonexistent_is_404(self, client):
        resp = client.get("/api/briefing/nonexistent-file.html")
        assert resp.status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
