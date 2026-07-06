"""Unit tests for the Reddit platform adapter (spec §4.9).

All transport is stubbed at the module chokepoint (``reddit._http``) — zero
network. The credential store is faked to plaintext-on-tmpdir so auth blobs
round-trip without touching DPAPI/vault. Covers: the script-app OAuth code
flow (HTTP Basic client pair + mandatory User-Agent on every call), the
/api/submit self/link flows and the media-asset lease flow (S3 POST-policy
upload), get_subreddit_requirements() surfacing sub rules as compose-time
warnings, flair/nsfw/spoiler mapping from options and tags, the conservative
≤10/day local budget, the §8.2 counts mapping, and §12.5 fixed content-free
error strings.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import platforms as preg                # noqa: E402
from agent_friday.services.platforms import base as pbase          # noqa: E402
from agent_friday.services.platforms import reddit as rmod         # noqa: E402
from agent_friday.services.platforms.reddit import RedditAdapter   # noqa: E402


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
    """Scriptable stand-in for reddit._http — records every call."""

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


def _form(call):
    """Decode an application/x-www-form-urlencoded body → {k: v}."""
    q = parse_qs((call["body"] or b"").decode("utf-8"), keep_blank_values=True)
    return {k: v[0] for k, v in q.items()}


def _submit_ok(sr="test", id36="abc12", title="t"):
    return _ok({"json": {"errors": [], "data": {
        "id": id36, "name": f"t3_{id36}",
        "url": f"https://www.reddit.com/r/{sr}/comments/{id36}/x/"}}})


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(rmod, "_http", fake)
    return fake


@pytest.fixture
def adapter():
    a = RedditAdapter()
    a.configure({"client_id": "cid-app"})
    return a


def _connect(a, **extra):
    """Store a live-looking OAuth blob directly (fake credential store)."""
    blob = {"access_token": "reddit-access-1",     # pragma: allowlist secret
            "refresh_token": "reddit-refresh-1",   # pragma: allowlist secret
            "expires_at": "2099-01-01T00:00:00Z",
            "scopes": ["identity", "submit", "flair", "read"],
            "account": "fridaybot",
            "connected_at": "2026-01-01T00:00:00Z"}
    blob.update(extra)
    assert a.save_credentials(blob)["ok"]
    return blob


# ── capabilities (§4.9) ──────────────────────────────────────────────────────
def test_capabilities_honest(adapter):
    caps = adapter.capabilities()
    assert caps["title_limit"] == 300
    assert caps["char_limit"] == 40000
    assert caps["thread"] is False                 # no threads on Reddit
    assert caps["native_schedule"] is False
    assert caps["native_delete"] is True
    assert caps["analytics"] == "counts"
    assert caps["hashtags_max"] == 0               # not a Reddit norm
    assert caps["media"]["alt_text"] is False      # captions, not alt text
    assert any("one target per subreddit" in n for n in caps["notes"])
    assert adapter.default_daily_limit == 10       # §4.9 conservative budget
    assert adapter.rate_budget()["limit"] == 10


# ── auth: script-app OAuth code flow ─────────────────────────────────────────
def test_connect_url_binds_state_and_asks_permanent(adapter, http):
    url = adapter.connect_url("state-7")
    assert url and url.startswith(rmod.AUTH_URL + "?")
    assert "state=state-7" in url
    assert "duration=permanent" in url             # refresh token, please
    assert "client_id=cid-app" in url
    blob = adapter.load_credentials()
    assert blob["pending_auth"]["state"] == "state-7"
    assert http.calls == []                        # no transport to build a URL


def test_connect_url_requires_client_id(http):
    a = RedditAdapter()
    a.configure({})
    assert a.connect_url("s") is None
    assert a._last_error == "missing_client_config"
    assert http.calls == []


def test_handle_callback_round_trip_and_replay_rejected(adapter, http):
    adapter.set_simple_secret("app-secret-1")      # pragma: allowlist secret
    http.route(f"POST {rmod.TOKEN_URL}",
               _ok({"access_token": "granted-tok",    # pragma: allowlist secret
                    "refresh_token": "granted-rot",   # pragma: allowlist secret
                    "expires_in": 3600,
                    "scope": "identity submit flair read"}))
    http.route(f"GET {rmod.OAUTH_API}/api/v1/me",
               _ok({"name": "fridaybot", "total_karma": 5}))
    adapter.connect_url("state-7")
    st = adapter.handle_callback({"state": "state-7", "code": "code-9"})
    assert st["connected"] is True
    assert st["account"] == "fridaybot"
    assert st["expires_at"]
    # nothing tokenish leaves through status()
    assert "granted-tok" not in json.dumps(st)
    assert "granted-rot" not in json.dumps(st)
    blob = adapter.load_credentials()
    assert blob["access_token"] == "granted-tok"
    assert blob["refresh_token"] == "granted-rot"
    assert "pending_auth" not in blob
    # the exchange used HTTP Basic (client id : secret) + a descriptive UA
    tok = http.posts(rmod.TOKEN_URL)[0]
    import base64 as b64
    expect = b64.b64encode(b"cid-app:app-secret-1").decode("ascii")
    assert tok["headers"]["Authorization"] == f"Basic {expect}"
    assert tok["headers"]["User-Agent"] == rmod.DEFAULT_USER_AGENT
    form = _form(tok)
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "code-9"
    assert form["redirect_uri"] == rmod.DEFAULT_REDIRECT_URI
    # replay of the same callback is rejected (state already spent)
    st2 = adapter.handle_callback({"state": "state-7", "code": "code-9"})
    assert st2["last_error"] == "oauth_state_mismatch"
    assert len(http.posts(rmod.TOKEN_URL)) == 1


def test_handle_callback_state_mismatch_no_exchange(adapter, http):
    adapter.connect_url("state-7")
    st = adapter.handle_callback({"state": "EVIL", "code": "c"})
    assert st["connected"] is False
    assert st["last_error"] == "oauth_state_mismatch"
    assert http.posts(rmod.TOKEN_URL) == []


def test_client_secret_alone_is_not_connected(adapter):
    adapter.set_simple_secret("app-secret-1")      # pragma: allowlist secret
    st = adapter.status()
    assert st["connected"] is False
    assert st["client_configured"] is True


def test_refresh_fresh_token_no_transport(adapter, http):
    _connect(adapter)
    assert adapter.refresh() is True
    assert http.calls == []


def test_refresh_expiring_rotates_and_keeps_refresh_token(adapter, http):
    soon = (pbase._now_utc() + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _connect(adapter, expires_at=soon)
    http.route(f"POST {rmod.TOKEN_URL}",
               _ok({"access_token": "fresh-tok", "expires_in": 3600}))  # pragma: allowlist secret
    assert adapter.refresh() is True
    form = _form(http.posts(rmod.TOKEN_URL)[0])
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "reddit-refresh-1"
    blob = adapter.load_credentials()
    assert blob["access_token"] == "fresh-tok"
    assert blob["refresh_token"] == "reddit-refresh-1"   # kept when not rotated
    exp = pbase._parse_iso(blob["expires_at"])
    assert (exp - pbase._now_utc()).total_seconds() > 3000


def test_refresh_failure_returns_false(adapter, http):
    soon = (pbase._now_utc() + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _connect(adapter, expires_at=soon)
    http.route(f"POST {rmod.TOKEN_URL}",
               {"status": 401, "json": None, "text": "nope", "headers": {}})
    assert adapter.refresh() is False
    assert adapter.refresh() in (True, False)      # idempotent, never raises


def test_refresh_disconnected_is_false(adapter, http):
    assert adapter.refresh() is False
    assert http.calls == []


def test_revoke_hits_revoke_endpoint_and_purges(adapter, http, _fake_store):
    _connect(adapter)
    http.route(f"POST {rmod.REVOKE_URL}", _ok({}, status=204))
    assert adapter.revoke() is True
    form = _form(http.posts(rmod.REVOKE_URL)[0])
    # revoking the refresh grant kills the whole token family
    assert form["token"] == "reddit-refresh-1"
    assert form["token_type_hint"] == "refresh_token"
    assert adapter.load_credentials() is None
    assert adapter.status()["connected"] is False
    assert adapter.revoke() is True                # idempotent


# ── get_subreddit_requirements (§4.9 — compose-time warnings) ────────────────
def _route_sub(http, sr="test", about=None, rules=None, reqs=None):
    d = {"subreddit_type": "public", "submission_type": "any",
         "over18": False, "allow_images": True, "allow_videos": True,
         "subscribers": 1000}
    d.update(about or {})
    http.route(f"GET {rmod.OAUTH_API}/r/{sr}/about/rules",
               _ok({"rules": rules if rules is not None else []}))
    http.route(f"GET {rmod.OAUTH_API}/api/v1/{sr}/post_requirements",
               _ok(reqs) if reqs is not None
               else {"status": 404, "json": None, "text": "", "headers": {}})
    http.route(f"GET {rmod.OAUTH_API}/r/{sr}/about", _ok({"kind": "t5", "data": d}))


def test_subreddit_requirements_surface_rules_as_warnings(adapter, http):
    _connect(adapter)
    _route_sub(http, "test",
               about={"submission_type": "link", "over18": True,
                      "allow_images": False},
               rules=[{"short_name": "Be kind", "kind": "all",
                       "description": "No harassment"},
                      {"short_name": "No self-promo", "kind": "link",
                       "description": "90/10 rule applies"}],
               reqs={"is_flair_required": True, "title_text_max_length": 100,
                     "title_required_strings": ["[OC]"],
                     "body_restriction_policy": "notAllowed"})
    out = adapter.get_subreddit_requirements("r/test")
    assert out["ok"], out
    assert out["subreddit"] == "test"
    w = "\n".join(out["warnings"])
    assert "link posts only" in w
    assert "18+" in w
    assert "images are not allowed" in w
    assert "flair is required" in w
    assert "capped at 100 chars" in w
    assert "specific text in titles" in w
    assert "bodies are not allowed" in w
    assert "2 community rule(s)" in w
    assert out["rules"][0]["short_name"] == "Be kind"
    assert out["post_requirements"]["is_flair_required"] is True
    # every call carried the mandatory User-Agent
    for c in http.calls:
        assert c["headers"]["User-Agent"] == rmod.DEFAULT_USER_AGENT


def test_subreddit_requirements_cached(adapter, http):
    _connect(adapter)
    _route_sub(http, "test")
    first = adapter.get_subreddit_requirements("test")
    n = len(http.calls)
    second = adapter.get_subreddit_requirements("TEST")   # case-insensitive hit
    assert len(http.calls) == n                    # served from cache
    assert second["ok"] and second["about"] == first["about"]


def test_subreddit_requirements_invalid_name_no_transport(adapter, http):
    _connect(adapter)
    out = adapter.get_subreddit_requirements("not a subreddit!!")
    assert out == {"ok": False, "error": "invalid_subreddit"}
    assert http.calls == []


def test_subreddit_requirements_not_connected(adapter, http):
    out = adapter.get_subreddit_requirements("test")
    assert out == {"ok": False, "error": "not_connected"}


# ── prepare: one target per subreddit, flair/nsfw/spoiler mapping ────────────
def test_prepare_requires_subreddit(adapter):
    res = adapter.prepare({"adapted_body": "x", "format": "post"}, {})
    assert res["ok"] is False
    assert res["error"] == "missing_subreddit"


def test_prepare_title_from_first_line_with_warning(adapter):
    res = adapter.prepare(
        {"adapted_body": "A great discovery\n\nDetails follow.",
         "format": "post", "options": {"subreddit": "test"}}, {})
    assert res["ok"], res
    assert res["prepared"]["title"] == "A great discovery"
    assert any("derived" in w for w in res["warnings"])


def test_prepare_no_title_at_all_rejected(adapter):
    res = adapter.prepare({"adapted_body": "", "format": "post",
                           "options": {"subreddit": "test"}}, {})
    assert res["ok"] is False
    assert res["error"] == "missing_title"


def test_prepare_title_over_300_rejected(adapter):
    res = adapter.prepare({"adapted_body": "b", "format": "post",
                           "options": {"subreddit": "test"}},
                          {"title": "T" * 301})
    assert res["ok"] is False
    assert "title exceeds" in res["error"]


def test_prepare_body_over_40k_rejected(adapter):
    res = adapter.prepare({"adapted_body": "x" * 40001, "format": "post",
                           "adapted_title": "t",
                           "options": {"subreddit": "test"}}, {})
    assert res["ok"] is False
    assert "char_limit" in res["error"]


def test_prepare_maps_flair_nsfw_spoiler_from_options_and_tags(adapter):
    res = adapter.prepare(
        {"adapted_body": "b", "adapted_title": "t", "format": "post",
         "options": {"subreddit": "test", "flair_id": "f-123",
                     "flair_text": "Discussion", "sendreplies": False}},
        {"tags": ["NSFW", "Spoiler"]})
    assert res["ok"], res
    p = res["prepared"]
    assert p["subreddit"] == "test"
    assert p["nsfw"] is True                       # from post tag
    assert p["spoiler"] is True                    # from post tag
    assert p["flair_id"] == "f-123"
    assert p["flair_text"] == "Discussion"
    assert p["sendreplies"] is False


def test_prepare_hashtags_dropped_with_warning(adapter):
    res = adapter.prepare(
        {"adapted_body": "b", "adapted_title": "t", "format": "post",
         "hashtags": ["ai", "agents"], "options": {"subreddit": "test"}}, {})
    assert res["ok"]
    assert res["prepared"]["hashtags"] == []
    assert any("hashtags dropped" in w for w in res["warnings"])


def test_prepare_segments_merged_reddit_has_no_threads(adapter):
    res = adapter.prepare(
        {"adapted_body": "", "adapted_title": "t", "format": "post",
         "segments": ["part one", "part two"],
         "options": {"subreddit": "test"}}, {})
    assert res["ok"]
    assert res["prepared"]["body"] == "part one\n\npart two"
    assert res["prepared"]["segments"] == []
    assert any("no threads" in w for w in res["warnings"])


def test_prepare_merges_subreddit_rule_warnings_when_connected(adapter, http):
    _connect(adapter)
    _route_sub(http, "test",
               reqs={"is_flair_required": True, "title_text_max_length": None,
                     "title_required_strings": [],
                     "body_restriction_policy": "none"})
    res = adapter.prepare({"adapted_body": "b", "adapted_title": "t",
                           "format": "post",
                           "options": {"subreddit": "test"}}, {})
    assert res["ok"], res
    assert any("flair is required" in w for w in res["warnings"])


def test_prepare_never_raises_on_garbage(adapter):
    res = adapter.prepare(None, None)
    assert isinstance(res, dict) and "ok" in res


# ── publish: self / link flows (mandatory User-Agent everywhere) ─────────────
def test_publish_self_post_form_and_budget(adapter, http):
    _connect(adapter)
    http.route(f"POST {rmod.SUBMIT_URL}", _submit_ok("test", "abc12"))
    prep = adapter.prepare(
        {"adapted_body": "Markdown **body**", "adapted_title": "My title",
         "format": "post", "id": "tgt_1",
         "options": {"subreddit": "test", "flair_id": "f-9"}},
        {"id": "post_1", "tags": ["nsfw"]})
    assert prep["ok"], prep
    res = adapter.publish(prep["prepared"])
    assert res["ok"] is True
    assert res["platform_post_id"] == "t3_abc12"
    assert res["post_url"] == "https://www.reddit.com/r/test/comments/abc12/x/"
    call = http.posts(rmod.SUBMIT_URL)[0]
    assert call["headers"]["Authorization"] == "Bearer reddit-access-1"
    assert call["headers"]["User-Agent"] == rmod.DEFAULT_USER_AGENT
    form = _form(call)
    assert form["kind"] == "self"
    assert form["sr"] == "test"
    assert form["title"] == "My title"
    assert form["text"] == "Markdown **body**"
    assert form["api_type"] == "json"
    assert form["nsfw"] == "true"
    assert form["spoiler"] == "false"
    assert form["flair_id"] == "f-9"
    assert adapter.rate_budget()["used"] == 1      # consumed on success only


def test_publish_link_post(adapter, http):
    _connect(adapter)
    http.route(f"POST {rmod.SUBMIT_URL}", _submit_ok("test", "lnk01"))
    res = adapter.publish({"subreddit": "test", "title": "Look at this",
                           "format": "link_post",
                           "link_url": "https://example.com/article",
                           "options": {}})
    assert res["ok"], res
    form = _form(http.posts(rmod.SUBMIT_URL)[0])
    assert form["kind"] == "link"
    assert form["url"] == "https://example.com/article"


def test_publish_link_post_without_url_fails(adapter, http):
    _connect(adapter)
    res = adapter.publish({"subreddit": "test", "title": "t",
                           "format": "link_post", "options": {}})
    assert res["ok"] is False
    assert res["error"] == "missing_link_url"
    assert http.posts(rmod.SUBMIT_URL) == []


def test_publish_empty_self_body_fails(adapter, http):
    _connect(adapter)
    res = adapter.publish({"subreddit": "test", "title": "t", "body": "  ",
                           "options": {}})
    assert res["ok"] is False
    assert res["error"] == "empty_post"


def test_publish_without_credentials_never_touches_transport(adapter, http):
    res = adapter.publish({"subreddit": "test", "title": "t", "body": "b"})
    assert res == {"ok": False, "error": "not_connected"}
    assert http.calls == []


def test_publish_budget_10_per_day_defers(adapter, http):
    _connect(adapter)
    adapter.consume_budget(10)                     # §4.9 conservative cap
    res = adapter.publish({"subreddit": "test", "title": "t", "body": "b",
                           "options": {}})
    assert res["ok"] is False
    assert res["error"] == "rate_budget_exhausted"
    assert res.get("defer_until")
    assert http.calls == []


# ── media-asset lease flow (§4.9) ────────────────────────────────────────────
def _media_routes(http, asset_id="as_1", key="rte_images/k1"):
    http.route(f"POST {rmod.MEDIA_ASSET_URL}",
               _ok({"args": {"action": "//reddit-uploads.example.com/",
                             "fields": [{"name": "key", "value": key},
                                        {"name": "policy", "value": "p0licy"}]},
                    "asset": {"asset_id": asset_id}}))
    http.route("POST https://reddit-uploads.example.com/",
               {"status": 201, "json": None, "text": "",
                "headers": {"location":
                            f"https://reddit-uploads.example.com/{key}"}})


def test_prepare_image_rides_lease_flow(adapter, http, tmp_path):
    _connect(adapter)
    _media_routes(http)
    img = tmp_path / "cat.png"
    img.write_bytes(b"\x89PNG" + b"\x00" * 32)
    res = adapter.prepare(
        {"adapted_body": "b", "adapted_title": "t", "format": "image_post",
         "adapted_assets": [{"kind": "image", "path": str(img),
                             "alt_text": "a cat"}],
         "options": {"subreddit": "pics"}}, {})
    assert res["ok"], res
    media = res["prepared"]["media"]
    assert media[0]["asset_id"] == "as_1"
    assert media[0]["asset_url"] == "https://reddit-uploads.example.com/rte_images/k1"
    assert media[0]["caption"] == "a cat"
    # lease request declared the file
    lease = _form(http.posts(rmod.MEDIA_ASSET_URL)[0])
    assert lease["filepath"] == "cat.png"
    assert lease["mimetype"] == "image/png"
    # S3 upload: every lease field present, file part last, UA mandatory
    s3 = http.posts("reddit-uploads.example.com")[0]
    assert s3["headers"]["User-Agent"] == rmod.DEFAULT_USER_AGENT
    body = s3["body"]
    assert b'name="key"' in body and b"rte_images/k1" in body
    assert b'name="policy"' in body
    assert body.index(b'name="policy"') < body.index(b'name="file"')
    assert b"\x89PNG" in body


def test_publish_single_image_submits_kind_image(adapter, http, tmp_path):
    _connect(adapter)
    _media_routes(http)
    http.route(f"POST {rmod.SUBMIT_URL}", _submit_ok("pics", "img01"))
    img = tmp_path / "cat.png"
    img.write_bytes(b"\x89PNG" + b"\x00" * 32)
    prep = adapter.prepare(
        {"adapted_body": "", "adapted_title": "A cat", "format": "image_post",
         "adapted_assets": [{"kind": "image", "path": str(img)}],
         "options": {"subreddit": "pics"}}, {})
    assert prep["ok"], prep
    res = adapter.publish(prep["prepared"])
    assert res["ok"], res
    form = _form(http.posts(rmod.SUBMIT_URL)[0])
    assert form["kind"] == "image"
    assert form["url"] == "https://reddit-uploads.example.com/rte_images/k1"


def test_publish_gallery_for_multi_image(adapter, http):
    _connect(adapter)
    http.route(f"POST {rmod.GALLERY_URL}", _submit_ok("pics", "gal01"))
    res = adapter.publish({
        "subreddit": "pics", "title": "Two cats", "body": "",
        "media": [{"kind": "image", "asset_id": "as_1",
                   "asset_url": "https://u/1", "caption": "cat one"},
                  {"kind": "image", "asset_id": "as_2",
                   "asset_url": "https://u/2", "caption": "cat two"}],
        "nsfw": False, "spoiler": False, "options": {}})
    assert res["ok"], res
    call = http.posts(rmod.GALLERY_URL)[0]
    payload = call["json"]
    assert payload["sr"] == "pics"
    assert [i["media_id"] for i in payload["items"]] == ["as_1", "as_2"]
    assert payload["items"][0]["caption"] == "cat one"
    assert payload["nsfw"] is False                # booleans on the JSON endpoint
    # gallery endpoint only — plain /api/submit was never touched
    assert [c for c in http.calls if c["url"] == rmod.SUBMIT_URL] == []


def test_publish_video_with_poster(adapter, http):
    _connect(adapter)
    http.route(f"POST {rmod.SUBMIT_URL}", _submit_ok("videos", "vid01"))
    res = adapter.publish({
        "subreddit": "videos", "title": "Clip", "body": "",
        "media": [{"kind": "video", "asset_id": "as_v",
                   "asset_url": "https://u/v.mp4", "caption": ""}],
        "options": {"poster_url": "https://u/poster.png"}})
    assert res["ok"], res
    form = _form(http.posts(rmod.SUBMIT_URL)[0])
    assert form["kind"] == "video"
    assert form["url"] == "https://u/v.mp4"
    assert form["video_poster_url"] == "https://u/poster.png"


def test_prepare_mixed_image_and_video_rejected(adapter):
    res = adapter.prepare(
        {"adapted_body": "b", "adapted_title": "t", "format": "post",
         "adapted_assets": [{"kind": "image"}, {"kind": "video"}],
         "options": {"subreddit": "test"}}, {})
    assert res["ok"] is False
    assert "mixed" in res["error"]


# ── in-body submit errors + async websocket confirmation (§7.2) ──────────────
def test_submit_in_body_ratelimit_maps_to_fixed_string(adapter, http):
    _connect(adapter)
    http.route(f"POST {rmod.SUBMIT_URL}",
               _ok({"json": {"errors": [["RATELIMIT",
                                         "you are doing that too much; try "
                                         "again in 9 minutes", "ratelimit"]]}}))
    res = adapter.publish({"subreddit": "test", "title": "t", "body": "b",
                           "options": {}})
    assert res["ok"] is False
    assert res["error"] == "rate_limited"
    assert "RATELIMIT" in res["codes"]             # enum code travels
    assert "9 minutes" not in json.dumps(res)      # message text never does
    assert adapter.rate_budget()["used"] == 0      # nothing landed


def test_submit_in_body_rejection_maps_to_submit_rejected(adapter, http):
    _connect(adapter)
    http.route(f"POST {rmod.SUBMIT_URL}",
               _ok({"json": {"errors": [["SUBREDDIT_NOTALLOWED",
                                         "you aren't allowed to post there.",
                                         "sr"]]}}))
    res = adapter.publish({"subreddit": "test", "title": "t", "body": "b",
                           "options": {}})
    assert res["ok"] is False
    assert res["error"] == "submit_rejected"
    assert res["codes"] == ["SUBREDDIT_NOTALLOWED"]
    assert "allowed to post" not in json.dumps(res)


def test_websocket_only_response_resolved_by_probe(adapter, http):
    _connect(adapter)
    http.route(f"POST {rmod.SUBMIT_URL}",
               _ok({"json": {"errors": [], "data": {
                   "websocket_url": "wss://ws.reddit.example/x"}}}))
    http.route(f"GET {rmod.OAUTH_API}/user/fridaybot/submitted",
               _ok({"data": {"children": [
                   {"data": {"subreddit": "test", "title": "Async post",
                             "name": "t3_ws001",
                             "permalink": "/r/test/comments/ws001/async_post/"}}]}}))
    res = adapter.publish({"subreddit": "test", "title": "Async post",
                           "body": "b", "options": {}})
    assert res["ok"] is True
    assert res["platform_post_id"] == "t3_ws001"
    assert res["post_url"] == "https://www.reddit.com/r/test/comments/ws001/async_post/"
    assert res["raw"]["probed"] is True
    assert adapter.rate_budget()["used"] == 1      # it DID land


def test_websocket_only_probe_miss_is_honest_unconfirmed(adapter, http):
    _connect(adapter)
    http.route(f"POST {rmod.SUBMIT_URL}",
               _ok({"json": {"errors": [], "data": {
                   "websocket_url": "wss://ws.reddit.example/x"}}}))
    http.route(f"GET {rmod.OAUTH_API}/user/fridaybot/submitted",
               _ok({"data": {"children": []}}))
    res = adapter.publish({"subreddit": "test", "title": "Async post",
                           "body": "b", "options": {}})
    assert res["ok"] is False
    assert res["error"] == "submit_unconfirmed"
    assert res["unconfirmed"] is True              # §7.2 verify-probe owns retry
    assert adapter.rate_budget()["used"] == 1      # budget honestly spent


# ── delete / analytics (§8.2 unified keys, missing ≠ zero) ───────────────────
def test_delete_prefixes_t3_and_posts_api_del(adapter, http):
    _connect(adapter)
    http.route(f"POST {rmod.OAUTH_API}/api/del", _ok({}))
    res = adapter.delete("abc12")
    assert res == {"ok": True, "deleted": True}
    assert _form(http.posts("/api/del")[0])["id"] == "t3_abc12"


def test_fetch_metrics_unified_mapping(adapter, http):
    _connect(adapter)
    http.route(f"GET {rmod.OAUTH_API}/api/info?id=t3_abc12",
               _ok({"data": {"children": [{"data": {
                   "score": 41, "num_comments": 7, "num_crossposts": 2,
                   "upvote_ratio": 0.93}}]}}))
    m = adapter.fetch_metrics("abc12")
    assert m == {"likes": 41, "comments": 7, "shares": 2}
    assert "impressions" not in m                  # Reddit has none — absent


def test_fetch_metrics_missing_counts_absent_not_zero(adapter, http):
    _connect(adapter)
    http.route(f"GET {rmod.OAUTH_API}/api/info?id=t3_x",
               _ok({"data": {"children": [{"data": {"score": 3}}]}}))
    m = adapter.fetch_metrics("t3_x")
    assert m == {"likes": 3}
    assert "comments" not in m and "shares" not in m


def test_fetch_metrics_unknown_or_disconnected_is_none(adapter, http):
    assert adapter.fetch_metrics("t3_abc") is None   # not connected
    _connect(adapter)
    http.route("/api/info", _ok({"data": {"children": []}}))
    assert adapter.fetch_metrics("t3_abc") is None


def test_fetch_account_metrics(adapter, http):
    _connect(adapter)
    http.route(f"GET {rmod.OAUTH_API}/api/v1/me",
               _ok({"name": "fridaybot", "total_karma": 812,
                    "subreddit": {"subscribers": 44}}))
    assert adapter.fetch_account_metrics() == {"karma": 812, "followers": 44}


# ── §12.5 fixed content-free error strings ───────────────────────────────────
def test_errors_are_fixed_content_free_strings(adapter, http):
    _connect(adapter)
    http.route(rmod.SUBMIT_URL,
               {"status": 403, "json": {"message": "SECRET-DETAIL"},
                "text": "SECRET-DETAIL", "headers": {}})
    res = adapter.publish({"subreddit": "test", "title": "t", "body": "b",
                           "options": {}})
    assert res["error"] == "forbidden"
    assert "SECRET-DETAIL" not in json.dumps(res)

    http.routes.clear()
    http.route(rmod.SUBMIT_URL,
               {"status": 429, "json": None, "text": "chill out",
                "headers": {"retry-after": "60"}})
    res = adapter.publish({"subreddit": "test", "title": "t", "body": "b",
                           "options": {}})
    assert res["error"] == "rate_limited"
    assert res["retry_after"] == 60                # §7.5: publisher honors it
    assert "chill out" not in json.dumps(res)

    http.routes.clear()                            # dead network → network_error
    http.route(rmod.SUBMIT_URL,
               {"status": 0, "json": None, "text": "", "headers": {},
                "error": "network_error"})
    res = adapter.publish({"subreddit": "test", "title": "t", "body": "b",
                           "options": {}})
    assert res["error"] == "network_error"


def test_registry_resolves_reddit():
    a = preg.get_adapter("reddit")
    assert isinstance(a, RedditAdapter)
    assert a.name == "reddit"
