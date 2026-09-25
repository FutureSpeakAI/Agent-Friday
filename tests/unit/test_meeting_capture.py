"""Meeting capture: consent, the indicator, auto-stop, storage and local notes.

Every test drives a fake microphone with synthetic PCM (a sine tone for
speech, zeros for silence) and a fake transcriber. No real audio is recorded
and no whisper model is loaded.
"""
from __future__ import annotations

import array
import json
import math
import time

import pytest

from agent_friday.services import meeting_capture as mc
from agent_friday.services import local_only_guard


def tone(seconds: float, amp: int = 8000, hz: float = 440.0) -> bytes:
    n = int(mc.RATE * seconds)
    a = array.array("h", (int(amp * math.sin(2 * math.pi * hz * i / mc.RATE))
                          for i in range(n)))
    return a.tobytes()


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(mc.RATE * seconds)


class FakeSource:
    kind = "microphone"

    def __init__(self, max_s):
        self.max_s = max_s
        self.error = ""
        self.on = None
        self.stopped = False

    def start(self, on_pcm):
        self.on = on_pcm

    def stop(self):
        self.stopped = True

    def feed(self, pcm):
        self.on(pcm)


class Harness:
    def __init__(self, tmp_path, monkeypatch, **kw):
        monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
        self.root = tmp_path / "meetings"
        self.sources = []
        self.transcribed = []
        self.notes_calls = []
        self.todos = []
        self.emails = []
        self.notices = []
        self.T = [1000.0]
        self.seat = kw.pop("seat", "local-test-model")
        self.notes = kw.pop("notes", {
            "notes": ["Reviewed the launch plan."],
            "decisions": ["Ship on Friday."],
            "action_items": [
                {"task": "Send the release notes", "owner": "Sam", "due": "2026-10-02"},
                {"task": "Book the demo room", "owner": "", "due": "unknown"}]})

        def factory(max_s):
            s = FakeSource(max_s)
            self.sources.append(s)
            return s

        def transcriber(pcm):
            self.transcribed.append(len(pcm))
            return "we agree to ship on friday"

        def notes_call(system, user, model):
            # The call happens inside a local-only run: a cloud provider
            # would be refused right here.
            assert local_only_guard.is_active()
            with pytest.raises(local_only_guard.CloudRefused):
                local_only_guard.refuse_if_active("anthropic", "a-cloud-model")
            self.notes_calls.append((system, user, model))
            return self.notes

        opts = dict(source_factory=factory, transcriber=transcriber,
                    seat_resolver=lambda: self.seat, notes_call=notes_call,
                    asr_check=lambda: "", lock_detector=lambda: None,
                    event_lookup=lambda: None,
                    notifier=lambda m, reason, msg: self.notices.append(reason),
                    todo_writer=lambda t: self.todos.append(t) or t,
                    email_requester=lambda **k: self.emails.append(k) or {"approval_id": "ap1"},
                    clock=lambda: self.T[0], tick_s=0.01, chunk_s=1.0)
        opts.update(kw)
        self.m = mc.MeetingManager(**opts)

    @property
    def src(self):
        return self.sources[-1]

    def start(self, **kw):
        kw.setdefault("consent_ack", True)
        return self.m.start(**kw)

    def files(self):
        return sorted(p.relative_to(self.root).as_posix()
                      for p in self.root.rglob("*") if p.is_file())


@pytest.fixture
def h(tmp_path, monkeypatch):
    hh = Harness(tmp_path, monkeypatch)
    yield hh
    try:
        hh.m.stop()
    except mc.MeetingError:
        pass
    hh.m.wait_idle(5)


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


# ── consent ─────────────────────────────────────────────────────────────────

