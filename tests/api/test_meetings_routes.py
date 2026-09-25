"""The meetings API: consent before capture, one-click stop, local only."""
from __future__ import annotations

import pytest

from agent_friday.services import meeting_capture as mc


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
                          notifier=lambda *a: None, tick_s=0.05)
    m.sources = sources
    monkeypatch.setattr(mc, "_manager", m)
    yield m
    try:
        m.stop()
    except mc.MeetingError:
        pass
    m.wait_idle(5)


def test_start_needs_consent_then_records_and_stops(client, mgr):
    r = client.post("/api/meetings/start", json={"title": "Interview"})
    assert r.status_code == 400
    assert r.get_json()["code"] == "consent_required"
    assert mgr.sources == []

    st = client.get("/api/meetings/status").get_json()
    assert st["recording"] is False
    assert st["consent_text"] == mc.CONSENT_TEXT
    assert st["system_audio"]["captured"] is False

    r = client.post("/api/meetings/start", json={
        "consent_ack": True, "consent_version": mc.CONSENT_VERSION})
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
