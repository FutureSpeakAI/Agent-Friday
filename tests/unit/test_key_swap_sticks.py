"""A key you swap in Settings must still be the key after a restart.

Stephen, 2026-08-26: "we need to fix the installer so the Friday that ships
to users can swap API keys from the settings menu". Swap, not just add — a
key that never worked and a key that stopped working look identical to a
user, so replacing one is the realistic case, not the exotic one.

Adding worked. Swapping did not survive a restart, and the reason is a
precedence rule nobody could see:

    provider_api_key()  ->  environment first, credential store second

and the two are written by different halves of the product:

    setup wizard  -> config.yaml + settings.json + START.BAT   (never the store)
    Settings UI   -> encrypted credential store                 (never start.bat)

Friday re-bootstraps her environment from start.bat on every launch
(_bootstrap_env_from_launch_scripts — the boot log says "Loaded 9
environment variable(s) from start.bat, launch_now.bat, friday_startup.bat").
So:

  1. the wizard writes the first key into start.bat
  2. that key later expires, or runs out of credit
  3. she pastes a new one into Settings; it is stored and hot-reloaded, and
     it works — hot_reload_provider_key sets os.environ live
  4. she closes Friday and opens it again
  5. start.bat puts the DEAD key back into the environment
  6. environment wins, and the panel still says "connected"

Friday is broken again, by the key she already replaced, and nothing on
screen disagrees with her having fixed it.

This was invisible to the author for the same reason as everything else this
week: his credential store holds no anthropic or google-gemini key at all
(list_provider_keys() -> ['atlascloud', 'firecrawl']), because the Settings
panel that writes them was unreachable until today. With an empty store the
old precedence and the new one do exactly the same thing.

The rule now: a key saved through the product is a deliberate, later
instruction; an environment variable is ambient configuration. The same
distinction model_router._chosen_seat draws between a binding and a default,
and seat_binding draws between "a value he changed" and "the factory value".

Nothing shadows silently in either direction — /api/providers reports which
source is in play, so a start.bat rotation that does not take is visible
rather than baffling.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.routing import provider_descriptors as pd  # noqa: E402

ANTHROPIC = {
    "name": "anthropic",
    "auth": {"type": "env_var", "key": "ANTHROPIC_API_KEY", "key_aliases": []},
}
KEYLESS = {"name": "ollama-local", "auth": {"type": "none"}}


@pytest.fixture
def store(monkeypatch):
    """A stand-in credential store we can fill per-test."""
    held = {}

    def _get(provider):
        return held.get(provider)

    import agent_friday.services.credential_store as cs
    monkeypatch.setattr(cs, "get_provider_key", _get, raising=False)
    return held


def test_a_key_saved_in_settings_beats_a_stale_start_bat(monkeypatch, store):
    """The whole bug, in one assertion."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-DEAD-from-start-bat")
    store["anthropic"] = "fake-key-LIVE-she-just-pasted-this"

    assert pd.provider_api_key(ANTHROPIC) == "fake-key-LIVE-she-just-pasted-this"


def test_the_environment_still_works_when_nothing_was_saved(monkeypatch, store):
    """No stored key: behaviour is byte-identical to before this change.

    This is why the change is safe to ship. Stephen's store holds no
    anthropic key, so for him the two rules are the same rule.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-from-start-bat")
    assert pd.provider_api_key(ANTHROPIC) == "fake-key-from-start-bat"


def test_removing_the_saved_key_falls_back_to_the_environment(monkeypatch, store):
    """Remove must mean remove — and then the ambient key is honoured again,
    rather than leaving the provider dead with a key sitting right there."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-from-start-bat")
    store["anthropic"] = "fake-key-saved"
    assert pd.provider_api_key(ANTHROPIC) == "fake-key-saved"
    store.pop("anthropic")
    assert pd.provider_api_key(ANTHROPIC) == "fake-key-from-start-bat"


def test_a_keyless_provider_is_untouched(monkeypatch, store):
    store["ollama-local"] = "should-never-be-consulted"
    assert pd.provider_api_key(KEYLESS) is None


def test_an_unreadable_store_falls_back_rather_than_failing(monkeypatch):
    """A corrupt or locked store must not take a working env key down with it."""
    import agent_friday.services.credential_store as cs

    def _boom(provider):
        raise RuntimeError("vault locked")

    monkeypatch.setattr(cs, "get_provider_key", _boom, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-from-start-bat")
    assert pd.provider_api_key(ANTHROPIC) == "fake-key-from-start-bat"


class TestKeySource:
    """Neither source may shadow the other invisibly."""

    def test_reports_settings_when_the_store_wins(self, monkeypatch, store):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-env")
        store["anthropic"] = "fake-key-saved"
        assert pd.provider_key_source(ANTHROPIC) == "settings"

    def test_reports_environment_when_the_env_is_used(self, monkeypatch, store):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-env")
        assert pd.provider_key_source(ANTHROPIC) == "environment"

    def test_reports_none_when_there_is_no_key(self, monkeypatch, store):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert pd.provider_key_source(ANTHROPIC) == "none"

    def test_keyless_providers_report_none(self, monkeypatch, store):
        assert pd.provider_key_source(KEYLESS) == "none"


class TestMasking:
    """Enough to recognise a key. Never enough to use one."""

    def test_shows_only_a_short_tail(self):
        assert pd.mask_key("fake-key-0000-ABCDEFGHIJKLMNOP") == "…MNOP"

    def test_a_short_key_never_leaks_more_than_it_hides(self):
        # Anything this short is not a real key; refuse rather than echo it.
        assert pd.mask_key("abcd") == "…"
        assert pd.mask_key("") == ""
        assert pd.mask_key(None) == ""

    def test_the_mask_never_contains_the_whole_key(self):
        key = "fake-key-0000-OPAQUEOPAQUEOPAQUE"
        assert key not in pd.mask_key(key)
