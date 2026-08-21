"""Self-QC and asset-handling tools for Friday's agent seat.

Born from the storybook E2E test (2026-08-19), where the seat had to say
"I can't view pixels this session" every turn while an external agent did the
looking. Three tools close that loop:

  inspect_image — describe / QC a local image (Gemini flash vision, the same
                  provider+model the chat screenshot path already uses).
  inspect_audio — transcribe a local audio file (faster-whisper, on-device),
                  report duration + loudness, and optionally ask a vision-tier
                  question about tone/quality (Gemini audio understanding).
  save_output   — download a generation result URL to disk through
                  creative_store (byte verification, sidecar, manifest,
                  provenance) instead of shelling out to curl.

Registration is by explicit call from agent.py so the registries stay owned
there:  media_tools.register(CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS, TOOL_RINGS)
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_AUDIO_EXT = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac", ".opus"}
_VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv"}

_MAX_IMAGE_BYTES = 18 * 1024 * 1024


def _roots():
    from agent_friday import core
    roots = [Path(core.CREATIONS_DIR)]
    daily = getattr(core, "DAILY_CREATIONS_DIR", None)
    if daily:
        roots.append(Path(daily))
    home = Path.home()
    roots.append(home / "Desktop")
    roots.append(home / "Downloads")
    return roots


def _resolve_media_path(raw):
    """Resolve a user/model-supplied path to a real file, or (None, error)."""
    if not raw or not str(raw).strip():
        return None, "a 'path' to a local file is required."
    p = Path(os.path.expanduser(str(raw).strip().strip('"')))
    candidates = [p] if p.is_absolute() else []
    if not p.is_absolute():
        candidates += [root / p for root in _roots()]
    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c.resolve(), None
        except OSError:
            continue
    # Last resort: search creations roots by filename.
    name = p.name
    for root in _roots()[:2]:
        try:
            hits = list(root.rglob(name))
        except OSError:
            hits = []
        if len(hits) == 1:
            return hits[0].resolve(), None
        if len(hits) > 1:
            return None, ("%d files named %r under %s — pass a more specific "
                          "path." % (len(hits), name, root))
    return None, "no file found at %r (also searched the creations folders)." % str(raw)


def _gemini_client():
    from agent_friday import core
    if not getattr(core, "GEMINI_API_KEY", None):
        return None
    from google import genai
    return genai.Client(api_key=core.GEMINI_API_KEY)


def _ffprobe_duration(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)], timeout=30).decode().strip()
        return float(out)
    except Exception:
        return None


def _volume_stats(path):
    try:
        out = subprocess.run(
            ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, timeout=120).stderr.decode(errors="replace")
        mean = maxv = None
        for line in out.splitlines():
            if "mean_volume:" in line:
                mean = line.split("mean_volume:")[1].strip()
            elif "max_volume:" in line:
                maxv = line.split("max_volume:")[1].strip()
        return mean, maxv
    except Exception:
        return None, None


# ── inspect_image ───────────────────────────────────────────────────────────

def _tool_inspect_image(inp):
    inp = inp or {}
    path, err = _resolve_media_path(inp.get("path"))
    if err:
        return "inspect_image error: " + err
    if path.suffix.lower() not in _IMAGE_EXT:
        # Video? Grab a frame instead so clips are inspectable too.
        if path.suffix.lower() in _VIDEO_EXT:
            return _inspect_video_frame(path, inp)
        return "inspect_image error: %s is not an image file." % path.name
    try:
        data = path.read_bytes()
    except OSError as e:
        return "inspect_image error: could not read %s (%s)" % (path, e)
    if len(data) > _MAX_IMAGE_BYTES:
        return "inspect_image error: %s is %.1f MB — too large to inspect." % (
            path.name, len(data) / 1e6)
    question = (inp.get("question") or "").strip() or (
        "Describe this image precisely: subjects, their appearance (hair, "
        "clothing, colors), composition, art style, and any text or lettering "
        "visible anywhere in the image. If characters are present, describe "
        "each one's distinguishing features.")
    client = _gemini_client()
    if client is None:
        return ("inspect_image error: no vision provider configured "
                "(GEMINI_API_KEY missing).")
    from google.genai import types
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp"}[
        path.suffix.lower().lstrip(".")]
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[question, types.Part.from_bytes(data=data, mime_type=mime)])
        return "[%s | %.1f KB]\n%s" % (path.name, len(data) / 1024,
                                       (resp.text or "").strip())
    except Exception as e:
        return "inspect_image error: vision call failed (%s)" % e


def _inspect_video_frame(path, inp):
    """Sample frames from a video and describe them (start/middle/end)."""
    dur = _ffprobe_duration(path) or 0.0
    stamps = [0.2, max(0.2, dur / 2), max(0.4, dur - 0.4)] if dur else [0.2]
    client = _gemini_client()
    if client is None:
        return ("inspect_image error: no vision provider configured "
                "(GEMINI_API_KEY missing).")
    from google.genai import types
    question = (inp.get("question") or "").strip() or (
        "These are frames from the start, middle and end of one video clip. "
        "Describe what happens across them: subjects, motion, consistency of "
        "characters between frames, and any text/lettering.")
    parts = [question]
    import tempfile
    for i, t in enumerate(stamps):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            frame_path = tf.name
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", "%.2f" % t, "-i",
                 str(path), "-frames:v", "1", frame_path],
                timeout=60, check=True)
            parts.append(types.Part.from_bytes(
                data=Path(frame_path).read_bytes(), mime_type="image/png"))
        except Exception:
            continue
        finally:
            try:
                os.unlink(frame_path)
            except OSError:
                pass
    if len(parts) == 1:
        return "inspect_image error: could not extract frames from %s" % path.name
    try:
        resp = client.models.generate_content(model="gemini-2.5-flash",
                                              contents=parts)
        return "[%s | %.1fs video, %d frames sampled]\n%s" % (
            path.name, dur, len(parts) - 1, (resp.text or "").strip())
    except Exception as e:
        return "inspect_image error: vision call failed (%s)" % e


# ── inspect_audio ───────────────────────────────────────────────────────────

_WHISPER = None


def _whisper():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel("base.en", device="cpu", compute_type="int8")
    return _WHISPER


def _tool_inspect_audio(inp):
    inp = inp or {}
    path, err = _resolve_media_path(inp.get("path"))
    if err:
        return "inspect_audio error: " + err
    if path.suffix.lower() not in _AUDIO_EXT | _VIDEO_EXT:
        return "inspect_audio error: %s is not an audio/video file." % path.name
    dur = _ffprobe_duration(path)
    mean, maxv = _volume_stats(path)
    lines = ["[%s]" % path.name,
             "duration: %s" % ("%.1fs" % dur if dur else "unknown"),
             "levels: mean %s, peak %s" % (mean or "?", maxv or "?")]
    try:
        segs, _info = _whisper().transcribe(str(path))
        text = " ".join(s.text.strip() for s in segs).strip()
        lines.append("transcript: %s" % (text or "(no speech detected)"))
    except Exception as e:
        lines.append("transcript unavailable (%s)" % e)
    question = (inp.get("question") or "").strip()
    if question:
        client = _gemini_client()
        if client is not None:
            from google.genai import types
            try:
                data = path.read_bytes()
                mime = "audio/mp4" if path.suffix.lower() in (".m4a", ".mp4") \
                    else "audio/mpeg" if path.suffix.lower() == ".mp3" \
                    else "audio/wav" if path.suffix.lower() == ".wav" \
                    else "audio/ogg"
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[question,
                              types.Part.from_bytes(data=data, mime_type=mime)])
                lines.append("listen check: " + (resp.text or "").strip())
            except Exception as e:
                lines.append("listen check unavailable (%s)" % e)
        else:
            lines.append("listen check unavailable (no GEMINI_API_KEY)")
    return "\n".join(lines)


# ── save_output ─────────────────────────────────────────────────────────────

def _tool_save_output(inp):
    inp = inp or {}
    url = (inp.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "save_output error: 'url' must be an http(s) result URL."
    from agent_friday import core
    from agent_friday.services import creative_store
    dest_dir = None
    sub = (inp.get("folder") or "").strip().strip("/\\")
    if sub:
        # Confine to the creations roots — no absolute/parent escapes.
        if os.path.isabs(sub) or ".." in sub.replace("\\", "/").split("/"):
            return "save_output error: 'folder' must be a relative subfolder name."
        dest_dir = Path(core.DAILY_CREATIONS_DIR) / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
    filename = (inp.get("filename") or "").strip()
    job = {"provider": "higgsfield", "kind": "media",
           "request_id": (inp.get("job_id") or "manual"),
           "prompt": "", "model": ""}
    try:
        res = creative_store.download_output(url, job=job, dest_dir=dest_dir)
    except Exception as e:
        return "save_output error: %s" % e
    if not res.get("ok"):
        return "save_output failed: %s" % json.dumps(
            {k: v for k, v in res.items() if k != "file_record"})
    saved = Path(res["path"])
    if filename and filename != saved.name:
        # Honour an explicit filename (creative_store names by job/url hash).
        safe = Path(filename).name
        target = saved.with_name(safe)
        try:
            if target.exists():
                target.unlink()
            saved.rename(target)
            saved = target
        except OSError as e:
            return "save_output: downloaded to %s but rename failed (%s)" % (saved, e)
    return "saved: %s (%.1f KB)" % (saved, saved.stat().st_size / 1024)


# ── registration ────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "inspect_image",
        "description": (
            "LOOK at a local image (or sample frames from a local video) with "
            "a vision model and get a precise description back. Use this to "
            "verify your own generations: character consistency, art style, "
            "stray text/lettering, whether a scene matches its brief. Pass an "
            "absolute path or a filename that exists under the creations "
            "folders. Optional 'question' focuses the look (e.g. 'is the king "
            "bald with a gray beard? any text visible?')."),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Local image or video file path."},
                "question": {"type": "string", "description": "Optional: what to check for."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_audio",
        "description": (
            "LISTEN to a local audio file: returns duration, loudness levels, "
            "and a local speech-to-text transcript (on-device whisper). Use "
            "this to verify narration/TTS before shipping — hallucinated or "
            "garbled speech shows up in the transcript. Optional 'question' "
            "additionally plays the audio to a cloud model for a tone/quality "
            "judgment (e.g. 'does this voice suit a bedtime story?')."),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Local audio (or video) file path."},
                "question": {"type": "string", "description": "Optional tone/quality question."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "save_output",
        "description": (
            "Download a generation result URL to disk safely (verifies the "
            "bytes are real media, writes provenance + manifest). Use this "
            "instead of shell curl for saving Higgsfield or other generation "
            "outputs. 'folder' is an optional subfolder under the daily "
            "creations directory (e.g. 'storybook-liberty/clips'); 'filename' "
            "optionally renames the saved file."),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL of the finished output."},
                "folder": {"type": "string", "description": "Optional relative subfolder under creations."},
                "filename": {"type": "string", "description": "Optional exact filename to save as."},
                "job_id": {"type": "string", "description": "Optional job id for provenance."},
            },
            "required": ["url"],
        },
    },
]

RINGS = {"inspect_image": 2, "inspect_audio": 2, "save_output": 2}

HANDLERS = {
    "inspect_image": _tool_inspect_image,
    "inspect_audio": _tool_inspect_audio,
    "save_output": _tool_save_output,
}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
