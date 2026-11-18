"""The voice slate: every engine and voice with the facts a device needs, and a switch check that says what
would leave the house and never moves voice to the cloud on its own."""
from __future__ import annotations

import pytest

from agent_friday.services import voice_slate as vs

CAT = {
    "voice_engines": [
        {"id": "local", "label": "Local CPU", "available": True},
        {"id": "local-gpu", "label": "Local GPU", "available": False, "hint": "Needs a CUDA GPU."},
        {"id": "gemini", "label": "Gemini Live (cloud)", "available": True},
        {"id": "openai", "label": "OpenAI Realtime (cloud)", "available": False,
         "hint": "Add an OpenAI key."},
    ],
    "tts_engines": [{"id": "piper", "available": True}, {"id": "kokoro", "available": True}],
}
CLOUD = [{"name": "elevenlabs", "label": "ElevenLabs (cloud)", "selectable": True, "reason": None,
          "models": [{"id": "eleven_flash_v2_5"}], "durable_output": True},
         {"name": "inworld", "label": "Inworld (cloud)", "selectable": False,
          "reason": "not available in this build", "models": [], "durable_output": False}]


@pytest.fixture
def pc(monkeypatch):
    state = {"settings": {"voice_engine": "local", "model_routing": {"mode": "local_preferred"}}}
    monkeypatch.setattr(vs, "_catalog", lambda: CAT)
    monkeypatch.setattr(vs, "_settings", lambda: state["settings"])
    monkeypatch.setattr(vs, "_cloud_providers", lambda: CLOUD)
    monkeypatch.setattr(vs, "_cloud_egress", lambda n: "Only the reply text, after the privacy gate.")
    monkeypatch.setattr(vs, "_piper_voices", lambda: ["en_US-amy-medium"])
    monkeypatch.setattr(vs, "_gemini_voices", lambda: {"Aoede": "female", "Puck": "male"})
    monkeypatch.setattr(vs, "_kokoro_voices", lambda: ["af_heart"])
    monkeypatch.setattr(vs, "_openai_state", lambda: {"key_present": False, "model": "m", "default": "marin",
                                                      "price": "about $0.10 a minute"})
    return state


def _rows(pc):
    return {r["id"]: r for r in vs.slate()["engines"]}


def test_every_engine_is_listed_with_where_it_runs_key_latency_and_what_leaves(pc):
    r = _rows(pc)
    assert set(r) == {"local", "local-gpu", "gemini", "openai", "cloud:elevenlabs", "cloud:inworld"}
    assert r["local"]["local"] and not r["local"]["cloud"] and r["local"]["what_leaves"] == vs.LEAVES_NOTHING
    assert r["local"]["selected"] and vs.slate()["selected"] == "local"
    assert {v["id"] for v in r["local"]["voices"]} == {"en_US-amy-medium", "af_heart"}
    g = r["gemini"]
    assert g["cloud"] and g["key_present"] and g["can_serve"] and g["latency_class"] == "fast"
    assert "Google" in g["what_leaves"] and {v["id"] for v in g["voices"]} == {"Aoede", "Puck"}
    o = r["openai"]
    assert not o["key_present"] and not o["can_serve"] and o["blocked"]["code"] == vs.UNAVAILABLE
    assert "OpenAI key" in o["blocked"]["reason"] and o["voices"][0]["id"] == "marin" and o["voices"][0]["default"]
    e = r["cloud:elevenlabs"]
    assert e["kind"] == "synthesis_only" and e["can_serve"] and not e["voices_complete"]
    assert "reply text" in e["what_leaves"] and "microphone" not in e["what_leaves"]
    assert r["cloud:inworld"]["blocked"]["reason"] == "not available in this build"
    assert all(x["latency_class"] in ("fast", "medium", "slow") and x["latency_basis"] for x in r.values())


def test_local_only_turns_every_cloud_engine_off_with_the_reason(pc):
    pc["settings"]["model_routing"]["mode"] = "local_only"
    r = _rows(pc)
    assert r["gemini"]["available"] and not r["gemini"]["can_serve"]
    assert r["gemini"]["blocked"]["code"] == vs.LOCAL_ONLY_MODE and "Local only" in r["gemini"]["blocked"]["reason"]
    assert r["local"]["can_serve"]


def test_a_local_switch_is_allowed_and_says_nothing_leaves(pc):
    p = vs.plan_switch("local", voice="af_heart", conversation_id="c1")
    assert p["outcome"] == "ok" and p["what_leaves"] == vs.LEAVES_NOTHING
    assert p["conversation_id"] == "c1" and p["takes_effect"] == "next_reply" and p["voice_verified"]


def test_a_cloud_speech_engine_is_never_switched_here_the_owner_is_asked(pc):
    p = vs.plan_switch("gemini", voice="Puck", conversation_id="c1")
    assert p["outcome"] == vs.NEEDS_CHOICE and "Google" in p["what_leaves"]
    assert p["takes_effect"] == "next_connection" and "Nothing has changed" in p["message"]


def test_cloud_synthesis_is_allowed_with_its_disclosure(pc):
    p = vs.plan_switch("cloud:elevenlabs", voice="any-account-voice-id")
    assert p["outcome"] == "ok" and "reply text" in p["what_leaves"] and not p["voice_verified"]


@pytest.mark.parametrize("engine,voice,code", [
    ("klingon", None, vs.UNKNOWN_ENGINE),
    ("openai", None, vs.UNAVAILABLE),
    ("cloud:inworld", None, vs.UNAVAILABLE),
    ("gemini", "Nope", vs.UNKNOWN_VOICE),
])
def test_a_pick_that_cannot_run_is_refused_with_a_code_and_words(pc, engine, voice, code):
    with pytest.raises(vs.VoiceNotServable) as e:
        vs.plan_switch(engine, voice=voice)
    assert e.value.code == code and e.value.reason


def test_local_only_refuses_a_cloud_switch_loudly(pc):
    pc["settings"]["model_routing"]["mode"] = "local_only"
    with pytest.raises(vs.VoiceNotServable) as e:
        vs.plan_switch("gemini")
    assert e.value.code == vs.LOCAL_ONLY_MODE


def test_every_built_in_engine_is_in_the_closed_set_and_old_spellings_resolve(pc):
    """Walks every small case: each listed built-in id is an Engine; auto and gemini-live resolve."""
    built_in = {r["id"] for r in vs.slate()["engines"] if not r["id"].startswith("cloud:")}
    assert built_in == {e.value for e in vs.Engine}
    assert vs.find("gemini-live")["id"] == "gemini" and vs.find("auto")["id"] == "local"
    pc["settings"]["voice_engine"] = "auto"
    assert vs.slate()["selected"] == "local"


def test_the_cloud_speech_engines_are_the_ones_voice_engines_names(pc):
    """One module spells the cloud engines (voice_engines); each switchable one is a closed-set Engine whose
    slate row really is a cloud row, so adding one to voice_engines.SWITCH_ENGINES reaches the slate."""
    from agent_friday.services import voice_engines
    assert {e.value for e in vs.CLOUD_SPEECH} == set(voice_engines.SWITCH_ENGINES)
    assert {e for e in voice_engines.CLOUD_ENGINES if e != "gemini-live"} <= {e.value for e in vs.Engine}
    rows = {r["id"]: r for r in vs.slate()["engines"]}
    assert all(rows[e.value]["cloud"] for e in vs.CLOUD_SPEECH)
