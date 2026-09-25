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
    # On llama-server, ANY change to the system message re-prefills the
    # whole prompt, so the system text must
    # be byte-identical across builds. The clock moved, and the SYSTEM text
    # did not -- the volatile tail rides in `meta["volatile"]` and is put in
    # the user turn by `_voice_user_message`.
    assert p1 == p2
    assert VOLATILE_MARKER not in p1
    assert meta1["volatile"] != meta2["volatile"]
    assert meta1["volatile"].startswith(VOLATILE_MARKER)
    prefix = p1
    # Order: description first, then the voice rules + choreography, then the
    # stable context; everything volatile is in the tail, not the prefix.
    assert prefix.index("Your ears are faster-whisper") < prefix.index("NEVER use markdown")
    assert prefix.index("NEVER use markdown") < prefix.index("TOOL CHOREOGRAPHY")
    assert prefix.index("TOOL CHOREOGRAPHY") < prefix.index("== FRIDAY ==")
    for volatile in ("Now: 2026", "== CONTINUITY ==", "== TONE =="):
        assert volatile not in p1
        assert volatile in meta1["volatile"]
    for m in (meta1, meta2):
        assert m["is_local_brain"] is True and m["provider"] == "local"
    # The user turn carries the fresh tail, then what was said.
    u = rv._voice_user_message("what time is it", volatile=meta2["volatile"])
    assert u.startswith(VOLATILE_MARKER) and "09:13" in u
    assert u.endswith("== THE USER JUST SAID ==\nwhat time is it")
    # With no volatile block (a builder without the marker) the text is bare.
    assert rv._voice_user_message("hi", volatile="") == "hi"


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
