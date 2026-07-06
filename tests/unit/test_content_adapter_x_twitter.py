"""Unit tests for the X/Twitter platform adapter (spec §4.4).

All transport is stubbed at the module chokepoint (``x_twitter._http``) —
zero network. The credential store is faked to plaintext-on-tmpdir so auth
blobs round-trip without touching DPAPI/vault. Covers: the transport-stubbed
publish happy path, the OAuth2+PKCE auth blob round-trip, the platform's
sharp constraints (thread reply chains with per-segment resume + jitter,
URL-counts-as-23 length math, tier-as-configuration budgets with monthly
posts-remaining surfacing), the §8.2 metrics mapping, v2 chunked media
upload, and §12.5 fixed content-free error strings.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import platforms as preg                    # noqa: E402
from agent_friday.services.platforms import base as pbase              # noqa: E402
from agent_friday.services.platforms import x_twitter as xmod          # noqa: E402
from agent_friday.services.platforms.x_twitter import XTwitterAdapter  # noqa: E402


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
def _fake_store(monkeypatch):
    """Plaintext-on-tmpdir credential store — no DPAPI/vault in unit tests."""
    from agent_friday.services import credential_store as cs
    audits = []
    provider_keys = {}

    def _write(path, data):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(bytes(data))
        return "plaintext"

    monkeypatch.setattr(cs, "write_secret", _write)
    monkeypatch.setattr(cs, "read_secret", lambda p: Path(p).read_bytes())
    monkeypatch.setattr(cs, "set_provider_key",
                        lambda prov, val: provider_keys.__setitem__(prov, val) or "plaintext")
    monkeypatch.setattr(cs, "get_provider_key", provider_keys.get)
    monkeypatch.setattr(cs, "delete_provider_key",
                        lambda prov: provider_keys.pop(prov, None) is not None)
    monkeypatch.setattr(cs, "audit_event",
                        lambda category, event, **f: audits.append((category, event, f)))
    return {"audits": audits, "provider_keys": provider_keys}


class FakeHTTP:
    """Scriptable stand-in for x_twitter._http — records every call."""

    def __init__(self):
        self.calls = []
        self.routes = []            # (needle-or-callable, response-or-callable)

    def route(self, needle, response):
        self.routes.append((needle, response))

    def __call__(self, method, url, *, headers=None, body=None, timeout=20):
        hdrs = dict(headers or {})
        parsed = None
        if body and hdrs.get("Content-Type", "").startswith("application/json"):
            parsed = json.loads(body.decode("utf-8"))
        call = {"method": method, "url": url, "headers": hdrs,
                "body": body, "json": parsed}
        self.calls.append(call)
        for needle, resp in self.routes:
            hit = (needle(method, url, call) if callable(needle)
                   else needle in f"{method} {url}")
            if hit:
                out = resp(call) if callable(resp) else resp
                return dict(out)
        return {"status": 404, "json": None, "text": "", "headers": {}}

    def posts(self, needle):
        return [c for c in self.calls if needle in f"{c['method']} {c['url']}"]


def _ok(payload, status=200, headers=None):
    return {"status": status, "json": payload,
            "text": json.dumps(payload), "headers": headers or {}}


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(xmod, "_http", fake)
    return fake


@pytest.fixture
def adapter():
    a = XTwitterAdapter()
    a.configure({"client_id": "cid-123", "thread_jitter_s": 0})
    return a


def _connect(a, **extra):
    """Store a live-looking auth blob directly (fake credential store)."""
    blob = {"access_token": "tok-live-abc",        # pragma: allowlist secret
            "refresh_token": "rtok-1",             # pragma: allowlist secret
            "expires_at": "2099-01-01T00:00:00Z",
            "scopes": ["tweet.read", "tweet.write", "users.read", "offline.access"],
            "account": "friday_fan"}
    blob.update(extra)
    assert a.save_credentials(blob)["ok"]
    return blob


# ── publish happy path (transport stubbed) ────────────────────────────────────
def test_publish_single_post_happy_path(adapter, http):
    _connect(adapter)
    http.route("POST https://api.x.com/2/tweets", _ok({"data": {"id": "1867"}}, 201))
    prep = adapter.prepare({"adapted_body": "hello world", "format": "post",
                            "id": "tgt_1"}, {"id": "post_1"})
    assert prep["ok"], prep
    res = adapter.publish(prep["prepared"])
    assert res["ok"] is True
    assert res["platform_post_id"] == "1867"
    assert res["post_url"].endswith("/1867")
    assert res["raw"]["segment_ids"] == ["1867"]
    tweets = http.posts("POST https://api.x.com/2/tweets")
    assert len(tweets) == 1
    assert tweets[0]["json"]["text"] == "hello world"
    assert tweets[0]["headers"]["Authorization"] == "Bearer tok-live-abc"
    # budget consumed once, in both windows
    b = adapter.rate_budget()
    assert b["used"] == 1
    assert b["monthly"]["used"] == 1


def test_publish_without_credentials_never_touches_transport(adapter, http):
    res = adapter.publish({"body": "x"})
    assert res == {"ok": False, "error": "not_connected"}
    assert http.calls == []


# ── auth: OAuth2 + PKCE, state bound, blob round-trip ─────────────────────────
def test_connect_url_pkce_and_state(adapter):
    url = adapter.connect_url("state-xyz")
    assert url.startswith(xmod.AUTH_URL + "?")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["response_type"] == "code"
    assert q["client_id"] == "cid-123"
    assert q["redirect_uri"] == \
        "http://localhost:3000/api/content/platforms/x_twitter/callback"
    assert q["state"] == "state-xyz"
    assert q["code_challenge_method"] == "S256"
    assert set(q["scope"].split()) == \
        {"tweet.read", "tweet.write", "users.read", "offline.access"}
    # challenge is S256 of the stored verifier
    pend = adapter.load_credentials()["pending_auth"]
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(pend["verifier"].encode()).digest()).decode().rstrip("=")
    assert q["code_challenge"] == expected
    # a pending flow is NOT a connection
    assert adapter.status()["connected"] is False


def test_connect_url_without_client_id_is_none():
    a = XTwitterAdapter()
    a.configure({})
    assert a.connect_url("s") is None
    assert a.status()["last_error"] == "missing_client_config"
    assert a.status()["client_configured"] is False


def test_callback_round_trip_persists_blob_and_hides_tokens(adapter, http):
    adapter.connect_url("st-1")
    http.route("POST " + xmod.TOKEN_URL, _ok({
        "access_token": "fresh-tok",               # pragma: allowlist secret
        "refresh_token": "fresh-rtok",             # pragma: allowlist secret
        "expires_in": 7200,
        "scope": "tweet.read tweet.write users.read offline.access"}))
    http.route("GET https://api.x.com/2/users/me",
               _ok({"data": {"id": "9", "username": "friday_fan"}}))
    st = adapter.handle_callback({"state": "st-1", "code": "authcode-1"})
    assert st["connected"] is True
    assert st["account"] == "friday_fan"
    # nothing tokenish leaves through status()
    assert "fresh-tok" not in json.dumps(st)
    assert "fresh-rtok" not in json.dumps(st)
    # the blob round-trips through the credential store
    blob = adapter.load_credentials()
    assert blob["access_token"] == "fresh-tok"
    assert blob["refresh_token"] == "fresh-rtok"
    assert blob["scopes"] == ["tweet.read", "tweet.write", "users.read",
                              "offline.access"]
    assert "pending_auth" not in blob
    # the exchange carried the PKCE verifier and loopback redirect
    ex = http.posts("POST " + xmod.TOKEN_URL)[0]
    form = {k: v[0] for k, v in parse_qs(ex["body"].decode()).items()}
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "authcode-1"
    assert form["code_verifier"]
    assert form["redirect_uri"].endswith("/api/content/platforms/x_twitter/callback")


def test_callback_state_mismatch_rejected_and_single_use(adapter, http):
    adapter.connect_url("good-state")
    st = adapter.handle_callback({"state": "evil-state", "code": "c"})
    assert st["connected"] is False
    assert st["last_error"] == "oauth_state_mismatch"
    assert http.posts("POST " + xmod.TOKEN_URL) == []
    # pending is single-use — even the right state cannot be replayed now
    st2 = adapter.handle_callback({"state": "good-state", "code": "c"})
    assert st2["connected"] is False
    assert http.posts("POST " + xmod.TOKEN_URL) == []


def test_refresh_rotates_tokens(adapter, http):
    _connect(adapter, expires_at="2020-01-01T00:00:00Z")     # expired
    http.route("POST " + xmod.TOKEN_URL, _ok({
        "access_token": "rotated-tok",             # pragma: allowlist secret
        "refresh_token": "rotated-rtok",           # pragma: allowlist secret
        "expires_in": 7200}))
    assert adapter.refresh() is True
    blob = adapter.load_credentials()
    assert blob["access_token"] == "rotated-tok"
    assert blob["refresh_token"] == "rotated-rtok"           # X rotates
    assert blob["expires_at"] > "2020-01-01"


def test_revoke_calls_platform_and_purges(adapter, http):
    _connect(adapter)
    http.route("POST " + xmod.REVOKE_URL, _ok({"revoked": True}))
    assert adapter.revoke() is True
    assert adapter.load_credentials() is None
    assert adapter.status()["connected"] is False
    # the revoke call carried the token in the FORM, never in a log/envelope
    rev = http.posts("POST " + xmod.REVOKE_URL)
    assert len(rev) == 1


# ── sharp constraint: threads — reply chains, resume, jitter ─────────────────
def test_thread_resume_after_mid_thread_failure(adapter, http):
    _connect(adapter)
    ids = iter(["101", "102", "103"])
    state = {"count": 0, "fail_on": {3}}

    def tweets(call):
        state["count"] += 1
        if state["count"] in state["fail_on"]:
            return {"status": 500, "json": {"detail": "boom"},
                    "text": "boom", "headers": {}}
        return _ok({"data": {"id": next(ids)}}, 201)

    http.route("POST https://api.x.com/2/tweets", tweets)
    prepared = {"target_id": "tgt_th", "body": "",
                "segments": ["one", "two", "three"], "media_ids": []}

    r1 = adapter.publish(prepared)
    assert r1["ok"] is False
    assert r1["error"] == "platform_http_error"
    assert r1["resume_index"] == 2
    assert r1["segment_ids"] == ["101", "102"]
    tw = http.posts("POST https://api.x.com/2/tweets")
    assert "reply" not in tw[0]["json"]                       # thread root
    assert tw[1]["json"]["reply"]["in_reply_to_tweet_id"] == "101"
    assert adapter.rate_budget()["used"] == 2   # only confirmed segments count

    # retry resumes from segment 3 — never reposts a confirmed segment
    state["fail_on"] = set()
    r2 = adapter.publish(prepared)
    assert r2["ok"] is True
    tw = http.posts("POST https://api.x.com/2/tweets")
    assert len(tw) == 4                          # 3 attempts + 1 resumed segment
    assert tw[-1]["json"]["text"] == "three"
    assert tw[-1]["json"]["reply"]["in_reply_to_tweet_id"] == "102"
    assert r2["platform_post_id"] == "101"       # thread root id
    assert r2["raw"]["segment_ids"] == ["101", "102", "103"]
    assert r2["raw"]["resumed_from"] == 2
    assert adapter.rate_budget()["used"] == 3
    # progress cleared after full confirmation
    assert xmod._read_progress() == {}


def test_thread_jitter_between_segments(adapter, http, monkeypatch):
    _connect(adapter)
    adapter.configure({"client_id": "cid-123", "thread_jitter_s": [0.5, 0.5]})
    sleeps = []
    monkeypatch.setattr(xmod.time, "sleep", lambda s: sleeps.append(s))
    seq = iter(["1", "2", "3"])
    http.route("POST https://api.x.com/2/tweets",
               lambda c: _ok({"data": {"id": next(seq)}}, 201))
    res = adapter.publish({"target_id": "tgt_j",
                           "segments": ["a", "b", "c"], "body": ""})
    assert res["ok"] is True
    # a pause before segment 2 and 3, never before the root
    assert sleeps == [0.5, 0.5]


def test_thread_edited_segments_do_not_resume_stale_progress(adapter, http):
    _connect(adapter)
    seq = iter(["11", "12", "21", "22"])
    calls = {"n": 0}

    def tweets(call):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"status": 500, "json": {}, "text": "", "headers": {}}
        return _ok({"data": {"id": next(seq)}}, 201)

    http.route("POST https://api.x.com/2/tweets", tweets)
    assert adapter.publish({"target_id": "t", "segments": ["a", "b"]})["ok"] is False
    # user edits the thread → fingerprint changes → starts fresh, no resume
    res = adapter.publish({"target_id": "t", "segments": ["a EDITED", "b"]})
    assert res["ok"] is True
    tw = http.posts("POST https://api.x.com/2/tweets")
    assert tw[2]["json"]["text"] == "a EDITED"
    assert "reply" not in tw[2]["json"]


# ── sharp constraint: URLs count as 23 characters ─────────────────────────────
def test_urls_count_as_23_characters(adapter):
    long_url = "https://example.com/" + "p" * 120
    body = "x" * 250 + " " + long_url        # raw len 391, X-len 250+1+23 = 274
    prep = adapter.prepare({"adapted_body": body, "format": "post"}, {})
    assert prep["ok"] is True, prep
    body2 = "x" * 260 + " " + long_url       # X-len 284 → over the 280 limit
    prep2 = adapter.prepare({"adapted_body": body2, "format": "post"}, {})
    assert prep2["ok"] is False
    assert "char_limit" in prep2["error"]
    # the helper itself is exact
    assert xmod._x_len("abc") == 3
    assert xmod._x_len(long_url) == 23
    assert xmod._x_len(f"go {long_url} now") == 3 + 23 + 4


def test_over_limit_thread_segment_rejected(adapter):
    prep = adapter.prepare({"segments": ["fine", "y" * 300], "format": "thread"}, {})
    assert prep["ok"] is False
    assert "segment" in prep["error"]


# ── sharp constraint: paid-tier budget as configuration ──────────────────────
def test_tier_budgets_and_monthly_surfacing(adapter):
    adapter.configure({"tier": "free"})
    b = adapter.rate_budget()
    assert b["limit"] == 17
    assert b["monthly"]["window"] == "month"
    assert b["monthly"]["limit"] == 500
    st = adapter.status()
    assert st["plan"] == "free"
    assert st["monthly_posts_remaining"] == 500

    adapter.configure({"tier": "basic"})
    assert adapter.rate_budget()["limit"] == 100
    assert adapter.rate_budget()["monthly"]["limit"] == 3000
    assert adapter.capabilities()["analytics"] == "full"

    # the monthly cap gates even when the daily window has room
    adapter.configure({"tier": "basic", "daily_post_limit": 1000,
                       "monthly_post_limit": 2})
    adapter.consume_budget(2)
    assert adapter.budget_would_exceed(1) is True
    assert adapter.status()["monthly_posts_remaining"] == 0


def test_publish_defers_when_budget_exhausted_no_attempt_burned(adapter, http):
    _connect(adapter)
    adapter.configure({"client_id": "cid-123", "daily_post_limit": 1})
    adapter.consume_budget(1)
    res = adapter.publish({"body": "over budget"})
    assert res["ok"] is False
    assert res["error"] == "rate_budget_exhausted"
    assert res["defer_until"]                  # publisher defers to reset_at
    assert http.posts("POST https://api.x.com/2/tweets") == []


# ── analytics: §8.2 unified mapping, missing ≠ zero ──────────────────────────
def test_fetch_metrics_unified_mapping(adapter, http):
    _connect(adapter)
    http.route("GET https://api.x.com/2/tweets?ids=42", _ok({"data": [{
        "id": "42", "public_metrics": {
            "impression_count": 1000, "like_count": 50, "reply_count": 7,
            "retweet_count": 12, "quote_count": 3, "bookmark_count": 9}}]}))
    m = adapter.fetch_metrics("42")
    assert m == {"impressions": 1000, "likes": 50, "comments": 7,
                 "shares": 15, "saves": 9}


def test_fetch_metrics_missing_is_absent_not_zero(adapter, http):
    _connect(adapter)
    http.route("GET https://api.x.com/2/tweets?ids=43", _ok({"data": [{
        "id": "43", "public_metrics": {
            "like_count": 2, "reply_count": 1, "retweet_count": 4}}]}))
    m = adapter.fetch_metrics("43")
    assert m == {"likes": 2, "comments": 1, "shares": 4}
    assert "impressions" not in m and "saves" not in m


def test_fetch_metrics_without_credentials_is_none(adapter, http):
    assert adapter.fetch_metrics("42") is None
    assert http.calls == []


# ── media: v2 chunked upload with alt text ────────────────────────────────────
def test_prepare_uploads_media_v2_chunked_with_alt_text(adapter, http, tmp_path):
    _connect(adapter)
    img = tmp_path / "art.png"
    img.write_bytes(b"\x89PNG fake image bytes")

    def media(call):
        cmd = (call["json"] or {}).get("command")
        if cmd == "INIT":
            return _ok({"data": {"id": "m-77"}}, 202)
        if cmd == "APPEND":
            return _ok({}, 204)
        if cmd == "FINALIZE":
            return _ok({"data": {"id": "m-77"}})
        return {"status": 400, "json": None, "text": "", "headers": {}}

    http.route("POST " + xmod.MEDIA_UPLOAD_URL, media)
    http.route("POST " + xmod.MEDIA_METADATA_URL, _ok({}))
    http.route("POST https://api.x.com/2/tweets", _ok({"data": {"id": "555"}}, 201))

    prep = adapter.prepare(
        {"adapted_body": "look what I made", "format": "image_post",
         "id": "tgt_m",
         "adapted_assets": [{"kind": "image", "out_path": str(img),
                             "alt_text": "a cat"}]},
        {"id": "post_m"})
    assert prep["ok"], prep
    assert prep["prepared"]["media_ids"] == ["m-77"]
    cmds = [c["json"]["command"] for c in http.posts("POST " + xmod.MEDIA_UPLOAD_URL)]
    assert cmds == ["INIT", "APPEND", "FINALIZE"]
    meta = http.posts("POST " + xmod.MEDIA_METADATA_URL)
    assert meta and meta[0]["json"]["metadata"]["alt_text"]["text"] == "a cat"

    # publish attaches the uploaded media to the root tweet
    res = adapter.publish(prep["prepared"])
    assert res["ok"] is True
    tw = http.posts("POST https://api.x.com/2/tweets")
    assert tw[0]["json"]["media"] == {"media_ids": ["m-77"]}


def test_prepare_media_validation_no_transport(adapter, http):
    # 4 images, no local files → warnings only, zero transport
    assets = [{"kind": "image"} for _ in range(4)]
    prep = adapter.prepare({"adapted_body": "b", "adapted_assets": assets}, {})
    assert prep["ok"] is True
    assert any("alt text" in w for w in prep["warnings"])
    assert http.calls == []
    # 5 images → rejected
    prep5 = adapter.prepare(
        {"adapted_body": "b", "adapted_assets": assets + [{"kind": "image"}]}, {})
    assert prep5["ok"] is False
    assert "too many images" in prep5["error"]
    # mixed image + video → rejected (X media must be homogeneous)
    mixed = adapter.prepare(
        {"adapted_body": "b",
         "adapted_assets": [{"kind": "image"}, {"kind": "video"}]}, {})
    assert mixed["ok"] is False


# ── §12.5: adapter errors are fixed, content-free strings ─────────────────────
def test_adapter_errors_are_fixed_content_free_strings(adapter, http):
    _connect(adapter)
    juicy = "PII C:\\Users\\somebody secret-path leaked-body"
    http.route("POST https://api.x.com/2/tweets",
               {"status": 403, "json": {"detail": juicy},
                "text": juicy, "headers": {}})
    res = adapter.publish({"body": "hi"})
    assert res["ok"] is False and res["error"] == "forbidden"
    assert juicy not in json.dumps(res)
    assert adapter.status()["last_error"] == "forbidden"

    http.routes.clear()
    http.route("POST https://api.x.com/2/tweets",
               {"status": 429, "json": {}, "text": "",
                "headers": {"retry-after": "30"}})
    res = adapter.publish({"body": "hi"})
    assert res["error"] == "rate_limited"
    assert res["retry_after"] == 30            # §7.5 — publisher honors it

    http.routes.clear()
    http.route("POST https://api.x.com/2/tweets",
               {"status": 401, "json": {}, "text": "", "headers": {}})
    res = adapter.publish({"body": "hi"})
    assert res["error"] == "auth_error"


# ── capabilities honesty ──────────────────────────────────────────────────────
def test_capabilities_defaults_and_premium(adapter):
    caps = adapter.capabilities()
    assert caps["char_limit"] == 280
    assert caps["thread"] is True
    assert caps["native_schedule"] is False
    assert caps["native_delete"] is True
    assert caps["analytics"] == "counts"        # free tier reads are minimal
    assert caps["media"]["images"]["max"] == 4
    assert caps["media"]["video"]["max_s"] == 140
    assert caps["media"]["alt_text"] is True
    adapter.configure({"premium": True, "tier": "pro"})
    caps = adapter.capabilities()
    assert caps["char_limit"] == 25000
    assert caps["analytics"] == "full"


def test_delete_tweet(adapter, http):
    _connect(adapter)
    http.route("DELETE https://api.x.com/2/tweets/9",
               _ok({"data": {"deleted": True}}))
    assert adapter.delete("9") == {"ok": True, "deleted": True}


# ── registry pickup ───────────────────────────────────────────────────────────
def test_registry_resolves_x_twitter_and_aliases():
    for alias in ("x_twitter", "twitter", "x", "x-twitter"):
        a = preg.get_adapter(alias)
        assert isinstance(a, XTwitterAdapter), alias
