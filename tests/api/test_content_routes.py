"""API tests — Content Pipeline routes (docs/CONTENT_PIPELINE_SPEC.md §11).

Covers every route in the §11 table against an isolated content store
(DB + publish log + voice cards monkeypatched to tmp), the mock platform
adapter, and the autouse LLM stubs from the api conftest. Envelope errors
ride HTTP 200 (creations-route convention) — asserted throughout.

The end-to-end egress-hold test (gate divergence → HELD, adapter mock never
called) exercises services/publisher.py and auto-skips until that module
lands; the HELD/release rails themselves are covered store-side below.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from agent_friday.services import content_pipeline as cp
from agent_friday.services import content_composer as cc
from agent_friday.services import platforms as pr
import agent_friday.routes.content_pipeline as content_routes


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future(hours=3):
    return _iso(datetime.now(timezone.utc) + timedelta(hours=hours))


@pytest.fixture(autouse=True)
def _content_env(tmp_path, monkeypatch):
    """Isolated content store + platform registry per test."""
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cp, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(cc, "VOICE_CARDS_DIR", tmp_path / "voice_cards")
    monkeypatch.setattr(pr, "CONFIG_PATH", tmp_path / "platforms.json")
    pr._reset_for_tests()
    yield
    pr._reset_for_tests()


def _mk_post(client, body="Sunrise over the lake — new piece finished today.",
             platforms=("linkedin",), **extra):
    payload = {"title": "Test post", "body": body,
               "platforms": list(platforms)}
    payload.update(extra)
    res = client.post("/api/content/posts", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"], data
    return data["post"]


def _confirm_target(post_id, platform_post_id="ext-1"):
    """Drive one target through claim → SENT → CONFIRMED store-side."""
    claimed = cp.claim_due_targets()
    assert claimed["ok"]
    tid = None
    for t in claimed["targets"]:
        if t["post_id"] == post_id:
            tid = t["id"]
    assert tid, f"no claimed target for {post_id}: {claimed}"
    assert cp.set_target_status(tid, "SENT")["ok"]
    res = cp.set_target_status(tid, "CONFIRMED",
                               post_url=f"mock://post/{platform_post_id}",
                               platform_post_id=platform_post_id)
    assert res["ok"], res
    return tid


# ═════════════════════════════════════════════════════════════════════════════
#  Posts CRUD
# ═════════════════════════════════════════════════════════════════════════════

def test_create_list_get_patch_delete_post(client):
    post = _mk_post(client)
    pid = post["id"]
    assert post["status"] == "DRAFT"
    assert post["targets"] and post["targets"][0]["platform"] == "linkedin"

    res = client.get("/api/content/posts?status=DRAFT&platform=linkedin")
    body = res.get_json()
    assert body["ok"] and any(p["id"] == pid for p in body["posts"])

    res = client.get(f"/api/content/posts/{pid}")
    assert res.get_json()["post"]["title"] == "Test post"

    res = client.patch(f"/api/content/posts/{pid}",
                       json={"title": "Renamed", "tags": ["art"]})
    body = res.get_json()
    assert body["ok"] and body["post"]["title"] == "Renamed"
    assert body["post"]["tags"] == ["art"]

    res = client.delete(f"/api/content/posts/{pid}")
    body = res.get_json()
    assert body["ok"] and body["post"]["status"] == "CANCELLED"


def test_create_post_coerces_assets_and_schedule(client):
    post = _mk_post(
        client,
        assets=[{"filename": "friday-image-1.png", "kind": "image",
                 "alt_text": "a sunrise", "content_hash": "sha256:abc"}],
        schedule={"publish_at": _future(), "timezone": "America/Chicago"})
    assert post["assets"][0]["alt_text"] == "a sunrise"
    assert post["schedule"]["timezone"] == "America/Chicago"
    # targets inherit the schedule instant
    assert post["targets"][0]["publish_at"] == post["schedule"]["publish_at"]


def test_errors_ride_http_200(client):
    res = client.get("/api/content/posts/post_nope")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is False and "not found" in body["error"]


# ═════════════════════════════════════════════════════════════════════════════
#  Compose / preview
# ═════════════════════════════════════════════════════════════════════════════

def test_compose_adapts_targets(client):
    post = _mk_post(client, platforms=["linkedin", "twitter"])
    res = client.post(f"/api/content/posts/{post['id']}/compose", json={})
    body = res.get_json()
    assert body["ok"], body
    plats = {t["platform"] for t in body["targets"]}
    assert plats == {"linkedin", "twitter"}
    for t in body["targets"]:
        assert (t.get("adapted_body") or "").strip()
    # composed payloads persisted onto the stored post
    stored = body["post"]
    assert any((t.get("adapted_body") or "").strip() for t in stored["targets"])


def test_preview_stateless(client):
    res = client.post("/api/content/preview",
                      json={"body": "One sharp idea, shipped.",
                            "platform": "twitter", "format": "post"})
    body = res.get_json()
    assert body["ok"], body
    assert body["char_limit"] == 280
    assert body["adapted_body"].strip()
    assert isinstance(body["char_used"], int)
    # unknown platform → in-body error, HTTP 200
    res = client.post("/api/content/preview",
                      json={"body": "x", "platform": "myspace"})
    assert res.status_code == 200 and res.get_json()["ok"] is False


# ═════════════════════════════════════════════════════════════════════════════
#  Schedule / conflicts / optimal time
# ═════════════════════════════════════════════════════════════════════════════

def test_schedule_sets_publish_at(client):
    post = _mk_post(client)
    when = _future()
    res = client.post(f"/api/content/posts/{post['id']}/schedule",
                      json={"publish_at": when})
    body = res.get_json()
    assert body["ok"], body
    assert body["post"]["status"] == "SCHEDULED"
    assert body["post"]["schedule"]["publish_at"] == when
    assert body["post"]["targets"][0]["publish_at"] == when
    assert body["conflicts"] == []


def test_schedule_requires_time_or_optimal(client):
    post = _mk_post(client)
    res = client.post(f"/api/content/posts/{post['id']}/schedule", json={})
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is False and "publish_at" in body["error"]


def test_schedule_optimal_time_resolves(client):
    post = _mk_post(client)
    res = client.post(f"/api/content/posts/{post['id']}/schedule",
                      json={"optimal_time": True, "timezone": "America/Chicago"})
    body = res.get_json()
    assert body["ok"], body
    assert body["resolved_optimal"] is True
    assert body["post"]["schedule"]["publish_at"]
    assert body["post"]["status"] == "SCHEDULED"


def test_schedule_conflict_detection(client):
    when = datetime.now(timezone.utc) + timedelta(hours=6)
    a = _mk_post(client)
    client.post(f"/api/content/posts/{a['id']}/schedule",
                json={"publish_at": _iso(when)})
    b = _mk_post(client)
    res = client.post(f"/api/content/posts/{b['id']}/schedule",
                      json={"publish_at": _iso(when + timedelta(minutes=30))})
    body = res.get_json()
    assert body["ok"]
    assert body["conflicts"], "same-platform post 30 min away must warn (§6.8)"
    assert body["conflicts"][0]["platform"] == "linkedin"
    assert body["conflicts"][0]["post_id"] == a["id"]


# ═════════════════════════════════════════════════════════════════════════════
#  Publish-now / cancel / HELD release
# ═════════════════════════════════════════════════════════════════════════════

def test_publish_now_arms_targets(client):
    post = _mk_post(client)
    res = client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    body = res.get_json()
    assert body["ok"], body
    assert body["post"]["status"] == "PUBLISHING"
    assert body["post"]["targets"][0]["publish_at"]
    assert "dispatch" in body            # best-effort publisher kick recorded


def test_cancel_single_target_then_post(client):
    post = _mk_post(client, platforms=["linkedin", "twitter"])
    tid = post["targets"][0]["id"]
    res = client.post(f"/api/content/posts/{post['id']}/cancel",
                      json={"target_id": tid})
    body = res.get_json()
    assert body["ok"]
    skipped = [t for t in body["post"]["targets"] if t["id"] == tid][0]
    assert skipped["status"] == "SKIPPED"
    res = client.post(f"/api/content/posts/{post['id']}/cancel", json={})
    assert res.get_json()["post"]["status"] == "CANCELLED"


def _held_post(client):
    """Create a post and drive one target to HELD (gate outcome, §7.3)."""
    post = _mk_post(client)
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    claimed = cp.claim_due_targets()
    tid = [t["id"] for t in claimed["targets"] if t["post_id"] == post["id"]][0]
    res = cp.set_target_status(tid, "HELD", error="classifier: TIER_2 span")
    assert res["ok"] and res["post_status"] == "HELD"
    return post["id"], tid


def test_release_requires_ack(client):
    pid, tid = _held_post(client)
    # no ack → refused, nothing moves
    res = client.post(f"/api/content/posts/{pid}/release",
                      json={"target_id": tid})
    body = res.get_json()
    assert res.status_code == 200 and body["ok"] is False
    assert "ack" in body["error"]
    assert cp.get_target(tid)["target"]["status"] == "HELD"
    # ack must be literal true, not truthy junk
    res = client.post(f"/api/content/posts/{pid}/release",
                      json={"target_id": tid, "ack": "yes"})
    assert res.get_json()["ok"] is False


def test_release_with_ack_reopens_target(client):
    pid, tid = _held_post(client)
    res = client.post(f"/api/content/posts/{pid}/release",
                      json={"target_id": tid, "ack": True})
    body = res.get_json()
    assert body["ok"], body
    assert body["post"]["status"] == "PUBLISHING"
    assert cp.get_target(tid)["target"]["status"] == "PENDING"


# ═════════════════════════════════════════════════════════════════════════════
#  Repurpose
# ═════════════════════════════════════════════════════════════════════════════

def test_repurpose_creates_spread(client):
    res = client.post("/api/content/repurpose",
                      json={"source": {"kind": "wiki", "ref": "drafts/piece.md"},
                            "title": "Essay",
                            "body": "A long editorial about sovereign tools.",
                            "spread": ["linkedin", "twitter"]})
    body = res.get_json()
    assert body["ok"], body
    assert body["spread"] == ["linkedin", "twitter"]
    post = body["post"]
    assert post["source"]["kind"] == "repurpose"
    assert {t["platform"] for t in post["targets"]} == {"linkedin", "twitter"}
    for t in post["targets"]:
        assert (t.get("adapted_body") or "").strip()


def test_repurpose_from_existing_post_default_spread(client):
    src = _mk_post(client, body="Original body worth spinning out.")
    res = client.post("/api/content/repurpose",
                      json={"source": {"kind": "post", "ref": src["id"]}})
    body = res.get_json()
    assert body["ok"], body
    assert "linkedin" in body["spread"] and "federation" in body["spread"]


def test_repurpose_needs_content(client):
    res = client.post("/api/content/repurpose", json={"source": {"kind": "idea"}})
    assert res.status_code == 200
    assert res.get_json()["ok"] is False


# ═════════════════════════════════════════════════════════════════════════════
#  Queue / calendar / best-times
# ═════════════════════════════════════════════════════════════════════════════

def test_queue_shape(client):
    post = _mk_post(client)
    when = _future()
    client.post(f"/api/content/posts/{post['id']}/schedule",
                json={"publish_at": when})
    cp.append_publish_log({"event": "publish", "post_id": post["id"],
                           "target_id": post["targets"][0]["id"],
                           "platform": "linkedin", "ok": True})
    res = client.get("/api/content/queue")
    body = res.get_json()
    assert body["ok"]
    assert set(body) >= {"upcoming", "held", "history", "pause_all"}
    row = [r for r in body["upcoming"] if r["post_id"] == post["id"]][0]
    assert row["platform"] == "linkedin"
    assert row["publish_at"] == when
    assert row["status"] == "PENDING"
    assert body["history"] and body["history"][0]["event"] == "publish"
    assert body["pause_all"] is False


def test_queue_pins_held(client):
    pid, tid = _held_post(client)
    body = client.get("/api/content/queue").get_json()
    held_ids = [r["target_id"] for r in body["held"]]
    assert tid in held_ids


def test_calendar_shape(client):
    post = _mk_post(client)
    when = datetime.now(timezone.utc) + timedelta(days=2)
    client.post(f"/api/content/posts/{post['id']}/schedule",
                json={"publish_at": _iso(when)})
    frm = _iso(datetime.now(timezone.utc))
    to = _iso(datetime.now(timezone.utc) + timedelta(days=7))
    res = client.get(f"/api/content/calendar?from={frm}&to={to}")
    body = res.get_json()
    assert body["ok"]
    pill = [p for p in body["pills"] if p["post_id"] == post["id"]][0]
    assert pill["platform"] == "linkedin" and pill["recurrence"] == "none"
    assert isinstance(body["suggestions"], list)
    for s in body["suggestions"]:
        assert s["label"] in ("learned", "general")
        assert frm <= s["at"] <= to


def test_best_times_seed_and_learned(client):
    assert cp.upsert_best_time("linkedin", 1, 9, 0.42, 8)["ok"]
    res = client.get("/api/content/best-times?platform=linkedin")
    body = res.get_json()
    assert body["ok"]
    assert body["seed"]["linkedin"], "seed table must ship day one (§6.4)"
    assert all(set(c) == {"weekday", "hour"} for c in body["seed"]["linkedin"])
    learned = body["learned"]
    assert learned and learned[0]["weekday"] == 1 and learned[0]["samples"] == 8


# ═════════════════════════════════════════════════════════════════════════════
#  Analytics
# ═════════════════════════════════════════════════════════════════════════════

def test_analytics_summary_and_drilldown(client):
    post = _mk_post(client)
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    tid = _confirm_target(post["id"])
    assert cp.insert_engagement_snapshot(
        tid, {"likes": 10, "impressions": 100, "comments": 2})["ok"]

    res = client.get("/api/content/analytics/summary?days=30")
    body = res.get_json()
    assert body["ok"] and body["posts_published"] >= 1
    assert body["totals"]["likes"] >= 10
    assert body["best_post"] is not None
    assert 0 < body["engagement_rate"] <= 1

    res = client.get(f"/api/content/analytics/post/{post['id']}")
    body = res.get_json()
    assert body["ok"] and len(body["snapshots"]) == 1
    assert body["analytics"]["likes"] == 10


def test_analytics_refresh_polls_adapter(client):
    adapter = pr.get_adapter("mock")
    adapter.reset()
    adapter.published.append({"platform_post_id": "mock-77"})
    adapter.set_metrics("mock-77", {"likes": 7, "impressions": 120})

    post = _mk_post(client, platforms=["mock"])
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    tid = _confirm_target(post["id"], platform_post_id="mock-77")

    res = client.post(f"/api/content/analytics/refresh/{tid}")
    body = res.get_json()
    assert body["ok"], body
    assert body["metrics"]["likes"] == 7
    snaps = cp.get_snapshots(target_id=tid)["snapshots"]
    assert len(snaps) == 1 and snaps[0]["metrics"]["likes"] == 7


def test_analytics_refresh_needs_confirmed_target(client):
    post = _mk_post(client, platforms=["mock"])
    tid = post["targets"][0]["id"]
    res = client.post(f"/api/content/analytics/refresh/{tid}")
    assert res.status_code == 200
    assert res.get_json()["ok"] is False


def test_insights_route_never_500s(client):
    res = client.get("/api/content/insights")
    assert res.status_code == 200
    body = res.get_json()
    assert "ok" in body
    if body["ok"]:
        assert isinstance(body.get("insights"), list)


# ═════════════════════════════════════════════════════════════════════════════
#  Platform accounts
# ═════════════════════════════════════════════════════════════════════════════

def test_platforms_status_lists_declared_adapters(client):
    res = client.get("/api/content/platforms")
    body = res.get_json()
    assert body["ok"]
    assert "mock" in body["platforms"]
    entry = body["platforms"]["mock"]
    assert entry["available"] is True and entry["platform"] == "mock"
    # every declared module reports, available or not
    assert set(pr.ADAPTER_MODULES) <= set(body["platforms"])
    assert "pause_all" in body


def test_platform_connect_tokenless(client):
    res = client.post("/api/content/platforms/mock/connect", json={})
    body = res.get_json()
    assert body["ok"], body
    assert body["mode"] == "token" and body["needs"] == "secret"
    # nothing token-shaped is echoed back
    assert "connect_url" not in body


def test_platform_connect_with_pasted_secret(client):
    res = client.post("/api/content/platforms/mock/connect",
                      json={"secret": "paste-me-123",
                            "options": {"latency_s": 0}})
    body = res.get_json()
    assert body["ok"], body
    assert body["mode"] == "token"
    assert body["status"]["connected"] is True
    assert "paste-me-123" not in res.get_data(as_text=True)


def test_platform_connect_unknown(client):
    res = client.post("/api/content/platforms/geocities/connect", json={})
    assert res.status_code == 200
    assert res.get_json()["ok"] is False


def test_platform_callback_state_binding(client):
    # bad/absent state → CSRF backstop, adapter never sees the params
    res = client.get("/api/content/platforms/mock/callback?state=bogus")
    assert res.status_code == 302 and "bad_state" in res.headers["Location"]

    content_routes._OAUTH_STATES["s-good"] = {"platform": "mock",
                                              "ts": time.time()}
    res = client.get("/api/content/platforms/mock/callback?state=s-good")
    assert res.status_code == 302
    loc = res.headers["Location"]
    assert "workspace=content" in loc and "tab=accounts" in loc
    assert "connected=mock" in loc
    # single-use: replaying the same state fails
    res = client.get("/api/content/platforms/mock/callback?state=s-good")
    assert "bad_state" in res.headers["Location"]


def test_platform_disconnect_revokes_and_purges(client):
    adapter = pr.get_adapter("mock")
    adapter.reset()
    adapter.published.append({"platform_post_id": "mock-9"})
    post = _mk_post(client, platforms=["mock"])
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    tid = _confirm_target(post["id"], platform_post_id="mock-9")
    cp.insert_engagement_snapshot(tid, {"likes": 1})
    cp.upsert_best_time("mock", 2, 10, 0.3, 6)

    res = client.delete("/api/content/platforms/mock?purge_analytics=1")
    body = res.get_json()
    assert body["ok"] and body["revoked"] is True
    assert body["purged_analytics"] is True
    assert cp.get_snapshots(target_id=tid)["snapshots"] == []
    assert cp.get_best_times(platform="mock")["best_times"] == []
    assert body["status"]["connected"] is False


def test_platform_test_route(client):
    res = client.post("/api/content/platforms/mock/test", json={})
    body = res.get_json()
    assert res.status_code == 200
    assert body["connected"] is True and body["ok"] is True


def test_platform_connect_pasted_secret_probe_failure_not_ok(client, monkeypatch):
    """A pasted token is verified LIVE at connect time (§4.1) — when the
    probe fails, connect must not claim success just because the secret
    landed in the credential store."""
    adapter = pr.get_adapter("mock")
    adapter.reset()
    monkeypatch.setattr(adapter, "fetch_account_metrics", lambda: None)
    res = client.post("/api/content/platforms/mock/connect",
                      json={"secret": "bad-token-xyz"})
    body = res.get_json()
    assert body["ok"] is False
    assert body["verified"] is False
    assert body.get("error")
    assert "bad-token-xyz" not in res.get_data(as_text=True)


def test_platform_connect_pasted_secret_probe_success_verified(client):
    res = client.post("/api/content/platforms/mock/connect",
                      json={"secret": "good-token"})
    body = res.get_json()
    assert body["ok"] is True and body["verified"] is True
    assert body["status"]["connected"] is True


def test_platform_connect_app_password_creates_session(client, monkeypatch):
    """App-password (Bluesky-style) connect must createSession NOW, with the
    top-level identity fields (identifier) folded into the options the
    adapter's callback receives."""
    adapter = pr.get_adapter("mock")
    adapter.reset()
    monkeypatch.setattr(adapter, "auth_mode", "app_password", raising=False)
    seen = {}
    orig_cb = adapter.handle_callback

    def _cb(params):
        seen["params"] = dict(params or {})
        return orig_cb(params)

    monkeypatch.setattr(adapter, "handle_callback", _cb)
    res = client.post("/api/content/platforms/mock/connect",
                      json={"identifier": "me.bsky.social",
                            "secret": "app-pass-1"})
    body = res.get_json()
    assert body["ok"] is True and body["verified"] is True
    assert seen["params"].get("identifier") == "me.bsky.social"
    assert body["status"]["connected"] is True
    assert "app-pass-1" not in res.get_data(as_text=True)


