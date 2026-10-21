"""North-star A16: no AI route is pre-selected.

The person picks where Friday thinks (on this computer, in the cloud, or
both) at setup; a recommendation is a label, never a selection. On a new
install no route counts as picked until the owner's own routing change records
one (core.route_pick_pending), so Settings presses no option until then. An
install that already existed keeps its stored route as its choice, and how
Friday routes does not change on update.

New behaviour is reached through getattr with a stand-in, so on a tree without
it each test fails on its assertion, not on an AttributeError.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_friday.services import cloud_intent

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def real_settings(tmp_path, monkeypatch):
    """A home of its own, read and written through the real settings loader."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setitem(core.NETWORK_STATE, "status", "online")
    core._invalidate_settings_cache()
    yield core
    core._invalidate_settings_cache()


def _pending(core, settings=None) -> bool:
    fn = getattr(core, "route_pick_pending", None)
    return bool(fn(settings) if fn else False)


# ── a new install: nothing picked ────────────────────────────────────────────

def test_a_new_install_has_no_route_picked(real_settings):
    core = real_settings
    core._load_settings_raw()                       # the first read seeds settings.json
    assert core.SETTINGS_FILE.exists()
    assert _pending(core) is True
    # How Friday routes meanwhile is unchanged (the factory mode, unchosen).
    assert cloud_intent.mode_was_chosen() is False


def test_only_the_owners_own_pick_ends_it(real_settings):
    core = real_settings
    core._load_settings_raw()
    # A generic write (a whole-settings save, a script, a model's tool) cannot
    # record a pick.
    core._save_settings({"model_routing": {"mode": "local_only", "mode_chosen": "local_only",
                                           "mode_chosen_via": "settings"}})
    assert _pending(core) is True
    # The owner's routing change (Settings, the setup chat, setup completion) does.
    core._save_settings({"model_routing": {"mode": "local_only"}}, owner_routing_change=True,
                        routing_change_via="settings")
    assert _pending(core) is False


def test_a_route_the_terminal_setup_kept_without_asking_is_not_a_pick(real_settings):
    core = real_settings
    core._load_settings_raw()
    s = {"model_routing": {"mode": "cloud_only", "mode_chosen": "cloud_only",
                           "mode_chosen_via": "unverified"}}
    assert _pending(core, s) is True
    s["model_routing"]["mode_chosen_via"] = "setup"
    assert _pending(core, s) is False


# ── an existing install keeps its choice ─────────────────────────────────────

@pytest.mark.parametrize("stored", ["cloud_only", "local_preferred", "local_only"])
def test_an_existing_install_keeps_its_route_and_it_counts_as_picked(real_settings, stored):
    core = real_settings
    core.SETTINGS_FILE.write_text(json.dumps({"agent_name": "TEST AGENT",
                                              "model_routing": {"mode": stored}}),
                                  encoding="utf-8")
    core._invalidate_settings_cache()
    s = core._load_settings_raw()
    assert s["model_routing"]["mode"] == stored
    assert getattr(core, "install_origin", lambda: "")() == "existing"
    assert _pending(core, s) is False
    assert cloud_intent.routing_mode() == stored


# ── the terminal setup never answers the routing question itself ─────────────

def _drive_step_routing(monkeypatch, answers, existing=""):
    from agent_friday import setup_wizard as sw
    asked = []

    def _ask(prompt, *a, **kw):
        asked.append(kw.get("default"))
        a_ = answers.pop(0)
        # Enter on an empty line returns the prompt's default, as rich does.
        return kw.get("default", "") if a_ is None else a_

    monkeypatch.setattr(sw.Prompt, "ask", staticmethod(_ask))
    monkeypatch.setattr(sw, "_say_screen", lambda *a, **k: {"choices": [
        {"mode": "cloud_only", "label": "Cloud", "detail": "d"},
        {"mode": "local_only", "label": "Local", "detail": "d"},
        {"mode": "local_preferred", "label": "Both", "detail": "d"}]})
    from agent_friday import setup_brain
    monkeypatch.setattr(setup_brain, "assess", lambda: {"capable": True, "reason": ""})
    return sw.step_routing(9, 3, existing), asked


def test_enter_on_the_routing_screen_picks_nothing_on_a_new_install(monkeypatch):
    mode, asked = _drive_step_routing(monkeypatch, [None, None, "2"])
    assert mode == "local_only", "Enter alone must not pick a route"
    assert len(asked) == 3 and all(not d for d in asked), asked


def test_enter_keeps_a_route_the_owner_already_chose(monkeypatch):
    mode, _asked = _drive_step_routing(monkeypatch, [None], existing="local_preferred")
    assert mode == "local_preferred"


def test_the_terminal_setup_offers_no_stored_route_on_a_new_install():
    src = (REPO / "src/agent_friday/setup_wizard.py").read_text(encoding="utf-8")
    assert "step_routing(total_steps, 3, _routing_offered)" in src
    assert '"" if _core.route_pick_pending(existing) else _routing_existing' in src


# ── Settings > Models presses no route until one is picked ───────────────────

def test_the_models_payload_says_whether_a_route_was_picked():
    src = (REPO / "src/agent_friday/routes/intelligence.py").read_text(encoding="utf-8")
    assert '"routing_mode_picked": not _route_pick_pending(settings)' in src


@pytest.mark.skipif(not shutil.which("node"), reason="node is not on PATH")
def test_the_models_card_presses_nothing_until_a_route_is_picked():
    cp = subprocess.run([shutil.which("node"), "--test",
                         str(REPO / "tests" / "ui" / "route_not_preselected.test.cjs")],
                        capture_output=True, text=True, encoding="utf-8", timeout=120,
                        cwd=str(REPO))
    assert cp.returncode == 0, (cp.stdout[-4000:] + cp.stderr[-2000:])
