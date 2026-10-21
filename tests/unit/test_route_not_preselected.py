"""North-star A16: no AI route is pre-selected.

The person picks where Friday thinks (on this computer, in the cloud, or
both) at setup; a recommendation is a label, never a selection. A new
install stores that no route has been picked yet (model_routing.
route_pick_pending), so Settings presses no option until the owner picks one.
An install that already existed keeps its stored route as its choice, and
how Friday routes does not change on update.
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
    """A temp home read and written through the real settings loader."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setitem(core.NETWORK_STATE, "status", "online")
    core._invalidate_settings_cache()
    yield core
    core._invalidate_settings_cache()


def _on_disk(core) -> dict:
    return json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8"))


# ── a new install: nothing picked ────────────────────────────────────────────

def test_a_new_install_stores_that_no_route_is_picked(real_settings):
    core = real_settings
    seeded = core._load_settings_raw()
    assert core.SETTINGS_FILE.exists()
    assert _on_disk(core)["model_routing"].get("route_pick_pending") is True
    assert seeded["model_routing"].get("route_pick_pending") is True
    # How Friday routes meanwhile is unchanged (the factory mode, unchosen).
    assert cloud_intent.mode_was_chosen() is False


def test_only_the_owners_own_pick_clears_it(real_settings):
    core = real_settings
    core._load_settings_raw()
    # A generic write (a whole-settings save, a script, a model's tool) cannot
    # mark a route as picked.
    core._save_settings({"model_routing": {"route_pick_pending": False,
                                           "vault_local_only": True}})
    assert core._load_settings_raw()["model_routing"].get("route_pick_pending") is True
    # The owner's routing change (Settings, the setup chat, setup completion) does.
    core._save_settings({"model_routing": {"mode": "local_only"}}, owner_routing_change=True)
    mr = core._load_settings_raw()["model_routing"]
    assert mr.get("route_pick_pending") is False and mr["mode"] == "local_only"
    assert core.route_pick_pending() is False


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
    assert core.route_pick_pending(s) is False
    assert cloud_intent.routing_mode() == stored
    assert "route_pick_pending" not in _on_disk(core).get("model_routing", {})


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
    assert '"route_pick_pending") is True else _routing_existing' in src


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
