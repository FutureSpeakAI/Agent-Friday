"""Turn any media into any other, locally, with what is installed.

The conversions the Media workspace offers beyond the text-made ones in
media_index.turn_into (episode, post, page, article, slides, read aloud,
image → video):

    transcript   audio, music, video, episode → a text card with timestamps
    captions     the same → .srt and .vtt files
    wavevideo    audio, music, episode → a waveform video with the captions burned in
    soundtrack   video → its sound track (audio)
    still        video → a still (image)
    narration    deck → the slides read aloud by the local voice
    deckvideo    deck → the slides, each held while the voice reads it
    ocr          image → the words the picture carries (local OCR; not a description)

Each one needs backends that may be absent on a PC (``BACKENDS``): the
capability says which, and why not; the menu offers only what works and the
server refuses the same way. Every job is a Media-owned card made at once in
Draft with "working", an orb with real steps, then the file, its credential
and the kept status, or a "failed" badge with the message and a notice to the
owner. Nothing here leaves this PC. Music is transcribed only when it is
Friday's own (a creation with a sidecar or a credential): the words of
somebody else's song are not reproduced.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import agent_friday.core as core

AV_FROM = ("audio", "music", "video", "episode")
#: target id -> what it needs, which source kinds it takes, the word the menu uses, the kind it makes
CONVERSIONS: Dict[str, Dict[str, Any]] = {
    "transcript": {"needs": ("asr",), "from": AV_FROM, "word": "a transcript", "note": "the words, with the time they were said", "kind": "article"},
    "captions": {"needs": ("asr",), "from": AV_FROM, "word": "captions", "note": "an .srt and a .vtt file", "kind": "doc"},
    "wavevideo": {"needs": ("ffmpeg",), "from": ("audio", "music", "episode"), "word": "a video", "note": "a waveform, the captions burned in", "kind": "video"},
    "soundtrack": {"needs": ("ffmpeg",), "from": ("video",), "word": "the sound track", "note": "the audio on its own", "kind": "audio"},
    "still": {"needs": ("ffmpeg",), "from": ("video",), "word": "a still", "note": "one frame as a picture", "kind": "image"},
    "narration": {"needs": ("voice",), "from": ("deck",), "word": "narration", "note": "the local voice reads each slide", "kind": "audio"},
    "deckvideo": {"needs": ("voice", "ffmpeg"), "from": ("deck",), "word": "a narrated video", "note": "each slide held while the voice reads it", "kind": "video"},
    "ocr": {"needs": ("ocr",), "from": ("image",), "word": "the words in it", "note": "read by local OCR; not a description", "kind": "article"},
}
_SUBPROCESS_FLAGS = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0

#: Tests swap these: the voice (text, voice) -> float32 samples, the office renderer.
speak_fn: Optional[Callable[[str, str], Any]] = None


# ── what this PC has ─────────────────────────────────────────────────────────

def backends() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from agent_friday.services import media_transcripts as mt
        ok = mt.available()
        out["asr"] = {"available": ok, "name": "faster-whisper base.en, on this PC" if ok else None,
                      "reason": "" if ok else "The local speech recogniser (faster-whisper) is not installed on this computer."}
    except Exception as e:
        out["asr"] = {"available": False, "name": None, "reason": "The local speech recogniser could not be loaded (%s)." % str(e)[:100]}
    try:
        from agent_friday.services import media_previews as mp
        exe = mp.ffmpeg_exe()
        out["ffmpeg"] = {"available": bool(exe), "name": "ffmpeg" if exe else None, "reason": "" if exe else "ffmpeg is not on this computer."}
    except Exception as e:
        out["ffmpeg"] = {"available": False, "name": None, "reason": "ffmpeg could not be found (%s)." % str(e)[:100]}
    try:
        if speak_fn is not None:
            out["voice"] = {"available": True, "name": "local voice", "reason": ""}
        else:
            from agent_friday.services import podcast_render as pr
            voices = pr.installed_voices()
            out["voice"] = {"available": bool(voices), "name": "local voice (Kokoro)" if voices else None,
                            "reason": "" if voices else "No local voice is installed on this computer."}
    except Exception as e:
        out["voice"] = {"available": False, "name": None, "reason": "The local voice is not installed (%s)." % str(e)[:100]}
    try:
        from agent_friday.services import file_extraction as fx
        ok = bool(fx.ocr_available())
        out["ocr"] = {"available": ok, "name": "local OCR (RapidOCR)" if ok else None, "reason": "" if ok else "Local OCR is not installed on this computer."}
    except Exception as e:
        out["ocr"] = {"available": False, "name": None, "reason": "Local OCR could not be loaded (%s)." % str(e)[:100]}
    try:
        from agent_friday.services import office_engine
        ok = bool(office_engine.available())
        out["office"] = {"available": ok, "name": "office tool" if ok else None, "reason": "" if ok else "The office tool is not installed on this computer."}
    except Exception as e:
        out["office"] = {"available": False, "name": None, "reason": "The office tool could not be loaded (%s)." % str(e)[:100]}
    return out


def capability(target: str, be: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    spec = CONVERSIONS.get(target)
    if spec is None:
        return {"available": False, "backend": None, "reason": "Not a conversion Media knows."}
    be = be if be is not None else backends()
    names = []
    for need in spec["needs"]:
        b = be.get(need) or {"available": False, "reason": "%s is missing." % need}
        if not b.get("available"):
            return {"available": False, "backend": None, "reason": b.get("reason") or ("%s is missing." % need), "needs": list(spec["needs"])}
        names.append(b.get("name") or need)
    return {"available": True, "backend": " + ".join(names), "reason": "", "needs": list(spec["needs"])}


def resolve(card_kind: str, asked: str) -> Optional[str]:
    """The conversion a card of this kind means by a menu or voice word
    ("video" on an audio card is the waveform video; on a deck, the narrated one)."""
    a = (asked or "").strip().lower()
    k = card_kind
    if a in CONVERSIONS and k in CONVERSIONS[a]["from"]:
        return a
    words = {
        "video": {"audio": "wavevideo", "music": "wavevideo", "episode": "wavevideo", "deck": "deckvideo"},
        "audio": {"video": "soundtrack", "deck": "narration"},
        "sound track": {"video": "soundtrack"}, "soundtrack": {"video": "soundtrack"},
        "narrate": {"deck": "narration"}, "narration": {"deck": "narration"}, "narrated video": {"deck": "deckvideo"},
        "transcript": {k2: "transcript" for k2 in AV_FROM}, "text": {**{k2: "transcript" for k2 in AV_FROM}, "image": "ocr"},
        "words": {"image": "ocr", **{k2: "transcript" for k2 in AV_FROM}}, "ocr": {"image": "ocr"},
        "captions": {k2: "captions" for k2 in AV_FROM}, "subtitles": {k2: "captions" for k2 in AV_FROM},
        "still": {"video": "still"}, "image": {"video": "still"}, "frame": {"video": "still"}, "picture": {"video": "still"},
    }
    return (words.get(a) or {}).get(k)


# ── the job runner ───────────────────────────────────────────────────────────

def _notify(text: str, card_id: str) -> None:
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.send({"type": "notice", "workspace": "media", "card": card_id, "text": text})
    except Exception:
        pass


class _Orb:
    def __init__(self, label: str, steps: List[str]):
        self.pid = "media-" + uuid.uuid4().hex[:8]
        self.steps = steps
        try:
            core.process_register(self.pid, name="Media", label=label, category="monitoring", icon="\U0001f3ac", steps=[], step_total=len(steps))
        except Exception:
            self.pid = None

    def step(self, n: int, frac: float, label: Optional[str] = None) -> None:
        if not self.pid:
            return
        try:
            core.process_update(self.pid, progress=round(max(0.0, min(0.97, frac)), 3), label=label or (self.steps[n] if n < len(self.steps) else None),
                                step={"type": "phase", "name": self.steps[n] if n < len(self.steps) else "", "ts": time.time()}, step_n=n + 1, step_total=len(self.steps))
        except Exception:
            pass

    def done(self, ok: bool, message: str = "") -> None:
        if not self.pid:
            return
        try:
            core.process_update(self.pid, status="completed" if ok else "error", progress=1.0, label=message or None, result=message or None)
        except Exception:
            pass


def _media_file(c: Dict[str, Any]) -> Optional[Path]:
    from agent_friday.services import media_previews as mp
    return mp.media_path(c)


def looks_like_a_song(path: Optional[Path]) -> bool:
    """An imported recording that carries an artist or an album tag is somebody's song."""
    if path is None or path.suffix.lower() not in (".mp3", ".m4a", ".flac", ".ogg"):
        return False
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
    except OSError:
        return False
    return any(tag in head for tag in (b"TPE1", b"TALB", b"\xa9ART", b"\xa9alb", b"ARTIST=", b"ALBUM="))


