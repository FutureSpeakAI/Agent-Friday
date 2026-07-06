"""Unit tests for the Mastodon platform adapter (spec §4.8).

All transport is stubbed at the module chokepoint (``mastodon._http``) —
zero network. The credential store is faked to plaintext-on-tmpdir so auth
blobs round-trip without touching DPAPI/vault. Covers: instance-aware limit
discovery from /api/v2/instance (never hard-coded 500), the async media
processing poll, the Idempotency-Key derived from the target id, native
scheduled_at (≥5 min, threads rejected honestly), the nsfw→sensitive +
spoiler_text/CW mapping, reply-chain threads with per-segment resume, the
§8.2 counts mapping, and §12.5 fixed content-free error strings.
"""
from __future__ import annotations

import hashlib
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
from agent_friday.services.platforms import mastodon as mmod           # noqa: E402
from agent_friday.services.platforms.mastodon import MastodonAdapter   # noqa: E402

INSTANCE = "https://mstdn.example"


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


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The media poll and thread jitter sleep for real — not in unit tests."""
    monkeypatch.setattr(mmod.time, "sleep", lambda s: None)


class FakeHTTP:
    """Scriptable stand-in for mastodon._http — records every call."""

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
    monkeypatch.setattr(mmod, "_http", fake)
    return fake


@pytest.fixture
def adapter():
    a = MastodonAdapter()
    a.configure({"instance": "mstdn.example", "thread_jitter_s": 0})
    return a


def _connect(a, **extra):
    """Store a live-looking OAuth blob directly (fake credential store)."""
    blob = {"access_token": "masto-access-1",      # pragma: allowlist secret
            "scopes": ["write:statuses", "write:media", "read:statuses"],
            "account": "@friday@mstdn.example",
            "instance": INSTANCE,
            "app": {"client_id": "cid-1", "client_secret": "csec-1",  # pragma: allowlist secret
                    "instance": INSTANCE},
            "expires_at": None,
            "connected_at": "2026-01-01T00:00:00Z"}
    blob.update(extra)
    assert a.save_credentials(blob)["ok"]
    return blob


def _instance_payload(max_chars=500, max_media=4, image_bytes=16 * 1024 * 1024,
                      video_bytes=99 * 1024 * 1024):
    return {"domain": "mstdn.example",
            "configuration": {
                "statuses": {"max_characters": max_chars,
                             "max_media_attachments": max_media},
                "media_attachments": {"image_size_limit": image_bytes,
                                      "video_size_limit": video_bytes}}}


def _route_instance(http, **kw):
    http.route(f"GET {INSTANCE}/api/v2/instance", _ok(_instance_payload(**kw)))


# ── instance-aware limits (§4.8 — never hard-coded 500) ──────────────────────
def test_capabilities_discovered_from_instance_not_hardcoded(adapter, http):
    _route_instance(http, max_chars=1234, max_media=6,
                    image_bytes=8 * 1024 * 1024)
    caps = adapter.capabilities()
    assert caps["char_limit"] == 1234                 # NOT 500
    assert caps["media"]["images"]["max"] == 6
    assert caps["media"]["images"]["max_bytes"] == 8 * 1024 * 1024
    assert caps["native_schedule"] is True
    assert caps["thread"] is True
    assert caps["analytics"] == "counts"
    assert any("discovered live" in n for n in caps["notes"])


def test_instance_discovery_is_cached(adapter, http):
    _route_instance(http)
    adapter.capabilities()
    adapter.capabilities()
    assert len(http.posts("/api/v2/instance")) == 1


def test_unreachable_instance_falls_back_conservative(adapter, http):
    http.route("/api/v2/instance",
               {"status": 0, "json": None, "text": "", "headers": {},
                "error": "network_error"})
    caps = adapter.capabilities()
    assert caps["char_limit"] == 500
    assert caps["media"]["images"]["max"] == 4
    assert any("not reachable" in n for n in caps["notes"])


def test_no_instance_configured_no_transport(http):
    a = MastodonAdapter()
    a.configure({})
    caps = a.capabilities()
    assert caps["char_limit"] == 500
    assert any("no instance configured" in n for n in caps["notes"])
    assert http.calls == []


def test_body_over_500_passes_when_instance_allows_more(adapter, http):
    """THE §4.8 point: a 600-char body is legal on a 5000-char instance."""
    _route_instance(http, max_chars=5000)
    _connect(adapter)
    res = adapter.prepare({"adapted_body": "x" * 600, "format": "post"}, {})
    assert res["ok"], res


def test_body_over_discovered_limit_rejected_even_under_500(adapter, http):
    _route_instance(http, max_chars=300)
    res = adapter.prepare({"adapted_body": "x" * 400, "format": "post"}, {})
    assert res["ok"] is False
    assert "char_limit" in res["error"]


# ── auth: OAuth2 code flow with per-instance app registration ────────────────
def test_connect_url_registers_app_and_binds_state(adapter, http):
    http.route(f"POST {INSTANCE}/api/v1/apps",
               _ok({"client_id": "cid-9", "client_secret": "csec-9"}))  # pragma: allowlist secret
    url = adapter.connect_url("state-42")
    assert url and url.startswith(f"{INSTANCE}/oauth/authorize?")
    assert "state=state-42" in url
    assert "code_challenge_method=S256" in url
    assert "client_id=cid-9" in url
    app_call = http.posts("/api/v1/apps")[0]
    assert app_call["json"]["redirect_uris"] == mmod.DEFAULT_REDIRECT_URI
    blob = adapter.load_credentials()
    assert blob["pending_auth"]["state"] == "state-42"
    assert blob["app"]["client_id"] == "cid-9"


def test_connect_url_requires_instance_no_transport(http):
    a = MastodonAdapter()
    a.configure({})
    assert a.connect_url("s") is None
    assert a._last_error == "missing_instance_config"
    assert http.calls == []


def test_handle_callback_round_trip_and_replay_rejected(adapter, http):
    http.route(f"POST {INSTANCE}/api/v1/apps",
               _ok({"client_id": "cid-9", "client_secret": "csec-9"}))  # pragma: allowlist secret
    http.route(f"POST {INSTANCE}/oauth/token",
               _ok({"access_token": "granted-token",   # pragma: allowlist secret
                    "scope": "write:statuses write:media read:statuses"}))
    http.route("GET " + INSTANCE + "/api/v1/accounts/verify_credentials",
               _ok({"acct": "friday", "followers_count": 10}))
    adapter.connect_url("state-42")
    st = adapter.handle_callback({"state": "state-42", "code": "code-1"})
    assert st["connected"] is True
    assert st["account"] == "@friday@mstdn.example"
    assert st["auth_source"] == "oauth"
    # nothing tokenish leaves through status()
    assert "granted-token" not in json.dumps(st)
    blob = adapter.load_credentials()
    assert blob["access_token"] == "granted-token"
    assert "pending_auth" not in blob              # single-use, popped
    # form asserts on the exchange itself
    tok = http.posts("/oauth/token")[0]
    form = tok["body"].decode("utf-8")
    assert "grant_type=authorization_code" in form
    assert "code=code-1" in form and "code_verifier=" in form
    # replay of the same callback is rejected (state already spent)
    st2 = adapter.handle_callback({"state": "state-42", "code": "code-1"})
    assert st2["last_error"] == "oauth_state_mismatch"
    assert len(http.posts("/oauth/token")) == 1    # no second exchange


def test_handle_callback_state_mismatch_no_exchange(adapter, http):
    http.route(f"POST {INSTANCE}/api/v1/apps",
               _ok({"client_id": "cid-9", "client_secret": "s"}))  # pragma: allowlist secret
    adapter.connect_url("state-42")
    st = adapter.handle_callback({"state": "EVIL", "code": "code-1"})
    assert st["connected"] is False
    assert st["last_error"] == "oauth_state_mismatch"
    assert http.posts("/oauth/token") == []


def test_token_paste_mode_connects_and_publishes(adapter, http):
    adapter.set_simple_secret("pasted-token-1")    # pragma: allowlist secret
    st = adapter.status()
    assert st["connected"] is True
    assert st["auth_source"] == "token_paste"
    http.route(f"POST {INSTANCE}/api/v1/statuses",
               _ok({"id": "111", "url": f"{INSTANCE}/@friday/111"}))
    res = adapter.publish({"target_id": "tgt_1", "body": "hello fedi",
                           "segments": [], "options": {}})
    assert res["ok"] is True
    call = http.posts("/api/v1/statuses")[0]
    assert call["headers"]["Authorization"] == "Bearer pasted-token-1"


def test_refresh_reflects_token_presence(adapter, http):
    assert adapter.refresh() is False              # nothing stored
    _connect(adapter)
    assert adapter.refresh() is True               # tokens never expire — no HTTP
    assert http.calls == []


def test_revoke_platform_side_and_idempotent(adapter, http, _fake_store):
    _connect(adapter)
    http.route(f"POST {INSTANCE}/oauth/revoke", _ok({}))
    assert adapter.revoke() is True
    assert len(http.posts("/oauth/revoke")) == 1
    form = http.posts("/oauth/revoke")[0]["body"].decode("utf-8")
    assert "token=masto-access-1" in form  # pragma: allowlist secret
    assert adapter.load_credentials() is None
    assert adapter.status()["connected"] is False
    assert adapter.revoke() is True                # idempotent


# ── prepare: nsfw→sensitive + CW mapping, spoiler counts against limit ───────
def test_prepare_maps_nsfw_tag_to_sensitive_and_cw(adapter, http):
    _route_instance(http)
    res = adapter.prepare(
        {"adapted_body": "spicy art", "format": "post",
         "options": {"spoiler_text": "CW: art"}},
        {"tags": ["NSFW", "art"]})
    assert res["ok"], res
    opts = res["prepared"]["options"]
    assert opts["sensitive"] is True
    assert opts["spoiler_text"] == "CW: art"


def test_prepare_sensitive_option_without_tag(adapter, http):
    _route_instance(http)
    res = adapter.prepare({"adapted_body": "x", "format": "post",
                           "options": {"sensitive": True}}, {})
    assert res["ok"] and res["prepared"]["options"]["sensitive"] is True
    res2 = adapter.prepare({"adapted_body": "x", "format": "post"}, {})
    assert res2["ok"] and res2["prepared"]["options"]["sensitive"] is False


def test_prepare_spoiler_text_counts_against_limit(adapter, http):
    _route_instance(http, max_chars=500)
    body = "x" * 490
    ok = adapter.prepare({"adapted_body": body, "format": "post"}, {})
    assert ok["ok"]
    over = adapter.prepare({"adapted_body": body, "format": "post",
                            "options": {"spoiler_text": "y" * 20}}, {})
    assert over["ok"] is False
    assert "spoiler_text counts" in over["error"]


def test_prepare_over_limit_thread_segment_rejected(adapter, http):
    _route_instance(http, max_chars=100)
    res = adapter.prepare({"adapted_body": "hook", "format": "thread",
                           "segments": ["ok", "z" * 150]}, {})
    assert res["ok"] is False
    assert "segment(s) [1]" in res["error"]


def test_prepare_unknown_visibility_warns_and_defaults(adapter, http):
    _route_instance(http)
    res = adapter.prepare({"adapted_body": "x", "format": "post",
                           "options": {"visibility": "loud"}}, {})
    assert res["ok"]
    assert res["prepared"]["options"]["visibility"] == "public"
    assert any("visibility" in w for w in res["warnings"])


def test_prepare_hashtag_block_appended_not_duplicated(adapter, http):
    _route_instance(http)
    res = adapter.prepare({"adapted_body": "already has #Fedi",
                           "format": "post",
                           "hashtags": ["Fedi", "Friday"]}, {})
    assert res["ok"]
    body = res["prepared"]["body"]
    assert body.count("#Fedi") == 1                # not duplicated
    assert "#Friday" in body


def test_prepare_missing_alt_text_warns(adapter, http, tmp_path):
    _route_instance(http)
    res = adapter.prepare(
        {"adapted_body": "pic", "format": "image_post",
         "adapted_assets": [{"kind": "image"}]}, {})
    assert res["ok"]
    assert any("alt text" in w for w in res["warnings"])


def test_prepare_video_must_be_only_attachment(adapter, http):
    _route_instance(http)
    res = adapter.prepare(
        {"adapted_body": "x", "format": "post",
         "adapted_assets": [{"kind": "video"}, {"kind": "image"}]}, {})
    assert res["ok"] is False
    assert res["error"] == "video must be the only attachment"


def test_prepare_never_raises_on_garbage(adapter):
    res = adapter.prepare(None, None)
    assert isinstance(res, dict) and "ok" in res


# ── media upload: async processing poll (§4.8) ───────────────────────────────
def _img(tmp_path, name="a.png", size=64):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG" + b"\x00" * size)
    return p


def test_media_upload_sync_200_with_alt_text(adapter, http, tmp_path):
    _route_instance(http)
    _connect(adapter)
    http.route(f"POST {INSTANCE}/api/v2/media", _ok({"id": "m1"}))
    res = adapter.prepare(
        {"adapted_body": "pic", "format": "image_post",
         "adapted_assets": [{"kind": "image", "path": str(_img(tmp_path)),
                             "alt_text": "a red square"}]}, {})
    assert res["ok"], res
    assert res["prepared"]["media_ids"] == ["m1"]
    up = http.posts("/api/v2/media")[0]
    assert up["headers"]["Authorization"] == "Bearer masto-access-1"
    assert b"a red square" in up["body"]           # description field rides along
    assert b"\x89PNG" in up["body"]


def test_media_upload_async_202_polls_until_processed(adapter, http, tmp_path):
    _route_instance(http)
    _connect(adapter)
    http.route(f"POST {INSTANCE}/api/v2/media", _ok({"id": "m2"}, status=202))
    polls = {"n": 0}

    def _poll(call):
        polls["n"] += 1
        if polls["n"] < 3:                          # 206 = still processing
            return {"status": 206, "json": None, "text": "", "headers": {}}
        return _ok({"id": "m2", "url": "https://files/m2.png"})

    http.route(f"GET {INSTANCE}/api/v1/media/m2", _poll)
    res = adapter.prepare(
        {"adapted_body": "pic", "format": "image_post",
         "adapted_assets": [{"kind": "image", "path": str(_img(tmp_path))}]}, {})
    assert res["ok"], res
    assert res["prepared"]["media_ids"] == ["m2"]
    assert polls["n"] == 3


def test_media_upload_poll_exhausted_fails_loudly(adapter, http, tmp_path):
    _route_instance(http)
    _connect(adapter)
    http.route(f"POST {INSTANCE}/api/v2/media", _ok({"id": "m3"}, status=202))
    http.route(f"GET {INSTANCE}/api/v1/media/m3",
               {"status": 206, "json": None, "text": "", "headers": {}})
    res = adapter.prepare(
        {"adapted_body": "pic", "format": "image_post",
         "adapted_assets": [{"kind": "image", "path": str(_img(tmp_path))}]}, {})
    assert res["ok"] is False
    assert res["error"] == "media_upload_failed"
    assert len(http.posts(f"GET {INSTANCE}/api/v1/media/m3")) == mmod._MEDIA_POLL_TRIES


def test_media_too_large_per_discovered_instance_limit(adapter, http, tmp_path):
    _route_instance(http, image_bytes=16)          # tiny discovered cap
    _connect(adapter)
    res = adapter.prepare(
        {"adapted_body": "pic", "format": "image_post",
         "adapted_assets": [{"kind": "image",
                             "path": str(_img(tmp_path, size=64))}]}, {})
    assert res["ok"] is False
    assert res["error"] == "media_too_large"
    assert http.posts("/api/v2/media") == []       # rejected before upload


def test_media_upload_requires_connection_no_transport(adapter, http, tmp_path):
    _route_instance(http)
    res = adapter.prepare(
        {"adapted_body": "pic", "format": "image_post",
         "adapted_assets": [{"kind": "image", "path": str(_img(tmp_path))}]}, {})
    assert res["ok"] is False
    assert res["error"] == "not_connected"
    assert http.posts("/api/v2/media") == []


# ── publish: Idempotency-Key derived from target id (§4.8/§7.2) ──────────────
def test_publish_single_status_sends_idempotency_key(adapter, http):
    _connect(adapter)
    http.route(f"POST {INSTANCE}/api/v1/statuses",
               _ok({"id": "42", "url": f"{INSTANCE}/@friday/42"}))
    res = adapter.publish({"target_id": "tgt_9", "body": "hello",
                           "segments": [], "options": {}})
    assert res["ok"] is True
    assert res["platform_post_id"] == "42"
    assert res["post_url"] == f"{INSTANCE}/@friday/42"
    key1 = http.posts("/api/v1/statuses")[0]["headers"]["Idempotency-Key"]
    assert len(key1) == 64 and all(c in "0123456789abcdef" for c in key1)
    # identical retry reuses the exact key (double-post insurance) …
    adapter.publish({"target_id": "tgt_9", "body": "hello",
                     "segments": [], "options": {}})
    key2 = http.posts("/api/v1/statuses")[1]["headers"]["Idempotency-Key"]
    assert key2 == key1
    # … an edited retry mints a new one
    adapter.publish({"target_id": "tgt_9", "body": "hello, edited",
                     "segments": [], "options": {}})
    key3 = http.posts("/api/v1/statuses")[2]["headers"]["Idempotency-Key"]
    assert key3 != key1
    # and a different target never shares a key
    adapter.publish({"target_id": "tgt_10", "body": "hello",
                     "segments": [], "options": {}})
    key4 = http.posts("/api/v1/statuses")[3]["headers"]["Idempotency-Key"]
    assert key4 != key1


def test_publish_without_credentials_never_touches_transport(adapter, http):
    res = adapter.publish({"body": "x"})
    assert res == {"ok": False, "error": "not_connected"}
    assert http.calls == []


def test_publish_thread_reply_chain_resumes_after_mid_thread_failure(adapter, http):
    _connect(adapter)
    counter = {"n": 0}

    def _statuses(call):
        counter["n"] += 1
        if counter["n"] == 3:                      # third segment dies once
            return {"status": 500, "json": None, "text": "boom", "headers": {}}
        i = counter["n"]
        return _ok({"id": f"s{i}", "url": f"{INSTANCE}/@friday/s{i}"})

    http.route("/api/v1/statuses", _statuses)
    prepared = {"target_id": "tgt_t", "body": "hook",
                "segments": ["one", "two", "three"], "options": {}}
    res = adapter.publish(prepared)
    assert res["ok"] is False
    assert res["error"] == "platform_http_error"
    assert res["resume_index"] == 2
    assert res["segment_ids"] == ["s1", "s2"]
    # retry resumes: only ONE more create, replying to the last confirmed id
    res2 = adapter.publish(prepared)
    assert res2["ok"] is True, res2
    assert res2["platform_post_id"] == "s1"        # root of the thread
    assert res2["raw"]["segment_ids"] == ["s1", "s2", "s4"]
    assert res2["raw"]["resumed_from"] == 2
    calls = http.posts("/api/v1/statuses")
    assert len(calls) == 4                          # 3 attempts + 1 resume
    assert calls[3]["json"]["in_reply_to_id"] == "s2"
    # budget: only successful segments consumed
    assert adapter.rate_budget()["used"] == 3


def test_publish_edited_thread_does_not_resume_stale_progress(adapter, http):
    _connect(adapter)
    seen = {"n": 0}

    def _statuses(call):
        seen["n"] += 1
        if seen["n"] == 2:
            return {"status": 500, "json": None, "text": "", "headers": {}}
        return _ok({"id": f"e{seen['n']}"})

    http.route("/api/v1/statuses", _statuses)
    adapter.publish({"target_id": "tgt_e", "body": "",
                     "segments": ["a", "b"], "options": {}})
    # edit segment content → fingerprint changes → starts from segment 0
    res = adapter.publish({"target_id": "tgt_e", "body": "",
                           "segments": ["a EDITED", "b"], "options": {}})
    assert res["ok"] is True
    calls = http.posts("/api/v1/statuses")
    assert json.loads(calls[2]["body"])["status"] == "a EDITED"
    assert "in_reply_to_id" not in json.loads(calls[2]["body"])


def test_publish_thread_cw_and_sensitive_ride_every_segment(adapter, http):
    _connect(adapter)
    seen = {"n": 0}

    def _statuses(call):
        seen["n"] += 1
        return _ok({"id": f"c{seen['n']}"})

    http.route("/api/v1/statuses", _statuses)
    res = adapter.publish({"target_id": "tgt_cw", "body": "",
                           "segments": ["one", "two"],
                           "media_ids": ["m1"],
                           "options": {"sensitive": True,
                                       "spoiler_text": "CW: long",
                                       "language": "en"}})
    assert res["ok"], res
    payloads = [json.loads(c["body"]) for c in http.posts("/api/v1/statuses")]
    for p in payloads:
        assert p["sensitive"] is True
        assert p["spoiler_text"] == "CW: long"
        assert p["language"] == "en"
    assert payloads[0]["media_ids"] == ["m1"]      # media on the root only
    assert "media_ids" not in payloads[1]
    assert payloads[1]["in_reply_to_id"] == "c1"


# ── native scheduling (§4.8/§6.5) ────────────────────────────────────────────
def _iso_in(minutes):
    return (pbase._now_utc() + timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def test_publish_native_schedule_at_least_5_min(adapter, http):
    _connect(adapter)
    when = _iso_in(30)
    http.route("/api/v1/statuses", _ok({"id": "sch1"}))
    res = adapter.publish({"target_id": "tgt_s", "body": "later",
                           "segments": [], "options": {"scheduled_at": when}})
    assert res["ok"] is True
    assert res["post_url"] is None                 # no URL until it publishes
    assert res["raw"]["scheduled"] is True
    assert res["raw"]["scheduled_at"] == when
    payload = json.loads(http.posts("/api/v1/statuses")[0]["body"])
    assert payload["scheduled_at"] == when


def test_publish_schedule_under_5_min_posts_now(adapter, http):
    _connect(adapter)
    http.route("/api/v1/statuses", _ok({"id": "now1", "url": f"{INSTANCE}/@f/now1"}))
    res = adapter.publish({"target_id": "tgt_n", "body": "soon",
                           "segments": [],
                           "options": {"scheduled_at": _iso_in(2)}})
    assert res["ok"] is True
    assert res["raw"].get("scheduled_ignored") is True
    payload = json.loads(http.posts("/api/v1/statuses")[0]["body"])
    assert "scheduled_at" not in payload           # posted immediately instead


def test_publish_native_schedule_thread_rejected_honestly(adapter, http):
    _connect(adapter)
    res = adapter.publish({"target_id": "tgt_x", "body": "",
                           "segments": ["a", "b"],
                           "options": {"scheduled_at": _iso_in(30)}})
    assert res["ok"] is False
    assert res["error"] == "native_schedule_thread_unsupported"
    assert http.calls == []                        # surfaced, never half-sent


def test_publish_budget_exhausted_defers_no_transport(adapter, http):
    _connect(adapter)
    adapter.consume_budget(adapter._budget_limit())
    res = adapter.publish({"target_id": "t", "body": "x", "options": {}})
    assert res["ok"] is False
    assert res["error"] == "rate_budget_exhausted"
    assert res.get("defer_until")
    assert http.calls == []


# ── delete: statuses first, scheduled_statuses fallback ──────────────────────
def test_delete_status_and_scheduled_fallback(adapter, http):
    _connect(adapter)
    http.route(f"DELETE {INSTANCE}/api/v1/statuses/live1", _ok({"id": "live1"}))
    assert adapter.delete("live1") == {"ok": True, "deleted": True}

    http.route(f"DELETE {INSTANCE}/api/v1/statuses/sch9",
               {"status": 404, "json": None, "text": "", "headers": {}})
    http.route(f"DELETE {INSTANCE}/api/v1/scheduled_statuses/sch9", _ok({}))
    res = adapter.delete("sch9")
    assert res["ok"] is True and res["deleted"] is True and res["scheduled"] is True


# ── analytics: §8.2 counts mapping, missing ≠ zero ───────────────────────────
def test_fetch_metrics_unified_mapping(adapter, http):
    _connect(adapter)
    http.route(f"GET {INSTANCE}/api/v1/statuses/42",
               _ok({"id": "42", "favourites_count": 7, "reblogs_count": 3,
                    "replies_count": 2}))
    m = adapter.fetch_metrics("42")
    assert m == {"likes": 7, "shares": 3, "comments": 2}
    assert "impressions" not in m                  # Mastodon has none — absent


def test_fetch_metrics_missing_counts_absent_not_zero(adapter, http):
    _connect(adapter)
    http.route(f"GET {INSTANCE}/api/v1/statuses/43",
               _ok({"id": "43", "favourites_count": 5}))
    m = adapter.fetch_metrics("43")
    assert m == {"likes": 5}
    assert "comments" not in m and "shares" not in m


def test_fetch_metrics_disconnected_or_unknown_is_none(adapter, http):
    assert adapter.fetch_metrics("42") is None     # not connected
    _connect(adapter)
    http.route(f"GET {INSTANCE}/api/v1/statuses/gone",
               {"status": 404, "json": None, "text": "", "headers": {}})
    assert adapter.fetch_metrics("gone") is None


def test_fetch_account_metrics(adapter, http):
    _connect(adapter)
    http.route("GET " + INSTANCE + "/api/v1/accounts/verify_credentials",
               _ok({"followers_count": 120, "statuses_count": 900}))
    assert adapter.fetch_account_metrics() == {"followers": 120, "posts": 900}


# ── §12.5 fixed content-free error strings ───────────────────────────────────
def test_errors_are_fixed_content_free_strings(adapter, http):
    _connect(adapter)
    http.route("/api/v1/statuses",
               {"status": 401, "json": {"error": "The access token is SECRET-DETAIL"},
                "text": "SECRET-DETAIL", "headers": {}})
    res = adapter.publish({"target_id": "t", "body": "x", "options": {}})
    assert res["error"] == "auth_error"
    assert "SECRET-DETAIL" not in json.dumps(res)

    http.routes.clear()
    http.route("/api/v1/statuses",
               {"status": 429, "json": None, "text": "slow down please",
                "headers": {"retry-after": "120"}})
    res = adapter.publish({"target_id": "t", "body": "x", "options": {}})
    assert res["error"] == "rate_limited"
    assert res["retry_after"] == 120               # §7.5: publisher honors it
    assert "slow down" not in json.dumps(res)

    http.routes.clear()                            # dead network → network_error
    http.route("/api/v1/statuses",
               {"status": 0, "json": None, "text": "", "headers": {},
                "error": "network_error"})
    res = adapter.publish({"target_id": "t", "body": "x", "options": {}})
    assert res["error"] == "network_error"


def test_registry_resolves_mastodon():
    a = preg.get_adapter("mastodon")
    assert isinstance(a, MastodonAdapter)
    assert a.name == "mastodon"
