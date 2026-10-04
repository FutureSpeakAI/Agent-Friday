"""One project store (Chat Hub M2): the chat sidebar's projects and the creative
Series Bible are one record, `~/.friday/projects/<id>/project.json`.

A project holds standing instructions, files, connected codebases, a preferred
seat and the Bible; every chat inside it inherits them. The legacy Bible folder
(`<id>/bible.json`) migrates losslessly and the migration can be rolled back.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import creative_memory as cm
from agent_friday.services import projects


@pytest.fixture(autouse=True)
def _roots(monkeypatch, tmp_path):
    monkeypatch.setattr(projects, "_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


LEGACY = {
    "id": "dawn-patrol-ab12cd", "name": "Dawn Patrol", "type": "video-series",
    "created": "2026-09-01T08:00:00", "updated": "2026-09-02T09:30:00",
    "characters": [{"name": "Mara", "visual_description": "silver hair, red coat", "voice_profile": "low, dry",
                    "aliases": ["M"], "notes": "lead"}],
    "locations": [{"name": "The pier", "description": "fog, lamps", "notes": ""}],
    "continuity": [{"ts": "2026-09-01T08:10:00", "scene": "1", "note": "Mara loses the key"}],
    "style_guide": {"palette": "teal and rust", "tone": "quiet"},
    "assets": ["mara_01.png", "pier_dusk.mp4"],
    "pipeline_status": {"stage": "storyboard", "state": "done"},
}


def _write_legacy(tmp_path: Path) -> Path:
    d = tmp_path / "projects" / LEGACY["id"]
    d.mkdir(parents=True)
    (d / "bible.json").write_text(json.dumps(LEGACY, indent=2), encoding="utf-8")
    return d / "bible.json"


def test_a_legacy_bible_migrates_losslessly_and_the_migration_rolls_back(tmp_path):
    bible_path = _write_legacy(tmp_path)
    before = bible_path.read_bytes()

    manifest = projects.migrate_legacy()
    assert manifest["created"] == [str(tmp_path / "projects" / LEGACY["id"] / "project.json")]
    rec = projects.load(LEGACY["id"])
    assert rec and rec["name"] == "Dawn Patrol" and rec["type"] == "video-series"
    for k in ("characters", "locations", "continuity", "style_guide", "assets", "pipeline_status"):
        assert rec["bible"][k] == LEGACY[k], k
    # the creative view reads the same record, field for field
    view = cm.get_project(LEGACY["id"])
    for k in ("name", "type", "characters", "locations", "continuity", "style_guide", "assets", "pipeline_status"):
        assert view[k] == LEGACY[k], k
    assert view["created"] == LEGACY["created"] and view["updated"] == LEGACY["updated"]
    assert bible_path.read_bytes() == before, "the legacy file is left exactly as it was"

    # idempotent
    assert projects.migrate_legacy()["created"] == []

    # rollback removes only what the migration wrote
    removed = projects.rollback(manifest)
    assert removed == 1
    assert not (tmp_path / "projects" / LEGACY["id"] / "project.json").exists()
    assert bible_path.read_bytes() == before


def test_the_record_carries_what_a_chat_inherits():
    p = projects.create("Parks desk", instructions="Answer like a city reporter.", seat={"model": "bonsai2:27b", "provider": "bonsai"})
    assert p["type"] == "general" and p["files"] == [] and p["codebases"] == []
    assert set(p["bible"]) >= {"characters", "locations", "continuity", "style_guide", "assets", "pipeline_status"}
    # an old record without the new keys reads back with them
    raw = Path(projects._root()) / p["id"] / "project.json"
    d = json.loads(raw.read_text(encoding="utf-8"))
    for k in ("type", "bible", "files", "codebases"):
        d.pop(k)
    raw.write_text(json.dumps(d), encoding="utf-8")
    again = projects.load(p["id"])
    assert again["files"] == [] and again["codebases"] == [] and again["type"] == "general" and "bible" in again


def test_files_round_trip_and_the_caps_hold():
    p = projects.create("Files")
    entry = projects.add_file(p["id"], "notes.md", b"# Parks\nThe pier closes at dusk.\n")
    assert entry["name"] == "notes.md" and entry["bytes"] == 33 and len(entry["sha256"]) == 64
    assert [f["name"] for f in projects.list_files(p["id"])] == ["notes.md"]
    assert projects.read_file(p["id"], "notes.md") == b"# Parks\nThe pier closes at dusk.\n"
    assert projects.read_file(p["id"], "missing.md") is None
    with pytest.raises(ValueError):
        projects.add_file(p["id"], "big.bin", b"x" * (projects.MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError):
        projects.add_file(p["id"], "../escape.md", b"x")
    assert projects.remove_file(p["id"], "notes.md") is True
    assert projects.list_files(p["id"]) == [] and projects.read_file(p["id"], "notes.md") is None


def test_off_the_record_writes_no_file(monkeypatch):
    p = projects.create("Quiet")
    monkeypatch.setattr(projects, "_off_record", lambda: True)
    with pytest.raises(RuntimeError):
        projects.add_file(p["id"], "notes.md", b"secret")
    assert not (Path(projects._root()) / p["id"] / "files").exists()


def test_a_codebase_connects_both_ways():
    p = projects.create("Builds")
    rec = cb.create("Rent tracker", template="static")
    projects.connect_codebase(p["id"], rec["id"])
    assert projects.load(p["id"])["codebases"] == [rec["id"]]
    assert cb.load(rec["id"]).get("project") == p["id"]
    projects.disconnect_codebase(p["id"], rec["id"])
    assert projects.load(p["id"])["codebases"] == [] and not cb.load(rec["id"]).get("project")


def test_the_context_block_carries_instructions_files_codebases_and_the_bible():
    p = projects.create("Parks desk", instructions="Answer like a city reporter.")
    projects.add_file(p["id"], "notes.md", b"The pier closes at dusk.")
    projects.add_file(p["id"], "photo.png", b"\x89PNG\r\n")
    rec = cb.create("Rent tracker", template="static")
    projects.connect_codebase(p["id"], rec["id"])
    cm.add_character(p["id"], "Mara", "silver hair, red coat")
    conv = convs.create("Field notes")
    convs.patch(conv["id"], project=p["id"])
    block = projects.context_block(conv["id"])
    assert projects.CONTEXT_HEADER in block and "Parks desk" in block
    assert "Answer like a city reporter." in block
    assert "notes.md" in block and "The pier closes at dusk." in block
    assert "photo.png" in block and "PNG" not in block, "binary files are named, never excerpted"
    assert "Rent tracker" in block
    assert "Mara" in block
    other = convs.create("Elsewhere")
    assert projects.context_block(other["id"]) == ""


def test_deleting_a_project_removes_only_its_own_folder_and_keeps_the_chats():
    p = projects.create("Gone")
    projects.add_file(p["id"], "a.txt", b"a")
    conv = convs.create("Kept")
    convs.patch(conv["id"], project=p["id"])
    other = projects.create("Stays")
    assert cm.delete_project(p["id"]) is True
    assert projects.load(p["id"]) is None and not (Path(projects._root()) / p["id"]).exists()
    assert convs.load(conv["id"]) is not None and convs.load(conv["id"]).get("project") is None
    assert projects.load(other["id"]) is not None
    assert cm.delete_project("nothing-here") is False
    assert cm.delete_project("../projects") is False
