"""ElevenLabs text-to-speech for Friday's agent seat.

Added 2026-08-19. The seat could already *listen* to audio (inspect_audio) and
*save* a provider's output (save_output), but it could not produce speech: the
storybook pipeline had narration as a hole in the middle of it. Two tools close
that hole:

  speak_text   — synthesise speech from text via ElevenLabs, verify the bytes
                 are really audio, and file them into the creations folder with
                 a sidecar + manifest + provenance record.
  list_voices  — enumerate the voices this account can actually use, so the
                 model picks a real voice_id instead of inventing one.

Design rules, consistent with creative_engine.py / media_tools.py:

  * The HTTP client is imported LAZILY inside the call sites, so importing this
    module never requires network libs or a key — it stays import-safe under
    FRIDAY_TESTING and offline.
  * The API key is read from core.ELEVENLABS_API_KEY (env / settings.json).
    No keys in source.
  * Bytes are verified through creative_store._looks_real before anything is
    called a creation. A 200 response carrying an HTML error page, or a
    zero-byte body, is a failure — not a silent empty MP3. This is the same
    class of bug the creative_store docstring was written about.
  * Output lands under DAILY_CREATIONS_DIR with the standard sidecar/manifest/
    provenance, so ElevenLabs audio shows up in the gallery like every other
    creation and is never a stray file only this tool knows about.
  * Model + default voice resolve through ~/.friday/settings.json
    ("elevenlabs_model" / "elevenlabs_voice_id") so a model rename needs no
    code change — same escape hatch creative_engine uses for creative_models.

Verification is deliberately delegated, not duplicated: speak_text tells the
seat to run inspect_audio on the result. That keeps one transcription path in
the codebase and means generated narration gets listened to before it ships.

Registration is by explicit call from agent.py so the registries stay owned
there:  elevenlabs_tools.register(CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS, TOOL_RINGS)
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

API_ROOT = "https://api.elevenlabs.io/v1"

# Sensible defaults. Both overridable per-call and via settings.json.
#   eleven_multilingual_v2 — highest quality, the right default for narration.
#   eleven_flash_v2_5      — ~10x cheaper and much lower latency; the right
#                            choice for conversational use. See
#                            docs/design/elevenlabs-voice.md.
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"      # "Rachel" — a stock voice
DEFAULT_VOICE_NAME = "Rachel"

_TIMEOUT = 120
_MAX_CHARS = 5000        # guard against an accidental novel-length bill


def _settings():
    try:
        from agent_friday import core
        loader = getattr(core, "_load_settings", None)
        return loader() if callable(loader) else {}
    except Exception:
        return {}


def _api_key():
    """Resolve the key from core, the live env, then settings.json."""
    try:
        from agent_friday import core
        key = getattr(core, "ELEVENLABS_API_KEY", "") or ""
    except Exception:
        key = ""
    if not key:
        key = os.environ.get("ELEVENLABS_API_KEY", "") or ""
    if not key:
        key = _settings().get("elevenlabs_api_key", "") or ""
    return key.strip()


_NO_KEY = (
    "no ElevenLabs API key configured. Set ELEVENLABS_API_KEY (start.bat or "
    "the environment), then restart the server. Note: an ElevenLabs API key "
    "starts with 'sk_' — the shorter hex string shown next to a key in the "
    "dashboard is the key *id*, not the secret, and will be rejected."
)


def _request(method, path, *, key, json_body=None, stream=False):
    """Thin HTTP wrapper. Returns (response, error_string)."""
    import requests
    url = API_ROOT + path
    try:
        resp = requests.request(
            method, url,
            headers={"xi-api-key": key, "accept": "*/*"},
            json=json_body, timeout=_TIMEOUT, stream=stream)
    except Exception as e:
        return None, "network error talking to ElevenLabs (%s)" % e
    if resp.status_code >= 400:
        detail = ""
        try:
            body = resp.json().get("detail")
            if isinstance(body, dict):
                detail = body.get("message") or body.get("status") or ""
            elif body:
                detail = str(body)
        except Exception:
            detail = (resp.text or "")[:300]
        return None, "ElevenLabs HTTP %d: %s" % (resp.status_code,
                                                 detail or "(no detail)")
    return resp, None


def _safe_name(text, ext=".mp3"):
    stem = re.sub(r"[^A-Za-z0-9]+", "-", (text or "speech")[:40]).strip("-").lower()
    return "%s-%s%s" % (stem or "speech", uuid.uuid4().hex[:8], ext)


def _dest_dir(folder):
    from agent_friday import core
    base = Path(getattr(core, "DAILY_CREATIONS_DIR", None)
                or core.CREATIONS_DIR)
    sub = (folder or "").strip().strip("/\\")
    if sub:
        if os.path.isabs(sub) or ".." in sub.replace("\\", "/").split("/"):
            return None, "'folder' must be a relative subfolder name."
        base = base / sub
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return None, "could not create %s (%s)" % (base, e)
    return base, None


# ── speak_text ──────────────────────────────────────────────────────────────

def _tool_speak_text(inp):
    inp = inp or {}
    text = (inp.get("text") or "").strip()
    if not text:
        return "speak_text error: 'text' is required."
    if len(text) > _MAX_CHARS:
        return ("speak_text error: text is %d characters (limit %d). Split it "
                "into several calls — this limit exists so a runaway prompt "
                "cannot spend the character quota in one shot."
                % (len(text), _MAX_CHARS))
    if os.environ.get("FRIDAY_TESTING"):
        return "speak_text: skipped (FRIDAY_TESTING set — no network calls)."

    key = _api_key()
    if not key:
        return "speak_text error: " + _NO_KEY

    settings = _settings()
    voice_id = (inp.get("voice_id") or settings.get("elevenlabs_voice_id")
                or DEFAULT_VOICE_ID)
    model_id = (inp.get("model_id") or settings.get("elevenlabs_model")
                or DEFAULT_MODEL)

    dest_dir, err = _dest_dir(inp.get("folder"))
    if err:
        return "speak_text error: " + err

    body = {"text": text, "model_id": model_id}
    stability = inp.get("stability")
    similarity = inp.get("similarity_boost")
    if stability is not None or similarity is not None:
        vs = {}
        if stability is not None:
            vs["stability"] = float(stability)
        if similarity is not None:
            vs["similarity_boost"] = float(similarity)
        body["voice_settings"] = vs

    started = time.time()
    resp, err = _request("POST", "/text-to-speech/%s" % voice_id,
                         key=key, json_body=body, stream=True)
    if err:
        return "speak_text failed: " + err
    try:
        data = resp.content
    except Exception as e:
        return "speak_text failed: could not read audio body (%s)" % e
    elapsed = time.time() - started

    # A 200 with an HTML error page or an empty body is a failure, not a
    # creation. Reuse the canonical check rather than writing a second one.
    from agent_friday.services import creative_store
    if not creative_store._looks_real(data, ".mp3"):
        return ("speak_text failed: ElevenLabs returned %d bytes that are not "
                "valid MP3 audio — refusing to file it as a creation."
                % len(data))

    filename = (inp.get("filename") or "").strip()
    filename = Path(filename).name if filename else _safe_name(text)
    if not filename.lower().endswith(".mp3"):
        filename += ".mp3"
    dest = dest_dir / filename
    try:
        dest.write_bytes(data)
    except OSError as e:
        return "speak_text failed: could not write %s (%s)" % (dest, e)

    job = "elevenlabs-tts-%s" % uuid.uuid4().hex[:8]
    for fn, args in (("_write_sidecar", (filename, API_ROOT + "/text-to-speech/"
                                         + voice_id, job, len(data))),
                     ("_append_manifest", (dest_dir, filename, dest, job)),
                     ("_write_provenance", (dest, job))):
        try:                              # provenance is best-effort by design
            getattr(creative_store, fn)(*args)
        except Exception:
            pass

    return ("spoke %d chars -> %s (%.1f KB, %.1fs, voice=%s, model=%s)\n"
            "Verify it before shipping: inspect_audio path=%s"
            % (len(text), dest, len(data) / 1024, elapsed, voice_id,
               model_id, dest))


# ── list_voices ─────────────────────────────────────────────────────────────

def _tool_list_voices(inp):
    inp = inp or {}
    if os.environ.get("FRIDAY_TESTING"):
        return "list_voices: skipped (FRIDAY_TESTING set — no network calls)."
    key = _api_key()
    if not key:
        return "list_voices error: " + _NO_KEY
    resp, err = _request("GET", "/voices", key=key)
    if err:
        return "list_voices failed: " + err
    try:
        voices = (resp.json() or {}).get("voices") or []
    except Exception as e:
        return "list_voices failed: could not parse reply (%s)" % e
    if not voices:
        return "list_voices: this account has no voices available."
    want = (inp.get("search") or "").strip().lower()
    lines = []
    for v in voices:
        name = v.get("name") or "(unnamed)"
        vid = v.get("voice_id") or "?"
        cat = v.get("category") or "?"
        labels = v.get("labels") or {}
        desc = ", ".join(str(x) for x in labels.values() if x)
        if want and want not in name.lower() and want not in desc.lower():
            continue
        lines.append("%-24s %s  [%s]%s"
                     % (name, vid, cat, ("  " + desc) if desc else ""))
    if not lines:
        return "list_voices: no voice matched %r (%d total)." % (want, len(voices))
    return ("%d voice(s):\n" % len(lines)) + "\n".join(lines)


# ── registration ────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "speak_text",
        "description": (
            "SPEAK: turn text into natural speech with ElevenLabs and save it "
            "as an MP3 in the creations folder. Use this for narration, "
            "voiceover, and audio drafts. Pick a voice with list_voices first "
            "if the user asked for a particular sound; otherwise the default "
            "voice is used. Always run inspect_audio on the result before "
            "telling the user it is finished — that catches garbled or "
            "truncated speech. Costs characters against the ElevenLabs quota, "
            "so do not re-generate identical text."),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to speak (max 5000 chars)."},
                "voice_id": {"type": "string", "description": "Optional ElevenLabs voice_id (see list_voices)."},
                "model_id": {"type": "string", "description": "Optional model, e.g. eleven_multilingual_v2 (quality) or eleven_flash_v2_5 (fast/cheap)."},
                "folder": {"type": "string", "description": "Optional relative subfolder under the daily creations directory."},
                "filename": {"type": "string", "description": "Optional exact filename to save as (.mp3)."},
                "stability": {"type": "number", "description": "Optional 0-1. Lower is more expressive, higher more consistent."},
                "similarity_boost": {"type": "number", "description": "Optional 0-1 adherence to the original voice."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "list_voices",
        "description": (
            "List the ElevenLabs voices this account can actually use, with "
            "their voice_ids. Call this before speak_text whenever the user "
            "asks for a specific kind of voice (warm, British, male, "
            "narrator), so you pass a real voice_id instead of guessing one. "
            "Optional 'search' filters by name or descriptive label."),
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Optional filter on voice name or label."},
            },
            "required": [],
        },
    },
]

RINGS = {"speak_text": 2, "list_voices": 2}

HANDLERS = {
    "speak_text": _tool_speak_text,
    "list_voices": _tool_list_voices,
}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
