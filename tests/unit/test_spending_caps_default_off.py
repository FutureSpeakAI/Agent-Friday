"""Spending caps ship off; they are asked about in the first-run setup
conversation (services/spending_caps.py gets them ready to be asked).

On origin/main two caps were on out of the box: the daily creation ceiling
($0.50, so the daily creation stopped choosing images, music or a film once
the day's spend passed it) and the orchestrator's per-workspace budgets
(1000ψ a month, 100ψ a task). Now every cap is off until the owner sets it,
None or 0 is no cap wherever it is read, and a cap the owner sets applies.

Since north-star A17 (2026-10-21) one exception: a NEW install starts with
the daily cap on paid cloud spend on (tests/unit/test_daily_spending_cap_on.py).
The tests here use the shared test home, an existing install.
"""
from __future__ import annotations

import json
import uuid

from agent_friday import core


def _fresh_settings(extra: dict | None = None) -> None:
    """A settings.json that never set a cap: every cap comes from the defaults."""
    core.FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    core.SETTINGS_FILE.write_text(json.dumps(dict(extra or {"agent_name": "TEST AGENT"})),
                                  encoding="utf-8")
    core._invalidate_settings_cache()


def _ws(tag: str) -> str:
    return "caps-off-%s-%s" % (tag, uuid.uuid4().hex[:8])


def _spent_today(monkeypatch, usd: float) -> None:
    from agent_friday.services import cost_meter
    monkeypatch.setattr(cost_meter, "_rolling_spend", lambda *a, **k: (usd, usd))


# ── off by default, through the readers that enforce them ───────────────────

def test_the_daily_creation_has_no_ceiling_by_default(friday_dir, monkeypatch):
    from agent_friday.services import creations
    _fresh_settings()
    _spent_today(monkeypatch, 0.60)
    remaining = creations._daily_budget_remaining()
    assert remaining > 100, (
        "with no ceiling set, $0.60 spent today must not use up a daily creation "
        "budget (got %r left)" % remaining)
    modes = [m for m, _ in creations._candidate_modes(remaining)]
    for expensive in ("image", "music-clip", "short-production"):
        assert expensive in modes, "%s was left out with no cap set" % expensive


def test_a_workspace_has_no_task_budget_by_default(friday_dir):
    from agent_friday.services import budget_enforcer as be
    _fresh_settings()
    ws = _ws("default")
    policy = be.get_policy(ws)
    assert policy["monthly_cap_mψ"] == 0 and policy["per_task_cap_mψ"] == 0
    # More than the old 100ψ per-task and 1000ψ monthly defaults.
    assert be.reserve_budget(ws, 2_000_000) is True
    assert be.check_remaining(ws) is None
    status = be.budget_status(ws)
    assert status["capped"] is False and status["remaining_mψ"] is None
    assert status["no_cap_label"] == "No cap (ask me during setup)"


def test_every_registered_cap_is_off_for_an_install_that_never_set_one(friday_dir):
    """An install whose settings.json never set a cap (the shared test home is
    an existing install). A NEW install starts with the daily cap on
    (north-star A17): tests/unit/test_daily_spending_cap_on.py."""
    from agent_friday.services import spending_caps as sc
    _fresh_settings()
    s = core._load_settings()
    assert sc.CAPS, "the registry is empty"
    for cap in sc.CAPS:
        for field in ("key", "label", "unit", "scope", "suggested", "question", "reader"):
            assert cap.get(field) not in (None, ""), (cap.get("key"), field)
        assert cap["get"](s) is None, "%s ships on" % cap["key"]
    assert all(row["display"] == sc.NO_CAP_LABEL for row in sc.current(s))
    assert s["spending_caps"]["asked"] is False
    assert core.DEFAULT_SETTINGS["daily_creation_budget_usd"] in (None, 0)


# ── ready for the setup conversation ────────────────────────────────────────

def test_setup_questions_lists_every_unanswered_cap_in_plain_words(friday_dir):
    from agent_friday.services import spending_caps as sc
    _fresh_settings()
    qs = sc.setup_questions()
    assert [q["key"] for q in qs] == [c["key"] for c in sc.CAPS]
    by_key = {q["key"]: q for q in qs}
    creation = by_key["daily_creation"]["question"]
    assert "$0.50" in creation and "Do you want" in creation
    assert "{" not in creation, "the question was not filled in"
    for q in qs:
        assert q["suggested"] and q["unit"] and q["question"].endswith((".", "?"))


def test_an_answered_cap_is_not_asked_again_even_when_the_answer_is_no_cap(friday_dir):
    from agent_friday.services import spending_caps as sc
    _fresh_settings()
    res = sc.record_answer("daily_creation", None)
    assert res["ok"] and res["value"] is None and res["display"] == sc.NO_CAP_LABEL
    keys = [q["key"] for q in sc.setup_questions()]
    assert "daily_creation" not in keys
    assert core._load_settings().get("daily_creation_budget_usd") in (None, 0)


