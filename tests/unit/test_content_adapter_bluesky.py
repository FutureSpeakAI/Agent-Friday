"""Unit tests for the Bluesky/ATProto platform adapter (spec §4.7).

All transport is stubbed at the module chokepoint (``bluesky._http``) —
zero network. The credential store is faked to plaintext-on-tmpdir so auth
blobs round-trip without touching DPAPI/vault. Covers: the transport-stubbed
publish happy path, the app-password createSession auth blob round-trip
(refresh JWT rotation + re-login fallback), the platform's sharp constraints
(UTF-8 byte-offset richtext facets with emoji-adjacent links, the
300-GRAPHEME limit, uploadBlob ≤1 MB with recompress, video feature-detect,
reply-chain threads with per-segment resume), the §8.2 getPosts counts
mapping, and §12.5 fixed content-free error strings.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import platforms as preg                  # noqa: E402
from agent_friday.services.platforms import base as pbase            # noqa: E402
from agent_friday.services.platforms import bluesky as bmod          # noqa: E402
from agent_friday.services.platforms.bluesky import BlueskyAdapter   # noqa: E402


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
    """Scriptable stand-in for bluesky._http — records every call."""

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
    monkeypatch.setattr(bmod, "_http", fake)
    return fake


@pytest.fixture
def adapter():
    a = BlueskyAdapter()
    a.configure({"thread_jitter_s": 0})
    return a


def _connect(a, **extra):
    """Store a live-looking session blob directly (fake credential store)."""
    blob = {"did": "did:plc:abc123",
            "handle": "friday.bsky.social",
            "account": "friday.bsky.social",
            "identifier": "friday.bsky.social",
            "access_jwt": "jwt-access-1",          # pragma: allowlist secret
            "refresh_jwt": "jwt-refresh-1",        # pragma: allowlist secret
            "expires_at": "2099-01-01T00:00:00Z",
            "pds": "https://bsky.social",
            "scopes": ["app_password"]}
    blob.update(extra)
    assert a.save_credentials(blob)["ok"]
    return blob


def _uri(rkey):
    return f"at://did:plc:abc123/app.bsky.feed.post/{rkey}"


# ── publish happy path (transport stubbed) ────────────────────────────────────
def test_publish_single_post_happy_path(adapter, http):
    _connect(adapter)
    http.route("POST https://bsky.social/xrpc/com.atproto.repo.createRecord",
               _ok({"uri": _uri("3k2aaa"), "cid": "bafy1"}))
    prep = adapter.prepare({"adapted_body": "hello bluesky", "format": "post",
                            "id": "tgt_1"}, {"id": "post_1"})
    assert prep["ok"], prep
    res = adapter.publish(prep["prepared"])
    assert res["ok"] is True
    assert res["platform_post_id"] == _uri("3k2aaa")
    assert res["post_url"] == "https://bsky.app/profile/friday.bsky.social/post/3k2aaa"
    call = http.posts("createRecord")[0]
    assert call["headers"]["Authorization"] == "Bearer jwt-access-1"
    envelope = call["json"]
    assert envelope["repo"] == "did:plc:abc123"
    assert envelope["collection"] == "app.bsky.feed.post"
    rec = envelope["record"]
    assert rec["$type"] == "app.bsky.feed.post"
    assert rec["text"] == "hello bluesky"
    assert rec["createdAt"].endswith("Z")
    assert "facets" not in rec                      # plain text → no facets
    # budget consumed once by the successful publish
    assert adapter.rate_budget()["used"] == 1


def test_publish_without_credentials_never_touches_transport(adapter, http):
    res = adapter.publish({"body": "x"})
    assert res == {"ok": False, "error": "not_connected"}
    assert http.calls == []


# ── auth: app-password createSession, blob round-trip ─────────────────────────
def test_connect_url_is_none_token_paste_mode(adapter):
    # app-password mode has no browser flow — Accounts tab posts the paste
    assert adapter.connect_url("state-1") is None


def test_handle_callback_creates_session_and_hides_tokens(adapter, http, _fake_store):
    http.route("POST https://bsky.social/xrpc/com.atproto.server.createSession",
               _ok({"did": "did:plc:xyz", "handle": "friday.bsky.social",
                    "accessJwt": "fresh-access",     # pragma: allowlist secret
                    "refreshJwt": "fresh-refresh"})) # pragma: allowlist secret
    st = adapter.handle_callback({"identifier": "friday.bsky.social",
                                  "app_password": "abcd-efgh-ijkl-mnop"})  # pragma: allowlist secret
    assert st["connected"] is True
    assert st["account"] == "friday.bsky.social"
    # nothing tokenish leaves through status()
    assert "fresh-access" not in json.dumps(st)
    assert "fresh-refresh" not in json.dumps(st)
    assert "abcd-efgh" not in json.dumps(st)
    # the blob round-trips through the credential store
    blob = adapter.load_credentials()
    assert blob["access_jwt"] == "fresh-access"
    assert blob["refresh_jwt"] == "fresh-refresh"
    assert blob["did"] == "did:plc:xyz"
    assert blob["handle"] == "friday.bsky.social"
    assert blob["expires_at"]
    # the app password is kept (provider key) for silent session re-login
    assert _fake_store["provider_keys"]["platform_bluesky"] == "abcd-efgh-ijkl-mnop"
    # createSession carried identifier + password as JSON to the PDS
    call = http.posts("createSession")[0]
    assert call["json"] == {"identifier": "friday.bsky.social",
                            "password": "abcd-efgh-ijkl-mnop"}  # pragma: allowlist secret


def test_handle_callback_missing_identifier_no_transport(adapter, http):
    st = adapter.handle_callback({"app_password": "pw"})  # pragma: allowlist secret
    assert st["connected"] is False
    assert st["last_error"] == "missing_identifier"
    assert http.calls == []


def test_handle_callback_bad_password_stays_disconnected(adapter, http):
    http.route("createSession",
               {"status": 401, "json": {"error": "AuthenticationRequired"},
                "text": "", "headers": {}})
    st = adapter.handle_callback({"identifier": "friday.bsky.social",
                                  "app_password": "wrong"})  # pragma: allowlist secret
    assert st["connected"] is False
    assert st["last_error"] == "auth_error"


def test_stored_app_password_alone_is_not_connected(adapter, _fake_store):
    # a pasted-but-never-exchanged app password is NOT a live session
    _fake_store["provider_keys"]["platform_bluesky"] = "abcd-efgh"  # pragma: allowlist secret
    assert adapter.status()["connected"] is False


def test_refresh_rotates_session_jwts(adapter, http):
    _connect(adapter, expires_at="2020-01-01T00:00:00Z")     # expired
    http.route("POST https://bsky.social/xrpc/com.atproto.server.refreshSession",
               _ok({"accessJwt": "rotated-access",           # pragma: allowlist secret
                    "refreshJwt": "rotated-refresh",         # pragma: allowlist secret
                    "did": "did:plc:abc123", "handle": "friday.bsky.social"}))
    assert adapter.refresh() is True
    blob = adapter.load_credentials()
    assert blob["access_jwt"] == "rotated-access"
    assert blob["refresh_jwt"] == "rotated-refresh"
    assert blob["expires_at"] > "2020-01-01"
    # the refresh call used the REFRESH jwt as bearer, not the access jwt
    call = http.posts("refreshSession")[0]
    assert call["headers"]["Authorization"] == "Bearer jwt-refresh-1"


def test_refresh_dead_jwt_relogins_from_app_password(adapter, http, _fake_store):
    _fake_store["provider_keys"]["platform_bluesky"] = "abcd-efgh"  # pragma: allowlist secret
    _connect(adapter, expires_at="2020-01-01T00:00:00Z")
    http.route("refreshSession",
               {"status": 400, "json": {"error": "ExpiredToken"},
                "text": "", "headers": {}})
    http.route("createSession",
               _ok({"did": "did:plc:abc123", "handle": "friday.bsky.social",
                    "accessJwt": "relogin-access",           # pragma: allowlist secret
                    "refreshJwt": "relogin-refresh"}))       # pragma: allowlist secret
    assert adapter.refresh() is True
    assert adapter.load_credentials()["access_jwt"] == "relogin-access"
    # re-login used the stored identifier + app password
    call = http.posts("createSession")[0]
    assert call["json"]["identifier"] == "friday.bsky.social"


def test_revoke_deletes_session_and_purges(adapter, http, _fake_store):
    _fake_store["provider_keys"]["platform_bluesky"] = "abcd"  # pragma: allowlist secret
    _connect(adapter)
    http.route("deleteSession", _ok({}, 200))
    assert adapter.revoke() is True
    assert adapter.load_credentials() is None
    assert "platform_bluesky" not in _fake_store["provider_keys"]
    assert adapter.status()["connected"] is False
    # deleteSession is authenticated by the refresh JWT
    call = http.posts("deleteSession")[0]
    assert call["headers"]["Authorization"] == "Bearer jwt-refresh-1"


def test_custom_pds_is_configuration_not_hardcode(adapter, http):
    adapter.configure({"pds": "https://pds.example.org/",
                       "identifier": "me.example.org", "thread_jitter_s": 0})
    http.route("POST https://pds.example.org/xrpc/com.atproto.server.createSession",
               _ok({"did": "did:plc:me", "handle": "me.example.org",
                    "accessJwt": "a", "refreshJwt": "r"}))
    st = adapter.handle_callback({"app_password": "pw-1"})  # pragma: allowlist secret
    assert st["connected"] is True
    assert adapter.load_credentials()["pds"] == "https://pds.example.org"


# ── sharp constraint: 300 GRAPHEMES (clusters, not chars or bytes) ────────────
def test_grapheme_len_counts_clusters():
    assert bmod.grapheme_len("hello") == 5
    assert bmod.grapheme_len("") == 0
    assert bmod.grapheme_len("👍🏽") == 1            # skin-tone modifier
    assert bmod.grapheme_len("🇺🇸🇺🇸") == 2          # regional indicators pair up
    assert bmod.grapheme_len("👨‍👩‍👧") == 1   # ZWJ family
    assert bmod.grapheme_len("é") == 1        # combining accent
    assert bmod.grapheme_len("héllo wörld") == 11


def test_prepare_limit_is_graphemes_not_codepoints(adapter):
    family = "👨‍👩‍👧"                    # 1 grapheme, 5 code points
    prep = adapter.prepare({"adapted_body": family * 300, "format": "post"}, {})
    assert prep["ok"] is True, prep                 # 300 graphemes fits exactly
    prep2 = adapter.prepare({"adapted_body": family * 301, "format": "post"}, {})
    assert prep2["ok"] is False
    assert "graphemes" in prep2["error"]


def test_over_limit_thread_segment_rejected(adapter):
    prep = adapter.prepare({"segments": ["fine", "y" * 301],
                            "format": "thread"}, {})
    assert prep["ok"] is False
    assert "segment" in prep["error"]


# ── sharp constraint: UTF-8 BYTE-offset richtext facets ───────────────────────
def test_detect_facets_emoji_adjacent_link_byte_offsets():
    text = "🔥🔥 hot take: https://example.com/post wow"
    facets = bmod.detect_facets(text)
    links = [f for f in facets
             if f["features"][0]["$type"] == bmod.FACET_LINK]
    assert len(links) == 1
    idx = links[0]["index"]
    prefix = "🔥🔥 hot take: "
    assert idx["byteStart"] == len(prefix.encode("utf-8"))
    assert idx["byteEnd"] == idx["byteStart"] + len("https://example.com/post")
    # byte offsets ≠ char offsets when emoji precede the link — the whole point
    assert idx["byteStart"] != text.index("https")
    # gold check: slicing the UTF-8 bytes yields exactly the URL
    raw = text.encode("utf-8")
    assert raw[idx["byteStart"]:idx["byteEnd"]].decode("utf-8") == \
        "https://example.com/post"
    assert links[0]["features"][0]["uri"] == "https://example.com/post"


def test_detect_facets_mentions_tags_and_url_trim():
    text = "cc @alice.bsky.social see https://ex.com/a. #FridayAI 🎉"
    raw = text.encode("utf-8")
    facets = bmod.detect_facets(text)
    by_type = {f["features"][0]["$type"]: f for f in facets}

    m = by_type[bmod.FACET_MENTION]
    assert m["features"][0]["handle"] == "alice.bsky.social"
    assert raw[m["index"]["byteStart"]:m["index"]["byteEnd"]].decode() == \
        "@alice.bsky.social"

    l = by_type[bmod.FACET_LINK]
    # trailing punctuation trimmed off the URL facet
    assert l["features"][0]["uri"] == "https://ex.com/a"
    assert raw[l["index"]["byteStart"]:l["index"]["byteEnd"]].decode() == \
        "https://ex.com/a"

    t = by_type[bmod.FACET_TAG]
    assert t["features"][0]["tag"] == "FridayAI"
    assert raw[t["index"]["byteStart"]:t["index"]["byteEnd"]].decode() == \
        "#FridayAI"
    # facets are ordered by byteStart
    starts = [f["index"]["byteStart"] for f in facets]
    assert starts == sorted(starts)


def test_detect_facets_url_fragments_not_double_faceted():
    # the '#anchor' inside a URL must not also become a tag facet
    facets = bmod.detect_facets("read https://ex.com/docs#anchor now")
    types = [f["features"][0]["$type"] for f in facets]
    assert types == [bmod.FACET_LINK]


def test_publish_resolves_mention_dids_in_facets(adapter, http):
    _connect(adapter)
    http.route("resolveHandle", _ok({"did": "did:plc:alice"}))
    http.route("createRecord", _ok({"uri": _uri("rk1"), "cid": "c1"}))
    res = adapter.publish({"body": "hi @alice.bsky.social", "target_id": "t1"})
    assert res["ok"] is True
    rec = http.posts("createRecord")[0]["json"]["record"]
    feats = [ft for f in rec["facets"] for ft in f["features"]]
    assert {"$type": bmod.FACET_MENTION, "did": "did:plc:alice"} in feats
    # the pending {"handle": ...} shape never ships in the record
    assert all("handle" not in ft for ft in feats)


def test_unresolvable_mention_degrades_to_plain_text(adapter, http):
    _connect(adapter)
    http.route("resolveHandle",
               {"status": 400, "json": {}, "text": "", "headers": {}})
    http.route("createRecord", _ok({"uri": _uri("rk2"), "cid": "c2"}))
    res = adapter.publish({"body": "hi @ghost.bsky.social", "target_id": "t2"})
    assert res["ok"] is True                        # the post still ships
    rec = http.posts("createRecord")[0]["json"]["record"]
    assert "facets" not in rec                      # just unlinked, not failed
    assert rec["text"] == "hi @ghost.bsky.social"


# ── sharp constraint: threads — reply chains, resume, jitter ─────────────────
def test_thread_reply_chain_and_resume_after_mid_thread_failure(adapter, http):
    _connect(adapter)
    rkeys = iter(["aaa1", "aaa2", "aaa3"])
    state = {"count": 0, "fail_on": {3}}

    def create(call):
        state["count"] += 1
        if state["count"] in state["fail_on"]:
            return {"status": 500, "json": {"error": "boom"},
                    "text": "boom", "headers": {}}
        rk = next(rkeys)
        return _ok({"uri": _uri(rk), "cid": f"cid-{rk}"})

    http.route("createRecord", create)
    prepared = {"target_id": "tgt_th", "body": "",
                "segments": ["one", "two", "three"]}

    r1 = adapter.publish(prepared)
    assert r1["ok"] is False
    assert r1["error"] == "platform_http_error"
    assert r1["resume_index"] == 2
    assert r1["segments"] == [_uri("aaa1"), _uri("aaa2")]
    calls = http.posts("createRecord")
    assert "reply" not in calls[0]["json"]["record"]          # thread root
    reply2 = calls[1]["json"]["record"]["reply"]
    assert reply2["root"] == {"uri": _uri("aaa1"), "cid": "cid-aaa1"}
    assert reply2["parent"]["uri"] == _uri("aaa1")
    assert adapter.rate_budget()["used"] == 2   # only confirmed segments count

    # retry resumes from segment 3 — never reposts a confirmed segment
    state["fail_on"] = set()
    r2 = adapter.publish(prepared)
    assert r2["ok"] is True
    calls = http.posts("createRecord")
    assert len(calls) == 4                       # 3 attempts + 1 resumed segment
    last = calls[-1]["json"]["record"]
    assert last["text"] == "three"
    assert last["reply"]["root"]["uri"] == _uri("aaa1")
    assert last["reply"]["parent"]["uri"] == _uri("aaa2")
    assert r2["platform_post_id"] == _uri("aaa1")   # thread root uri
    assert r2["raw"]["segments"] == [
        {"uri": _uri("aaa1"), "cid": "cid-aaa1"},
        {"uri": _uri("aaa2"), "cid": "cid-aaa2"},
        {"uri": _uri("aaa3"), "cid": "cid-aaa3"}]
    assert r2["raw"]["resumed_from"] == 2
    assert adapter.rate_budget()["used"] == 3
    # progress cleared after full confirmation
    assert bmod._read_progress() == {}


def test_thread_jitter_between_segments(adapter, http, monkeypatch):
    _connect(adapter)
    adapter.configure({"thread_jitter_s": [0.5, 0.5]})
    sleeps = []
    monkeypatch.setattr(bmod.time, "sleep", lambda s: sleeps.append(s))
    rkeys = iter(["j1", "j2", "j3"])
    http.route("createRecord",
               lambda c: _ok({"uri": _uri(next(rkeys)), "cid": "c"}))
    res = adapter.publish({"target_id": "tgt_j",
                           "segments": ["a", "b", "c"], "body": ""})
    assert res["ok"] is True
    # a pause before segment 2 and 3, never before the root
    assert sleeps == [0.5, 0.5]


def test_thread_edited_segments_do_not_resume_stale_progress(adapter, http):
    _connect(adapter)
    rkeys = iter(["s11", "s12", "s21", "s22"])
    calls = {"n": 0}

    def create(call):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"status": 500, "json": {}, "text": "", "headers": {}}
        return _ok({"uri": _uri(next(rkeys)), "cid": "c"})

    http.route("createRecord", create)
    assert adapter.publish({"target_id": "t",
                            "segments": ["a", "b"]})["ok"] is False
    # user edits the thread → fingerprint changes → starts fresh, no resume
    res = adapter.publish({"target_id": "t", "segments": ["a EDITED", "b"]})
    assert res["ok"] is True
    tw = http.posts("createRecord")
    assert tw[2]["json"]["record"]["text"] == "a EDITED"
    assert "reply" not in tw[2]["json"]["record"]


# ── local rate budget (§4.1) ──────────────────────────────────────────────────
def test_publish_defers_when_budget_exhausted_no_attempt_burned(adapter, http):
    _connect(adapter)
    adapter.configure({"daily_post_limit": 1, "thread_jitter_s": 0})
    adapter.consume_budget(1)
    res = adapter.publish({"body": "over budget"})
    assert res["ok"] is False
    assert res["error"] == "rate_budget_exhausted"
    assert res["defer_until"]                  # publisher defers to reset_at
    assert http.posts("createRecord") == []


# ── media: uploadBlob ≤1 MB with recompress ───────────────────────────────────
def test_prepare_uploads_image_blob_with_alt_text(adapter, http, tmp_path):
    _connect(adapter)
    img = tmp_path / "art.png"
    img.write_bytes(b"\x89PNG small fake")
    blob = {"$type": "blob", "ref": {"$link": "bafkimg1"},
            "mimeType": "image/png", "size": 15}
    http.route("uploadBlob", _ok({"blob": blob}))
    http.route("createRecord", _ok({"uri": _uri("rk9"), "cid": "c9"}))
    prep = adapter.prepare(
        {"adapted_body": "look what I made", "format": "image_post",
         "id": "tgt_m",
         "adapted_assets": [{"kind": "image", "out_path": str(img),
                             "alt_text": "a cat"}]},
        {"id": "post_m"})
    assert prep["ok"], prep
    assert prep["prepared"]["embed_images"] == [{"image": blob, "alt": "a cat"}]
    up = http.posts("uploadBlob")[0]
    assert up["headers"]["Content-Type"] == "image/png"
    assert up["headers"]["Authorization"] == "Bearer jwt-access-1"
    assert up["body"] == b"\x89PNG small fake"

    # publish attaches the uploaded blob as an images embed on the root post
    res = adapter.publish(prep["prepared"])
    assert res["ok"] is True
    rec = http.posts("createRecord")[0]["json"]["record"]
    assert rec["embed"]["$type"] == "app.bsky.embed.images"
    assert rec["embed"]["images"][0]["image"] == blob
    assert rec["embed"]["images"][0]["alt"] == "a cat"


def test_oversized_image_recompressed_under_cap(adapter, http, tmp_path, monkeypatch):
    _connect(adapter)
    big = tmp_path / "big.png"
    big.write_bytes(b"P" * (bmod.IMAGE_MAX_BYTES + 1))
    monkeypatch.setattr(bmod, "_recompress_image",
                        lambda data, cap: (b"small-jpeg", "image/jpeg", (640, 480)))
    blob = {"$type": "blob", "ref": {"$link": "bafkjpg"},
            "mimeType": "image/jpeg", "size": 10}
    http.route("uploadBlob", _ok({"blob": blob}))
    prep = adapter.prepare(
        {"adapted_body": "big", "adapted_assets": [
            {"kind": "image", "out_path": str(big), "alt_text": "big art"}]}, {})
    assert prep["ok"], prep
    up = http.posts("uploadBlob")[0]
    assert up["body"] == b"small-jpeg"              # the recompressed copy ships
    assert up["headers"]["Content-Type"] == "image/jpeg"
    embed = prep["prepared"]["embed_images"][0]
    assert embed["aspectRatio"] == {"width": 640, "height": 480}


def test_oversized_image_that_cannot_recompress_fails_loudly(adapter, http,
                                                             tmp_path, monkeypatch):
    _connect(adapter)
    big = tmp_path / "big.png"
    big.write_bytes(b"P" * (bmod.IMAGE_MAX_BYTES + 1))
    monkeypatch.setattr(bmod, "_recompress_image", lambda data, cap: None)
    prep = adapter.prepare(
        {"adapted_body": "big", "adapted_assets": [
            {"kind": "image", "out_path": str(big), "alt_text": "x"}]}, {})
    assert prep["ok"] is False
    assert prep["error"] == "media_too_large"       # never silently drops media
    assert http.posts("uploadBlob") == []


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
    # mixed image + video → rejected (one embed type per post)
    mixed = adapter.prepare(
        {"adapted_body": "b",
         "adapted_assets": [{"kind": "image"}, {"kind": "video"}]}, {})
    assert mixed["ok"] is False


# ── video: feature-detected, never assumed (§4.7) ─────────────────────────────
def test_video_not_advertised_until_detected(adapter):
    caps = adapter.capabilities()
    assert caps["media"]["video"] is None           # unknown = not advertised
    adapter.configure({"video": True, "thread_jitter_s": 0})
    caps = adapter.capabilities()
    assert caps["media"]["video"]["max_s"] == 180


def test_video_feature_detect_gates_upload(adapter, http, tmp_path):
    _connect(adapter)
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake mp4")
    http.route("getUploadLimits", _ok({"canUpload": False}))
    prep = adapter.prepare(
        {"adapted_body": "v", "adapted_assets": [
            {"kind": "video", "out_path": str(vid)}]}, {})
    assert prep["ok"] is False
    assert prep["error"] == "video_not_available"   # §4.14: surface, don't fall
    assert http.posts("uploadBlob") == []


def test_video_uploads_when_feature_detected(adapter, http, tmp_path):
    _connect(adapter)
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake mp4 bytes")
    http.route("getUploadLimits", _ok({"canUpload": True}))
    blob = {"$type": "blob", "ref": {"$link": "bafkvid"},
            "mimeType": "video/mp4", "size": 14}
    http.route("uploadBlob", _ok({"blob": blob}))
    http.route("createRecord", _ok({"uri": _uri("rkv"), "cid": "cv"}))
    prep = adapter.prepare(
        {"adapted_body": "v", "format": "post", "id": "tgt_v",
         "adapted_assets": [{"kind": "video", "out_path": str(vid)}]},
        {"id": "post_v"})
    assert prep["ok"], prep
    assert prep["prepared"]["embed_video"] == {"video": blob}
    up = http.posts("uploadBlob")[0]
    assert up["headers"]["Content-Type"] == "video/mp4"
    res = adapter.publish(prep["prepared"])
    assert res["ok"] is True
    rec = http.posts("createRecord")[0]["json"]["record"]
    assert rec["embed"] == {"$type": "app.bsky.embed.video", "video": blob}
    # the probe result is cached and now honestly advertised
    assert adapter.capabilities()["media"]["video"] is not None


# ── link-card external embed ──────────────────────────────────────────────────
def test_link_card_external_embed(adapter, http):
    _connect(adapter)
    http.route("createRecord", _ok({"uri": _uri("rkl"), "cid": "cl"}))
    res = adapter.publish({"body": "check this", "target_id": "t",
                           "options": {"link_card": {
                               "uri": "https://example.com",
                               "title": "Example", "description": "d"}}})
    assert res["ok"] is True
    rec = http.posts("createRecord")[0]["json"]["record"]
    assert rec["embed"]["$type"] == "app.bsky.embed.external"
    assert rec["embed"]["external"]["uri"] == "https://example.com"
    assert rec["embed"]["external"]["title"] == "Example"


# ── analytics: §8.2 getPosts counts, missing ≠ zero ──────────────────────────
def test_fetch_metrics_unified_mapping(adapter, http):
    _connect(adapter)
    http.route("app.bsky.feed.getPosts", _ok({"posts": [{
        "uri": _uri("rk"), "likeCount": 50, "replyCount": 7,
        "repostCount": 12, "quoteCount": 3}]}))
    m = adapter.fetch_metrics(_uri("rk"))
    assert m == {"likes": 50, "comments": 7, "shares": 15}  # repost + quote
    # no impressions on Bluesky — absent, never zero
    assert "impressions" not in m


def test_fetch_metrics_missing_is_absent_not_zero(adapter, http):
    _connect(adapter)
    http.route("app.bsky.feed.getPosts",
               _ok({"posts": [{"uri": _uri("rk2"), "likeCount": 2}]}))
    m = adapter.fetch_metrics(_uri("rk2"))
    assert m == {"likes": 2}
    assert "shares" not in m and "comments" not in m


def test_fetch_metrics_invalid_or_cold_is_none(adapter, http):
    assert adapter.fetch_metrics(_uri("rk")) is None     # not connected
    _connect(adapter)
    assert adapter.fetch_metrics("1867") is None         # not an at:// uri
    assert http.calls == []


def test_fetch_account_metrics(adapter, http):
    _connect(adapter)
    http.route("app.bsky.actor.getProfile",
               _ok({"followersCount": 12, "postsCount": 3}))
    assert adapter.fetch_account_metrics() == {"followers": 12, "posts": 3}


# ── delete (native takedown) ──────────────────────────────────────────────────
def test_delete_record(adapter, http):
    _connect(adapter)
    http.route("deleteRecord", _ok({}, 200))
    assert adapter.delete(_uri("rk1")) == {"ok": True, "deleted": True}
    call = http.posts("deleteRecord")[0]
    assert call["json"] == {"repo": "did:plc:abc123",
                            "collection": "app.bsky.feed.post", "rkey": "rk1"}


def test_delete_invalid_uri(adapter, http):
    _connect(adapter)
    res = adapter.delete("not-an-at-uri")
    assert res == {"ok": False, "error": "invalid_post_id"}
    assert http.calls == []


# ── §12.5: adapter errors are fixed, content-free strings ─────────────────────
def test_adapter_errors_are_fixed_content_free_strings(adapter, http):
    _connect(adapter)
    juicy = "PII C:\\Users\\somebody secret-path leaked-body"
    http.route("createRecord",
               {"status": 403, "json": {"message": juicy},
                "text": juicy, "headers": {}})
    res = adapter.publish({"body": "hi"})
    assert res["ok"] is False and res["error"] == "forbidden"
    assert juicy not in json.dumps(res)
    assert adapter.status()["last_error"] == "forbidden"

    http.routes.clear()
    http.route("createRecord",
               {"status": 429, "json": {}, "text": "",
                "headers": {"retry-after": "30"}})
    res = adapter.publish({"body": "hi"})
    assert res["error"] == "rate_limited"
    assert res["retry_after"] == 30            # §7.5 — publisher honors it

    http.routes.clear()
    http.route("createRecord",
               {"status": 401, "json": {}, "text": "", "headers": {}})
    res = adapter.publish({"body": "hi"})
    assert res["error"] == "auth_error"


# ── capabilities honesty ──────────────────────────────────────────────────────
def test_capabilities_honest(adapter):
    caps = adapter.capabilities()
    assert caps["char_limit"] == 300                # graphemes (§4.7)
    assert caps["thread"] is True
    assert caps["native_schedule"] is False
    assert caps["native_delete"] is True
    assert caps["analytics"] == "counts"            # getPosts; no impressions
    assert caps["media"]["images"]["max"] == 4
    assert caps["media"]["images"]["max_bytes"] == 1_000_000
    assert caps["media"]["alt_text"] is True
    assert any("grapheme" in n for n in caps["notes"])


# ── registry pickup ───────────────────────────────────────────────────────────
def test_registry_resolves_bluesky():
    a = preg.get_adapter("bluesky")
    assert isinstance(a, BlueskyAdapter)
