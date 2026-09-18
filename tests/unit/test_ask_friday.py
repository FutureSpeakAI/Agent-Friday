"""Local brain, cloud mouth (clean-sheet §4.5, D7).

`ask_friday` is the one Gemini Live tool that reaches Stephen's context: it
dispatches to the LOCAL agent pipeline and returns the answer only after the
egress gate has sealed it for google-gemini. A withheld answer is returned
whole (the marker), never partially redacted.
"""
import agent_friday.services.voice_engine as ve


def test_ask_friday_is_declared_to_the_live_api():
    names = [t[0] for t in ve._VOICE_LIVE_TOOLS]
    assert "ask_friday" in names
    assert "ask_friday" in ve._voice_tool_names()
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "ask_friday")
    assert spec[3] == ["question"]
    assert "local model" in spec[1] and "knowledge graph" in spec[1]


def test_ask_friday_is_egress_gated(monkeypatch):
    """The local model's answer passes `_gate_voice_tool_result`; a withheld
    string comes back whole, never partial."""
    import agent_friday.routes.voice as rv
    calls = {}

    def fake_generate(messages, system=None, model=None, **kw):
        calls["messages"] = messages
        calls["system"] = system
        calls["model"] = model
        calls["ctx"] = kw.get("session_ctx")
        calls["max_tokens"] = kw.get("max_tokens")
        return "Janet's number is 555-0100 and the vault says TIER_3 secret.", []
    monkeypatch.setattr("agent_friday.services.agent._generate_agent", fake_generate)
    monkeypatch.setattr("agent_friday.services.local_seats.resolve", lambda role: "seat-x")
    monkeypatch.setattr(rv, "_build_voice_system_prompt",
                        lambda settings=None, description=None: ("VOICE PROMPT", {}))
    monkeypatch.setattr(rv, "_voice_reply_cap", lambda settings=None: 300)
    gated = {}

    def fake_gate(result, fname):
        gated["in"] = result
        gated["fname"] = fname
        return "[withheld: contains never-send material]"
    monkeypatch.setattr(rv, "_gate_voice_tool_result", fake_gate)
    out = ve._voice_tool_run("ask_friday", {"question": "what is Janet's number?"},
                             lambda o: None)
    assert out == "[withheld: contains never-send material]"      # whole, not partial
    assert "555-0100" not in out
    assert gated["fname"] == "ask_friday"
    assert gated["in"].startswith("Janet's number")
    # It ran on the LOCAL pipeline with the full contract and the voice cap.
    assert calls["model"] == "seat-x"
    assert calls["ctx"]["provider"] == "local" and calls["ctx"]["is_voice"] is True
    assert calls["max_tokens"] == 300
    # The relay note rides in the USER turn and the question ends it; the
    # system text is the voice prompt untouched, so it shares the seat's
    # prefix cache with local sessions (measured 2026-09-18: any change to
    # the system message re-prefills the whole prompt).
    user = calls["messages"][0]["content"]
    assert user.endswith("what is Janet's number?")
    assert "RELAYED from a cloud voice session" in user
    assert calls["system"] == "VOICE PROMPT"


def test_ask_friday_without_a_seat_says_so(monkeypatch):
    monkeypatch.setattr("agent_friday.services.local_seats.resolve", lambda role: None)
    out = ve._voice_tool_run("ask_friday", {"question": "anything"}, lambda o: None)
    assert "not loaded" in out and "cannot be reached" in out


def test_ask_friday_reports_mind_busy_to_the_hud(monkeypatch):
    import agent_friday.routes.voice as rv
    monkeypatch.setattr("agent_friday.services.local_seats.resolve", lambda role: "seat-x")
    monkeypatch.setattr("agent_friday.services.agent._generate_agent",
                        lambda *a, **k: ("fine", []))
    monkeypatch.setattr(rv, "_build_voice_system_prompt",
                        lambda settings=None, description=None: ("P", {}))
    monkeypatch.setattr(rv, "_gate_voice_tool_result", lambda r, f: r)
    frames = []
    out = ve._voice_tool_run("ask_friday", {"question": "q"}, frames.append)
    assert out == "fine"
    stages = [f for f in frames if f.get("type") == "stage"]
    assert stages[0]["state"] == "busy" and stages[0]["detail"] == "asking local model"
    assert stages[-1]["state"] == "idle"


def test_cloud_contract_names_ask_friday():
    """§4.5 honesty line: reach is real, and it is through the local model
    -- when that model is proven (§3.1); the caller states it here."""
    import agent_friday.routes.voice as rv
    r = rv._voice_context_reach("gemini", ["query_calendar", "check_email", "ask_friday"],
                                local_mind_ready=True)
    assert r["knowledge_graph"] is True and r["memory"] is True
    assert r["full_context"] is True and r["via_local"] is True
    assert r["line"] == ("2 native tools + ask_friday → your context is reached "
                         "through Friday's local model")
    assert r["notice"] == ""
    # Without it, the old F3 warning stands.
    r = rv._voice_context_reach("gemini", ["query_calendar", "check_email"])
    assert r["knowledge_graph"] is False and r["full_context"] is False
    assert "cannot reach your knowledge graph" in r["notice"]
    # The manifest's cloud contract says the same thing from the same names.
    from agent_friday.services import voice_manifest as vm
    c = vm.VoiceManifest.cloud_contract({"knowledge_graph": True, "memory": True})
    assert c["ask_friday"] is True and "ask_friday" in c["line"]
