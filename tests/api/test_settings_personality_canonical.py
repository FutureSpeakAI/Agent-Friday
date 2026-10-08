"""Settings edits reach the canonical personality used by subsequent prompts."""

import pytest

import agent_friday.core as core
from agent_friday.routes import core_routes
from agent_friday.services import soul


OLD_BODY = "Measured and curious. [prior-persona]"
NEW_BODY = "Candid, patient, with occasional dry humor. [updated-persona]"
OLD_TEXT = "# Personality\n" + OLD_BODY
NEW_TEXT = "# Personality\n" + NEW_BODY
LEGACY_TEXT = "A separate legacy personality. [legacy-persona]"


@pytest.fixture
def persona_home(tmp_path, monkeypatch):
    """Keep real personality I/O isolated from unrelated settings discovery."""
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "SOUL_FILE", tmp_path / "SOUL.md")
    monkeypatch.setattr(core, "AGENT_PERSONALITY_FILE", tmp_path / "agent-personality.txt")
    monkeypatch.setattr(soul, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(soul, "SOUL_FILE", tmp_path / "SOUL.md")
    monkeypatch.setattr(soul, "HISTORY_DIR", tmp_path / "soul_history")
    monkeypatch.setattr(soul, "_cache", {"text": None, "mtime": 0.0})
    monkeypatch.setattr(core_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(core_routes, "_save_settings", lambda delta, **kwargs: dict(delta))
    return tmp_path


def _seed_warm_personality():
    soul.SOUL_FILE.write_text(OLD_TEXT, encoding="utf-8")
    core.AGENT_PERSONALITY_FILE.write_text(LEGACY_TEXT, encoding="utf-8")
    assert core._load_agent_personality() == OLD_BODY
    assert soul._cache["text"] == OLD_TEXT


def _assert_saved_personality(client, response):
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert response.get_json()["personality"] == NEW_BODY, "canonical personality did not change"
    assert client.get("/api/settings").get_json()["personality"] == NEW_BODY
    assert core._load_agent_personality() == NEW_BODY
    assert soul.SOUL_FILE.read_text(encoding="utf-8") == NEW_TEXT
    # This is the real shared prompt prefix, not a model or audio acceptance test.
    prefix = core._settings_system_prefix({}, core._load_agent_personality())
    assert NEW_BODY in prefix and OLD_BODY not in prefix and LEGACY_TEXT not in prefix


@pytest.mark.parametrize("legacy_write", [False, True], ids=["canonical", "legacy-write-inverse"])
def test_existing_soul_and_warm_cache_reach_next_prompt(
        client, persona_home, monkeypatch, legacy_write):
    _seed_warm_personality()
    if legacy_write:
        def save_legacy(text):
            core._save_agent_personality(text)
            return {"ok": True}
        monkeypatch.setattr(soul, "save_soul", save_legacy)

    response = client.post("/api/settings", json={"personality": NEW_TEXT})
    if legacy_write:
        with pytest.raises(AssertionError, match="canonical personality did not change"):
            _assert_saved_personality(client, response)
        assert core._load_agent_personality() == OLD_BODY
        assert core.AGENT_PERSONALITY_FILE.read_text(encoding="utf-8") == NEW_TEXT
        assert not soul.history()
    else:
        _assert_saved_personality(client, response)
        assert core.AGENT_PERSONALITY_FILE.read_text(encoding="utf-8") == LEGACY_TEXT
        versions = soul.history()
        assert len(versions) == 1
        assert (soul.HISTORY_DIR / versions[0]["name"]).read_text(encoding="utf-8") == OLD_TEXT
        soul._invalidate()
        assert core._load_agent_personality() == NEW_BODY


def test_first_canonical_save_keeps_the_legacy_fallback_untouched(client, persona_home):
    core.AGENT_PERSONALITY_FILE.write_text(LEGACY_TEXT, encoding="utf-8")
    assert core._load_agent_personality() == LEGACY_TEXT
    assert not soul.SOUL_FILE.exists()
    response = client.post("/api/settings", json={"personality": NEW_TEXT})
    _assert_saved_personality(client, response)
    assert core.AGENT_PERSONALITY_FILE.read_text(encoding="utf-8") == LEGACY_TEXT
    assert not soul.history(), "there was no canonical prior version to snapshot"


@pytest.mark.parametrize("value", [{"text": "invalid"}, [], 7, "", " \n\t", "x" * (32768 + 1)],
                         ids=["object", "list", "number", "empty", "whitespace", "oversized"])
def test_invalid_personality_is_not_reported_saved(client, persona_home, value):
    _seed_warm_personality()
    response = client.post("/api/settings", json={"personality": value})
    assert response.status_code == 400
    assert response.get_json()["status"] == "error"
    assert response.get_json()["message"]
    assert core._load_agent_personality() == OLD_BODY
    assert soul.SOUL_FILE.read_text(encoding="utf-8") == OLD_TEXT
    assert core.AGENT_PERSONALITY_FILE.read_text(encoding="utf-8") == LEGACY_TEXT
    assert not soul.history()


@pytest.mark.parametrize("body", [{}, {"personality": None}], ids=["omitted", "null"])
def test_missing_personality_does_not_reset_it(client, persona_home, body):
    _seed_warm_personality()
    response = client.post("/api/settings", json=body)
    assert response.status_code == 200
    assert response.get_json()["personality"] == OLD_BODY
    assert soul.SOUL_FILE.read_text(encoding="utf-8") == OLD_TEXT
    assert not soul.history()


def test_service_write_failure_is_refused_without_internal_details(
        client, persona_home, monkeypatch):
    _seed_warm_personality()

    def fail_write(path, text):
        raise OSError("synthetic-persona-write-detail")

    monkeypatch.setattr(soul, "_atomic_write", fail_write)
    response = client.post("/api/settings", json={"personality": NEW_TEXT})
    body = response.get_json()
    assert response.status_code == 400
    assert body["status"] == "error"
    assert "Couldn't save the personality" in body["message"]
    assert body["error_id"]
    assert "synthetic-persona-write-detail" not in response.get_data(as_text=True)
    assert core._load_agent_personality() == OLD_BODY
    assert soul.SOUL_FILE.read_text(encoding="utf-8") == OLD_TEXT
    assert core.AGENT_PERSONALITY_FILE.read_text(encoding="utf-8") == LEGACY_TEXT


def test_explicit_soul_reset_remains_separate_and_versioned(client, persona_home):
    _seed_warm_personality()
    response = client.post("/api/soul/reset")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert soul.SOUL_FILE.read_text(encoding="utf-8") == soul.default_soul()
    assert core._load_agent_personality() == soul.render_personality()
    assert OLD_BODY not in core._load_agent_personality()
    versions = soul.history()
    assert len(versions) == 1
    assert (soul.HISTORY_DIR / versions[0]["name"]).read_text(encoding="utf-8") == OLD_TEXT
