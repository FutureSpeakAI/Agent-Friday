"""Unit tests for services/content_pipeline.py — content data model & store.

Covers the §3 spec surface: entity constructors, SQLite store CRUD, the §3.2
status machine (every edge, HELD sticky-safety + 7-day expiry, PARTIAL re-arm
that never reopens CONFIRMED targets), claim-due atomicity (mark-before-run),
ψ-award idempotency, engagement rollups, best-times upsert, publish-log
rotation, and the §3.4 kanban graduation helper.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import content_pipeline as cpl  # noqa: E402

PAST = "2026-07-01T09:00:00Z"
NOW = "2026-07-04T12:00:00Z"
FUTURE = "2026-07-10T09:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    yield


def _mk_post(platforms=("linkedin",), publish_at=PAST, **kw):
    schedule = cpl.new_schedule_config(publish_at=publish_at) if publish_at else None
    res = cpl.create_post(title="Test", body="Body text", platforms=list(platforms),
                          schedule=schedule, **kw)
    assert res["ok"], res
    return res["post"]


def _mk_scheduled(platforms=("linkedin",), publish_at=PAST, **kw):
    post = _mk_post(platforms=platforms, publish_at=publish_at, **kw)
    res = cpl.schedule_post(post["id"])
    assert res["ok"], res
    return res["post"]


def _claim_one(now=NOW):
    res = cpl.claim_due_targets(now=now)
    assert res["ok"], res
    assert res["count"] == 1
    return res["targets"][0]


# ─────────────────────────────────────────────────────────────────────────────
#  Constructors
# ─────────────────────────────────────────────────────────────────────────────

class TestConstructors:
    def test_new_post_shape(self):
        p = cpl.new_post(title="t", body="b", platforms=["linkedin"])
        assert p["id"].startswith("post_")
        assert p["status"] == "DRAFT"
        assert p["platforms"][0]["platform"] == "linkedin"
        assert p["published_at"] is None

    def test_new_target_shape(self):
        t = cpl.new_platform_target("bluesky", "thread")
        assert t["id"].startswith("tgt_")
        assert t["status"] == "PENDING"
        assert t["format"] == "thread"
        assert t["attempt"] == 0 and t["not_before"] == 0.0

    def test_new_target_bad_format_falls_back(self):
        assert cpl.new_platform_target("x", "nonsense")["format"] == "post"

    def test_new_variant_shape(self):
        v = cpl.new_variant("Hook A", "body")
        assert v["id"].startswith("var_") and v["weight"] == 0.5

    def test_new_schedule_canonicalizes_instants(self):
        s = cpl.new_schedule_config(publish_at="2026-07-04T09:00:00+00:00")
        assert s["publish_at"] == "2026-07-04T09:00:00Z"
        assert s["recurrence"] == "none"
        assert s["timezone"]

    def test_new_asset_ref_kind_fallback(self):
        a = cpl.new_asset_ref("f.png", "sha256:x", kind="bogus")
        assert a["kind"] == "image"

    def test_engagement_metrics_missing_stays_absent(self):
        m = cpl.new_engagement_metrics(likes=5)
        assert m["likes"] == 5
        assert "impressions" not in m  # missing != zero


# ─────────────────────────────────────────────────────────────────────────────
#  Post CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestPostCrud:
    def test_create_and_get_roundtrip(self):
        post = _mk_post(platforms=("linkedin", "bluesky"))
        got = cpl.get_post(post["id"])
        assert got["ok"]
        assert got["post"]["title"] == "Test"
        assert len(got["post"]["targets"]) == 2
        assert got["post"]["status"] == "DRAFT"

    def test_provenance_hash_from_primary_asset(self):
        asset = cpl.new_asset_ref("img.png", "sha256:abc123")
        post = _mk_post(assets=[asset])
        assert cpl.get_post(post["id"])["post"]["provenance_hash"] == "sha256:abc123"

    def test_unknown_platform_warns_but_stores(self):
        res = cpl.create_post(body="x", platforms=["myspace"])
        assert res["ok"]
        assert any("myspace" in w for w in res["warnings"])

    def test_get_missing_post_envelope(self):
        res = cpl.get_post("post_nope")
        assert res["ok"] is False and "error" in res

    def test_list_filters_status(self):
        _mk_post()
        _mk_scheduled()
        drafts = cpl.list_posts(status="DRAFT")
        scheduled = cpl.list_posts(status="SCHEDULED")
        assert drafts["ok"] and len(drafts["posts"]) == 1
        assert scheduled["ok"] and len(scheduled["posts"]) == 1

    def test_list_filters_platform(self):
        _mk_post(platforms=("linkedin",))
        _mk_post(platforms=("mastodon",))
        res = cpl.list_posts(platform="mastodon")
        assert len(res["posts"]) == 1
        assert res["posts"][0]["targets"][0]["platform"] == "mastodon"

    def test_list_filters_source_kind(self):
        _mk_post(source={"kind": "creation", "ref": "f.png"})
        _mk_post(source={"kind": "news", "ref": "n1"})
        res = cpl.list_posts(source_kind="news")
        assert len(res["posts"]) == 1

    def test_update_post_content(self):
        post = _mk_post()
        res = cpl.update_post(post["id"], {"title": "New", "tags": ["nsfw"]})
        assert res["ok"]
        assert res["post"]["title"] == "New"
        assert res["post"]["tags"] == ["nsfw"]

    def test_edit_scheduled_returns_to_draft(self):
        post = _mk_scheduled()
        res = cpl.update_post(post["id"], {"body": "edited"})
        assert res["ok"]
        assert res["post"]["status"] == "DRAFT"  # §3.2 edit edge

    def test_edit_scheduled_metadata_keeps_scheduled(self):
        post = _mk_scheduled()
        res = cpl.update_post(post["id"], {"tags": ["a"]})
        assert res["ok"]
        assert res["post"]["status"] == "SCHEDULED"

    def test_update_terminal_post_rejected(self):
        post = _mk_scheduled()
        tgt = _claim_one()
        cpl.set_target_status(tgt["id"], "CONFIRMED")
        res = cpl.update_post(post["id"], {"body": "nope"})
        assert res["ok"] is False

    def test_patch_schedule_recomputes_target_publish_at(self):
        # Regression: a 'schedule' PATCH must move targets.publish_at too —
        # the publisher scans the target column, not schedule_json.
        post = _mk_scheduled(publish_at=PAST)
        res = cpl.update_post(post["id"], {"schedule": {"publish_at": FUTURE}})
        assert res["ok"], res
        assert res["post"]["schedule"]["publish_at"] == FUTURE
        assert res["post"]["targets"][0]["publish_at"] == FUTURE
        assert res["post"]["status"] == "SCHEDULED"
        # nothing is claimable at the instant the user moved away from
        assert cpl.claim_due_targets(now=NOW)["count"] == 0

    def test_patch_schedule_merges_and_canonicalizes(self):
        post = _mk_scheduled(publish_at=PAST)
        res = cpl.update_post(
            post["id"], {"schedule": {"publish_at": "2026-07-10T09:00:00+00:00"}})
        assert res["ok"]
        sched = res["post"]["schedule"]
        assert sched["publish_at"] == FUTURE          # canonical Z form
        assert sched["timezone"]                       # merged, not replaced

    def test_delete_post_is_cancel(self):
        post = _mk_post()
        res = cpl.delete_post(post["id"])
        assert res["ok"]
        assert res["post"]["status"] == "CANCELLED"

    def test_delete_published_post_erases_to_cancelled(self):
        # §12.6 erase: delete works on a PUBLISHED post (cancel still refuses).
        post = _mk_scheduled()
        tgt = _claim_one()
        cpl.set_target_status(tgt["id"], "CONFIRMED")
        assert cpl.get_post(post["id"])["post"]["status"] == "PUBLISHED"
        assert cpl.cancel_post(post["id"])["ok"] is False   # cancel ≠ erase
        res = cpl.delete_post(post["id"])
        assert res["ok"], res
        assert res["post"]["status"] == "CANCELLED"
        # the confirmed target keeps its record — audit story intact
        assert res["post"]["targets"][0]["status"] == "CONFIRMED"
        # idempotent second erase
        assert cpl.delete_post(post["id"])["ok"]


# ─────────────────────────────────────────────────────────────────────────────
#  Scheduling + publish-now
# ─────────────────────────────────────────────────────────────────────────────

class TestScheduling:
    def test_schedule_resolves_target_publish_at(self):
        post = _mk_scheduled(publish_at=PAST)
        assert post["status"] == "SCHEDULED"
        assert post["targets"][0]["publish_at"] == PAST

    def test_target_override_wins(self):
        t = cpl.new_platform_target("linkedin", publish_at_override=FUTURE)
        res = cpl.create_post(body="x", platforms=[t],
                              schedule=cpl.new_schedule_config(publish_at=PAST))
        post = cpl.schedule_post(res["post"]["id"])["post"]
        assert post["targets"][0]["publish_at"] == FUTURE

    def test_schedule_without_instant_rejected(self):
        post = _mk_post(publish_at=None)
        res = cpl.schedule_post(post["id"])
        assert res["ok"] is False

    def test_schedule_optimal_time_without_instant_ok(self):
        post = _mk_post(publish_at=None)
        res = cpl.schedule_post(post["id"], {"optimal_time": True})
        assert res["ok"]
        assert res["post"]["status"] == "SCHEDULED"

    def test_reschedule_from_scheduled(self):
        post = _mk_scheduled(publish_at=PAST)
        res = cpl.schedule_post(post["id"], {"publish_at": FUTURE})
        assert res["ok"]
        assert res["post"]["targets"][0]["publish_at"] == FUTURE

    def test_schedule_from_publishing_rejected(self):
        post = _mk_scheduled()
        _claim_one()
        res = cpl.schedule_post(post["id"], {"publish_at": FUTURE})
        assert res["ok"] is False

    def test_publish_now_from_draft(self):
        post = _mk_post(publish_at=None)
        res = cpl.publish_now(post["id"])
        assert res["ok"]
        assert res["post"]["status"] == "PUBLISHING"
        # publish_now stamps wall-clock now — claim with a later instant
        claimed = cpl.claim_due_targets(now="2027-01-01T00:00:00Z")
        assert claimed["count"] == 1

    def test_publish_now_without_targets_rejected(self):
        post = _mk_post(platforms=(), publish_at=None)
        res = cpl.publish_now(post["id"])
        assert res["ok"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  claim_due_targets — mark-before-run atomicity
# ─────────────────────────────────────────────────────────────────────────────

class TestClaimDue:
    def test_claims_due_target_and_marks_preparing(self):
        post = _mk_scheduled()
        tgt = _claim_one()
        assert tgt["status"] == "PREPARING"
        assert tgt["attempt"] == 1
        assert cpl.get_post(post["id"])["post"]["status"] == "PUBLISHING"

    def test_second_claim_returns_nothing(self):
        _mk_scheduled()
        _claim_one()
        again = cpl.claim_due_targets(now=NOW)
        assert again["ok"] and again["count"] == 0

    def test_future_target_not_claimed(self):
        _mk_scheduled(publish_at=FUTURE)
        res = cpl.claim_due_targets(now=NOW)
        assert res["count"] == 0

    def test_not_before_backoff_gates_claim(self):
        post = _mk_scheduled()
        future_epoch = datetime(2026, 7, 5, tzinfo=timezone.utc).timestamp()
        tid = post["targets"][0]["id"]
        tgt = _claim_one()
        assert tgt["id"] == tid
        # transient failure → retry backoff: PREPARING → PENDING + not_before
        r = cpl.set_target_status(tid, "PENDING", error="429",
                                  not_before=future_epoch)
        assert r["ok"]
        assert r["post_status"] == "PUBLISHING"  # in flight, not FAILED
        assert cpl.claim_due_targets(now=NOW)["count"] == 0
        later = cpl.claim_due_targets(now="2026-07-06T00:00:00Z")
        assert later["count"] == 1

    def test_draft_post_targets_never_claimed(self):
        _mk_post(publish_at=PAST)  # DRAFT — targets have a due publish_at
        assert cpl.claim_due_targets(now=NOW)["count"] == 0

    def test_cancelled_post_targets_never_claimed(self):
        post = _mk_scheduled()
        cpl.cancel_post(post["id"])
        assert cpl.claim_due_targets(now=NOW)["count"] == 0

    def test_held_post_pending_targets_sticky_safe(self):
        # A post with one HELD target must not ship its other PENDING target.
        post = _mk_scheduled(platforms=("linkedin", "bluesky"))
        claimed = cpl.claim_due_targets(now=NOW)
        assert claimed["count"] == 2
        t1, t2 = claimed["targets"]
        r = cpl.set_target_status(t1["id"], "HELD")
        assert r["post_status"] == "HELD"
        # put the second back to PENDING (e.g. deferred) — still must not claim
        cpl.defer_target(t2["id"], 0)
        assert cpl.claim_due_targets(now=NOW)["count"] == 0  # sticky-safe

    def test_concurrent_claims_never_double_claim(self):
        for _ in range(5):
            _mk_scheduled()
        results, errs = [], []

        def worker():
            try:
                r = cpl.claim_due_targets(now=NOW)
                assert r["ok"]
                results.extend(t["id"] for t in r["targets"])
            except Exception as e:  # pragma: no cover
                errs.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errs
        assert len(results) == 5
        assert len(set(results)) == 5  # each target claimed exactly once

    def test_defer_refunds_attempt(self):
        _mk_scheduled()
        tgt = _claim_one()
        assert tgt["attempt"] == 1
        res = cpl.defer_target(tgt["id"], 0)
        assert res["ok"]
        assert res["target"]["status"] == "PENDING"
        assert res["target"]["attempt"] == 0  # no attempt burned (§7.1 step 5)

    def test_defer_requires_preparing(self):
        post = _mk_scheduled()
        res = cpl.defer_target(post["targets"][0]["id"], 0)
        assert res["ok"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  Crash recovery — stale PREPARING claims
# ─────────────────────────────────────────────────────────────────────────────

class TestRecoverStaleClaims:
    def _aged_claim(self, hours=2):
        _mk_scheduled()
        tgt = _claim_one()
        old = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        with cpl._connect() as con:
            con.execute("UPDATE targets SET updated_at=? WHERE id=?",
                        (old, tgt["id"]))
        return tgt

    def test_stale_preparing_recovers_to_pending(self):
        tgt = self._aged_claim()
        res = cpl.recover_stale_claims()
        assert res["ok"] and tgt["id"] in res["recovered"]
        got = cpl.get_target(tgt["id"])["target"]
        assert got["status"] == "PENDING"
        assert got["attempt"] == 1            # the burned attempt stands
        # claimable again on the next tick
        assert cpl.claim_due_targets(now="2027-01-01T00:00:00Z")["count"] == 1

    def test_fresh_claim_not_recovered(self):
        _mk_scheduled()
        # claim at wall-clock now — updated_at stamps the claim instant
        res = cpl.claim_due_targets()
        assert res["count"] == 1
        assert cpl.recover_stale_claims()["count"] == 0

    def test_exclude_protects_in_flight_work(self):
        tgt = self._aged_claim()
        res = cpl.recover_stale_claims(exclude=[tgt["id"]])
        assert res["ok"] and res["count"] == 0
        assert cpl.get_target(tgt["id"])["target"]["status"] == "PREPARING"


# ─────────────────────────────────────────────────────────────────────────────
#  Status machine — rollups (§3.2)
# ─────────────────────────────────────────────────────────────────────────────

class TestStatusMachine:
    def test_happy_path_to_published(self):
        post = _mk_scheduled()
        tgt = _claim_one()
        r = cpl.set_target_status(tgt["id"], "SENT")
        assert r["post_status"] == "PUBLISHING"
        r = cpl.set_target_status(tgt["id"], "CONFIRMED",
                                  post_url="https://l.i/1", platform_post_id="p1")
        assert r["post_status"] == "PUBLISHED"
        got = cpl.get_post(post["id"])["post"]
        assert got["published_at"] is not None
        assert got["targets"][0]["post_url"] == "https://l.i/1"

    def test_mixed_confirmed_failed_is_partial(self):
        post = _mk_scheduled(platforms=("linkedin", "twitter"))
        claimed = cpl.claim_due_targets(now=NOW)["targets"]
        cpl.set_target_status(claimed[0]["id"], "CONFIRMED")
        r = cpl.set_target_status(claimed[1]["id"], "FAILED", error="500")
        assert r["post_status"] == "PARTIAL"

    def test_all_failed_is_failed(self):
        post = _mk_scheduled(platforms=("linkedin", "twitter"))
        for t in cpl.claim_due_targets(now=NOW)["targets"]:
            cpl.set_target_status(t["id"], "FAILED", error="boom")
        assert cpl.get_post(post["id"])["post"]["status"] == "FAILED"

    def test_any_held_wins_rollup(self):
        post = _mk_scheduled(platforms=("linkedin", "twitter"))
        claimed = cpl.claim_due_targets(now=NOW)["targets"]
        cpl.set_target_status(claimed[0]["id"], "CONFIRMED")
        r = cpl.set_target_status(claimed[1]["id"], "HELD")
        assert r["post_status"] == "HELD"

    def test_in_flight_stays_publishing(self):
        post = _mk_scheduled(platforms=("linkedin", "twitter"))
        claimed = cpl.claim_due_targets(now=NOW)["targets"]
        r = cpl.set_target_status(claimed[0]["id"], "CONFIRMED")
        assert r["post_status"] == "PUBLISHING"  # second still PREPARING

    def test_confirmed_target_is_immutable(self):
        _mk_scheduled()
        tgt = _claim_one()
        cpl.set_target_status(tgt["id"], "CONFIRMED")
        for bad in ("PENDING", "FAILED", "HELD", "SKIPPED"):
            res = cpl.set_target_status(tgt["id"], bad)
            assert res["ok"] is False

    def test_unknown_status_rejected(self):
        post = _mk_scheduled()
        res = cpl.set_target_status(post["targets"][0]["id"], "EXPLODED")
        assert res["ok"] is False

    def test_published_at_is_first_confirmation(self):
        post = _mk_scheduled(platforms=("linkedin", "twitter"))
        claimed = cpl.claim_due_targets(now=NOW)["targets"]
        cpl.set_target_status(claimed[0]["id"], "CONFIRMED")
        first = cpl.get_post(post["id"])["post"]["published_at"]
        cpl.set_target_status(claimed[1]["id"], "CONFIRMED")
        assert cpl.get_post(post["id"])["post"]["published_at"] == first

    def test_skipped_counts_as_dead(self):
        post = _mk_scheduled(platforms=("linkedin", "twitter"))
        claimed = cpl.claim_due_targets(now=NOW)["targets"]
        cpl.set_target_status(claimed[0]["id"], "CONFIRMED")
        cpl.set_target_status(claimed[1]["id"], "SKIPPED")
        assert cpl.get_post(post["id"])["post"]["status"] == "PARTIAL"


# ─────────────────────────────────────────────────────────────────────────────
#  Cancel
# ─────────────────────────────────────────────────────────────────────────────

class TestCancel:
    def test_cancel_scheduled_post(self):
        post = _mk_scheduled()
        res = cpl.cancel_post(post["id"])
        assert res["ok"]
        assert res["post"]["status"] == "CANCELLED"
        assert res["post"]["targets"][0]["status"] == "SKIPPED"

    def test_cancel_publishing_post_rejected(self):
        post = _mk_scheduled()
        _claim_one()
        res = cpl.cancel_post(post["id"])
        assert res["ok"] is False

    def test_cancel_single_target_rolls_up(self):
        post = _mk_scheduled(platforms=("linkedin", "twitter"))
        claimed = cpl.claim_due_targets(now=NOW)["targets"]
        cpl.set_target_status(claimed[0]["id"], "CONFIRMED")
        # second was claimed too; put it back to PENDING then cancel it
        cpl.defer_target(claimed[1]["id"], 0)
        res = cpl.cancel_post(post["id"], target_id=claimed[1]["id"])
        assert res["ok"]
        assert res["post_status"] == "PARTIAL"

    def test_cancel_terminal_post_rejected(self):
        post = _mk_scheduled()
        tgt = _claim_one()
        cpl.set_target_status(tgt["id"], "CONFIRMED")
        res = cpl.cancel_post(post["id"])
        assert res["ok"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  HELD — release + 7-day expiry (§3.2)
# ─────────────────────────────────────────────────────────────────────────────

class TestHeld:
    def _held_post(self, platforms=("linkedin",)):
        post = _mk_scheduled(platforms=platforms)
        claimed = cpl.claim_due_targets(now=NOW)["targets"]
        for t in claimed:
            cpl.set_target_status(t["id"], "HELD")
        return cpl.get_post(post["id"])["post"]

    def test_release_returns_to_publishing(self):
        post = self._held_post()
        res = cpl.release_held(post["id"])
        assert res["ok"]
        assert res["post"]["status"] == "PUBLISHING"
        assert res["post"]["targets"][0]["status"] == "PENDING"

    def test_release_then_confirm_publishes(self):
        post = self._held_post()
        cpl.release_held(post["id"])
        tgt = cpl.claim_due_targets(now="2027-01-01T00:00:00Z")["targets"][0]
        r = cpl.set_target_status(tgt["id"], "CONFIRMED")
        assert r["post_status"] == "PUBLISHED"

    def test_release_single_target_keeps_held_while_others_held(self):
        post = self._held_post(platforms=("linkedin", "twitter"))
        t_held = post["targets"][0]["id"]
        res = cpl.release_held(post["id"], target_id=t_held)
        assert res["ok"]
        assert res["post"]["status"] == "HELD"  # the other target is still HELD

    def test_released_target_is_claimable_despite_held_post(self):
        # Regression: releasing one of several holds must not deadlock — the
        # released target carries options.released and IS claimable while the
        # post stays HELD for its sibling; the sibling stays sticky-safe.
        post = self._held_post(platforms=("linkedin", "twitter"))
        t_rel = post["targets"][0]["id"]
        t_other = post["targets"][1]["id"]
        assert cpl.release_held(post["id"], target_id=t_rel)["ok"]
        claimed = cpl.claim_due_targets(now="2027-01-01T00:00:00Z")
        assert claimed["count"] == 1
        assert claimed["targets"][0]["id"] == t_rel
        assert cpl.get_target(t_other)["target"]["status"] == "HELD"
        assert cpl.get_post(post["id"])["post"]["status"] == "HELD"
        # the released target publishes; the post stays HELD for the sibling
        r = cpl.set_target_status(t_rel, "CONFIRMED")
        assert r["ok"] and r["post_status"] == "HELD"

    def test_partial_release_restarts_hold_expiry_clock(self):
        # Regression: a single-target release is a human ack — it must bump
        # posts.updated_at so expire_stale_holds does NOT fire on the ORIGINAL
        # hold's 7-day schedule and sweep the released target to SKIPPED.
        post = self._held_post(platforms=("linkedin", "twitter"))
        t_rel = post["targets"][0]["id"]
        # age the hold: updated_at 8 days back, THEN release one target
        old = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with cpl._connect() as con:
            con.execute("UPDATE posts SET updated_at=? WHERE id=?", (old, post["id"]))
        assert cpl.release_held(post["id"], target_id=t_rel)["ok"]
        res = cpl.expire_stale_holds()
        assert res["ok"] and res["count"] == 0          # clock restarted
        got = cpl.get_post(post["id"])["post"]
        assert got["status"] == "HELD"                  # sibling still sticky
        statuses = {t["id"]: t["status"] for t in got["targets"]}
        assert statuses[t_rel] == "PENDING"             # ack honored, not SKIPPED

    def test_release_non_held_post_rejected(self):
        post = _mk_scheduled()
        res = cpl.release_held(post["id"])
        assert res["ok"] is False

    def test_fresh_hold_not_expired(self):
        post = self._held_post()
        res = cpl.expire_stale_holds()
        assert res["ok"] and res["count"] == 0
        assert cpl.get_post(post["id"])["post"]["status"] == "HELD"

    def test_stale_hold_expires_to_cancelled(self):
        post = self._held_post()
        # age the hold: updated_at 8 days back
        old = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with cpl._connect() as con:
            con.execute("UPDATE posts SET updated_at=? WHERE id=?", (old, post["id"]))
        res = cpl.expire_stale_holds()
        assert res["ok"] and post["id"] in res["expired"]
        got = cpl.get_post(post["id"])["post"]
        assert got["status"] == "CANCELLED"
        assert got["targets"][0]["status"] == "SKIPPED"

    def test_expiry_boundary_uses_seven_days(self):
        post = self._held_post()
        six_days = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with cpl._connect() as con:
            con.execute("UPDATE posts SET updated_at=? WHERE id=?", (six_days, post["id"]))
        assert cpl.expire_stale_holds()["count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
#  Re-arm (§3.2 — never reopens CONFIRMED)
# ─────────────────────────────────────────────────────────────────────────────

class TestRearm:
    def _partial_post(self):
        post = _mk_scheduled(platforms=("linkedin", "twitter"))
        claimed = cpl.claim_due_targets(now=NOW)["targets"]
        cpl.set_target_status(claimed[0]["id"], "CONFIRMED",
                              post_url="https://ok/1", platform_post_id="ok1")
        cpl.set_target_status(claimed[1]["id"], "FAILED", error="500")
        return cpl.get_post(post["id"])["post"]

    def test_rearm_partial_reopens_only_failed(self):
        post = self._partial_post()
        assert post["status"] == "PARTIAL"
        res = cpl.rearm_post(post["id"], publish_at=PAST)
        assert res["ok"]
        assert res["post"]["status"] == "SCHEDULED"
        by_status = {t["status"] for t in res["post"]["targets"]}
        assert by_status == {"CONFIRMED", "PENDING"}
        assert len(res["reopened"]) == 1

    def test_rearm_never_touches_confirmed(self):
        post = self._partial_post()
        confirmed = [t for t in post["targets"] if t["status"] == "CONFIRMED"][0]
        cpl.rearm_post(post["id"], publish_at=PAST)
        got = cpl.get_target(confirmed["id"])["target"]
        assert got["status"] == "CONFIRMED"
        assert got["post_url"] == "https://ok/1"  # untouched — no double-post

    def test_rearm_claim_only_returns_reopened(self):
        post = self._partial_post()
        cpl.rearm_post(post["id"], publish_at=PAST)
        claimed = cpl.claim_due_targets(now=NOW)
        assert claimed["count"] == 1
        assert claimed["targets"][0]["platform"] == "twitter"

    def test_rearm_then_confirm_publishes(self):
        post = self._partial_post()
        cpl.rearm_post(post["id"], publish_at=PAST)
        tgt = cpl.claim_due_targets(now=NOW)["targets"][0]
        r = cpl.set_target_status(tgt["id"], "CONFIRMED")
        assert r["post_status"] == "PUBLISHED"

    def test_rearm_failed_post(self):
        post = _mk_scheduled()
        tgt = _claim_one()
        cpl.set_target_status(tgt["id"], "FAILED", error="dead")
        assert cpl.get_post(post["id"])["post"]["status"] == "FAILED"
        res = cpl.rearm_post(post["id"])
        assert res["ok"]
        assert res["post"]["status"] == "SCHEDULED"
        assert res["post"]["targets"][0]["error"] is None  # error cleared

    def test_rearm_wrong_state_rejected(self):
        post = _mk_post()
        assert cpl.rearm_post(post["id"])["ok"] is False
        sched = _mk_scheduled()
        assert cpl.rearm_post(sched["id"])["ok"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  Targets — add / update
# ─────────────────────────────────────────────────────────────────────────────

class TestTargets:
    def test_add_targets_resolves_publish_at(self):
        post = _mk_scheduled()
        res = cpl.add_targets(post["id"], ["mastodon"])
        assert res["ok"]
        added = [t for t in res["post"]["targets"] if t["platform"] == "mastodon"][0]
        assert added["publish_at"] == PAST

    def test_update_target_payload(self):
        post = _mk_post()
        tid = post["targets"][0]["id"]
        res = cpl.update_target(tid, {"adapted_body": "Native text",
                                      "hashtags": ["#ai"], "char_limit": 3000})
        assert res["ok"]
        assert res["target"]["adapted_body"] == "Native text"
        assert res["target"]["hashtags"] == ["#ai"]
        assert res["target"]["char_limit"] == 3000

    def test_update_confirmed_target_rejected(self):
        _mk_scheduled()
        tgt = _claim_one()
        cpl.set_target_status(tgt["id"], "CONFIRMED")
        res = cpl.update_target(tgt["id"], {"adapted_body": "sneaky edit"})
        assert res["ok"] is False

    def test_update_override_recomputes_publish_at(self):
        post = _mk_scheduled()
        tid = post["targets"][0]["id"]
        res = cpl.update_target(tid, {"publish_at_override": FUTURE})
        assert res["ok"]
        assert res["target"]["publish_at"] == FUTURE

    def test_get_missing_target_envelope(self):
        assert cpl.get_target("tgt_nope")["ok"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  ψ awards — idempotency (§8.7)
# ─────────────────────────────────────────────────────────────────────────────

class TestPsiAwards:
    def test_first_crossing_awards(self):
        res = cpl.award_psi("tgt_a", "likes", 10, 1000)
        assert res["ok"] and res["awarded"] is True

    def test_second_crossing_does_not(self):
        cpl.award_psi("tgt_a", "likes", 10, 1000)
        res = cpl.award_psi("tgt_a", "likes", 10, 1000)
        assert res["ok"] and res["awarded"] is False

    def test_repoll_storm_mints_once(self):
        awarded = sum(
            1 for _ in range(50)
            if cpl.award_psi("tgt_storm", "likes", 10, 1000)["awarded"])
        assert awarded == 1

    def test_distinct_thresholds_are_distinct_awards(self):
        assert cpl.award_psi("tgt_b", "likes", 10, 1000)["awarded"]
        assert cpl.award_psi("tgt_b", "likes", 20, 1000)["awarded"]
        assert cpl.award_psi("tgt_b", "shares", 10, 1000)["awarded"]

    def test_list_awards_filters_by_target(self):
        cpl.award_psi("tgt_c", "likes", 10, 1000)
        cpl.award_psi("tgt_d", "likes", 10, 1000)
        res = cpl.list_psi_awards(target_id="tgt_c")
        assert res["ok"] and len(res["awards"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
#  Best-times histogram
# ─────────────────────────────────────────────────────────────────────────────

class TestBestTimes:
    def test_upsert_and_read(self):
        assert cpl.upsert_best_time("linkedin", 1, 9, 0.42, 7)["ok"]
        rows = cpl.get_best_times(platform="linkedin")["best_times"]
        assert rows[0]["score"] == 0.42 and rows[0]["samples"] == 7

    def test_upsert_updates_in_place(self):
        cpl.upsert_best_time("linkedin", 1, 9, 0.1, 2)
        cpl.upsert_best_time("linkedin", 1, 9, 0.9, 6)
        rows = cpl.get_best_times(platform="linkedin")["best_times"]
        assert len(rows) == 1 and rows[0]["score"] == 0.9

    def test_min_samples_filter(self):
        cpl.upsert_best_time("x", 2, 10, 0.5, 3)
        cpl.upsert_best_time("x", 3, 11, 0.6, 8)
        rows = cpl.get_best_times(platform="x", min_samples=5)["best_times"]
        assert len(rows) == 1 and rows[0]["samples"] == 8

    def test_invalid_cell_rejected(self):
        assert cpl.upsert_best_time("x", 9, 10, 0.5, 1)["ok"] is False
        assert cpl.upsert_best_time("x", 1, 99, 0.5, 1)["ok"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  Engagement snapshots + rollup
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshots:
    def _published(self, platforms=("linkedin", "bluesky")):
        post = _mk_scheduled(platforms=platforms)
        targets = cpl.claim_due_targets(now=NOW)["targets"]
        for t in targets:
            cpl.set_target_status(t["id"], "CONFIRMED")
        return cpl.get_post(post["id"])["post"]

    def test_snapshot_missing_target_rejected(self):
        res = cpl.insert_engagement_snapshot("tgt_nope", {"likes": 1})
        assert res["ok"] is False

    def test_rollup_totals_and_rate(self):
        post = self._published()
        t1, t2 = post["targets"]
        cpl.insert_engagement_snapshot(
            t1["id"], {"impressions": 100, "likes": 8, "comments": 2})
        res = cpl.insert_engagement_snapshot(t2["id"], {"likes": 10})
        a = res["analytics"]
        assert a["impressions"] == 100
        assert a["likes"] == 18
        # (18 likes + 2 comments) / 100 impressions
        assert a["engagement_rate"] == pytest.approx(0.2)
        assert set(a["per_platform"]) == {"linkedin", "bluesky"}

    def test_latest_snapshot_wins_not_summed(self):
        post = self._published(platforms=("linkedin",))
        tid = post["targets"][0]["id"]
        cpl.insert_engagement_snapshot(tid, {"likes": 5},
                                       collected_at="2026-07-05T00:00:00Z")
        res = cpl.insert_engagement_snapshot(tid, {"likes": 9},
                                             collected_at="2026-07-06T00:00:00Z")
        assert res["analytics"]["likes"] == 9  # latest, not 14

    def test_missing_metric_stays_absent(self):
        post = self._published(platforms=("bluesky",))
        res = cpl.insert_engagement_snapshot(post["targets"][0]["id"], {"likes": 3})
        assert "impressions" not in res["analytics"]  # "—", not 0 (§8.2)

    def test_rate_denominator_falls_back_to_video_views(self):
        post = self._published(platforms=("tiktok",))
        res = cpl.insert_engagement_snapshot(
            post["targets"][0]["id"], {"video_views": 50, "likes": 5})
        assert res["analytics"]["engagement_rate"] == pytest.approx(0.1)

    def test_raw_payload_capped(self):
        post = self._published(platforms=("linkedin",))
        tid = post["targets"][0]["id"]
        cpl.insert_engagement_snapshot(tid, {"likes": 1},
                                       raw={"blob": "x" * 20000})
        snap = cpl.get_snapshots(target_id=tid)["snapshots"][0]
        assert snap["raw"].get("_truncated") is True

    def test_non_numeric_metrics_dropped(self):
        post = self._published(platforms=("linkedin",))
        res = cpl.insert_engagement_snapshot(
            post["targets"][0]["id"],
            {"likes": "12", "comments": "ignore me", "evil": 99})
        a = res["analytics"]
        assert a["likes"] == 12          # coerced
        assert "comments" not in a       # non-numeric dropped
        assert "evil" not in a           # unknown key dropped

    def test_snapshot_history_preserved(self):
        post = self._published(platforms=("linkedin",))
        tid = post["targets"][0]["id"]
        for i in range(3):
            cpl.insert_engagement_snapshot(
                tid, {"likes": i}, collected_at=f"2026-07-0{5 + i}T00:00:00Z")
        res = cpl.get_snapshots(target_id=tid)
        assert res["count"] == 3  # time-series rows, nothing overwritten


# ─────────────────────────────────────────────────────────────────────────────
#  Publish log
# ─────────────────────────────────────────────────────────────────────────────

class TestPublishLog:
    def test_append_and_read(self):
        res = cpl.append_publish_log(
            {"target_id": "tgt_1", "platform": "linkedin", "outcome": "confirmed"})
        assert res["ok"]
        entries = cpl.read_publish_log()["entries"]
        assert entries[0]["target_id"] == "tgt_1"
        assert entries[0]["ts"]  # stamped

    def test_read_filters_by_target(self):
        cpl.append_publish_log({"target_id": "tgt_1", "outcome": "confirmed"})
        cpl.append_publish_log({"target_id": "tgt_2", "outcome": "failed"})
        entries = cpl.read_publish_log(target_id="tgt_2")["entries"]
        assert len(entries) == 1 and entries[0]["outcome"] == "failed"

    def test_rotation_caps_lines(self, monkeypatch):
        monkeypatch.setattr(cpl, "PUBLISH_LOG_KEEP", 10)
        for i in range(25):
            cpl.append_publish_log({"target_id": f"tgt_{i}"})
        lines = cpl.PUBLISH_LOG.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 10
        assert json.loads(lines[-1])["target_id"] == "tgt_24"  # newest kept

    def test_read_empty_log_ok(self):
        assert cpl.read_publish_log() == {"ok": True, "entries": []}


# ─────────────────────────────────────────────────────────────────────────────
#  §3.4 kanban graduation
# ─────────────────────────────────────────────────────────────────────────────

class TestGraduation:
    ITEM = {"id": "abc12345", "title": "Ship notes", "type": "post",
            "stage": "drafting", "channel": "linkedin",
            "notes": "raw notes", "draft": "Polished draft body",
            "tags": ["launch"]}

    def test_graduates_to_draft_post(self):
        res = cpl.graduate_idea(dict(self.ITEM))
        assert res["ok"]
        post = res["post"]
        assert post["status"] == "DRAFT"
        assert post["body"] == "Polished draft body"
        assert post["source"] == {"kind": "idea", "ref": "abc12345",
                                  "channel": "linkedin", "type": "post"}
        assert post["targets"][0]["platform"] == "linkedin"

    def test_falls_back_to_notes_without_draft(self):
        item = dict(self.ITEM)
        del item["draft"]
        res = cpl.graduate_idea(item)
        assert res["post"]["body"] == "raw notes"

    def test_unknown_channel_graduates_without_target(self):
        item = dict(self.ITEM, channel="blog")
        res = cpl.graduate_idea(item)
        assert res["ok"]
        assert res["post"]["targets"] == []
        assert any("blog" in w for w in res["warnings"])

    def test_x_channel_maps_to_twitter(self):
        res = cpl.graduate_idea(dict(self.ITEM, channel="x"))
        assert res["post"]["targets"][0]["platform"] == "twitter"

    def test_scheduled_for_carries_into_schedule(self):
        res = cpl.graduate_idea(dict(self.ITEM, scheduled_for=FUTURE))
        assert res["ok"]
        assert res["post"]["schedule"]["publish_at"] == FUTURE
        assert res["post"]["status"] == "DRAFT"  # graduation never auto-arms

    def test_invalid_item_envelope(self):
        assert cpl.graduate_idea({})["ok"] is False
        assert cpl.graduate_idea(None)["ok"] is False
