"""Trusted outlets start at high trust without becoming Local news.

Red on main 2f88c152a: the only way to give an outlet a high starting trust was
the Local beat list (`news_local_sources`), which also fetched it as a Local
feed and labelled its stories Local. There was no setting, no control and no
seed for "trust this beat outlet, but it is not my local paper".
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday import source_trust_graph as stg
from agent_friday.services import news_engine as ne

ROOT = Path(__file__).resolve().parents[2]
TRUSTED = ["beat-outlet.example", "https://www.trade-journal.example/section"]
DOMAINS = ["beat-outlet.example", "trade-journal.example"]


def _settings(monkeypatch, **values):
    base = dict(core.DEFAULT_SETTINGS)
    base.update(values)
    monkeypatch.setattr(core, "_load_settings", lambda: dict(base))


def test_the_setting_ships_empty():
    assert core.DEFAULT_SETTINGS["news_trusted_outlets"] == []


def test_trusted_outlets_seed_high_like_the_builtin_list(monkeypatch, tmp_path):
    _settings(monkeypatch, news_trusted_outlets=TRUSTED, news_local_sources=[],
              news_local_area="")
    graph = stg.SourceTrustGraph(friday_dir=tmp_path)
    reference = graph.score_for("reuters.com")
    for domain in DOMAINS:
        assert graph.score_for(domain) == reference
        assert ne._trust_color_from_score(graph.score_for(domain)) == "green"
        prior = graph.explain(domain)["prior"]
        assert prior["value"] == 0.75 and "trusted outlets" in prior["based_on"]
    monkeypatch.setattr(ne, "_HAS_TRUST_GRAPHS", False)
    assert ne._trust_rating(DOMAINS[0], banned=set(), boosted=set()) == "green"
    assert ne._trust_rating(DOMAINS[0], banned={DOMAINS[0]}, boosted=set()) == "red"


def test_trusted_outlets_are_not_local_news(monkeypatch):
    _settings(monkeypatch, news_trusted_outlets=TRUSTED, news_local_sources=[],
              news_local_area="")
    assert ne._local_sources() == ()
    assert stg.local_beat_sources() == ()
    meta = ne.category_meta("Local")
    feeds = " ".join(str(f) for f in ((meta or {}).get("feeds") or []))
    for domain in DOMAINS:
        assert domain not in feeds


def test_an_unknown_outlet_still_starts_neutral(monkeypatch, tmp_path):
    _settings(monkeypatch, news_trusted_outlets=TRUSTED)
    graph = stg.SourceTrustGraph(friday_dir=tmp_path)
    assert graph.score_for("someone-else.example") == pytest.approx(0.5)


@pytest.mark.parametrize("junk", [None, 5, "not a domain", ["", None, "x"], {"a": 1}])
def test_malformed_values_never_raise(monkeypatch, junk, tmp_path):
    _settings(monkeypatch, news_trusted_outlets=junk)
    graph = stg.SourceTrustGraph(friday_dir=tmp_path)
    assert graph.score_for("x") == pytest.approx(0.5)
    assert graph.score_for("beat-outlet.example") == pytest.approx(0.5)
    assert stg.trusted_outlets() == ()


def test_the_setting_survives_a_save_and_reload(monkeypatch, tmp_path):
    """`_load_settings_raw` drops keys DEFAULT_SETTINGS does not declare."""
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    core._invalidate_settings_cache()
    try:
        core._save_settings({"news_trusted_outlets": TRUSTED})
        core._invalidate_settings_cache()
        raw = core._load_settings_raw()
        assert raw["news_trusted_outlets"] == TRUSTED
        assert stg.trusted_outlets() == tuple(DOMAINS)
        assert stg.local_beat_sources() == ()
    finally:
        core._invalidate_settings_cache()


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_control_sits_beside_the_local_outlets_list(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    start = text.index("function NewsLocalBeatSettings()")
    body = text[start:text.index("\nfunction NewsWS(", start)]
    assert "news_trusted_outlets:" in body
    assert "'aria-label': 'Trusted outlets'" in body
    assert "not added to Local news" in body
