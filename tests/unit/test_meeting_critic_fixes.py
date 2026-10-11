"""Meeting capture: the start is atomic, a delete wins over a finishing
transcript, notes never reach a cloud-tagged seat, unfinished meetings are
repaired, status never waits, the watchdog survives, the start card's token is
single use, the line said aloud is the line on screen, and one card covers one
meeting's transcript.

Every test drives a fake source with synthetic PCM and fake model calls. No
audio device is opened, no model is loaded and nothing leaves the process.
"""
from __future__ import annotations

import array
import json
import math
import re
import threading
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent_friday.services import meeting_capture as mc
from agent_friday.services import meeting_policy as mp

ROOT = Path(__file__).resolve().parents[2]


def tone(seconds: float, amp: int = 8000) -> bytes:
    n = int(mc.RATE * seconds)
    return array.array("h", (int(amp * math.sin(2 * math.pi * 440 * i / mc.RATE))
                             for i in range(n))).tobytes()


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class Src:
    kind = "microphone"

    def __init__(self, max_s=0, start_gate=None, start_error=None):
        self.error = ""
        self.on = None
        self.stopped = False
        self.started = threading.Event()
        self.gate = start_gate
        self.fail = start_error

    def start(self, on_pcm):
        self.on = on_pcm
        self.started.set()
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail is not None:
            raise self.fail

    def stop(self):
        self.stopped = True


class Rig:
    def __init__(self, tmp_path, monkeypatch, *, seat="local-test-model",
                 src_cls=Src, src_kw=None, **kw):
        monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
        self.root = tmp_path / "meetings"
        self.sources, self.released, self.notes_calls = [], [], []
        self.ledger, self.spoken = [], []
        self.seat = seat
        self.hold = None                 # an Event the transcriber waits on
        self.in_transcribe = threading.Event()

        def factory(max_s, **k):
            s = src_cls(max_s, **(src_kw or {}))
            self.sources.append(s)
            return s

        def transcriber(pcm):
            if self.hold is not None:
                self.in_transcribe.set()
                self.hold.wait(10)
            return "we agree to ship on friday"

        def notes_call(system, user, model):
            self.notes_calls.append(model)
            return {"notes": ["a point"], "decisions": [], "action_items": []}

        opts = dict(
            source_factory=factory, transcriber=transcriber,
            seat_resolver=lambda: self.seat, notes_call=notes_call,
            asr_check=lambda: "", lock_detector=lambda: None,
            event_lookup=lambda: None, notifier=lambda *a: None,
            scheduler=lambda mid: types.SimpleNamespace(
                release=lambda: self.released.append(mid)),
            partials_factory=lambda: None, speech_gate=lambda pcm: True,
            ledger=lambda kind, **f: self.ledger.append((kind, f)),
            retention=lambda: "", speaker=self.spoken.append,
            tick_s=0.01, chunk_s=1.0)
        opts.update(kw)
        self.m = mc.MeetingManager(**opts)

    def start(self, **kw):
        kw.setdefault("consent_ack", True)
        kw.setdefault("consent_version", mc.CONSENT_VERSION)
        kw.setdefault("confirm", True)
        return self.m.start(**kw)

    def finish(self, st, seconds=1.2):
        self.sources[-1].on(tone(seconds))
        self.m.stop()
        assert self.m.wait_idle(10)
        return st["id"]


@pytest.fixture
def make(tmp_path, monkeypatch):
    rigs = []

    def build(**kw):
        r = Rig(tmp_path, monkeypatch, **kw)
        rigs.append(r)
        return r
    yield build
    for r in rigs:
        if r.hold is not None:
            r.hold.set()
        for s in r.sources:
            if s.gate is not None:
                s.gate.set()
        try:
            r.m.stop()
        except mc.MeetingError:
            pass
        r.m.wait_idle(5)


# ── H1: a start is all or nothing ───────────────────────────────────────────