def own_music(c: Dict[str, Any]) -> bool:
    """Friday's own music: a creation with a sidecar or a credential. Somebody
    else's song is not transcribed, so its words are never reproduced here."""
    p = Path(c["path"]) if c.get("path") else None
    if c.get("kind") != "music" and not looks_like_a_song(p):
        return True
    if c.get("signed"):
        return True
    if c.get("source_kind") == "creation":
        try:
            from agent_friday.services.creative_engine import creation_metadata
            if creation_metadata(Path(c["path"]).name):
                return True
        except Exception:
            pass
    return False


def convert(c: Dict[str, Any], target: str, sync: Optional[bool] = None) -> Dict[str, Any]:
    """Start one conversion. Returns at once with the new card (Draft, "working"),
    or a refusal with the reason. ``sync`` (tests) waits for the work."""
    from agent_friday.services import media_index as mi
    spec = CONVERSIONS.get(target)
    if spec is None:
        return {"status": "error", "message": "Not a conversion Media knows."}
    if c.get("kind") not in spec["from"]:
        return {"status": "error", "message": "%s cannot be turned into %s." % (mi.KIND_WORD.get(c.get("kind"), c.get("kind")), spec["word"])}
    cap = capability(target)
    if not cap["available"]:
        return {"status": "unavailable", "message": cap["reason"]}
    if target in ("transcript", "captions", "wavevideo") and not own_music(c):
        return {"status": "denied", "message": "That music is not Friday's own, so its words are not written out here: lyrics are somebody's work."}
    src = _media_file(c)
    if src is None and target != "ocr":
        return {"status": "error", "message": "There is no file on this PC to convert."}
    if target == "ocr":
        src = Path(c["path"]) if c.get("path") else None
        if src is None or not src.is_file():
            return {"status": "error", "message": "There is no picture file on this PC to read."}
    if sync is None:
        sync = bool(os.environ.get("FRIDAY_TESTING"))
    cid = "media:" + uuid.uuid4().hex[:12]
    title = {"transcript": "Transcript: ", "captions": "Captions: ", "wavevideo": "Video: ", "soundtrack": "Sound of: ", "still": "Still from: ",
             "narration": "Narration: ", "deckvideo": "Video: ", "ocr": "Words in: "}[target] + c["title"]
    rec = {"id": cid, "kind": spec["kind"], "title": title, "project": c.get("project"), "sources": [c["title"]],
           "maker": "%s · this PC" % cap["backend"], "status": "draft", "created": time.time(), "origin": "turn",
           "relations": [{"to": c["id"], "how": "made_from"}], "file": "", "extra": {"badges": ["working"], "conversion": target, "from": str(src)}}
    mi._write_media_record(cid, rec)
    mi._rescan_media_records()

    def work() -> None:
        orb = _Orb("Media: %s" % title[:48], STEPS.get(target, ["working", "saving"]))
        try:
            file_path, body, extra = DOERS[target](c, src, cid, orb)
            if file_path is not None:
                mi._sign(Path(file_path), spec["kind"], [{"kind": "card", "ref": c["id"], "title": c["title"]}], "media." + target)
                rec["file"] = str(file_path)
            if body is not None:
                (mi.cards_dir() / (cid.split(":")[1] + ".md")).write_text(body, encoding="utf-8")
            rec["status"] = "kept"
            rec["extra"] = dict(extra or {}, conversion=target, **{"from": str(src)})
            orb.done(True, "%s is ready." % title)
            _notify("%s is ready in Media." % title, cid)
        except Exception as e:
            msg = str(getattr(e, "user_message", None) or e)[:240]
            rec["status"] = "draft"
            rec["extra"] = {"badges": ["failed"], "error": msg, "conversion": target, "from": str(src)}
            orb.done(False, msg)
            _notify("%s could not be made: %s" % (title, msg), cid)
        mi._write_media_record(cid, rec)
        mi._rescan_media_records()

    if sync:
        work()
    else:
        threading.Thread(target=work, name="media-convert-" + target, daemon=True).start()
    return {"status": "ok", "card": mi.get(cid), "message": "Making %s on this PC; the card says when it is done." % spec["word"]}


