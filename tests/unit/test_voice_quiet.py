"""A remote voice turn is quiet, honest, speakable and fast (Stephen, 2026-11-14, talking to the paired tablet).

His screen filled with three red "Failed - Transcribing audio - faster-whisper base.en" orbs, two "TV dictation"
orbs (on a tablet) and a toast per helper ("... $0.0000"); the answer had asterisks and hashes read aloud; and the
turn ran on the 27B model with medium reasoning. The promises guarded here:

* the red base.en orbs were the background media sweep failing on SILENT video clips (PyAV: IndexError, no audio
  stream), not the tablet's audio: a file with no audio track is a quiet skip, not a failed job;
* a spoken sentence is one activity named for the paired device's kind ("Tablet voice"), a recogniser that misses
  and is recovered by the next shows nothing, and only a final failure shows, once, in plain words;
* no toast for a routine helper finishing, and never "$0.0000";
* the reply has a speech version (no markdown) beside the formatted one; on a voice turn the stream's text is the
  speech version and the formatted copy rides along for the screen;
* a voice turn whose device picked no model and whose chat has no seat gets the fastest listed cloud model,
  labelled as a recommendation; a device's own pick, or an existing seat, is never overridden;
* progress is said in plain words ("Checking your calendar..."), never a tool name.
"""
import json
import re
import sys
import time
from pathlib import Path

import pytest

from agent_friday.services import device_pairing, media_transcripts as mt, visible_jobs
from agent_friday.tv_gateway import owner_turn, speech, voice_reply
from tests.unit.test_owner_turn_stream import _clean, ask, client, events, phone, wire  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[2]


# ── speech-safe text ─────────────────────────────────────────────────────────

MARKDOWN = ("## Your day\n\n**Morning**\n- Call [Sam](http://example.com/a) at *9*\n- Review `code` now\n\n"
            "| who | when |\n|---|---|\n| Sam | 9 |\n\n1. First thing\n> quoted\n---\n```py\nprint(1)\n```\nSee https://x.io/y")


def test_nothing_in_the_speech_version_is_markdown():
    t = voice_reply.speech_text(MARKDOWN)
    for bad in ("*", "#", "|", "`", "](", "http", "---", "- ", ">"):
        assert bad not in t, (bad, t)
    for good in ("Your day", "Morning", "Call Sam at 9.", "Review code now.", "who, when", "Sam, 9", "First thing."):
        assert good in t, (good, t)


def test_plain_words_are_untouched():
    said = "Jere Jay is your father-in-law, born in 1950."
    assert voice_reply.speech_text(said) == said


def test_a_word_with_an_inner_underscore_keeps_it():
    assert voice_reply.speech_text("the my_file name") == "the my_file name"


# ── progress in plain words ──────────────────────────────────────────────────

@pytest.mark.parametrize("tool,words", [({"name": "get_calendar_events"}, "Checking your calendar…"),
                                        ("gmail_search", "Reading your mail…"),
                                        ({"label": "Searching the wiki"}, "Looking in your notes…"),
                                        ("zzz_unknown_tool", "Working on it…")])
def test_progress_is_plain_words(tool, words):
    assert voice_reply.progress_words(tool) == words


# ── the silent-clip failure ──────────────────────────────────────────────────

class _Av:
    def __init__(self, n=None, boom=None):
        self.n, self.boom = n, boom

    def open(self, path):
        if self.boom:
            raise self.boom
        outer = self

        class C:
            streams = type("S", (), {"audio": [object()] * outer.n})()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return C()


def test_a_video_with_no_audio_stream_is_not_an_audio_file(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "av", _Av(0))
    assert mt.has_audio_track(tmp_path / "clip.mp4") is False
    monkeypatch.setitem(sys.modules, "av", _Av(1))
    assert mt.has_audio_track(tmp_path / "clip.mp4") is True


def test_a_file_the_container_reader_cannot_open_is_left_to_the_recogniser(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "av", _Av(boom=OSError("bad")))
    assert mt.has_audio_track(tmp_path / "x.mp4") is True            # never hides a real failure