def test_a_failure_after_the_source_started_stops_it_and_leaves_nothing_half_started(make, monkeypatch):
    r = make()

    def boom(self, rec):
        raise RuntimeError("streaming ear failed to load")
    monkeypatch.setattr(mc.MeetingManager, "_start_partials", boom)

    with pytest.raises(mc.MeetingError) as e:
        r.start(title="Planning")
    assert e.value.code == "start_failed"
    src = r.sources[-1]
    assert src.stopped, "the source was left capturing"
    assert len(r.released) == 1, "the scheduler hold was not released"
    assert r.m.status()["recording"] is False
    with pytest.raises(mc.MeetingError) as e2:
        r.m.stop()
    assert e2.value.code == "not_recording"
    assert r.m.wait_idle(5)
    row = r.m.list()[0]
    assert row["state"] == "error", "a failed start was finished as a meeting"
    assert r.notes_calls == [], "notes were run for a recording that never started"
    assert r.m.get(row["id"])["stop_reason"] == "device_error"


def test_a_source_that_will_not_start_is_stopped_and_stays_an_error(make):
    r = make(src_kw={"start_error": OSError("no device")})
    with pytest.raises(mc.MeetingError) as e:
        r.start()
    assert e.value.code == "device_error"
    assert r.sources[-1].stopped
    assert r.m.wait_idle(5)
    assert r.m.list()[0]["state"] == "error"
    assert r.notes_calls == []


def test_a_save_that_fails_while_the_other_side_fails_to_open_does_not_orphan_the_capture(make, monkeypatch):
    r = make()
    real = mc._write_enc
    calls = []

    def flaky(path, data):
        calls.append(path.name)
        if path.name == "meeting.json.enc" and calls.count("meeting.json.enc") > 1:
            raise PermissionError("held by another program")
        return real(path, data)
    monkeypatch.setattr(mc, "_write_enc", flaky)

    st = r.start(system_audio=True)         # the fake source has no loopback
    assert st["recording"] is True, "a save error aborted a start that had already begun"
    assert r.m.status()["system_audio"]["captured"] is False
    r.m.stop()


def test_status_answers_and_shows_recording_while_the_source_is_still_opening(make):
    gate = threading.Event()
    r = make(src_kw={"start_gate": gate})
    out = []
    t = threading.Thread(target=lambda: out.append(r.start(title="Slow device")), daemon=True)
    t.start()
    try:
        assert wait_for(lambda: r.sources and r.sources[-1].started.is_set())
        got = []
        s = threading.Thread(target=lambda: got.append(r.m.status()), daemon=True)
        s.start()
        s.join(2.0)
        assert not s.is_alive(), "status waited behind start()"
        st = got[0]
        assert st["recording"] is True and st["starting"] is True
        assert st["capturing"] is False
    finally:
        gate.set()
        t.join(5)
    assert r.m.status()["starting"] is False


def test_a_stop_during_start_waits_for_the_source_to_open_and_then_stops_it(make):
    gate = threading.Event()
    r = make(src_kw={"start_gate": gate})
    t = threading.Thread(target=lambda: r.start(title="Slow device"), daemon=True)
    t.start()
    assert wait_for(lambda: r.sources and r.sources[-1].started.is_set())
    done = []
    p = threading.Thread(target=lambda: done.append(r.m.stop()), daemon=True)
    p.start()
    time.sleep(0.3)
    assert not r.sources[-1].stopped, "the source was stopped while it was still opening"
    gate.set()
    t.join(5)
    p.join(5)
    assert done and r.sources[-1].stopped
    assert r.m.status()["recording"] is False


# ── H2: a delete of the audio wins over a transcript that is finishing ──────

def test_deleting_the_audio_while_the_transcript_finishes_removes_it_for_good(make):
    r = make()
    r.hold = threading.Event()
    st = r.start(title="Planning", keep_audio=True)
    r.sources[-1].on(tone(2.2))
    r.m.stop()
    assert r.in_transcribe.wait(5), "the worker never reached the first part"
    adir = r.root / st["id"] / "audio"
    assert any(adir.glob("*.pcm.enc")), "the first part's audio was not kept"

    out = r.m.delete_audio(st["id"])
    assert out["audio_files"] >= 1 and out["has_audio"] is False
    r.hold.set()
    assert r.m.wait_idle(10)

    assert not adir.exists(), "the worker wrote audio again after the delete"
    assert not list((r.root / st["id"]).rglob("*.pcm.enc*"))
    rec = r.m.get(st["id"])
    assert rec["keep_audio"] is False and rec["audio_deleted_at"]
    assert rec["has_audio"] is False
    assert rec["summary"]["status"] == "done", "the transcript and notes are untouched"
    assert [(k, f["scope"]) for k, f in r.ledger] == [("meeting_deletion", "audio")]


