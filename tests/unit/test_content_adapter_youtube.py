"""Unit tests for the YouTube platform adapter (spec §4.6).

ALL transport is stubbed at the module-level ``youtube._request`` seam and all
Google credential access at ``youtube._google_credentials`` — zero live
network, zero real token store. Covers: the resumable videos.insert happy
path (metadata + bytes + budget), thumbnails.set, native scheduling via
status.publishAt (>=10 min lead) + cancel, the 1,600-units-per-upload local
quota budget (6/day default, surfaced in status()), audit-state honesty,
title/description/tags validation, Shorts semantics, unified metrics mapping
(views→impressions†, watch_time_s, subscribersGained→follows_gained), and
fixed content-free error strings (§12.5).
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
from agent_friday.services.platforms import youtube as yt              # noqa: E402
from agent_friday.services.platforms.youtube import (                  # noqa: E402
    YOUTUBE_SCOPES, YT_UPLOAD_SCOPE, YouTubeAdapter)


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
    + in-memory provider keys + audit capture."""
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

    def stub(method, url, *, headers=None, data=None, timeout=120.0):
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
    monkeypatch.setattr(yt, "_request", stub)
    return stub


class FakeCreds:
    def __init__(self, token="tok-google-test"):   # pragma: allowlist secret
        self.token = token
        self.valid = True


@pytest.fixture
def gcreds(monkeypatch):
    """Stub the google_accounts credential seam."""
    holder = {"creds": FakeCreds(), "calls": []}

    def fake(account_id):
        holder["calls"].append(account_id)
        return holder["creds"]

    monkeypatch.setattr(yt, "_google_credentials", fake)
    return holder


def _connected(**extra) -> YouTubeAdapter:
    a = YouTubeAdapter()
    a.configure(dict(extra.pop("config", {})))
    blob = {"account_id": "acc1", "account": "creator@example.com",
            "scopes": list(YOUTUBE_SCOPES),
            "linked_at": "2026-07-01T00:00:00Z"}
    blob.update(extra)
    assert a.save_credentials(blob)["ok"]
    return a


def _video_asset(tmp_path, name="clip.mp4", size=64, **meta):
    p = tmp_path / name
    p.write_bytes(b"\x00\x01video-bytes" + b"v" * size)
    asset = {"kind": "video", "path": str(p)}
    asset.update(meta)
    return asset


def _image_asset(tmp_path, name="thumb.jpg", size=128):
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8jpeg" + b"t" * size)
    return {"kind": "image", "path": str(p)}


def _target(tmp_path, **over):
    t = {"id": "tgt_1", "format": "video_upload",
         "adapted_title": "Friday builds a pipeline",
         "adapted_body": "A video about the content pipeline.",
         "hashtags": ["friday", "automation"],
         "adapted_assets": [_video_asset(tmp_path)]}
    t.update(over)
    return t


def _happy_routes(http, video_id="vid123", status_body=None):
    http.route("POST", "uploadType=resumable", status=200,
               headers={"Location": "https://upload.example/session-1"})
    http.route("PUT", "session-1", status=200,
               body={"id": video_id, "status": status_body or
                     {"privacyStatus": "public", "uploadStatus": "uploaded"}})
    http.route("POST", "thumbnails/set", status=200, body={"items": []})


# ── publish happy path (resumable videos.insert) ─────────────────────────────
def test_publish_happy_path_resumable_upload(http, gcreds, tmp_path):
    a = _connected()
    _happy_routes(http)
    prep = a.prepare(_target(tmp_path), {"id": "post_1"})
    assert prep["ok"], prep
    res = a.publish(prep["prepared"])
    assert res["ok"] is True
    assert res["platform_post_id"] == "vid123"
    assert res["post_url"] == "https://www.youtube.com/watch?v=vid123"
    assert res["native_scheduled"] is False

    # two transport calls: initiation + bytes (no thumbnail supplied)
    assert len(http.calls) == 2
    init, upload = http.calls
    assert "uploadType=resumable" in init["url"] and "part=snippet,status" in init["url"]
    assert init["headers"]["Authorization"] == "Bearer tok-google-test"
    assert init["headers"]["X-Upload-Content-Type"] == "video/mp4"
    meta = json.loads(init["data"].decode("utf-8"))
    assert meta["snippet"]["title"] == "Friday builds a pipeline"
    assert meta["snippet"]["description"] == "A video about the content pipeline."
    assert meta["snippet"]["tags"] == ["friday", "automation"]
    assert meta["snippet"]["categoryId"] == "22"
    assert meta["status"]["privacyStatus"] == "public"
    assert meta["status"]["selfDeclaredMadeForKids"] is False
    assert "publishAt" not in meta["status"]
    assert init["headers"]["X-Upload-Content-Length"] == str(len(upload["data"]))

    # the PUT carried the actual file bytes to the session URL
    assert upload["url"] == "https://upload.example/session-1"
    assert upload["data"].startswith(b"\x00\x01video-bytes")

    # one upload consumed exactly one budget slot (1,600 quota units)
    assert a.rate_budget()["used"] == 1
    assert gcreds["calls"] == ["acc1"]


