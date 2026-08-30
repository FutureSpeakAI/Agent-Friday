"""The endpoints behind the Cost & Usage panel.

These routes have returned 200 continuously since 91411e9 while nothing
rendered them (see tests/unit/test_cost_panel_is_reachable.py). Now that a
panel reads them again, the shape it reads is a contract.

The three fields the restored panel adds over the original are asserted
explicitly, because they are the ones with teeth: cache_hit_rate says whether
the prompt-cache breakpoints are landing, and by_model is what turns "$1,190
this month" into "$679 of it is Opus" -- a number a person can act on.
"""
from __future__ import annotations

import json


def test_summary_exposes_the_fields_the_panel_renders(client):
    resp = client.get("/api/costs/summary?range=today")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    s = data["summary"]
    for field in ("total_usd", "total_calls", "input_tokens", "output_tokens",
                  "cache_read_tokens", "cache_write_tokens", "cache_hit_rate",
                  "by_provider", "by_workspace", "by_model", "by_kind"):
        assert field in s, "summary lost %r, which the panel renders" % field
    assert isinstance(s["by_model"], dict)
    assert 0.0 <= s["cache_hit_rate"] <= 1.0


def test_every_range_the_pills_offer_is_accepted(client):
    """The panel offers today / 7d / month. All three must work -- a pill
    that 500s is a pill that lies about the spend being zero."""
    for rng in ("today", "7d", "month"):
        resp = client.get("/api/costs/summary?range=%s" % rng)
        assert resp.status_code == 200, "range=%s failed" % rng
        assert resp.get_json()["summary"]["range"] == rng


def test_timeseries_returns_a_list_the_sparkline_can_plot(client):
    resp = client.get("/api/costs/timeseries?range=month")
    assert resp.status_code == 200
    series = resp.get_json()["series"]
    assert isinstance(series, list)
    for point in series:
        assert "date" in point and "usd" in point


def test_budget_reads_back_what_it_was_given(client):
    resp = client.get("/api/costs/budget")
    assert resp.status_code == 200
    budget = resp.get_json()["budget"]
    for field in ("daily", "monthly", "daily_enabled", "monthly_enabled"):
        assert field in budget


def test_arming_a_budget_does_not_reset_the_rest_of_settings(client):
    """The blast-radius test.

    set_budget() goes through _save_settings, which merges against the file on
    disk. That merge used to fail open -- an unreadable settings file left
    `existing` empty and CONVERTED the save into a factory reset, which is how
    the 2026-08-24 BOM incident became permanent. Arming a budget alert must
    never be the thing that costs someone their configuration.
    """
    from agent_friday.core import _load_settings_raw

    before = dict(_load_settings_raw())
    before_keys = set(before)
    assert len(before_keys) > 3, "fixture settings too thin to prove a merge"

    resp = client.post("/api/costs/budget",
                       data=json.dumps({"monthly_enabled": True,
                                        "monthly": 50}),
                       content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["budget"]["monthly_enabled"] is True

    after = _load_settings_raw()
    lost = before_keys - set(after)
    assert not lost, "arming a budget destroyed settings keys: %s" % sorted(lost)
    for key in before_keys:
        if key == "cost_budget":
            continue
        assert after[key] == before[key], (
            "arming a budget changed unrelated setting %r: %r -> %r"
            % (key, before[key], after[key]))


def test_budget_patch_is_partial(client):
    """The panel sends one field at a time (toggling Daily must not wipe the
    monthly limit sitting next to it)."""
    client.post("/api/costs/budget",
                data=json.dumps({"daily": 7.5, "monthly": 99.0}),
                content_type="application/json")
    client.post("/api/costs/budget",
                data=json.dumps({"daily_enabled": True}),
                content_type="application/json")
    budget = client.get("/api/costs/budget").get_json()["budget"]
    assert budget["daily_enabled"] is True
    assert budget["monthly"] == 99.0, "a partial patch clobbered monthly"
    assert budget["daily"] == 7.5, "a partial patch clobbered daily"