def test_a_delete_that_could_not_finish_is_refused_and_not_counted(make, monkeypatch):
    r = make()
    st = r.start(keep_audio=True)
    r.finish(st)
    monkeypatch.setattr(mc, "_remove_tree", lambda p: False)
    with pytest.raises(mc.MeetingError) as e:
        r.m.delete_audio(st["id"])
    assert e.value.code == "delete_incomplete"
    assert r.ledger == []


def test_removing_a_tree_retries_a_held_file_and_reports_the_truth(tmp_path, monkeypatch):
    d = tmp_path / "audio"
    d.mkdir()
    (d / "a.pcm.enc").write_bytes(b"x")
    calls = []
    real = mc.shutil.rmtree

    def held_once(p):
        calls.append(p)
        if len(calls) == 1:
            raise PermissionError("held")
        return real(p)
    monkeypatch.setattr(mc.shutil, "rmtree", held_once)
    assert mc._remove_tree(d) is True and not d.exists() and len(calls) == 2
    monkeypatch.setattr(mc.shutil, "rmtree", lambda p: (_ for _ in ()).throw(PermissionError("held")))
    d.mkdir()
    assert mc._remove_tree(d) is False


# ── H3: notes never go to a cloud-tagged seat ───────────────────────────────

@pytest.mark.parametrize("seat", ["glm-4.6:cloud", "gpt-oss:120b-cloud", "kimi-k2:cloud"])
def test_notes_are_not_written_by_a_cloud_tagged_seat(make, seat):
    r = make(seat=seat)
    st = r.start()
    r.finish(st)
    assert r.notes_calls == [], "the transcript was sent to a cloud-tagged model"
    s = r.m.get(st["id"])["summary"]
    assert s["status"] == "skipped" and s["reason"] == mc.NO_LOCAL_SEAT_CLOUD


def test_a_local_seat_still_gets_the_notes(make):
    r = make(seat="qwen3:8b")
    st = r.start()
    r.finish(st)
    assert r.notes_calls == ["qwen3:8b"]
    assert r.m.get(st["id"])["summary"]["status"] == "done"


def test_the_default_notes_call_refuses_a_cloud_tag_before_any_call():
    raw = mc._default_notes_call.__wrapped__
    with pytest.raises(mc.MeetingError) as e:
        raw("system", "user", "gpt-oss:120b-cloud")
    assert e.value.code == "cloud_seat"


# ── M1: meetings a closed Friday left unfinished become interrupted ─────────

def _force_state(m, mid, state):
    rec = mc._load(mid)
    rec["state"] = state
    rec["ended_at"] = None if state == "recording" else rec["ended_at"]
    rec["summary"] = {"status": "pending"}
    mc._write_enc(mc._dir(mid) / "meeting.json.enc", json.dumps(rec).encode("utf-8"))


def test_unfinished_meetings_become_interrupted_and_retention_applies(make, monkeypatch):
    r = make()
    ids = []
    for state in ("recording", "transcribing"):
        st = r.start(keep_audio=True)
        mid = r.finish(st)
        _force_state(r.m, mid, state)
        ids.append(mid)
    done = r.start(keep_audio=True)
    done_id = r.finish(done)
    assert r.m.get(done_id)["state"] == "done"

    # The sweep skips a meeting still being made: before the repair, nothing goes.
    monkeypatch.setattr(mp, "text_retention", lambda: ("days", 1))
    monkeypatch.setattr(mp, "audio_retention", lambda: "")
    later = datetime.now() + timedelta(days=3)
    assert mp.sweep(r.m, now=later)["meetings"] == 1     # only the finished one

    assert r.m.repair_interrupted() == 2
    assert r.m.repair_interrupted() == 0
    rows = {x["id"]: x for x in r.m.list()}
    assert {rows[i]["state"] for i in ids} == {"interrupted"}
    assert all(rows[i]["stop_reason"] for i in ids)
    assert r.m.get(ids[0])["summary"]["status"] == "skipped"

    # The kept audio of an interrupted meeting is deletable, and retention applies.
    assert r.m.has_audio(ids[0])
    assert r.m.delete_audio(ids[0])["has_audio"] is False
    assert mp.sweep(r.m, now=later)["meetings"] == 2
    assert r.m.list() == []


