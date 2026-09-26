"""A name that arrives in a request never reaches a file outside its root.

One test per Friday-owned root a route joins a request value under. Each one
plants a file (or folder) exactly where the traversal would land, sends the
traversal, and asserts the planted file is untouched and nothing is returned
from it. The helper underneath is agent_friday.paths.contained / safe_name
(tests/unit/test_path_containment.py covers the path forms themselves).

A backslash is a path separator only on Windows, and Flask's default route
converter lets one through (``%5C``), so the URL-segment cases run there.
JSON-body cases that carry ``../`` run everywhere; a few rely on Windows
normalising ``missing-dir\\..`` lexically and are marked the same way.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

windows_only = pytest.mark.skipif(os.name != "nt", reason="backslash is a separator only on Windows")


def _plant_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "keep.txt").write_text("keep", encoding="utf-8")
    return path


def _plant_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


# ── deletes ─────────────────────────────────────────────────────────────────

@windows_only
def test_task_delete_cannot_leave_the_tasks_folder(client):
    from agent_friday.services import task_journal as tj
    victim = _plant_dir(Path(tj.tasks_dir()).parent / "pt-victim-tasks")
    try:
        resp = client.delete("/api/tasks/..%5Cpt-victim-tasks")
        assert resp.status_code == 404
        assert (victim / "keep.txt").exists()
    finally:
        import shutil
        shutil.rmtree(victim, ignore_errors=True)


@windows_only
def test_creative_project_delete_cannot_leave_the_projects_folder(client):
    from agent_friday.services import creative_memory as cm
    victim = _plant_dir(Path(cm.PROJECTS_DIR).parent / "pt-victim-cm")
    try:
        resp = client.delete("/api/creative/projects/..%5Cpt-victim-cm")
        assert resp.get_json().get("deleted") is False
        assert (victim / "keep.txt").exists()
    finally:
        import shutil
        shutil.rmtree(victim, ignore_errors=True)


@windows_only
def test_project_delete_cannot_leave_the_projects_folder(client):
    from agent_friday.services import projects as proj
    victim = _plant_json(Path(proj._root()).parent / "pt-victim-proj" / "project.json",
                         {"id": "pt-victim-proj", "name": "victim"})
    try:
        resp = client.delete("/api/projects/..%5Cpt-victim-proj")
        assert resp.status_code == 404
        assert victim.exists()
    finally:
        victim.unlink(missing_ok=True)


def test_wiki_delete_cannot_leave_the_wiki(client):
    from agent_friday.core import WIKI_DIR
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    victim = WIKI_DIR.parent / "pt-victim-wiki.md"
    victim.write_text("keep", encoding="utf-8")
    try:
        resp = client.delete("/api/wiki/file", json={
            "file": "../pt-victim-wiki.md", "confirm": "DELETE"})
        assert resp.status_code == 400
        assert victim.exists()
    finally:
        victim.unlink(missing_ok=True)


# ── writes ──────────────────────────────────────────────────────────────────

@windows_only
def test_draft_confirm_cannot_rewrite_json_outside_the_queue(client):
    from agent_friday.services.misc_engine import FLOW_QUEUE_DIR
    victim = _plant_json(Path(FLOW_QUEUE_DIR).parent / "pt-victim-draft.json", {"a": 1})
    try:
        resp = client.post("/api/flow/draft/confirm",
                           json={"draft_id": "x/../../pt-victim-draft"})
        assert resp.status_code == 404
        assert json.loads(victim.read_text(encoding="utf-8")) == {"a": 1}
    finally:
        victim.unlink(missing_ok=True)


@windows_only
def test_calendar_enrich_cannot_write_outside_the_queue(client):
    from agent_friday.services.misc_engine import FLOW_QUEUE_DIR
    victim = Path(FLOW_QUEUE_DIR).parent / "pt-victim-cal.json"
    victim.unlink(missing_ok=True)
    try:
        resp = client.post("/api/calendar/enrich",
                           json={"event_id": "x/../../pt-victim-cal", "research": "r"})
        assert resp.status_code == 400
        assert not victim.exists()
    finally:
        victim.unlink(missing_ok=True)


def test_contact_research_cannot_write_outside_its_folder(client):
    from agent_friday.services.misc_engine import _contacts_research_dir
    victim = Path(_contacts_research_dir()).parent / "pt-victim-contact.md"
    victim.unlink(missing_ok=True)
    try:
        resp = client.post("/api/contacts/research", json={"name": "../pt-victim-contact"})
        assert resp.status_code == 400
        assert not victim.exists()
    finally:
        victim.unlink(missing_ok=True)


def test_skill_upload_name_cannot_leave_the_upload_folder(client):
    victim = Path(tempfile.gettempdir()) / "pt-victim-skill.zip"
    victim.unlink(missing_ok=True)
    try:
        client.post("/api/skills/import", data={
            "file": (io.BytesIO(b"not a zip"), "../pt-victim-skill.zip")},
            content_type="multipart/form-data")
        assert not victim.exists()
    finally:
        victim.unlink(missing_ok=True)


def test_recipe_save_cannot_leave_the_recipes_folder(client):
    from agent_friday.services import recipes
    victim = Path(recipes.RECIPES_DIR).parent / "pt-victim-recipe.yaml"
    victim.unlink(missing_ok=True)
    try:
        resp = client.post("/api/recipes", json={
            "name": "../pt-victim-recipe", "steps": [{"prompt": "hi"}]})
        assert resp.status_code == 400
        assert not victim.exists()
    finally:
        victim.unlink(missing_ok=True)


def test_distro_save_cannot_leave_the_distros_folder(client):
    from agent_friday.services import distributions
    victim = Path(distributions.DISTROS_DIR).parent / "pt-victim-distro.yaml"
    victim.unlink(missing_ok=True)
    try:
        resp = client.post("/api/distros", json={"name": "../pt-victim-distro"})
        assert resp.status_code == 400
        assert not victim.exists()
    finally:
        victim.unlink(missing_ok=True)


def test_pipeline_register_cannot_leave_the_definitions_folder(client):
    from agent_friday.services import creative_pipeline as cp
    victim = Path(cp.DEFS_DIR).parent / "pt-victim-pipe.json"
    victim.unlink(missing_ok=True)
    try:
        resp = client.post("/api/pipelines", json={
            "id": "../pt-victim-pipe",
            "stages": [{"instruction": "x", "output_key": "y"}]})
        assert resp.get_json().get("status") == "error"
        assert not victim.exists()
    finally:
        victim.unlink(missing_ok=True)


# ── reads ───────────────────────────────────────────────────────────────────

@windows_only
def test_briefing_read_cannot_leave_the_briefing_folders(client):
    from agent_friday.services import news_engine
    victim = Path(news_engine.HOME) / ".friday" / "pt-victim-brief.md"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("PT-SECRET", encoding="utf-8")
    try:
        resp = client.get("/api/briefing/..%5C..%5Cpt-victim-brief.md")
        assert resp.status_code == 404
        assert b"PT-SECRET" not in resp.data
    finally:
        victim.unlink(missing_ok=True)


@windows_only
def test_conversation_read_cannot_leave_the_conversations_folder(client):
    from agent_friday.services import conversations as conv
    victim = _plant_json(Path(conv._root()).parent / "pt-victim-conv" / "conversation.json",
                         {"id": "pt-victim-conv", "title": "PT-SECRET"})
    try:
        resp = client.get("/api/conversations/..%5Cpt-victim-conv")
        assert resp.status_code == 404
        assert b"PT-SECRET" not in resp.data
    finally:
        victim.unlink(missing_ok=True)


@windows_only
def test_goal_read_cannot_leave_the_goals_folder(client):
    from agent_friday.services import goals
    victim = _plant_json(Path(goals.GOALS_DIR).parent / "pt-victim-goal.json",
                         {"goal_id": "x", "title": "PT-SECRET"})
    try:
        resp = client.get("/api/goals/..%5Cpt-victim-goal")
        assert resp.status_code == 404
        assert b"PT-SECRET" not in resp.data
    finally:
        victim.unlink(missing_ok=True)


@windows_only
def test_work_proposal_read_cannot_leave_the_proposals_folder(client):
    from agent_friday.services import workflow_plan as wp
    victim = _plant_json(Path(wp.proposals_dir()).parent / "pt-victim-plan.json",
                         {"id": "x", "title": "PT-SECRET"})
    try:
        resp = client.get("/api/work/proposals/..%5Cpt-victim-plan")
        assert resp.status_code == 404
        assert b"PT-SECRET" not in resp.data
    finally:
        victim.unlink(missing_ok=True)


def test_provenance_read_cannot_leave_the_provenance_folder(client):
    from agent_friday.services import provenance
    victim = _plant_json(Path(provenance.PROVENANCE_DIR).parent / "pt-victim.jsonld",
                         {"artifact": {"content_hash": "x"}, "note": "PT-SECRET"})
    try:
        resp = client.get("/api/provenance/%2E%2E/pt-victim")
        assert b"PT-SECRET" not in resp.data
    finally:
        victim.unlink(missing_ok=True)
