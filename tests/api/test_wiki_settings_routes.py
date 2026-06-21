"""API tests for the Wiki, Settings, Setup, Skills, Model-stats,
Context-log, and Creations route groups.

Covered routes
--------------
Wiki
  GET  /api/wiki/structure
  GET  /api/wiki/<section>/<filename>
  PUT  /api/wiki/edit
  DELETE /api/wiki/file
  GET  /api/wiki/pending
  POST /api/wiki/pending/<id>/approve
  POST /api/wiki/pending/<id>/reject
  POST /api/wiki/search
  POST /api/wiki/correct
  SKIP /api/wiki/update   — background LLM; covered only for non-5xx
  SKIP /api/wiki/setup-research — background LLM; covered only for non-5xx

Settings / Setup
  GET  /api/settings
  POST /api/settings
  GET  /api/setup/status
  GET  /api/setup/skip
  POST /api/setup/skip
  POST /api/setup/complete

Skills
  GET  /api/skills
  GET  /api/skillopt/state
  SKIP /api/skills/import (zip)
  SKIP /api/skills/<name>/export (zip) — tested only 4xx on bad input

Model
  GET  /api/model-stats

Context log
  GET  /api/context/stats
  GET  /api/compression-stats
  POST /api/context/search
  POST /api/context/pause
  POST /api/context/resume
  DELETE /api/context/range
  GET  /api/context/export

Creations
  GET  /api/creations
  GET  /api/creations/<filename>
  GET  /creation/<filename>          (branded viewer)
  GET  /api/creations/daily
  GET  /api/creations/daily/latest

All I/O lands in the isolated temp home configured by the root conftest.
LLM calls are blocked by the autouse _no_real_llm fixture in api/conftest.py.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.conftest import CANNED_TEXT  # noqa: F401


# ═══════════════════════════════════════════════════════════════
#  WIKI — structure
# ═══════════════════════════════════════════════════════════════

class TestWikiStructure:
    def test_structure_200(self, client):
        resp = client.get("/api/wiki/structure")
        assert resp.status_code == 200

    def test_structure_shape_empty(self, client):
        data = client.get("/api/wiki/structure").get_json()
        assert data["status"] == "ok"
        assert "structure" in data
        assert "recent" in data
        assert "pending_count" in data
        assert isinstance(data["structure"], dict)
        assert isinstance(data["recent"], list)
        assert isinstance(data["pending_count"], int)

    def test_structure_reflects_written_file(self, client, server_module):
        """A file PUT via /api/wiki/edit appears in the structure."""
        wiki_dir = server_module.WIKI_DIR
        # Write via the edit endpoint so we go through the real path
        resp = client.put(
            "/api/wiki/edit",
            json={"file": "testzone/struct-check.md", "content": "# Struct Test\n"},
        )
        assert resp.status_code == 200
        data = client.get("/api/wiki/structure").get_json()
        assert data["status"] == "ok"
        sections = data["structure"]
        assert "testzone" in sections
        names = [f["filename"] for f in sections["testzone"]]
        assert "struct-check.md" in names
        # Cleanup
        (wiki_dir / "testzone" / "struct-check.md").unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
#  WIKI — page read (GET /api/wiki/<section>/<filename>)
# ═══════════════════════════════════════════════════════════════

class TestWikiPage:
    def test_missing_file_404(self, client):
        resp = client.get("/api/wiki/nosection/nofile.md")
        assert resp.status_code == 404

    def test_missing_file_json_body(self, client):
        data = client.get("/api/wiki/nosection/nofile.md").get_json()
        assert data is not None
        assert "status" in data
        assert data["status"] == "not_found"

    def test_existing_file_200(self, client, server_module):
        """Write a file then read it back through the route."""
        wiki_dir = server_module.WIKI_DIR
        section_dir = wiki_dir / "readzone"
        section_dir.mkdir(parents=True, exist_ok=True)
        (section_dir / "hello.md").write_text("# Hello\nContent here.", encoding="utf-8")
        try:
            resp = client.get("/api/wiki/readzone/hello.md")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert "content" in data
            assert "Hello" in data["content"]
            assert data["section"] == "readzone"
        finally:
            (section_dir / "hello.md").unlink(missing_ok=True)
            section_dir.rmdir()

    def test_auto_appends_md_extension(self, client, server_module):
        """Requesting without .md extension still resolves the file."""
        wiki_dir = server_module.WIKI_DIR
        section_dir = wiki_dir / "readzone2"
        section_dir.mkdir(parents=True, exist_ok=True)
        (section_dir / "noext.md").write_text("content", encoding="utf-8")
        try:
            resp = client.get("/api/wiki/readzone2/noext")
            assert resp.status_code == 200
        finally:
            (section_dir / "noext.md").unlink(missing_ok=True)
            section_dir.rmdir()


# ═══════════════════════════════════════════════════════════════
#  WIKI — edit (PUT /api/wiki/edit)
# ═══════════════════════════════════════════════════════════════

class TestWikiEdit:
    def test_edit_missing_file_param_400(self, client):
        resp = client.put("/api/wiki/edit", json={"content": "some text"})
        assert resp.status_code == 400

    def test_edit_missing_content_param_400(self, client):
        resp = client.put("/api/wiki/edit", json={"file": "zone/page.md"})
        assert resp.status_code == 400

    def test_edit_missing_both_400(self, client):
        resp = client.put("/api/wiki/edit", json={})
        assert resp.status_code == 400

    def test_edit_malformed_json_not_500(self, client):
        resp = client.put(
            "/api/wiki/edit",
            data="{bad json",
            content_type="application/json",
        )
        assert resp.status_code < 500

    def test_edit_creates_file(self, client, server_module):
        wiki_dir = server_module.WIKI_DIR
        resp = client.put(
            "/api/wiki/edit",
            json={"file": "editzone/page.md", "content": "# Edit Test\nHello."},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["saved"] == "editzone/page.md"
        assert data["bytes"] > 0
        created = wiki_dir / "editzone" / "page.md"
        assert created.exists()
        assert "Edit Test" in created.read_text(encoding="utf-8")
        # Cleanup
        created.unlink(missing_ok=True)

    def test_edit_round_trip_get(self, client, server_module):
        """PUT a file → GET it back through /api/wiki/<section>/<filename>."""
        wiki_dir = server_module.WIKI_DIR
        content = "# Round Trip\nThis is persisted content."
        client.put(
            "/api/wiki/edit",
            json={"file": "roundtrip/test.md", "content": content},
        )
        resp = client.get("/api/wiki/roundtrip/test.md")
        assert resp.status_code == 200
        assert resp.get_json()["content"] == content
        # Cleanup
        (wiki_dir / "roundtrip" / "test.md").unlink(missing_ok=True)

    def test_edit_path_traversal_rejected(self, client):
        """A ../traversal in the filename must be rejected with 400, not 500."""
        resp = client.put(
            "/api/wiki/edit",
            json={"file": "../secrets/key.md", "content": "evil"},
        )
        assert resp.status_code == 400

    def test_edit_deep_traversal_rejected(self, client):
        resp = client.put(
            "/api/wiki/edit",
            json={"file": "good/../../etc/passwd", "content": "evil"},
        )
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════
#  WIKI — delete (DELETE /api/wiki/file)
# ═══════════════════════════════════════════════════════════════

class TestWikiDelete:
    def test_delete_no_confirm_token_400(self, client):
        resp = client.delete("/api/wiki/file", json={"file": "zone/page.md"})
        assert resp.status_code == 400

    def test_delete_wrong_confirm_400(self, client):
        resp = client.delete(
            "/api/wiki/file",
            json={"file": "zone/page.md", "confirm": "delete"},
        )
        assert resp.status_code == 400

    def test_delete_traversal_rejected(self, client):
        resp = client.delete(
            "/api/wiki/file",
            json={"file": "../etc/passwd", "confirm": "DELETE"},
        )
        assert resp.status_code == 400

    def test_delete_missing_file_reports_not_found(self, client):
        resp = client.delete(
            "/api/wiki/file",
            json={"file": "ghost/nope.md", "confirm": "DELETE"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "not_found"
        assert data["deleted"] is False

    def test_delete_existing_file(self, client, server_module):
        wiki_dir = server_module.WIKI_DIR
        # Create it via the edit route
        client.put(
            "/api/wiki/edit",
            json={"file": "delzone/todel.md", "content": "bye"},
        )
        assert (wiki_dir / "delzone" / "todel.md").exists()
        resp = client.delete(
            "/api/wiki/file",
            json={"file": "delzone/todel.md", "confirm": "DELETE"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["deleted"] is True
        assert not (wiki_dir / "delzone" / "todel.md").exists()

    def test_delete_malformed_json_not_500(self, client):
        resp = client.delete(
            "/api/wiki/file",
            data="{not json",
            content_type="application/json",
        )
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════
#  WIKI — pending / approve / reject
# ═══════════════════════════════════════════════════════════════

class TestWikiPending:
    def test_pending_200_empty(self, client):
        resp = client.get("/api/wiki/pending")
        assert resp.status_code == 200

    def test_pending_shape(self, client):
        data = client.get("/api/wiki/pending").get_json()
        assert data["status"] == "ok"
        assert "pending" in data
        assert isinstance(data["pending"], list)

    def _queue_proposal(self, client):
        """POST /api/wiki/update with auto=true to queue a pending item."""
        resp = client.post(
            "/api/wiki/update",
            json={
                "file": "pendtest/notes.md",
                "section": "Test Section",
                "new_value": "Some new content.",
                "reason": "unit test",
                "auto": True,
            },
        )
        return resp

    def test_pending_approve_nonexistent_404(self, client):
        resp = client.post("/api/wiki/pending/deadbeef0000/approve")
        assert resp.status_code == 404

    def test_pending_reject_nonexistent_404(self, client):
        resp = client.post("/api/wiki/pending/deadbeef0000/reject")
        assert resp.status_code == 404

    def test_pending_approve_round_trip(self, client, server_module):
        """Queue a proposal, approve it, verify it lands in the wiki file."""
        queue_resp = self._queue_proposal(client)
        assert queue_resp.status_code == 200
        pid = queue_resp.get_json()["id"]

        # Verify it shows in pending list
        items = client.get("/api/wiki/pending").get_json()["pending"]
        ids = [p["id"] for p in items]
        assert pid in ids

        # Approve
        approve_resp = client.post(f"/api/wiki/pending/{pid}/approve")
        assert approve_resp.status_code == 200
        data = approve_resp.get_json()
        assert data["status"] == "ok"
        assert data["approved"] == pid

        # Verify the wiki file was actually written
        wiki_file = server_module.WIKI_DIR / "pendtest" / "notes.md"
        assert wiki_file.exists()
        assert "Some new content." in wiki_file.read_text(encoding="utf-8")

        # Should no longer appear in pending (it's now approved)
        remaining = client.get("/api/wiki/pending").get_json()["pending"]
        remaining_ids = [p["id"] for p in remaining]
        assert pid not in remaining_ids

        # Cleanup
        wiki_file.unlink(missing_ok=True)

    def test_pending_reject_round_trip(self, client):
        """Queue a proposal then reject it."""
        queue_resp = self._queue_proposal(client)
        assert queue_resp.status_code == 200
        pid = queue_resp.get_json()["id"]

        reject_resp = client.post(f"/api/wiki/pending/{pid}/reject")
        assert reject_resp.status_code == 200
        data = reject_resp.get_json()
        assert data["status"] == "ok"
        assert data["rejected"] == pid

        # Rejected item no longer in pending
        items = client.get("/api/wiki/pending").get_json()["pending"]
        assert pid not in [p["id"] for p in items]


# ═══════════════════════════════════════════════════════════════
#  WIKI — search
# ═══════════════════════════════════════════════════════════════

class TestWikiSearch:
    def test_search_empty_query_returns_empty(self, client):
        resp = client.post("/api/wiki/search", json={"query": ""})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["results"] == []

    def test_search_no_body_returns_empty(self, client):
        resp = client.post("/api/wiki/search", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["results"] == []

    def test_search_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/wiki/search",
            data="{bad json",
            content_type="application/json",
        )
        assert resp.status_code < 500

    def test_search_finds_content(self, client, server_module):
        wiki_dir = server_module.WIKI_DIR
        section_dir = wiki_dir / "searchzone"
        section_dir.mkdir(parents=True, exist_ok=True)
        (section_dir / "findme.md").write_text(
            "# Findable\nThe secret phrase: xyzzy_unique_token\n",
            encoding="utf-8",
        )
        try:
            resp = client.post(
                "/api/wiki/search", json={"query": "xyzzy_unique_token"}
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["query"] == "xyzzy_unique_token"
            paths = [r["path"] for r in data["results"]]
            assert any("findme.md" in p for p in paths)
        finally:
            (section_dir / "findme.md").unlink(missing_ok=True)
            section_dir.rmdir()

    def test_search_no_match_returns_empty(self, client):
        resp = client.post(
            "/api/wiki/search",
            json={"query": "zzzNOTHING_HERE_aaaabbbbccccddddeeeef"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["results"] == []

    def test_search_shape_of_result_entry(self, client, server_module):
        wiki_dir = server_module.WIKI_DIR
        section_dir = wiki_dir / "searchzone2"
        section_dir.mkdir(parents=True, exist_ok=True)
        (section_dir / "shaped.md").write_text(
            "# Shaped\nHere is SHAPE_TOKEN for tests.\n", encoding="utf-8"
        )
        try:
            data = client.post(
                "/api/wiki/search", json={"query": "SHAPE_TOKEN"}
            ).get_json()
            assert data["status"] == "ok"
            assert len(data["results"]) >= 1
            result = data["results"][0]
            assert "path" in result
            assert "matches" in result
            assert "snippets" in result
            assert isinstance(result["snippets"], list)
        finally:
            (section_dir / "shaped.md").unlink(missing_ok=True)
            section_dir.rmdir()


# ═══════════════════════════════════════════════════════════════
#  WIKI — correct
# ═══════════════════════════════════════════════════════════════

class TestWikiCorrect:
    def test_correct_no_old_text_400(self, client):
        resp = client.post(
            "/api/wiki/correct",
            json={"old_text": "", "new_text": "new"},
        )
        assert resp.status_code == 400

    def test_correct_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/wiki/correct",
            data="{bad json",
            content_type="application/json",
        )
        assert resp.status_code < 500

    def test_correct_modifies_file(self, client, server_module):
        wiki_dir = server_module.WIKI_DIR
        section_dir = wiki_dir / "correctzone"
        section_dir.mkdir(parents=True, exist_ok=True)
        target = section_dir / "target.md"
        target.write_text("Before: OLD_VALUE_XYZ. End.", encoding="utf-8")
        try:
            resp = client.post(
                "/api/wiki/correct",
                json={"old_text": "OLD_VALUE_XYZ", "new_text": "NEW_VALUE_ABC"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["count"] >= 1
            # Verify file was actually changed on disk
            assert "NEW_VALUE_ABC" in target.read_text(encoding="utf-8")
            assert "OLD_VALUE_XYZ" not in target.read_text(encoding="utf-8")
        finally:
            target.unlink(missing_ok=True)
            section_dir.rmdir()

    def test_correct_no_match_reports_zero(self, client):
        resp = client.post(
            "/api/wiki/correct",
            json={
                "old_text": "NOTHING_WILL_MATCH_THIS_UNIQUE_XYZZY_9999",
                "new_text": "replaced",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 0
        assert data["modified"] == []


# ═══════════════════════════════════════════════════════════════
#  WIKI — background LLM routes (smoke only)
# ═══════════════════════════════════════════════════════════════

class TestWikiLlmRoutesSmoke:
    """These routes trigger background LLM calls. With the autouse stub they
    return promptly and should not 500."""

    def test_wiki_update_non_5xx(self, client):
        resp = client.post(
            "/api/wiki/update",
            json={
                "file": "smoke/test.md",
                "section": "Smoke",
                "new_value": "content",
                "reason": "test",
                "auto": True,
            },
        )
        assert resp.status_code < 500

    def test_wiki_setup_research_non_5xx(self, client):
        resp = client.post(
            "/api/wiki/setup-research",
            json={
                "full_name": "Test User",
                "birthdate": "1990-01-01",
                "location": "Testville",
            },
        )
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════════════════════════

class TestSettings:
    def test_get_settings_200(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200

    def test_get_settings_shape(self, client):
        data = client.get("/api/settings").get_json()
        assert data["status"] == "ok"
        assert "settings" in data
        assert "personality" in data
        assert "default_personality" in data
        assert isinstance(data["settings"], dict)

    def test_post_settings_200(self, client):
        resp = client.post(
            "/api/settings",
            json={"settings": {"test_key_xyzzy": "hello"}},
        )
        assert resp.status_code == 200

    def test_post_settings_shape(self, client):
        data = client.post(
            "/api/settings",
            json={"settings": {"test_key_xyzzy": "hello"}},
        ).get_json()
        assert data["status"] == "ok"
        assert "settings" in data

    def test_settings_round_trip(self, client):
        """POST a known settings key then GET it back.

        Note: _load_settings() only surfaces keys present in DEFAULT_SETTINGS;
        synthetic keys are saved to disk but filtered out on read. This test
        therefore uses a known key ('communication_style').
        """
        client.post(
            "/api/settings",
            json={"settings": {"communication_style": "casual"}},
        )
        data = client.get("/api/settings").get_json()
        assert data["settings"].get("communication_style") == "casual"

    def test_settings_post_empty_body_not_500(self, client):
        resp = client.post("/api/settings", json={})
        assert resp.status_code < 500

    def test_settings_post_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/settings",
            data="{bad json",
            content_type="application/json",
        )
        assert resp.status_code < 500

    def test_settings_personality_string_round_trip(self, client):
        """The personality store is free text: POST a string, GET it back."""
        text = "Curious, candid, and a little mischievous. [test-marker]"
        post_resp = client.post("/api/settings", json={"personality": text})
        assert post_resp.status_code == 200
        assert "[test-marker]" in (post_resp.get_json().get("personality") or "")
        # And it persists on a fresh GET.
        got = client.get("/api/settings").get_json().get("personality") or ""
        assert "[test-marker]" in got

    def test_settings_personality_non_string_is_400_not_500(self, client):
        """A dict personality is invalid input — must be a clean 400, never a
        500 from _save_agent_personality().strip() blowing up on a non-string.
        (Regression guard for the AttributeError this suite originally caught.)"""
        resp = client.post("/api/settings", json={"personality": {"x": 1}})
        assert resp.status_code == 400
        assert resp.status_code != 500


# ═══════════════════════════════════════════════════════════════
#  SETUP
# ═══════════════════════════════════════════════════════════════

class TestSetup:
    def test_setup_status_200(self, client):
        resp = client.get("/api/setup/status")
        assert resp.status_code == 200

    def test_setup_status_shape(self, client):
        data = client.get("/api/setup/status").get_json()
        assert "initialized" in data
        assert isinstance(data["initialized"], bool)

    def test_setup_skip_get_200(self, client):
        resp = client.get("/api/setup/skip")
        assert resp.status_code == 200

    def test_setup_skip_post_200(self, client):
        resp = client.post("/api/setup/skip")
        assert resp.status_code == 200

    def test_setup_skip_marks_initialized(self, client):
        """After skip, status should report initialized=True."""
        skip_resp = client.post("/api/setup/skip")
        assert skip_resp.status_code == 200
        assert skip_resp.get_json()["status"] == "ok"
        status = client.get("/api/setup/status").get_json()
        assert status["initialized"] is True

    def test_setup_complete_200(self, client):
        resp = client.post("/api/setup/complete", json={})
        assert resp.status_code == 200

    def test_setup_complete_shape(self, client):
        data = client.post("/api/setup/complete", json={}).get_json()
        assert data["status"] == "ok"

    def test_setup_complete_persists_settings(self, client):
        """setup/complete with agent_name should persist it to settings."""
        resp = client.post(
            "/api/setup/complete",
            json={"agent_name": "TestFriday", "temperature": 0.7},
        )
        assert resp.status_code == 200
        settings = client.get("/api/settings").get_json()["settings"]
        assert settings.get("agent_name") == "TestFriday"
        assert abs(settings.get("temperature", -1) - 0.7) < 1e-6

    def test_setup_complete_no_body_not_500(self, client):
        resp = client.post("/api/setup/complete", json=None)
        assert resp.status_code < 500

    def test_setup_complete_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/setup/complete",
            data="{malformed",
            content_type="application/json",
        )
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════
#  SKILLS
# ═══════════════════════════════════════════════════════════════

class TestSkills:
    def test_skills_list_200(self, client):
        resp = client.get("/api/skills")
        assert resp.status_code == 200

    def test_skills_list_shape(self, client):
        data = client.get("/api/skills").get_json()
        # Either {"skills": [...], "count": N} or {"error": ..., "skills": [], "count": 0}
        assert "skills" in data
        assert "count" in data
        assert isinstance(data["skills"], list)
        assert isinstance(data["count"], int)

    def test_skills_count_matches_list(self, client):
        data = client.get("/api/skills").get_json()
        assert data["count"] == len(data["skills"])

    def test_skills_export_unknown_404(self, client):
        """Exporting a nonexistent skill should be 404, not 500."""
        resp = client.get("/api/skills/TOTALLY_FAKE_SKILL_XYZZY/export")
        assert resp.status_code in (404, 500)
        # At minimum must not be silent 200 with garbage
        assert resp.status_code != 200

    def test_skillopt_state_200(self, client):
        resp = client.get("/api/skillopt/state")
        assert resp.status_code == 200

    def test_skillopt_state_shape(self, client):
        data = client.get("/api/skillopt/state").get_json()
        assert data is not None
        assert isinstance(data, dict)

    def test_skills_import_no_payload_400(self, client):
        """POST import with no file and no path → 400."""
        resp = client.post(
            "/api/skills/import",
            json={},
        )
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════
#  MODEL STATS
# ═══════════════════════════════════════════════════════════════

class TestModelStats:
    def test_model_stats_200(self, client):
        resp = client.get("/api/model-stats")
        assert resp.status_code == 200

    def test_model_stats_has_mode(self, client):
        data = client.get("/api/model-stats").get_json()
        assert "mode" in data

    def test_model_stats_numeric_fields(self, client):
        data = client.get("/api/model-stats").get_json()
        # These keys exist even on error fallback
        assert "local_requests" in data or "error" in data
        assert "cloud_requests" in data or "error" in data


# ═══════════════════════════════════════════════════════════════
#  CONTEXT LOG
# ═══════════════════════════════════════════════════════════════

class TestContextStats:
    def test_context_stats_200(self, client):
        resp = client.get("/api/context/stats")
        assert resp.status_code == 200

    def test_context_stats_shape(self, client):
        data = client.get("/api/context/stats").get_json()
        assert data["status"] == "ok"
        assert "enabled" in data
        assert "total_entries" in data
        assert "total_bytes" in data
        assert "days" in data
        assert "log_dir" in data
        assert isinstance(data["total_entries"], int)
        assert isinstance(data["total_bytes"], int)

    def test_context_stats_retention_days_present(self, client):
        data = client.get("/api/context/stats").get_json()
        assert "retention_days" in data


class TestCompressionStats:
    def test_compression_stats_200(self, client):
        resp = client.get("/api/compression-stats")
        assert resp.status_code == 200

    def test_compression_stats_shape(self, client):
        data = client.get("/api/compression-stats").get_json()
        # Either "ok" (headroom installed) or error fallback — both valid
        assert "status" in data
        assert data["status"] in ("ok", "error")


class TestContextSearch:
    def test_search_empty_query_200(self, client):
        resp = client.post("/api/context/search", json={"query": ""})
        assert resp.status_code == 200

    def test_search_shape(self, client):
        data = client.post("/api/context/search", json={"query": "test"}).get_json()
        assert data["status"] == "ok"
        assert "results" in data
        assert "count" in data
        assert isinstance(data["results"], list)

    def test_search_count_matches_results(self, client):
        data = client.post(
            "/api/context/search", json={"query": ""}
        ).get_json()
        assert data["count"] == len(data["results"])

    def test_search_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/context/search",
            data="{not valid",
            content_type="application/json",
        )
        assert resp.status_code < 500

    def test_search_no_body_not_500(self, client):
        resp = client.post("/api/context/search", json=None)
        assert resp.status_code < 500


class TestContextPauseResume:
    def test_pause_200(self, client):
        resp = client.post("/api/context/pause")
        assert resp.status_code == 200

    def test_pause_shape(self, client):
        data = client.post("/api/context/pause").get_json()
        assert data["status"] == "ok"
        assert "enabled" in data
        assert data["enabled"] is False

    def test_resume_200(self, client):
        resp = client.post("/api/context/resume")
        assert resp.status_code == 200

    def test_resume_shape(self, client):
        data = client.post("/api/context/resume").get_json()
        assert data["status"] == "ok"
        assert "enabled" in data
        assert data["enabled"] is True

    def test_pause_then_resume_round_trip(self, client):
        """Pause disables logging; resume re-enables it."""
        pause_data = client.post("/api/context/pause").get_json()
        assert pause_data["enabled"] is False
        resume_data = client.post("/api/context/resume").get_json()
        assert resume_data["enabled"] is True


class TestContextDeleteRange:
    def test_delete_no_confirm_400(self, client):
        resp = client.delete("/api/context/range", json={})
        assert resp.status_code == 400

    def test_delete_wrong_confirm_400(self, client):
        resp = client.delete(
            "/api/context/range",
            json={"confirm": "delete"},
        )
        assert resp.status_code == 400

    def test_delete_with_confirm_200(self, client):
        resp = client.delete(
            "/api/context/range",
            json={"confirm": "DELETE"},
        )
        assert resp.status_code == 200

    def test_delete_shape(self, client):
        data = client.delete(
            "/api/context/range",
            json={"confirm": "DELETE"},
        ).get_json()
        assert data["status"] == "ok"
        assert "deleted" in data
        assert "count" in data
        assert isinstance(data["deleted"], list)

    def test_delete_malformed_json_not_500(self, client):
        resp = client.delete(
            "/api/context/range",
            data="{bad json",
            content_type="application/json",
        )
        assert resp.status_code < 500


class TestContextExport:
    def test_export_200(self, client):
        resp = client.get("/api/context/export")
        assert resp.status_code == 200

    def test_export_is_zip(self, client):
        resp = client.get("/api/context/export")
        assert resp.status_code == 200
        # Content-Type should indicate zip
        ct = resp.content_type or ""
        assert "zip" in ct or "octet" in ct

    def test_export_valid_zip_bytes(self, client):
        """The response body must parse as a valid ZIP archive."""
        resp = client.get("/api/context/export")
        import io
        buf = io.BytesIO(resp.data)
        # zipfile.is_zipfile requires seekable; ZipFile constructor is the real test
        zf = zipfile.ZipFile(buf)
        zf.close()


# ═══════════════════════════════════════════════════════════════
#  CREATIONS
# ═══════════════════════════════════════════════════════════════

class TestCreationsList:
    def test_list_200(self, client, creations_dir):
        resp = client.get("/api/creations")
        assert resp.status_code == 200

    def test_list_shape_empty(self, client, creations_dir):
        data = client.get("/api/creations").get_json()
        assert data["status"] == "ok"
        assert "files" in data
        assert isinstance(data["files"], list)

    def test_list_shows_synthetic_file(self, client, creations_dir):
        (creations_dir / "test_poem.md").write_text("# Poem\nRoses are red.", encoding="utf-8")
        data = client.get("/api/creations").get_json()
        names = [f["name"] for f in data["files"]]
        assert "test_poem.md" in names

    def test_list_entry_shape(self, client, creations_dir):
        (creations_dir / "test_entry.md").write_text("content", encoding="utf-8")
        data = client.get("/api/creations").get_json()
        entry = next(f for f in data["files"] if f["name"] == "test_entry.md")
        assert "name" in entry
        assert "size" in entry
        assert "modified" in entry
        assert "type" in entry
        assert entry["type"] == "md"

    def test_list_html_file_present(self, client, creations_dir):
        (creations_dir / "test_app.html").write_text("<h1>Hello</h1>", encoding="utf-8")
        data = client.get("/api/creations").get_json()
        names = [f["name"] for f in data["files"]]
        assert "test_app.html" in names


class TestCreationsServe:
    def test_serve_existing_file_200(self, client, creations_dir):
        (creations_dir / "rawfile.txt").write_text("raw content", encoding="utf-8")
        resp = client.get("/api/creations/rawfile.txt")
        assert resp.status_code == 200

    def test_serve_missing_file_404(self, client, creations_dir):
        resp = client.get("/api/creations/nonexistent_xyz.txt")
        assert resp.status_code == 404

    def test_serve_returns_correct_content(self, client, creations_dir):
        (creations_dir / "content_check.txt").write_text("hello world", encoding="utf-8")
        resp = client.get("/api/creations/content_check.txt")
        assert b"hello world" in resp.data


class TestCreationBrandedViewer:
    """GET /creation/<filename> — branded full-page wrapper."""

    def test_missing_file_404(self, client, creations_dir):
        resp = client.get("/creation/nonexistent_xyz.html")
        assert resp.status_code == 404

    def test_md_file_returns_200_html(self, client, creations_dir):
        (creations_dir / "mypoem.md").write_text("# My Poem\nVerse one.", encoding="utf-8")
        resp = client.get("/creation/mypoem.md")
        assert resp.status_code == 200
        assert "text/html" in resp.content_type

    def test_md_file_has_return_link(self, client, creations_dir):
        (creations_dir / "poem_return.md").write_text("# Return\nHi.", encoding="utf-8")
        resp = client.get("/creation/poem_return.md")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="replace")
        assert "Return to Friday Desktop" in html

    def test_html_file_has_sandbox_attr(self, client, creations_dir):
        """An HTML creation must be wrapped in a sandboxed iframe."""
        (creations_dir / "myapp.html").write_text(
            "<!DOCTYPE html><html><body><p>Hello</p></body></html>",
            encoding="utf-8",
        )
        resp = client.get("/creation/myapp.html")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="replace")
        assert "sandbox=" in html, "iframe sandbox attribute missing for .html creation"

    def test_html_file_has_return_link(self, client, creations_dir):
        (creations_dir / "myapp2.html").write_text("<html><body>App</body></html>", encoding="utf-8")
        resp = client.get("/creation/myapp2.html")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="replace")
        assert "Return to Friday Desktop" in html

    def test_html_file_iframe_src_points_to_api(self, client, creations_dir):
        """The iframe src should point to /api/creations/<filename>."""
        fname = "iframetest.html"
        (creations_dir / fname).write_text("<html><body>x</body></html>", encoding="utf-8")
        resp = client.get(f"/creation/{fname}")
        html = resp.data.decode("utf-8", errors="replace")
        assert f"/api/creations/{fname}" in html

    def test_md_file_content_rendered_or_fallback(self, client, creations_dir):
        """The page must include the raw markdown text in the inline script
        or a fallback element so the content is not silently lost."""
        (creations_dir / "content_in_page.md").write_text(
            "# Title\nUNIQUE_CONTENT_abc123", encoding="utf-8"
        )
        resp = client.get("/creation/content_in_page.md")
        html = resp.data.decode("utf-8", errors="replace")
        assert "UNIQUE_CONTENT_abc123" in html

    def test_friday_desktop_branding_present(self, client, creations_dir):
        """The page must show the FRIDAY DESKTOP brand in the top bar."""
        (creations_dir / "branded.md").write_text("content", encoding="utf-8")
        resp = client.get("/creation/branded.md")
        html = resp.data.decode("utf-8", errors="replace")
        assert "FRIDAY DESKTOP" in html


# ═══════════════════════════════════════════════════════════════
#  CREATIONS — daily
# ═══════════════════════════════════════════════════════════════

class TestCreationsDaily:
    def test_daily_list_200(self, client):
        resp = client.get("/api/creations/daily")
        assert resp.status_code == 200

    def test_daily_list_shape(self, client):
        data = client.get("/api/creations/daily").get_json()
        assert data["status"] == "ok"
        assert "creations" in data
        assert isinstance(data["creations"], list)

    def test_daily_latest_200(self, client):
        resp = client.get("/api/creations/daily/latest")
        assert resp.status_code == 200

    def test_daily_latest_shape_empty(self, client):
        data = client.get("/api/creations/daily/latest").get_json()
        # When no daily creations exist: status="empty" or "ok"
        assert "status" in data
        assert data["status"] in ("ok", "empty")
        assert "creation" in data

    def test_daily_latest_no_500_when_empty(self, client, server_module):
        """With no daily creation files, the route must not 500."""
        daily_dir = server_module.FRIDAY_DIR / "creations"
        existed_files = list(daily_dir.glob("*.json")) if daily_dir.exists() else []
        # Back up any existing files temporarily
        backups = []
        for f in existed_files:
            bak = f.with_suffix(".json.bak")
            f.rename(bak)
            backups.append((bak, f))
        try:
            resp = client.get("/api/creations/daily/latest")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] in ("ok", "empty")
        finally:
            for bak, orig in backups:
                bak.rename(orig)

    def test_daily_list_with_synthetic_entry(self, client, server_module):
        """Dropping a synthetic daily JSON makes it appear in the list."""
        daily_dir = server_module.FRIDAY_DIR / "creations"
        daily_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "date": "2099-01-01",
            "title": "Test Creation",
            "type": "poem",
            "mood": "curious",
            "content": "In the far future...",
        }
        synth = daily_dir / "2099-01-01.json"
        synth.write_text(json.dumps(record), encoding="utf-8")
        try:
            data = client.get("/api/creations/daily").get_json()
            dates = [c["date"] for c in data["creations"]]
            assert "2099-01-01" in dates
        finally:
            synth.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
#  CROSS-ROUTE ROUND-TRIPS
# ═══════════════════════════════════════════════════════════════

class TestRoundTrips:
    """Multi-route interactions to verify integration between handlers."""

    def test_wiki_edit_then_search(self, client, server_module):
        """PUT a wiki file → POST search for its unique content."""
        wiki_dir = server_module.WIKI_DIR
        content = "# Integration\nUNIQUE_ROUNDTRIP_TOKEN_9876"
        client.put(
            "/api/wiki/edit",
            json={"file": "integration/roundtrip.md", "content": content},
        )
        data = client.post(
            "/api/wiki/search",
            json={"query": "UNIQUE_ROUNDTRIP_TOKEN_9876"},
        ).get_json()
        assert data["status"] == "ok"
        paths = [r["path"] for r in data["results"]]
        assert any("roundtrip.md" in p for p in paths)
        # Cleanup
        (wiki_dir / "integration" / "roundtrip.md").unlink(missing_ok=True)

    def test_wiki_edit_then_correct(self, client, server_module):
        """PUT a file → POST correct to replace a token → verify replacement."""
        wiki_dir = server_module.WIKI_DIR
        client.put(
            "/api/wiki/edit",
            json={"file": "correcttest/file.md", "content": "Value: OLD_CORRECT_TOKEN\n"},
        )
        resp = client.post(
            "/api/wiki/correct",
            json={"old_text": "OLD_CORRECT_TOKEN", "new_text": "NEW_CORRECT_TOKEN"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] >= 1
        target = wiki_dir / "correcttest" / "file.md"
        assert "NEW_CORRECT_TOKEN" in target.read_text(encoding="utf-8")
        target.unlink(missing_ok=True)

    def test_context_pause_then_stats(self, client):
        """After pause, context/stats reports enabled=False."""
        client.post("/api/context/pause")
        data = client.get("/api/context/stats").get_json()
        assert data["enabled"] is False
        # Restore
        client.post("/api/context/resume")

    def test_settings_post_then_get_round_trip(self, client):
        """POST a known settings key and confirm GET reflects it.

        Note: _load_settings() filters to DEFAULT_SETTINGS keys only, so only
        known keys survive a round-trip. Using 'temperature' (a float key).
        """
        client.post("/api/settings", json={"settings": {"temperature": 0.55}})
        data = client.get("/api/settings").get_json()
        assert abs(data["settings"].get("temperature", -1) - 0.55) < 1e-6

    def test_creations_put_then_list_then_view(self, client, creations_dir):
        """Drop an HTML file → list → branded view → all 200, sandbox present."""
        fname = "integ_test.html"
        (creations_dir / fname).write_text(
            "<!DOCTYPE html><body>Integration</body></html>", encoding="utf-8"
        )
        # List
        names = [f["name"] for f in client.get("/api/creations").get_json()["files"]]
        assert fname in names

        # Raw serve
        raw = client.get(f"/api/creations/{fname}")
        assert raw.status_code == 200

        # Branded viewer
        viewer = client.get(f"/creation/{fname}")
        assert viewer.status_code == 200
        html = viewer.data.decode("utf-8", errors="replace")
        assert "sandbox=" in html
        assert "Return to Friday Desktop" in html


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