def test_publish_sets_thumbnail_best_effort(http, gcreds, tmp_path):
    a = _connected()
    _happy_routes(http)
    tgt = _target(tmp_path)
    tgt["adapted_assets"].append(_image_asset(tmp_path))
    prep = a.prepare(tgt, {"id": "post_1"})
    assert prep["ok"] and prep["prepared"]["thumbnail_asset"] is not None
    res = a.publish(prep["prepared"])
    assert res["ok"] is True and res["warnings"] == []
    thumb = [c for c in http.calls if "thumbnails/set" in c["url"]]
    assert len(thumb) == 1
    assert thumb[0]["url"].endswith("videoId=vid123")
    assert thumb[0]["headers"]["Content-Type"] == "image/jpeg"
    assert thumb[0]["data"].startswith(b"\xff\xd8jpeg")


def test_thumbnail_failure_never_fails_the_publish(http, gcreds, tmp_path):
    a = _connected()
    http.route("POST", "uploadType=resumable", status=200,
               headers={"Location": "https://upload.example/session-1"})
    http.route("PUT", "session-1", status=200, body={"id": "vid123"})
    http.route("POST", "thumbnails/set", status=403, body={})
    tgt = _target(tmp_path)
    tgt["adapted_assets"].append(_image_asset(tmp_path))
    res = a.publish(a.prepare(tgt, {})["prepared"])
    assert res["ok"] is True
    assert "thumbnail_set_failed" in res["warnings"]
    assert a.rate_budget()["used"] == 1


# ── native scheduling via status.publishAt (§4.6 / §6.5) ─────────────────────
def test_native_schedule_publishat_when_10min_out(http, gcreds, tmp_path):
    a = _connected()
    _happy_routes(http)
    when = pbase._now_utc() + timedelta(minutes=30)
    tgt = _target(tmp_path, options={"publish_at": when.isoformat()})
    prep = a.prepare(tgt, {"id": "post_1"})
    assert prep["ok"], prep
    st = prep["prepared"]["status"]
    assert prep["prepared"]["native_scheduled"] is True
    assert st["privacyStatus"] == "private"       # required with publishAt
    assert st["publishAt"] == when.strftime("%Y-%m-%dT%H:%M:%SZ")

    res = a.publish(prep["prepared"])
    assert res["ok"] is True
    assert res["native_scheduled"] is True
    assert res["publish_at"] == st["publishAt"]
    meta = json.loads(http.calls[0]["data"].decode("utf-8"))
    assert meta["status"]["publishAt"] == st["publishAt"]
    assert meta["status"]["privacyStatus"] == "private"


def test_no_native_schedule_under_10min(gcreds, tmp_path):
    a = _connected()
    when = pbase._now_utc() + timedelta(minutes=5)
    prep = a.prepare(_target(tmp_path, options={"publish_at": when.isoformat()}), {})
    assert prep["ok"]
    assert prep["prepared"]["native_scheduled"] is False
    assert "publishAt" not in prep["prepared"]["status"]
    assert prep["prepared"]["status"]["privacyStatus"] == "public"
    assert any("10 min" in w for w in prep["warnings"])


def test_capabilities_advertise_native_schedule():
    caps = YouTubeAdapter().capabilities()
    assert caps["native_schedule"] is True
    assert caps["native_delete"] is True
    assert caps["analytics"] == "full"
    assert caps["title_limit"] == 100
    assert caps["char_limit"] == 5000
    assert set(caps["formats"]) == {"video_upload", "short"}