def test_platform_connect_app_password_session_failure_not_ok(client, monkeypatch):
    adapter = pr.get_adapter("mock")
    adapter.reset()
    monkeypatch.setattr(adapter, "auth_mode", "app_password", raising=False)
    monkeypatch.setattr(adapter, "handle_callback",
                        lambda params: {"connected": False})
    res = client.post("/api/content/platforms/mock/connect",
                      json={"identifier": "me.bsky.social",
                            "secret": "wrong-pass"})
    body = res.get_json()
    assert body["ok"] is False and body["verified"] is False


# ═════════════════════════════════════════════════════════════════════════════
#  Delete + takedown ordering (§12.6 erase)
# ═════════════════════════════════════════════════════════════════════════════

def test_delete_published_post_with_takedown(client):
    """§12.6 erase on a PUBLISHED post: local delete succeeds (CANCELLED) and
    the confirmed platform copy is taken down."""
    adapter = pr.get_adapter("mock")
    adapter.reset()
    adapter.published.append({"platform_post_id": "mock-take-1"})
    post = _mk_post(client, platforms=["mock"])
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    _confirm_target(post["id"], platform_post_id="mock-take-1")

    res = client.delete(f"/api/content/posts/{post['id']}?takedown=1")
    body = res.get_json()
    assert body["ok"], body
    assert body["post"]["status"] == "CANCELLED"
    tds = body.get("takedowns") or []
    assert len(tds) == 1 and tds[0]["ok"] is True
    assert "mock-take-1" in adapter.deleted