def test_capture_does_not_start_without_consent(h):
    with pytest.raises(mc.MeetingError) as e:
        h.m.start(consent_ack=False, title="Interview")
    assert e.value.code == "consent_required"
    assert mc.CONSENT_TEXT in str(e.value)
    assert h.sources == [], "the microphone was opened before consent"
    assert not h.root.exists()
    # A truthy value that is not an explicit confirmation is not consent.
    with pytest.raises(mc.MeetingError):
        h.m.start(consent_ack="yes")
    assert h.sources == []


def test_the_acknowledgement_is_stored_and_opens_the_transcript(h):
    st = h.start(title="School meeting")
    m = h.m.get(st["id"])
    assert m["consent"]["text"] == mc.CONSENT_TEXT
    assert m["consent"]["acknowledged_at"]
    assert m["segments"][0]["kind"] == "consent"
    assert mc.CONSENT_TEXT in m["segments"][0]["text"]


def test_a_changed_consent_statement_must_be_read_again(h):
    with pytest.raises(mc.MeetingError) as e:
        h.start(consent_version=mc.CONSENT_VERSION + 1)
    assert e.value.code == "consent_changed"
    assert h.sources == []


def test_no_recording_without_local_transcription(tmp_path, monkeypatch):
    hh = Harness(tmp_path, monkeypatch, asr_check=lambda: "not installed")
    with pytest.raises(mc.MeetingError) as e:
        hh.start()
    assert e.value.code == "no_local_transcription"
    assert hh.sources == []


# ── lifecycle and indicator ────────────────────────────────────────────────

def test_session_lifecycle_transcribes_in_the_background(h):
    st = h.start(title="Interview")
    assert st["recording"] is True and st["capturing"] is False
    with pytest.raises(mc.MeetingError) as e:
        h.start()
    assert e.value.code == "already_recording"
    h.src.feed(tone(2.5))
    assert wait_for(lambda: len(h.transcribed) >= 2)
    row = h.m.stop()
    assert h.src.stopped
    assert row["stop_reason"] == "owner"
    assert h.m.status()["recording"] is False
    assert h.m.wait_idle(5)
    m = h.m.get(st["id"])
    speech = [s for s in m["segments"] if s["kind"] == "speech"]
    # 2.5 s in 1 s chunks: two full chunks while recording, the tail at stop.
    assert len(speech) == 3
    assert [s["t"] for s in speech] == sorted(s["t"] for s in speech)
    assert m["state"] == "done"
    assert m["summary"]["status"] == "done"


def test_indicator_state_tracks_first_sample_and_elapsed(h):
    h.start(title="Standup")
    h.T[0] += 42
    st = h.m.status()
    assert st["recording"] and st["elapsed_s"] == 42
    assert st["capturing"] is False, "indicator claimed capture before any audio"
    h.src.feed(silence(0.02))
    assert h.m.status()["capturing"] is True
    assert h.m.status()["system_audio"]["captured"] is False


def test_silence_is_not_transcribed(h):
    h.start()
    h.src.feed(silence(2.0))
    h.m.stop()
    assert h.m.wait_idle(5)
    assert h.transcribed == []


def test_a_transcription_failure_leaves_a_marked_gap(tmp_path, monkeypatch):
    def boom(pcm):
        raise RuntimeError("model not loaded")
    hh = Harness(tmp_path, monkeypatch, transcriber=boom)
    st = hh.start()
    hh.src.feed(tone(1.0))
    hh.m.stop()
    assert hh.m.wait_idle(5)
    m = hh.m.get(st["id"])
    assert m["transcription_errors"] >= 1
    assert any(s["kind"] == "gap" for s in m["segments"])


# ── auto-stop ───────────────────────────────────────────────────────────────

def test_recording_stops_at_its_maximum_length(h):
    st = h.start(max_duration_s=120)
    h.T[0] += 121
    assert wait_for(lambda: not h.m.status()["recording"])
    assert wait_for(lambda: h.notices == ["max_duration"])
    assert h.src.stopped
    assert h.m.status()["last_stop"]["reason"] == "max_duration"
    h.m.wait_idle(5)
    assert h.m.get(st["id"])["stop_reason"] == "max_duration"


