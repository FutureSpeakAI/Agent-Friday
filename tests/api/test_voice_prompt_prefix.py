"""The voice prompt is prefix-stable (clean-sheet §4.4, D5).

Two consecutive assembled prompts must be byte-identical up to
`prompt_cache.VOLATILE_MARKER`; the clock and every other volatile block sit
after it. This is what lets llama-server's prefix cache make turn 2's prefill
small (prompt_n < 2,000 — the live acceptance in §9.2).
"""
from agent_friday.services.prompt_cache import VOLATILE_MARKER


def _wire(monkeypatch, rv, clock):
    stable = ("== FRIDAY ==\nPersona and cLaws and self-knowledge.\n"
              "== AVAILABLE TOOLS ==\nknowledge_query, search_wiki ...\n")
    monkeypatch.setattr(
        rv, "_get_friday_system_prompt",
        lambda **kw: stable + VOLATILE_MARKER + "\nNow: " + clock["t"] + "\n")
    monkeypatch.setattr(rv, "_build_session_continuity_block",
                        lambda: "\n== CONTINUITY ==\nlast turn at " + clock["t"])
    monkeypatch.setattr(rv, "_build_emotional_tone_block",
                        lambda: "\n== TONE ==\nsteady " + clock["t"])
    monkeypatch.setattr(rv, "_vault_local_only", lambda: False)
    monkeypatch.setattr(rv, "_vault_cloud_fallback", lambda: "warn")
    monkeypatch.setattr("agent_friday.routing.model_router.provider_family",
                        lambda m: "local")


def test_voice_prompt_is_prefix_stable(monkeypatch):
    import agent_friday.routes.voice as rv
    from agent_friday.services import voice_manifest as vm
    clock = {"t": "2026-09-16 09:12"}
    _wire(monkeypatch, rv, clock)
    monkeypatch.setattr(vm.get_manifest(), "describe_for_model",
                        lambda: "Your ears are faster-whisper small on the GPU.")
    p1, meta1 = rv._build_voice_system_prompt({"orchestrator_model": "seat"})
    clock["t"] = "2026-09-16 09:13"                       # a minute passes
    p2, meta2 = rv._build_voice_system_prompt({"orchestrator_model": "seat"})
    assert p1 != p2                                       # the clock moved
    i1, i2 = p1.index(VOLATILE_MARKER), p2.index(VOLATILE_MARKER)
    assert i1 == i2 and p1[:i1] == p2[:i2]                # identical prefix
    prefix = p1[:i1]
    # Order: description first, then the voice rules + choreography, then the
    # stable context; everything volatile is AFTER the marker.
    assert prefix.index("Your ears are faster-whisper") < prefix.index("NEVER use markdown")
    assert prefix.index("NEVER use markdown") < prefix.index("TOOL CHOREOGRAPHY")
    assert prefix.index("TOOL CHOREOGRAPHY") < prefix.index("== FRIDAY ==")
    for volatile in ("Now: 2026", "== CONTINUITY ==", "== TONE =="):
        assert p1.index(volatile) > i1
    assert meta1 == meta2 == {"is_local_brain": True, "provider": "local"}


def test_description_change_changes_the_prefix_only_when_a_proof_changes(monkeypatch):
    """The first paragraph is the manifest's self-description: constant while
    the proofs are constant, so it does not churn the cache."""
    import agent_friday.routes.voice as rv
    from agent_friday.services import voice_manifest as vm
    clock = {"t": "09:12"}
    _wire(monkeypatch, rv, clock)
    m = vm.get_manifest()
    monkeypatch.setattr(m, "describe_for_model", lambda: "desc A")
    a1 = rv._build_voice_system_prompt({"orchestrator_model": "seat"})[0]
    a2 = rv._build_voice_system_prompt({"orchestrator_model": "seat"})[0]
    assert a1 == a2
    monkeypatch.setattr(m, "describe_for_model", lambda: "desc B")
    b = rv._build_voice_system_prompt({"orchestrator_model": "seat"})[0]
    assert b != a1 and b.startswith("You are Agent Friday, a sovereign personal AI assistant "
                                    "in a LIVE VOICE conversation. desc B")
