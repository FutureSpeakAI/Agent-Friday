"""2026-08-14 sharpened defect #6 — per-message badges name the ACTUAL
responding model after any fallback, never the intended/configured seat.

Live evidence: all 12 of the morning's replies were badged
'qwen3.6-35b-a3b-iq4nl' while the brain never bound its port — gemma4:e4b
(seat substitution) and Claude (ladder fallback) did the actual answering.
Attribution was captured at routing; dispatch decides below it.

Contract: primitives call attribution.record_generation() when they produce
final text; the chat route reads the LAST generation for the persisted
message; the fallback chain rides the message. (In these tests the stubbed
dispatchers record — exactly what the real primitives now do.)
"""
from __future__ import annotations

import agent_friday.routes.chat as chat_mod
from agent_friday.services import attribution


class TestBadgeReflectsActualGenerator:
    def test_badge_names_the_fallback_model_not_the_routed_seat(self, client, monkeypatch):
        # The router intends the configured cloud seat; the dispatch layer
        # ends up on a different model (as a real primitive would record).
        def dispatch(messages, **kwargs):
            attribution.note_fallback(
                "local (qwen3.6-35b-a3b-iq4nl): ConnectionError port 8081")
            attribution.record_generation("gemma4:e4b", provider="ollama",
                                          seat="local")
            return "answered by the sidekick", []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
        resp = client.post("/api/chat", json={"message": "hello"})
        data = resp.get_json()
        assert data["friday_msg"]["model"] == "gemma4:e4b", (
            "badge must name the ACTUAL generator — the morning's badge lie")
        assert data["friday_msg"]["seat"] == "local"
        assert data["model"] == "gemma4:e4b"
        # The fallback chain rides the message and the payload.
        assert data["fallback_chain"], "fallback chain must be surfaced"
        assert "8081" in data["fallback_chain"][0]
        assert data["friday_msg"].get("fallback_chain")

    def test_attribution_persists_with_the_message(self, client, monkeypatch):
        def dispatch(messages, **kwargs):
            attribution.record_generation("gemma4:e4b", provider="ollama",
                                          seat="local")
            return "persisted attribution", []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
        client.post("/api/chat", json={"message": "persist me"})
        hist = client.get("/api/chat/history").get_json()["messages"]
        last = [m for m in hist if m.get("role") == "friday"][-1]
        assert last.get("model") == "gemma4:e4b"
        assert last.get("seat") == "local"

    def test_no_recording_falls_back_to_routing_intent(self, client, monkeypatch):
        # A stub that records nothing (legacy shape) must not blank the
        # badge — routing intent remains the last resort.
        monkeypatch.setattr(chat_mod, "_call_claude_agent",
                            lambda messages, **kw: ("plain", []))
        resp = client.post("/api/chat", json={"message": "hi"})
        data = resp.get_json()
        assert data["friday_msg"].get("model"), "badge must never be empty"

    def test_attribution_resets_between_turns(self, client, monkeypatch):
        # Turn 1 records a fallback; turn 2 records none — turn 2 must not
        # inherit turn 1's chain.
        state = {"n": 0}

        def dispatch(messages, **kwargs):
            state["n"] += 1
            if state["n"] == 1:
                attribution.note_fallback("leg one died")
                attribution.record_generation("gemma4:e4b", seat="local")
            else:
                attribution.record_generation("claude-sonnet-5", seat="cloud")
            return "turn %d" % state["n"], []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
        client.post("/api/chat", json={"message": "one"})
        d2 = client.post("/api/chat", json={"message": "two"}).get_json()
        assert d2["friday_msg"]["model"] == "claude-sonnet-5"
        assert not d2.get("fallback_chain"), (
            "turn 2 must not inherit turn 1's fallback chain")


class TestPrimitivesRecord:
    def test_call_ollama_records_post_substitution_model(self, client, monkeypatch):
        # The real _call_ollama must record whatever model ACTUALLY ran —
        # including a seat-gate substitution.
        import agent_friday.services.model_router as mr
        import agent_friday.routing.ollama_manager as om

        class FakeMgr:
            base_url = "http://fake:11434"

            def is_available(self):
                return True

            def chat_completion(self, messages, model=None, tools=None, **kw):
                return {"choices": [{"message": {"role": "assistant",
                                                 "content": "hi from " + str(model)},
                                     "finish_reason": "stop"}],
                        "model": model, "usage": {}}

        monkeypatch.setattr(om, "get_manager", lambda url=None: FakeMgr())
        from agent_friday.services import model_seat_gate as gate
        monkeypatch.setattr(gate, "resolve_local_seat",
                            lambda m, provider="local": {
                                "model": "gemma4:e4b", "seat_ok": False,
                                "requested": m, "reason": "not green",
                                "fallback": "last_known_green:gemma4:e4b"})
        attribution.reset()
        mr._call_ollama([{"role": "user", "content": "x"}],
                        model="ungated-model", tools=None)
        gen = attribution.last_generation()
        assert gen and gen["model"] == "ungated-model" or gen["model"] == "gemma4:e4b"
        # tools=None skips the gate; run again WITH tools to see substitution.
        attribution.reset()
        monkeypatch.setattr(mr, "CLAUDE_TOOLS", [], raising=False)
        mr._call_ollama([{"role": "user", "content": "x"}],
                        model="ungated-model",
                        tools=[{"name": "t", "description": "d",
                                "input_schema": {"type": "object",
                                                 "properties": {}}}])
        gen = attribution.last_generation()
        assert gen and gen["model"] == "gemma4:e4b", (
            "post-substitution model must be what gets recorded")
        assert gen["seat"] == "local"
