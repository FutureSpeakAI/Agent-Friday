"""The meetings routes for consent, retention, suggestions and audio deletion.

Local-only like the rest of /api/meetings; the audio device, the settings
store and the scheduler are fakes.
"""
from __future__ import annotations

import array
import math
import types

import pytest

from agent_friday.services import meeting_capture as mc
from agent_friday.services import meeting_policy as mp

REMOTE = {"REMOTE_ADDR": "203.0.113.9"}


def tone(seconds):
    n = int(mc.RATE * seconds)
    return array.array("h", (int(8000 * math.sin(2 * math.pi * 440 * i / mc.RATE))
                             for i in range(n))).tobytes()


class Src:
    kind = "microphone"
    error = ""

    def __init__(self, max_s):
        self.on = None

    def start(self, on_pcm):
        self.on = on_pcm

    def stop(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = {}
    monkeypatch.setattr(mp, "_get_settings", lambda: dict(cfg))
    monkeypatch.setattr(mp, "_put_settings", lambda d: cfg.update(d))
    monkeypatch.setattr(mp, "_state_path", lambda: tmp_path / "meetings" / "policy_state.json")
    monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
    calls, ledger = [], []

    def factory(max_s, system_audio=False):
        calls.append({"system_audio": system_audio})
        s = Src(max_s)
        calls.append(s)
        return s
    m = mc.MeetingManager(
        source_factory=factory, transcriber=lambda pcm: "we agreed", seat_resolver=lambda: "model",
        notes_call=lambda s, u, mo: {"notes": ["a point"], "decisions": [], "action_items": []},
        asr_check=lambda: "", lock_detector=lambda: None,
        event_lookup=lambda: {"title": "Design review", "event_id": "ev1"},
        notifier=lambda *a: None, scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
        partials_factory=lambda: None, speech_gate=lambda pcm: True,
        ledger=lambda k, **f: ledger.append((k, f)), retention=lambda: mp.audio_retention(),
        tick_s=0.05, chunk_s=1.0)
    m.calls, m.ledger_lines, m.cfg = calls, ledger, cfg
    monkeypatch.setattr(mc, "_manager", m)
    yield m
    try:
        m.stop()
    except mc.MeetingError:
        pass
    m.wait_idle(5)


def test_consent_line_is_read_edited_and_saved(client, env):
    d = client.get("/api/meetings/consent").get_json()
    assert d["line"] == mp.DEFAULT_CONSENT_LINE and d["mode"] == "ask_everyone"
    assert "everyone's consent" in d["jurisdiction_note"] and d["speak"] is False
    r = client.post("/api/meetings/consent", json={"line": "I'm recording for notes.", "speak": True})
    assert r.status_code == 200 and r.get_json()["line"] == "I'm recording for notes."
    assert env.cfg["meeting_speak_consent"] is True
    assert client.get("/api/meetings/consent").get_json()["edited"] is True


def test_a_consent_mode_that_is_not_offered_is_refused_without_a_stack_trace(client, env):
    r = client.post("/api/meetings/consent", json={"mode": "whatever"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "bad_request" and "Traceback" not in str(body) and "ValueError" not in str(body)


def test_retention_choices_are_offered_with_nothing_preselected(client, env):
    d = client.get("/api/meetings/retention").get_json()
    assert d["audio"]["selected"] == "" and d["text"]["selected"] == ""
    assert [o["value"] for o in d["audio"]["options"] if o["recommended"]] == ["delete_after_notes"]
    r = client.post("/api/meetings/retention", json={"audio": "30_days", "text": "days", "days": 14})
    assert r.status_code == 200
    again = client.get("/api/meetings/retention").get_json()
    assert again["audio"]["selected"] == "30_days" and again["text"]["days"] == 14


@pytest.mark.parametrize("body", [{"audio": "forever"}, {"days": "abc"}, {"days": -3}, {"text": "shred"}])
def test_retention_refuses_what_it_did_not_offer_in_plain_words(client, env, body):
    r = client.post("/api/meetings/retention", json=body)
    assert r.status_code == 400
    assert r.get_json()["error"] == "That choice was not understood."


def test_a_suggestion_comes_once_per_event_and_two_declines_turn_it_off(client, env):
    first = client.get("/api/meetings/suggestion").get_json()
    assert first["suggestion"]["text"].startswith("Your “Design review” is starting")
    again = client.get("/api/meetings/suggestion").get_json()["suggestion"]
    assert again == first["suggestion"], "loading the card does not spend it"
    r = client.post("/api/meetings/suggestion", json={"action": "shown", "key": again["key"]})
    assert r.status_code == 200
    assert client.get("/api/meetings/suggestion").get_json()["suggestion"] is None
    client.post("/api/meetings/suggestion", json={"action": "decline"})
    st = client.post("/api/meetings/suggestion", json={"action": "decline"}).get_json()
    assert st["enabled"] is False and st["off_after_declines"] is True
    env.event_lookup = lambda: {"title": "Another call", "event_id": "ev2"}
    assert client.get("/api/meetings/suggestion").get_json()["suggestion"] is None
    back = client.post("/api/meetings/suggestion/reset").get_json()
    assert back["enabled"] is True
    assert client.get("/api/meetings/suggestion").get_json()["suggestion"]["event"]["title"] == "Another call"


def test_no_suggestion_while_a_meeting_is_recording(client, env):
    client.post("/api/meetings/start", json={"consent_ack": True, "confirm": True})
    assert client.get("/api/meetings/suggestion").get_json()["suggestion"] is None


def test_the_start_card_can_ask_for_system_audio_and_the_factory_hears_it(client, env):
    r = client.post("/api/meetings/start", json={"consent_ack": True, "confirm": True, "system_audio": True})
    assert r.status_code == 200
    assert env.calls[0] == {"system_audio": True}
    client.post("/api/meetings/stop")


def test_system_audio_is_off_unless_it_is_exactly_true(client, env):
    client.post("/api/meetings/start", json={"consent_ack": True, "confirm": True, "system_audio": "yes"})
    assert env.calls[0] == {"system_audio": False}
    client.post("/api/meetings/stop")


def test_deleting_only_the_audio_keeps_the_notes(client, env):
    st = client.post("/api/meetings/start", json={"consent_ack": True, "confirm": True,
                                                   "keep_audio": True}).get_json()
    env.calls[1].on(tone(0.9))
    client.post("/api/meetings/stop")
    assert env.wait_idle(5)
    mid = st["id"]
    assert client.get("/api/meetings/%s/audio" % mid).status_code == 200
    r = client.delete("/api/meetings/%s/audio" % mid)
    assert r.status_code == 200 and r.get_json()["has_audio"] is False
    assert client.get("/api/meetings/%s/audio" % mid).status_code == 404
    one = client.get("/api/meetings/" + mid).get_json()["meeting"]
    assert one["summary"]["notes"] == ["a point"] and one["has_audio"] is False
    assert client.delete("/api/meetings/" + mid).status_code == 200
    assert [k for k, _ in env.ledger_lines] == ["meeting_deletion", "meeting_deletion"]


def test_deleting_the_audio_of_a_recording_is_refused(client, env):
    st = client.post("/api/meetings/start", json={"consent_ack": True, "confirm": True,
                                                   "keep_audio": True}).get_json()
    r = client.delete("/api/meetings/%s/audio" % st["id"])
    assert r.status_code == 409 and r.get_json()["code"] == "recording"


def test_live_is_empty_when_idle(client, env):
    assert client.get("/api/meetings/live").get_json()["partials"] == {}


@pytest.mark.parametrize("method,path", [
    ("get", "/api/meetings/consent"), ("post", "/api/meetings/consent"),
    ("get", "/api/meetings/retention"), ("post", "/api/meetings/retention"),
    ("get", "/api/meetings/suggestion"), ("post", "/api/meetings/suggestion"),
    ("post", "/api/meetings/suggestion/reset"), ("get", "/api/meetings/live"),
    ("delete", "/api/meetings/abcdefabcdef/audio"),
])
def test_the_new_routes_are_for_the_person_at_this_machine(client, env, method, path):
    r = getattr(client, method)(path, environ_base=REMOTE)
    assert r.status_code in (401, 403)
    assert env.calls == []
