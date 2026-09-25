"""ElevenLabs and Inworld keys live in the credential store.

They used to be read from core attributes, the environment, and then the
PLAINTEXT settings.json. A key typed into Friday (the setup checklist or
Settings) is now stored encrypted under the provider's own name; a key still
sitting in settings.json is read as a fallback and moved into the store.
"""
from __future__ import annotations

import pytest

import agent_friday.core as core
from agent_friday.services import cloud_voice, credential_store as cs, elevenlabs_tools


@pytest.fixture
def clean(monkeypatch):
    for attr in ("ELEVENLABS_API_KEY", "INWORLD_API_KEY"):
        monkeypatch.setattr(core, attr, "", raising=False)
        monkeypatch.delenv(attr, raising=False)
    for name in ("elevenlabs", "inworld"):
        cs.delete_provider_key(name)
    yield
    for name in ("elevenlabs", "inworld"):
        cs.delete_provider_key(name)


def test_a_stored_key_is_read_from_the_credential_store(clean, monkeypatch):
    monkeypatch.setattr(cloud_voice, "_settings", dict)
    cs.set_provider_key("inworld", "stored-inworld-value")
    assert cloud_voice._api_key("inworld") == "stored-inworld-value"


def test_elevenlabs_tools_resolves_the_same_way(clean, monkeypatch):
    monkeypatch.setattr(cloud_voice, "_settings", dict)
    cs.set_provider_key("elevenlabs", "stored-eleven-value")
    assert elevenlabs_tools._api_key() == "stored-eleven-value"


def test_a_plaintext_settings_key_is_migrated_into_the_store(clean, monkeypatch):
    written = []
    monkeypatch.setattr(cloud_voice, "_settings",
                        lambda: {"elevenlabs_api_key": "legacy-plain-value"})
    monkeypatch.setattr(core, "_save_settings", lambda d: written.append(d))
    assert cloud_voice._api_key("elevenlabs") == "legacy-plain-value"
    assert cs.get_provider_key("elevenlabs") == "legacy-plain-value"
    assert written == [{"elevenlabs_api_key": ""}], "the plaintext copy was not blanked"


def test_the_environment_still_wins(clean, monkeypatch):
    monkeypatch.setattr(cloud_voice, "_settings", dict)
    cs.set_provider_key("inworld", "stored")
    monkeypatch.setenv("INWORLD_API_KEY", "from-env")
    assert cloud_voice._api_key("inworld") == "from-env"
