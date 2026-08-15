"""The residency plan drives capability_routing (Q5), with two hard limits.

Hand-reconciliation is how `reasoning` came to name a model deleted from the
disk and `local` a `gemma4`/`gemma3:4b` that was never installed. Making the
plan authoritative removes that class of defect — but only if it refuses to
bind things it must not.
"""
from __future__ import annotations

import pytest

from agent_friday.services import seat_binding as sb


def _plan(**seats):
    base = {
        "interactive_brain": {"model_id": "gemma4:12b", "backend": "ollama"},
        "heavy_hitter": {"model_id": "gemma4:26b", "backend": "ollama"},
        "sidekick": {"model_id": "gemma4:e2b", "backend": "ollama"},
        "sidekick_heavy": {"model_id": "gemma4:e4b", "backend": "ollama"},
        "embedder": {"model_id": "qwen3-embedding:0.6b", "backend": "ollama"},
        "image": {"model_id": "z-image-turbo-fp8", "backend": "comfyui"},
    }
    base.update(seats)
    return {"seats": base, "refusals": []}


def _settings():
    return {
        "capability_routing": {
            "reasoning": {"provider": "ollama-local", "model": "old:1b"},
            "subagent": {"provider": "anthropic", "model": "claude-sonnet-5"},
            "creative_image": {"provider": "google-gemini",
                               "model": "gemini-nano-banana-2"},
            "embedding": {"provider": "local", "model": "all-MiniLM-L6-v2"},
            "local": {"provider": "ollama-local", "model": "gemma3:4b"},
        },
        "model_routing": {"local_model": "gemma3:4b"},
    }


@pytest.fixture
def all_green(monkeypatch):
    monkeypatch.setattr(
        "agent_friday.services.model_seat_gate.axis_status",
        lambda m, p="local": {"structural": "green", "honesty": "green",
                              "dual_green": True})


# ── D5: the embedding seat is never bound ────────────────────────────────────

def test_embedding_is_never_bound_from_a_plan(all_green):
    """The plan's embedder is qwen3-embedding:0.6b at 1024 dimensions; the live
    store is all-MiniLM-L6-v2 at 384. D5 gates that switch on a re-index path
    that does not exist, and A5 exists because the mismatch fails SILENTLY as
    permanent memory loss."""
    s = _settings()
    sb.apply(_plan(), s)
    assert s["capability_routing"]["embedding"] == {
        "provider": "local", "model": "all-MiniLM-L6-v2"}


def test_embedder_is_not_even_a_binding_target():
    assert "embedder" not in sb.SEAT_TO_CAPABILITY
    assert "embedder" in sb.NEVER_BIND


def test_voice_seats_are_not_bound_either(all_green):
    """stt/tts are already local-voice-lite and are not the plan's business."""
    s = _settings()
    s["capability_routing"]["asr"] = {"provider": "local-voice-lite",
                                      "model": "whisper-small"}
    sb.apply(_plan(), s)
    assert s["capability_routing"]["asr"]["model"] == "whisper-small"


# ── the mapping itself ───────────────────────────────────────────────────────

def test_the_plan_replaces_a_dangling_pointer(all_green):
    s = _settings()
    sb.apply(_plan(), s)
    assert s["capability_routing"]["reasoning"] == {
        "provider": "ollama-local", "model": "gemma4:12b"}
    assert s["capability_routing"]["local"]["model"] == "gemma4:e2b"


def test_heavy_hitter_becomes_a_real_seat(all_green):
    """There was no heavy_hitter key at all before this."""
    s = _settings()
    sb.apply(_plan(), s)
    assert s["capability_routing"]["heavy_hitter"]["model"] == "gemma4:26b"


def test_both_small_models_get_seats(all_green):
    s = _settings()
    sb.apply(_plan(), s)
    assert s["capability_routing"]["local"]["model"] == "gemma4:e2b"
    assert s["capability_routing"]["subagent"]["model"] == "gemma4:e4b"


def test_flat_mirrors_are_kept_in_step(all_green):
    """provider_health.py:306 guards against a foreign id reaching the
    Anthropic probe; a stale mirror is how that happened."""
    s = _settings()
    sb.apply(_plan(), s)
    assert s["orchestrator_model"] == "gemma4:12b"
    assert s["subagent_model"] == "gemma4:e4b"
    assert s["model_routing"]["local_model"] == "gemma4:e2b"


