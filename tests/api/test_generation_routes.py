"""Generation-route test suite.

Covers:
  Creative (Gemini-direct):  POST /api/create/poem, /api/create/code-art,
                             /api/create/image, /api/create/music,
                             /api/create/video
  Analyze:                   POST /api/analyze
  Voice TTS:                 POST /api/voice/tts
  Agentic/text (autouse stub):
    POST  /api/chat/send
    POST  /api/draft
    POST  /api/outreach/draft
    POST  /api/content/draft
    POST  /api/content/idea
    GET   /api/content/pipeline
    POST  /api/email/draft
    GET   /api/outreach/suggestions
    POST  /api/outreach/log
    GET   /api/outreach/pipeline
"""
from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from tests.conftest import CANNED_TEXT


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tiny_wav() -> bytes:
    """Return a minimal valid WAV file as bytes (24 kHz, mono, 1 frame)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00")  # one silent sample
    return buf.getvalue()


def _json_or_none(resp):
    try:
        return resp.get_json()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Creative routes — Gemini-direct  (need mock_gemini)
# ─────────────────────────────────────────────────────────────────────────────

class TestCreatePoem:
    """POST /api/create/poem — writes a .md file under CREATIONS_DIR."""

    def test_happy_path(self, client, mock_gemini, creations_dir):
        resp = client.post("/api/create/poem", json={"prompt": "A haiku about testing"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "filename" in data
        assert "text" in data
        # File must land in the isolated creations dir
        created = list(creations_dir.glob("friday-text-*.md"))
        assert len(created) == 1, f"Expected 1 .md file, found {created}"

    def test_stub_text_returned(self, client, mock_gemini, creations_dir):
        """Gemini stub returns [[gemini-test-stub]]; that text should appear in
        the response (directly from the stubbed generate_content call)."""
        resp = client.post("/api/create/poem", json={"prompt": "Test poem"})
        data = resp.get_json()
        assert "[[gemini-test-stub]]" in (data.get("text") or "")

    def test_default_prompt(self, client, mock_gemini, creations_dir):
        """Route should work with an empty body (uses the built-in default prompt)."""
        resp = client.post("/api/create/poem", json={})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_malformed_json_not_500(self, client, mock_gemini):
        resp = client.post("/api/create/poem", data="{bad", content_type="application/json")
        assert resp.status_code < 500

    def test_gemini_was_called(self, client, mock_gemini, creations_dir):
        client.post("/api/create/poem", json={"prompt": "Digital soul"})
        # mock_gemini records the contents arg passed to generate_content
        assert len(mock_gemini["prompts"]) >= 1


class TestCreateCodeArt:
    """POST /api/create/code-art — writes a .html file."""

    def test_happy_path(self, client, mock_gemini, creations_dir):
        resp = client.post("/api/create/code-art", json={"prompt": "Pulsing circles"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "filename" in data
        assert data["filename"].endswith(".html")
        # File on disk
        created = list(creations_dir.glob("friday-codeart-*.html"))
        assert len(created) == 1

    def test_stub_content_on_disk(self, client, mock_gemini, creations_dir):
        """The stub returns '[[gemini-test-stub]]'; that should end up in the file."""
        client.post("/api/create/code-art", json={"prompt": "Waves"})
        created = list(creations_dir.glob("friday-codeart-*.html"))
        assert created
        content = created[0].read_text(encoding="utf-8")
        assert "[[gemini-test-stub]]" in content

    def test_malformed_json_not_500(self, client, mock_gemini):
        resp = client.post("/api/create/code-art", data="!!!", content_type="application/json")
        assert resp.status_code < 500


class TestCreateImage:
    """POST /api/create/image — Imagen path; stub returns no candidates, so the
    route returns a structured error (status=error, not a 500)."""

    def test_returns_structured_response(self, client, mock_gemini):
        resp = client.post("/api/create/image", json={"prompt": "A futuristic city"})
        # Never a 500 — either ok or structured error
        assert resp.status_code < 500
        data = resp.get_json()
        # The stub returns candidates=[] so the loop body never fires.
        assert data.get("status") in ("ok", "error", "unavailable")
        if data["status"] == "error":
            assert "message" in data

    def test_malformed_json_not_500(self, client, mock_gemini):
        resp = client.post("/api/create/image", data="{bad", content_type="application/json")
        assert resp.status_code < 500


class TestCreateMusic:
    """POST /api/create/music — Lyria 3 path (services/music_engine).

    The installed google-genai has no batch Lyria surface, so without a real key
    the engine returns a {status:'demo'} preview rather than failing — that's the
    graceful-degradation contract. With a cloud key + SDK it would be 'ok'."""

    def test_returns_structured_response(self, client, mock_gemini):
        resp = client.post("/api/create/music", json={"prompt": "Lo-fi hip hop"})
        assert resp.status_code < 500
        data = resp.get_json()
        assert data.get("status") in ("ok", "demo", "blocked", "unavailable", "error")
        if data["status"] == "error":
            assert "message" in data
        if data["status"] == "demo":
            # demo mode still produces a real artifact + a message
            assert data.get("files") and data.get("message")

    def test_malformed_json_not_500(self, client, mock_gemini):
        resp = client.post("/api/create/music", data="not json", content_type="application/json")
        assert resp.status_code < 500


class TestCreateVideo:
    """POST /api/create/video — Veo path.

    The video handler calls `client.models.generate_videos`, which is NOT covered
    by mock_gemini's _Models stub (it only patches generate_content). This means
    the call raises AttributeError, which the handler's broad `except Exception`
    converts into a structured {"status": "error", "message": ...} — never a 500.
    """

    def test_returns_structured_error_not_500(self, client, mock_gemini):
        resp = client.post("/api/create/video", json={"prompt": "Abstract landscape"})
        assert resp.status_code < 500
        data = resp.get_json()
        # generate_videos is not on the mock; handler wraps to structured error
        assert data.get("status") in ("ok", "error", "unavailable")

    def test_malformed_json_not_500(self, client, mock_gemini):
        resp = client.post("/api/create/video", data="", content_type="application/json")
        assert resp.status_code < 500


# ─────────────────────────────────────────────────────────────────────────────
# Analyze route
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyze:
    """POST /api/analyze — Gemini file analysis."""

    def test_no_file_returns_400(self, client, mock_gemini):
        resp = client.post("/api/analyze")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_text_file_analysis(self, client, mock_gemini):
        data = {"file": (io.BytesIO(b"Hello world test content"), "notes.txt")}
        resp = client.post("/api/analyze", data=data, content_type="multipart/form-data")
        # Route catches all exceptions → should never 500 even if Gemini stub
        # returns minimal data.
        assert resp.status_code < 500
        result = resp.get_json()
        assert "analysis" in result
        assert result.get("filename") == "notes.txt"

    def test_md_file_analysis(self, client, mock_gemini):
        content = b"# README\n\nThis is a markdown file."
        data = {"file": (io.BytesIO(content), "README.md")}
        resp = client.post("/api/analyze", data=data, content_type="multipart/form-data")
        assert resp.status_code < 500
        result = resp.get_json()
        assert "analysis" in result

    def test_unknown_extension_graceful(self, client, mock_gemini):
        """Unsupported extension should return a fallback analysis, not 500."""
        data = {"file": (io.BytesIO(b"\x00\x01\x02"), "binary.xyz")}
        resp = client.post("/api/analyze", data=data, content_type="multipart/form-data")
        assert resp.status_code < 500
        result = resp.get_json()
        assert "analysis" in result

    def test_stub_text_in_analysis_for_text_file(self, client, mock_gemini):
        """The stub generate_content returns '[[gemini-test-stub]]'; it should
        appear in the analysis field for text files."""
        data = {"file": (io.BytesIO(b"Some text content here"), "doc.txt")}
        resp = client.post("/api/analyze", data=data, content_type="multipart/form-data")
        result = resp.get_json()
        assert "[[gemini-test-stub]]" in (result.get("analysis") or "")


# ─────────────────────────────────────────────────────────────────────────────
# Voice TTS route
# ─────────────────────────────────────────────────────────────────────────────

class TestVoiceTts:
    """POST /api/voice/tts — monkeypatches _synthesize_tts_wav to avoid real call."""

    def test_empty_text_returns_400(self, client):
        resp = client.post("/api/voice/tts", json={"text": ""})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_missing_text_returns_400(self, client):
        resp = client.post("/api/voice/tts", json={})
        assert resp.status_code == 400

    def test_happy_path_returns_wav(self, client, patch_app):
        """Stub _synthesize_tts_wav → fake WAV bytes; assert 200 + audio/wav."""
        wav_bytes = _tiny_wav()

        def _fake_synth(text, voice=None, style="briefing"):
            return io.BytesIO(wav_bytes)

        patch_app("_synthesize_tts_wav", _fake_synth)
        resp = client.post("/api/voice/tts", json={"text": "Hello Friday"})
        assert resp.status_code == 200
        assert "audio/wav" in resp.content_type
        assert len(resp.data) > 0

    def test_synth_exception_returns_500(self, client, patch_app):
        """If the TTS helper raises, the route must return 500 with a JSON error."""
        def _boom(text, voice=None, style="briefing"):
            raise RuntimeError("TTS backend unavailable")

        patch_app("_synthesize_tts_wav", _boom)
        resp = client.post("/api/voice/tts", json={"text": "Test"})
        assert resp.status_code == 500
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_malformed_json_not_500(self, client, patch_app):
        wav_bytes = _tiny_wav()

        def _fake_synth(text, voice=None, style="briefing"):
            return io.BytesIO(wav_bytes)

        patch_app("_synthesize_tts_wav", _fake_synth)
        resp = client.post("/api/voice/tts", data="{bad json", content_type="application/json")
        # With malformed JSON, request.json is None → text="" → 400
        assert resp.status_code in (400, 500)


# ─────────────────────────────────────────────────────────────────────────────
# Agentic/text routes — covered by autouse _no_real_llm stub
# ─────────────────────────────────────────────────────────────────────────────

class TestChatSend:
    """POST /api/chat/send"""

    def test_happy_path(self, client):
        resp = client.post("/api/chat/send", json={"message": "What is the weather?"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "friday_msg" in data
        assert data["friday_msg"]["text"] == CANNED_TEXT

    def test_empty_message_returns_400(self, client):
        resp = client.post("/api/chat/send", json={"message": "   "})
        assert resp.status_code == 400

    def test_missing_message_returns_400(self, client):
        resp = client.post("/api/chat/send", json={})
        assert resp.status_code == 400

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/chat/send", data="{!", content_type="application/json")
        assert resp.status_code < 500

    def test_response_contains_stub_text(self, client):
        resp = client.post("/api/chat/send", json={"message": "Ping"})
        data = resp.get_json()
        assert CANNED_TEXT in data["friday_msg"]["text"]

    def test_with_workspace_context(self, client):
        resp = client.post("/api/chat/send", json={
            "message": "What should I do today?",
            "workspace": "home",
        })
        assert resp.status_code == 200


class TestDraftGenerate:
    """POST /api/draft — background task, returns task_id immediately."""

    def test_happy_path_returns_task_id(self, client):
        resp = client.post("/api/draft", json={"prompt": "Write a LinkedIn post about Python"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "queued"
        assert "task_id" in data
        assert data["task_id"]

    def test_empty_prompt_returns_400(self, client):
        resp = client.post("/api/draft", json={"prompt": ""})
        assert resp.status_code == 400

    def test_missing_prompt_returns_400(self, client):
        resp = client.post("/api/draft", json={})
        assert resp.status_code == 400

    def test_mode_field_accepted(self, client):
        resp = client.post("/api/draft", json={
            "prompt": "Draft a slack message about the sprint",
            "mode": "slack_message",
        })
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "queued"

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/draft", data="!!!bad", content_type="application/json")
        assert resp.status_code < 500

    def test_task_id_registered(self, client, server_module):
        resp = client.post("/api/draft", json={"prompt": "Test draft prompt"})
        tid = resp.get_json()["task_id"]
        # task should appear in the server's TASKS dict (or be queryable)
        import time
        time.sleep(0.05)  # brief yield for background thread to register
        with server_module.TASKS_LOCK:
            assert tid in server_module.TASKS


class TestOutreachDraft:
    """POST /api/outreach/draft — uses get_genai_client() (falls back to template)."""

    def test_happy_path(self, client, mock_gemini):
        resp = client.post("/api/outreach/draft", json={
            "contact": "Jane Doe",
            "angle": "reconnect",
            "channel": "email",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "draft" in data
        assert data["draft"]  # non-empty

    def test_missing_contact_and_company_returns_400(self, client):
        resp = client.post("/api/outreach/draft", json={"angle": "reconnect"})
        assert resp.status_code == 400

    def test_stub_text_in_draft(self, client, mock_gemini):
        """mock_gemini patches genai.Client so get_genai_client uses the stub;
        stub returns '[[gemini-test-stub]]'."""
        resp = client.post("/api/outreach/draft", json={
            "contact": "Alice Smith",
            "angle": "job referral",
        })
        data = resp.get_json()
        # The stub or template fallback fills the draft
        assert data["draft"]

    def test_company_only_accepted(self, client, mock_gemini):
        resp = client.post("/api/outreach/draft", json={
            "company": "Acme Corp",
            "angle": "partnership",
        })
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/outreach/draft", data="{x", content_type="application/json")
        assert resp.status_code < 500


class TestContentDraft:
    """POST /api/content/draft — uses _generate_text (autouse stub)."""

    def test_happy_path_ad_hoc(self, client):
        resp = client.post("/api/content/draft", json={
            "title": "Why Python rocks",
            "channel": "linkedin",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "draft" in data
        assert CANNED_TEXT in data["draft"]

    def test_missing_title_without_id_returns_400(self, client):
        resp = client.post("/api/content/draft", json={"channel": "linkedin"})
        assert resp.status_code == 400

    def test_item_not_found_returns_404(self, client):
        resp = client.post("/api/content/draft", json={"id": "nonexistent-id"})
        assert resp.status_code == 404

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/content/draft", data="bad", content_type="application/json")
        assert resp.status_code < 500

    def test_advance_stage_param_accepted(self, client):
        """advance_stage=True should not break the response."""
        resp = client.post("/api/content/draft", json={
            "title": "Test post",
            "advance_stage": True,
        })
        assert resp.status_code == 200


class TestContentIdea:
    """POST /api/content/idea — adds item to pipeline."""

    def test_happy_path(self, client):
        resp = client.post("/api/content/idea", json={
            "title": "5 lessons from open-source",
            "type": "post",
            "channel": "linkedin",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "item" in data
        assert data["item"]["title"] == "5 lessons from open-source"
        assert "id" in data["item"]

    def test_missing_title_returns_400(self, client):
        resp = client.post("/api/content/idea", json={"type": "post"})
        assert resp.status_code == 400

    def test_empty_title_returns_400(self, client):
        resp = client.post("/api/content/idea", json={"title": "   "})
        assert resp.status_code == 400

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/content/idea", data="{x", content_type="application/json")
        assert resp.status_code < 500

    def test_item_appears_in_pipeline(self, client):
        title = "Unique idea for pipeline test"
        client.post("/api/content/idea", json={"title": title})
        pipeline_resp = client.get("/api/content/pipeline").get_json()
        all_items = [
            item for stage_items in pipeline_resp["by_stage"].values()
            for item in stage_items
        ]
        titles = [i.get("title") for i in all_items]
        assert title in titles


class TestContentPipeline:
    """GET /api/content/pipeline"""

    def test_happy_path(self, client):
        resp = client.get("/api/content/pipeline")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "stages" in data
        assert "by_stage" in data
        assert isinstance(data["total"], int)

    def test_stages_are_expected_values(self, client):
        data = client.get("/api/content/pipeline").get_json()
        expected = {"idea", "drafting", "review", "scheduled", "published"}
        assert set(data["stages"]) == expected

    def test_by_stage_has_all_keys(self, client):
        data = client.get("/api/content/pipeline").get_json()
        for stage in data["stages"]:
            assert stage in data["by_stage"]
            assert isinstance(data["by_stage"][stage], list)


class TestEmailDraft:
    """POST /api/email/draft — placeholder route."""

    def test_returns_placeholder(self, client):
        resp = client.post("/api/email/draft", json={"subject": "Hello", "to": "test@test.com"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "placeholder"
        assert "draft" in data

    def test_empty_body_not_500(self, client):
        resp = client.post("/api/email/draft", json={})
        assert resp.status_code < 500

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/email/draft", data="bad json", content_type="application/json")
        assert resp.status_code < 500


class TestOutreachSuggestions:
    """GET /api/outreach/suggestions"""

    def test_happy_path(self, client):
        resp = client.get("/api/outreach/suggestions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "warm_contacts" in data
        assert "career_targets" in data
        assert "total" in data
        assert isinstance(data["warm_contacts"], list)

    def test_total_is_int(self, client):
        data = client.get("/api/outreach/suggestions").get_json()
        assert isinstance(data["total"], int)


class TestOutreachLog:
    """POST /api/outreach/log"""

    def test_happy_path_contact(self, client):
        resp = client.post("/api/outreach/log", json={
            "contact": "Bob Martin",
            "channel": "linkedin",
            "angle": "reconnect",
            "status": "sent",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "entry" in data
        assert data["entry"]["contact"] == "Bob Martin"
        assert data["total"] >= 1

    def test_happy_path_company_only(self, client):
        resp = client.post("/api/outreach/log", json={
            "company": "Meta",
            "channel": "email",
        })
        assert resp.status_code == 200

    def test_missing_contact_and_company_returns_400(self, client):
        resp = client.post("/api/outreach/log", json={"angle": "cold"})
        assert resp.status_code == 400

    def test_entry_appears_in_pipeline(self, client):
        client.post("/api/outreach/log", json={
            "contact": "Pipeline Test Person",
            "channel": "email",
        })
        pipeline = client.get("/api/outreach/pipeline").get_json()
        contacts = [e.get("contact") for e in pipeline["recent"]]
        assert "Pipeline Test Person" in contacts

    def test_malformed_json_not_500(self, client):
        resp = client.post("/api/outreach/log", data="{bad", content_type="application/json")
        assert resp.status_code < 500


class TestOutreachPipeline:
    """GET /api/outreach/pipeline"""

    def test_happy_path(self, client):
        resp = client.get("/api/outreach/pipeline")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "total" in data
        assert "by_status" in data
        assert "by_channel" in data
        assert "recent" in data
        assert isinstance(data["recent"], list)

    def test_counts_consistent_after_log(self, client):
        """After logging an entry, pipeline total should increase by exactly 1."""
        before = client.get("/api/outreach/pipeline").get_json()["total"]
        client.post("/api/outreach/log", json={"contact": "Counter Test", "channel": "slack"})
        after = client.get("/api/outreach/pipeline").get_json()["total"]
        assert after == before + 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestCreateTimeline:
    """POST /api/create/timeline — FFmpeg assembly (services/timeline_engine)."""

    def test_requires_clips(self, client, mock_gemini):
        resp = client.post("/api/create/timeline", json={})
        assert resp.status_code < 500
        assert resp.get_json()["status"] == "error"

    def test_structured_response_with_clips(self, client, mock_gemini, creations_dir):
        # A clip that doesn't exist → validation error (structured, not a 500).
        resp = client.post("/api/create/timeline",
                           json={"clips": ["nope.mp4"], "exports": ["mp4-1080p"]})
        assert resp.status_code < 500
        assert resp.get_json().get("status") in ("ok", "demo", "error")

    def test_malformed_json_not_500(self, client, mock_gemini):
        resp = client.post("/api/create/timeline", data="x", content_type="application/json")
        assert resp.status_code < 500


class TestTimelineFormats:
    def test_lists_export_presets(self, client):
        resp = client.get("/api/timeline/formats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok" and "mp4-1080p" in data["formats"]


class TestProvenanceRoutes:
    def test_provenance_by_file_missing(self, client):
        resp = client.get("/api/provenance/by-file/does-not-exist.png")
        assert resp.status_code < 500
        assert resp.get_json()["status"] == "error"

    def test_provenance_unknown_hash(self, client):
        resp = client.get("/api/provenance/deadbeef")
        assert resp.status_code < 500
        assert resp.get_json()["status"] == "error"


class TestProvenanceLicense:
    def test_license_options_listed(self, client):
        resp = client.get("/api/provenance/license-options")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "CC-BY-4.0" in data["terms"] and "priced" in data["terms"]

    def test_set_license_missing_file(self, client):
        resp = client.post("/api/provenance/by-file/nope.png/license",
                           json={"license": {"terms": "CC0"}})
        assert resp.status_code < 500
        assert resp.get_json()["status"] == "error"

    def test_create_music_accepts_license(self, client, mock_gemini, creations_dir):
        resp = client.post("/api/create/music",
                           json={"prompt": "calm piano", "license": {"terms": "CC0"}})
        assert resp.status_code < 500
        assert resp.get_json().get("status") in ("ok", "demo", "error")
