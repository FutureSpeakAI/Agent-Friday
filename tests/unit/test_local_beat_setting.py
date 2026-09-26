"""The Local beat is the owner's setting, never a shipped city.

Invariants:
  * The shipped news code, trust seeds and DEFAULT_SETTINGS name no city and
    no city-specific outlet; `news_local_area` defaults to "" and
    `news_local_sources` to [].
  * Outlets the owner lists in `news_local_sources` are fetched for the Local
    beat and trusted exactly like the built-in high-trust outlets.
  * With nothing set there is no Local beat, nothing is trusted on its
    behalf, and nothing raises.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday import source_trust_graph as stg
from agent_friday.services import news_engine as ne

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_FILES = (
    ROOT / "src" / "agent_friday" / "services" / "news_engine.py",
    ROOT / "src" / "agent_friday" / "source_trust_graph.py",
)
# SHA-256 of the city and the three city-specific outlets that used to ship in
# the general trusted lists. Kept as digests so this public test does not
# itself name the city it guards against. None belongs in a shipped default.
FORBIDDEN_DIGESTS = {
    "c7c1319276e936c8d64f1d5ed80cd8a0cf54e6dea7b0125533eb4163e03a2c11",
    "f312bc11cd8dfc4da5d1af888cc5fc948552c16a5611a169789370b17f8fa97a",
    "713598fc4cdefc5aaf42df8ab8f5faacd0e18d8c2f247a95af77d5471a9b5389",
    "4e08dbd4d5b737646f4a50c4e5aca6563b63897471c29829b3801d19422a0e50",
}
_TOKEN = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+|[a-z]+")


def _forbidden_tokens(text):
    """Words and dotted hostnames in `text` whose digest is forbidden."""
    return sorted({t for t in _TOKEN.findall(text.lower())
                   if hashlib.sha256(t.encode()).hexdigest() in FORBIDDEN_DIGESTS})


AREA = "Springfield"
SOURCES = ["springfield-local.example", "https://www.springfield-gazette.example/news"]
DOMAINS = ["springfield-local.example", "springfield-gazette.example"]


def _settings(monkeypatch, **values):
    base = dict(core.DEFAULT_SETTINGS)
    base.update(values)
    monkeypatch.setattr(core, "_load_settings", lambda: dict(base))


@pytest.mark.parametrize("path", SHIPPED_FILES, ids=lambda p: p.name)
def test_shipped_news_code_names_no_city_or_city_outlet(path):
    hits = _forbidden_tokens(path.read_text(encoding="utf-8"))
    assert hits == [], f"{path.name} ships city-specific defaults"


def test_default_settings_carry_no_local_beat():
    assert core.DEFAULT_SETTINGS["news_local_area"] == ""
    assert core.DEFAULT_SETTINGS["news_local_sources"] == []
    assert _forbidden_tokens(json.dumps(core.DEFAULT_SETTINGS, default=str)) == []


def test_no_shipped_trust_list_contains_a_local_outlet():
    assert _forbidden_tokens(" ".join(ne._TRUSTED_DOMAINS)) == []
    assert _forbidden_tokens(" ".join(stg._SEED_HIGH)) == []


def test_the_digest_scan_can_fail():
    """A scan nobody has seen match anything is not evidence."""
    FORBIDDEN_DIGESTS.add(hashlib.sha256(b"springfield").hexdigest())
    try:
        assert _forbidden_tokens("x = 'Springfield'") == ["springfield"]
        assert _forbidden_tokens("feeds from springfield-local.example") == []
    finally:
        FORBIDDEN_DIGESTS.discard(hashlib.sha256(b"springfield").hexdigest())


def test_populated_local_beat_fetches_the_owners_outlets(monkeypatch):
    _settings(monkeypatch, news_local_area=AREA, news_local_sources=SOURCES)
    assert stg.local_beat_sources() == tuple(DOMAINS)
    meta = ne.category_meta("Local")
    assert meta["query"] == f"{AREA} local news today"
    for domain in DOMAINS:
        assert any(f"source:{domain}" in feed for feed in meta["feeds"]), meta["feeds"]


def test_local_outlets_alone_still_make_a_local_beat(monkeypatch):
    _settings(monkeypatch, news_local_area="", news_local_sources="springfield-local.example")
    meta = ne.category_meta("Local")
    assert meta["feeds"] and "source:springfield-local.example" in meta["feeds"][0]


def test_local_outlets_are_trusted_like_the_builtin_high_trust_set(monkeypatch, tmp_path):
    _settings(monkeypatch, news_local_area=AREA, news_local_sources=SOURCES)
    graph = stg.SourceTrustGraph(friday_dir=tmp_path)
    reference = graph.score_for("reuters.com")
    for domain in DOMAINS:
        assert stg._seed_for(domain) == stg._seed_for("reuters.com")
        assert graph.score_for(domain) == reference
        assert ne._trust_color_from_score(graph.score_for(domain)) == "green"
    monkeypatch.setattr(ne, "_HAS_TRUST_GRAPHS", False)
    for domain in DOMAINS:
        assert ne._trust_rating(domain, banned=set(), boosted=set()) == "green"
    # The owner's ban still wins over the local-beat trust.
    assert ne._trust_rating(DOMAINS[0], banned={DOMAINS[0]}, boosted=set()) == "red"


def test_empty_local_beat_means_no_local_section_and_no_trust(monkeypatch, tmp_path):
    _settings(monkeypatch, news_local_area="", news_local_sources=[])
    assert stg.local_beat_sources() == ()
    meta = ne.category_meta("Local")
    assert meta["feeds"] == [] and meta["query"] == ""
    assert stg._seed_for(DOMAINS[0]) != stg._seed_for("reuters.com")
    monkeypatch.setattr(ne, "_HAS_TRUST_GRAPHS", False)
    assert ne._trust_rating(DOMAINS[0], banned=set(), boosted=set()) == "yellow"


@pytest.mark.parametrize("junk", [None, 7, {"a": 1}, ["", "   ", None], "not a domain!"])
def test_malformed_local_sources_never_raise(monkeypatch, junk):
    _settings(monkeypatch, news_local_area="", news_local_sources=junk)
    assert isinstance(stg.local_beat_sources(), tuple)
    meta = ne.category_meta("Local")
    assert isinstance(meta["feeds"], list)


def test_the_setting_survives_a_save_and_reload(monkeypatch, tmp_path):
    """`_load_settings_raw` drops keys DEFAULT_SETTINGS does not declare; the
    Settings control would say "Saved" and revert without the declaration."""
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    core._invalidate_settings_cache()
    try:
        core._save_settings({"news_local_area": AREA, "news_local_sources": SOURCES})
        core._invalidate_settings_cache()
        raw = core._load_settings_raw()
        assert raw["news_local_area"] == AREA
        assert raw["news_local_sources"] == SOURCES
        assert stg.local_beat_sources() == tuple(DOMAINS)
    finally:
        core._invalidate_settings_cache()


def _component(text):
    start = text.index("function NewsLocalBeatSettings()")
    end = text.index("\nfunction NewsWS(", start)
    return text[start:end].replace("\r\n", "\n")


def test_both_ui_copies_carry_the_same_local_beat_control():
    """index.html is served; ui_parts/app.html is its hand-kept mirror. The
    control is defined once, identically, and rendered in each copy's
    Customize Briefing panel."""
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    assert _component(index) == _component(app)
    body = _component(index)
    assert "news_local_area:" in body and "news_local_sources:" in body
    for text, use in ((index, "React.createElement(NewsLocalBeatSettings, null)"),
                      (app, "<NewsLocalBeatSettings/>")):
        assert text.count(use) == 1
        panel = text.index("CUSTOMIZE BRIEFING")
        assert panel < text.index(use) < text.index("YOUR MEDIA DIET", panel)


def test_unreadable_settings_mean_no_local_beat(monkeypatch):
    def boom():
        raise RuntimeError("settings unavailable")
    monkeypatch.setattr(core, "_load_settings", boom)
    assert stg.local_beat_sources() == ()
    assert ne.category_meta("Local")["feeds"] == []