def test_cancel_scheduled_pins_private(http, gcreds):
    a = _connected()
    http.route("PUT", "/videos?part=status", status=200, body={"id": "vid123"})
    res = a.cancel_scheduled("vid123")
    assert res["ok"] is True and res["native_scheduled"] is False
    assert res["privacy_status"] == "private"
    body = json.loads(http.calls[0]["data"].decode("utf-8"))
    assert body == {"id": "vid123", "status": {"privacyStatus": "private"}}


# ── the quota trap: 1,600 units/upload → 6/day local budget ──────────────────
def test_quota_budget_defers_seventh_upload(http, gcreds, tmp_path):
    a = _connected()
    assert a.rate_budget()["limit"] == 6          # 10,000 // 1,600
    a.consume_budget(6)
    res = a.publish(a.prepare(_target(tmp_path), {})["prepared"])
    assert res["ok"] is False
    assert res["error"] == "quota_budget_exhausted"
    assert res["deferred"] is True
    assert res["retry_at"]                         # publisher re-queues to reset
    assert http.calls == []                        # nothing burned an attempt
    assert a.rate_budget()["used"] == 6


def test_budget_limit_derives_from_configured_quota():
    a = YouTubeAdapter()
    a.configure({"daily_quota_units": 3200})
    assert a.rate_budget()["limit"] == 2
    a2 = YouTubeAdapter()
    a2.configure({"daily_post_limit": 40})        # explicit override wins
    assert a2.rate_budget()["limit"] == 40


def test_status_surfaces_quota_and_audit_state(gcreds):
    a = _connected()
    a.consume_budget(2)
    st = a.status()
    assert st["connected"] is True
    q = st["quota"]
    assert q["units_per_upload"] == 1600
    assert q["daily_quota_units"] == 10000
    assert q["uploads_per_day"] == 6
    assert q["uploads_used_today"] == 2
    assert q["uploads_remaining_today"] == 4
    # audit-state honesty: unknown/unaudited say so, loudly
    assert st["audit_state"] == "unknown"
    assert "private" in st["audit_note"]

    a.configure({"project_audit_state": "audited"})
    st2 = a.status()
    assert st2["audit_state"] == "audited"
    assert "audit_note" not in st2


def test_status_scope_warning_when_upload_scope_missing(gcreds):
    a = _connected(scopes=["https://www.googleapis.com/auth/youtube"])
    st = a.status()
    assert "scope_warning" in st
    assert YT_UPLOAD_SCOPE not in st["scopes"]


def test_unaudited_public_upload_warns_in_prepare(gcreds, tmp_path):
    a = _connected()
    prep = a.prepare(_target(tmp_path), {})
    assert prep["ok"]
    assert any("audited" in w for w in prep["warnings"])


# ── prepare validation (§4.6 hard limits) ────────────────────────────────────
def test_title_missing_and_too_long(tmp_path):
    a = _connected()
    bad = a.prepare(_target(tmp_path, adapted_title=""), {})
    assert bad["ok"] is False and bad["error"] == "title_missing"
    long = a.prepare(_target(tmp_path, adapted_title="x" * 101), {})
    assert long["ok"] is False and long["error"] == "title_too_long"


def test_title_angle_brackets_sanitized(tmp_path):
    a = _connected()
    prep = a.prepare(_target(tmp_path, adapted_title="Watch <this> now"), {})
    assert prep["ok"]
    assert prep["prepared"]["snippet"]["title"] == "Watch this now"
    assert any("angle brackets" in w for w in prep["warnings"])


def test_description_limit_is_bytes_not_chars(tmp_path):
    a = _connected()
    # 2,501 two-byte chars = 5,002 bytes but only 2,501 characters
    prep = a.prepare(_target(tmp_path, adapted_body="é" * 2501), {})
    assert prep["ok"] is False and prep["error"] == "description_too_long"
    ok = a.prepare(_target(tmp_path, adapted_body="é" * 2500), {})
    assert ok["ok"] is True


def test_tags_truncate_at_500_total_chars(tmp_path):
    a = _connected()
    tags = [f"tag{i:03d}xxxx" for i in range(80)]   # 10 chars each
    prep = a.prepare(_target(tmp_path, hashtags=tags), {})
    assert prep["ok"]
    kept = prep["prepared"]["snippet"]["tags"]
    assert sum(len(t) for t in kept) <= 500
    assert len(kept) < len(tags)
    assert any("truncated" in w for w in prep["warnings"])


