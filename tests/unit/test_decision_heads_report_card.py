"""DM6: the decision heads' rows on Settings > Laya's report card.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, section 6.4 (2):
the nightly replay's results go to the Settings > Laya report card. The card
reads the head files and the newest nightly report, runs nothing, and shows
counts only. On the base branch there is no report_card() and no rows.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def dr(tmp_path, monkeypatch):
    from agent_friday.services import decision_heads, decision_replay
    if not hasattr(decision_replay, "report_card"):
        pytest.fail("decision_replay has no report_card")
    monkeypatch.setattr(decision_replay, "_friday_dir", lambda: tmp_path)
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FRIDAY_TRIAL", raising=False)
    decision_heads._reset_caches_for_tests()
    yield decision_replay
    decision_heads._reset_caches_for_tests()


def test_before_any_nightly_report_every_head_has_a_row_and_nothing_measured(dr):
    from agent_friday.services import decision_heads
    card = dr.report_card()
    assert card["night"] is None
    assert [r["id"] for r in card["rows"]] == decision_heads.head_ids()
    assert all(r["decisions"] is None and r["take_rate_pct"] is None for r in card["rows"])
    assert all(r["mode"] in ("off", "shadow") for r in card["rows"])          # nothing ships acting


def test_the_newest_nightly_report_fills_the_rows(dr):
    d = dr.bench_dir()
    d.mkdir(parents=True)
    old = {"night": "2026-10-28", "heads": {"loop.no_progress": {"n": 1, "by_set": {"shipped": {"take_rate": 0.0}}}}}
    new = {"night": "2026-10-29", "until": 1.0, "shortfall_s": 0, "heads": {"loop.no_progress": {
        "n": 6, "by_set": {"shipped": {"take_rate": 0.5, "verdict": {"pass": 2, "fail": 1, "uncertain": 3},
                                       "miss": 1, "wrong": 0, "ece": 0.1}},
        "latency_ms": {"p50": 1.0, "p95": 2.5}, "drift": {"take_rate_vs_week": 0.2, "flag": True}}}}
    (d / "2026-10-28.json").write_text(json.dumps(old), encoding="utf-8")
    (d / "2026-10-29.json").write_text(json.dumps(new), encoding="utf-8")
    (d / "2026-10-29-2.json").write_text(json.dumps(dict(new, heads={})), encoding="utf-8")   # a rerun is not read
    card = dr.report_card()
    row = next(r for r in card["rows"] if r["id"] == "loop.no_progress")
    assert card["night"] == "2026-10-29"
    # a percent, as the tab's pct() shows it: 0.5 of decisions is 50.0
    assert (row["decisions"], row["take_rate_pct"], row["pass"], row["fail"], row["uncertain"]) == (6, 50.0, 2, 1, 3)
    assert (row["miss"], row["wrong"], row["drift"], row["drift_flag"]) == (1, 0, 0.2, True)
    assert row["name"] == "Is a task stuck?"


def test_the_report_card_carries_the_rows_and_never_waits_on_them(dr, monkeypatch):
    from agent_friday.services import laya_settings
    assert laya_settings._decision_heads_card()["rows"]
    monkeypatch.setattr(dr, "report_card", lambda: (_ for _ in ()).throw(RuntimeError("broken")))
    assert laya_settings._decision_heads_card() is None
    src = Path(laya_settings.__file__).read_text(encoding="utf-8")
    assert '"decision_heads": _decision_heads_card()' in src


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_both_pages_show_the_decision_head_rows(page):
    html = (ROOT / page).read_text(encoding="utf-8")
    assert "const dh = rep.decision_heads;" in html
    assert "'data-laya-head': r.id" in html and 'title: "Her decision heads"' in html
    assert "pct(r.take_rate_pct)" in html and "pct(r.take_rate)" not in html


def test_a_corrupt_newest_report_falls_back_and_bad_shapes_do_not_break_the_card(dr):
    d = dr.bench_dir()
    d.mkdir(parents=True)
    (d / "2026-10-28.json").write_text(json.dumps({"night": "2026-10-28", "heads": {
        "loop.no_progress": {"n": 2, "by_set": ["not", "a", "dict"], "drift": 5}}}), encoding="utf-8")
    (d / "2026-10-29.json").write_text("{not json", encoding="utf-8")
    card = dr.report_card()
    row = next(r for r in card["rows"] if r["id"] == "loop.no_progress")
    assert card["night"] == "2026-10-28" and row["decisions"] == 2 and row["take_rate_pct"] is None
