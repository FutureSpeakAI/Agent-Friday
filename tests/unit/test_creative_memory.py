"""Unit tests for Creative Memory / Series Bible (services/creative_memory.py).

Home is redirected to a temp dir by the root conftest, so projects persist under
isolation. Each test creates its own project and operates on that id.
"""
import pytest

from agent_friday.services import creative_memory as cm


@pytest.fixture
def project():
    b = cm.create_project("Test Saga", "video-series")
    yield b["id"]
    cm.delete_project(b["id"])


def test_create_lists_and_activates(project):
    ids = [p["id"] for p in cm.list_projects()]
    assert project in ids
    assert cm.get_active_project_id() == project   # make_active default


def test_add_character_upsert_and_propagation(project):
    cm.add_character(project, "Maya", "silver-haired pilot in a red jacket",
                     voice_profile="calm alto")
    # upsert: second call updates, does not duplicate
    cm.add_character(project, "Maya", "silver-haired pilot in a navy jacket")
    chars = cm.list_characters(project)
    assert len(chars) == 1
    assert "navy jacket" in chars[0]["visual_description"]

    ctx = cm.character_context(project, ["Maya"])
    assert ctx["Maya"].startswith("silver-haired pilot")
    voices = cm.voice_context(project, ["Maya"])
    assert voices["Maya"] == "calm alto"


def test_character_alias_resolution(project):
    cm.add_character(project, "Maya", "a pilot", aliases=["Captain M"])
    assert cm.get_character(project, "Captain M")["name"] == "Maya"
    ctx = cm.character_context(project, ["Captain M"])
    assert ctx["Captain M"] == "a pilot"


def test_locations_and_continuity(project):
    cm.add_location(project, "The Hangar", "a vast neon-lit bay")
    cm.add_continuity(project, "Maya lost her helmet", scene="3")
    assert cm.list_locations(project)[0]["name"] == "The Hangar"
    cont = cm.list_continuity(project)
    assert cont[0]["note"] == "Maya lost her helmet"
    assert cont[0]["scene"] == "3"


def test_style_guide_merge(project):
    cm.set_style_guide(project, {"palette": "warm", "genre": "noir"})
    cm.set_style_guide(project, {"genre": "sci-fi"})   # merge
    sg = cm.get_project(project)["style_guide"]
    assert sg["palette"] == "warm"
    assert sg["genre"] == "sci-fi"


def test_assets_attach_and_list(project):
    cm.add_asset(project, "friday-image-x.png")
    cm.add_asset(project, "friday-image-x.png")   # dedup
    assert cm.list_assets(project) == ["friday-image-x.png"]


def test_project_prompt_context_summarizes_bible(project):
    cm.add_character(project, "Maya", "a silver-haired pilot")
    cm.add_location(project, "The Hangar", "neon bay")
    cm.add_continuity(project, "It is raining")
    block = cm.project_prompt_context(project)
    assert "Test Saga" in block
    assert "Maya" in block
    assert "The Hangar" in block
    assert "It is raining" in block


def test_delete_clears_active():
    b = cm.create_project("Throwaway", "card")
    pid = b["id"]
    assert cm.get_active_project_id() == pid
    assert cm.delete_project(pid) is True
    assert cm.get_active_project_id() == ""
    assert cm.get_project(pid) is None


def test_character_context_all_when_names_none(project):
    cm.add_character(project, "A", "look A")
    cm.add_character(project, "B", "")     # no description → excluded
    ctx = cm.character_context(project, None)
    assert ctx == {"A": "look A"}
