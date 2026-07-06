"""TikTok adapter unit tests (spec §4.12 / §15) — transport fully stubbed.

Covers: capability honesty, OAuth PKCE + state binding, auth blob round-trip
through a fake credential store, refresh, the chunked FILE_UPLOAD happy path,
the SELF_ONLY honesty pre-audit (§4.14 ladder surfaced, never silent), photo
mode staging requirement, the ≤5/day budget, metric normalization to the §8.2
unified keys, and fixed content-free error strings (§12.5). Zero live network:
every byte rides the module-level ``tiktok._http`` indirection, which is
replaced wholesale here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import credential_store as cs          # noqa: E402
from agent_friday.services.platforms import base as pbase         # noqa: E402
from agent_friday.services.platforms import tiktok as tk          # noqa: E402
from agent_friday.services.platforms.tiktok import (              # noqa: E402
    TikTokAdapter, _chunk_plan)

_FAKE_ACCESS = "at-test-123"        # pragma: allowlist secret
_FAKE_REFRESH = "rt-test-456"       # pragma: allowlist secret

_FIXED_ERRORS = {
    "not_connected", "client_key_missing", "oauth_state_mismatch",
    "oauth_callback_failed", "auth_failed", "transport_error",
    "tiktok_api_error", "asset_unreadable", "upload_failed",
    "publish_failed", "publish_status_timeout", "prepare_failed", "degraded",
}


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH",
                        tmp_path / "platforms" / "rate_budget.json")


@pytest.fixture(autouse=True)
def _fake_credstore(monkeypatch):
    """Plain-file secret store + in-memory provider keys — no DPAPI/vault."""
    provider_keys = {}
    audits = []

    def fake_write(path, data):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(bytes(data))
        return "fake"

    monkeypatch.setattr(cs, "write_secret", fake_write)
    monkeypatch.setattr(cs, "read_secret", lambda path: Path(path).read_bytes())
    monkeypatch.setattr(cs, "set_provider_key",
                        lambda provider, key: (provider_keys.__setitem__(provider, key), "fake")[1])
    monkeypatch.setattr(cs, "get_provider_key", provider_keys.get)
    monkeypatch.setattr(cs, "delete_provider_key",
                        lambda provider: provider_keys.pop(provider, None) is not None)
    monkeypatch.setattr(cs, "audit_event",
                        lambda category, event, **fields: audits.append((category, event, fields)))
    return {"provider_keys": provider_keys, "audits": audits}


class FakeHTTP:
    """Scripted stand-in for tiktok._http — records calls, routes by URL
    fragment, and never touches a socket."""

    def __init__(self):
        self.calls = []
        self.routes = {}      # url fragment -> callable(method, url, headers, body) -> resp

    def __call__(self, method, url, *, headers=None, body=None,
                 timeout=30, sensitive=False):
        self.calls.append({"method": method, "url": url,
                           "headers": dict(headers or {}), "body": body,
                           "sensitive": sensitive})
        for frag, fn in self.routes.items():
            if frag in url:
                return fn(method, url, dict(headers or {}), body)
        return {"status": 404, "json": None, "body": b"", "error": "tiktok_api_error"}

    def sent_to(self, frag):
        return [c for c in self.calls if frag in c["url"]]


def _ok(data):
    """A successful TikTok data envelope."""
    return {"status": 200, "body": b"",
            "json": {"data": data, "error": {"code": "ok", "message": ""}},
            "error": None}


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(tk, "_http", fake)
    return fake


def _adapter(**config):
    a = TikTokAdapter()
    base = {"client_key": "ck_test", "status_poll_interval_s": 0}
    base.update(config)
    a.configure(base)
    return a


def _connect(adapter):
    """Install a live-looking OAuth blob directly (round-trip tested apart)."""
    adapter.save_credentials({
        "access_token": _FAKE_ACCESS,        # pragma: allowlist secret
        "refresh_token": _FAKE_REFRESH,      # pragma: allowlist secret
        "open_id": "oid-1",
        "account": "Friday Test",
        "scopes": ["video.publish", "video.upload", "video.list"],
        "expires_at": "2099-01-01T00:00:00Z",
    })


def _creator_route(http, privacy=("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS",
                                  "FOLLOWER_OF_CREATOR", "SELF_ONLY"),
                   username="fridaytest", max_s=600):
    http.routes["creator_info/query"] = lambda m, u, h, b: _ok({
        "creator_username": username,
        "privacy_level_options": list(privacy),
        "max_video_post_duration_sec": max_s,
    })


def _video_target(tmp_path, size=3000, **options):
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00" * size)
    return {
        "id": "tgt_1", "format": "video_upload",
        "adapted_body": "Friday made a thing today",
        "hashtags": ["fridayai", "aiart"],
        "adapted_assets": [{"kind": "video", "out_path": str(vid),
                            "duration_s": 12.0}],
        "options": dict(options),
    }


# ── capabilities ──────────────────────────────────────────────────────────────
def test_capabilities_honest():
    a = _adapter()
    caps = a.capabilities()
    assert caps["char_limit"] == 2200
    assert caps["analytics"] == "counts"
    assert caps["thread"] is False
    assert caps["native_schedule"] is False
    assert caps["native_delete"] is False
    assert "video_upload" in caps["formats"] and "image_post" in caps["formats"]
    assert caps["media"]["images"]["max"] == 35          # photo mode
    assert caps["media"]["video"] is not None
    assert caps["media"]["alt_text"] is False
    assert a.default_daily_limit == 5                    # §4.12 budget
    assert a.rate_budget()["limit"] == 5


# ── auth lifecycle ────────────────────────────────────────────────────────────
def test_connect_url_pkce_and_state():
    a = _adapter()
    url = a.connect_url("state-abc")
    assert url and url.startswith("https://www.tiktok.com/v2/auth/authorize/")
    q = parse_qs(urlparse(url).query)
    assert q["client_key"] == ["ck_test"]
    assert q["state"] == ["state-abc"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"][0]                        # PKCE challenge present
    assert q["redirect_uri"] == [
        "http://localhost:3000/api/content/platforms/tiktok/callback"]
    assert "video.publish" in q["scope"][0]


def test_connect_url_requires_client_key():
    a = TikTokAdapter()
    a.configure({})
    assert a.connect_url("s") is None
    assert a.status()["last_error"] == "client_key_missing"


def test_callback_rejects_unknown_state(http):
    a = _adapter()
    a.connect_url("state-good")
    st = a.handle_callback({"code": "c1", "state": "state-EVIL"})
    assert st["connected"] is False
    assert st["last_error"] == "oauth_state_mismatch"
    assert http.sent_to("oauth/token") == []             # no exchange attempted


def test_oauth_callback_round_trip(http):
    http.routes["oauth/token"] = lambda m, u, h, b: {
        "status": 200, "body": b"", "error": None,
        "json": {"access_token": _FAKE_ACCESS,           # pragma: allowlist secret
                 "refresh_token": _FAKE_REFRESH,         # pragma: allowlist secret
                 "expires_in": 86400, "refresh_expires_in": 31536000,
                 "open_id": "oid-1",
                 "scope": "user.info.basic,video.publish,video.upload,video.list"}}
    http.routes["user/info"] = lambda m, u, h, b: _ok(
        {"user": {"open_id": "oid-1", "display_name": "Friday Test"}})

    a = _adapter()
    a.connect_url("st-1")
    st = a.handle_callback({"code": "code-xyz", "state": "st-1"})
    assert st["connected"] is True
    assert st["account"] == "Friday Test"
    assert "video.publish" in st["scopes"]
    assert st["expires_at"]

    # exchange used PKCE + the auth code, form-encoded, sensitive
    ex = http.sent_to("oauth/token")[0]
    form = parse_qs(ex["body"].decode("ascii"))
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["code-xyz"]
    assert form["code_verifier"][0]
    assert ex["sensitive"] is True

    # blob persisted and readable; state entry was single-use
    blob = a.load_credentials()
    assert blob["access_token"] == _FAKE_ACCESS
    st2 = a.handle_callback({"code": "code-xyz", "state": "st-1"})
    assert st2["last_error"] == "oauth_state_mismatch"

    # nothing tokenish in status() (§12.2)
    assert _FAKE_ACCESS not in json.dumps(st)
    assert _FAKE_REFRESH not in json.dumps(st)


def test_auth_blob_round_trip():
    a = _adapter()
    assert a.load_credentials() is None
    assert a.status()["connected"] is False
    _connect(a)
    blob = a.load_credentials()
    assert blob["account"] == "Friday Test"
    assert a.has_credentials() is True
    assert a.status()["connected"] is True
    res = a.clear_credentials()
    assert res["ok"] is True and res["removed"] is True
    assert a.load_credentials() is None
    assert a.status()["connected"] is False


def test_client_secret_alone_is_not_connected():
    a = _adapter()
    a.set_simple_secret("cs-app-secret")                 # pragma: allowlist secret
    assert a.status()["connected"] is False              # no OAuth blob yet


def test_refresh_paths(http):
    a = _adapter()
    assert a.refresh() is False                          # nothing stored

    _connect(a)
    assert a.refresh() is True                           # fresh — no HTTP call
    assert http.sent_to("oauth/token") == []

    # expired → refresh-token grant fires and the blob rolls forward
    blob = a.load_credentials()
    blob["expires_at"] = "2020-01-01T00:00:00Z"
    a.save_credentials(blob)
    http.routes["oauth/token"] = lambda m, u, h, b: {
        "status": 200, "body": b"", "error": None,
        "json": {"access_token": "at-new-789",           # pragma: allowlist secret
                 "refresh_token": _FAKE_REFRESH,
                 "expires_in": 86400, "open_id": "oid-1",
                 "scope": "video.publish"}}
    assert a.refresh() is True
    form = parse_qs(http.sent_to("oauth/token")[0]["body"].decode("ascii"))
    assert form["grant_type"] == ["refresh_token"]
    assert a.load_credentials()["access_token"] == "at-new-789"


def test_revoke_clears_and_is_idempotent(http):
    a = _adapter()
    _connect(a)
    http.routes["oauth/revoke"] = lambda m, u, h, b: {
        "status": 200, "json": {}, "body": b"", "error": None}
    assert a.revoke() is True
    assert a.status()["connected"] is False
    assert a.revoke() is True                            # idempotent, no creds


# ── publish happy path: chunked FILE_UPLOAD ───────────────────────────────────
def test_publish_video_happy_path(tmp_path, http):
    a = _adapter()
    _connect(a)
    _creator_route(http)
    http.routes["publish/video/init"] = lambda m, u, h, b: _ok({
        "publish_id": "v_pub_1",
        "upload_url": "https://upload.tiktokapis.example/v_pub_1"})
    http.routes["upload.tiktokapis.example"] = lambda m, u, h, b: {
        "status": 201, "json": None, "body": b"", "error": None}
    http.routes["status/fetch"] = lambda m, u, h, b: _ok({
        "status": "PUBLISH_COMPLETE",
        "publicaly_available_post_id": ["7412345"]})

    prep = a.prepare(_video_target(tmp_path, size=3000), {"id": "post_1"})
    assert prep["ok"], prep
    p = prep["prepared"]
    assert p["options"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert p["video_size"] == 3000
    assert (p["chunk_size"], p["total_chunk_count"]) == (3000, 1)
    assert "#fridayai" in p["caption"] and len(p["caption"]) <= 2200

    res = a.publish(p)
    assert res["ok"] is True
    assert res["platform_post_id"] == "7412345"
    assert res["post_url"] == "https://www.tiktok.com/@fridaytest/video/7412345"

    # init payload declared the FILE_UPLOAD chunk plan
    init = json.loads(http.sent_to("publish/video/init")[0]["body"])
    assert init["source_info"] == {"source": "FILE_UPLOAD", "video_size": 3000,
                                   "chunk_size": 3000, "total_chunk_count": 1}
    assert init["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    # the single chunk went up with a correct Content-Range
    up = http.sent_to("upload.tiktokapis.example")[0]
    assert up["method"] == "PUT"
    assert up["headers"]["Content-Range"] == "bytes 0-2999/3000"
    assert len(up["body"]) == 3000
    # success consumed the local budget (§4.12)
    assert a.rate_budget()["used"] == 1


def test_chunk_plan_math():
    mb = 1024 * 1024
    assert _chunk_plan(3 * mb) == (3 * mb, 1)            # small → whole file
    assert _chunk_plan(64 * mb) == (64 * mb, 1)          # fits one max chunk
    size = 130 * mb
    chunk, count = _chunk_plan(size)
    assert chunk == 50 * mb and count == 2               # floored count
    trailer = size - (count - 1) * chunk                 # final chunk absorbs rest
    assert 5 * mb <= chunk <= 64 * mb
    assert trailer < 128 * mb


def test_upload_failure_is_fixed_string(tmp_path, http):
    a = _adapter()
    _connect(a)
    _creator_route(http)
    http.routes["publish/video/init"] = lambda m, u, h, b: _ok({
        "publish_id": "v_pub_2", "upload_url": "https://upload.example/x"})
    http.routes["upload.example"] = lambda m, u, h, b: {
        "status": 0, "json": None, "body": b"", "error": "transport_error"}
    prep = a.prepare(_video_target(tmp_path), {"id": "post_1"})
    res = a.publish(prep["prepared"])
    assert res["ok"] is False and res["error"] == "upload_failed"
    assert a.rate_budget()["used"] == 0                  # no budget burned


# ── the sharp constraint: SELF_ONLY honesty pre-audit (§4.12 / §4.14) ─────────
def test_self_only_preaudit_surfaces_ladder_choice(tmp_path, http):
    a = _adapter()
    _connect(a)
    _creator_route(http, privacy=("SELF_ONLY",))         # unaudited app

    res = a.prepare(_video_target(tmp_path), {"id": "post_1"})
    assert res["ok"] is False
    assert res["degraded"] is True                       # never a silent fall
    assert res["requires_user_choice"] is True
    assert res["declared_rung"] == "api"
    assert "api_constrained" in res["options"]
    assert http.sent_to("publish/video/init") == []      # nothing was posted


def test_self_only_consented_lands_as_inbox_draft(tmp_path, http):
    a = _adapter()
    _connect(a)
    _creator_route(http, privacy=("SELF_ONLY",))
    http.routes["publish/video/init"] = lambda m, u, h, b: _ok({
        "publish_id": "v_pub_3", "upload_url": "https://upload.example/y"})
    http.routes["upload.example"] = lambda m, u, h, b: {
        "status": 201, "json": None, "body": b"", "error": None}
    http.routes["status/fetch"] = lambda m, u, h, b: _ok(
        {"status": "SEND_TO_USER_INBOX"})

    prep = a.prepare(_video_target(tmp_path, accept_self_only=True), {"id": "p1"})
    assert prep["ok"] is True
    assert prep["prepared"]["options"]["privacy_level"] == "SELF_ONLY"
    assert prep["prepared"]["self_only_downgrade"] is True
    assert any("private draft" in w for w in prep["warnings"])

    init_privacy = None
    res = a.publish(prep["prepared"])
    assert res["ok"] is True and res["inbox_draft"] is True
    assert res["platform_post_id"] == "v_pub_3"
    init_privacy = json.loads(
        http.sent_to("publish/video/init")[0]["body"])["post_info"]["privacy_level"]
    assert init_privacy == "SELF_ONLY"


def test_self_only_requested_explicitly_passes_preaudit(tmp_path, http):
    a = _adapter()
    _connect(a)
    _creator_route(http, privacy=("SELF_ONLY",))
    prep = a.prepare(_video_target(tmp_path, privacy_level="SELF_ONLY"),
                     {"id": "p1"})
    assert prep["ok"] is True                            # allowed level as-is
    assert prep["prepared"]["options"]["privacy_level"] == "SELF_ONLY"


# ── photo mode (§4.12): PULL_FROM_URL needs staging ───────────────────────────
def test_photo_mode_without_staging_surfaces_choice(http):
    a = _adapter()
    _connect(a)
    _creator_route(http)
    res = a.prepare({"id": "t1", "format": "image_post",
                     "adapted_body": "photo dump",
                     "adapted_assets": [{"kind": "image", "alt_text": "x"}]},
                    {"id": "p1"})
    assert res["ok"] is False and res.get("degraded") is True
    assert res["requires_user_choice"] is True


def test_photo_mode_publish_with_staged_urls(http):
    a = _adapter()
    _connect(a)
    _creator_route(http)
    http.routes["publish/content/init"] = lambda m, u, h, b: _ok(
        {"publish_id": "p_pub_1"})
    http.routes["status/fetch"] = lambda m, u, h, b: _ok(
        {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["88"]})

    prep = a.prepare({"id": "t1", "format": "image_post",
                      "adapted_title": "Gallery",
                      "adapted_body": "photo dump",
                      "adapted_assets": [
                          {"kind": "image", "alt_text": "a",
                           "staged_url": "https://stage.example/1.jpg"},
                          {"kind": "image", "alt_text": "b",
                           "staged_url": "https://stage.example/2.jpg"}],
                      "options": {}}, {"id": "p1"})
    assert prep["ok"], prep
    res = a.publish(prep["prepared"])
    assert res["ok"] is True and res["platform_post_id"] == "88"

    init = json.loads(http.sent_to("publish/content/init")[0]["body"])
    assert init["media_type"] == "PHOTO"
    assert init["post_mode"] == "DIRECT_POST"
    assert init["source_info"]["source"] == "PULL_FROM_URL"
    assert init["source_info"]["photo_images"] == [
        "https://stage.example/1.jpg", "https://stage.example/2.jpg"]
    assert init["post_info"]["title"] == "Gallery"


# ── local rate budget: ≤5/day (§4.12) ────────────────────────────────────────
def test_budget_five_per_day():
    a = _adapter()
    assert a.budget_would_exceed() is False
    a.consume_budget(5)
    assert a.rate_budget() == {"window": "day", "used": 5, "limit": 5,
                               "reset_at": a.rate_budget()["reset_at"]}
    assert a.budget_would_exceed() is True               # publisher defers


# ── analytics: Display API counts → §8.2 unified keys ─────────────────────────
def test_fetch_metrics_unified_mapping(http):
    a = _adapter()
    _connect(a)
    http.routes["video/query"] = lambda m, u, h, b: _ok({
        "videos": [{"id": "7412345", "like_count": 5, "comment_count": 2,
                    "share_count": 1, "view_count": 100}]})
    m = a.fetch_metrics("7412345")
    assert m == {"impressions": 100, "video_views": 100,
                 "likes": 5, "comments": 2, "shares": 1}
    body = json.loads(http.sent_to("video/query")[0]["body"])
    assert body == {"filters": {"video_ids": ["7412345"]}}


def test_fetch_metrics_missing_is_absent_not_zero(http):
    a = _adapter()
    _connect(a)
    http.routes["video/query"] = lambda m, u, h, b: _ok({
        "videos": [{"id": "9", "like_count": 3}]})       # partial report
    m = a.fetch_metrics("9")
    assert m == {"likes": 3}                             # missing ≠ 0 (§8.2)
    http.routes["video/query"] = lambda m2, u, h, b: _ok({"videos": []})
    assert a.fetch_metrics("unknown") is None


def test_fetch_metrics_none_when_disconnected(http):
    a = _adapter()
    assert a.fetch_metrics("7412345") is None
    assert a.fetch_account_metrics() is None
    assert http.calls == []                              # no blind API calls


def test_fetch_account_metrics(http):
    a = _adapter()
    _connect(a)
    http.routes["user/info"] = lambda m, u, h, b: _ok({
        "user": {"open_id": "oid-1", "follower_count": 42,
                 "likes_count": 7, "video_count": 3}})
    assert a.fetch_account_metrics() == {"followers": 42, "likes": 7, "posts": 3}


# ── §12.5: fixed content-free errors, envelopes never raise ──────────────────
def test_errors_are_fixed_and_content_free(tmp_path, http):
    a = _adapter()

    # disconnected paths
    assert a.publish({"video_path": "x"})["error"] == "not_connected"
    prep = a.prepare({"adapted_body": "hi", "format": "video_upload"}, {})
    assert prep["error"] == "not_connected"

    _connect(a)
    _creator_route(http)
    # missing/unreadable video asset → fixed string, no path leakage
    res = a.prepare({"adapted_body": "hi", "format": "video_upload",
                     "adapted_assets": [{"kind": "video",
                                         "out_path": str(tmp_path / "nope.mp4")}]},
                    {})
    assert res["error"] == "asset_unreadable"
    assert "nope.mp4" not in json.dumps(res)

    # API failure (unrouted endpoint → 404) → fixed string
    del http.routes["creator_info/query"]
    res = a.prepare(_video_target(tmp_path), {})
    assert res["error"] == "tiktok_api_error"

    # every error the adapter emitted so far is in the fixed vocabulary and
    # carries nothing tokenish
    for env in (a.publish({}), a.status()):
        blob = json.dumps(env)
        assert _FAKE_ACCESS not in blob and _FAKE_REFRESH not in blob
    assert a.status()["last_error"] in _FIXED_ERRORS | {None}


def test_publish_failed_and_timeout_envelopes(tmp_path, http):
    a = _adapter(status_poll_max=2)
    _connect(a)
    _creator_route(http)
    http.routes["publish/video/init"] = lambda m, u, h, b: _ok({
        "publish_id": "v_pub_9", "upload_url": "https://upload.example/z"})
    http.routes["upload.example"] = lambda m, u, h, b: {
        "status": 201, "json": None, "body": b"", "error": None}

    # platform-side FAILED → fixed error, fail_reason stays out of the envelope
    http.routes["status/fetch"] = lambda m, u, h, b: _ok(
        {"status": "FAILED", "fail_reason": "internal detail with /paths"})
    prep = a.prepare(_video_target(tmp_path), {"id": "p1"})
    res = a.publish(prep["prepared"])
    assert res["ok"] is False and res["error"] == "publish_failed"
    assert "internal detail" not in json.dumps(res)

    # stuck PROCESSING → bounded polling → ambiguous timeout for §7.2 probing
    http.routes["status/fetch"] = lambda m, u, h, b: _ok(
        {"status": "PROCESSING_UPLOAD"})
    res = a.publish(prep["prepared"])
    assert res["ok"] is False and res["error"] == "publish_status_timeout"
    assert res["raw"]["publish_id"] == "v_pub_9"


def test_prepare_and_publish_never_raise_on_garbage(http):
    a = _adapter()
    assert a.prepare(None, None)["ok"] is False
    assert a.publish(None)["ok"] is False
    assert isinstance(a.status(), dict)
