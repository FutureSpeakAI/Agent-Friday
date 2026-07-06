"""Unit tests for services/publisher.py — the §7 publication engine.

Hermetic: content store on tmp, mock platform adapter (in-memory transport),
moderation/egress gate seams stubbed per test, notifications captured, ψ earn
stubbed. Covers the §7.1 gate chain (harm block, PII hold, egress hold),
the happy path to CONFIRMED (+ publish log + once-per-post ψ), §7.5 retry
classes (transient backoff, exhaustion, budget deferral), pause-all, the
crash-recovery reaper, and the release-one-of-several-holds flow end to end.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import content_pipeline as cpl        # noqa: E402
from agent_friday.services import platforms as preg              # noqa: E402
from agent_friday.services import publisher as pub               # noqa: E402
from agent_friday.services.platforms import base as pbase        # noqa: E402

PAST = "2026-07-01T09:00:00Z"
NOW = "2026-07-04T12:00:00Z"
LATER = "2030-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    preg._reset_for_tests()
    # Default seams: everything passes; tests override per scenario.
    monkeypatch.setattr(pub, "_moderation_scan",
                        lambda text: {"ok": True, "blocked": False})
    monkeypatch.setattr(pub, "_gate", lambda text, provider, field: text)
    notes = []
    monkeypatch.setattr(pub, "_notify",
                        lambda title, body="", **kw: notes.append((title, body, kw)))
    earned = []
    monkeypatch.setattr(pub, "_earn_publish_psi",
                        lambda post, target: earned.append(post["id"]))
    pub._RUNNING.clear()
    yield {"notes": notes, "earned": earned}
    preg._reset_for_tests()
    pub._RUNNING.clear()


@pytest.fixture
def notes(_iso):
    return _iso["notes"]


def _armed_post(platforms=("mock",), publish_at=PAST, **kw):
    res = cpl.create_post(title="T", body="A perfectly public body.",
                          platforms=list(platforms),
                          schedule=cpl.new_schedule_config(publish_at=publish_at),
                          **kw)
    assert res["ok"], res
    post = cpl.schedule_post(res["post"]["id"])["post"]
    return post


def _mock_adapter():
    a = preg.get_adapter("mock")
    a.reset()
    return a


# ── happy path ────────────────────────────────────────────────────────────────

def test_tick_publishes_due_target(_iso):
    adapter = _mock_adapter()
    post = _armed_post()
    res = pub.tick(now=NOW)
    assert res["ok"], res
    assert res["claimed"] == 1
    assert res["outcomes"] == {"confirmed": 1}
    got = cpl.get_post(post["id"])["post"]
    assert got["status"] == "PUBLISHED"
    t = got["targets"][0]
    assert t["status"] == "CONFIRMED"
    assert t["post_url"].startswith("mock://post/")
    assert t["platform_post_id"]
    assert adapter.publish_calls == 1
    log = cpl.read_publish_log()["entries"]
    assert log[0]["event"] == "publish" and log[0]["outcome"] == "confirmed"
    assert _iso["earned"] == [post["id"]]


def test_tick_future_target_untouched():
    _mock_adapter()
    post = _armed_post(publish_at="2099-01-01T00:00:00Z")
    res = pub.tick(now=NOW)
    assert res["ok"] and res["claimed"] == 0
    assert cpl.get_post(post["id"])["post"]["status"] == "SCHEDULED"


def test_pause_all_blocks_dispatch():
    adapter = _mock_adapter()
    _armed_post()
    cfg = preg.load_config()
    cfg["pause_all"] = True
    preg.save_config(cfg)
    res = pub.tick(now=NOW)
    assert res["ok"] and res.get("paused") is True
    assert adapter.publish_calls == 0


# ── gate chain (§7.1) ─────────────────────────────────────────────────────────

def test_egress_divergence_holds_and_adapter_never_called(monkeypatch, notes):
    adapter = _mock_adapter()
    post = _armed_post()
    monkeypatch.setattr(pub, "_gate",
                        lambda text, provider, field: "[VAULT-PROTECTED]")
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"held": 1}
    got = cpl.get_post(post["id"])["post"]
    assert got["status"] == "HELD"
    assert got["targets"][0]["status"] == "HELD"
    assert adapter.prepare_calls == 0 and adapter.publish_calls == 0
    assert any("held" in title.lower() for title, _, _ in notes)


def test_gate_error_fails_closed_to_held(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()

    def boom(text, provider, field):
        raise RuntimeError("classifier down")
    monkeypatch.setattr(pub, "_gate", boom)
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"held": 1}
    assert adapter.publish_calls == 0
    assert cpl.get_post(post["id"])["post"]["status"] == "HELD"


def test_moderation_hard_block_fails_no_retry(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()
    monkeypatch.setattr(pub, "_moderation_scan",
                        lambda text: {"ok": True, "blocked": True,
                                      "harm_level": "H4",
                                      "reason": "violence incitement"})
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"failed": 1}
    got = cpl.get_post(post["id"])["post"]
    assert got["status"] == "FAILED"
    assert "moderation" in got["targets"][0]["error"]
    assert adapter.publish_calls == 0
    # permanent: nothing left to claim
    assert pub.tick(now=LATER)["claimed"] == 0


def test_moderation_h3_pii_goes_to_held_rail(monkeypatch):
    """H3 (doxxing/PII) is the §7.3/§12.7 hold-for-review class — a human
    release ('this is intentional') must remain possible."""
    adapter = _mock_adapter()
    post = _armed_post()
    monkeypatch.setattr(pub, "_moderation_scan",
                        lambda text: {"ok": True, "blocked": True,
                                      "harm_level": "H3",
                                      "reason": "doxxing: matched 'SSN'"})
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"held": 1}
    assert adapter.publish_calls == 0
    assert cpl.get_post(post["id"])["post"]["status"] == "HELD"


def test_moderation_unavailable_fails_closed(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()

    def boom(text):
        raise RuntimeError("no scanner")
    monkeypatch.setattr(pub, "_moderation_scan", boom)
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"held": 1}
    assert adapter.publish_calls == 0
    assert cpl.get_post(post["id"])["post"]["status"] == "HELD"


# ── §7.5 failure classes ──────────────────────────────────────────────────────

def test_transient_failure_backs_off_then_succeeds():
    adapter = _mock_adapter()
    adapter.configure({"publish_error": "rate_limited", "fail_publish": 1})
    post = _armed_post()
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"retrying": 1}
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "PENDING"
    assert t["not_before"] > time.time()          # backoff gate armed
    # not_before is wall-clock epoch; a far-future claim instant clears it
    res2 = pub.tick(now=LATER)
    assert res2["outcomes"] == {"confirmed": 1}
    assert cpl.get_post(post["id"])["post"]["status"] == "PUBLISHED"


def test_retries_exhaust_to_failed(notes):
    adapter = _mock_adapter()
    adapter.configure({"publish_error": "rate_limited", "fail_always": True})
    post = _armed_post()
    outcomes = []
    for _ in range(pub.RETRY_MAX + 1):
        r = pub.tick(now=LATER)
        outcomes += [k for k in (r.get("outcomes") or {})]
        if cpl.get_post(post["id"])["post"]["status"] == "FAILED":
            break
    got = cpl.get_post(post["id"])["post"]
    assert got["status"] == "FAILED"
    assert got["targets"][0]["attempt"] <= pub.RETRY_MAX
    assert any("failed" in title.lower() for title, _, _ in notes)


def test_budget_deferral_burns_no_attempt():
    adapter = _mock_adapter()
    adapter.configure({"daily_post_limit": 1})
    adapter.consume_budget(1)                      # budget exhausted today
    post = _armed_post()
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"deferred": 1}
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "PENDING"
    assert t["attempt"] == 0                       # §7.1 step 5: refunded
    assert t["not_before"] > 0
    assert adapter.publish_calls == 0


def test_adapter_unavailable_fails_loudly(monkeypatch):
    post = _armed_post(platforms=("mock",))
    monkeypatch.setattr(preg, "get_adapter", lambda name: None)
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"failed": 1}
    assert "adapter" in cpl.get_post(post["id"])["post"]["targets"][0]["error"]


# ── crash recovery (mark-before-run limbo) ────────────────────────────────────

def test_stale_preparing_claim_recovers_and_republishes():
    adapter = _mock_adapter()
    post = _armed_post()
    claimed = cpl.claim_due_targets(now=NOW)
    assert claimed["count"] == 1                   # simulate: process dies here
    tid = claimed["targets"][0]["id"]
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with cpl._connect() as con:
        con.execute("UPDATE targets SET updated_at=? WHERE id=?", (old, tid))
    res = pub.tick(now=LATER)
    assert res["outcomes"] == {"confirmed": 1}
    got = cpl.get_post(post["id"])["post"]
    assert got["status"] == "PUBLISHED"
    assert adapter.publish_calls == 1


def test_fresh_preparing_claim_not_yanked():
    _mock_adapter()
    _armed_post()
    cpl.claim_due_targets(now=NOW)                 # fresh claim, updated_at≈now
    res = pub.tick(now=NOW)
    assert res["claimed"] == 0                     # reaper leaves it alone


# ── SENT crash limbo — §7.2 restart-time verify (never blind-retry) ──────────

def _wedge_sent(hours=2):
    """Simulate a crash between the SENT mark and confirm/fail: claim, flip
    to SENT, then backdate updated_at as if the process died hours ago."""
    claimed = cpl.claim_due_targets(now=None)
    assert claimed["count"] == 1
    tid = claimed["targets"][0]["id"]
    assert cpl.set_target_status(tid, "SENT")["ok"]
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with cpl._connect() as con:
        con.execute("UPDATE targets SET updated_at=? WHERE id=?", (old, tid))
    return tid


def test_sent_limbo_probe_found_confirms_the_publish_that_happened(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()
    tid = _wedge_sent()
    monkeypatch.setattr(adapter, "verify_recent_post",
                        lambda prepared: {"platform_post_id": "mock-limbo-1",
                                          "post_url": "mock://post/limbo-1"},
                        raising=False)
    res = pub.tick(now=None)
    assert res["ok"], res
    assert res["sent_recovered"] == {"confirmed": 1}
    got = cpl.get_post(post["id"])["post"]
    assert got["status"] == "PUBLISHED"
    t = got["targets"][0]
    assert t["id"] == tid and t["status"] == "CONFIRMED"
    assert t["platform_post_id"] == "mock-limbo-1"   # the probe's find
    assert adapter.publish_calls == 0                # NEVER re-sent


def test_sent_limbo_probe_not_found_retries_then_publishes(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()
    _wedge_sent()
    monkeypatch.setattr(adapter, "verify_recent_post",
                        lambda prepared: None, raising=False)
    res = pub.tick(now=None)
    assert res["sent_recovered"] == {"retrying": 1}
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "PENDING" and t["not_before"] > time.time()
    res2 = pub.tick(now=LATER)                       # backoff elapsed
    assert res2["outcomes"] == {"confirmed": 1}
    assert cpl.get_post(post["id"])["post"]["status"] == "PUBLISHED"
    assert adapter.publish_calls == 1


def test_sent_limbo_without_probe_refuses_blind_retry(notes):
    adapter = _mock_adapter()                        # mock has no probe
    post = _armed_post()
    _wedge_sent()
    res = pub.tick(now=None)
    assert res["sent_recovered"] == {"failed": 1}
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "FAILED"
    assert "refusing blind retry" in t["error"]
    assert adapter.publish_calls == 0
    assert any("failed" in title.lower() for title, _, _ in notes)


def test_fresh_sent_target_not_yanked():
    _mock_adapter()
    post = _armed_post()
    claimed = cpl.claim_due_targets(now=None)
    tid = claimed["targets"][0]["id"]
    cpl.set_target_status(tid, "SENT")               # in flight right now
    res = pub.tick(now=None)
    assert "sent_recovered" not in res
    assert cpl.get_post(post["id"])["post"]["targets"][0]["status"] == "SENT"


# ── §7.2: an adapter marking a network error ambiguous takes the probe path ──

def test_publish_network_error_with_ambiguous_flag_runs_probe(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()
    monkeypatch.setattr(
        adapter, "publish",
        lambda prepared: {"ok": False, "error": "network_error",
                          "ambiguous": True})       # e.g. YouTube's lost PUT
    monkeypatch.setattr(adapter, "verify_recent_post",
                        lambda prepared: {"platform_post_id": "mock-amb-1",
                                          "post_url": "mock://post/amb-1"},
                        raising=False)
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"confirmed": 1}       # not the blind ladder
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "CONFIRMED"
    assert t["platform_post_id"] == "mock-amb-1"


# ── release one of several holds (deadlock regression) ───────────────────────

def test_release_single_held_target_actually_publishes():
    adapter = _mock_adapter()
    post = _armed_post(platforms=("mock", "linkedin"))
    claimed = cpl.claim_due_targets(now=NOW)["targets"]
    for t in claimed:
        cpl.set_target_status(t["id"], "HELD")
    assert cpl.get_post(post["id"])["post"]["status"] == "HELD"
    mock_tid = [t["id"] for t in claimed if t["platform"] == "mock"][0]
    rel = cpl.release_held(post["id"], target_id=mock_tid)
    assert rel["ok"]
    assert rel["post"]["status"] == "HELD"         # sibling still held
    # wall-clock tick: release stamped publish_at=now, and a far-future
    # instant would trip the 7-day hold expiry on the sibling
    res = pub.tick()
    assert res["outcomes"] == {"confirmed": 1}
    got = cpl.get_post(post["id"])["post"]
    by_plat = {t["platform"]: t for t in got["targets"]}
    assert by_plat["mock"]["status"] == "CONFIRMED"      # release delivered
    assert by_plat["linkedin"]["status"] == "HELD"       # sticky-safe intact
    assert got["status"] == "HELD"
    assert adapter.publish_calls == 1


# ── kick / start plumbing ─────────────────────────────────────────────────────

def test_kick_is_inert_under_testing():
    res = pub.kick()
    assert res["kicked"] is False
    assert "FRIDAY_TESTING" in res["reason"]


def test_start_registers_builtins():
    res = pub.start()
    assert res["ok"], res
    assert "content_publisher" in res["registered"]
    assert "content_analytics" in res["registered"]
    assert "content_insights" in res["registered"]
    from agent_friday.services.scheduler import BUILTIN_TASKS
    assert "content_publisher" in BUILTIN_TASKS


# ── §7.5: 429 Retry-After + 401 refresh-then-success ─────────────────────────

def test_429_retry_after_honored_over_backoff_ladder(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()
    monkeypatch.setattr(
        adapter, "publish",
        lambda prepared: {"ok": False, "error": "rate_limited",
                          "retry_after": 900})
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"retrying": 1}
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "PENDING"
    delta = t["not_before"] - time.time()
    assert 850 <= delta <= 905          # platform's 900 s, not the 300 s ladder


def test_auth_401_refresh_then_success():
    adapter = _mock_adapter()
    adapter.configure({"publish_error": "auth_error", "fail_publish": 1})
    post = _armed_post()
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"retrying": 1}      # refresh() ok → one more go
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "PENDING"
    res2 = pub.tick(now=LATER)
    assert res2["outcomes"] == {"confirmed": 1}
    assert cpl.get_post(post["id"])["post"]["status"] == "PUBLISHED"
    assert adapter.publish_calls == 2


# ── §7.2 ambiguous timeout — verify-before-retry probe, both outcomes ─────────

def test_ambiguous_timeout_probe_found_confirms_without_resend(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()
    monkeypatch.setattr(
        adapter, "publish",
        lambda prepared: {"ok": False, "error": "ambiguous_timeout"})
    probed = []

    def probe(prepared):
        probed.append(prepared)
        return {"platform_post_id": "mock-probe-1",
                "post_url": "mock://post/probe-1"}
    monkeypatch.setattr(adapter, "verify_recent_post", probe, raising=False)
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"confirmed": 1}
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "CONFIRMED"
    assert t["platform_post_id"] == "mock-probe-1"   # the probe's find, not a resend
    assert probed and probed[0].get("body")          # fingerprint = prepared payload
    assert pub.tick(now=LATER)["claimed"] == 0       # immutable; never re-sent


def test_ambiguous_timeout_probe_not_found_retries_safely(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()
    real_publish = adapter.publish
    calls = {"n": 0}

    def flaky(prepared):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "error": "ambiguous_timeout"}
        return real_publish(prepared)
    monkeypatch.setattr(adapter, "publish", flaky)
    monkeypatch.setattr(adapter, "verify_recent_post",
                        lambda prepared: None, raising=False)
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"retrying": 1}        # probe cleared the retry
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "PENDING" and t["not_before"] > time.time()
    res2 = pub.tick(now=LATER)
    assert res2["outcomes"] == {"confirmed": 1}
    assert calls["n"] == 2


def test_ambiguous_timeout_without_probe_refuses_blind_retry(monkeypatch):
    adapter = _mock_adapter()
    post = _armed_post()
    monkeypatch.setattr(
        adapter, "publish",
        lambda prepared: {"ok": False, "error": "ambiguous_timeout"})
    res = pub.tick(now=NOW)                           # mock has no probe
    assert res["outcomes"] == {"failed": 1}
    t = cpl.get_post(post["id"])["post"]["targets"][0]
    assert t["status"] == "FAILED"
    assert "refusing blind retry" in t["error"]


# ── §7.2/§3.2 PARTIAL re-arm never re-posts a confirmed target ────────────────

def test_partial_rearm_never_reposts(monkeypatch):
    adapter = _mock_adapter()
    real_get = preg.get_adapter
    monkeypatch.setattr(preg, "get_adapter",
                        lambda name: None if name == "linkedin" else real_get(name))
    post = _armed_post(platforms=("mock", "linkedin"))
    res = pub.tick(now=NOW)
    assert res["outcomes"] == {"confirmed": 1, "failed": 1}
    got = cpl.get_post(post["id"])["post"]
    assert got["status"] == "PARTIAL"
    by = {t["platform"]: t for t in got["targets"]}
    first_pid = by["mock"]["platform_post_id"]
    assert by["mock"]["status"] == "CONFIRMED"
    rearmed = cpl.rearm_post(post["id"])
    assert rearmed["ok"], rearmed
    res2 = pub.tick(now=LATER)
    assert res2["outcomes"] == {"failed": 1}          # linkedin only re-ran
    by2 = {t["platform"]: t for t in cpl.get_post(post["id"])["post"]["targets"]}
    assert by2["mock"]["status"] == "CONFIRMED"
    assert by2["mock"]["platform_post_id"] == first_pid
    assert adapter.publish_calls == 1                 # the cardinal sin, avoided


# ── §6.7 recurrence cloning + §6.3 DST-correct timezone math ──────────────────

def _recurrence_clones_of(parent_id):
    posts = cpl.list_posts(source_kind="recurrence")["posts"]
    return [p for p in posts if (p.get("source") or {}).get("ref") == parent_id]


def test_recurrence_confirms_and_clones_next_occurrence():
    _mock_adapter()
    res = cpl.create_post(title="T", body="Daily digest body.",
                          platforms=["mock"],
                          schedule=cpl.new_schedule_config(
                              publish_at=PAST, tz="UTC", recurrence="daily"))
    post = cpl.schedule_post(res["post"]["id"])["post"]
    out = pub.tick(now=NOW)
    assert out["outcomes"] == {"confirmed": 1}
    assert cpl.get_post(post["id"])["post"]["status"] == "PUBLISHED"
    clones = _recurrence_clones_of(post["id"])
    assert len(clones) == 1
    clone = clones[0]
    assert clone["status"] == "SCHEDULED"
    # daily 09:00 UTC template; next occurrence after 2026-07-04T12:00Z:
    assert clone["schedule"]["publish_at"] == "2026-07-05T09:00:00Z"
    assert "vary the phrasing" in (clone["source"].get("freshness_hint") or "")
    pub.tick(now=NOW)                                 # idempotent per instant
    assert len(_recurrence_clones_of(post["id"])) == 1


def test_recurrence_dst_fall_back_keeps_local_hour():
    _mock_adapter()
    res = cpl.create_post(title="T", body="Morning post.", platforms=["mock"],
                          schedule=cpl.new_schedule_config(
                              publish_at="2026-10-31T14:00:00Z",   # 09:00 CDT
                              tz="America/Chicago", recurrence="daily"))
    post = cpl.schedule_post(res["post"]["id"])["post"]
    out = pub.tick(now="2026-10-31T14:30:00Z")
    assert out["outcomes"] == {"confirmed": 1}
    clones = _recurrence_clones_of(post["id"])
    assert len(clones) == 1
    # Nov 1 2026 = US DST end: 09:00 America/Chicago becomes CST → 15:00 UTC.
    # The wall clock holds; the UTC instant shifts by an hour (§6.3).
    assert clones[0]["schedule"]["publish_at"] == "2026-11-01T15:00:00Z"


# ── §6.8 conflict-detection helper ────────────────────────────────────────────

def test_detect_conflicts_same_platform_window():
    _mock_adapter()
    a = _armed_post(publish_at="2026-08-01T12:00:00Z")
    res = pub.detect_conflicts("mock", "2026-08-01T13:00:00Z", window_hours=2)
    assert res["ok"]
    assert any(c["post_id"] == a["id"] for c in res["conflicts"])
    far = pub.detect_conflicts("mock", "2026-08-02T13:00:00Z", window_hours=2)
    assert far["ok"] and far["conflicts"] == []
    other = pub.detect_conflicts("linkedin", "2026-08-01T13:00:00Z", window_hours=2)
    assert other["ok"] and other["conflicts"] == []   # cross-platform is fine


# ── scheduler 'once' trigger (§6.2 extension) ─────────────────────────────────

def test_scheduler_once_trigger_fires_exactly_once():
    from datetime import datetime as _dt
    from agent_friday.services import scheduler as sch
    now = _dt.now()
    rec = {"id": "sched_once_test", "enabled": True, "trigger": "once",
           "spec": {"at": now.timestamp() - 5}}
    assert sch._is_due(rec, now) is True
    assert sch._next_run_ts(rec, now) == rec["spec"]["at"]
    fired = dict(rec, last_run_ts=now.timestamp())    # dispatch marked it
    assert sch._is_due(fired, now) is False
    assert sch._next_run_ts(fired, now) is None
    future = dict(rec, spec={"at": now.timestamp() + 3600})
    assert sch._is_due(future, now) is False


# ── provenance.add_publication (§7.7.2) ───────────────────────────────────────

def test_provenance_add_publication_signs_and_chains(tmp_path, monkeypatch):
    from agent_friday.services import provenance as prov
    monkeypatch.setattr(prov, "PROVENANCE_DIR", tmp_path / "prov")
    monkeypatch.setattr(prov, "LEDGER_FILE", tmp_path / "prov" / "ledger.jsonl")
    res = prov.add_publication("sha256:" + "ab" * 32, {
        "platform": "mock", "post_url": "mock://post/1",
        "platform_post_id": "1", "target_id": "tgt_x"})
    assert res["ok"], res
    e = res["entry"]
    assert e["type"] == "publication" and e["platform"] == "mock"
    assert e["signature"]["alg"] == "ed25519" and e["signature"]["value"]
    lines = (tmp_path / "prov" / "ledger.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and '"publication"' in lines[0]


# ── review-confirmed regressions (findings C1/C2/C5/C6/C8) ────────────────────

def test_gate_scans_post_title_fallback(monkeypatch):
    """C1: youtube/federation_pub fall back to post.title when adapted_title
    is empty — the gates must scan that fallback, not an empty string."""
    adapter = _mock_adapter()
    res = cpl.create_post(title="tier2 SECRET title", body="a clean body",
                          platforms=["mock"],
                          schedule=cpl.new_schedule_config(publish_at=PAST))
    assert res["ok"], res
    cpl.schedule_post(res["post"]["id"])
    monkeypatch.setattr(pub, "_gate",
                        lambda text, provider, field:
                        "[HELD]" if "SECRET" in text else text)
    out = pub.tick(now=NOW)
    assert out["ok"], out
    t = cpl.get_post(res["post"]["id"])["post"]["targets"][0]
    assert t["status"] == "HELD"
    assert adapter.publish_calls == 0


def test_gate_scans_hashtags_and_tags(monkeypatch):
    """C2: hashtags/mentions/options.tags egress as text (youtube snippet
    tags, federation listing tags) — they must clear the gate too."""
    adapter = _mock_adapter()
    post = _armed_post()
    tid = post["targets"][0]["id"]
    upd = cpl.update_target(tid, {"hashtags": ["SECRETTAG"]})
    assert upd["ok"], upd
    monkeypatch.setattr(pub, "_gate",
                        lambda text, provider, field:
                        "[HELD]" if "SECRETTAG" in text else text)
    out = pub.tick(now=NOW)
    assert out["ok"], out
    assert cpl.get_target(tid)["target"]["status"] == "HELD"
    assert adapter.publish_calls == 0


def test_h3_hold_release_actually_publishes(monkeypatch):
    """C6: releasing an H3-held target publishes it — before the fix the
    publisher re-held it every tick and 'publish anyway' never published."""
    adapter = _mock_adapter()
    post = _armed_post()
    monkeypatch.setattr(pub, "_moderation_scan",
                        lambda text: {"ok": True, "blocked": True,
                                      "harm_level": "H3", "reason": "pii"})
    pub.tick(now=NOW)
    tid = cpl.get_post(post["id"])["post"]["targets"][0]["id"]
    assert cpl.get_target(tid)["target"]["status"] == "HELD"
    rel = cpl.release_held(post["id"], tid)
    assert rel["ok"], rel
    # wall-clock tick — release stamps publish_at=now (see the e2e release
    # test above); moderation STILL flags H3, but the release wins
    pub.tick()
    assert cpl.get_target(tid)["target"]["status"] == "CONFIRMED"
    assert adapter.publish_calls == 1
    events = [e.get("event") for e in cpl.read_publish_log()["entries"]]
    assert "release_override" in events


def test_hard_floor_never_releasable(monkeypatch):
    """C6 guard: options.released must NOT bypass the H1/H2/H4 harm floor."""
    adapter = _mock_adapter()
    post = _armed_post()
    tid = post["targets"][0]["id"]
    opts = dict(post["targets"][0].get("options") or {})
    opts["released"] = True
    assert cpl.update_target(tid, {"options": opts})["ok"]
    monkeypatch.setattr(pub, "_moderation_scan",
                        lambda text: {"ok": True, "blocked": True,
                                      "harm_level": "H1", "reason": "harm"})
    pub.tick(now=NOW)
    assert cpl.get_target(tid)["target"]["status"] == "FAILED"
    assert adapter.publish_calls == 0


def test_egress_divergence_release_publishes(monkeypatch):
    """C6: an egress-flagged target the user explicitly released publishes."""
    adapter = _mock_adapter()
    post = _armed_post()
    monkeypatch.setattr(pub, "_gate", lambda text, provider, field: "[FLAG]")
    pub.tick(now=NOW)
    tid = cpl.get_post(post["id"])["post"]["targets"][0]["id"]
    assert cpl.get_target(tid)["target"]["status"] == "HELD"
    assert cpl.release_held(post["id"], tid)["ok"]
    pub.tick()             # wall-clock: gate STILL diverges — release wins
    assert cpl.get_target(tid)["target"]["status"] == "CONFIRMED"
    assert adapter.publish_calls == 1


def test_native_decline_defers_instead_of_publishing_early(monkeypatch):
    """C8: an adapter that declines platform-side scheduling at prepare time
    (lead decayed below its minimum) must NOT be published early — the claim
    is refunded and the due-scan sends it at the real instant."""
    adapter = _mock_adapter()
    _armed_post(publish_at=LATER)
    claimed = cpl.claim_due_targets("2099-01-01T00:00:00Z")["targets"]
    assert len(claimed) == 1
    target = claimed[0]
    target["_native_delegate"] = True
    monkeypatch.setattr(adapter, "prepare",
                        lambda t, p: {"ok": True,
                                      "prepared": {"native_scheduled": False}})
    assert pub._run_target(target) == "deferred"
    assert adapter.publish_calls == 0
    assert cpl.get_target(target["id"])["target"]["status"] == "PENDING"


def test_confirm_respects_adapter_native_outcome(monkeypatch, notes):
    """C8: a publish the platform did NOT schedule must not be recorded or
    announced as 'Scheduled natively'."""
    adapter = _mock_adapter()
    _armed_post()
    target = cpl.claim_due_targets(NOW)["targets"][0]
    target["_native_delegate"] = True
    orig = adapter.publish
    monkeypatch.setattr(adapter, "publish",
                        lambda prepared: dict(orig(prepared),
                                              native_scheduled=False))
    assert pub._run_target(target) == "confirmed"
    t = cpl.get_target(target["id"])["target"]
    assert not (t.get("options") or {}).get("native_scheduled")
    assert any(title.startswith("✓ Published") for title, _b, _k in notes)
    assert not any("natively" in title for title, _b, _k in notes)


def test_recurrence_probe_immune_to_pagination():
    """C5: the idempotency probe must find an existing clone even when it
    lies beyond list_posts' LIMIT-50 SQL window — a miss forks the series."""
    _mock_adapter()
    parent = cpl.create_post(title="T", body="B", platforms=["mock"],
                             schedule=cpl.new_schedule_config(
                                 publish_at=PAST, recurrence="daily"))["post"]
    nxt = "2026-07-05T09:00:00Z"
    clone = cpl.create_post(title="T", body="B", platforms=["mock"],
                            schedule=cpl.new_schedule_config(publish_at=nxt),
                            source={"kind": "recurrence", "ref": parent["id"]})
    assert clone["ok"], clone
    for i in range(60):            # push the clone beyond the 50-row window
        cpl.create_post(title=f"noise{i}", body="x", platforms=["mock"])
    probe = cpl.find_recurrence_clone(parent["id"], nxt)
    assert probe["ok"] and probe["found"] is True
    assert cpl.find_recurrence_clone(
        parent["id"], "2030-01-01T00:00:00Z")["found"] is False