def test_default_maximum_is_two_hours(h):
    h.start()
    assert h.src.max_s == 2 * 3600
    assert h.m.status()["max_s"] == 2 * 3600


def test_recording_stops_when_the_machine_sleeps(tmp_path, monkeypatch):
    W = [5000.0]
    hh = Harness(tmp_path, monkeypatch, wall=lambda: W[0])
    hh.start()
    time.sleep(0.05)
    assert hh.m.status()["recording"]
    W[0] += 600          # ten minutes passed between two ticks
    assert wait_for(lambda: not hh.m.status()["recording"])
    assert hh.m.status()["last_stop"]["reason"] == "sleep"
    hh.m.wait_idle(5)


def test_recording_stops_when_the_screen_locks(tmp_path, monkeypatch):
    locked = [False]
    hh = Harness(tmp_path, monkeypatch, lock_detector=lambda: locked[0])
    hh.start()
    assert hh.m.status()["lock_detection"] is True
    locked[0] = True
    assert wait_for(lambda: not hh.m.status()["recording"])
    assert hh.m.status()["last_stop"]["reason"] == "screen_locked"
    hh.m.wait_idle(5)


def test_lock_detection_that_reads_locked_at_start_is_ignored(tmp_path, monkeypatch):
    # The owner just clicked Start, so a detector saying "locked" cannot be
    # read correctly here; it must not stop every recording at once.
    hh = Harness(tmp_path, monkeypatch, lock_detector=lambda: True)
    hh.start()
    time.sleep(0.1)
    assert hh.m.status()["recording"] is True
    assert hh.m.status()["lock_detection"] is False
    hh.m.stop()
    hh.m.wait_idle(5)


def test_a_failed_microphone_stops_the_recording(h):
    h.start()
    h.src.error = "The microphone is already in use by another program."
    assert wait_for(lambda: not h.m.status()["recording"])
    assert h.m.status()["last_stop"]["reason"] == "device_error"


# ── storage ─────────────────────────────────────────────────────────────────

def test_transcript_and_title_are_encrypted_at_rest(h):
    st = h.start(title="Parent teacher conference")
    h.src.feed(tone(1.0))
    h.m.stop()
    assert h.m.wait_idle(5)
    files = h.files()
    assert files == [st["id"] + "/meeting.json.enc"]
    raw = (h.root / files[0]).read_bytes()
    for plain in (b"Parent teacher", b"ship on friday", b"Ship on Friday",
                  b"release notes", mc.CONSENT_TEXT.encode()):
        assert plain not in raw
    m = h.m.get(st["id"])
    assert m["title"] == "Parent teacher conference"
    assert any("ship on friday" in s["text"] for s in m["segments"])


def test_no_audio_is_kept_by_default(h):
    st = h.start()
    h.src.feed(tone(2.0))
    h.m.stop()
    assert h.m.wait_idle(5)
    assert h.files() == [st["id"] + "/meeting.json.enc"]
    assert h.m.get(st["id"])["has_audio"] is False
    with pytest.raises(mc.MeetingError):
        h.m.audio_wav(st["id"])


def test_audio_is_kept_encrypted_only_when_asked(h):
    import io
    import wave
    pcm = tone(2.0)
    st = h.start(keep_audio=True)
    h.src.feed(pcm)
    h.m.stop()
    assert h.m.wait_idle(5)
    audio = [f for f in h.files() if "/audio/" in f]
    assert audio and all(f.endswith(".pcm.enc") for f in audio)
    for f in audio:
        assert pcm[1000:1400] not in (h.root / f).read_bytes()
    with wave.open(io.BytesIO(h.m.audio_wav(st["id"]))) as w:
        assert w.readframes(w.getnframes()) == pcm