def test_a_meeting_this_process_is_recording_is_not_repaired(make):
    r = make()
    r.start()
    assert r.m.repair_interrupted() == 0
    assert r.m.status()["recording"] is True


# ── M4: the watchdog survives a failing tick ────────────────────────────────

class FlakySrc(Src):
    reads = 0

    @property
    def error(self):
        FlakySrc.reads += 1
        if FlakySrc.reads <= 3:
            raise RuntimeError("driver hiccup")
        return "The microphone stopped working."

    @error.setter
    def error(self, v):
        pass


def test_the_watchdog_logs_a_failed_tick_and_still_stops_on_a_device_error(make):
    FlakySrc.reads = 0
    r = make(src_cls=FlakySrc)
    r.start()
    assert wait_for(lambda: not r.m.status()["recording"]), "the watchdog died on its first exception"
    assert r.m.status()["last_stop"]["reason"] == "device_error"
    assert FlakySrc.reads > 3


# ── M5: the card's token, and a consent version that was actually shown ─────

def test_a_start_without_the_consent_version_the_card_showed_is_refused(make):
    r = make()
    with pytest.raises(mc.MeetingError) as e:
        r.m.start(consent_ack=True, confirm=True)
    assert e.value.code == "consent_changed"
    with pytest.raises(mc.MeetingError) as e:
        r.m.start(consent_ack=True, confirm=True, consent_version="not a number")
    assert e.value.code == "consent_changed"
    assert r.sources == []


def test_the_start_cards_token_is_single_use_and_short_lived(make, monkeypatch):
    r = make()
    card = r.m.issue_start_card()
    assert card["consent_text"] == mc.CONSENT_TEXT and card["consent_version"] == mc.CONSENT_VERSION
    r.m.start(consent_ack=True, nonce=card["nonce"], consent_version=card["consent_version"])
    r.m.stop()
    r.m.wait_idle(5)
    for token in (card["nonce"], "made-up", None, True):
        with pytest.raises(mc.MeetingError) as e:
            r.m.start(consent_ack=True, nonce=token, consent_version=mc.CONSENT_VERSION)
        assert e.value.code == "confirm_required"
    monkeypatch.setattr(mc, "START_NONCE_TTL_S", 0.0)
    old = r.m.issue_start_card()
    with pytest.raises(mc.MeetingError) as e:
        r.m.start(consent_ack=True, nonce=old["nonce"], consent_version=mc.CONSENT_VERSION)
    assert e.value.code == "confirm_required"
    assert len(r.sources) == 1


def test_a_bad_duration_does_not_spend_the_token(make):
    r = make()
    card = r.m.issue_start_card()
    with pytest.raises(mc.MeetingError) as e:
        r.m.start(consent_ack=True, nonce=card["nonce"],
                  consent_version=mc.CONSENT_VERSION, max_duration_s=5)
    assert e.value.code == "bad_duration"
    r.m.start(consent_ack=True, nonce=card["nonce"], consent_version=mc.CONSENT_VERSION)


def test_the_outstanding_tokens_are_bounded(make):
    r = make()
    for _ in range(mc.START_NONCE_MAX * 3):
        r.m.issue_start_card()
    assert len(r.m._nonces) <= mc.START_NONCE_MAX


# ── M7: the line said aloud is the line on screen ───────────────────────────

def test_the_spoken_consent_line_is_the_one_on_the_card(make, monkeypatch):
    monkeypatch.setattr(mp, "_get_settings", lambda: {})
    r = make()
    r.start(speak_consent=True, consent_line="  My   edited   wording, not saved. ")
    assert wait_for(lambda: r.spoken)
    assert r.spoken == ["My edited wording, not saved."]