def test_video_asset_required(tmp_path):
    a = _connected()
    res = a.prepare(_target(tmp_path, adapted_assets=[]), {})
    assert res["ok"] is False and res["error"] == "video_asset_missing"


def test_invalid_privacy_status_rejected(tmp_path):
    a = _connected()
    res = a.prepare(_target(tmp_path, options={"privacy_status": "secret"}), {})
    assert res["ok"] is False and res["error"] == "invalid_privacy_status"


def test_oversized_thumbnail_dropped_with_warning(tmp_path):
    a = _connected()
    big = tmp_path / "big.png"
    big.write_bytes(b"p" * (2 * 1024 * 1024 + 1))
    tgt = _target(tmp_path)
    tgt["adapted_assets"].append({"kind": "image", "path": str(big)})
    prep = a.prepare(tgt, {})
    assert prep["ok"]
    assert prep["prepared"]["thumbnail_asset"] is None
    assert any("2 MB" in w for w in prep["warnings"])


# ── Shorts: ordinary vertical upload carrying #shorts ────────────────────────
def test_short_appends_shorts_tag(tmp_path):
    a = _connected()
    tgt = _target(tmp_path, format="short",
                  adapted_assets=[_video_asset(tmp_path, duration_s=45,
                                               width=1080, height=1920)])
    prep = a.prepare(tgt, {})
    assert prep["ok"]
    assert "#shorts" in prep["prepared"]["snippet"]["description"].lower()


def test_short_over_3min_rejected(tmp_path):
    a = _connected()
    tgt = _target(tmp_path, format="short",
                  adapted_assets=[_video_asset(tmp_path, duration_s=200)])
    res = a.prepare(tgt, {})
    assert res["ok"] is False and res["error"] == "short_too_long"


def test_short_horizontal_warns(tmp_path):
    a = _connected()
    tgt = _target(tmp_path, format="short",
                  adapted_assets=[_video_asset(tmp_path, duration_s=45,
                                               width=1920, height=1080)])
    prep = a.prepare(tgt, {})
    assert prep["ok"]
    assert any("vertical" in w for w in prep["warnings"])


# ── auth + fixed error strings (§12.5) ───────────────────────────────────────
def test_publish_requires_connection(http, tmp_path):
    a = YouTubeAdapter()
    a.configure({})
    res = a.publish({"platform": "youtube", "body": "b"})
    assert res == {"ok": False, "error": "not_connected"}
    assert http.calls == []


def test_publish_auth_expired_when_google_cannot_mint(http, monkeypatch, tmp_path):
    a = _connected()
    monkeypatch.setattr(yt, "_google_credentials", lambda aid: None)
    res = a.publish(a.prepare(_target(tmp_path), {})["prepared"])
    assert res["ok"] is False and res["error"] == "auth_expired"
    assert http.calls == []
    assert a.refresh() is False


@pytest.mark.parametrize("status,expected", [
    (401, "auth_error"), (403, "permission_denied"),
    (429, "rate_limited"), (500, "server_error"), (0, "network_error")])
def test_upload_errors_are_fixed_strings(http, gcreds, tmp_path, status, expected):
    a = _connected()
    http.route("POST", "uploadType=resumable", status=status, body={})
    res = a.publish(a.prepare(_target(tmp_path), {})["prepared"])
    assert res["ok"] is False and res["error"] == expected
    assert a.rate_budget()["used"] == 0            # failures never burn budget


def test_refresh_true_with_live_google_credential(gcreds):
    a = _connected()
    assert a.refresh() is True
    assert gcreds["calls"] == ["acc1"]


# ── chunked resumable upload — §4.6 for real (stream, 308 resume, no RAM) ────

_SESSION = "https://upload.example/session-1"


