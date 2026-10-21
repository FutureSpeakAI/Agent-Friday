"""North-star A17: a daily spending cap is on by default.

A new install starts with a modest daily cap on everything sent to paid cloud
services; the person can change it at setup and in Settings > Spending; it is
shown in plain words; work that reaches it pauses with a card. An install from
before A17 keeps the cap it had, and is shown one card explaining the new cap
with a press to set it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import spending_caps as sc

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def real_settings(tmp_path, monkeypatch):
    """A temp home read and written through the real settings loader."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setitem(core.NETWORK_STATE, "status", "online")
    monkeypatch.setattr(core, "_CAPS_MIGRATION_TRIED", [False])
    core._invalidate_settings_cache()
    yield core
    core._invalidate_settings_cache()


def _existing(core, stored: dict) -> dict:
    """An install from before A17: a settings.json already on disk."""
    core.SETTINGS_FILE.write_text(json.dumps(dict({"agent_name": "TEST AGENT"}, **stored)),
                                  encoding="utf-8")
    core._invalidate_settings_cache()
    return core._load_settings()


def _on_disk(core) -> dict:
    return json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8"))


# ── a new install: on, modest, in plain words ────────────────────────────────

def _card(core):
    return getattr(core, "install_choices_state", lambda: {})().get("daily_cap_card")


def _push_card():
    fn = getattr(sc, "push_daily_cap_card_if_pending", None)
    return bool(fn()) if fn else False


def test_a_new_install_starts_with_the_daily_cap_on(real_settings):
    core = real_settings
    s = core._load_settings()
    cb = _on_disk(core)["cost_budget"]
    assert cb["hard_stop_daily_enabled"] is True
    assert 0 < cb["hard_stop_daily"] <= 5.0, "the default daily cap is modest"
    assert cb["hard_stop_daily"] == getattr(sc, "DAILY_CAP_DEFAULT_USD", None)
    from agent_friday.services import spend_guard
    assert spend_guard.enabled() is True
    assert s["cost_budget"]["hard_stop_daily_enabled"] is True
    # A new install never gets the card meant for older installs.
    assert _card(core) == "not_needed"
    assert _push_card() is False


def test_a_new_install_set_up_by_the_terminal_setup_first_keeps_the_cap(real_settings):
    """The installer runs the terminal setup before the app has ever started:
    it decides the install is new, then writes settings.json itself, with no
    cost_budget. The app's first read must still see the cap on, with no card."""
    core = real_settings
    getattr(core, "install_origin", lambda: None)()   # what setup_wizard.main() does first
    core.SETTINGS_FILE.write_text(json.dumps({"agent_name": "TEST AGENT",
                                              "setup_complete": True}), encoding="utf-8")
    core._invalidate_settings_cache()
    s = core._load_settings()
    assert getattr(sc, "daily_cap", lambda _s: None)(s) == 2.0
    assert _card(core) == "not_needed"


def test_the_cap_is_said_in_plain_words(real_settings):
    core = real_settings
    s = core._load_settings()
    words = getattr(sc, "plain_words", lambda _s: "")(s)
    assert "stops spending on cloud AI after $2.00 a day" in words
    assert "pauses" in words and "hard stop" not in words.lower()
    off = dict(s, cost_budget=dict(s["cost_budget"], hard_stop_daily_enabled=False))
    assert sc.plain_words(off).startswith("There is no daily cap")


def test_the_monthly_cap_and_the_other_caps_still_ship_off(real_settings):
    core = real_settings
    s = core._load_settings()
    on = {c["key"] for c in sc.current(s) if c["on"]}
    assert on == {"cloud_daily_stop"}


# ── an existing install keeps its choice and gets one card ───────────────────