def test_a_silent_clip_is_skipped_without_a_job_or_a_failure(monkeypatch, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(mt, "_path_for", lambda c: f)
    monkeypatch.setattr(mt, "_json_path", lambda c: tmp_path / "t.json")
    monkeypatch.setattr(mt, "has_audio_track", lambda p: False)
    monkeypatch.setattr(mt, "transcriber", None)
    from agent_friday.services import feature_models as fm
    monkeypatch.setattr(fm, "require", lambda *a, **k: None)
    monkeypatch.setattr(mt, "_run_whisper", lambda p: pytest.fail("the recogniser must not be asked about a silent clip"))
    rec = mt.transcribe({"id": "creation:1", "kind": "video"})
    assert rec["engine"] == "skipped" and rec["note"] == "no audio track" and rec["text"] == ""
    assert (tmp_path / "t.json").exists()                              # cached: not retried on the next sweep


# ── one quiet activity, named for the device ─────────────────────────────────

ONE_SECOND = b"\x00\x01" * 16000


class _Asr:
    def __init__(self, text="hello there", fail=None):
        self.text, self.fail, self._device, self.last_confidence = text, fail, "cpu", 0.9

    def transcribe(self, pcm, language=None, initial_prompt=None):
        if self.fail:
            raise self.fail
        return self.text


@pytest.fixture
def jobs(monkeypatch):
    seen = []
    real = visible_jobs.visible_job

    def spy(name, **kw):
        if kw.get("kind") == "tv_speech":                  # other background jobs may start in a busy test process
            seen.append({"name": name, "label": kw.get("label")})
        return real(name, **kw)
    monkeypatch.setattr(visible_jobs, "visible_job", spy)
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    speech._HEARD.clear()
    yield seen
    device_pairing._reset_for_tests()


def _dev(kind):
    from tests.unit.test_tv_gateway import FakeTv
    k = FakeTv()
    device_pairing.add_device(k.spki, name="A device", kind=kind)
    return device_pairing.get_device(k.id)


@pytest.mark.parametrize("kind,name", [("tablet", "Tablet voice"), ("phone", "Phone voice"), ("tv", "TV voice")])
def test_the_activity_is_named_for_the_devices_kind_never_tv_dictation(monkeypatch, jobs, kind, name):
    monkeypatch.setattr(speech, "_ASR", _Asr())
    speech.transcribe(_dev(kind), ONE_SECOND, principal="grownup")
    assert [j["name"] for j in jobs] == [name]
    assert "dictation" not in json.dumps(jobs).lower()


def test_a_miss_that_the_next_recogniser_recovers_shows_nothing_and_is_one_activity(monkeypatch, jobs):
    monkeypatch.setattr(speech, "_ASR", _Asr(fail=RuntimeError("cuda out of memory")))
    monkeypatch.setitem(speech._ASRS, "small", _Asr(text="open the news"))
    out = speech.transcribe(_dev("tablet"), ONE_SECOND, principal="grownup")
    assert out["text"] == "open the news" and out["model"] == "small"
    assert len(jobs) == 1                                               # one orb, not one per attempt


def test_when_nothing_can_hear_it_fails_once_in_plain_words(monkeypatch, jobs):
    monkeypatch.setattr(speech, "_ASR", _Asr(fail=RuntimeError("x")))
    monkeypatch.setitem(speech._ASRS, "small", _Asr(fail=RuntimeError("y")))
    with pytest.raises(speech.SpeechRefused) as e:
        speech.transcribe(_dev("tablet"), ONE_SECOND, principal="grownup")
    assert e.value.status == 503 and "RuntimeError" not in str(e.value) and "try again" in str(e.value)
    assert len(jobs) == 1


def test_a_device_that_just_sent_audio_is_in_a_voice_exchange(monkeypatch, jobs):
    monkeypatch.setattr(speech, "_ASR", _Asr())
    dev = _dev("tablet")
    assert not speech.recently_heard(dev["id"])
    speech.transcribe(dev, ONE_SECOND, principal="grownup")
    assert speech.recently_heard(dev["id"]) and not speech.recently_heard(dev["id"], within_s=-1)


# ── the reply: formatted for the screen, speech-safe for the voice ───────────

def _done(text):
    return {"done": True, "payload": {"response": text, "model": "bonsai2:27b", "seat": "local"}}


def _stream(r):
    ev = events(r)
    text = "".join(e["delta"]["text"] for e in ev if e.get("type") == "content_block_delta")
    return text, next(e for e in ev if e.get("type") == "message_delta")["friday"]


def test_a_text_turn_keeps_its_formatting_and_still_carries_a_speech_version(client, wire):
    wire([_done("## Today\n- **Call** Sam")])
    text, truth = _stream(ask(client, phone()))
    assert text == "## Today\n- **Call** Sam"
    assert "*" not in truth["speech"] and "#" not in truth["speech"] and truth["voice"] is False


def test_a_voice_turn_streams_the_speech_version_and_carries_the_formatted_one(client, wire):
    wire([_done("## Today\n- **Call** Sam")])
    text, truth = _stream(ask(client, phone(), voice=True))
    assert text == truth["speech"] and "*" not in text and "#" not in text
    assert truth["formatted"] == "## Today\n- **Call** Sam" and truth["voice"] is True


def test_a_device_that_just_dictated_gets_a_speech_safe_reply_without_saying_so(client, wire):
    wire([_done("**Bold** answer")])
    dev = phone()
    speech._HEARD[dev.id] = time.time()
    text, truth = _stream(ask(client, dev))
    assert text == "Bold answer" and truth["voice"] is True


def test_the_first_ping_says_what_is_happening_in_words(client, wire):
    wire([{"started": True}, {"tool": {"name": "get_calendar_events"}}, _done("ok")])
    ev = events(ask(client, phone()))
    pings = [e["friday"] for e in ev if e["type"] == "ping"]
    assert pings[0]["detail"] == "Thinking it over…"
    assert any(p["status"] == "working" and p["detail"] == "Checking your calendar…" for p in pings)
    assert "Listening" not in json.dumps(ev)


# ── the fast default for a voice turn ────────────────────────────────────────

ROWS = [{"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5", "available": True},
        {"id": "bonsai2:27b", "name": "Bonsai", "available": True}]


class _Resp:
    def __init__(self, conv):
        self.conv = conv

    def get_json(self, silent=True):
        return {"conversation": self.conv}


@pytest.fixture
def fast(monkeypatch):
    """Records the seats bound; the PC lists Haiku; no conversation has a seat yet."""
    bound = []
    monkeypatch.setattr(owner_turn, "_slate", lambda dev: ROWS)
    monkeypatch.setattr(owner_turn, "_api", lambda *a, **k: _Resp({}))
    monkeypatch.setattr(owner_turn, "bind_seat", lambda cid, m: (bound.append(m), cid or "conv-1")[1])
    return bound


def test_a_voice_turn_with_no_pick_gets_the_fast_model_labelled_as_a_recommendation(fast):
    cid, default = owner_turn.fast_voice_default(None)
    assert fast == ["claude-haiku-5-5"] and cid == "conv-1"
    assert default["recommended"] is True and default["label"] == "Claude Haiku 5.5" and "pick another" in default["why"]


def test_an_existing_seat_is_never_overridden(fast, monkeypatch):
    monkeypatch.setattr(owner_turn, "_api", lambda *a, **k: _Resp({"seat": {"model": "bonsai2:27b"}}))
    assert owner_turn.fast_voice_default("c1") == ("c1", None) and fast == []


def test_no_fast_model_listed_leaves_the_chat_alone(fast, monkeypatch):
    monkeypatch.setattr(owner_turn, "_slate", lambda dev: [ROWS[1]])
    assert owner_turn.fast_voice_default(None) == (None, None) and fast == []


def test_a_seat_the_pc_refuses_leaves_the_chat_alone(fast, monkeypatch):
    from agent_friday.tv_gateway import turn

    def refuse(cid, m):
        raise turn.TurnRefused(409, "no")
    monkeypatch.setattr(owner_turn, "bind_seat", refuse)
    assert owner_turn.fast_voice_default(None) == (None, None)


def test_a_device_pick_is_honoured_and_the_default_is_not_applied(client, wire, monkeypatch):
    monkeypatch.setattr(owner_turn, "_slate", lambda dev: ROWS)
    bound = []
    monkeypatch.setattr(owner_turn, "bind_seat", lambda cid, m: (bound.append(m), "c")[1])
    monkeypatch.setattr(owner_turn, "fast_voice_default", lambda cid, dev=None: pytest.fail("a pick must win"))
    wire([_done("ok")])
    _stream(ask(client, phone(), voice=True, model="bonsai2:27b"))
    assert bound == ["bonsai2:27b"]


# ── no toast for a routine helper ────────────────────────────────────────────

@pytest.mark.parametrize("path", ["index.html", "ui_parts/styles_and_scene.html"])
def test_a_routine_helper_finishing_raises_no_toast_and_no_zero_dollar_cost(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    m = re.search(r"async function receipt\(o, s\) \{.*?\n            \}\n", src, re.S)
    assert m, path
    body = m.group(0)
    assert "if (!(rc.cost_usd > 0) && !(rc.duration_s != null && rc.duration_s >= 60)) return;" in body
    assert "rc.cost_usd != null" not in body
