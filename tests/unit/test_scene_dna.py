"""Unit tests for Scene DNA layered prompting (services/scene_dna.py).

Pure module — no model, no I/O. Covers construction/coercion, surgical
single-layer edits, merge/overlay semantics, character-description propagation,
and both natural + labelled rendering.
"""
import pytest

from agent_friday.services import scene_dna as sd
from agent_friday.services.scene_dna import SceneDNA


def test_from_dict_coerces_characters_from_string():
    dna = SceneDNA.from_dict({"characters": "Maya, Theo , ,Rex"})
    assert dna.characters == ["Maya", "Theo", "Rex"]


def test_unknown_keys_land_in_extras_and_round_trip():
    dna = SceneDNA.from_dict({"setting": "a beach", "scene_number": 3})
    d = dna.to_dict()
    assert d["setting"] == "a beach"
    assert d["extras"]["scene_number"] == 3
    # every editable layer slot is present
    for layer in sd.layers():
        assert layer in d


def test_with_layer_is_surgical_and_returns_copy():
    dna = SceneDNA.from_dict({"setting": "a forest", "mood": "calm"})
    edited = dna.with_layer("mood", "tense and foreboding")
    assert edited.mood == "tense and foreboding"
    assert edited.setting == "a forest"      # untouched
    assert dna.mood == "calm"                # original unchanged (copy)


def test_with_layer_rejects_unknown_layer():
    with pytest.raises(KeyError):
        SceneDNA().with_layer("lighting", "noir")


def test_edit_layer_helper_on_dict():
    out = sd.edit_layer({"action": "running"}, "action", "walking slowly")
    assert out["action"] == "walking slowly"


def test_merge_overlay_non_empty_wins():
    base = SceneDNA.from_dict({"setting": "a city", "mood": "neutral",
                               "style": "photorealistic"})
    scene = SceneDNA.from_dict({"mood": "ominous", "characters": ["Maya"]})
    merged = base.merge(scene)
    assert merged.setting == "a city"        # kept from base
    assert merged.mood == "ominous"          # overridden by scene
    assert merged.style == "photorealistic"  # base default survives
    assert merged.characters == ["Maya"]


def test_render_natural_includes_all_nonempty_layers():
    dna = SceneDNA.from_dict({"setting": "a rooftop at dusk", "action": "she leaps",
                              "mood": "electric"})
    prompt = dna.render_prompt()
    assert "rooftop at dusk" in prompt
    assert "she leaps" in prompt
    assert "electric" in prompt


def test_render_labelled_form():
    dna = SceneDNA.from_dict({"setting": "a lab", "mood": "sterile"})
    out = dna.render_prompt(labelled=True)
    assert "Setting: a lab" in out
    assert "Mood: sterile" in out


def test_character_descriptions_propagate_into_prompt():
    dna = SceneDNA.from_dict({"characters": ["Maya"], "action": "waves"})
    prompt = dna.render_prompt(
        character_descriptions={"Maya": "a tall woman with silver hair"})
    assert "Maya (a tall woman with silver hair)" in prompt


def test_render_without_descriptions_uses_bare_names():
    dna = SceneDNA.from_dict({"characters": ["Maya", "Theo"]})
    assert "Maya" in dna.render_prompt()


def test_empty_dna_renders_empty_and_is_empty():
    assert SceneDNA().is_empty()
    assert sd.render({}) == ""


def test_validate_normalizes_untrusted_input():
    clean = sd.validate({"characters": "A,B", "junk": 1})
    assert clean["characters"] == ["A", "B"]
    assert "extras" in clean