def test_the_image_seat_binds_the_local_provider(all_green):
    s = _settings()
    sb.apply(_plan(), s)
    assert s["capability_routing"]["creative_image"] == {
        "provider": "local-comfyui", "model": "z-image-turbo-fp8"}


# ── gate-checked binding ─────────────────────────────────────────────────────

def test_an_ungated_local_model_is_refused_not_bound(monkeypatch):
    """Binding an ungated model trades a visible refusal now for an invisible
    one at dispatch time."""
    monkeypatch.setattr(
        "agent_friday.services.model_seat_gate.axis_status",
        lambda m, p="local": {"structural": "green", "honesty": "ungated",
                              "dual_green": False})
    s = _settings()
    prop = sb.apply(_plan(), s)
    assert "reasoning" not in prop["changes"]
    assert s["capability_routing"]["reasoning"]["model"] == "old:1b"
    r = [x for x in prop["refusals"] if x["capability"] == "reasoning"][0]
    assert "honesty=ungated" in r["explanation"]


def test_the_image_seat_needs_no_gate(monkeypatch):
    """The gate measures tool-calling. An image model holds no tools."""
    monkeypatch.setattr(
        "agent_friday.services.model_seat_gate.axis_status",
        lambda m, p="local": {"structural": "ungated", "honesty": "ungated",
                              "dual_green": False})
    s = _settings()
    sb.apply(_plan(), s)
    assert s["capability_routing"]["creative_image"]["model"] == \
        "z-image-turbo-fp8"


def test_a_refused_seat_leaves_the_previous_value_untouched(monkeypatch):
    monkeypatch.setattr(
        "agent_friday.services.model_seat_gate.axis_status",
        lambda m, p="local": {"structural": "red", "honesty": "red",
                              "dual_green": False})
    s = _settings()
    before = dict(s["capability_routing"]["subagent"])
    sb.apply(_plan(), s)
    assert s["capability_routing"]["subagent"] == before


# ── purity + reporting ───────────────────────────────────────────────────────

def test_propose_changes_nothing(all_green):
    s = _settings()
    import json
    before = json.dumps(s, sort_keys=True)
    sb.propose(_plan(), s)
    assert json.dumps(s, sort_keys=True) == before


def test_applying_twice_is_idempotent(all_green):
    s = _settings()
    sb.apply(_plan(), s)
    second = sb.apply(_plan(), s)
    assert second["changes"] == {}


def test_an_empty_seat_is_reported_with_the_plan_refusal(all_green):
    plan = _plan()
    plan["seats"]["heavy_hitter"] = None
    plan["refusals"] = [{"role": "heavy_hitter", "rule_id": "R2",
                         "explanation": "exceeds the hard ceiling"}]
    prop = sb.propose(plan, _settings())
    assert any(s["capability"] == "heavy_hitter" for s in prop["skipped"])


def test_describe_renders_bindings_and_refusals(all_green):
    prop = sb.propose(_plan(), _settings())
    text = sb.describe(prop)
    assert "reasoning" in text and "gemma4:12b" in text


def test_backend_determines_the_bound_provider(all_green):
    plan = _plan(interactive_brain={"model_id": "big:35b",
                                    "backend": "llama-server"})
    prop = sb.propose(plan, _settings())
    assert prop["changes"]["reasoning"]["provider"] == "llama-cpp-local"


def test_the_image_seat_updates_its_flat_mirror(all_green):
    """core._sync_capability_routing DERIVES capability_routing from the flat
    keys, so a capability written without its mirror is silently reverted.

    Caught live 2026-08-15: the image seat became provider `local-comfyui`
    with model `gemini-nano-banana-2` — a Google model on the on-device
    provider — because `creative_model` still named the Gemini one.
    """
    s = _settings()
    s["creative_model"] = "gemini-nano-banana-2"
    sb.apply(_plan(), s)
    assert s["creative_model"] == "z-image-turbo-fp8"


def test_every_bound_capability_has_a_flat_mirror():
    """A bound capability with no mirror loses to the sync on the next save."""
    missing = [c for c in sb.SEAT_TO_CAPABILITY.values()
               if c not in sb.CAPABILITY_TO_FLAT and c not in ("local",
                                                               "heavy_hitter")]
    assert not missing, "no flat mirror for: %s" % missing
