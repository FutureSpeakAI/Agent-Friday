"""The dock's workspaces by sentence: set_setting shows, hides and lists them.

A workspace hidden from the dock (Settings > Dock) could not be brought back by asking Friday: the settings
tool had no dock row. Now settings.display.dock.<workspace> takes show or hide, as a diff the owner says
Yes to like every other row, and op=list says what the dock shows and hides. Workspace names are checked
against the registry; a name that is no workspace is refused with the ones that are. The page's dock
reads the change when it is made (settings_changed with the dock's path), not on the next reload.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_friday.core import _load_settings, _save_settings
from agent_friday.governance import action_gate as ag
from agent_friday.services import agent, approvals as ap, desktop_bus
from agent_friday.services import setting_proposals as sp
from agent_friday.services import workspace_registry as wr

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def world(tmp_path, monkeypatch, friday_dir):
    from agent_friday.services import dissent_gate as dg
    desktop_bus.reset()
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(sp, "_store", lambda: tmp_path / "setting_changes.json")
    told = []
    real_tell = sp._tell_pages
    monkeypatch.setattr(sp, "_tell_pages", lambda path: (told.append(path), real_tell(path)))
    # A dock the owner arranged by hand, with Trust taken out of it.
    ids = [w["id"] for w in wr.workspaces()]
    # These tests hold the card path: the owner chose "ask first" for dock changes by asking (the default is
    # done at once with an Undo: tests/unit/test_owner_choices.py).
    _save_settings({"dock_custom": {"order": ids, "hidden": ["trust"]}, "show_all_workspaces": True,
                    "dock_by_asking": "ask_first"})
    yield told
    _save_settings({"dock_by_asking": "apply_with_undo"})
    desktop_bus.reset()


def _card():
    rows = [r for r in ap.list_approvals(kind="governed_action") if (r.get("payload") or {}).get("handler") == sp.HANDLER]
    rows.sort(key=lambda r: (r.get("status") == "pending", r.get("created_at") or 0))
    return rows[-1] if rows else None


def _set(path, value=None, **kw):
    inp = {"path": path}
    if value is not None:
        inp["value"] = value
    inp.update(kw)
    return agent._tool_set_setting(inp)


def _visible():
    return sp.dock_state(_load_settings())["visible"]


def test_showing_a_hidden_workspace_waits_for_the_yes_then_the_dock_shows_it(world):
    assert "trust" not in _visible()
    out = _set("settings.display.dock.trust", "show")
    assert out.startswith("SETTING_NEEDS_YES"), out
    assert "hidden → shown" in out and "Nothing has changed" in out
    assert "trust" in _load_settings()["dock_custom"]["hidden"]
    card = _card()
    assert card["status"] == "pending" and "dock" in card["title"]
    ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner")
    assert "trust" in _visible() and "trust" not in _load_settings()["dock_custom"]["hidden"]
    assert any(p.startswith("settings.display.dock") for p in world), world
    rows = sp.changes("settings.display.dock")
    assert len(rows) == 1 and (rows[0]["old"], rows[0]["new"]) == ("hidden", "shown") and rows[0]["undoable"]
    # The row's own Undo puts the dock back as it was.
    assert sp.undo(path="settings.display.dock")["ok"] is True and "trust" not in _visible()


def test_hiding_takes_one_workspace_out_and_leaves_the_rest(world):
    before = _visible()
    assert "finance" in before
    assert _set("settings.display.dock.finance", "hide").startswith("SETTING_NEEDS_YES")
    ap.decide_with_outcome(_card()["approval_id"], "approve", decided_by="owner")
    assert _visible() == [i for i in before if i != "finance"]


def test_a_workspace_is_named_by_its_label_or_alias_and_the_value_may_carry_it(world):
    out = _set("settings.display.dock", "show the trust graph")
    assert out.startswith("SETTING_NEEDS_YES") and "Trust" in out, out
    ap.decide_with_outcome(_card()["approval_id"], "approve", decided_by="owner")
    assert "trust" in _visible()


def test_an_unknown_workspace_is_refused_with_the_ones_that_exist(world):
    out = _set("settings.display.dock.nonsense", "show")
    assert out.startswith("SETTING_FAIL") and "nonsense" in out
    for wid in ("trust", "finance", "settings"):
        assert wid in out
    assert _card() is None


def test_already_shown_raises_no_card(world):
    out = _set("settings.display.dock.finance", "show")
    assert out.startswith("SETTING_UNCHANGED") and _card() is None


def test_list_says_what_the_dock_shows_and_hides_and_changes_nothing(world):
    before = _load_settings()["dock_custom"]
    out = _set("settings.display.dock", op="list")
    assert out.startswith("DOCK:")
    shown, _, hidden = out.partition("Hidden:")
    assert "Trust (trust)" in hidden and "Finance (finance)" in shown
    assert _load_settings()["dock_custom"] == before and _card() is None


def test_an_unarranged_dock_is_seeded_from_what_it_shows_so_one_show_reveals_one_workspace(world):
    # Never arranged, show_all off: the dock shows the core workspaces only.
    _save_settings({"dock_custom": {"order": [], "hidden": []}, "show_all_workspaces": False})
    before = _visible()
    assert "trust" not in before and before == [w["id"] for w in wr.workspaces() if w.get("core")]
    _set("settings.display.dock.trust", "show")
    ap.decide_with_outcome(_card()["approval_id"], "approve", decided_by="owner")
    assert sorted(_visible()) == sorted(before + ["trust"])


def test_the_folded_ids_are_the_pages():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    block = html[html.index("const WS_ALIASES = {"):]
    block = block[: block.index("\n};")]
    page = dict(re.findall(r"(\w+): \{\s*workspace: '(\w+)'", block))
    assert page == sp.DOCK_FOLDED


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_dock_reads_a_change_friday_made_without_a_reload(page):
    src = (ROOT / page).read_text(encoding="utf-8")
    # The App (which draws the dock from agentSettings.dock_custom) and the Dock panel both listen.
    hits = [m.start() for m in re.finditer(r"settings\.display\.dock'\)\s*!?==", src)]
    assert len(hits) >= 2, "the App and the Dock panel each re-read the dock on settings_changed"
    app = src[src.index("window.fridaySaveDock"):]
    app = app[: 1500]
    assert "friday:settings-changed" in app and "dock_custom" in app


def test_voice_has_the_same_dock_row_as_chat():
    from agent_friday.services import voice_engine as ve
    assert "set_setting" in ve._VOICE_SHARED_TOOLS
    desc = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "set_setting")["description"]
    assert "display.dock" in desc


def test_the_settings_manifest_names_the_dock_row():
    m = json.loads((ROOT / "src/agent_friday/resources/laya_manifests/settings.json").read_text(encoding="utf-8"))
    verb = next(v for v in m["verbs"] if v["tool"] == "set_setting")
    assert "dock" in verb["ui"]["control"] and any("dock" in p for p in verb["phrasings"])
