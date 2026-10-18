"""Settings > Laya in the page: one tab, the same panel in both UI files, reachable by voice, honest controls."""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGES = ["index.html", "ui_parts/app.html"]
TITLES = ["What Laya does", "Laya's modes", "Laya's report card", "Replay test", "Confidence bars", "Coming next"]


def _panel(page: str) -> str:
    text = (ROOT / page).read_text(encoding="utf-8")
    a = text.index("function SettingsTabLaya(")
    return text[a:text.index("// End Laya tab", a)]


def test_both_pages_carry_the_same_laya_tab():
    assert _panel(PAGES[0]) == _panel(PAGES[1])


@pytest.mark.parametrize("page", PAGES)
def test_the_rail_opens_the_laya_tab(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    assert re.search(r"tab === 'laya' && /\*#__PURE__\*/React\.createElement\(SettingsTabLaya, null\)", text)


def test_every_section_is_titled_for_voice_in_order():
    titles = re.findall(r'createElement\(StSection, \{\s*title: "([^"]+)"', _panel("index.html"))
    assert titles == TITLES


def test_voice_finds_the_tab_and_its_sections(monkeypatch):
    from agent_friday.services import desktop_targets as dt
    monkeypatch.chdir(ROOT)
    assert dt.settings_parts().get("laya") == TITLES
    tabs = [{"id": "privacy", "label": "Privacy & Data"}, {"id": "laya", "label": "Laya"},
            {"id": "connections", "label": "Connections"}]
    monkeypatch.setattr(dt, "_manifest", lambda: {"workspaces": {"settings": {"sections": tabs}}})
    r = dt.resolve_settings(query="confidence bars")
    assert (r.get("target") or {}).get("tab") == "laya" and (r.get("target") or {}).get("section") == "Confidence bars"
    r = dt.resolve_settings(query="replay test")
    assert (r.get("target") or {}).get("tab") == "laya"


def test_the_tab_writes_only_through_its_own_routes():
    p = _panel("index.html")
    assert "'/api/laya/settings/' + path" in p and "post('bars'" in p and "post('replay'" in p
    assert "'/api/settings'" not in p, "a bar must never be saved through the generic settings save"


def test_the_bars_mode_offers_shadow_and_act_only_and_modes_are_read_only_chips():
    p = _panel("index.html")
    mode = p[p.index("id:'laya-bars-mode'"):]
    mode = mode[:mode.index("))),")]
    assert re.findall(r"E\('option', \{value:'([a-z]+)'\}", mode) == ["shadow", "act"]
    assert p.count("E('select'") == 2, "only the bars mode and the per-action bars are controls"
    modes = p[p.index('title: "Laya\'s modes"'):p.index('title: "Laya\'s report card"')]
    assert "E('select'" not in modes and "E('input'" not in modes


def test_recommended_bars_are_labelled_not_selected():
    p = _panel("index.html")
    assert "' (recommended)'" in p and "'Recommended: '" in p
    assert "selected:" not in p and "defaultValue" not in p
    # an unset bar shows the shipped value, never the recommendation
    assert "r.yours === null || r.yours === undefined ? ''" in p


def test_train_my_laya_is_a_disabled_unticked_placeholder():
    p = _panel("index.html")
    i = p.index("Train my Laya (not available yet)")
    box = p[p.rindex("E('input'", 0, i):i]
    assert "checked:false" in box and "disabled:true" in box
