"""Giga Earth's set track (avatar-visual-genome.md §15): the Rez structure
never evolves by model. While it is on screen, a weekly or manual step moves
it one form along a fixed track (the Area 1 boss: the sealed ball, then the
tiles blasted away, the X-shaped robot, the swirling arms, the rings, the
final form). No model is asked, so it runs with no cloud and no local seat;
it is signed, undoable and rolled back like any other step, and no model's
step on another structure, and no shared gene, changes what it draws."""
import json

import pytest

from agent_friday.services import avatar_genome as g
from agent_friday.services import avatar_growth as gr

WEEK = 7 * 86400
EDEN = g.STRUCTURE_IDS.index("EDEN")


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday.governance.proof_of_integrity import IntegrityEngine
    eng = IntegrityEngine(friday_dir=tmp_path / "identity")
    monkeypatch.setattr(g, "_engine", lambda: eng)
    monkeypatch.setattr(g, "AVATAR_DIR", tmp_path / "avatar")
    monkeypatch.setattr(gr, "EVOLUTION_FILE", tmp_path / "evolution.json")
    monkeypatch.setattr(gr, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(gr, "_idle_blocked", lambda: "")
    monkeypatch.setattr(gr, "_local_seat", lambda: None)
    # No model of any kind: the track must not need one.
    monkeypatch.setattr(gr, "_frontier_available", lambda: (False, "no cloud model is connected", []))

    def no_model(*a, **k):
        raise AssertionError("the Rez track asked a model")
    monkeypatch.setattr(gr, "_call_model", no_model)
    monkeypatch.setattr(gr, "_seeded_propose", no_model)
    (tmp_path / "evolution.json").write_text(json.dumps({"preferred_scene_index": EDEN}))
    return tmp_path


def _stage():
    return g.express(g.active_genome())["EDEN"]["stage"]


def test_the_track_is_seven_named_forms_and_v1_is_the_sealed_ball():
    names = g.TRACKS["EDEN"]["forms"]
    assert names[0] == "Sealed" and names[-1] == "Final form" and len(names) == 7
    assert g.defaults()["structures"]["EDEN"] == {"stage": 0}
    assert g.express(g.defaults())["EDEN"]["stage"] == 0


def test_the_weekly_step_on_giga_earth_moves_one_form_with_no_model(home):
    gr.settings(now=0.0)
    out = gr.tick(now=WEEK + 1)
    assert out["status"] == "stepped"
    step = g.active_step()
    assert step["kind"] == "track" and step["target_structure"] == "EDEN"
    assert step["author"]["path"] == "track" and step["author"].get("model") is None
    assert step["sent"] is None
    assert step["name"] == "Giga Earth: Cracked"
    assert [d["gene"] for d in step["diff"]] == ["structures/EDEN/stage"]
    assert _stage() == 1
    assert g.verify_step(step) == "verified"


def test_evolve_now_walks_the_whole_track_then_holds_the_final_form(home):
    gr.settings(now=0.0)
    for n in range(1, 7):
        assert gr.evolve_now(now=n * 10.0)["status"] == "stepped"
        assert _stage() == n
    held = gr.evolve_now(now=100.0)
    assert held["status"] == "skipped" and "final form" in held["reason"]
    assert _stage() == 6 and len(g.history()) == 6


def test_undo_and_rollback_move_back_along_the_track(home):
    gr.settings(now=0.0)
    for n in range(1, 4):
        gr.evolve_now(now=n * 10.0)
    gr.undo()
    assert _stage() == 2
    first = [s for s in g.history() if s["name"] == "Giga Earth: Cracked"][0]
    g.rollback(first["content_hash"])
    assert _stage() == 1
    g.reset()
    assert _stage() == 0


def test_the_track_also_runs_when_evolution_is_off_only_by_hand(home):
    gr.set_settings(enabled=False, now=0.0)
    assert gr.tick(now=WEEK + 1)["status"] == "off"
    assert gr.evolve_now(now=WEEK + 2)["status"] == "stepped"
    assert _stage() == 1


def test_no_model_proposal_can_move_the_track_or_the_rest_of_giga_earth():
    parent = g.defaults()
    prop = g.defaults()
    prop["structures"]["EDEN"]["stage"] = 3
    prop["palette"]["base_offset"] = 6
    # A model's step on another structure never reaches Giga Earth ...
    child, moved = g.clamp_step(parent, prop, target="CUBES", step_number=1)
    assert child["structures"]["EDEN"]["stage"] == 0
    assert "structures/EDEN/stage" not in moved
    # ... and a model can never take a step while Giga Earth is on screen.
    child, moved = g.clamp_step(parent, prop, target="EDEN", step_number=1)
    assert moved == [] and child["structures"]["EDEN"]["stage"] == 0


def test_shared_density_never_changes_what_giga_earth_draws():
    gen = g.defaults()
    gen["form"]["density"] = g.SHARED_GENES["form"]["density"]["min"]
    assert g.express(gen)["EDEN"] == g.express(g.defaults())["EDEN"]


def test_voice_says_the_form_and_that_no_model_made_it(home):
    from agent_friday.services import avatar_tools
    gr.settings(now=0.0)
    reply = avatar_tools.handle({"action": "evolve_now"})
    assert "Cracked" in reply and "set track" in reply
    assert "made it" not in reply.split("set track")[0].split(".")[-1]
