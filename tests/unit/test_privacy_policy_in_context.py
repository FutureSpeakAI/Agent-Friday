"""The privacy policy in force is in the model's context on every turn, text and voice.

A model that has not read the rules guesses at them, and a model that guesses argues a refusal "may be
intended" when the settings say the data was allowed. So every assembled system context carries a
short block derived from the LIVE settings: the routing mode, the cloud-consent record, the vault
switch, whether the never-send floor has entries, the spending caps, the effective behaviour in one
line, and the standing rules (public queries are not private by topic; cloud-only mode is consent;
inform once; a refusal that contradicts the settings is a bug, never "intended").

The block is read from settings on every build, so a change shows on the next turn without a
restart, and it never repeats what is on the never-send list.
"""
from __future__ import annotations

import json

import pytest

from agent_friday import core

HEAD = "== PRIVACY POLICY =="


@pytest.fixture(autouse=True)
def online(monkeypatch):
    # The offline overlay forces local routing while the network monitor says offline; these tests
    # are about the settings as written.
    monkeypatch.setattr(core, "_apply_offline_routing_overlay", lambda s: s)


def _apply(mode, *, unrestricted=False, vault_local=True, consent=None, caps=None):
    core._save_settings({
        "model_routing": {
            "mode": mode, "unrestricted_cloud": unrestricted, "vault_local_only": vault_local,
            "cloud_consent": consent or {"answered": False, "choice": None, "at": None,
                                         "capability_snapshot": None}},
        "cost_budget": caps or {"daily_enabled": False, "monthly_enabled": False,
                                "hard_stop_daily_enabled": False, "hard_stop_monthly_enabled": False},
    }, _internal_cloud_consent_write=True, owner_routing_change=True)


def _section(text):
    assert HEAD in (text or ""), "the privacy policy is not in this context"
    rest = text[text.index(HEAD):]
    end = rest.find("\n==", len(HEAD))
    return rest if end < 0 else rest[:end]


def _text_context(provider="cloud"):
    from agent_friday.services.model_router import _build_context_prompt
    text, _sources = _build_context_prompt("what is on this weekend at Riverside Park",
                                           workspace="chat", provider=provider)
    return text


UNRESTRICTED = {"answered": True, "choice": "cloud_unrestricted", "at": "2026-10-09T09:00:00+00:00",
                "capability_snapshot": None}
GUARDED = {"answered": True, "choice": "cloud_guarded", "at": "2026-10-01T09:00:00+00:00",
           "capability_snapshot": None}


@pytest.mark.parametrize("settings, says", [
    # Local preferred with unrestricted consent: the cloud may use everything but the floor, and the
    # block says so in those words rather than leaving "local preferred" to be misread.
    (dict(mode="local_preferred", unrestricted=True, vault_local=False, consent=UNRESTRICTED),
     ["Mode: local preferred.", "Consent: unrestricted (2026-10-09).", "Local-only records: off.",
      "Cloud models may use all data except the never-send floor."]),
    # Cloud-only mode is consent to use cloud models, except the floor.
    (dict(mode="cloud_only", vault_local=False),
     ["Mode: cloud only.", "Consent: unanswered.",
      "Cloud-only mode = consent to cloud models for all but the never-send floor."]),
    # ... and the vault switch, when it is on, is stated as the exception it is.
    (dict(mode="cloud_only", vault_local=True),
     ["Local-only records: on.", "all but the never-send floor and private records."]),
    (dict(mode="local_only"),
     ["Mode: local only.", "No cloud model is used; all work stays on this computer."]),
    (dict(mode="local_preferred", consent=GUARDED),
     ["Consent: guarded (2026-10-01).",
      "Private data goes to the local model; cloud models get the rest."]),
    (dict(mode="cloud_only", caps={"hard_stop_daily_enabled": True, "hard_stop_daily": 5}),
     ["Spend caps: on."]),
    (dict(mode="cloud_only"), ["Spend caps: off."]),
])
def test_each_mode_and_consent_reads_as_its_effect(settings, says):
    _apply(**settings)
    block = _section(_text_context())
    for line in says:
        assert line in block, (line, block)
    assert "not private by topic" in block and "personal identifiers" in block
    assert "never argue it may be intended" in block and "offer the local model" in block
    assert len(block) // 4 <= 120, len(block) // 4


def test_it_is_in_every_text_context_and_survives_cloud_gating():
    _apply("local_preferred", unrestricted=True, vault_local=False, consent=UNRESTRICTED)
    from agent_friday.privacy.vault_access import VaultAccessControl
    from agent_friday.services.model_router import _build_context_prompt, _get_friday_system_prompt
    vac = VaultAccessControl()
    for provider in ("cloud", "local"):
        _section(_text_context(provider))
        gated, _ = _build_context_prompt("hello", workspace="chat", provider=provider, vault_control=vac)
        _section(gated)
    # The agent loop, background tasks and cloud voice build through this one funnel.
    _section(_get_friday_system_prompt("hello", "chat", provider="cloud", vault_control=vac))
    assert vac.classify(_section(_text_context())) == 1, "the block is public text"


def test_it_is_in_the_voice_front_and_the_speaker_prompt_once():
    _apply("local_preferred", unrestricted=True, vault_local=False, consent=UNRESTRICTED)
    import agent_friday.routes.voice as rv
    from agent_friday.services.voice_engine import build_voice_tool_contract
    front = rv._build_front_system_prompt({}, build_voice_tool_contract(), "Qwen3-1.7B")
    assert "Consent: unrestricted (2026-10-09)." in _section(front)
    assert front.count(HEAD) == 1, "the digest must not repeat what the front already states"
    speaker = rv._build_front_speaker_prompt({}, "Qwen3-1.7B")
    assert "Consent: unrestricted (2026-10-09)." in _section(speaker)


def test_a_settings_change_shows_on_the_next_build_without_a_restart():
    _apply("cloud_only", vault_local=False)
    assert "Mode: cloud only." in _section(_text_context())
    _apply("local_only")
    after = _section(_text_context())
    assert "Mode: local only." in after and "No cloud model is used" in after
    import agent_friday.routes.voice as rv
    assert "Mode: local only." in _section(rv._build_front_speaker_prompt({}, "Qwen3-1.7B"))


def test_it_says_the_floor_has_entries_and_never_what_they_are():
    shield = core.FRIDAY_DIR / "privacy_shield.json"
    before = shield.read_text(encoding="utf-8") if shield.exists() else None
    try:
        shield.write_text(json.dumps({"never_send": ["Riverside Clinic", "Elm Street 14"]}),
                          encoding="utf-8")
        _apply("cloud_only", vault_local=False)
        block = _section(_text_context("local"))
        assert "Never-send list: 2 items." in block
        assert "Riverside Clinic" not in block and "Elm Street" not in block
    finally:
        if before is None:
            shield.unlink()
        else:
            shield.write_text(before, encoding="utf-8")