def test_without_an_edited_line_the_saved_line_is_said(make, monkeypatch):
    monkeypatch.setattr(mp, "_get_settings", lambda: {})
    r = make()
    r.start(speak_consent=True)
    assert wait_for(lambda: r.spoken)
    assert r.spoken == [mp.DEFAULT_CONSENT_LINE]


# ── L2 / L8: one card for a meeting's transcript, notes are not deferrable ──

class _Cards:
    def __init__(self):
        self.log = []

    def factory(self):
        log = self.log

        class VJ:
            def __init__(self, name, **kw):
                self.name, self.kw = name, kw

            def __enter__(self):
                log.append(("enter", self.name, self.kw))
                return types.SimpleNamespace(
                    step=lambda n, **k: log.append(("step", self.name, n)))

            def __exit__(self, *a):
                log.append(("exit", self.name))
                return False
        return VJ


def test_a_meeting_has_one_transcript_card_with_a_step_per_part_and_one_notes_card(make, monkeypatch):
    cards = _Cards()
    monkeypatch.setattr(mc, "visible_job", cards.factory())
    notes = []

    def default_transcriber(pcm):
        return "we agree to ship on friday"

    def default_notes(system, user, model):
        notes.append(model)
        return {"notes": ["a point"], "decisions": [], "action_items": []}
    monkeypatch.setattr(mc, "_default_transcriber", default_transcriber)
    monkeypatch.setattr(mc, "_default_notes_call", default_notes)
    monkeypatch.setattr(mc, "NOTES_CHARS_PER_CALL", 40)
    r = make(transcriber=default_transcriber, notes_call=default_notes)
    st = r.start()
    r.finish(st, seconds=3.2)

    enters = [e for e in cards.log if e[0] == "enter"]
    names = [e[1] for e in enters]
    assert names.count("Meeting transcript") == 1, "a card per part, not per meeting"
    assert names.count("Meeting notes") == 1
    steps = [e for e in cards.log if e[0] == "step" and e[1] == "Meeting transcript"]
    assert len(steps) >= 3
    assert len(notes) >= 2, "the notes were split into several calls under one card"
    tr = next(e for e in enters if e[1] == "Meeting transcript")
    nt = next(e for e in enters if e[1] == "Meeting notes")
    assert tr[2]["deferrable"] is False and tr[2]["queued"] is False
    assert nt[2]["deferrable"] is False and nt[2]["queued"] is True


def test_notes_are_owner_work_not_deferrable_background():
    src = (ROOT / "src" / "agent_friday" / "services" / "meeting_capture.py").read_text(encoding="utf-8")
    assert re.search(r"@visible_job\('Meeting notes'[^)]*deferrable=False\)", src)
    assert 'local_scheduler.work("Meeting notes", cls="user_job", deferrable=False)' in src


# ── M3: the tray shows a recording state, not only a tooltip ────────────────

def test_the_tray_icon_changes_while_a_meeting_records(monkeypatch):
    tray = pytest.importorskip("agent_friday.friday_tray")
    from PIL import Image
    base = Image.new("RGBA", (64, 64), (10, 20, 30, 255))
    rec = tray.recording_icon(base)
    assert rec.size == base.size
    assert rec.getpixel((5, 5)) == base.getpixel((5, 5))
    r, g, b, _a = rec.getpixel((56, 56))
    assert r > 180 and g < 80 and b < 80, "no red dot in the corner"
    assert base.getpixel((56, 56)) == (10, 20, 30, 255), "the base image was altered"

    t = tray.FridayTray.__new__(tray.FridayTray)
    t.icon = types.SimpleNamespace(title=tray.TRAY_TITLE, icon=base)
    t._base_image, t._rec_image, t._icon_recording = base, None, False
    t.running = True
    monkeypatch.setattr(t, "_local_queue", lambda: {}, raising=False)
    monkeypatch.setattr(t, "_meeting_status",
                        lambda: {"recording": True, "elapsed_s": 5}, raising=False)
    t._update_meeting_title()
    assert t.icon.icon is not base and t.icon.icon.getpixel((56, 56))[0] > 180
    monkeypatch.setattr(t, "_meeting_status", lambda: {"recording": False}, raising=False)
    t._update_meeting_title()
    assert t.icon.icon is base
