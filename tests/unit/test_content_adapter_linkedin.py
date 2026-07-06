"""Unit tests for the LinkedIn platform adapter (spec §4.3).

ALL transport is stubbed at the module-level ``linkedin._request`` seam —
zero live network. Covers: the versioned-REST publish happy path (headers +
payload + budget), the initializeUpload image flow with alt text, OAuth
callback state binding + auth-blob round-trip through a fake credential
store, the ~60-day member-token expiry surfacing (needs_reauth /
expiring_soon / refresh()), fixed content-free error strings (§12.5), the
§4.14 article degradation, and the conservative 25/day local budget.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import platforms as preg                    # noqa: E402
from agent_friday.services.platforms import base as pbase              # noqa: E402
from agent_friday.services.platforms import linkedin as li             # noqa: E402
from agent_friday.services.platforms.linkedin import LinkedInAdapter   # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    preg._reset_for_tests()
    yield
    preg._reset_for_tests()


@pytest.fixture(autouse=True)
def fake_store(monkeypatch):
    """Fake credential store: plaintext blob files under the tmp platforms dir
    (so the round-trip is observable) + in-memory provider keys + audit capture."""
    from agent_friday.services import credential_store as cs
    simple, events = {}, []

    def _write(path, data):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(bytes(data))
        return "fake"

    monkeypatch.setattr(cs, "write_secret", _write)
    monkeypatch.setattr(cs, "read_secret", lambda path: Path(path).read_bytes())
    monkeypatch.setattr(cs, "audit_event",
                        lambda category, event, **f: events.append((category, event, f)))
    monkeypatch.setattr(cs, "set_provider_key",
                        lambda prov, v: (simple.__setitem__(prov, v), "fake")[1])
    monkeypatch.setattr(cs, "get_provider_key", lambda prov: simple.get(prov))
    monkeypatch.setattr(cs, "delete_provider_key",
                        lambda prov: simple.pop(prov, None) is not None)
    return {"simple": simple, "events": events}


@pytest.fixture
def http(monkeypatch):
    """Stub the transport seam. Routes match (method, url-fragment) in
    registration order; unmatched requests answer 599 so nothing passes by
    accident."""
    calls = []
    routes = []

    def stub(method, url, *, headers=None, data=None, timeout=30.0):
        calls.append({"method": method, "url": url,
                      "headers": dict(headers or {}), "data": data})
        for (m, frag), resp in routes:
            if method == m and frag in url:
                return dict(resp)
        return {"status": 599, "headers": {}, "body": b""}

    def route(method, frag, status=200, body=None, headers=None):
        raw = (json.dumps(body).encode("utf-8") if isinstance(body, (dict, list))
               else (body or b""))
        routes.append(((method, frag),
                       {"status": status, "headers": dict(headers or {}), "body": raw}))

    stub.calls, stub.route = calls, route
    monkeypatch.setattr(li, "_request", stub)
    return stub


def _iso_in(days: float) -> str:
    return (pbase._now_utc() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connected(days_left: float = 45.0, **extra) -> LinkedInAdapter:
    a = LinkedInAdapter()
    a.configure({"client_id": "cid-123"})
    blob = {
        "access_token": "at-test-value",     # pragma: allowlist secret
        "expires_at": _iso_in(days_left),
        "scopes": ["w_member_social", "openid", "profile"],
        "account": "Test Member",
        "person_urn": "urn:li:person:abc123",
    }
    blob.update(extra)
    assert a.save_credentials(blob)["ok"]
    return a


def _posts_call(http):
    hits = [c for c in http.calls if "/rest/posts" in c["url"] and c["method"] == "POST"]
    assert len(hits) == 1, f"expected exactly one POST /rest/posts, got {len(hits)}"
    return hits[0]


# ── publish happy path (versioned REST) ──────────────────────────────────────
def test_publish_happy_path_versioned_rest(http):
    http.route("POST", "/rest/posts", status=201,
               headers={"x-restli-id": "urn:li:share:6543"}, body={})
    a = _connected()
    prep = a.prepare({"adapted_body": "Hello LinkedIn", "format": "post",
                      "id": "tgt_1"}, {"id": "post_1"})
    assert prep["ok"], prep
    res = a.publish(prep["prepared"])
    assert res["ok"] is True
    assert res["platform_post_id"] == "urn:li:share:6543"
    assert res["post_url"] == "https://www.linkedin.com/feed/update/urn:li:share:6543/"

    call = _posts_call(http)
    # versioned REST: LinkedIn-Version + Restli protocol headers (§4.3)
    assert call["headers"]["LinkedIn-Version"] == li._DEFAULT_VERSION
    assert call["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
    assert call["headers"]["Authorization"].startswith("Bearer ")
    payload = json.loads(call["data"])
    assert payload["author"] == "urn:li:person:abc123"
    assert payload["commentary"] == "Hello LinkedIn"
    assert payload["lifecycleState"] == "PUBLISHED"
    assert payload["visibility"] == "PUBLIC"
    # a successful publish consumes the local budget
    assert a.rate_budget()["used"] == 1


def test_publish_requires_connection(http):
    a = LinkedInAdapter()
    a.configure({})
    res = a.publish({"body": "x", "format": "post", "options": {}})
    assert res == {"ok": False, "error": "not_connected"}
    assert http.calls == []


def test_publish_without_identity_is_fixed_error(http):
    a = _connected(person_urn="")
    res = a.publish({"body": "x", "format": "post", "options": {}})
    assert res["ok"] is False and res["error"] == "identity_missing"
    assert http.calls == []


# ── initializeUpload media flow (§4.3) ───────────────────────────────────────
def test_image_initialize_upload_flow_with_alt_text(http, tmp_path):
    img = tmp_path / "fox.png"
    img.write_bytes(b"PNGDATA")
    http.route("POST", "/rest/images?action=initializeUpload", status=200,
               body={"value": {"uploadUrl": "https://upload.example/u1",
                               "image": "urn:li:image:img1"}})
    http.route("PUT", "upload.example/u1", status=201)
    http.route("POST", "/rest/posts", status=201,
               headers={"x-restli-id": "urn:li:share:77"}, body={})

    a = _connected()
    target = {"adapted_body": "look at this fox", "format": "image_post",
              "id": "tgt_img",
              "adapted_assets": [{"kind": "image", "out_path": str(img),
                                  "alt_text": "a red fox"}]}
    prep = a.prepare(target, {"id": "post_img"})
    assert prep["ok"], prep
    assert prep["prepared"]["media"] == [
        {"id": "urn:li:image:img1", "kind": "image", "alt_text": "a red fox"}]
    # the adapted asset gains its platform media id (PlatformTarget shape)
    assert target["adapted_assets"][0]["platform_media_id"] == "urn:li:image:img1"

    init = [c for c in http.calls if "initializeUpload" in c["url"]][0]
    assert json.loads(init["data"]) == {
        "initializeUploadRequest": {"owner": "urn:li:person:abc123"}}
    assert init["headers"]["LinkedIn-Version"] == li._DEFAULT_VERSION
    put = [c for c in http.calls if c["method"] == "PUT"][0]
    assert put["data"] == b"PNGDATA"
    assert put["headers"]["Content-Type"] == "application/octet-stream"

    res = a.publish(prep["prepared"])
    assert res["ok"] is True
    payload = json.loads(_posts_call(http)["data"])
    assert payload["content"] == {"media": {"id": "urn:li:image:img1",
                                            "altText": "a red fox"}}


def test_multi_image_payload_uses_multiimage(http):
    http.route("POST", "/rest/posts", status=201,
               headers={"X-RestLi-Id": "urn:li:share:88"}, body={})
    a = _connected()
    res = a.publish({"body": "two pics", "format": "image_post", "options": {},
                     "media": [{"id": "urn:li:image:a", "alt_text": "first"},
                               {"id": "urn:li:image:b", "alt_text": ""}]})
    assert res["ok"] is True and res["platform_post_id"] == "urn:li:share:88"
    payload = json.loads(_posts_call(http)["data"])
    assert payload["content"] == {"multiImage": {"images": [
        {"id": "urn:li:image:a", "altText": "first"}, {"id": "urn:li:image:b"}]}}


def test_missing_asset_file_is_fixed_error(http):
    a = _connected()
    prep = a.prepare({"adapted_body": "x", "format": "image_post",
                      "adapted_assets": [{"kind": "image",
                                          "out_path": "Z:/nope/gone.png"}]},
                     {"id": "p"})
    assert prep["ok"] is False and prep["error"] == "asset_missing"
    assert http.calls == []   # nothing uploaded, nothing leaked


def test_link_post_builds_article_content(http):
    http.route("POST", "/rest/posts", status=201,
               headers={"x-restli-id": "urn:li:share:99"}, body={})
    a = _connected()
    res = a.publish({"body": "read this", "title": "My Piece",
                     "format": "link_post",
                     "options": {"link": "https://example.com/post"}, "media": []})
    assert res["ok"] is True
    payload = json.loads(_posts_call(http)["data"])
    assert payload["content"]["article"]["source"] == "https://example.com/post"
    assert payload["content"]["article"]["title"] == "My Piece"
    # link_post without a link is a fixed content-free error
    bad = a.publish({"body": "x", "format": "link_post", "options": {}})
    assert bad["ok"] is False and bad["error"] == "link_missing"


# ── OAuth: state binding + auth blob round-trip ──────────────────────────────
def test_connect_url_requires_client_and_binds_state():
    a = LinkedInAdapter()
    a.configure({})
    assert a.connect_url("s1") is None                 # no client_id configured
    assert a.status()["last_error"] == "client_not_configured"

    a.configure({"client_id": "cid-123"})
    url = a.connect_url("state-xyz")
    assert url.startswith(li._OAUTH_BASE + "/authorization?")
    assert "client_id=cid-123" in url
    assert "state=state-xyz" in url
    assert "w_member_social" in url
    from urllib.parse import quote
    assert quote(li._REDIRECT_URI, safe="") in url     # loopback redirect, encoded


def test_oauth_callback_state_mismatch_fails_closed(http):
    a = LinkedInAdapter()
    a.configure({"client_id": "cid-123"})
    a.set_simple_secret("cs-1")
    a.connect_url("expected-state")
    st = a.handle_callback({"code": "c0de", "state": "WRONG"})
    assert st["connected"] is False
    assert st["last_error"] == "state_mismatch"
    assert http.calls == []                            # no exchange attempted
    # state was single-use: even the right state is now rejected
    st2 = a.handle_callback({"code": "c0de", "state": "expected-state"})
    assert st2["connected"] is False
    assert http.calls == []


def test_oauth_callback_happy_path_and_blob_round_trip(http):
    http.route("POST", "/oauth/v2/accessToken", status=200,
               body={"access_token": "at-1", "expires_in": 5184000,
                     "scope": "w_member_social,openid,profile",
                     "refresh_token": "rt-1",
                     "refresh_token_expires_in": 31536000})
    http.route("GET", "/v2/userinfo", status=200,
               body={"sub": "xyz789", "name": "Ada L"})

    a = LinkedInAdapter()
    a.configure({"client_id": "cid-123"})
    a.set_simple_secret("cs-1")
    a.connect_url("state-1")
    st = a.handle_callback({"code": "c0de", "state": "state-1"})
    assert st["connected"] is True
    assert st["account"] == "Ada L"
    assert st["scopes"] == ["w_member_social", "openid", "profile"]
    assert st["expires_at"] and st["expires_in_days"] >= 59
    # nothing tokenish ever leaves status() (§12.2)
    dumped = json.dumps(st)
    assert "at-1" not in dumped and "rt-1" not in dumped and "cs-1" not in dumped

    exchange = [c for c in http.calls if "accessToken" in c["url"]][0]
    form = exchange["data"].decode("utf-8")
    assert "grant_type=authorization_code" in form
    assert "code=c0de" in form
    from urllib.parse import quote
    assert quote(li._REDIRECT_URI, safe="") in form

    # blob round-trip: a fresh instance reads the same encrypted-store blob
    assert (pbase.PLATFORMS_DIR / "linkedin.cred").exists()
    b = LinkedInAdapter()
    b.configure({})
    creds = b.load_credentials()
    assert creds["access_token"] == "at-1"             # pragma: allowlist secret
    assert creds["refresh_token"] == "rt-1"            # pragma: allowlist secret
    assert creds["person_urn"] == "urn:li:person:xyz789"


# ── the sharp constraint: ~60-day token expiry surfacing (§4.3) ──────────────
def test_expiry_surfacing_fresh_soon_and_expired(http):
    fresh = _connected(days_left=45)
    st = fresh.status()
    assert st["connected"] is True
    assert st["needs_reauth"] is False and st["expiring_soon"] is False
    assert 44 <= st["expires_in_days"] <= 45
    assert fresh.refresh() is True

    soon = _connected(days_left=3)
    st = soon.status()
    assert st["expiring_soon"] is True and st["needs_reauth"] is False
    assert soon.refresh() is True                      # still usable

    expired = _connected(days_left=-1)
    st = expired.status()
    assert st["needs_reauth"] is True
    assert st["expires_in_days"] == 0
    assert expired.refresh() is False                  # unusable → re-auth prompt
    res = expired.publish({"body": "x", "format": "post", "options": {}})
    assert res["ok"] is False and res["error"] == "auth_expired"
    assert http.calls == []                            # never hit the wire


def test_expired_token_renews_via_partner_refresh_grant(http):
    http.route("POST", "/oauth/v2/accessToken", status=200,
               body={"access_token": "at-2", "expires_in": 5184000})
    a = _connected(days_left=-1, refresh_token="rt-1")
    a.set_simple_secret("cs-1")
    assert a.refresh() is True
    form = http.calls[0]["data"].decode("utf-8")
    assert "grant_type=refresh_token" in form
    creds = a.load_credentials()
    assert creds["access_token"] == "at-2"             # pragma: allowlist secret
    assert a.status()["needs_reauth"] is False


# ── fixed content-free error strings (§12.5) ─────────────────────────────────
@pytest.mark.parametrize("status,expected", [
    (500, "server_error"),
    (429, "rate_limited"),
    (401, "auth_error"),
    (403, "permission_denied"),
    (0, "network_error"),
    (422, "publish_failed"),
])
def test_publish_errors_are_fixed_strings(http, status, expected):
    juicy = {"message": "boom at C:/Users/someone/private/file.txt",
             "serviceErrorCode": 12345}
    headers = {"Retry-After": "120"} if status == 429 else {}
    http.route("POST", "/rest/posts", status=status, body=juicy, headers=headers)
    a = _connected()
    res = a.publish({"body": "x", "format": "post", "options": {}})
    assert res["ok"] is False and res["error"] == expected
    if status == 429:
        assert res["retry_after"] == 120
    dumped = json.dumps(res)
    assert "private" not in dumped and "boom" not in dumped   # no body leak
    assert "at-test-value" not in dumped                       # no token leak
    assert a.status()["last_error"] == expected                # fixed in status too


def test_delete_versioned_rest(http):
    http.route("DELETE", "/rest/posts/", status=204)
    a = _connected()
    assert a.delete("urn:li:share:6543") == {"ok": True}
    call = http.calls[0]
    assert "urn%3Ali%3Ashare%3A6543" in call["url"]     # URN URL-encoded
    assert call["headers"]["LinkedIn-Version"] == li._DEFAULT_VERSION


# ── §4.14: articles degrade loudly, never silently ───────────────────────────
def test_article_format_degrades_honestly(http):
    a = _connected()
    env = a.prepare({"adapted_body": "long piece", "format": "article"}, {})
    assert env["ok"] is False and env["degraded"] is True
    assert env["requires_user_choice"] is True
    assert env["declared_rung"] == "api"
    assert env["options"] == ["api_constrained", "assisted_handoff", "clipboard"]
    assert http.calls == []


# ── capabilities honesty (§4.2 / §4.3) ───────────────────────────────────────
def test_capabilities_member_vs_org():
    a = LinkedInAdapter()
    a.configure({})
    caps = a.capabilities()
    assert caps["char_limit"] == 3000
    assert caps["thread"] is False
    assert caps["native_schedule"] is False
    assert caps["native_delete"] is True
    assert caps["analytics"] == "counts"               # member = counts only
    assert "article" not in caps["formats"]            # no public write API
    assert caps["media"]["alt_text"] is True
    assert caps["media"]["video"]["max_s"] == 30 * 60

    a.configure({"organization_urn": "urn:li:organization:9"})
    assert a.capabilities()["analytics"] == "full"     # org page → full


# ── analytics mapping (§8.2 LinkedIn column) ─────────────────────────────────
def test_fetch_metrics_member_counts_only(http):
    http.route("GET", "/rest/socialActions/", status=200,
               body={"likesSummary": {"totalLikes": 12},
                     "commentsSummary": {"aggregatedTotalComments": 4}})
    a = _connected()
    m = a.fetch_metrics("urn:li:share:1")
    assert m == {"likes": 12, "comments": 4}           # missing ≠ zero: no impressions


def test_fetch_metrics_org_adds_impressions(http):
    http.route("GET", "/rest/socialActions/", status=200,
               body={"likesSummary": {"totalLikes": 2},
                     "commentsSummary": {"totalFirstLevelComments": 1}})
    http.route("GET", "organizationalEntityShareStatistics", status=200,
               body={"elements": [{"totalShareStatistics": {
                   "impressionCount": 900, "clickCount": 30, "shareCount": 7}}]})
    a = _connected()
    a.configure({"client_id": "cid-123",
                 "organization_urn": "urn:li:organization:9"})
    m = a.fetch_metrics("urn:li:share:1")
    assert m == {"likes": 2, "comments": 1,
                 "impressions": 900, "clicks": 30, "shares": 7}


def test_fetch_metrics_unavailable_is_none(http):
    a = LinkedInAdapter()
    a.configure({})
    assert a.fetch_metrics("urn:li:share:1") is None   # not connected
    assert a.fetch_account_metrics() is None           # member API can't report


# ── conservative local budget (§4.3: 25/day default) ─────────────────────────
def test_local_budget_conservative_default():
    a = LinkedInAdapter()
    a.configure({})
    assert a.default_daily_limit == 25
    b = a.rate_budget()
    assert b["limit"] == 25 and b["used"] == 0 and b["window"] == "day"
    a.consume_budget(24)
    assert a.budget_would_exceed() is False            # 25th post still fits
    a.consume_budget(1)
    assert a.budget_would_exceed() is True             # 26th would break it
    # configurable per the spec
    a.configure({"daily_post_limit": 3})
    assert a.rate_budget()["limit"] == 3


# ── registry pickup ──────────────────────────────────────────────────────────
def test_registry_serves_linkedin_adapter():
    a = preg.get_adapter("linkedin")
    assert isinstance(a, LinkedInAdapter)
    assert preg.ADAPTER_MODULES["linkedin"] == "linkedin"
