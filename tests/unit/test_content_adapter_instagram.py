"""Instagram adapter unit tests (spec §4.5 / §15) — transport fully stubbed.

Covers: capability honesty (JPEG-only 4:5…1.91:1, no delete, full insights),
OAuth code flow with bound single-use state, auth blob round-trip through a
fake credential store, long-lived refresh + expiry surfacing, the container
publish flow (single image, carousel ≤10, reel via resumable upload), the
§7.4 staging strategy (hold with the clear message when unconfigured — never
silent), the 100/24 h budget with content_publishing_limit sync, metric
normalization to the §8.2 unified keys, and fixed content-free error strings
(§12.5). Zero live network: every byte rides the module-level
``instagram._request`` seam, which is replaced wholesale here.
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

from agent_friday.services import credential_store as cs           # noqa: E402
from agent_friday.services.platforms import base as pbase          # noqa: E402
from agent_friday.services.platforms import instagram as ig        # noqa: E402
from agent_friday.services.platforms.instagram import (            # noqa: E402
    InstagramAdapter, _STAGING_HOLD_MSG)

_FAKE_SHORT = "ig-short-123"        # pragma: allowlist secret
_FAKE_LONG = "ig-long-456"          # pragma: allowlist secret
_FAKE_APP_SECRET = "app-secret-789"  # pragma: allowlist secret

_FIXED_ERRORS = {
    ig.E_NOT_CONNECTED, ig.E_AUTH_EXPIRED, ig.E_IDENTITY_MISSING,
    ig.E_CLIENT_NOT_CONFIGURED, ig.E_STATE_MISMATCH, ig.E_OAUTH_FAILED,
    ig.E_PROFESSIONAL_REQUIRED, ig.E_UNSUPPORTED_FORMAT, ig.E_MEDIA_REQUIRED,
    ig.E_TRANSFORM_REQUIRED, ig.E_ASSET_MISSING, ig.E_ASSET_TOO_LARGE,
    ig.E_ASSET_NOT_STAGED, ig.E_VIDEO_OUT_OF_RANGE, ig.E_CAPTION_TOO_LONG,
    ig.E_TOO_MANY_HASHTAGS, ig.E_CONTAINER_FAILED, ig.E_UPLOAD_FAILED,
    ig.E_PUBLISH_FAILED, ig.E_PREPARE_FAILED, ig.E_NOT_PREPARED,
    ig.E_AUTH_ERROR, ig.E_PERMISSION, ig.E_RATE_LIMITED, ig.E_SERVER_ERROR,
    ig.E_NETWORK_ERROR, "degraded",
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
    """Scripted stand-in for instagram._request — records calls, routes by URL
    fragment IN INSERTION ORDER (register the more specific fragment first,
    e.g. "media_publish" before "/media"), never touches a socket."""

    def __init__(self):
        self.calls = []
        self.routes = {}   # url fragment -> callable(method, url, headers, data) -> resp

    def __call__(self, method, url, *, headers=None, data=None, timeout=30.0):
        self.calls.append({"method": method, "url": url,
                           "headers": dict(headers or {}), "data": data})
        for frag, fn in self.routes.items():
            if frag in url:
                return fn(method, url, dict(headers or {}), data)
        return {"status": 404, "headers": {}, "body": b"{}"}

    def sent_to(self, frag):
        return [c for c in self.calls if frag in c["url"]]


def _ok(payload, status=200):
    return {"status": status, "headers": {},
            "body": json.dumps(payload).encode("utf-8")}


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(ig, "_request", fake)
    return fake


def _adapter(**config):
    a = InstagramAdapter()
    base = {"client_id": "app-id-1", "container_poll_interval_s": 0}
    base.update(config)
    a.configure(base)
    return a


def _connect(adapter, account_type="BUSINESS"):
    """Install a live-looking OAuth blob directly (round-trip tested apart)."""
    adapter.save_credentials({
        "access_token": _FAKE_LONG,          # pragma: allowlist secret
        "user_id": "17841400",
        "account": "fridaytest",
        "account_type": account_type,
        "scopes": list(ig._SCOPES),
        "expires_at": "2099-01-01T00:00:00Z",
    })


def _image_target(n=1, **options):
    assets = [{"kind": "image", "alt_text": f"art {i}", "format": "jpeg",
               "staged_url": f"https://stage.example/{i}.jpg"}
              for i in range(1, n + 1)]
    return {"id": "tgt_1", "format": "image_post",
            "adapted_body": "Friday made a thing today",
            "hashtags": ["fridayai", "aiart"],
            "adapted_assets": assets,
            "options": dict(options)}


def _publish_routes(http, uid="17841400", media_id="90001"):
    """Wire the full container→publish→permalink flow. Registration order
    matters: media_publish before the /media container-create fragment."""
    containers = {"n": 0}

    def _container(m, u, h, d):
        containers["n"] += 1
        return _ok({"id": f"cont_{containers['n']}"})

    http.routes["fields=status_code"] = lambda m, u, h, d: _ok(
        {"status_code": "FINISHED"})
    http.routes["media_publish"] = lambda m, u, h, d: _ok({"id": media_id})
    http.routes["fields=permalink"] = lambda m, u, h, d: _ok(
        {"permalink": f"https://www.instagram.com/p/{media_id}/"})
    http.routes[f"/{uid}/media"] = _container
    return containers


# ── capabilities (§4.2 / §4.5 honesty) ────────────────────────────────────────
def test_capabilities_honest():
    a = _adapter()
    caps = a.capabilities()
    assert caps["char_limit"] == 2200
    assert caps["analytics"] == "full"                   # per-media insights
    assert caps["thread"] is False
    assert caps["native_schedule"] is False
    assert caps["native_delete"] is False                # no delete API surface
    assert caps["hashtags_max"] == 30
    assert set(caps["formats"]) == {"image_post", "reel", "story"}
    img = caps["media"]["images"]
    assert img["max"] == 10                              # carousel ≤10 children
    assert img["formats"] == ["jpeg"]                    # JPEG only (§4.5)
    assert img["aspect"] == (0.8, 1.91)                  # 4:5 … 1.91:1
    assert caps["media"]["video"] is not None
    assert caps["media"]["alt_text"] is True
    assert a.default_daily_limit == 100                  # 100/24 h (§4.5)
    assert a.rate_budget()["limit"] == 100


# ── auth lifecycle (§4.1 / §12.2) ─────────────────────────────────────────────
def test_connect_url_state_bound():
    a = _adapter()
    url = a.connect_url("state-abc")
    assert url and url.startswith("https://www.instagram.com/oauth/authorize")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["app-id-1"]
    assert q["state"] == ["state-abc"]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == [
        "http://localhost:3000/api/content/platforms/instagram/callback"]
    assert "instagram_business_content_publish" in q["scope"][0]


def test_connect_url_requires_client_id():
    a = InstagramAdapter()
    a.configure({})
    assert a.connect_url("s") is None
    assert a.status()["last_error"] == ig.E_CLIENT_NOT_CONFIGURED


def test_callback_rejects_unknown_state(http):
    a = _adapter()
    a.set_simple_secret(_FAKE_APP_SECRET)
    a.connect_url("state-good")
    st = a.handle_callback({"code": "c1", "state": "state-EVIL"})
    assert st["connected"] is False
    assert st["last_error"] == ig.E_STATE_MISMATCH
    assert http.calls == []                              # no exchange attempted


def test_oauth_callback_round_trip(http):
    http.routes["api.instagram.com/oauth/access_token"] = lambda m, u, h, d: _ok(
        {"access_token": _FAKE_SHORT,                    # pragma: allowlist secret
         "user_id": "17841400",
         "permissions": ["instagram_business_basic",
                         "instagram_business_content_publish"]})
    http.routes["ig_exchange_token"] = lambda m, u, h, d: _ok(
        {"access_token": _FAKE_LONG,                     # pragma: allowlist secret
         "token_type": "bearer", "expires_in": 5183944})
    http.routes["/me?fields="] = lambda m, u, h, d: _ok(
        {"user_id": "17841400", "username": "fridaytest",
         "account_type": "BUSINESS"})

    a = _adapter()
    a.set_simple_secret(_FAKE_APP_SECRET)
    a.connect_url("st-1")
    st = a.handle_callback({"code": "code-xyz", "state": "st-1"})
    assert st["connected"] is True
    assert st["account"] == "fridaytest"
    assert st["professional_account"] is True
    assert "instagram_business_content_publish" in st["scopes"]
    assert st["expires_at"]

    # exchange was a form-encoded POST carrying the auth code
    ex = http.sent_to("api.instagram.com/oauth/access_token")[0]
    assert ex["method"] == "POST"
    form = parse_qs(ex["data"].decode("utf-8"))
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["code-xyz"]
    assert form["redirect_uri"] == [
        "http://localhost:3000/api/content/platforms/instagram/callback"]

    # blob persisted with the long-lived token; state was single-use
    blob = a.load_credentials()
    assert blob["access_token"] == _FAKE_LONG
    assert blob["user_id"] == "17841400"
    st2 = a.handle_callback({"code": "code-xyz", "state": "st-1"})
    assert st2["last_error"] == ig.E_STATE_MISMATCH

    # nothing tokenish in status() (§12.2)
    dump = json.dumps(st)
    assert _FAKE_SHORT not in dump and _FAKE_LONG not in dump
    assert _FAKE_APP_SECRET not in dump


def test_callback_exchange_failure_is_fixed_string(http):
    http.routes["api.instagram.com/oauth/access_token"] = lambda m, u, h, d: {
        "status": 400, "headers": {},
        "body": b'{"error_message": "secret details with /paths"}'}
    a = _adapter()
    a.set_simple_secret(_FAKE_APP_SECRET)
    a.connect_url("st-1")
    st = a.handle_callback({"code": "bad", "state": "st-1"})
    assert st["connected"] is False
    assert st["last_error"] == ig.E_OAUTH_FAILED
    assert "secret details" not in json.dumps(st)        # §12.5 content-free


def test_auth_blob_round_trip():
    a = _adapter()
    assert a.load_credentials() is None
    assert a.status()["connected"] is False
    _connect(a)
    blob = a.load_credentials()
    assert blob["account"] == "fridaytest"
    assert a.has_credentials() is True
    assert a.status()["connected"] is True
    res = a.clear_credentials()
    assert res["ok"] is True and res["removed"] is True
    assert a.load_credentials() is None
    assert a.status()["connected"] is False


def test_app_secret_alone_is_not_connected():
    a = _adapter()
    a.set_simple_secret(_FAKE_APP_SECRET)
    assert a.status()["connected"] is False              # no OAuth blob yet


def test_refresh_paths(http):
    a = _adapter()
    assert a.refresh() is False                          # nothing stored

    _connect(a)
    assert a.refresh() is True                           # fresh — no HTTP call
    assert http.calls == []

    # expiring soon → best-effort renewal fires and the blob rolls forward
    blob = a.load_credentials()
    exp_soon = pbase._now_utc() + __import__("datetime").timedelta(days=3)
    blob["expires_at"] = exp_soon.strftime("%Y-%m-%dT%H:%M:%SZ")
    a.save_credentials(blob)
    http.routes["refresh_access_token"] = lambda m, u, h, d: _ok(
        {"access_token": "ig-renewed-1",                 # pragma: allowlist secret
         "token_type": "bearer", "expires_in": 5183944})
    assert a.refresh() is True
    assert len(http.sent_to("refresh_access_token")) == 1
    assert a.load_credentials()["access_token"] == "ig-renewed-1"

    # expired long-lived token cannot be renewed → re-auth required
    blob = a.load_credentials()
    blob["expires_at"] = "2020-01-01T00:00:00Z"
    a.save_credentials(blob)
    assert a.refresh() is False
    st = a.status()
    assert st["needs_reauth"] is True
    assert st["last_error"] == ig.E_AUTH_EXPIRED


def test_status_expiry_countdown():
    a = _adapter()
    _connect(a)
    blob = a.load_credentials()
    exp = pbase._now_utc() + __import__("datetime").timedelta(days=5)
    blob["expires_at"] = exp.strftime("%Y-%m-%dT%H:%M:%SZ")
    a.save_credentials(blob)
    st = a.status()
    assert st["expiring_soon"] is True                   # ≤7-day warning window
    assert st["needs_reauth"] is False
    assert st["expires_in_days"] in (4, 5)


def test_revoke_is_local_purge_and_idempotent():
    a = _adapter()
    _connect(a)
    assert a.revoke() is True                            # no revoke API (§12.6)
    assert a.status()["connected"] is False
    assert a.revoke() is True                            # idempotent


# ── the sharp constraint: container publish flow (§4.5) ───────────────────────
def test_publish_single_image_happy_path(http):
    a = _adapter()
    _connect(a)
    containers = _publish_routes(http)

    prep = a.prepare(_image_target(), {"id": "post_1"})
    assert prep["ok"], prep
    p = prep["prepared"]
    assert p["creation_id"] == "cont_1"
    assert containers["n"] == 1
    assert "#fridayai" in p["caption"] and "#aiart" in p["caption"]

    # container carried the staged public URL + caption + alt text
    create = http.sent_to("/17841400/media")[0]
    form = parse_qs(create["data"].decode("utf-8"))
    assert form["image_url"] == ["https://stage.example/1.jpg"]
    assert form["alt_text"] == ["art 1"]
    assert "#fridayai" in form["caption"][0]

    res = a.publish(p)
    assert res["ok"] is True
    assert res["platform_post_id"] == "90001"
    assert res["post_url"] == "https://www.instagram.com/p/90001/"

    # media_publish fired with the container id, and budget was consumed
    pub = parse_qs(http.sent_to("media_publish")[0]["data"].decode("utf-8"))
    assert pub["creation_id"] == ["cont_1"]
    assert a.rate_budget()["used"] == 1


def test_publish_carousel_children_then_parent(http):
    a = _adapter()
    _connect(a)
    _publish_routes(http)

    prep = a.prepare(_image_target(n=3), {"id": "post_1"})
    assert prep["ok"], prep
    assert prep["prepared"]["creation_id"] == "cont_4"   # 3 children + parent

    creates = [parse_qs(c["data"].decode("utf-8"))
               for c in http.sent_to("/17841400/media")
               if "media_publish" not in c["url"]]
    children, parents = creates[:3], creates[3:]
    for i, child in enumerate(children, start=1):
        assert child["is_carousel_item"] == ["true"]
        assert child["image_url"] == [f"https://stage.example/{i}.jpg"]
        assert "caption" not in child                    # caption on parent only
    assert parents[0]["media_type"] == ["CAROUSEL"]
    assert parents[0]["children"] == ["cont_1,cont_2,cont_3"]
    assert "#fridayai" in parents[0]["caption"][0]

    res = a.publish(prep["prepared"])
    assert res["ok"] is True
    assert a.rate_budget()["used"] == 1                  # carousel = 1 (§4.5)


def test_publish_reel_resumable_upload(tmp_path, http):
    a = _adapter()
    _connect(a)
    _publish_routes(http)
    http.routes["rupload"] = lambda m, u, h, d: {"status": 200, "headers": {},
                                                 "body": b'{"success": true}'}
    http.routes[f"/17841400/media"] = lambda m, u, h, d: _ok(
        {"id": "cont_9", "uri": "https://rupload.example/ig-api-upload/cont_9"})
    http.routes["rupload.example"] = http.routes.pop("rupload")

    vid = tmp_path / "reel.mp4"
    vid.write_bytes(b"\x00" * 4096)
    prep = a.prepare({"id": "t1", "format": "reel",
                      "adapted_body": "watch this",
                      "adapted_assets": [{"kind": "video", "out_path": str(vid),
                                          "duration_s": 12.0}],
                      "options": {"share_to_feed": True}},
                     {"id": "p1"})
    assert prep["ok"], prep
    assert prep["prepared"]["creation_id"] == "cont_9"

    # container declared REELS + resumable; binary went to the upload URI
    create = parse_qs(http.sent_to("/17841400/media")[0]["data"].decode("utf-8"))
    assert create["media_type"] == ["REELS"]
    assert create["upload_type"] == ["resumable"]
    assert create["share_to_feed"] == ["true"]
    up = http.sent_to("rupload.example")[0]
    assert up["headers"]["Authorization"].startswith("OAuth ")
    assert up["headers"]["offset"] == "0"
    assert up["headers"]["file_size"] == "4096"
    assert len(up["data"]) == 4096

    res = a.publish(prep["prepared"])
    assert res["ok"] is True and res["platform_post_id"] == "90001"


def test_reel_duration_out_of_range(tmp_path, http):
    a = _adapter()
    _connect(a)
    vid = tmp_path / "blip.mp4"
    vid.write_bytes(b"\x00" * 128)
    res = a.prepare({"id": "t1", "format": "reel", "adapted_body": "x",
                     "adapted_assets": [{"kind": "video", "out_path": str(vid),
                                         "duration_s": 1.0}]},
                    {"id": "p1"})
    assert res["ok"] is False and res["error"] == ig.E_VIDEO_OUT_OF_RANGE
    assert http.calls == []                              # rejected locally


def test_publish_story_uses_public_url(http):
    a = _adapter()
    _connect(a)
    _publish_routes(http)
    prep = a.prepare({"id": "t1", "format": "story", "adapted_body": "",
                      "adapted_assets": [{"kind": "image", "alt_text": "s",
                                          "staged_url": "https://stage.example/s.jpg"}]},
                     {"id": "p1"})
    assert prep["ok"], prep
    create = parse_qs(http.sent_to("/17841400/media")[0]["data"].decode("utf-8"))
    assert create["media_type"] == ["STORIES"]
    assert create["image_url"] == ["https://stage.example/s.jpg"]


def test_container_error_fails_publish_without_burning_budget(http):
    a = _adapter()
    _connect(a)
    http.routes["fields=status_code"] = lambda m, u, h, d: _ok(
        {"status_code": "ERROR"})
    res = a.publish({"creation_id": "cont_bad", "format": "image_post"})
    assert res["ok"] is False and res["error"] == ig.E_CONTAINER_FAILED
    assert a.rate_budget()["used"] == 0
    assert http.sent_to("media_publish") == []           # never attempted


def test_publish_requires_prepared_container():
    a = _adapter()
    _connect(a)
    res = a.publish({"format": "image_post"})
    assert res["ok"] is False and res["error"] == ig.E_NOT_PREPARED


# ── §7.4 staging strategy: hold with a clear message, never silent ────────────
def test_unstaged_asset_without_staging_holds_with_clear_message(http):
    a = _adapter()                                       # no staging_base_url
    _connect(a)
    target = _image_target()
    del target["adapted_assets"][0]["staged_url"]        # local-only asset
    res = a.prepare(target, {"id": "p1"})
    assert res["ok"] is False
    assert res["degraded"] is True                       # §4.14: surfaced choice
    assert res["requires_user_choice"] is True
    assert res["reason"] == _STAGING_HOLD_MSG
    assert "configure staging or post manually" in res["reason"]
    assert http.sent_to("/17841400/media") == []         # nothing was created


def test_unstaged_asset_with_staging_configured_is_fixed_error(http):
    a = _adapter(staging_base_url="https://stage.example")
    _connect(a)
    target = _image_target()
    del target["adapted_assets"][0]["staged_url"]
    res = a.prepare(target, {"id": "p1"})
    assert res["ok"] is False
    assert res["error"] == ig.E_ASSET_NOT_STAGED         # publisher stages+retries
    assert res.get("degraded") is None


# ── §4.5 JPEG-only + aspect window: auto-crop/convert plan ────────────────────
def test_non_jpeg_image_gets_conversion_plan():
    a = _adapter()
    target = _image_target()
    target["adapted_assets"][0].update({"format": "png", "filename": "art.png"})
    res = a.prepare(target, {"id": "p1"})
    assert res["ok"] is False and res["error"] == ig.E_TRANSFORM_REQUIRED
    plan = res["transform_plan"]
    assert any(p["action"] == "convert" and p["to"] == "jpeg" for p in plan)
    assert any("JPEG only" in w for w in res["warnings"])


def test_out_of_aspect_image_gets_crop_plan():
    a = _adapter()
    target = _image_target()
    target["adapted_assets"][0].update({"filename": "tall.jpg",
                                        "width": 1000, "height": 2000})
    res = a.prepare(target, {"id": "p1"})                # aspect 0.5 < 4:5
    assert res["ok"] is False and res["error"] == ig.E_TRANSFORM_REQUIRED
    crop = [p for p in res["transform_plan"] if p["action"] == "crop"]
    assert crop and crop[0]["target_aspect"] == 0.8

    target["adapted_assets"][0].update({"width": 2000, "height": 1000})
    res = a.prepare(target, {"id": "p1"})                # aspect 2.0 > 1.91
    crop = [p for p in res["transform_plan"] if p["action"] == "crop"]
    assert crop and crop[0]["target_aspect"] == 1.91


def test_compliant_jpeg_needs_no_plan(http):
    a = _adapter()
    _connect(a)
    _publish_routes(http)
    target = _image_target()
    target["adapted_assets"][0].update({"filename": "ok.jpg",
                                        "width": 1080, "height": 1350})
    res = a.prepare(target, {"id": "p1"})                # 4:5 exactly
    assert res["ok"] is True


# ── caption norms (§4.5) ──────────────────────────────────────────────────────
def test_caption_norms_enforced():
    a = _adapter()
    # caption (body + hashtag block) over 2,200 → fixed error
    target = _image_target()
    target["adapted_body"] = "x" * 2195
    res = a.prepare(target, {"id": "p1"})
    assert res["ok"] is False and res["error"] == ig.E_CAPTION_TOO_LONG

    # >30 hashtags → fixed error
    target = _image_target()
    target["hashtags"] = [f"tag{i}" for i in range(31)]
    res = a.prepare(target, {"id": "p1"})
    assert res["ok"] is False and res["error"] == ig.E_TOO_MANY_HASHTAGS

    # >20 mentions → warning, not a block (disconnected → not_connected next)
    target = _image_target()
    target["adapted_body"] = " ".join(f"@user{i}" for i in range(21))
    res = a.prepare(target, {"id": "p1"})
    assert res["error"] == ig.E_NOT_CONNECTED
    assert any("mention" in w for w in res["warnings"])


def test_media_is_mandatory_and_formats_checked():
    a = _adapter()
    res = a.prepare({"id": "t1", "format": "image_post", "adapted_body": "hi"},
                    {"id": "p1"})
    assert res["ok"] is False and res["error"] == ig.E_MEDIA_REQUIRED
    res = a.prepare({"id": "t1", "format": "article", "adapted_body": "hi"},
                    {"id": "p1"})
    assert res["ok"] is False and res["error"] == ig.E_UNSUPPORTED_FORMAT


def test_professional_account_required(http):
    a = _adapter()
    _connect(a, account_type="PERSONAL")
    assert a.status()["professional_account"] is False
    res = a.prepare(_image_target(), {"id": "p1"})
    assert res["ok"] is False and res["error"] == ig.E_PROFESSIONAL_REQUIRED
    assert a.publish({"creation_id": "c1"})["error"] == ig.E_PROFESSIONAL_REQUIRED
    assert http.calls == []                              # refused locally


# ── 100/24 h budget + content_publishing_limit sync (§4.5) ────────────────────
def test_budget_hundred_per_day():
    a = _adapter()
    assert a.budget_would_exceed() is False
    a.consume_budget(100)
    b = a.rate_budget()
    assert (b["used"], b["limit"]) == (100, 100)
    assert a.budget_would_exceed() is True               # publisher defers


def test_budget_syncs_from_content_publishing_limit(http):
    a = _adapter()
    _connect(a)
    http.routes["content_publishing_limit"] = lambda m, u, h, d: _ok(
        {"data": [{"quota_usage": 42, "config": {"quota_total": 100}}]})
    b = a.rate_budget()
    assert b["used"] == 42                               # platform count wins
    assert b["limit"] == 100
    calls = len(http.sent_to("content_publishing_limit"))
    assert calls == 1
    a.rate_budget()                                      # throttled re-sync
    assert len(http.sent_to("content_publishing_limit")) == calls


def test_budget_survives_quota_endpoint_failure(http):
    a = _adapter()
    _connect(a)
    http.routes["content_publishing_limit"] = lambda m, u, h, d: {
        "status": 500, "headers": {}, "body": b"{}"}
    b = a.rate_budget()                                  # falls back to local
    assert (b["used"], b["limit"]) == (0, 100)


# ── analytics: insights → §8.2 unified keys ───────────────────────────────────
def test_fetch_metrics_unified_mapping(http):
    a = _adapter()
    _connect(a)
    http.routes["/insights"] = lambda m, u, h, d: _ok({"data": [
        {"name": "views", "values": [{"value": 100}]},
        {"name": "reach", "total_value": {"value": 80}},
        {"name": "likes", "values": [{"value": 5}]},
        {"name": "comments", "values": [{"value": 2}]},
        {"name": "shares", "values": [{"value": 1}]},
        {"name": "saved", "values": [{"value": 4}]},
        {"name": "follows", "values": [{"value": 3}]},
    ]})
    m = a.fetch_metrics("90001")
    assert m == {"impressions": 100, "reach": 80, "likes": 5, "comments": 2,
                 "shares": 1, "saves": 4, "follows_gained": 3}


def test_fetch_metrics_missing_is_absent_not_zero(http):
    a = _adapter()
    _connect(a)
    http.routes["/insights"] = lambda m, u, h, d: _ok(
        {"data": [{"name": "likes", "values": [{"value": 7}]}]})
    assert a.fetch_metrics("90001") == {"likes": 7}      # missing ≠ 0 (§8.2)
    http.routes["/insights"] = lambda m, u, h, d: _ok({"data": []})
    assert a.fetch_metrics("90001") is None


def test_fetch_metrics_retries_core_set_on_400(http):
    a = _adapter()
    _connect(a)

    def _insights(m, u, h, d):
        if "views," in u:                                # richer set rejected
            return {"status": 400, "headers": {}, "body": b"{}"}
        return _ok({"data": [{"name": "saved", "values": [{"value": 9}]}]})

    http.routes["/insights"] = _insights
    assert a.fetch_metrics("90001") == {"saves": 9}
    assert len(http.sent_to("/insights")) == 2           # one honest retry


def test_fetch_metrics_none_when_disconnected(http):
    a = _adapter()
    assert a.fetch_metrics("90001") is None
    assert a.fetch_account_metrics() is None
    assert http.calls == []                              # no blind API calls


def test_fetch_account_metrics(http):
    a = _adapter()
    _connect(a)
    http.routes["followers_count"] = lambda m, u, h, d: _ok(
        {"followers_count": 42, "media_count": 7})
    assert a.fetch_account_metrics() == {"followers": 42, "posts": 7}


# ── §12.5: fixed content-free errors, envelopes never raise ───────────────────
def test_errors_are_fixed_and_content_free(http):
    a = _adapter()

    # disconnected paths
    assert a.publish({"creation_id": "c"})["error"] == ig.E_NOT_CONNECTED
    assert a.prepare(_image_target(), {})["error"] == ig.E_NOT_CONNECTED

    # expired token → fixed string
    _connect(a)
    blob = a.load_credentials()
    blob["expires_at"] = "2020-01-01T00:00:00Z"
    a.save_credentials(blob)
    assert a.publish({"creation_id": "c"})["error"] == ig.E_AUTH_EXPIRED

    # platform 4xx/5xx → fixed strings, platform body never leaks
    _connect(a)
    http.routes["/17841400/media"] = lambda m, u, h, d: {
        "status": 500, "headers": {},
        "body": b'{"error": {"message": "internal detail with /paths"}}'}
    res = a.prepare(_image_target(), {"id": "p1"})
    assert res["error"] == ig.E_SERVER_ERROR
    assert "internal detail" not in json.dumps(res)

    http.routes["/17841400/media"] = lambda m, u, h, d: {
        "status": 429, "headers": {"Retry-After": "120"}, "body": b"{}"}
    res = a.prepare(_image_target(), {"id": "p1"})
    assert res["error"] == ig.E_RATE_LIMITED
    assert res["retry_after"] == 120                     # §7.5 honors Retry-After

    # everything emitted stays in the fixed vocabulary; nothing tokenish
    st = a.status()
    assert st["last_error"] in _FIXED_ERRORS | {None}
    assert _FAKE_LONG not in json.dumps(st)

    # delete is honestly unsupported (no API surface)
    assert a.delete("90001") == {"ok": False, "error": "not_supported"}


def test_prepare_and_publish_never_raise_on_garbage(http):
    a = _adapter()
    assert a.prepare(None, None)["ok"] is False
    assert a.publish(None)["ok"] is False
    assert isinstance(a.status(), dict)