def test_delete_refusal_fires_no_takedowns(client, monkeypatch):
    """Ordering guard: when the store refuses the delete (PUBLISHING post),
    no irreversible platform-side takedown may fire."""
    adapter = pr.get_adapter("mock")
    adapter.reset()
    deletes = []
    monkeypatch.setattr(adapter, "delete",
                        lambda pid: deletes.append(pid) or {"ok": True})
    post = _mk_post(client, platforms=["mock", "linkedin"])
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    claimed = cp.claim_due_targets()
    tid = next(t["id"] for t in claimed["targets"]
               if t["post_id"] == post["id"] and t["platform"] == "mock")
    assert cp.set_target_status(tid, "SENT")["ok"]
    assert cp.set_target_status(tid, "CONFIRMED", post_url="mock://post/t2",
                                platform_post_id="mock-take-2")["ok"]
    got = client.get(f"/api/content/posts/{post['id']}").get_json()
    assert got["post"]["status"] == "PUBLISHING"  # linkedin target in flight

    res = client.delete(f"/api/content/posts/{post['id']}?takedown=1")
    body = res.get_json()
    assert body["ok"] is False
    assert "takedowns" not in body
    assert deletes == []


# ═════════════════════════════════════════════════════════════════════════════
#  Voice cards / export
# ═════════════════════════════════════════════════════════════════════════════

