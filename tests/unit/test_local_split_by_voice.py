"""The owner's split can be changed by voice or chat through set_setting (settings.models.quick_private_split).

Turning it on asks first (the usual setting card: nothing changes before the owner's own Yes); turning it off goes
straight back to the default. Either change re-seats the card through local_split.apply_mode.
"""
import pytest

from agent_friday.services import local_split as ls
from agent_friday.services import setting_proposals as sp


@pytest.fixture
def world(monkeypatch):
    st = {"settings": {"local_turn_split": "off"}, "applied": [], "held": []}
    monkeypatch.setattr(ls, "_settings", lambda s=None: st["settings"] if s is None else s)
    monkeypatch.setattr(ls, "_installed", lambda m: True)

    def apply_mode(mode):
        st["applied"].append(mode)
        st["settings"]["local_turn_split"] = mode
        return {"ok": True}
    monkeypatch.setattr(ls, "apply_mode", apply_mode)

    def hold(tool, inp, **kw):
        st["held"].append((tool, inp, kw))
        return None if sp._APPROVED.get() else "SETTING_NEEDS_YES: card raised"
    monkeypatch.setattr(sp, "hold", hold)
    return st


def test_turning_it_on_asks_first(world):
    out = sp.tool({"path": "settings.models.quick_private_split", "value": "on"})
    assert out.startswith("SETTING_NEEDS_YES")
    assert world["applied"] == []
    tool, inp, kw = world["held"][0]
    assert tool == "quick_private_split" and kw["run_as"] == "set_setting"
    assert kw["new"] == ls.LABELS[ls.MODE_SPLIT]


def test_the_yes_applies_it(world):
    tok = sp._APPROVED.set(True)
    try:
        out = sp.tool({"path": "settings.models.quick_private_split", "value": "on"})
    finally:
        sp._APPROVED.reset(tok)
    assert world["applied"] == [ls.MODE_SPLIT] and "FridayWeaver" in out


def test_turning_it_off_needs_no_card(world):
    world["settings"]["local_turn_split"] = ls.MODE_SPLIT
    out = sp.tool({"path": "settings.models.quick_private_split", "value": "off"})
    assert world["applied"] == [ls.MODE_OFF] and world["held"] == []
    assert "Off" in out or "off" in out


@pytest.mark.parametrize("word", ["fridayweaver", "split", "yes", "turn it on", "fridayweaver_quick_bonsai_private"])
def test_words_for_on(world, word):
    sp.tool({"path": "settings.models.quick_private_split", "value": word})
    assert world["held"][-1][2]["new"] == ls.LABELS[ls.MODE_SPLIT]


def test_unknown_value_is_refused(world):
    out = sp.tool({"path": "settings.models.quick_private_split", "value": "purple"})
    assert out.startswith("SETTING_FAIL") and world["applied"] == []


def test_already_set_is_said(world):
    world["settings"]["local_turn_split"] = ls.MODE_SPLIT
    out = sp.tool({"path": "settings.models.quick_private_split", "value": "on"})
    assert out.startswith("SETTING_UNCHANGED") and world["applied"] == []


def test_row_is_listed_for_the_model():
    assert any("quick_private_split" in p for p in sp.PATHS)
    assert sp.KEYS_OF["quick_private_split"] == ("local_turn_split",)
