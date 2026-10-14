"""Consent, retention and suggestions for the meeting recorder (MT-4).

Settings, the work queue and the clock are fakes or temp folders. No audio
device is opened and nothing leaves the process.
"""
from __future__ import annotations

import array
import math
import re
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent_friday.services import meeting_capture as mc
from agent_friday.services import meeting_policy as mp
from agent_friday.services import work_queue as wq

ROOT = Path(__file__).resolve().parents[2]


def tone(seconds, amp=8000):
    n = int(mc.RATE * seconds)
    return array.array("h", (int(amp * math.sin(2 * math.pi * 440 * i / mc.RATE))
                             for i in range(n))).tobytes()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Settings in a dict, the policy state and the work queue in a temp folder."""
    cfg = {}
    monkeypatch.setattr(mp, "_get_settings", lambda: dict(cfg))
    monkeypatch.setattr(mp, "_put_settings", lambda delta: cfg.update(delta))
    monkeypatch.setattr(mp, "_state_path", lambda: tmp_path / "meetings" / "policy_state.json")
    monkeypatch.setattr(wq, "queue_dir", lambda: tmp_path / "work_queue")
    monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
    return cfg


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
def mgr(store):
    ledger = []
    m = mc.MeetingManager(
        source_factory=lambda max_s, **k: Src(max_s), transcriber=lambda pcm: "we agreed",
        seat_resolver=lambda: "local-model",
        notes_call=lambda s, u, model: {"notes": ["a point"], "decisions": [], "action_items": []},
        asr_check=lambda: "", lock_detector=lambda: None, event_lookup=lambda: None,
        notifier=lambda *a: None, scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
        partials_factory=lambda: None, speech_gate=lambda pcm: True,
        ledger=lambda kind, **f: ledger.append((kind, f)),
        retention=lambda: mp.audio_retention(), tick_s=0.01, chunk_s=1.0)
    m.ledger_lines = ledger
    yield m
    try:
        m.stop()
    except mc.MeetingError:
        pass
    m.wait_idle(5)


def record(m, keep_audio=True, notes=True):
    """One finished meeting. Returns its id."""
    if not notes:
        m.seat_resolver = lambda: None
    st = m.start(consent_ack=True, confirm=True, consent_version=mc.CONSENT_VERSION, title="Planning", keep_audio=keep_audio)
    m._active.source.on(tone(0.9))
    m.stop()
    assert m.wait_idle(5)
    return st["id"]


def age(m, mid, days):
    rec = mc._load(mid)
    when = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    rec["started_at"] = rec["ended_at"] = when
    m._save(rec)


# ── consent line and jurisdiction note ──────────────────────────────────────

def test_the_consent_line_has_a_plain_default_and_is_editable(store):
    c = mp.consent()
    assert c["line"] == mp.DEFAULT_CONSENT_LINE and c["edited"] is False
    mp.set_consent(line="  Quick note:   I'm recording   this for my notes. ")
    c = mp.consent()
    assert c["line"] == "Quick note: I'm recording this for my notes." and c["edited"] is True
    assert c["default_line"] == mp.DEFAULT_CONSENT_LINE


def test_putting_the_default_wording_back_stores_nothing(store):
    mp.set_consent(line="mine")
    mp.set_consent(line=mp.DEFAULT_CONSENT_LINE)
    assert store["meeting_consent_line"] == ""


def test_the_consent_line_is_capped(store):
    mp.set_consent(line="x" * 5000)
    assert len(mp.consent()["line"]) == mp.CONSENT_LINE_MAX


def test_the_jurisdiction_note_says_some_places_need_everyones_consent_and_defaults_to_ask_everyone(store):
    c = mp.consent()
    assert "everyone's consent" in c["jurisdiction_note"]
    assert "can't decide the law" in c["jurisdiction_note"]
    assert c["mode"] == "ask_everyone"
    assert mp.set_consent(mode="owner_decides")["mode"] == "owner_decides"
    with pytest.raises(ValueError):
        mp.set_consent(mode="never_ask")


def test_the_spoken_line_is_off_unless_the_owner_turns_it_on(store):
    said = []
    assert mp.speak_line_if_asked(said.append) is False and said == []
    mp.set_consent(speak=True, line="Recording for notes.")
    assert mp.speak_line_if_asked(said.append) is True and said == ["Recording for notes."]


def test_a_voice_that_fails_does_not_stop_a_recording(store):
    mp.set_consent(speak=True)

    def broken(text):
        raise RuntimeError("no output device")
    assert mp.speak_line_if_asked(broken) is False


def test_starting_with_the_spoken_line_says_it_through_the_speaker_hook(store):
    said = []
    m = mc.MeetingManager(
        source_factory=lambda max_s, **k: Src(max_s), transcriber=lambda pcm: "",
        asr_check=lambda: "", lock_detector=lambda: None, event_lookup=lambda: None,
        notifier=lambda *a: None, scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
        partials_factory=lambda: None, ledger=lambda *a, **k: None, retention=lambda: "",
        speaker=said.append, tick_s=0.01)
    try:
        m.start(consent_ack=True, confirm=True, consent_version=mc.CONSENT_VERSION, speak_consent=True)
        deadline = time.monotonic() + 3
        while not said and time.monotonic() < deadline:
            time.sleep(0.01)
        assert said == [mp.DEFAULT_CONSENT_LINE]
    finally:
        m.stop()
        m.wait_idle(5)


def test_not_asking_for_the_spoken_line_says_nothing(store):
    said = []
    m = mc.MeetingManager(
        source_factory=lambda max_s, **k: Src(max_s), transcriber=lambda pcm: "",
        asr_check=lambda: "", lock_detector=lambda: None, event_lookup=lambda: None,
        notifier=lambda *a: None, scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
        partials_factory=lambda: None, ledger=lambda *a, **k: None, retention=lambda: "",
        speaker=said.append, tick_s=0.01)
    try:
        m.start(consent_ack=True, confirm=True, consent_version=mc.CONSENT_VERSION)
        time.sleep(0.1)
        assert said == []
    finally:
        m.stop()
        m.wait_idle(5)


# ── retention is offered, never preselected ─────────────────────────────────

def test_nothing_is_preselected_and_the_recommended_label_selects_nothing(store):
    r = mp.retention()
    assert r["audio"]["selected"] == "" and r["text"]["selected"] == ""
    rec = [o["value"] for o in r["audio"]["options"] if o["recommended"]]
    assert rec == ["delete_after_notes"]
    assert [o["value"] for o in r["audio"]["options"]] == ["delete_after_notes", "30_days", "keep"]
    assert not any(o["recommended"] for o in r["text"]["options"])
    assert mp.audio_retention() == "" and mp.text_retention() == ("", 0)


def test_a_choice_is_stored_as_made(store):
    mp.set_retention(audio="30_days", text="days", days=45)
    assert mp.audio_retention() == "30_days" and mp.text_retention() == ("days", 45)
    assert mp.retention()["audio"]["selected"] == "30_days"


@pytest.mark.parametrize("kw", [{"audio": "forever"}, {"text": "shred"}, {"days": 0},
                                {"days": 99999}, {"days": "soon"}])
def test_a_choice_that_is_not_offered_is_refused(store, kw):
    with pytest.raises((ValueError, TypeError)):
        mp.set_retention(**kw)


def test_the_settings_and_docs_declare_every_meeting_key():
    from agent_friday import core
    docs = (ROOT / "docs" / "user-guide" / "configuration.md").read_text(encoding="utf-8")
    for key in mp.SETTING_KEYS:
        assert key in core.DEFAULT_SETTINGS, key
        assert "`%s`" % key in docs, key
    d = core.DEFAULT_SETTINGS
    assert d["meeting_audio_retention"] == "" and d["meeting_text_retention"] == ""
    assert d["meeting_consent_mode"] == "ask_everyone"
    assert d["meeting_speak_consent"] is False and d["meeting_suggestions"] is True


# ── deleting audio is separate from deleting the notes ──────────────────────

def test_deleting_the_audio_leaves_the_transcript_and_notes(store, mgr):
    mid = record(mgr)
    assert mgr.get(mid)["has_audio"] is True
    out = mgr.delete_audio(mid)
    assert out["audio_files"] >= 1 and out["has_audio"] is False
    got = mgr.get(mid)
    assert got["has_audio"] is False and got["keep_audio"] is False
    assert got["summary"]["status"] == "done" and got["summary"]["notes"] == ["a point"]
    assert any(g["kind"] == "speech" for g in got["segments"])
    assert not (Path(mc._root()) / mid / "audio").exists(), "the files are gone, not hidden"
    with pytest.raises(mc.MeetingError) as e:
        mgr.audio_wav(mid)
    assert e.value.code == "no_audio"


def test_deleting_a_meeting_deletes_everything(store, mgr):
    mid = record(mgr)
    mgr.delete(mid)
    assert not (Path(mc._root()) / mid).exists()
    assert mgr.list() == []


def test_the_ledger_gets_counts_only(store, mgr):
    mid = record(mgr)
    mgr.delete_audio(mid)
    mgr.delete(mid)
    kinds = [k for k, _ in mgr.ledger_lines]
    assert kinds == ["meeting_deletion", "meeting_deletion"]
    for _, fields in mgr.ledger_lines:
        assert set(fields) == {"scope", "reason", "audio_files", "meetings"}
        assert all(isinstance(fields[k], (int, str)) for k in fields)
        assert "Planning" not in str(fields) and mid not in str(fields)


def test_the_real_ledger_keeps_counts_and_drops_everything_else(tmp_path, monkeypatch):
    from agent_friday.services import activity_ledger as al
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "ledger.jsonl")
    assert al.record("meeting_deletion", scope="audio", reason="owner", audio_files=3,
                     meetings=0, title="Planning", path="C:/x", text="we agreed")
    line = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert '"audio_files": 3' in line
    assert "Planning" not in line and "C:/x" not in line and "we agreed" not in line


def test_a_recording_meeting_cannot_be_deleted_or_have_its_audio_deleted(store, mgr):
    st = mgr.start(consent_ack=True, confirm=True, consent_version=mc.CONSENT_VERSION, keep_audio=True)
    for fn in (mgr.delete, mgr.delete_audio):
        with pytest.raises(mc.MeetingError) as e:
            fn(st["id"])
        assert e.value.code == "recording"


# ── retention: "delete once the notes are made" ─────────────────────────────

def test_audio_goes_when_the_notes_are_made_if_the_owner_chose_that(store, mgr):
    mp.set_retention(audio="delete_after_notes")
    mid = record(mgr)
    got = mgr.get(mid)
    assert got["summary"]["status"] == "done" and got["has_audio"] is False
    assert got["summary"]["notes"], "the notes stay"


def test_audio_stays_when_no_choice_was_made(store, mgr):
    mid = record(mgr)
    assert mgr.get(mid)["has_audio"] is True


def test_audio_stays_if_the_notes_could_not_be_made(store, mgr):
    mp.set_retention(audio="delete_after_notes")
    mid = record(mgr, notes=False)
    got = mgr.get(mid)
    assert got["summary"]["status"] == "skipped" and got["has_audio"] is True


@pytest.mark.parametrize("choice", ["keep", "30_days"])
def test_other_choices_do_not_delete_at_notes_time(store, mgr, choice):
    mp.set_retention(audio=choice)
    mid = record(mgr)
    assert mgr.get(mid)["has_audio"] is True


# ── the sweep ───────────────────────────────────────────────────────────────

def test_the_sweep_does_nothing_until_the_owner_chooses(store, mgr):
    mid = record(mgr)
    age(mgr, mid, 400)
    assert mp.sweep(mgr) == {"audio_files": 0, "meetings": 0}
    assert mgr.get(mid)["has_audio"] is True


def test_30_days_deletes_old_audio_only(store, mgr):
    mp.set_retention(audio="30_days")
    old, new = record(mgr), record(mgr)
    age(mgr, old, 40)
    age(mgr, new, 10)
    out = mp.sweep(mgr)
    assert out["audio_files"] >= 1 and out["meetings"] == 0
    assert mgr.get(old)["has_audio"] is False and mgr.get(old)["summary"]["notes"]
    assert mgr.get(new)["has_audio"] is True


def test_delete_after_notes_catches_audio_left_behind(store, mgr):
    mid = record(mgr)                      # recorded before the owner chose
    mp.set_retention(audio="delete_after_notes")
    out = mp.sweep(mgr)
    assert out["audio_files"] >= 1 and mgr.get(mid)["has_audio"] is False


def test_text_after_n_days_deletes_the_whole_old_meeting(store, mgr):
    mp.set_retention(text="days", days=30)
    old, new = record(mgr), record(mgr)
    age(mgr, old, 40)
    age(mgr, new, 10)
    out = mp.sweep(mgr)
    assert out["meetings"] == 1
    ids = [r["id"] for r in mgr.list()]
    assert ids == [new]
    assert not (Path(mc._root()) / old).exists()


def test_keeping_text_deletes_nothing(store, mgr):
    mp.set_retention(text="keep")
    mid = record(mgr)
    age(mgr, mid, 4000)
    assert mp.sweep(mgr)["meetings"] == 0


def test_a_meeting_in_progress_is_never_swept(store, mgr):
    mp.set_retention(audio="30_days", text="days", days=1)
    st = mgr.start(consent_ack=True, confirm=True, consent_version=mc.CONSENT_VERSION, keep_audio=True)
    assert mp.sweep(mgr) == {"audio_files": 0, "meetings": 0}
    assert mgr.status()["id"] == st["id"]


# ── the sweep runs through the work queue ───────────────────────────────────

def test_nothing_is_queued_while_no_choice_has_been_made(store):
    assert mp.queue_retention() is None and wq.all_items() == []


def test_the_sweep_is_queued_once_and_only_when_due(store):
    mp.set_retention(audio="keep")
    first = mp.queue_retention()
    assert first and first["cls"] == "background" and first["disposition"] == "when_away"
    assert mp.queue_retention() is None, "one waiting item is enough"
    wq.update(first["id"], status="done", finished_at=time.time())
    assert mp.queue_retention() is None, "it ran a few hours ago"
    assert mp.queue_retention(now=time.time() + mp.RETENTION_EVERY_S + 5)


def test_the_queued_sweep_runs_when_the_owner_is_away_and_records_its_counts(store, mgr, monkeypatch):
    mp.set_retention(audio="30_days")
    mid = record(mgr)
    age(mgr, mid, 40)
    item = mp.queue_retention()
    monkeypatch.setattr(wq, "is_away", lambda *a, **k: False)
    assert mp.run_retention(mgr)["ran"] == 0
    assert wq.get(item["id"])["status"] == "queued"
    monkeypatch.setattr(wq, "is_away", lambda *a, **k: True)
    out = mp.run_retention(mgr)
    done = wq.get(item["id"])
    assert out["ran"] == 1 and done["status"] == "done"
    assert done["result"]["audio_files"] >= 1 and mgr.get(mid)["has_audio"] is False


def test_the_sweep_waits_while_a_meeting_is_recording(store, mgr, monkeypatch):
    from agent_friday.services import turn_queue as tq
    mp.set_retention(audio="30_days")
    item = mp.queue_retention()
    monkeypatch.setattr(wq, "is_away", lambda *a, **k: True)
    tq.request_background_pause("meeting:test", "you are in a meeting")
    try:
        assert mp.run_retention(mgr) == {"ran": 0, "why": "you are in a meeting"}
    finally:
        tq.clear_background_pause("meeting:test")
    assert wq.get(item["id"])["status"] == "queued"


def test_a_sweep_that_fails_is_recorded_as_failed_not_lost(store, monkeypatch):
    mp.set_retention(audio="keep")
    item = mp.queue_retention()
    monkeypatch.setattr(wq, "is_away", lambda *a, **k: True)

    class Broken:
        def list(self):
            raise RuntimeError("disk gone")
    mp.run_retention(Broken())
    got = wq.get(item["id"])
    assert got["status"] == "failed" and "disk gone" in got["error"]


def test_other_background_items_in_the_queue_are_left_alone(store, mgr, monkeypatch):
    mp.set_retention(audio="keep")
    other = wq.enqueue("Something else", "{}", cls="background", disposition="when_away")
    mp.queue_retention()
    monkeypatch.setattr(wq, "is_away", lambda *a, **k: True)
    mp.run_retention(mgr)
    assert wq.get(other["id"])["status"] == "queued"


def test_the_scheduler_tick_runs_the_retention_tick():
    src = (ROOT / "src" / "agent_friday" / "services" / "scheduler.py").read_text(encoding="utf-8")
    tick = src[src.index("def _tick():"):src.index("def _meeting_retention_tick")]
    assert "_meeting_retention_tick()" in tick
    assert "meeting_policy.retention_tick()" in src


# ── "take notes?" suggestions: No Pestering ─────────────────────────────────

EV = {"title": "Design review", "event_id": "ev-1"}


def show(event, today="2026-10-12"):
    """What the page does: ask for the card, and report it once it is on screen."""
    card = mp.suggest(event, today=today)
    if card:
        mp.mark_shown(card["key"])
    return card


def test_a_suggestion_appears_once_per_event(store):
    first = show(EV)
    assert first and "Design review" in first["text"] and first["text"].endswith("Take notes?")
    assert mp.suggest(EV, today="2026-10-12") is None
    assert mp.suggest(dict(EV, event_id="ev-2", title="Standup"), today="2026-10-12")


def test_asking_for_the_card_does_not_spend_it_only_seeing_it_does(store):
    a = mp.suggest(EV, today="2026-10-12")
    b = mp.suggest(EV, today="2026-10-12")          # a page that loaded but was never looked at
    assert a and a == b
    assert mp.answer_suggestion("shown", a["key"])["enabled"] is True
    assert mp.suggest(EV, today="2026-10-12") is None


def test_only_a_real_suggestion_key_can_be_marked_shown(store):
    for bad in (None, "", "ev-1", "zz" * 8, "0" * 15):
        with pytest.raises(ValueError):
            mp.mark_shown(bad)


def test_declining_with_a_key_also_counts_the_card_as_seen(store):
    card = mp.suggest(EV, today="2026-10-12")
    mp.answer_suggestion("decline", card["key"])
    assert mp.suggest(EV, today="2026-10-12") is None


def test_an_event_without_an_id_is_keyed_by_title_and_day(store):
    e = {"title": "Lunch call", "event_id": ""}
    assert show(e)
    assert mp.suggest(e, today="2026-10-12") is None
    assert mp.suggest(e, today="2026-10-13")


def test_no_suggestion_while_recording_and_the_event_is_not_used_up(store):
    assert mp.suggest(EV, recording=True) is None
    assert show(EV)


def test_two_declines_switch_suggestions_off(store):
    assert mp.answer_suggestion("decline")["enabled"] is True
    st = mp.answer_suggestion("decline")
    assert st["enabled"] is False and st["off_after_declines"] is True
    assert mp.suggest(dict(EV, event_id="ev-9"), today="2026-10-12") is None


def test_accepting_is_not_a_decline_and_starts_nothing(store, monkeypatch):
    def no_manager():
        raise AssertionError("a suggestion must never reach the recorder")
    monkeypatch.setattr(mc, "get_manager", no_manager)
    for _ in range(3):
        st = mp.answer_suggestion("accept")
    assert st["declines"] == 0 and st["enabled"] is True


def test_the_owner_can_switch_suggestions_off_or_back_on(store):
    store["meeting_suggestions"] = False
    assert mp.suggest(EV, today="2026-10-12") is None
    mp.answer_suggestion("decline")
    mp.answer_suggestion("decline")
    st = mp.reset_suggestions()
    assert st["enabled"] is True and st["declines"] == 0 and store["meeting_suggestions"] is True


def test_an_answer_that_is_not_one_is_refused(store):
    with pytest.raises(ValueError):
        mp.answer_suggestion("start")


def test_the_suggestion_state_holds_hashes_not_event_names(store, tmp_path):
    show(EV)
    raw = (tmp_path / "meetings" / "policy_state.json").read_text(encoding="utf-8")
    assert "Design review" not in raw and "ev-1" not in raw
    assert re.search(r'"offered": \["[0-9a-f]{16}"\]', raw)