def test_voice_cards_roundtrip(client):
    res = client.get("/api/content/voice-cards/linkedin")
    body = res.get_json()
    assert body["ok"] and body["text"].strip()
    res = client.post("/api/content/voice-cards/linkedin",
                      json={"text": "Hook first. Two paragraphs. One question."})
    assert res.get_json()["ok"]
    body = client.get("/api/content/voice-cards/linkedin").get_json()
    assert body["text"].startswith("Hook first.")
    # unknown platform → in-body error
    assert client.get("/api/content/voice-cards/geocities").get_json()["ok"] is False


def test_export_bundle_shape(client):
    post = _mk_post(client)
    client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    tid = _confirm_target(post["id"])
    cp.insert_engagement_snapshot(tid, {"likes": 3})
    cp.upsert_best_time("linkedin", 1, 9, 0.5, 6)
    cp.append_publish_log({"event": "publish", "target_id": tid,
                           "post_id": post["id"], "ok": True})
    res = client.get("/api/content/export")
    body = res.get_json()
    assert body["ok"]
    assert set(body) >= {"exported_at", "posts", "snapshots", "best_times",
                         "psi_awards", "publish_log"}
    assert any(p["id"] == post["id"] for p in body["posts"])
    exported = [p for p in body["posts"] if p["id"] == post["id"]][0]
    assert exported["targets"], "targets ride inside their posts (§12.6)"
    assert body["snapshots"] and body["best_times"] and body["publish_log"]


