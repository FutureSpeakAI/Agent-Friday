"""The hard spending cap must actually stop spend -- proven by driving REAL
recorded spend past the threshold through cost_meter's own DB and asserting
the real cloud choke points refuse the next call.

Stephen (2026-09-06): "A spending limit that reports itself as set and
doesn't stop anything is the worst instance of the placebo pattern in the
product ... The test has to prove the stop actually stops -- drive real
spend past the threshold and assert the work halts, not merely that a flag
flipped."

Two caps: the alert cap (default, unchanged, never blocks) and the hard
stop (opt-in, blocks cloud, never local). Both are asserted here so a
future change cannot quietly turn the alert cap into a stop or the stop
into an alert.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import cost_meter as cm
from agent_friday.services import spend_guard as sg


@pytest.fixture(autouse=True)
def _fresh(friday_dir, monkeypatch):
    cm.reset_for_tests()
    if cm.DB_PATH.exists():
        cm.DB_PATH.unlink()
    cm.reset_for_tests()
    sg.reset_for_tests()
    monkeypatch.setattr(sg, "HALT_LOG", friday_dir / "spend_halts.jsonl")
    if sg.HALT_LOG.exists():
        sg.HALT_LOG.unlink()          # the test home is shared across tests
    pushed = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw) or kw)
    # cost_meter's own alert path reads a different engine handle; silence it.
    monkeypatch.setattr(cm, "_push_budget_alert", lambda *a, **k: None)
    yield pushed
    cm.reset_for_tests()


def _settings(monkeypatch, **cost_budget):
    monkeypatch.setattr(cm, "_load_settings", lambda: {"cost_budget": cost_budget})


def _spend(usd: float, **attr):
    """Record a real cloud call with an authoritative cost into costs.db."""
    cm.record("anthropic", "claude-sonnet-5", 100, 50, cost_usd=usd, **attr)


# ── the stop actually stops ──────────────────────────────────────────────────

def test_real_spend_past_hard_cap_halts_the_next_cloud_call(monkeypatch, _fresh):
    from agent_friday.services import model_router as mr
    _settings(monkeypatch, hard_stop_monthly=1.0, hard_stop_monthly_enabled=True)

    _spend(0.60)
    # Below the cap: the real router choke point lets a cloud call through
    # to the gate (we stop it there by stubbing the gate itself).
    import agent_friday.services.egress_gate as eg
    monkeypatch.setattr(eg, "gate_operational", lambda: True)
    monkeypatch.setattr(eg, "seal_outbound", lambda payload, provider, **k: payload)
    assert mr._seal_or_block({"messages": []}, "anthropic") == {"messages": []}
    assert sg.tripped() is None

    _spend(0.60)   # 1.20 total -- crosses the $1.00 cap
    with pytest.raises(sg.SpendCapReached) as ei:
        mr._seal_or_block({"messages": []}, "anthropic")
    msg = str(ei.value)
    assert "$1.20" in msg and "$1.00" in msg and "monthly" in msg
    assert "raise the cap" in msg and "Local models" in msg

    # Loud: the trip notification fired at the crossing (from record()),
    # and the halt itself is both notified and written to the ledger.
    titles = [p["title"] for p in _fresh]
    assert any("Hard spending cap reached" in t for t in titles), titles
    assert any("Stopped by the hard spending cap" in t for t in titles), titles
    trip = next(p for p in _fresh if "Hard spending cap reached" in p["title"])
    assert trip["priority"] == "high" and "$1.20" in trip["body"] and "$1.00" in trip["body"]
    rows = [json.loads(l) for l in sg.HALT_LOG.read_text(encoding="utf-8").splitlines()]
    assert rows and rows[-1]["spend"] == 1.2 and rows[-1]["limit"] == 1.0
    assert "anthropic model call" in rows[-1]["what"]


def test_alert_only_cap_never_halts(monkeypatch, _fresh):
    """The default cap is the alert cap: it warns and work continues."""
    _settings(monkeypatch, monthly=1.0, monthly_enabled=True)
    _spend(5.00)
    sg.check("anthropic", what="a chat turn")          # no raise
    assert sg.tripped() is None
    assert not sg.HALT_LOG.exists()


def test_hard_stop_off_by_default(monkeypatch):
    _settings(monkeypatch)
    _spend(1000.0)
    sg.check("anthropic")
    assert sg.enabled() is False


def test_local_provider_is_never_halted(monkeypatch):
    _settings(monkeypatch, hard_stop_monthly=1.0, hard_stop_monthly_enabled=True)
    _spend(5.00)
    assert sg.tripped() is not None
    sg.check("ollama", what="a local call")             # no raise
    with pytest.raises(sg.SpendCapReached):
        sg.check("openai", what="a cloud call")


def test_switching_the_stop_off_resumes_cloud_work(monkeypatch):
    _settings(monkeypatch, hard_stop_daily=1.0, hard_stop_daily_enabled=True)
    _spend(2.00)
    with pytest.raises(sg.SpendCapReached):
        sg.check("anthropic")
    _settings(monkeypatch, hard_stop_daily=1.0, hard_stop_daily_enabled=False)
    sg.check("anthropic")                               # resumes
    _settings(monkeypatch, hard_stop_daily=3.0, hard_stop_daily_enabled=True)
    sg.check("anthropic")                               # raised cap resumes too


# ── the other choke points ───────────────────────────────────────────────────

def test_creative_generation_is_halted_with_a_legible_envelope(monkeypatch):
    from agent_friday.services import creative_engine as ce
    _settings(monkeypatch, hard_stop_monthly=1.0, hard_stop_monthly_enabled=True)
    _spend(1.50)
    out = ce.generate_image("a lighthouse at dusk")
    assert out["status"] == "blocked" and out.get("spend_cap") is True
    assert "Hard spending cap reached" in out["reason"] and "image generation" in out["reason"]
    out = ce.generate_video("a lighthouse at dusk")
    assert out["status"] == "blocked" and "video generation" in out["reason"]


def test_firecrawl_is_halted_before_the_wire(monkeypatch):
    import requests
    from agent_friday.services import firecrawl as fc
    _settings(monkeypatch, hard_stop_monthly=1.0, hard_stop_monthly_enabled=True)
    _spend(1.50)
    monkeypatch.setattr(fc, "api_key", lambda: "fake-firecrawl-key")  # pragma: allowlist secret
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("wire touched")))
    out = fc.search("weather in Denver")
    assert out["ok"] is False and "Hard spending cap" in out["error"]


# ── boundary rules ───────────────────────────────────────────────────────────

def test_scheduled_job_halt_is_attributed_and_notified_separately(monkeypatch, _fresh):
    """Rule 3: a 3am scheduled run fails fast at the guard, costs nothing,
    and gets its own notification naming the schedule."""
    _settings(monkeypatch, hard_stop_monthly=1.0, hard_stop_monthly_enabled=True)
    _spend(1.50)
    cm.push_attribution(schedule_id="job-intel", workspace="career")
    try:
        with pytest.raises(sg.SpendCapReached) as ei:
            sg.check("anthropic", what="anthropic model call")
    finally:
        cm.pop_attribution()
    assert "scheduled job 'job-intel'" in str(ei.value)
    halt = [p for p in _fresh if "Stopped by the hard spending cap" in p["title"]]
    assert halt and "job-intel" in halt[-1]["body"]
    assert halt[-1]["dedupe_key"].endswith(":job-intel")
    # And it spent nothing: the rolling total is unchanged by the refusal.
    assert cm._rolling_spend()[1] == 1.5


def test_halt_notification_is_once_per_thing_per_period(monkeypatch, _fresh):
    _settings(monkeypatch, hard_stop_monthly=1.0, hard_stop_monthly_enabled=True)
    _spend(1.50)
    for _ in range(3):
        with pytest.raises(sg.SpendCapReached):
            sg.check("anthropic", what="anthropic model call")
    halts = [p for p in _fresh if "Stopped by the hard spending cap" in p["title"]]
    assert len(halts) == 1
    rows = sg.HALT_LOG.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3, "every refusal is in the ledger even when the notification is deduped"


def test_status_reports_tripped_and_recent_halts(monkeypatch):
    _settings(monkeypatch, hard_stop_monthly=1.0, hard_stop_monthly_enabled=True)
    _spend(1.50)
    with pytest.raises(sg.SpendCapReached):
        sg.check("anthropic", what="anthropic model call")
    st = sg.status()
    assert st["enabled"] is True
    assert st["tripped"]["period"] == "monthly" and st["tripped"]["spend"] == 1.5
    assert st["recent_halts"][0]["what"].startswith("anthropic model call")
    assert "raise the cap" in st["resume"]


def test_guard_failure_does_not_halt_everything(monkeypatch):
    """A broken guard falls back to the alert cap, loudly, rather than
    becoming a stop nobody set."""
    _settings(monkeypatch, hard_stop_monthly=1.0, hard_stop_monthly_enabled=True)
    monkeypatch.setattr(cm, "_rolling_spend", lambda: (_ for _ in ()).throw(OSError("db locked")))
    sg.check("anthropic")                               # no raise


def test_budget_settings_round_trip_the_hard_stop_keys(monkeypatch):
    saved = {}
    from agent_friday import core
    monkeypatch.setattr(core, "_load_settings_raw", lambda: {"cost_budget": {"monthly": 50.0}})
    monkeypatch.setattr(core, "_save_settings", lambda patch: saved.update(patch))
    monkeypatch.setattr(cm, "_load_settings",
                        lambda: {"cost_budget": saved.get("cost_budget", {})})
    b = cm.set_budget({"hard_stop_monthly": 20, "hard_stop_monthly_enabled": True})
    assert saved["cost_budget"]["hard_stop_monthly"] == 20
    assert saved["cost_budget"]["monthly"] == 50.0, "the alert cap is untouched"
    assert b["hard_stop_monthly_enabled"] is True