def test_an_existing_install_with_no_daily_cap_keeps_none_and_gets_one_card(real_settings, monkeypatch):
    core = real_settings
    s = _existing(core, {"cost_budget": {"daily": 5.0, "monthly": 50.0}})
    assert sc.daily_cap(s) is None, "the update must not switch the cap on"
    cb = _on_disk(core)["cost_budget"]
    assert cb["hard_stop_daily_enabled"] is False
    assert cb["daily"] == 5.0 and cb["monthly"] == 50.0
    assert _card(core) == "pending"
    pushed = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw) or kw)
    assert _push_card() is True
    assert _push_card() is False, "the card shows once"
    assert len(pushed) == 1
    card = pushed[0]
    assert card["title"] == "New: a daily spending cap"
    assert "nothing has changed" in card["body"] and "$2.00 a day" in card["body"]
    assert card["actions"] == [{"label": "Set my daily cap", "workspace": "settings", "tab": "costs"}]
    assert card["emitter"] == "SC1" and card["priority"] == "high"
    assert _card(core) == "shown"


def test_a_card_that_could_not_be_shown_is_tried_again(real_settings, monkeypatch):
    core = real_settings
    _existing(core, {"cost_budget": {}})
    import agent_friday.notifications_engine as ne

    def _down(**kw):
        raise OSError("notifications store busy")
    monkeypatch.setattr(ne, "push", _down)
    assert _push_card() is False
    assert _card(core) == "pending"
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    assert _push_card() is True and _card(core) == "shown"


def test_the_card_is_never_queued_twice_even_when_settings_json_is_rewritten(real_settings, monkeypatch):
    """The terminal setup and `friday config set` rewrite settings.json whole
    from config.yaml, dropping the migration's stamp. The card's state lives
    beside settings.json, so it is not queued again."""
    core = real_settings
    _existing(core, {"cost_budget": {}})
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    assert _push_card() is True
    core.SETTINGS_FILE.write_text(json.dumps({"agent_name": "TEST AGENT"}), encoding="utf-8")
    monkeypatch.setattr(core, "_CAPS_MIGRATION_TRIED", [False])
    core._invalidate_settings_cache()
    s = core._load_settings()
    assert sc.daily_cap(s) is None, "a rewrite does not switch an existing install's cap on"
    assert _card(core) == "shown" and _push_card() is False


def test_an_existing_install_with_no_cost_block_at_all_is_not_switched_on(real_settings):
    core = real_settings
    s = _existing(core, {})
    assert sc.daily_cap(s) is None
    from agent_friday.services import spend_guard
    assert spend_guard.enabled() is False
    cb = _on_disk(core)["cost_budget"]
    assert cb["hard_stop_daily_enabled"] is False
    assert cb["hard_stop_daily"] == 2.0, "turning it on later starts at the default figure"


def test_an_existing_install_with_its_own_daily_cap_keeps_it_and_needs_no_card(real_settings):
    core = real_settings
    s = _existing(core, {"cost_budget": {"hard_stop_daily": 7.5, "hard_stop_daily_enabled": True}})
    assert sc.daily_cap(s) == 7.5
    assert _card(core) == "not_needed"
    assert _push_card() is False


def test_an_existing_install_that_switched_the_cap_off_keeps_it_off(real_settings):
    core = real_settings
    s = _existing(core, {"cost_budget": {"hard_stop_daily": 3.0, "hard_stop_daily_enabled": False}})
    assert sc.daily_cap(s) is None
    assert _on_disk(core)["cost_budget"]["hard_stop_daily"] == 3.0


def test_an_existing_install_that_lost_its_settings_file_is_not_switched_on(real_settings):
    core = real_settings
    (core.FRIDAY_DIR / ".setup_complete").write_text("done", encoding="utf-8")
    s = core._load_settings()                 # no settings.json: the seed is written
    assert getattr(core, "install_origin", lambda: "")() == "existing"
    assert s["cost_budget"]["hard_stop_daily_enabled"] is False


def test_an_unreadable_settings_file_does_not_switch_an_existing_install_on(real_settings):
    core = real_settings
    core.SETTINGS_FILE.write_text("{ not json", encoding="utf-8")
    core._invalidate_settings_cache()
    s = core._load_settings_raw()
    assert core._is_degraded_settings(s)
    assert s["cost_budget"]["hard_stop_daily_enabled"] is False


