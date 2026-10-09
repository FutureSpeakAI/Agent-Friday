"""Settings rows by sentence: a row has a stable path, "take me to big mode" lands on it outlined, and a row Friday
changed shows who changed it, when, and an Undo that holds for thirty days."""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from agent_friday.services import desktop_targets as dt
from agent_friday.services import setting_proposals as sp

ROOT = Path(__file__).resolve().parents[2]


MANIFEST = {"workspaces": {"settings": {"sections": [
    {"id": "voice", "label": "Voice & Camera", "aliases": ["voice"]},
    {"id": "appearance", "label": "Appearance", "aliases": ["appearance"]}]}}}


@pytest.fixture(autouse=True)
def _manifest(monkeypatch):
    monkeypatch.setattr(dt, "_manifest", lambda: MANIFEST)


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_a_phrase_for_a_row_lands_on_its_tab_with_the_row_to_outline():
    r = dt.resolve_settings("big mode")
    assert r["ok"] is True, r
    t = r["target"]
    assert t["workspace"] == "settings" and t["tab"] == "voice" and t["row"] == "settings.accessibility.big_mode"
    assert r["verify"] == {"workspace": "settings", "key": "row", "value": "settings.accessibility.big_mode"}
    assert dt.resolve_settings("show my day")["target"]["tab"] == "appearance"
    assert dt.resolve_settings("hologram depth")["target"]["row"] == "settings.hologram.window.depth_strength"


def test_a_phrase_that_names_no_row_still_finds_a_tab_or_fails_as_before():
    assert dt.resolve_settings("voice")["target"]["tab"] == "voice"
    assert "row" not in dt.resolve_settings("voice")["target"]
    assert dt.resolve_settings("the flux capacitor")["ok"] is False


def test_every_row_the_navigator_promises_is_on_the_page_with_its_key():
    for page in ("index.html", "ui_parts/app.html"):
        text = _read(page)
        for path in sp.ROW_HOMES:
            if path.startswith("settings.hologram.window."):
                dial = path.rsplit(".", 1)[1]
                assert "sl('%s'" % dial in text, (page, path)
            else:
                assert path in text, (page, path)
        assert "settingKey: 'settings.hologram.window.' + key" in text, page
        assert '"data-st-key": settingKey || undefined' in text, page


def test_the_row_components_carry_the_key_and_the_provenance_line_in_both_pages():
    for page in ("index.html", "ui_parts/app.html"):
        text = _read(page)
        assert "window.FridaySettingRows.Provenance" in text, page
        assert "window.FridaySettingRows.highlight(t.row)" in text, page
        assert "friday:settings-changed" in text, page
        assert "row: shownRow" in text, page
    assert "settings_rows.js" in _read("index.html") and "settings_rows.js" in _read("ui_parts/styles_and_scene.html")


def test_the_outline_is_the_reticles_look_in_tokens_and_goes_on_the_next_input():
    js = _read("static/settings_rows.js")
    assert "outline:2px solid var(--fr-cyan)" in js
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", js), "tokens only"
    assert "animation" not in js, "no pulse"
    assert "prefers-reduced-motion" in js
    assert "['wheel', 'pointerdown', 'keydown', 'touchstart']" in js and "HIT_MS = 12000" in js


def test_the_changes_route_lists_a_row_and_the_owners_undo_works_once(tmp_path, monkeypatch, friday_dir):
    from agent_friday import server
    from agent_friday.core import _load_settings, _save_settings
    monkeypatch.setattr(sp, "_store", lambda: tmp_path / "setting_changes.json")
    _save_settings({"big_mode": "off"})
    sp.record_change("big_mode", label="big mode", old="off", new="on", snapshot={"big_mode": "off"}, by="Friday, by a proposal you accepted")
    _save_settings({"big_mode": "on"})
    server.app.config["TESTING"] = True
    c = server.app.test_client()
    d = c.get("/api/settings/changes?path=settings.accessibility.big_mode&limit=1").get_json()
    row = d["changes"][0]
    assert row["undoable"] is True and "snapshot" not in row and row["by"].startswith("Friday")
    r = c.post("/api/settings/changes/%s/undo" % row["id"])
    assert r.status_code == 200 and _load_settings()["big_mode"] == "off"
    again = c.post("/api/settings/changes/%s/undo" % row["id"])
    assert again.status_code == 409 and "already undone" in again.get_json()["text"]