def test_an_unknown_cap_is_refused_with_the_known_ones_named(friday_dir):
    from agent_friday.services import spending_caps as sc
    _fresh_settings()
    res = sc.record_answer("nonesuch", 1.0)
    assert res["ok"] is False and "daily_creation" in res["error"]


# ── once the owner sets one, it applies ─────────────────────────────────────

def test_a_creation_ceiling_the_owner_sets_applies(friday_dir, monkeypatch):
    from agent_friday.services import creations, spending_caps as sc
    _fresh_settings()
    assert sc.record_answer("daily_creation", 0.50)["ok"]
    _spent_today(monkeypatch, 0.60)
    remaining = creations._daily_budget_remaining()
    assert remaining == 0.0
    modes = [m for m, _ in creations._candidate_modes(remaining)]
    assert "image" not in modes and "short-production" not in modes
    assert "daily_creation" not in [q["key"] for q in sc.setup_questions()]


def test_a_workspace_budget_the_owner_sets_applies(friday_dir):
    from agent_friday.services import budget_enforcer as be, spending_caps as sc
    _fresh_settings()
    assert sc.record_answer("workspace_monthly", 10_000)["ok"]
    ws = _ws("owner")
    assert be.get_policy(ws)["monthly_cap_mψ"] == 10_000
    assert be.reserve_budget(ws, 6_000) is True
    assert be.reserve_budget(ws, 6_000) is False
    assert be.budget_status(ws)["capped"] is True


def test_a_cloud_stop_the_owner_sets_turns_on_and_keeps_the_rest_of_cost_budget(friday_dir):
    from agent_friday.services import spending_caps as sc
    _fresh_settings({"cost_budget": {"daily": 7.0, "monthly": 70.0,
                                     "daily_enabled": True, "loop_guard_enabled": False}})
    assert sc.record_answer("cloud_daily_stop", 5)["ok"]
    cb = core._load_settings()["cost_budget"]
    assert cb["hard_stop_daily"] == 5.0 and cb["hard_stop_daily_enabled"] is True
    # The alert figures and the loop guard the owner set are untouched.
    assert cb["daily"] == 7.0 and cb["daily_enabled"] is True
    assert cb["loop_guard_enabled"] is False
    from agent_friday.services import spend_guard
    assert spend_guard.enabled() is True


# ── installs from before caps shipped off: a one-time, logged migration ─────
#
# settings.json was written out with every default, so an existing install
# holds "daily_creation_budget_usd": 0.5 that nobody chose; budgets.db can hold
# policy rows at the old 1,000,000 / 100,000 mψ the panel was pre-filled with.

def test_an_old_install_loses_the_shipped_creation_ceiling_once_and_is_asked(
        friday_dir, monkeypatch, caplog):
    import logging
    from agent_friday.services import spending_caps as sc
    monkeypatch.setattr(core, "_CAPS_MIGRATION_TRIED", [False], raising=False)
    _fresh_settings({"agent_name": "TEST AGENT", "daily_creation_budget_usd": 0.5})

    with caplog.at_level(logging.INFO, logger="friday.spending_caps"):
        s = core._load_settings()
    assert s["daily_creation_budget_usd"] is None, \
        "the old shipped $0.50 must not stay on after the update"
    assert "daily_creation" in [q["key"] for q in sc.setup_questions()]
    assert "one-time" in caplog.text.lower() and "0.50" in caplog.text

    on_disk = json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert on_disk["daily_creation_budget_usd"] is None
    assert on_disk["spending_caps"].get(sc.MIGRATION_STAMP)

    # Done means done: a $0.50 the owner sets afterwards is theirs.
    core._save_settings({"daily_creation_budget_usd": 0.5})
    core._invalidate_settings_cache()
    assert core._load_settings()["daily_creation_budget_usd"] == 0.5


