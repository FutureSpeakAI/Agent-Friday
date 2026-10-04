"""Friday's look, by voice (avatar-visual-genome.md §8.4): one tool,
avatar_evolution, declared once in the text registry and shared into voice,
so what she says comes from the same record the screen shows."""
import json

import pytest

from agent_friday.services import avatar_genome as g
from agent_friday.services import avatar_growth as gr

DAY = 86400.0


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday.governance.proof_of_integrity import IntegrityEngine
    eng = IntegrityEngine(friday_dir=tmp_path / "identity")
    monkeypatch.setattr(g, "_engine", lambda: eng)
    monkeypatch.setattr(g, "AVATAR_DIR", tmp_path / "avatar")
    monkeypatch.setattr(gr, "EVOLUTION_FILE", tmp_path / "evolution.json")
    monkeypatch.setattr(gr, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(gr, "_local_seat", lambda: None)
    return tmp_path


def _tool():
    from agent_friday.services import avatar_tools
    return avatar_tools


def _step(offset, created, name, model=None, parent=None):
    gen = g.defaults()
    gen["palette"]["base_offset"] = offset
    return g.commit_step(gen, parent=parent, kind="growth", target="CUBES", reason="a test",
                         author={"path": "cloud" if model else "seeded", "model": model},
                         input_digest="sha256:x", name=name, now=created)


def test_the_tool_is_declared_once_and_shared_into_voice():
    from agent_friday.services import agent, voice_engine
    names = [t["name"] for t in agent.CLAUDE_TOOLS]
    assert names.count("avatar_evolution") == 1
    assert "avatar_evolution" in agent.CLAUDE_TOOL_HANDLERS
    assert "avatar_evolution" in voice_engine._VOICE_SHARED_TOOLS
    assert "avatar_evolution" in [n for n, _d, _s in voice_engine._voice_shared_tool_specs()]


def test_it_changes_only_the_owners_own_desktop_so_it_needs_no_card():
    from agent_friday.governance import action_gate
    src = open(action_gate.__file__, encoding="utf-8").read()
    assert '"avatar_evolution"' in src


def test_what_changed_is_said_in_words_and_names_the_model(home):
    a = _step(4, 1000.0, "Tidewater", model="claude-opus-5-5")
    out = _tool().handle({"action": "describe"})
    assert "claude-opus-5-5" in out
    assert "Tidewater" in out
    assert "base_offset" not in out and "palette/" not in out    # words, not gene names
    assert "four degrees" in out or "4 degrees" in out


def test_describe_on_the_first_look_says_so(home):
    out = _tool().handle({"action": "describe"})
    assert "the way I started" in out


def test_go_back_to_last_months_look(home, monkeypatch):
    now = 100 * DAY
    a = _step(4, now - 40 * DAY, "Early")
    b = _step(8, now - 20 * DAY, "Middle", parent=a["content_hash"])
    c = _step(12, now - 2 * DAY, "Late", parent=b["content_hash"])
    monkeypatch.setattr(_tool(), "_now", lambda: now)
    out = _tool().handle({"action": "rollback", "when": "last month"})
    assert g.active_step()["content_hash"] == a["content_hash"]
    assert "Early" in out
    # "undo that" goes back to where she was
    _tool().handle({"action": "undo"})
    assert g.active_step() is None or g.active_step()["content_hash"] != a["content_hash"]


@pytest.mark.parametrize("phrase,expect", [
    ("last week", "Middle"), ("2 weeks ago", "Middle"), ("5 weeks ago", "Early"), ("Late", "Late"),
    ("the one claude-opus-5-5 made", "Early"), ("the first look", None)])
def test_other_ways_to_name_a_look(home, monkeypatch, phrase, expect):
    now = 100 * DAY
    a = _step(4, now - 40 * DAY, "Early", model="claude-opus-5-5")
    b = _step(8, now - 20 * DAY, "Middle", parent=a["content_hash"])
    _step(12, now - 2 * DAY, "Late", parent=b["content_hash"])
    monkeypatch.setattr(_tool(), "_now", lambda: now)
    _tool().handle({"action": "rollback", "when": phrase})
    s = g.active_step()
    assert (s["name"] if s else None) == expect


def test_an_ambiguous_name_asks_which(home):
    a = _step(4, 1000.0, "Harbor Lattice")
    _step(8, 2000.0, "Harbor Weave", parent=a["content_hash"])
    before = g.active_step()["content_hash"]
    out = _tool().handle({"action": "rollback", "when": "harbor"})
    assert "Harbor Lattice" in out and "Harbor Weave" in out and "which" in out.lower()
    assert g.active_step()["content_hash"] == before


def test_turn_off_and_on_and_choose_the_author(home):
    t = _tool()
    assert "off" in t.handle({"action": "set_enabled", "enabled": False}).lower()
    assert gr.settings()["enabled"] is False
    t.handle({"action": "set_enabled", "enabled": True})
    assert gr.settings()["enabled"] is True
    out = t.handle({"action": "set_author", "author": "seeded"})
    assert gr.settings()["author"] == "seeded" and "from now on" in out.lower()
    out = t.handle({"action": "set_author", "author": "rm -rf"})
    assert gr.settings()["author"] == "seeded" and "can't" in out.lower()


def test_evolve_now_by_voice_says_what_changed(home):
    t = _tool()
    t.handle({"action": "set_author", "author": "seeded"})
    out = t.handle({"action": "evolve_now"})
    assert g.active_step() is not None
    assert "Friday" in out or "made it" in out.lower()


def test_evolve_now_while_waiting_offers_both_fixes(home, monkeypatch):
    monkeypatch.setattr(gr, "_frontier_available", lambda: (False, "no cloud model is connected", []))
    monkeypatch.setattr(gr, "_local_seat", lambda: "gemma-local")
    out = _tool().handle({"action": "evolve_now"})
    assert "Accounts & Keys" in out and "gemma-local" in out
    assert g.active_step() is None


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """The structure choice kept in a scratch evolution.json; a fake page."""
    from agent_friday.routes import insights
    monkeypatch.setattr(insights, "EVOLUTION_FILE", tmp_path / "evolution.json")
    state = {"pushed": [], "page": True}

    def push(action):
        state["pushed"].append(action)
        return {"delivered": state["page"]}
    monkeypatch.setattr(_tool(), "push", push)
    state["file"] = tmp_path / "evolution.json"
    return state


@pytest.mark.parametrize("said, index, words", [
    ("the wormhole", 13, "Einstein-Rosen Bridge"),
    ("Hawking Radiation", 14, "Hawking Radiation"),
    ("a black hole", 14, "Hawking Radiation"),
    ("the sacred sphere", 1, "Dyson Sphere"),
    ("Giga Earth", 12, "Giga Earth"),
    ("the ocean of light", 9, "Ocean of Light"),
])
def test_show_picks_the_structure_by_any_of_its_names(scene, said, index, words):
    out = _tool().handle({"action": "show", "structure": said})
    assert json.loads(scene["file"].read_text("utf-8"))["preferred_scene_index"] == index
    from agent_friday.routes.insights import SCENE_NAMES
    assert scene["pushed"] == [{"type": "scene", "target": SCENE_NAMES[index]}]
    assert out == "Now showing %s." % words


def test_show_with_no_page_open_still_keeps_the_choice(scene):
    scene["page"] = False
    out = _tool().handle({"action": "show", "structure": "wormhole"})
    assert json.loads(scene["file"].read_text("utf-8"))["preferred_scene_index"] == 13
    assert "when the Friday window is open" in out


def test_show_an_unknown_name_keeps_nothing_and_lists_the_choices(scene):
    out = _tool().handle({"action": "show", "structure": "a teapot"})
    assert not scene["file"].exists() and scene["pushed"] == []
    assert "don't have a structure called a teapot" in out
    assert "Einstein-Rosen Bridge" in out and "Hawking Radiation" in out
