"""The voice-slate contract for the phone, the tablet and the TV (Addendum H, 2026-11-15).

* GET /tv/v1/voices lists every voice engine and voice with provider, local or cloud, key present, a latency
  class and what leaves the house; an owner device may select, a TV or family device sees it read-only.
* POST /tv/v1/voice/switch moves ONE conversation to a voice engine, never quietly: a local engine binds, a
  cloud speech engine is 409 needs_choice (the owner answers on the PC), Local only and unavailable engines are
  coded refusals, and a non-owner device is 403.
"""
import pytest

from agent_friday.services import device_pairing, voice_slate
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, voices
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

CAT = {"voice_engines": [{"id": "local", "label": "Local CPU", "available": True},
                         {"id": "local-gpu", "label": "Local GPU", "available": False, "hint": "Needs a GPU."},
                         {"id": "gemini", "label": "Gemini Live (cloud)", "available": True},
                         {"id": "openai", "label": "OpenAI Realtime", "available": False, "hint": "Add a key."}],
       "tts_engines": [{"id": "piper", "available": True}]}


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    st = {"s": {"voice_engine": "local", "model_routing": {"mode": "local_preferred"}}, "asked": []}
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(voice_slate, "_catalog", lambda: CAT)
    monkeypatch.setattr(voice_slate, "_settings", lambda: st["s"])
    monkeypatch.setattr(voice_slate, "_cloud_providers", lambda: [])
    monkeypatch.setattr(voice_slate, "_piper_voices", lambda: ["en_US-amy-medium"])
    monkeypatch.setattr(voice_slate, "_gemini_voices", lambda: {"Aoede": "female", "Puck": "male"})
    monkeypatch.setattr(voice_slate, "_kokoro_voices", lambda: [])
    monkeypatch.setattr(voice_slate, "_openai_state",
                        lambda: {"key_present": False, "model": "m", "default": "marin", "price": None})
    monkeypatch.setattr(voices, "_ask_owner", lambda engine: st["asked"].append(engine) or True)
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    voices._reset_for_tests()
    yield st
    device_pairing._reset_for_tests()
    voices._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def paired(kind, member):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="A device", kind=kind)
    if member:
        household.bind_device(dev.id, member)
    return dev


def get(client, dev, **q):
    return client.get("/tv/v1/voices", query_string=q, headers=auth(open_session(client, dev)))


def switch(client, dev, **body):
    return client.post("/tv/v1/voice/switch", json=body, headers=auth(open_session(client, dev)))


@pytest.mark.parametrize("kind", ["phone", "tablet", "watch"])
def test_an_owner_device_gets_every_engine_with_the_facts(client, kind):
    body = get(client, paired(kind, "stephen")).get_json()
    assert body["scope"] == "owner" and body["can_select"] and body["selected"] == "local"
    e = {x["id"]: x for x in body["engines"]}
    assert {"local", "local-gpu", "gemini", "openai"} <= set(e)
    assert e["gemini"]["cloud"] and e["gemini"]["key_present"] and "Google" in e["gemini"]["what_leaves"]
    assert not e["openai"]["can_serve"] and e["openai"]["blocked"]["reason"]
    assert e["local"]["local"] and e["local"]["latency_class"] in ("fast", "medium", "slow")


def test_a_tv_sees_the_list_but_selects_nothing_and_is_told_why(client):
    body = get(client, paired("tv", None)).get_json()
    assert body["scope"] == "household" and not body["can_select"] and "owner's phone" in body["note"]
    assert body["engines"]


def test_no_session_is_a_401(client):
    assert client.get("/tv/v1/voices").status_code == 401


def test_a_local_switch_binds_that_conversation_and_says_what_it_did(client):
    dev = paired("phone", "stephen")
    r = switch(client, dev, engine="local", voice="en_US-amy-medium", conversation_id="c1")
    b = r.get_json()
    assert r.status_code == 200 and b["ok"] and b["applies"] == "this_conversation"
    assert b["bound"] == {"conversation_id": "c1", "engine": "local", "voice": "en_US-amy-medium"}
    assert b["what_leaves"] == voice_slate.LEAVES_NOTHING
    again = get(client, dev, conversation_id="c1").get_json()
    assert again["conversation"]["engine"] == "local"
    assert get(client, dev, conversation_id="other").get_json()["conversation"]["engine"] is None


def test_a_cloud_speech_engine_is_409_needs_choice_nothing_is_bound_and_the_pc_asks(client, _clean):
    r = switch(client, paired("tablet", "stephen"), engine="gemini", voice="Puck", conversation_id="c1")
    b = r.get_json()
    assert r.status_code == 409 and b["code"] == "needs_choice" and b["applies"] == "nothing_changed"
    assert "Google" in b["what_leaves"] and b["asked_on_pc"] is True and _clean["asked"] == ["gemini"]
    assert voices.bound_voice("c1") is None


@pytest.mark.parametrize("body,status,code", [
    ({"engine": "klingon"}, 400, "unknown_engine"),
    ({"engine": "openai"}, 409, "engine_unavailable"),
    ({"engine": "local", "voice": "nope"}, 400, "unknown_voice"),
])
def test_a_pick_that_cannot_run_is_refused_in_words_and_nothing_is_bound(client, body, status, code):
    r = switch(client, paired("phone", "stephen"), conversation_id="c1", **body)
    j = r.get_json()
    assert r.status_code == status and j["code"] == code and j["error"] and voices.bound_voice("c1") is None


def test_local_only_refuses_a_cloud_switch_loudly(client, _clean):
    _clean["s"]["model_routing"]["mode"] = "local_only"
    r = switch(client, paired("phone", "stephen"), engine="gemini", conversation_id="c1")
    assert r.status_code == 409 and r.get_json()["code"] == "local_only_mode" and not _clean["asked"]


def test_a_tv_cannot_switch_voice_and_a_bad_request_is_400(client):
    assert switch(client, paired("tv", None), engine="local", conversation_id="c1").status_code == 403
    assert switch(client, paired("phone", "stephen"), engine="local").status_code == 400


def test_a_bound_voice_is_rechecked_when_read_and_says_what_leaves(client, _clean):
    dev = paired("phone", "stephen")
    switch(client, dev, engine="local", voice="en_US-amy-medium", conversation_id="c9")
    b = voices.bound_voice("c9")
    assert b["still_valid"] is True and b["notice"] is None and b["engine"] == "local"
    assert b["what_leaves"] == voice_slate.LEAVES_NOTHING and b["latency_class"] and b["local"] is True
    assert voices.bound_voice("never-bound") is None


def test_a_bound_cloud_synthesis_voice_turns_invalid_loudly_when_local_only_is_switched_on(client, _clean, monkeypatch):
    monkeypatch.setattr(voice_slate, "_cloud_providers", lambda: [
        {"name": "elevenlabs", "label": "ElevenLabs (cloud)", "selectable": True, "reason": None,
         "models": [], "durable_output": True}])
    monkeypatch.setattr(voice_slate, "_cloud_egress", lambda n: "Only the reply text, after the privacy gate.")
    dev = paired("tablet", "stephen")
    assert switch(client, dev, engine="cloud:elevenlabs", conversation_id="c8").status_code == 200
    assert voices.bound_voice("c8")["still_valid"] is True
    _clean["s"]["model_routing"]["mode"] = "local_only"
    b = voices.bound_voice("c8")
    assert b["still_valid"] is False and "Local only" in b["notice"] and b["engine"] == "cloud:elevenlabs"
