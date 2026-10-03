"""Turn any media into any other: every sensible cell of the matrix works
locally, is offered only where its backend is on this PC, shows progress,
and fails visibly. Music that is not Friday's own is never transcribed.
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import wave
import zipfile
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import media_convert as mc
from agent_friday.services import media_index as mi
from agent_friday.services import media_transcripts as mt

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "media_ws.js").read_text(encoding="utf-8")


def _wav(path: Path, seconds: float = 2.0) -> None:
    rate = 8000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 330 * i / rate))) for i in range(int(rate * seconds))))


def _pptx(path: Path) -> None:
    slide = '<?xml version="1.0"?><p:sld xmlns:p="x" xmlns:a="y"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{}</a:t></a:r></a:p><a:p><a:r><a:t>{}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide.format("Covista pitch", "Three numbers explain the quarter."))
        z.writestr("ppt/slides/slide2.xml", slide.format("Revenue", "It climbed in the third quarter."))


def _mp4(path: Path, wav: Path) -> bool:
    from agent_friday.services import media_previews as mp
    exe = mp.ffmpeg_exe()
    if not exe:
        return False
    r = subprocess.run([exe, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x224466:s=160x90:r=8", "-i", str(wav), "-t", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], capture_output=True, timeout=60)
    return r.returncode == 0 and path.exists()


FAKE_TX = {"text": "Today I said the harbour wall needs money.", "segments": [
    {"start": 0.0, "end": 0.9, "text": "Today I said"}, {"start": 0.9, "end": 1.9, "text": "the harbour wall needs money."}], "engine": "fake asr"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    fd = tmp_path / ".friday"
    fd.mkdir()
    creations = tmp_path / "friday-creations"
    creations.mkdir()
    monkeypatch.setattr(core, "FRIDAY_DIR", fd)
    monkeypatch.setattr(core, "CREATIONS_DIR", creations)
    monkeypatch.setattr(core, "DAILY_CREATIONS_DIR", fd / "creations")
    from agent_friday.services import office_engine, content_pipeline as cp, creative_engine as ce
    (fd / "documents").mkdir()
    monkeypatch.setattr(office_engine, "DOCUMENTS_DIR", fd / "documents")
    (fd / "creations_meta").mkdir()
    monkeypatch.setattr(ce, "CREATIVE_META_DIR", fd / "creations_meta")
    monkeypatch.setattr(cp, "DB_PATH", fd / "content_pipeline.db")
    (fd / "content").mkdir()
    monkeypatch.setattr(cp, "PUBLISH_LOG", fd / "content" / "publish_log.jsonl")
    from agent_friday.services import misc_engine, provenance, approvals
    monkeypatch.setattr(misc_engine, "CONTENT_DIR", fd / "content")
    monkeypatch.setattr(misc_engine, "CONTENT_PIPELINE_FILE", fd / "content" / "pipeline.json")
    monkeypatch.setattr(provenance, "PROVENANCE_DIR", fd / "provenance", raising=False)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", fd / "approvals.json", raising=False)
    monkeypatch.setattr(mi, "_STATE", {"state": "never", "started": None, "finished": None, "indexed": 0, "counts": {}, "signature": None, "checked": 0.0, "reason": ""}, raising=False)
    monkeypatch.setattr(mt, "transcriber", lambda p: dict(FAKE_TX))
    monkeypatch.setattr(mc, "speak_fn", lambda text, voice: __import__("numpy").zeros(2400, dtype="float32"))
    sent = []
    from agent_friday.services import desktop_bus
    monkeypatch.setattr(desktop_bus, "send", lambda m: sent.append(m))
    _wav(creations / "friday-music-low-tide.wav")
    (fd / "creations_meta" / "friday-music-low-tide.wav.json").write_text(json.dumps({"kind": "music", "prompt": "low tide, slow"}), encoding="utf-8")
    _wav(creations / "friday-audio-note.wav", 1.0)
    # an imported recording with an artist and an album tag: somebody's song
    (creations / "imported-song.mp3").write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x40TPE1\x00\x00\x00\x05\x00\x00\x03BandTALB\x00\x00\x00\x06\x00\x00\x03Album" + b"\x00" * 64)
    _pptx(fd / "documents" / "pitch.pptx")
    has_mp4 = _mp4(creations / "friday-video-quay.mp4", creations / "friday-audio-note.wav")
    mi.reindex()
    by = {c.get("filename") or c["title"]: c for c in mi.query(view="all", limit=100)["cards"]}
    return {"fd": fd, "creations": creations, "by": by, "sent": sent, "has_mp4": has_mp4}


def test_every_cell_is_offered_only_with_its_backend_and_the_menu_reads_the_same_map(home, monkeypatch):
    caps = mi.turn_capabilities()
    assert set(caps["by_group"]) >= {"image", "audio", "music", "video", "deck", "text", "document"}
    for group, cells in caps["by_group"].items():
        for target, cap in cells.items():
            assert set(cap) >= {"available", "reason"} and (cap["available"] or cap["reason"]), (group, target)
    # no recogniser: transcript, captions say so and refuse; the waveform video still works (without captions)
    monkeypatch.setattr(mt, "available", lambda: False)
    monkeypatch.setattr(mt, "transcriber", None)
    caps = mi.turn_capabilities()
    assert caps["by_group"]["audio"]["transcript"]["available"] is False and "recogniser" in caps["by_group"]["audio"]["transcript"]["reason"]
    r = mi.turn_into(home["by"]["friday-audio-note.wav"]["id"], "transcript")
    assert r["status"] == "unavailable" and "recogniser" in r["message"]
    # the page builds the menu from by_group and names what is missing
    assert "const caps = (window.__mediaTurns && window.__mediaTurns.by_group && window.__mediaTurns.by_group[turnGroup(c.kind)]) || {};" in JS
    assert "'data-missing-turns'" in JS
    for word in ("['transcript', 'a transcript'", "['captions', 'captions'", "['wavevideo', 'a video'", "['soundtrack', 'the sound track'", "['still', 'a still'", "['narration', 'narration'", "['deckvideo', 'a narrated video'", "['ocr', 'the words in it'"):
        assert word in JS, word + " is in the menu"


def test_audio_becomes_a_transcript_and_captions_with_timestamps(home):
    note = home["by"]["friday-audio-note.wav"]
    r = mi.turn_into(note["id"], "transcript")
    assert r["status"] == "ok", r
    card = mi.get(r["card"]["id"])
    assert card["kind"] == "article" and card["status"] == "kept" and "[0:00] Today I said" in card["body"]
    assert card["maker"].startswith("faster-whisper") and any(x["how"] == "made_from" for x in card["relations"])
    assert "ready in Media" in home["sent"][-1]["text"]
    r = mi.turn_into(note["id"], "captions")
    card = mi.get(r["card"]["id"])
    assert card["status"] == "kept" and card["path"].endswith(".srt")
    srt = Path(card["path"]).read_text(encoding="utf-8")
    assert "00:00:00,900 --> 00:00:01,900" in srt and "the harbour wall needs money." in srt
    assert Path(card["extra"]["vtt"]).read_text(encoding="utf-8").startswith("WEBVTT")


def test_music_is_transcribed_only_when_it_is_fridays_own(home):
    own = home["by"]["friday-music-low-tide.wav"]
    assert own["kind"] == "music" and mc.own_music(own)
    assert mi.turn_into(own["id"], "transcript")["status"] == "ok"
    imported = home["by"]["imported-song.mp3"]
    assert not mc.own_music(imported)
    r = mi.turn_into(imported["id"], "transcript")
    assert r["status"] == "denied" and "lyrics" in r["message"]
    assert mi.turn_into(imported["id"], "captions")["status"] == "denied"


def test_audio_becomes_a_waveform_video_and_video_gives_back_its_sound_and_a_still(home):
    pytest.importorskip("numpy")
    from agent_friday.services import media_previews as mp
    if not mp.ffmpeg_exe():
        pytest.skip("no ffmpeg here")
    note = home["by"]["friday-audio-note.wav"]
    r = mi.turn_into(note["id"], "video")
    assert r["status"] == "ok", r
    card = mi.get(r["card"]["id"])
    assert card["kind"] == "video" and card["status"] == "kept", card.get("extra")
    assert Path(card["path"]).suffix == ".mp4" and Path(card["path"]).stat().st_size > 1000
    assert card["extra"].get("waveform") is True
    if not home["has_mp4"]:
        pytest.skip("no test video could be made here")
    vid = home["by"]["friday-video-quay.mp4"]
    snd = mi.get(mi.turn_into(vid["id"], "audio")["card"]["id"])
    assert snd["kind"] == "audio" and snd["status"] == "kept" and Path(snd["path"]).suffix == ".m4a"
    still = mi.get(mi.turn_into(vid["id"], "still")["card"]["id"])
    assert still["kind"] == "image" and still["status"] == "kept" and Path(still["path"]).read_bytes()[:4] == b"\x89PNG"
    tx = mi.get(mi.turn_into(vid["id"], "transcript")["card"]["id"])
    assert tx["status"] == "kept" and "harbour wall" in tx["body"]


def test_a_deck_is_narrated_and_becomes_a_video_one_slide_per_line_spoken(home, monkeypatch):
    pytest.importorskip("numpy")
    from agent_friday.services import media_previews as mp
    if not mp.ffmpeg_exe():
        pytest.skip("no ffmpeg here")
    deck = home["by"]["pitch.pptx"]
    assert mc.slide_texts(Path(deck["path"]))[0][0] == "Covista pitch"
    r = mi.turn_into(deck["id"], "narration")
    assert r["status"] == "ok", r
    nar = mi.get(r["card"]["id"])
    assert nar["kind"] == "audio" and nar["status"] == "kept" and nar["extra"]["slides"] == 2 and len(nar["extra"]["slide_starts"]) == 2
    r = mi.turn_into(deck["id"], "video")
    vid = mi.get(r["card"]["id"])
    assert vid["kind"] == "video" and vid["status"] == "kept", vid.get("extra")
    assert Path(vid["path"]).suffix == ".mp4" and vid["extra"]["slides"] == 2 and vid["extra"]["duration_s"] >= 3
    # the deck's words also feed the text-made conversions: an article from the slides
    art = mi.turn_into(deck["id"], "article")
    assert art["status"] == "ok" and "Three numbers explain the quarter." in (mi.get(art["card"]["id"])["body"] or "")


def test_a_failure_is_visible_on_the_card_and_in_a_notice(home, monkeypatch):
    note = home["by"]["friday-audio-note.wav"]
    monkeypatch.setattr(mt, "transcriber", lambda p: (_ for _ in ()).throw(RuntimeError("the recogniser crashed")))
    r = mi.turn_into(note["id"], "transcript")
    assert r["status"] == "ok", "the card is made at once; the failure lands on it"
    card = mi.get(r["card"]["id"])
    assert card["status"] == "draft" and "failed" in card["badges"] and "recogniser crashed" in card["extra"]["error"]
    assert "could not be made" in home["sent"][-1]["text"] and "recogniser crashed" in home["sent"][-1]["text"]


def test_words_in_a_picture_are_read_by_local_ocr_or_honestly_refused(home, monkeypatch):
    from agent_friday.services import file_extraction as fx
    from PIL import Image
    p = home["creations"] / "friday-image-sign.png"
    Image.new("RGB", (64, 32), (255, 255, 255)).save(p, "PNG")
    mi.reindex()
    img = [c for c in mi.query(view="all", limit=100)["cards"] if c.get("filename") == "friday-image-sign.png"][0]
    monkeypatch.setattr(fx, "ocr_available", lambda: False)
    assert mi.turn_into(img["id"], "ocr")["status"] == "unavailable"
    monkeypatch.setattr(fx, "ocr_available", lambda: True)
    monkeypatch.setattr(fx, "_ocr_image_file", lambda path: fx.ExtractionResult("[OCR]\nNO MOORING", None, ocr=True))
    card = mi.get(mi.turn_into(img["id"], "ocr")["card"]["id"])
    assert card["kind"] == "article" and card["status"] == "kept" and "NO MOORING" in card["body"] and "Not a description" in card["body"]


def test_every_conversion_is_voice_callable(home, monkeypatch):
    from agent_friday.services import media_card_tools as tools
    note = home["by"]["friday-audio-note.wav"]
    out = json.loads(tools._tool_media_turn({"card": note["id"], "into": "transcript"}))
    assert out["status"] == "ok" and out["card"]["kind"] == "article"
    out = json.loads(tools._tool_media_turn({"card": note["id"], "into": "captions"}))
    assert out["status"] == "ok"
    deck = home["by"]["pitch.pptx"]
    out = json.loads(tools._tool_media_turn({"card": deck["id"], "into": "narration"}))
    assert out["status"] == "ok" and out["card"]["kind"] == "audio"
    out = json.loads(tools._tool_media_turn({"card": note["id"], "into": "a sonnet"}))
    assert out["status"] == "error" and "transcript" in out["say"]