STEPS = {
    "transcript": ["listening", "writing it out"], "captions": ["listening", "timing the lines"],
    "wavevideo": ["listening", "drawing the waveform", "burning in the captions"], "soundtrack": ["separating the sound"],
    "still": ["picking the frame"], "narration": ["reading the slides", "speaking", "saving"],
    "deckvideo": ["reading the slides", "rendering the slides", "speaking", "assembling the video"], "ocr": ["reading the picture"],
}


# ── the doers: (card, source file, new id, orb) -> (file or None, body or None, extra) ──

def _out_dir(sub: str) -> Path:
    from agent_friday.services import media_index as mi
    d = mi.media_dir() / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ffmpeg() -> str:
    from agent_friday.services import media_previews as mp
    exe = mp.ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg is not on this computer")
    return exe


def _font_env() -> Dict[str, str]:
    """ffmpeg's subtitles filter (libass) reads fonts through fontconfig, which
    has no default config on Windows: point it at the system fonts with a
    small config written beside the index, once."""
    env = dict(os.environ)
    if sys.platform != "win32":
        return env
    try:
        from agent_friday.services import media_index as mi
        conf = mi.media_dir() / "fonts.conf"
        cache = mi.media_dir() / "fontcache"
        cache.mkdir(parents=True, exist_ok=True)
        if not conf.exists():
            conf.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>C:/Windows/Fonts</dir><cachedir>%s</cachedir></fontconfig>\n'
                            % str(cache).replace("\\", "/"), encoding="utf-8")
        env["FONTCONFIG_FILE"] = str(conf)
    except Exception:
        pass
    return env


