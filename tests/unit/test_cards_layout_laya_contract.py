"""The Laya contract for "hide the cards" / "show the cards" (settings.cards_layout).

The router rule, the tool's `cards` argument and the UI control come from the avatar package
(tests/unit/test_simple_cards.py holds those); this file holds the manifest side of that
entry: the verb row, its gate class and voice exposure, its phrasings against the router, the
UI-action fixture row and that it declares no job.
"""
import json
import pathlib

from agent_friday.services import agent
from agent_friday.services import laya_manifests as lm
from agent_friday.services import laya_router as lr

REPO = pathlib.Path(__file__).resolve().parents[2]
PHRASES = ["hide the cards", "show the cards"]


def _verb():
    return next(v for v in lm.verbs("settings") if v["action"] == "settings.cards_layout")


def test_the_manifest_row_is_a_router_rule_on_the_internal_chat_tray_tool():
    from agent_friday.governance import action_gate
    verb = _verb()
    assert verb["tool"] == "set_chat_tray" and verb["laya_tier"] == "router_rule"
    assert verb["gate"]["class"] == lm.gate_class("set_chat_tray") == "internal"
    assert verb["gate"]["ring"] == agent.TOOL_RINGS["set_chat_tray"]
    assert "set_chat_tray" in action_gate.INTERNAL_TOOLS
    assert verb["voice"] == {"live": True, "shared": False}
    assert not [j for j in lm.jobs("settings") if "cards" in j["id"]], "it makes no model call, so it declares no job"


def test_the_manifest_phrasings_are_the_ones_the_router_sends_to_the_chat_tray_tool():
    verb = _verb()
    assert verb["phrasings"] == PHRASES
    for phrase in verb["phrasings"]:
        route = lr._tier0(phrase)
        assert route is not None and route.tool == "set_chat_tray" and route.args["cards"] in ("hide", "show"), phrase


def test_the_ui_action_fixture_has_the_cards_row():
    fixture = json.loads((REPO / "tests" / "fixtures" / "laya_ws_ui_actions.json").read_text(encoding="utf-8"))
    row = next(a for a in fixture["workspaces"]["settings"] if a["id"] == "settings.cards_layout")
    assert row["audit_status"] == "new" and row["group"] == "settings"


def test_the_cards_verb_is_counted_in_the_settings_coverage_floor():
    floor = json.loads((REPO / "tests" / "fixtures" / "laya_coverage_floor.json").read_text(encoding="utf-8"))
    assert floor["workspaces"]["settings"]["laya"] >= 2
    assert lm.coverage("settings", [a["id"] for a in json.loads(
        (REPO / "tests" / "fixtures" / "laya_ws_ui_actions.json").read_text(encoding="utf-8"))["workspaces"]["settings"]])["laya"] >= 2