def _chunk_target(tmp_path, size=80):
    p = tmp_path / "big.mp4"
    p.write_bytes(bytes(range(256)) * (size // 256) + bytes(range(size % 256)))
    return {"id": "tgt_1", "format": "video_upload",
            "adapted_title": "Friday builds a pipeline",
            "adapted_body": "A video about the content pipeline.",
            "adapted_assets": [{"kind": "video", "path": str(p)}]}


def _resumable_server(monkeypatch, size, *, drop=None, probe_answers=None,
                      video_id="vidC"):
    """A stateful fake resumable session: accumulates committed bytes, answers
    308+Range until complete, then 200 with the video resource. ``drop`` is a
    set of PUT ordinals (1-based, data-carrying only) that die with status 0;
    ``probe_answers`` is a list of responses for ``bytes */total`` queries
    (default: honest 308 with the committed range)."""
    calls = []
    state = {"got": 0, "puts": 0, "probes": 0}
    probe_answers = list(probe_answers or [])

    def stub(method, url, *, headers=None, data=None, timeout=120.0):
        h = dict(headers or {})
        calls.append({"method": method, "url": url, "headers": h, "data": data})
        if "uploadType=resumable" in url:
            return {"status": 200, "headers": {"Location": _SESSION}, "body": b""}
        if "thumbnails/set" in url:
            return {"status": 200, "headers": {}, "body": b"{}"}
        assert url == _SESSION
        cr = str(h.get("Content-Range") or "")
        if cr.startswith("bytes */"):                       # status query
            state["probes"] += 1
            if probe_answers:
                return dict(probe_answers.pop(0))
            if state["got"] >= size:
                return {"status": 200, "headers": {},
                        "body": json.dumps({"id": video_id}).encode("utf-8")}
            if state["got"] == 0:
                return {"status": 308, "headers": {}, "body": b""}
            return {"status": 308,
                    "headers": {"Range": f"bytes=0-{state['got'] - 1}"},
                    "body": b""}
        state["puts"] += 1
        first, last = (int(x) for x in cr.split()[1].split("/")[0].split("-"))
        assert first == state["got"], "must resume exactly at the committed byte"
        assert len(data) == last - first + 1
        if drop and state["puts"] in drop:
            return {"status": 0, "headers": {}, "body": b""}  # bytes vanish
        state["got"] = last + 1
        if state["got"] >= size:
            return {"status": 200, "headers": {},
                    "body": json.dumps({"id": video_id}).encode("utf-8")}
        return {"status": 308,
                "headers": {"Range": f"bytes=0-{state['got'] - 1}"}, "body": b""}

    stub.calls, stub.state = calls, state
    monkeypatch.setattr(yt, "_request", stub)
    return stub


def test_upload_streams_in_bounded_chunks(monkeypatch, gcreds, tmp_path):
    a = _connected()
    monkeypatch.setattr(yt, "UPLOAD_CHUNK_BYTES", 32)
    server = _resumable_server(monkeypatch, 80)
    res = a.publish(a.prepare(_chunk_target(tmp_path, 80), {})["prepared"])
    assert res["ok"] is True and res["platform_post_id"] == "vidC"
    puts = [c for c in server.calls if c["method"] == "PUT"]
    assert len(puts) == 3                                   # 32 + 32 + 16
    assert all(len(c["data"]) <= 32 for c in puts)          # never the whole file
    init = server.calls[0]
    assert init["headers"]["X-Upload-Content-Length"] == "80"
    assert puts[0]["headers"]["Content-Range"] == "bytes 0-31/80"
    assert puts[2]["headers"]["Content-Range"] == "bytes 64-79/80"
    assert a.rate_budget()["used"] == 1


def test_dropped_chunk_resumes_from_committed_offset(monkeypatch, gcreds, tmp_path):
    a = _connected()
    monkeypatch.setattr(yt, "UPLOAD_CHUNK_BYTES", 32)
    server = _resumable_server(monkeypatch, 80, drop={2})   # 2nd chunk dies
    res = a.publish(a.prepare(_chunk_target(tmp_path, 80), {})["prepared"])
    assert res["ok"] is True                                # recovered in-flight
    assert server.state["probes"] == 1                      # asked, not restarted
    ranges = [c["headers"]["Content-Range"] for c in server.calls
              if c["method"] == "PUT" and not
              c["headers"]["Content-Range"].startswith("bytes */")]
    # byte 0 uploads exactly once: the resume continued at offset 32
    assert ranges == ["bytes 0-31/80", "bytes 32-63/80",
                      "bytes 32-63/80", "bytes 64-79/80"]


def test_probe_discovers_completed_upload(monkeypatch, gcreds, tmp_path):
    """The §7.2 window itself: the response to the final chunk is lost but the
    session says 200 — the adapter returns success, no second video."""
    a = _connected()
    server = _resumable_server(monkeypatch, 80, drop={1}, video_id="vidZ",
                               probe_answers=[{
                                   "status": 200, "headers": {},
                                   "body": json.dumps({"id": "vidZ"}).encode()}])
    res = a.publish(a.prepare(_chunk_target(tmp_path, 80), {})["prepared"])
    assert res["ok"] is True and res["platform_post_id"] == "vidZ"
    assert server.state["puts"] == 1                        # nothing re-sent


def test_dropped_final_chunk_unverifiable_is_ambiguous(monkeypatch, gcreds, tmp_path):
    """Final bytes left, session unreachable: network_error MUST carry the
    §7.2 ambiguous flag so the publisher verifies before any retry."""
    a = _connected()
    dead = {"status": 0, "headers": {}, "body": b""}
    server = _resumable_server(monkeypatch, 80, drop={1},
                               probe_answers=[dead, dead, dead])
    res = a.publish(a.prepare(_chunk_target(tmp_path, 80), {})["prepared"])
    assert res["ok"] is False and res["error"] == "network_error"
    assert res["ambiguous"] is True
    assert server.state["probes"] == 3
    assert a.rate_budget()["used"] == 0


def test_dropped_nonfinal_chunk_is_plain_network_error(monkeypatch, gcreds, tmp_path):
    """A drop before the final chunk cannot have published anything — plain
    transient, no ambiguity, the ladder may retry freely."""
    a = _connected()
    monkeypatch.setattr(yt, "UPLOAD_CHUNK_BYTES", 32)
    dead = {"status": 0, "headers": {}, "body": b""}
    _resumable_server(monkeypatch, 80, drop={1, 2, 3, 4, 5},
                      probe_answers=[dead] * 12)
    res = a.publish(a.prepare(_chunk_target(tmp_path, 80), {})["prepared"])
    assert res["ok"] is False and res["error"] == "network_error"
    assert not res.get("ambiguous")


# ── verify_recent_post — the §7.2 probe ──────────────────────────────────────

def test_verify_recent_post_finds_recent_title_match(http, gcreds):
    a = _connected()
    now = pbase._now_utc()
    recent = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    http.route("GET", "/search", status=200, body={"items": [
        {"id": {"videoId": "old1"},                  # same title, days old —
         "snippet": {"title": "My &amp; Video",      # must NOT confirm
                     "publishedAt": old}},
        {"id": {"videoId": "other"},
         "snippet": {"title": "Unrelated", "publishedAt": recent}},
        {"id": {"videoId": "new1"},                  # HTML-escaped by the API
         "snippet": {"title": "My &amp; Video", "publishedAt": recent}},
    ]})
    found = a.verify_recent_post({"snippet": {"title": "My & Video"}})
    assert found == {"platform_post_id": "new1",
                     "post_url": "https://www.youtube.com/watch?v=new1"}
    assert "forMine=true" in http.calls[0]["url"]


def test_verify_recent_post_none_on_no_match_or_error(http, gcreds):
    a = _connected()
    recent = pbase._now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    http.route("GET", "/search", status=200, body={"items": [
        {"id": {"videoId": "x"},
         "snippet": {"title": "Something else", "publishedAt": recent}}]})
    assert a.verify_recent_post({"title": "My Video"}) is None
    assert a.verify_recent_post({"title": ""}) is None      # no fingerprint
    # transport failure → None (cannot tell ≠ confirmed)
    http.calls.clear()
    import agent_friday.services.platforms.youtube as _yt
    orig = _yt._request
    try:
        _yt._request = lambda *a_, **k: {"status": 0, "headers": {}, "body": b""}
        assert a.verify_recent_post({"title": "My Video"}) is None
    finally:
        _yt._request = orig


def test_revoke_unlinks_locally_only(gcreds):
    a = _connected()
    assert a.status()["connected"] is True
    assert a.revoke() is True
    assert a.status()["connected"] is False


def test_handle_callback_links_existing_google_account(monkeypatch):
    import types
    fake_ga = types.SimpleNamespace(
        get_account=lambda aid: {"id": aid, "email": "creator@example.com",
                                 "scopes": list(YOUTUBE_SCOPES)})
    monkeypatch.setitem(sys.modules, "agent_friday.services.google_accounts", fake_ga)
    # `from agent_friday.services import google_accounts` resolves the PACKAGE
    # ATTRIBUTE first when the real module was already imported by an earlier
    # test, so patch that too — the sys.modules entry alone only covers the
    # never-imported case (which is why this passed in isolation).
    import agent_friday.services as _svc_pkg
    monkeypatch.setattr(_svc_pkg, "google_accounts", fake_ga, raising=False)
    a = YouTubeAdapter()
    a.configure({})
    st = a.handle_callback({"account_id": "acc9"})
    assert st["connected"] is True
    assert st["account"] == "creator@example.com"
    blob = a.load_credentials()
    assert blob["account_id"] == "acc9"
    assert YT_UPLOAD_SCOPE in blob["scopes"]


# ── analytics: unified metric keys (§4.6) ────────────────────────────────────
def test_fetch_metrics_maps_to_unified_keys(http, gcreds):
    a = _connected()
    http.route("GET", "/videos?part=statistics", status=200, body={
        "items": [{"id": "vid123", "statistics": {
            "viewCount": "1200", "likeCount": "90", "commentCount": "14"}}]})
    http.route("GET", "youtubeanalytics", status=200, body={
        "columnHeaders": [{"name": "views"}, {"name": "estimatedMinutesWatched"},
                          {"name": "averageViewDuration"},
                          {"name": "subscribersGained"}],
        "rows": [[1200, 340, 17, 25]]})
    m = a.fetch_metrics("vid123")
    assert m is not None
    assert m["views"] == 1200
    assert m["impressions"] == 1200                # † views proxy
    assert m["impressions_are_views"] is True      # honesty flag
    assert m["likes"] == 90 and m["comments"] == 14
    assert m["watch_time_s"] == 340 * 60
    assert m["avg_view_duration_s"] == 17
    assert m["follows_gained"] == 25
    assert m["source"] == "youtube_data+analytics"
    assert m["fetched_at"].endswith("Z")


def test_fetch_metrics_survives_analytics_api_failure(http, gcreds):
    a = _connected()
    http.route("GET", "/videos?part=statistics", status=200, body={
        "items": [{"statistics": {"viewCount": "7"}}]})
    http.route("GET", "youtubeanalytics", status=403, body={})
    m = a.fetch_metrics("vid123")
    assert m is not None and m["views"] == 7 and m["impressions"] == 7
    assert m["source"] == "youtube_data"
    assert "watch_time_s" not in m


def test_fetch_metrics_none_when_disconnected_or_unknown(http, gcreds):
    a = YouTubeAdapter()
    a.configure({})
    assert a.fetch_metrics("vid123") is None       # not connected, no transport
    assert http.calls == []
    b = _connected()
    http.route("GET", "/videos?part=statistics", status=200, body={"items": []})
    assert b.fetch_metrics("vid-unknown") is None


def test_fetch_account_metrics_channel_counts(http, gcreds):
    a = _connected()
    http.route("GET", "/channels?part=statistics", status=200, body={
        "items": [{"statistics": {"subscriberCount": "5400",
                                  "viewCount": "999999", "videoCount": "42"}}]})
    m = a.fetch_account_metrics()
    assert m["followers"] == 5400
    assert m["views"] == 999999
    assert m["posts"] == 42


# ── delete ────────────────────────────────────────────────────────────────────
def test_delete_video(http, gcreds):
    a = _connected()
    http.route("DELETE", "/videos?id=vid123", status=204)
    res = a.delete("vid123")
    assert res == {"ok": True, "deleted": True}
    assert http.calls[0]["headers"]["Authorization"] == "Bearer tok-google-test"


def test_delete_error_is_fixed_string(http, gcreds):
    a = _connected()
    http.route("DELETE", "/videos?id=vid123", status=404, body={})
    res = a.delete("vid123")
    assert res["ok"] is False and res["error"] == "delete_failed"


# ── registry pickup ───────────────────────────────────────────────────────────
def test_registry_resolves_youtube():
    a = preg.get_adapter("youtube")
    assert a is not None, preg.import_error("youtube")
    assert isinstance(a, YouTubeAdapter)
