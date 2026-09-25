"""The provider you pick in setup must be the provider you get.

The setup wizard asks "Choose your primary AI provider" and offers
Anthropic, OpenAI and Ollama (local). Writing the answer only to `provider`
without touching `model_routing.mode` is not enough.

`mode` is what routes an actual turn. So picking "Ollama (local)" without
setting it leaves the machine on the factory `cloud_only`, and every fresh
install lands on cloud_only no matter what was answered.

A keyless safety net that silently runs the turn on Ollama whenever no
Anthropic key is present would hide that, but it also answers a user who
chose cloud only locally, so there is none for cloud_only users. That makes
this gap load bearing in the other direction — someone who deliberately
chose a local model, and gave no cloud key, would be told to add one.

Both halves have to be true at once:

  * cloud only must not answer locally  (test_cloud_only_is_honoured.py)
  * choosing local must actually route locally  (this file)

Also pinned here: the block written must be COMPLETE. The wizard's
_save_config does a raw json.dumps over settings.json with no merge, and
core._load_settings_raw replaces top-level keys wholesale rather than
deep-merging them. A partial {"mode": ...} would therefore delete
ollama_url, vault_local_only, fallback_to_cloud and the rest of the
routing config — a silent reset wearing the shape of a fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday import setup_wizard as wiz  # noqa: E402


def test_choosing_ollama_routes_locally():
    block = wiz._routing_block_for("ollama", {})
    assert block is not None
    assert block["mode"] in ("local_preferred", "local_only"), (
        "the wizard offered 'Ollama (local)', so a turn must reach Ollama; "
        "got mode=%r" % block.get("mode")
    )


def test_choosing_a_cloud_provider_stays_cloud():
    for pid in ("anthropic", "openai"):
        block = wiz._routing_block_for(pid, {})
        assert block["mode"] == "cloud_only", pid


def test_the_block_is_complete_not_a_partial_reset():
    """A partial model_routing is a settings wipe, not a setting."""
    block = wiz._routing_block_for("ollama", {})
    for key in ("ollama_url", "vault_local_only", "fallback_to_cloud",
                "default_cloud_model"):
        assert key in block, (
            "%s missing — _save_config writes this dict verbatim and "
            "_load_settings_raw replaces model_routing wholesale, so a "
            "partial block deletes the rest of the routing config" % key
        )


def test_an_existing_choice_is_preserved_not_clobbered():
    """Re-running setup must not silently undo a mode set later in the UI.

    The seat-binding bug class has exactly this shape: a planner recomputing
    a value the user has already chosen, on every run.
    """
    existing = {"mode": "smart", "ollama_url": "http://box.lan:11434"}
    block = wiz._routing_block_for("anthropic", existing,
                                   previous_provider="anthropic")
    assert block["mode"] == "smart", "unchanged provider must not reset mode"
    assert block["ollama_url"] == "http://box.lan:11434"


def test_changing_provider_does_move_the_mode():
    """Changing the answer IS the instruction to change the routing."""
    existing = {"mode": "cloud_only"}
    block = wiz._routing_block_for("ollama", existing,
                                   previous_provider="anthropic")
    assert block["mode"] in ("local_preferred", "local_only")


def test_it_fails_safe_when_defaults_cannot_be_read(monkeypatch):
    """No defaults, no write. Better to leave routing alone than to
    persist a block missing the keys everything else depends on."""
    monkeypatch.setattr(wiz, "_default_routing", lambda: None)
    assert wiz._routing_block_for("ollama", {}) is None