def _fontfile() -> Optional[str]:
    for cand in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        if Path(cand).exists():
            return cand.replace("\\", "/").replace(":", "\\:")
    return None


def _run(argv: List[str], timeout: float = 900.0) -> subprocess.CompletedProcess:
    r = subprocess.run(argv, capture_output=True, timeout=timeout, creationflags=_SUBPROCESS_FLAGS, env=_font_env())
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError("ffmpeg: " + (err[-1] if err else "failed with code %s" % r.returncode)[:200])
    return r


def _stamp(t: float, vtt: bool = False) -> str:
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return ("%02d:%02d:%02d.%03d" if vtt else "%02d:%02d:%02d,%03d") % (h, m, s, ms)


def _transcribe(c: Dict[str, Any], orb: _Orb) -> Dict[str, Any]:
    from agent_friday.services import media_transcripts as mt
    orb.step(0, 0.1)
    rec = mt.transcribe(c)
    if not rec or not rec.get("text"):
        raise RuntimeError("the recogniser heard no words in it" if rec else "the file could not be transcribed")
    return rec


def _do_transcript(c, src, cid, orb) -> Tuple[Optional[Path], Optional[str], Dict[str, Any]]:
    rec = _transcribe(c, orb)
    orb.step(1, 0.8)
    lines = ["# Transcript: %s" % c["title"], "", "_%s, on this PC._" % (rec.get("engine") or "local recogniser"), ""]
    for s in rec.get("segments") or []:
        t = int(float(s.get("start") or 0))
        lines.append("[%d:%02d] %s" % (t // 60, t % 60, (s.get("text") or "").strip()))
    return None, "\n".join(lines) + "\n", {"words": len(rec["text"].split()), "engine": rec.get("engine"), "segments": len(rec.get("segments") or [])}


def captions_files(rec: Dict[str, Any], base: Path) -> Tuple[Path, Path]:
    """Write .srt and .vtt beside each other from timed segments."""
    segs = [s for s in (rec.get("segments") or []) if (s.get("text") or "").strip()]
    srt, vtt = [], ["WEBVTT", ""]
    for i, s in enumerate(segs, start=1):
        a, b = float(s.get("start") or 0), float(s.get("end") or (float(s.get("start") or 0) + 2))
        srt += [str(i), "%s --> %s" % (_stamp(a), _stamp(b)), s["text"].strip(), ""]
        vtt += ["%s --> %s" % (_stamp(a, True), _stamp(b, True)), s["text"].strip(), ""]
    sp, vp = base.with_suffix(".srt"), base.with_suffix(".vtt")
    sp.write_text("\n".join(srt) + "\n", encoding="utf-8")
    vp.write_text("\n".join(vtt) + "\n", encoding="utf-8")
    return sp, vp


def _do_captions(c, src, cid, orb):
    rec = _transcribe(c, orb)
    orb.step(1, 0.8)
    base = _out_dir("captions") / cid.split(":")[1]
    sp, vp = captions_files(rec, base)
    return sp, sp.read_text(encoding="utf-8"), {"vtt": str(vp), "lines": len(rec.get("segments") or []), "engine": rec.get("engine")}


def _sub_path(p: Path) -> str:
    """A path the subtitles filter accepts on every platform."""
    s = str(p).replace("\\", "/")
    s = s.replace(":", "\\:")
    return s.replace("'", "\\'")


def _do_wavevideo(c, src, cid, orb):
    exe = _ffmpeg()
    out = _out_dir("video") / (cid.split(":")[1] + ".mp4")
    srt = None
    try:
        from agent_friday.services import media_transcripts as mt
        if mt.available() and own_music(c):
            orb.step(0, 0.1)
            rec = mt.transcribe(c)
            if rec and rec.get("segments"):
                srt, _vtt = captions_files(rec, _out_dir("captions") / cid.split(":")[1])
    except Exception:
        srt = None
    orb.step(1, 0.4)
    title = c["title"].replace("'", "’").replace(":", "\\:").replace("%", "%%")
    chain = "[0:a]showwaves=s=1280x620:mode=cline:rate=25:colors=0x00E5FF|0x7B61FF,format=yuv420p,pad=1280:720:0:50:0x0C0E14[w]"
    ff = _fontfile()
    graph = chain + ";[w]drawtext=%stext='%s':fontcolor=0xECF0F8:fontsize=30:x=24:y=16[v]" % (("fontfile='%s':" % ff) if ff else "", title)
    note = ""
    if srt is not None:
        graph2 = graph.replace("[v]", "[t]") + ";[t]subtitles='%s':force_style='FontSize=26,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1'[v]" % _sub_path(srt)
        try:
            orb.step(2, 0.6)
            _run([exe, "-v", "error", "-y", "-i", str(src), "-filter_complex", graph2, "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])
            return out, None, {"captions": str(srt), "waveform": True}
        except Exception as e:
            note = "captions omitted (%s)" % str(e)[:120]
    try:
        _run([exe, "-v", "error", "-y", "-i", str(src), "-filter_complex", graph, "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])
    except Exception:
        # no drawtext font on this machine: the waveform alone
        _run([exe, "-v", "error", "-y", "-i", str(src), "-filter_complex", chain.replace("[w]", "[v]"), "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])
    return out, None, {"waveform": True, "note": note or ("no captions: " + ("no transcript" if srt is None else "")).strip(": ")}


def _do_soundtrack(c, src, cid, orb):
    exe = _ffmpeg()
    orb.step(0, 0.3)
    out = _out_dir("audio") / (cid.split(":")[1] + ".m4a")
    _run([exe, "-v", "error", "-y", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "160k", str(out)])
    return out, None, {}


def _do_still(c, src, cid, orb):
    exe = _ffmpeg()
    orb.step(0, 0.3)
    out = _out_dir("images") / (cid.split(":")[1] + ".png")
    for ss in ("1.5", "0"):
        try:
            _run([exe, "-v", "error", "-y", "-ss", ss, "-i", str(src), "-frames:v", "1", str(out)])
            if out.exists():
                break
        except Exception:
            continue
    if not out.exists():
        raise RuntimeError("no frame could be read from the video")
    return out, None, {"at_s": 1.5}


def slide_texts(path: Path) -> List[Tuple[str, str]]:
    """(title, words) per slide of a .pptx, from the zip: the first paragraph
    with words is the title, the rest are the words."""
    import re as _re
    import zipfile
    out: List[Tuple[str, str]] = []
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted([n for n in z.namelist() if _re.match(r"ppt/slides/slide\d+\.xml$", n)], key=lambda n: int(_re.findall(r"\d+", n)[-1]))
            for n in names:
                xml = z.read(n).decode("utf-8", "ignore")
                paras = []
                for block in _re.findall(r"<a:p\b.*?</a:p>", xml, _re.S):
                    t = " ".join(x.strip() for x in _re.findall(r"<a:t>([^<]*)</a:t>", block)).strip()
                    if t:
                        paras.append(t)
                head = paras[0][:120] if paras else ""
                rest = " ".join(paras[1:]).strip() if len(paras) > 1 else ""
                out.append((head, rest))
    except Exception:
        return out
    return out


def _speak(text: str, voice: str):
    if speak_fn is not None:
        return speak_fn(text, voice)
    from agent_friday.services import podcast_render as pr
    return pr.speaker().speak(text, voice)


def _narrate_slides(slides: List[Tuple[str, str]], orb: _Orb, step: int) -> Tuple[List[bytes], str]:
    """PCM per slide (16-bit, podcast_render.RATE), and the voice used."""
    from agent_friday.services import podcast_render as pr
    from agent_friday.services import media_index as mi
    voices = pr.installed_voices() if speak_fn is None else ["af_heart"]
    voice = "af_heart" if "af_heart" in voices else (voices[0] if voices else "af_heart")
    out: List[bytes] = []
    n = max(1, len(slides))
    for i, (head, words) in enumerate(slides):
        text = (head + ". " if head else "") + words
        pcm = b""
        for chunk in mi._chunks(text)[:60]:
            pcm += pr._silence(0.3) if chunk == "" else pr._to_pcm16(_speak(chunk, voice))
        if not pcm:
            pcm = pr._silence(2.0)
        out.append(pcm + pr._silence(0.6))
        orb.step(step, 0.2 + 0.5 * (i + 1) / n, "speaking slide %d of %d" % (i + 1, n))
    return out, voice


def _do_narration(c, src, cid, orb):
    from agent_friday.services import podcast_render as pr
    orb.step(0, 0.1)
    slides = slide_texts(src)
    if not any(h or w for h, w in slides):
        raise RuntimeError("the deck has no words to read")
    pcms, voice = _narrate_slides(slides, orb, 1)
    orb.step(2, 0.9)
    wav = _out_dir("audio") / (cid.split(":")[1] + ".wav")
    pr.write_wav(b"".join(pcms), wav)
    out = wav
    try:
        mp3 = wav.with_suffix(".mp3")
        if pr.encode_mp3(wav, mp3, title=c["title"], album="Narrated by Agent Friday™", artist="Agent Friday™") and mp3.exists():
            out = mp3
    except Exception:
        pass
    starts, t = [], 0.0
    for p in pcms:
        starts.append(round(t, 2)); t += len(p) / 2 / pr.RATE
    return out, None, {"duration_s": round(t, 2), "voice": voice, "slides": len(slides), "slide_starts": starts}


def _slide_card(title: str, words: str, index: int, total: int, size=(1280, 720)):
    from PIL import Image, ImageDraw, ImageFont
    W, H = size
    img = Image.new("RGB", (W, H), (12, 14, 20))
    d = ImageDraw.Draw(img)
    ft = fb = fs = None
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            ft, fb, fs = ImageFont.truetype(name, 54), ImageFont.truetype(name, 30), ImageFont.truetype(name, 20)
            break
        except OSError:
            continue
    if ft is None:
        ft = fb = fs = ImageFont.load_default()
    y = 80
    if title:
        d.text((80, y), title[:70], fill=(236, 240, 248), font=ft); y += 100
    line, lines = "", []
    for w in (words or "").split():
        if len(line) + len(w) + 1 > 62:
            lines.append(line); line = w
        else:
            line = (line + " " + w).strip()
        if len(lines) >= 11:
            break
    if line and len(lines) < 11:
        lines.append(line)
    for ln in lines:
        d.text((80, y), ln, fill=(170, 182, 200), font=fb); y += 42
    d.text((80, H - 48), "%d / %d" % (index, total), fill=(110, 120, 140), font=fs)
    d.rectangle([0, H - 6, W, H], fill=(0, 229, 255))
    return img


def render_slides(src: Path, slides: List[Tuple[str, str]], into: Path, orb: _Orb, step: int) -> Tuple[List[Path], bool]:
    """One PNG per slide: the office tool's own render when it is here, else a text card."""
    into.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    office = None
    try:
        from agent_friday.services import office_engine
        office = office_engine if office_engine.available() else None
    except Exception:
        office = None
    n = max(1, len(slides))
    used_office = False
    for i, (head, words) in enumerate(slides, start=1):
        p = into / ("slide-%03d.png" % i)
        png = None
        if office is not None:
            try:
                png, _note = office.screenshot(src, page=str(i), width=1280)
            except Exception:
                png = None
        if png:
            p.write_bytes(png); used_office = True
        else:
            _slide_card(head, words, i, len(slides)).save(p, "PNG")
        out.append(p)
        orb.step(step, 0.1 + 0.2 * i / n, "rendering slide %d of %d" % (i, n))
    return out, used_office


def _do_deckvideo(c, src, cid, orb):
    from agent_friday.services import podcast_render as pr
    exe = _ffmpeg()
    orb.step(0, 0.05)
    slides = slide_texts(src)
    if not slides:
        raise RuntimeError("the deck has no slides")
    work = _out_dir("video") / (cid.split(":")[1] + "-slides")
    pngs, used_office = render_slides(src, slides, work, orb, 1)
    pcms, voice = _narrate_slides(slides, orb, 2)
    orb.step(3, 0.85)
    wav = work / "narration.wav"
    pr.write_wav(b"".join(pcms), wav)
    durations = [max(1.5, len(p) / 2 / pr.RATE) for p in pcms]
    lst = work / "list.txt"
    lines = []
    for p, dur in zip(pngs, durations):
        lines += ["file '%s'" % str(p).replace("\\", "/").replace("'", "\\'"), "duration %.3f" % dur]
    lines.append("file '%s'" % str(pngs[-1]).replace("\\", "/").replace("'", "\\'"))
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = _out_dir("video") / (cid.split(":")[1] + ".mp4")
    _run([exe, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(wav), "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:0x0C0E14,format=yuv420p",
          "-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest", str(out)])
    return out, None, {"duration_s": round(sum(durations), 2), "voice": voice, "slides": len(slides), "rendered_by": "office tool" if used_office else "text cards"}


def _do_ocr(c, src, cid, orb):
    from agent_friday.services import file_extraction as fx
    orb.step(0, 0.3)
    res = fx._ocr_image_file(src)
    if res is None:
        raise RuntimeError("local OCR is not installed")
    if not res.text:
        raise RuntimeError(res.error or "no readable words in the picture")
    text = res.text
    body = "# Words in: %s\n\n_The words the picture carries, read by local OCR on this PC. Not a description of the picture._\n\n%s\n" % (c["title"], text.split("\n", 1)[1] if text.startswith("[") and "\n" in text else text)
    return None, body, {"words": len(text.split()), "engine": "local OCR"}


DOERS: Dict[str, Callable[..., Tuple[Optional[Path], Optional[str], Dict[str, Any]]]] = {
    "transcript": _do_transcript, "captions": _do_captions, "wavevideo": _do_wavevideo, "soundtrack": _do_soundtrack,
    "still": _do_still, "narration": _do_narration, "deckvideo": _do_deckvideo, "ocr": _do_ocr,
}


def text_of(c: Dict[str, Any]) -> str:
    """The words a card carries, for the text-made conversions: its body, what
    the preview pass read out of it, or an extraction done now (documents)."""
    body = (c.get("body") or "").strip()
    if body:
        return body
    try:
        from agent_friday.services import media_previews as mp
        t = (mp.details(c) or {}).get("text") or ""
        if t.strip():
            return t
    except Exception:
        pass
    p = Path(c["path"]) if c.get("path") else None
    if p is None or not p.is_file():
        return ""
    if p.suffix.lower() == ".pptx":
        return "\n\n".join((h + "\n" + w).strip() for h, w in slide_texts(p) if (h or w))
    if p.suffix.lower() in (".docx", ".pdf", ".txt", ".md"):
        try:
            from agent_friday.services import file_extraction as fx
            return fx.extract_text(p).text or ""
        except Exception:
            return ""
    return ""