def test_the_boot_shows_the_card_and_it_is_a_registered_notice():
    src = (REPO / "src/agent_friday/server.py").read_text(encoding="utf-8")
    assert "_sc.push_daily_cap_card_if_pending" in src
    from agent_friday.services import notice_registry as nr
    e = nr.REGISTRY.get("SC1")
    assert e is not None and e.kind == "spend_cap_intro" and e.place == nr.NEEDS_YOU


# ── reaching it pauses, with a card ──────────────────────────────────────────

def test_reaching_the_cap_pauses_with_a_card_in_plain_words(friday_dir, monkeypatch):
    from agent_friday.services import cost_meter as cm, spend_guard as sg
    cm.reset_for_tests()
    if cm.DB_PATH.exists():
        cm.DB_PATH.unlink()
    cm.reset_for_tests()
    sg.reset_for_tests()
    monkeypatch.setattr(sg, "HALT_LOG", friday_dir / "spend_halts_a17.jsonl")
    pushed = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw) or kw)
    monkeypatch.setattr(cm, "_push_budget_alert", lambda *a, **k: None)
    monkeypatch.setattr(cm, "_load_settings", lambda: {"cost_budget": {
        "hard_stop_daily": 2.0, "hard_stop_daily_enabled": True}})
    try:
        cm.record("anthropic", "claude-sonnet-5", 100, 50, cost_usd=2.10)
        with pytest.raises(sg.SpendCapReached) as ei:
            sg.check("anthropic", what="a research step")
        msg = str(ei.value)
        assert msg.startswith("Paused by your spending cap: $2.10 of your $2.00 a day cap")
        assert "Nothing done so far is lost" in msg and "tomorrow" in msg
        assert "hard" not in msg.lower()
        assert pushed, "no card"
        for card in pushed:
            assert card["actions"] == [{"label": "Change my spending cap",
                                        "workspace": "settings", "tab": "costs"}]
            assert "hard" not in (card["title"] + card["body"]).lower()
        titles = [p["title"] for p in pushed]
        assert "Spending cap reached: paid cloud work is paused" in titles
        assert any(t.startswith("Paused by your spending cap: a research step") for t in titles)
        assert sg.status().get("plain"), "Settings > Spending gets the cap in plain words"
    finally:
        cm.reset_for_tests()


def test_a_task_the_cap_stops_says_paused():
    from agent_friday.services import agent
    assert agent.SPEND_CAP_PAUSE_MARK == "[Paused by your spending cap]"
    src = (REPO / "src/agent_friday/services/agent.py").read_text(encoding="utf-8")
    assert "Halted by hard spending cap" not in src


# ── Settings > Spending says it plainly ─────────────────────────────────────

@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_spending_page_says_it_in_plain_words(page):
    src = (REPO / page).read_text(encoding="utf-8")
    assert "Off by default — your choice, like local vs cloud." not in src
    assert 'title: "Spending caps"' in src
    assert "hardStop && hardStop.plain ? hardStop.plain" in src
    assert '"Daily cap"' in src and '"Monthly cap"' in src
    assert "Hard cap reached" not in src


# ── setup asks about the daily cap, already on ───────────────────────────────

def test_the_setup_promise_names_the_daily_cap():
    from agent_friday.services import setup_chat_copy as copy
    assert "a daily spending cap limits that, and you set it" in copy.CONTRACT
    assert "monthly ceiling" not in copy.CONTRACT


def test_the_money_step_offers_keep_labelled_and_sets_the_daily_cap(real_settings):
    from agent_friday.services import setup_steps as steps
    core = real_settings
    core._load_settings()
    choices = steps.money_choices()
    assert choices[0] == ("keep", "Keep $2.00 a day")
    assert ("none", "No daily cap") in choices
    assert steps.recommended_for("money") == "keep"
    steps.apply_daily_cap(5.0)
    s = core._load_settings()
    assert sc.daily_cap(s) == 5.0
    assert "cloud_daily_stop" in s["spending_caps"]["answered"]
    steps.apply_daily_cap(None)
    assert sc.daily_cap(core._load_settings()) is None