def test_the_migration_stamp_is_one_value_until_it_is_saved(friday_dir, monkeypatch):
    """The persist is tried once per process. When it has already been tried
    (another settings file earlier in this process, or a write that failed),
    every read applies the migration in memory again -- and must carry the
    same stamp each time, so an unrelated save does not change the block."""
    from datetime import datetime as _real_dt, timedelta, timezone
    from agent_friday.services import spending_caps as sc
    monkeypatch.setattr(core, "_CAPS_MIGRATION_TRIED", [True], raising=False)
    tick = [0]

    class _Clock(_real_dt):
        @classmethod
        def now(cls, tz=None):
            tick[0] += 1
            return _real_dt(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=tick[0])

    monkeypatch.setattr(sc, "datetime", _Clock)
    monkeypatch.setattr(sc, "_PROCESS_STAMP", [], raising=False)
    _fresh_settings({"agent_name": "TEST AGENT", "daily_creation_budget_usd": 0.5})

    first = core._load_settings_raw()["spending_caps"].get(sc.MIGRATION_STAMP)
    core._invalidate_settings_cache()
    second = core._load_settings_raw()["spending_caps"].get(sc.MIGRATION_STAMP)
    assert first and first == second, "each read re-stamped the migration: %r -> %r" % (first, second)
    core._save_settings({"agent_name": "TEST AGENT 2"})
    core._invalidate_settings_cache()
    assert core._load_settings_raw()["spending_caps"].get(sc.MIGRATION_STAMP) == first

    # A stamp already stored is never replaced.
    _fresh_settings({"agent_name": "TEST AGENT",
                     "spending_caps": {sc.MIGRATION_STAMP: "2025-01-01T00:00:00+00:00"}})
    assert core._load_settings_raw()["spending_caps"][sc.MIGRATION_STAMP] == \
        "2025-01-01T00:00:00+00:00"


def test_an_answered_creation_ceiling_of_fifty_cents_is_kept(friday_dir, monkeypatch):
    from agent_friday.services import spending_caps as sc
    monkeypatch.setattr(core, "_CAPS_MIGRATION_TRIED", [False], raising=False)
    _fresh_settings({"agent_name": "TEST AGENT", "daily_creation_budget_usd": 0.5,
                     "spending_caps": {"answered": {"daily_creation": "2026-01-01T00:00:00+00:00"}}})
    s = core._load_settings()
    assert s["daily_creation_budget_usd"] == 0.5
    assert "daily_creation" not in [q["key"] for q in sc.setup_questions()]
    assert sc.migrate_settings(dict(s)) is None, "the stamp must stop a second run"


def test_old_default_budget_rows_are_cleared_once_and_chosen_ones_kept(
        friday_dir, tmp_path, monkeypatch, caplog):
    import logging
    import sqlite3
    from agent_friday.services import budget_enforcer as be, spending_caps as sc
    _fresh_settings()
    db = tmp_path / "budgets.db"
    # budgets.db as the previous release left it (its schema, its rows).
    with sqlite3.connect(str(db)) as c:
        c.executescript(
            "CREATE TABLE budget_policies (workspace TEXT PRIMARY KEY, "
            "monthly_cap_mψ INTEGER DEFAULT 1000000, per_task_cap_mψ INTEGER DEFAULT 100000, "
            "warn_pct INTEGER DEFAULT 80, updated_at TEXT);")
        c.executemany("INSERT INTO budget_policies VALUES (?,?,?,?,?)", [
            ("ws-defaulted", 1_000_000, 100_000, 70, "2026-01-01T00:00:00+00:00"),
            ("ws-chosen-task", 1_000_000, 50_000, 80, "2026-01-01T00:00:00+00:00"),
            ("ws-chosen-month", 500_000, 100_000, 80, "2026-01-01T00:00:00+00:00")])
    monkeypatch.setattr(be, "DB_PATH", db)
    monkeypatch.setattr(be, "_SCHEMA_DONE", False)

    with caplog.at_level(logging.INFO, logger="friday.budget"):
        p = be.get_policy("ws-defaulted")
    assert (p["monthly_cap_mψ"], p["per_task_cap_mψ"]) == (0, 0), \
        "a row at exactly the old shipped caps must not keep enforcing them"
    assert p["warn_pct"] == 70
    assert be.reserve_budget("ws-defaulted", 2_000_000) is True
    assert "ws-defaulted" in caplog.text and "one-time" in caplog.text.lower()
    assert be.get_policy("ws-chosen-task")["per_task_cap_mψ"] == 50_000
    assert be.get_policy("ws-chosen-task")["monthly_cap_mψ"] == 1_000_000
    assert be.get_policy("ws-chosen-month")["monthly_cap_mψ"] == 500_000

    # The cleared row follows the owner's answer in the setup conversation.
    assert sc.record_answer("workspace_monthly", 10_000)["ok"]
    assert be.get_policy("ws-defaulted")["monthly_cap_mψ"] == 10_000

    # Never again: a policy at those figures saved now, then a new process.
    be.set_policy("ws-later", monthly_cap_mψ=1_000_000, per_task_cap_mψ=100_000)
    monkeypatch.setattr(be, "_SCHEMA_DONE", False)
    assert be.get_policy("ws-later")["monthly_cap_mψ"] == 1_000_000
    with sqlite3.connect(str(db)) as c:
        assert c.execute("SELECT COUNT(*) FROM budget_meta WHERE key=?",
                         (be.MIGRATION_KEY,)).fetchone()[0] == 1