# ═════════════════════════════════════════════════════════════════════════════
#  Flow destination (§10.2) + settings defaults
# ═════════════════════════════════════════════════════════════════════════════

def test_flow_destination_creates_draft(client):
    res = client.post("/api/flow", json={
        "data_type": "contact_research",
        "content": "Notes worth sharing with the world.",
        "metadata": {"person_name": "Ada"},
        "destinations": ["content_pipeline"],
    })
    assert res.status_code == 200
    body = res.get_json()
    result = body["results"][0]
    assert result["ok"], result
    assert result["destination"] == "content_pipeline"
    got = cp.get_post(result["post_id"])
    assert got["ok"]
    assert got["post"]["status"] == "DRAFT"
    assert got["post"]["source"]["kind"] == "flow"
    assert "Notes worth sharing" in got["post"]["body"]


def test_content_settings_defaults():
    import agent_friday.core as core
    c = core.DEFAULT_SETTINGS["content"]
    assert c["enabled"] is True
    assert c["conflict_window_hours"] == 2
    assert c["psi_daily_cap"] == 200
    assert c["staging_base_url"] == ""


# ═════════════════════════════════════════════════════════════════════════════
#  Egress hold end-to-end (§7.3 / §15) — needs services/publisher.py
# ═════════════════════════════════════════════════════════════════════════════