def test_delete_removes_transcript_notes_and_audio(h):
    st = h.start(keep_audio=True)
    h.src.feed(tone(1.0))
    with pytest.raises(mc.MeetingError) as e:
        h.m.delete(st["id"])
    assert e.value.code == "recording"
    h.m.stop()
    assert h.m.wait_idle(5)
    assert h.files()
    out = h.m.delete(st["id"])
    assert out["exists"] is False
    assert h.files() == []
    assert h.m.list() == []
    with pytest.raises(mc.MeetingError) as e:
        h.m.get(st["id"])
    assert e.value.code == "not_found"


def test_meeting_ids_cannot_reach_outside_the_folder(h):
    for bad in ("../settings", "..", "abc", "/etc/passwd"):
        with pytest.raises(mc.MeetingError):
            h.m.get(bad)
        with pytest.raises(mc.MeetingError):
            h.m.delete(bad)


def test_the_title_comes_from_the_calendar_when_none_is_given(tmp_path, monkeypatch):
    hh = Harness(tmp_path, monkeypatch, event_lookup=lambda: {
        "title": "Design review", "event_id": "ev1"})
    st = hh.start()
    m = hh.m.get(st["id"])
    assert (m["title"], m["event_id"]) == ("Design review", "ev1")
    hh.m.stop()
    hh.m.wait_idle(5)


# ── notes ───────────────────────────────────────────────────────────────────

def _record(hh, seconds=1.0, **kw):
    st = hh.start(**kw)
    hh.src.feed(tone(seconds))
    hh.m.stop()
    assert hh.m.wait_idle(5)
    return hh.m.get(st["id"])


def test_notes_are_written_on_a_local_seat(h):
    m = _record(h)
    s = m["summary"]
    assert s["status"] == "done" and s["local"] is True
    assert s["model"] == "local-test-model"
    assert len(h.notes_calls) == 1
    assert "ship on friday" in h.notes_calls[0][1]
    assert s["decisions"] == ["Ship on Friday."]
    # An owner or date is kept only when stated.
    assert s["action_items"][0] == {"task": "Send the release notes",
                                    "owner": "Sam", "due": "2026-10-02"}
    assert s["action_items"][1] == {"task": "Book the demo room",
                                    "owner": None, "due": None}


def test_notes_are_skipped_with_a_reason_when_no_local_seat_serves(tmp_path, monkeypatch):
    hh = Harness(tmp_path, monkeypatch, seat=None)
    m = _record(hh)
    assert m["summary"]["status"] == "skipped"
    assert m["summary"]["reason"] == mc.NO_LOCAL_SEAT
    assert hh.notes_calls == [], "notes were attempted without a local seat"
    # Retrying once a local seat is up writes them.
    hh.seat = "local-test-model"
    assert hh.m.write_notes(m["id"])["status"] == "done"


def test_a_model_that_returns_nothing_is_a_failure_not_empty_notes(tmp_path, monkeypatch):
    hh = Harness(tmp_path, monkeypatch, notes=None)
    m = _record(hh)
    assert m["summary"]["status"] == "failed"
    assert "action_items" not in m["summary"]


def test_nothing_transcribed_means_no_notes_call(h):
    st = h.start()
    h.src.feed(silence(1.0))
    h.m.stop()
    assert h.m.wait_idle(5)
    assert h.m.get(st["id"])["summary"]["status"] == "skipped"
    assert h.notes_calls == []


def test_the_default_notes_path_is_local_only():
    import inspect
    src = inspect.getsource(mc)
    assert "local_call.call_json" in src
    for cloud in ("model_router", "anthropic", "_call_openai", "call_claude"):
        assert cloud not in src.lower().replace("never go to a cloud", "")


def test_long_transcripts_are_split_and_merged(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "NOTES_CHARS_PER_CALL", 60)
    hh = Harness(tmp_path, monkeypatch)
    _record(hh, seconds=4.0)
    assert len(hh.notes_calls) > 1
    m = hh.m.list()[0]
    full = hh.m.get(m["id"])["summary"]
    assert full["decisions"] == ["Ship on Friday."], "merge duplicated items"


