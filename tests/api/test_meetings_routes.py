"""The meetings API: consent before capture, one-click stop, local only."""
from __future__ import annotations

import types

import pytest

from agent_friday.services import meeting_capture as mc
from agent_friday.services import screen_click


class FakeSource:
    kind = "microphone"
    error = ""

    def __init__(self, max_s):
        self.on = None
        self.stopped = False

    def start(self, on_pcm):
        self.on = on_pcm

    def stop(self):
        self.stopped = True


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
    sources = []

    def factory(max_s):
        s = FakeSource(max_s)
        sources.append(s)
        return s

    m = mc.MeetingManager(source_factory=factory,
                          transcriber=lambda pcm: "hello there",
                          seat_resolver=lambda: None,
                          asr_check=lambda: "", lock_detector=lambda: None,
                          event_lookup=lambda: {"title": "Design review",
                                                "event_id": "ev1"},
                          notifier=lambda *a: None, tick_s=0.05,
                          scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
                          partials_factory=lambda: None, ledger=lambda *a, **k: None,
                          retention=lambda: "")
    m.sources = sources
    monkeypatch.setattr(mc, "_manager", m)
    # These tests are the page: its click on the screen is a given unless a test says otherwise.
    monkeypatch.setattr(screen_click, "screen_session", lambda req: True)
    yield m
    try:
        m.stop()
    except mc.MeetingError:
        pass
    m.wait_idle(5)


def start_body(client, **extra):
    """What the start card sends: the consent version and the single-use token
    it was handed."""
    card = client.post("/api/meetings/start-card").get_json()
    return {"consent_ack": True, "nonce": card["nonce"],
            "consent_version": card["consent_version"]} | extra


def test_start_needs_consent_then_records_and_stops(client, mgr):
    r = client.post("/api/meetings/start", json={"title": "Interview"})
    assert r.status_code == 400
    assert r.get_json()["code"] == "consent_required"
    assert mgr.sources == []

    st = client.get("/api/meetings/status").get_json()
    assert st["recording"] is False
    assert st["consent_text"] == mc.CONSENT_TEXT
    assert st["system_audio"]["captured"] is False

    # consent alone is not a start: the owner's explicit confirm is part of it
    r = client.post("/api/meetings/start", json={
        "consent_ack": True, "consent_version": mc.CONSENT_VERSION})
    assert r.status_code == 400 and r.get_json()["code"] == "confirm_required"
    assert mgr.sources == []

    # a bare confirm:true in the body is not a start: the card's token is
    r = client.post("/api/meetings/start", json={
        "consent_ack": True, "confirm": True, "consent_version": mc.CONSENT_VERSION})
    assert r.status_code == 400 and r.get_json()["code"] == "confirm_required"
    assert mgr.sources == []

    r = client.post("/api/meetings/start", json=start_body(client))
    assert r.status_code == 200, r.get_json()
    mid = r.get_json()["id"]
    assert client.get("/api/meetings/status").get_json()["recording"] is True

    r = client.post("/api/meetings/stop")
    assert r.status_code == 200
    assert mgr.sources[0].stopped
    assert mgr.wait_idle(5)

    rows = client.get("/api/meetings").get_json()["meetings"]
    assert [x["id"] for x in rows] == [mid]
    assert rows[0]["title"] == "Design review"
    one = client.get("/api/meetings/" + mid).get_json()["meeting"]
    assert one["summary"]["status"] == "skipped"

    assert client.delete("/api/meetings/" + mid).status_code == 200
    assert client.get("/api/meetings/" + mid).status_code == 404


def test_the_suggested_title_comes_from_the_calendar(client, mgr):
    assert client.get("/api/meetings/suggest").get_json()["event"]["title"] == "Design review"


def test_a_remote_session_cannot_start_the_microphone(client, mgr):
    r = client.post("/api/meetings/start", json={"consent_ack": True},
                    environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code in (401, 403)
    assert mgr.sources == []
    r = client.get("/api/meetings", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code in (401, 403)


def test_tasks_need_a_choice(client, mgr):
    r = client.post("/api/meetings/abcdefabcdef/tasks", json={})
    assert r.status_code == 400


def test_a_follow_up_needs_recipient_subject_and_body(client, mgr):
    r = client.post("/api/meetings/abcdefabcdef/follow-up", json={"to": "a@b.example"})
    assert r.status_code == 400


def test_the_start_card_hands_out_the_consent_text_and_a_single_use_token(client, mgr, monkeypatch):
    from agent_friday.services import meeting_policy as mp
    monkeypatch.setattr(mp, "_get_settings", lambda: {})
    card = client.post("/api/meetings/start-card").get_json()
    assert card["status"] == "ok" and card["nonce"]
    assert card["consent_text"] == mc.CONSENT_TEXT
    assert card["consent_version"] == mc.CONSENT_VERSION
    assert card["jurisdiction_note"], "the jurisdiction note is part of the card"
    assert mgr.sources == [], "asking for the card started nothing"

    body = {"consent_ack": True, "nonce": card["nonce"],
            "consent_version": card["consent_version"]}
    assert client.post("/api/meetings/start", json=body).status_code == 200
    client.post("/api/meetings/stop")
    assert mgr.wait_idle(5)
    r = client.post("/api/meetings/start", json=body)
    assert r.status_code == 400 and r.get_json()["code"] == "confirm_required"
    assert len(mgr.sources) == 1


def test_a_start_without_the_consent_version_the_card_showed_is_refused(client, mgr):
    card = client.post("/api/meetings/start-card").get_json()
    r = client.post("/api/meetings/start", json={"consent_ack": True, "nonce": card["nonce"]})
    assert r.status_code == 400 and r.get_json()["code"] == "consent_changed"
    assert mgr.sources == []


def test_the_start_card_is_for_the_person_at_this_machine(client, mgr):
    r = client.post("/api/meetings/start-card", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code in (401, 403)


def test_the_start_card_and_the_start_need_a_click_on_the_screen(client, mgr, monkeypatch):
    """A local process with no browser headers can neither get a start card nor
    start a recording with a made-up body."""
    monkeypatch.setattr(screen_click, "screen_session", lambda req: False)
    r = client.post("/api/meetings/start-card")
    assert r.status_code == 403 and r.get_json()["code"] == "screen_only"
    r = client.post("/api/meetings/start", json={
        "consent_ack": True, "nonce": "anything", "consent_version": mc.CONSENT_VERSION})
    assert r.status_code == 403 and r.get_json()["code"] == "screen_only"
    r = client.post("/api/meetings/start", json={
        "consent_ack": True, "confirm": True, "consent_version": mc.CONSENT_VERSION})
    assert r.status_code == 403
    assert mgr.sources == []


def test_a_token_from_a_different_screen_session_is_refused(client, mgr, monkeypatch):
    from agent_friday.routes import meetings as rm
    who = {"id": "session-a"}
    monkeypatch.setattr(rm, "_session_id", lambda: who["id"])
    card = client.post("/api/meetings/start-card").get_json()
    body = {"consent_ack": True, "nonce": card["nonce"],
            "consent_version": card["consent_version"]}
    who["id"] = "session-b"
    r = client.post("/api/meetings/start", json=body)
    assert r.status_code == 400 and r.get_json()["code"] == "confirm_required"
    assert mgr.sources == []
    who["id"] = "session-a"
    assert client.post("/api/meetings/start", json=body).status_code == 200