def test_egress_hold_adapter_never_called(client, patch_app, monkeypatch):
    """Gate divergence on the final body → target HELD, and the platform
    adapter is NEVER invoked (fail-closed, §7.3). Skips until the publisher
    module lands; the HELD/release rails are covered above regardless."""
    pub = pytest.importorskip(
        "agent_friday.services.publisher",
        reason="publisher module not landed yet — hold rail covered store-side")
    tick = (getattr(pub, "tick", None) or getattr(pub, "run_once", None)
            or getattr(pub, "dispatch_due", None))
    if not callable(tick):
        pytest.skip("publisher has no tick entry point")

    adapter = pr.get_adapter("mock")
    adapter.reset()

    # The classifier sees something private: gate output diverges from input.
    def _diverging_gate(text, provider=None, field=None, **kw):
        return "[VAULT-PROTECTED]"
    patch_app("gate_text", _diverging_gate)
    try:
        from agent_friday.services import egress_gate
        monkeypatch.setattr(egress_gate, "gate_text", _diverging_gate,
                            raising=False)
    except Exception:
        pass

    post = _mk_post(client, body="my SSN is definitely private",
                    platforms=["mock"])
    res = client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
    assert res.get_json()["ok"]
    tick()                              # idempotent if the route already kicked

    assert adapter.publish_calls == 0, \
        "held content must never reach the platform adapter"
    got = cp.get_post(post["id"])["post"]
    statuses = {t["status"] for t in got["targets"]}
    assert "CONFIRMED" not in statuses
    assert "HELD" in statuses or got["status"] == "HELD"