# ── actions only on request ─────────────────────────────────────────────────

def test_action_items_become_tasks_only_on_request(h):
    m = _record(h)
    assert h.todos == [], "tasks were created without being asked"
    out = h.m.create_tasks(m["id"], [0])
    assert [c["index"] for c in out["created"]] == [0]
    assert len(h.todos) == 1
    t = h.todos[0]
    assert t["title"] == "Send the release notes"
    assert t["deadline"] == "2026-10-02"
    assert t["source"] == "meeting:" + m["id"]
    again = h.m.create_tasks(m["id"], [0, 1])
    assert [c["index"] for c in again["created"]] == [1]
    assert again["skipped"] == [0]
    assert h.todos[1]["deadline"] is None


def test_a_follow_up_only_becomes_an_approval_card(h):
    m = _record(h)
    d = h.m.follow_up_draft(m["id"], "thank_you")
    assert "Ship on Friday." in d["body"] and "Send the release notes" in d["body"]
    assert h.emails == []
    out = h.m.request_follow_up(m["id"], to="sam@example.com",
                                subject=d["subject"], body=d["body"])
    assert out == {"queued": True, "sent": False, "approval_id": "ap1"}
    assert len(h.emails) == 1
    assert h.emails[0]["requested_by"] == "meetings:follow_up"


def test_the_default_email_path_is_the_draft_email_card():
    import inspect
    src = inspect.getsource(mc._default_email_requester)
    assert "gmail_send.request_send" in src
    assert ".send(" not in src.replace("request_send(", "")


# ── helpers ─────────────────────────────────────────────────────────────────

def test_cut_point_lands_in_the_quiet_between_words():
    buf = tone(0.8) + silence(0.2) + tone(1.0)
    cut = mc.cut_point(buf, len(tone(1.5)))
    quiet_lo, quiet_hi = len(tone(0.8)), len(tone(0.8)) + len(silence(0.2))
    assert quiet_lo <= cut <= quiet_hi
    assert cut % 2 == 0


def test_tray_tooltip():
    assert mc.tray_tooltip({"recording": False}) is None
    assert mc.tray_tooltip({"recording": True, "elapsed_s": 3725}) == \
        "Friday — recording a meeting (1:02:05)"


def test_the_recorder_streams_instead_of_buffering_when_asked():
    from agent_friday.services.push_to_talk import WaveInRecorder, MAX_RECORD_S
    got = []
    rec = WaveInRecorder(on_chunk=got.append, max_seconds=7200)
    assert rec.max_seconds == 7200
    rec._keep(b"\x01\x02")
    assert got == [b"\x01\x02"] and rec.stop() == b""
    plain = WaveInRecorder()
    assert plain.max_seconds == MAX_RECORD_S
    plain._keep(b"\x03\x04")
    assert plain.stop() == b"\x03\x04"


def test_list_rows_carry_no_transcript(h):
    _record(h)
    rows = h.m.list()
    assert len(rows) == 1
    assert "segments" in rows[0] and isinstance(rows[0]["segments"], int)
    assert "ship on friday" not in json.dumps(rows)


def test_the_tray_tooltip_follows_the_recording(monkeypatch):
    tray = pytest.importorskip("agent_friday.friday_tray")
    t = tray.FridayTray.__new__(tray.FridayTray)

    class Icon:
        title = tray.TRAY_TITLE

    t.icon = Icon()
    t.running = True
    monkeypatch.setattr(t, "_meeting_status",
                        lambda: {"recording": True, "elapsed_s": 65}, raising=False)
    t._update_meeting_title()
    assert t.icon.title == "Friday — recording a meeting (0:01:05)"
    monkeypatch.setattr(t, "_meeting_status", lambda: {"recording": False},
                        raising=False)
    t._update_meeting_title()
    assert t.icon.title == tray.TRAY_TITLE
