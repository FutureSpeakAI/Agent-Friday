"""The federation is held back from this release behind one switch.

`held_features.federation` defaults to off. While it is off, nothing the user
can see or say offers the Marketplace, positrons (the economy) or federation:
no workspace, dock entry, command-palette item, Settings tab, Trust strip,
navigate target or content platform. While it is on, every one of those
surfaces is back exactly as it was.

Buying is outside that switch. A purchase is refused in every configuration of
this release, and a listing is never auto-accepted at its price.

The UI half is held statically over BOTH index.html (served) and
ui_parts/app.html (its hand-maintained mirror): each surface is drawn behind a
runtime check of the setting, and the two files gate the same surfaces.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
APP = ROOT / "ui_parts" / "app.html"
REGISTRY = ROOT / "static" / "workspace_registry.js"
UI_FILES = pytest.mark.parametrize("path", [INDEX, APP], ids=["index.html", "app.html"])


def _with_federation(monkeypatch, on):
    """Overlay held_features.federation on whatever the settings file says."""
    import agent_friday.core as core
    real = core._load_settings

    def _load():
        s = dict(real() or {})
        s["held_features"] = {"federation": bool(on)}
        return s
    monkeypatch.setattr(core, "_load_settings", _load)


# ── The setting ──────────────────────────────────────────────────────────────

def test_default_settings_hold_the_federation_off():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["held_features"] == {"federation": False}


def test_reader_is_off_by_default_and_on_only_when_set():
    from agent_friday.core import DEFAULT_SETTINGS
    from agent_friday.services import held_features
    assert held_features.enabled("federation", DEFAULT_SETTINGS) is False
    assert held_features.enabled("federation", {}) is False
    assert held_features.enabled("federation", {"held_features": {"federation": "yes"}}) is False
    assert held_features.enabled("federation", {"held_features": {"federation": True}}) is True


def test_reader_follows_the_saved_settings(monkeypatch):
    from agent_friday.services import held_features
    _with_federation(monkeypatch, False)
    assert held_features.enabled("federation") is False
    _with_federation(monkeypatch, True)
    assert held_features.enabled("federation") is True


# ── Buying is refused regardless ─────────────────────────────────────────────

@pytest.mark.parametrize("on", [False, True], ids=["off", "on"])
def test_purchase_intent_is_refused_in_every_configuration(monkeypatch, on):
    from agent_friday.services import marketplace as mkt
    _with_federation(monkeypatch, on)
    listing = mkt.create_listing(asset_id="held-test-asset", price_mpsi=10, title="t")
    assert listing and listing.get("id")
    r = mkt.purchase_intent(listing["id"], buyer_agent_id="buyer")
    assert r.get("ok") is False
    assert r.get("error") == "not_in_this_release"
    assert "invoice" not in r


@pytest.mark.parametrize("on", [False, True], ids=["off", "on"])
def test_complete_purchase_is_refused_in_every_configuration(monkeypatch, on):
    from agent_friday.services import marketplace as mkt
    _with_federation(monkeypatch, on)
    r = mkt.complete_purchase("inv", buyer_agent_id="buyer", payment_confirmed=True)
    assert r.get("ok") is False
    assert r.get("error") == "not_in_this_release"


def test_auto_accept_at_listing_price_is_forced_false():
    from agent_friday.services import marketplace as mkt
    assert mkt.DEFAULT_POLICY["selling"]["auto_accept_at_listing_price"] is False
    # A stored policy cannot turn it back on.
    mkt.update_policy({"selling": {"enabled": True, "auto_accept_at_listing_price": True}})
    assert mkt.get_policy()["selling"]["auto_accept_at_listing_price"] is False


# ── What Friday can name and reach (navigate tools, voice, prompts) ──────────

def test_registry_marks_marketplace_as_held_by_the_federation_switch():
    from agent_friday.services import workspace_registry as reg
    text = REGISTRY.read_text(encoding="utf-8")
    entry = reg.get("marketplace")
    assert entry is not None, "the workspace stays in the source"
    assert entry.get("held") == "federation"
    raw = json.loads(text[text.index("/*BEGIN JSON*/") + 14:text.index("/*END JSON*/")])
    held = [w["id"] for w in raw["workspaces"] if w.get("held")]
    assert held == ["marketplace"]


def test_off_no_navigate_target_names_the_marketplace(monkeypatch):
    from agent_friday.services import workspace_registry as reg
    from agent_friday.services.agent import _resolve_workspace
    _with_federation(monkeypatch, False)
    assert "marketplace" not in reg.ids()
    assert "marketplace" not in reg.tool_list().lower()
    assert "marketplace" not in reg.spoken_list().lower()
    assert reg.resolve("marketplace") is None
    assert reg.resolve("skill store") is None
    assert _resolve_workspace("marketplace") is None
    assert _resolve_workspace("the marketplace") is None
    # Everything else still resolves.
    assert reg.resolve("news") == "news"
    assert _resolve_workspace("settings") == "settings"


def test_on_the_marketplace_is_a_navigate_target_again(monkeypatch):
    from agent_friday.services import workspace_registry as reg
    from agent_friday.services.agent import _resolve_workspace
    _with_federation(monkeypatch, True)
    assert "marketplace" in reg.ids()
    assert "marketplace" in reg.tool_list().lower()
    assert reg.resolve("skill store") == "marketplace"
    assert _resolve_workspace("the marketplace") == "marketplace"


def test_voice_navigate_description_follows_the_switch(monkeypatch):
    from agent_friday.services.voice_engine import _navigate_tool_description
    _with_federation(monkeypatch, False)
    assert "marketplace" not in _navigate_tool_description("Workspaces: {workspace_ids}.").lower()
    _with_federation(monkeypatch, True)
    assert "marketplace" in _navigate_tool_description("Workspaces: {workspace_ids}.").lower()


# ── Publishing onto the federation as a content platform ─────────────────────

def test_off_the_federation_publisher_is_not_a_content_platform(monkeypatch):
    from agent_friday.services import platforms
    platforms._reset_for_tests()
    _with_federation(monkeypatch, False)
    try:
        assert platforms.get_adapter("federation") is None
        assert "federation_pub" not in platforms.status()["platforms"]
        assert "federation_pub" not in platforms.list_adapters()
    finally:
        platforms._reset_for_tests()


def test_on_the_federation_publisher_is_a_content_platform_again(monkeypatch):
    from agent_friday.services import platforms
    platforms._reset_for_tests()
    _with_federation(monkeypatch, True)
    try:
        assert "federation_pub" in platforms.status()["platforms"]
    finally:
        platforms._reset_for_tests()


# ── The UI, both files ───────────────────────────────────────────────────────

def _fn(src, head):
    start = src.index(head)
    end = src.index("\nfunction ", start + len(head))
    return src[start:end]


@UI_FILES
def test_ui_has_one_runtime_reader_of_the_switch(path):
    src = path.read_text(encoding="utf-8")
    body = _fn(src, "function fridayHeldOn(")
    assert "held_features" in body, "%s: fridayHeldOn does not read held_features" % path.name
    assert "function fridaySetHeldFeatures(" in src
    assert "function fridayWsHeld(" in src
    # App hands the loaded settings to the reader on every render.
    assert re.search(r"fridaySetHeldFeatures\(\s*agentSettings\.held_features\s*\)", src), (
        "%s: App never tells the UI what the switch says" % path.name)


@UI_FILES
def test_ui_dock_drops_held_workspaces(path):
    src = path.read_text(encoding="utf-8")
    groups = _fn(src, "function fridayDockGroups(")
    assert re.search(r"held\s*:\s*w\.held", groups), "%s: dock items lose the held mark" % path.name
    dock = _fn(src, "function dockArrangement(")
    assert "fridayWsHeld(" in dock, "%s: the dock still draws held workspaces" % path.name
    # The dock recomputes when the switch changes.
    m = re.search(r"const dockShape\s*=\s*React\.useMemo\((.*?)\);\n", src, flags=re.S)
    assert m and "held_features" in m.group(1), "%s: dockShape ignores the switch" % path.name


@UI_FILES
def test_ui_palette_and_open_refuse_held_workspaces(path):
    src = path.read_text(encoding="utf-8")
    m = re.search(r"const wsResults\s*=\s*WS\.filter\(([^\n]*)", src)
    assert m and "fridayWsHeld(" in m.group(1), "%s: the palette offers held workspaces" % path.name
    # openWs and toggle both pass through tabGo first; a held workspace stops there.
    m = re.search(r"const tabGo\s*=\s*rawId\s*=>\s*\{\s*if\s*\(\s*fridayWsHeld\(rawId\)\s*\)\s*return true;", src)
    assert m, "%s: a held workspace can still be opened" % path.name
    body = re.sub(r"\s+", "", src)
    assert "constopenWs=rawId=>{if(tabGo(rawId))return;" in body
    assert "consttoggle=id=>{if(tabGo(id))return;" in body


@UI_FILES
def test_ui_marketplace_workspace_renders_only_when_on(path):
    src = path.read_text(encoding="utf-8")
    m = re.search(r"marketplace\s*:\s*([^\n,]{0,120})", src[src.index("const wsMap"):])
    assert m and m.group(1).lstrip().startswith("fridayHeldOn('federation')"), (
        "%s: the Marketplace workspace renders without the switch" % path.name)


@UI_FILES
def test_ui_settings_tabs_follow_the_switch(path):
    src = path.read_text(encoding="utf-8")
    assert "SETTINGS_SHOW_HELD_FEATURES" not in src, (
        "%s: Federation/Economy still hang off a compile-time constant" % path.name)
    body = _fn(src, "function settingsShowHeldFeatures(")
    assert "fridayHeldOn('federation'" in body
    ws = _fn(src, "function SettingsWS(")
    for tab in ("Federation", "Economy"):
        for m in re.finditer(r"SettingsTab%s\b" % tab, ws):
            assert "settingsShowHeldFeatures(" in ws[max(0, m.start() - 200):m.start()], (
                "%s: %s tab renders without the switch" % (path.name, tab))
    assert re.search(r"settingsShowHeldFeatures\(\)\s*\?\s*SETTINGS_TABS_WITH_HELD", ws), (
        "%s: the Settings rail does not follow the switch" % path.name)


@UI_FILES
def test_ui_trust_federation_strip_follows_the_switch(path):
    src = path.read_text(encoding="utf-8")
    hits = [m.start() for m in re.finditer(r"fed-strip\"", src)]
    hits = [h for h in hits if "className" in src[h - 40:h]]
    assert hits, "%s: no Trust federation strip found" % path.name
    for h in hits:
        assert "fridayHeldOn('federation')" in src[max(0, h - 160):h], (
            "%s: the Trust tab's federation strip draws without the switch" % path.name)


@UI_FILES
def test_ui_priced_license_and_federation_platform_follow_the_switch(path):
    src = path.read_text(encoding="utf-8")
    picker = _fn(src, "function LicensePicker(")
    assert "licenseTermsShown(" in picker, "%s: positron pricing is offered while held" % path.name
    terms = _fn(src, "function licenseTermsShown(")
    assert "'priced'" in terms and "fridayHeldOn('federation')" in terms
    lists = re.findall(r"filter\(\s*p\s*=>\s*p\s*!==\s*'discord'[^)]*\)", src)
    assert len(lists) == 3, "%s: content platform lists moved (%d)" % (path.name, len(lists))
    for f in lists:
        assert "contentPlatformShown(p)" in f, (
            "%s: a content platform list offers the federation while held" % path.name)


def test_ui_both_files_gate_the_same_surfaces():
    def gates(p):
        s = p.read_text(encoding="utf-8")
        return {k: len(re.findall(re.escape(k), s)) > 0 for k in (
            "fridayHeldOn('federation')", "fridayWsHeld(", "settingsShowHeldFeatures(",
            "SETTINGS_TABS_WITH_HELD", "licenseTermsShown(", "contentPlatformShown(",
            "fridaySetHeldFeatures(")}
    a, b = gates(INDEX), gates(APP)
    assert a == b and all(a.values()), (a, b)


@UI_FILES
def test_the_finance_wallet_is_held_with_the_economy(path):
    """The Finance workspace drew the economy wallet whatever the switch said."""
    html = path.read_text(encoding="utf-8")
    for line in html.splitlines():
        if "'quickref'" in line.replace('"', "'") and "'overview'" in line.replace('"', "'"):
            assert "'wallet'" not in line.split("]")[0], \
                "the wallet view is listed unconditionally in the Finance tabs"
            assert "fridayHeldOn('federation')" in line
    for m in re.finditer(r"view\s*===\s*'wallet'", html):
        before = html[max(0, m.start() - 60):m.start()]
        assert "fridayHeldOn('federation')" in before, \
            "the Finance wallet panel renders without the held-features check"

