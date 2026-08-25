"""
Agent Friday — Music Generation Engine (Lyria 3)
FutureSpeak.AI · Asimov's Mind

The music half of the creative stack. Mirrors ``services/creative_engine.py``
exactly — same envelope shape, same content-safety gate, same process orb, same
metadata sidecar, same Scene-DNA wiring — so a reviewer who knows the image/video
engine already knows this one.

  • generate_music() — text-to-music, image-to-music (mood transfer from up to
                       10 stills), custom lyrics with section tags, timestamp
                       cues, instrumental mode, multi-language vocals. Output is
                       44.1 kHz stereo MP3/WAV in the creations folder.

LYRIA 3 MODELS (friendly → real API id, settings-overridable):
  • lyria-clip → lyria-3-clip-preview   (≤30 s clips)
  • lyria-pro  → lyria-3-pro-preview    (full songs)

SDK FEASIBILITY (verified against the installed google-genai):
  The spec's batch surface (``client.models.generate_music`` +
  ``GenerateMusicConfig``) is ASSUMED from the Imagen/Veo pattern. The installed
  SDK (1.72.x) ships only the Lyria *RealTime* streaming API (``LiveMusic*``),
  not a batch ``generate_music`` method. So the cloud call is feature-detected:
  when the batch surface is present we use it; when it is absent (or no key is
  configured) we fall back to DEMO MODE — a written description of exactly what
  WOULD be generated — instead of breaking. This matches the local-default,
  cloud-as-premium ethos: absence of cloud degrades gracefully, never hard-fails.
  When Google ships the batch surface, only ``_generate_music_cloud`` changes.

Design rules (identical to creative_engine): lazy SDK import (import-safe under
FRIDAY_TESTING and offline), no keys in source, content safety on every prompt,
process orb on every generation, metadata sidecar + signed provenance per file.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import agent_friday.core as core
from agent_friday.core import CREATIONS_DIR

# Reuse the image/video engine's orb + save + metadata + notify helpers verbatim
# so music generation surfaces identically to image/video generation.
from agent_friday.services.creative_engine import (
    _orb_start, _orb_update, _orb_fail, _defer, _safe_remove,
    _save_bytes, _file_record, _write_metadata, _notify, _timestamp,
)

_log = logging.getLogger("friday.music_engine")


# ═══════════════════════════════════════════════════════════════════════════
#  MODEL RESOLUTION — friendly id → real Lyria 3 API model string.
#  Overridable via settings.json {"music_models": {...}}.
# ═══════════════════════════════════════════════════════════════════════════

_MUSIC_MODEL_MAP = {
    "lyria-clip":   "lyria-3-clip-preview",
    "lyria_clip":   "lyria-3-clip-preview",
    "lyria-3-clip": "lyria-3-clip-preview",
    "lyria-pro":    "lyria-3-pro-preview",
    "lyria_pro":    "lyria-3-pro-preview",
    "lyria-3-pro":  "lyria-3-pro-preview",
    "lyria":        "lyria-3-clip-preview",
}
DEFAULT_MUSIC_MODEL = "lyria-clip"
CLIP_MAX_SECONDS = 30

#: Fallback length for a Higgsfield music model that requires `duration` when
#: the caller names none. `sonilo_music` declares duration REQUIRED (unlike
#: every video model, where it is optional), so omitting it is a submit error
#: rather than a provider default. 30 s matches CLIP_MAX_SECONDS — the clip
#: length Friday already means by "a music clip" — so the default length does
#: not silently change with the provider.
HIGGSFIELD_DEFAULT_MUSIC_SECONDS = CLIP_MAX_SECONDS


def _hf_music_extra(model_id: str, duration_seconds=None) -> dict:
    """Model-specific params for a Higgsfield music submit.

    Only sends `duration` to models that publish one, read from the
    enumerated constraints rather than assumed — and supplies the default
    when the model requires it and the caller gave nothing.
    """
    try:
        from agent_friday.services import higgsfield_generate as _hf
        spec = (_hf.model_constraints(model_id) or {}).get("duration")
    except Exception:
        spec = None
    if not spec:
        return {}
    try:
        seconds = int(duration_seconds) if duration_seconds else None
    except (TypeError, ValueError):
        seconds = None
    if seconds is None:
        if not spec.get("required") and spec.get("default") is None:
            return {}                     # optional and unasked — let it be
        seconds = spec.get("default") or HIGGSFIELD_DEFAULT_MUSIC_SECONDS
    # Respect a published range rather than letting the vendor reject it.
    lo, hi = spec.get("min"), spec.get("max")
    try:
        if lo is not None:
            seconds = max(int(lo), seconds)
        if hi is not None:
            seconds = min(int(hi), seconds)
    except (TypeError, ValueError):
        pass
    return {"duration": int(seconds)}


def _settings_overrides() -> Dict[str, str]:
    try:
        from agent_friday.core import _load_settings
        s = _load_settings() or {}
        ov = s.get("music_models") or {}
        return {str(k).lower(): str(v) for k, v in ov.items() if v}
    except Exception:
        return {}


def _seat_model() -> str:
    """The music model Stephen chose, from capability_routing.creative_music.

    This seat was declared, defaulted, mirrored to `music_model` and rendered
    in the picker while NOTHING read it: every caller passed model= explicitly
    and this function fell through to a constant, so choosing a music model
    changed a stored value and nothing else (see services/role_consumers.py).
    """
    try:
        from agent_friday.core import _load_settings
        cr = (_load_settings() or {}).get("capability_routing") or {}
        return ((cr.get("creative_music") or {}).get("model") or "").strip()
    except Exception:
        return ""


def resolve_music_model(requested: Optional[str] = None) -> str:
    """Resolve a friendly/UI music model id to the real Lyria 3 API model string.
    explicit request → settings override → SEAT → built-in table → default.

    An explicit `requested` still wins: a caller naming a model is answering a
    narrower question than the seat, and the seat is the DEFAULT rather than an
    override.
    """
    key = (requested or "").strip().lower()
    overrides = _settings_overrides()
    if key and key in overrides:
        return overrides[key]
    if not key:
        key = _seat_model().lower() or DEFAULT_MUSIC_MODEL
        if key in overrides:
            return overrides[key]
    if key in _MUSIC_MODEL_MAP:
        return _MUSIC_MODEL_MAP[key]
    # Raw-id passthrough. Applies to a seat value as well as an explicit
    # request: the seat may legitimately hold a real API id like
    # `lyria-3-pro-preview` that the friendly table does not list, and
    # dropping it back to the default would be the silent substitution this
    # seat was wired up to end. Anything not matching the lyria shape falls
    # through to the default, so a junk seat value cannot break generation.
    if key and re.match(r"^lyria[\w.\-]*$", key):
        # Explicit requests keep their original casing, exactly as before.
        return requested.strip() if requested else key
    return _MUSIC_MODEL_MAP[DEFAULT_MUSIC_MODEL]


# ═══════════════════════════════════════════════════════════════════════════
#  CONTENT SAFETY — harm floor ONLY (§25.3).
#  Music is maximally open: dark, explicit, political, controversial lyrics are
#  ALLOWED. The one bright line is measurable harm to a REAL person — doxxing,
#  targeted harassment, CSAM. We scan both the prompt and any custom lyrics.
# ═══════════════════════════════════════════════════════════════════════════

_MUSIC_SAFETY_RULES: List[tuple] = [
    ("sexual content involving minors", re.compile(
        r"\b(child|children|kid|kids|minor|minors|underage|pre-?teen|toddler|"
        r"infant|loli|shota|schoolgirl|schoolboy)\b[^.]{0,40}"
        r"\b(nude|naked|sexual|sexually|porn|erotic|explicit|fondl|molest)\b", re.I)),
    ("sexual content involving minors", re.compile(
        r"\b(nude|naked|sexual|sexually|porn|erotic|explicit)\b[^.]{0,40}"
        r"\b(child|children|minor|underage|pre-?teen|toddler|infant)\b", re.I)),
    ("targeted harassment / doxxing of a real person", re.compile(
        r"\b(home address|phone number|social security|ssn|where (he|she|they) live)\b"
        r"[^.]{0,60}\b(kill|hurt|attack|harass|find|stalk)\b", re.I)),
    ("incitement of violence against a named real person", re.compile(
        r"\b(kill|murder|assassinate|lynch|behead)\b[^.]{0,30}"
        r"\b(president|senator|governor|mayor|ceo|named|specific real)\b", re.I)),
]


def check_music_safety(prompt: str, lyrics: Optional[str] = None, *,
                       minor_mode: Optional[bool] = None) -> tuple:
    """Harm-floor gate for music. Returns (allowed, reason|None). Blocks only
    content that harms a real person; offensive/dark/explicit themes pass.

    When minor mode is on (arg or settings) an age-appropriate filter runs on
    top, reusing the creative engine's family-mode rules — so a child's Friday
    won't generate adult/explicit music either."""
    text = ((prompt or "") + "\n" + (lyrics or "")).strip()
    if not text:
        return False, "Empty prompt — nothing to generate."
    if len(text) > 12000:
        return False, "Prompt + lyrics are too long (max 12000 characters)."
    for category, rx in _MUSIC_SAFETY_RULES:
        if rx.search(text):
            return False, (
                f"This request is blocked by Friday's harm floor (cLaws): it "
                f"appears to request {category}. Dark or explicit themes are fine — "
                f"this is specifically about harm to a real person. Try a different "
                f"prompt.")
    try:
        from agent_friday.services.creative_engine import _minor_mode_active, check_minor_appropriate
        if _minor_mode_active(minor_mode):
            ok, reason = check_minor_appropriate(text)
            if not ok:
                return False, reason
    except Exception:
        pass
    return True, None


# ═══════════════════════════════════════════════════════════════════════════
#  PROMPT COMPOSITION — folds the Scene DNA *audio* layer in (zero new fields).
# ═══════════════════════════════════════════════════════════════════════════

def _compose_music_prompt(prompt: str, scene_dna: Optional[dict],
                          mode: str, negative_prompt: Optional[str]) -> str:
    """Build the flat music prompt. The EXISTING SceneDNA.audio layer
    (scene_dna.py) seeds the prompt, so a storyboard that already says
    'audio: tense strings, distant thunder' drives music with no schema change."""
    parts: List[str] = []
    if scene_dna:
        try:
            from agent_friday.services import scene_dna as _sd
            dna = _sd.SceneDNA.from_dict(scene_dna)
            if dna.audio:
                parts.append(dna.audio)
            # Mood also colours the score when present.
            if dna.mood:
                parts.append(f"Mood: {dna.mood}")
        except Exception:
            pass
    if prompt and prompt.strip():
        parts.append(prompt.strip())
    if mode == "instrumental":
        parts.append("Instrumental, no vocals.")
    if negative_prompt:
        parts.append(f"Avoid: {negative_prompt.strip()}")
    return " ".join(p for p in parts if p).strip()


def is_available() -> bool:
    """True if a Gemini API key is configured (cloud music is *possible*)."""
    return bool(getattr(core, "GEMINI_API_KEY", ""))


def cloud_music_available() -> tuple:
    """(available, reason). Cloud music needs BOTH a key AND a google-genai that
    exposes the batch Lyria surface. Feature-detected so a future SDK upgrade
    lights this up with no code change."""
    if not is_available():
        return False, "no Gemini API key configured"
    try:
        from google import genai  # noqa: F401
        from google.genai import models as _m
        if not hasattr(_m.Models, "generate_music"):
            return False, ("installed google-genai has no batch Lyria surface "
                           "(generate_music) — upgrade the SDK to enable cloud music")
        return True, None
    except Exception as e:
        return False, f"google-genai unavailable: {e}"


# ═══════════════════════════════════════════════════════════════════════════
#  GENERATE
# ═══════════════════════════════════════════════════════════════════════════

def generate_music(prompt: str, *,
                   model: Optional[str] = None,
                   mode: str = "instrumental",
                   lyrics: Optional[str] = None,
                   seed_image_path: Optional[str] = None,
                   seed_image_paths: Optional[List[str]] = None,
                   duration_seconds: Optional[int] = None,
                   language: str = "en",
                   timestamps: Optional[list] = None,
                   negative_prompt: Optional[str] = None,
                   session_ctx: Optional[dict] = None,
                   scene_dna: Optional[dict] = None,
                   project_id: Optional[str] = None,
                   license=None) -> Dict[str, Any]:
    """Generate music via Lyria 3. Returns the standard creative envelope:
    {status, files, model, api_model, prompt, ...}. Never raises.
    status ∈ {ok, demo, blocked, unavailable, error}.

    mode: "instrumental" | "song" (with vocals). lyrics enables vocal synthesis
    (section tags like [verse]/[chorus] are honoured by the model). seed_image(s)
    do mood transfer from up to 10 stills. timestamps = [{"t":0.0,"cue":"verse"}]
    section control. scene_dna reads the existing audio layer.
    """
    full_prompt = _compose_music_prompt(prompt or "", scene_dna, mode, negative_prompt)
    allowed, reason = check_music_safety(full_prompt, lyrics)
    if not allowed:
        return {"status": "blocked", "reason": reason}

    # ── Higgsfield-seated music models. ───────────────────────────────────
    # This branch sits BEFORE resolve_music_model() deliberately, and that
    # function is left untouched.
    #
    # resolve_music_model ends with a Lyria-shaped passthrough whose
    # fall-through is unconditional (music_engine.py:121-124), so ANY
    # non-Lyria id becomes lyria-3-clip-preview. Routing a Higgsfield pick
    # through it would call Google, return a Lyria track, and report success
    # — silent substitution, reachable from a settings toggle. Branching
    # first means the guard keeps doing its real job (surviving a junk seat
    # value) for the Lyria ids it was written for, bit-for-bit unchanged.
    try:
        from agent_friday.services import higgsfield_generate as _hf
        _requested_hf = model or _seat_model()
        if _hf.is_higgsfield_model(_requested_hf):
            return _hf.generate("audio", full_prompt, model=_requested_hf,
                                extra=_hf_music_extra(_requested_hf,
                                                      duration_seconds))
    except Exception as _hf_err:                 # never break the Lyria path
        _log.warning("higgsfield music dispatch skipped: %s", _hf_err)

    api_model = resolve_music_model(model)
    # Clip model caps at 30 s; the pro model takes a full song.
    if duration_seconds and api_model.startswith("lyria-3-clip"):
        try:
            duration_seconds = min(int(duration_seconds), CLIP_MAX_SECONDS)
        except (TypeError, ValueError):
            duration_seconds = None

    seeds = list(seed_image_paths or [])
    if seed_image_path:
        seeds.insert(0, seed_image_path)
    seeds = seeds[:10]   # Lyria 3 accepts up to 10 reference stills

    available, why = cloud_music_available()
    if not available:
        # Graceful degradation — demo mode explains what WOULD be generated.
        return _demo_music(full_prompt, model or DEFAULT_MUSIC_MODEL, api_model,
                           mode, lyrics, duration_seconds, language, why,
                           project_id, license)

    orb = _orb_start(f"Composing music — {(model or DEFAULT_MUSIC_MODEL)}…",
                     icon="🎵", name="Generating music")
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret
        _orb_update(orb, progress=0.2, label="Submitting to Lyria…")

        files = _generate_music_cloud(
            client, types, api_model, full_prompt, mode=mode, lyrics=lyrics,
            duration_seconds=duration_seconds, language=language,
            timestamps=timestamps, negative_prompt=negative_prompt,
            seeds=seeds, orb=orb)

        if not files:
            _orb_fail(orb)
            return {"status": "error",
                    "message": "Lyria finished but returned no audio (it may have "
                               "been filtered). Try a different prompt."}

        created = datetime.now().isoformat()
        for f in files:
            meta = {
                "kind": "music", "prompt": full_prompt, "mode": mode,
                "lyrics": bool(lyrics), "language": language,
                "model": model or DEFAULT_MUSIC_MODEL, "api_model": api_model,
                "duration_seconds": duration_seconds, "created": created,
            }
            _write_metadata(f["filename"], meta)
            _write_provenance(f, full_prompt, model or DEFAULT_MUSIC_MODEL,
                              api_model, mode, project_id, license=license)
        _attach_project(project_id, files)
        _orb_update(orb, status="completed", progress=1.0, label="Done")
        _defer(3.0, _safe_remove, orb)
        for f in files:
            _notify(f["filename"])

        return {"status": "ok", "kind": "music", "files": files,
                "model": model or DEFAULT_MUSIC_MODEL, "api_model": api_model,
                "prompt": full_prompt, "mode": mode, "duration_seconds": duration_seconds}
    except Exception as e:
        _orb_fail(orb)
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"Music generation failed: {e}"}


def _generate_music_cloud(client, types, api_model, full_prompt, *, mode, lyrics,
                          duration_seconds, language, timestamps, negative_prompt,
                          seeds, orb) -> List[Dict[str, str]]:
    """The single SDK-coupled function. When Google ships the batch Lyria surface
    this is the only body that changes. Polls the long-running op like Veo, then
    downloads + saves the audio. Feature-detected upstream by cloud_music_available."""
    import time
    cfg_kwargs: Dict[str, Any] = {"mode": mode, "language": language}
    if lyrics:
        cfg_kwargs["lyrics"] = lyrics
    if duration_seconds:
        cfg_kwargs["duration_seconds"] = int(duration_seconds)
    if timestamps:
        cfg_kwargs["section_cues"] = timestamps
    if negative_prompt:
        cfg_kwargs["negative_prompt"] = negative_prompt

    try:
        config = types.GenerateMusicConfig(**cfg_kwargs)
    except Exception:
        config = None

    gen_kwargs: Dict[str, Any] = {"model": api_model, "prompt": full_prompt}
    if config is not None:
        gen_kwargs["config"] = config
    if seeds:
        seed_bytes = _load_seed(seeds[0])
        if seed_bytes:
            gen_kwargs["image"] = types.Image(image_bytes=seed_bytes,
                                              mime_type="image/png")

    operation = client.models.generate_music(**gen_kwargs)

    waited = 0
    while not bool(getattr(operation, "done", False)) and waited < 600:
        time.sleep(8)
        waited += 8
        operation = client.operations.get(operation)
        _orb_update(orb, progress=min(0.9, 0.2 + 0.7 * (waited / 90)),
                   label=f"Composing… {waited}s elapsed")

    _orb_update(orb, progress=0.92, label="Downloading audio…")
    return _extract_and_save_audio(operation, client, full_prompt)


def _extract_and_save_audio(operation, client, prompt: str) -> List[Dict[str, str]]:
    """Pull generated audio out of a finished Lyria op and save it (44.1 kHz
    stereo). Handles SDK shape differences across versions."""
    container = (getattr(operation, "response", None)
                 or getattr(operation, "result", None) or operation)
    out: List[Dict[str, str]] = []
    tracks = (getattr(container, "generated_music", None)
              or getattr(container, "generated_audio", None) or [])
    for tr in tracks:
        audio = getattr(tr, "audio", None) or tr
        data = (getattr(audio, "audio_bytes", None)
                or getattr(audio, "data", None))
        if isinstance(data, str):
            import base64
            try:
                data = base64.b64decode(data)
            except Exception:
                continue
        if not data:
            continue
        mime = getattr(audio, "mime_type", "") or "audio/wav"
        ext = "mp3" if "mp" in mime else "wav"
        fname = f"friday-music-{_timestamp()}-{uuid.uuid4().hex[:4]}.{ext}"
        _save_bytes(data, fname)
        out.append(_file_record(fname, "music"))
    return out


def _load_seed(path: str) -> Optional[bytes]:
    try:
        p = Path(path).expanduser()
        if not p.exists():
            cand = CREATIONS_DIR / Path(path).name
            if cand.exists():
                p = cand
        if p.exists() and p.is_file():
            return p.read_bytes()
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  DEMO MODE — graceful degradation when cloud music is unavailable.
#  Writes a real artifact (a description of the intended track) so the request
#  never breaks, the Studio gallery shows something, and provenance still signs.
# ═══════════════════════════════════════════════════════════════════════════

def _demo_music(full_prompt, model, api_model, mode, lyrics,
                duration_seconds, language, why, project_id,
                license=None) -> Dict[str, Any]:
    orb = _orb_start("Music (demo mode)…", icon="🎵", name="Music — demo")
    try:
        dur = duration_seconds or (30 if "clip" in api_model else 180)
        lines = [
            f"# 🎵 Friday — Music (demo preview)",
            "",
            f"> Cloud music is unavailable ({why}). This is a description of what "
            f"Friday *would* generate with **{model}** ({api_model}). The full "
            f"track renders once a Gemini key + a Lyria-capable google-genai are "
            f"in place.",
            "",
            f"- **Prompt:** {full_prompt}",
            f"- **Mode:** {mode}" + ("  (with vocals)" if lyrics else "  (instrumental)"),
            f"- **Language:** {language}",
            f"- **Target length:** ~{dur}s",
            f"- **Output:** 44.1 kHz stereo {'MP3/WAV' }",
        ]
        if lyrics:
            lines += ["", "## Lyrics", "", "```", lyrics.strip(), "```"]
        fname = f"friday-music-demo-{_timestamp()}-{uuid.uuid4().hex[:4]}.md"
        _save_bytes("\n".join(lines).encode("utf-8"), fname)
        rec = _file_record(fname, "music")
        _write_metadata(fname, {
            "kind": "music", "demo": True, "prompt": full_prompt, "mode": mode,
            "model": model, "api_model": api_model, "language": language,
            "duration_seconds": dur, "reason": why,
            "created": datetime.now().isoformat(),
        })
        _write_provenance(rec, full_prompt, model, api_model, mode, project_id,
                          demo=True, license=license)
        _attach_project(project_id, [rec])
        _orb_update(orb, status="completed", progress=1.0, label="Demo ready")
        _defer(3.0, _safe_remove, orb)
        _notify(fname)
        return {"status": "demo", "kind": "music", "files": [rec],
                "model": model, "api_model": api_model, "prompt": full_prompt,
                "mode": mode, "message": (
                    f"Cloud music is unavailable ({why}). Wrote a demo preview "
                    f"describing the track instead.")}
    except Exception as e:
        _orb_fail(orb)
        return {"status": "unavailable",
                "message": f"Music unavailable ({why}); demo mode failed: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
#  SHARED — provenance + project attachment
# ═══════════════════════════════════════════════════════════════════════════

def _write_provenance(file_rec, prompt, model, api_model, mode, project_id,
                      *, demo: bool = False, license=None) -> None:
    """Sign a Content Credential for the track (Layer 2). Best-effort."""
    try:
        from agent_friday.services import provenance
        from hashlib import sha256
        tool = {"tool": "music_engine.generate_music", "model": model,
                "api_model": api_model, "mode": mode,
                "prompt_hash": "sha256:" + sha256(prompt.encode("utf-8")).hexdigest()}
        if demo:
            tool["demo"] = True
        provenance.write(file_rec["path"], tool_chain=[tool], media_type="music",
                         license=license)
    except Exception:
        pass


def _attach_project(project_id, files) -> None:
    if not project_id:
        return
    try:
        from agent_friday.services import creative_memory
        for f in files:
            creative_memory.add_asset(project_id, f["filename"])
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
#  DISPATCH
# ═══════════════════════════════════════════════════════════════════════════

def generate(prompt: str, **opts) -> Dict[str, Any]:
    """Convenience dispatch used by routes + tools."""
    return generate_music(
        prompt,
        model=opts.get("model"),
        mode=opts.get("mode") or "instrumental",
        lyrics=opts.get("lyrics"),
        seed_image_path=opts.get("seed_image_path"),
        seed_image_paths=opts.get("seed_image_paths"),
        duration_seconds=opts.get("duration_seconds"),
        language=opts.get("language") or "en",
        timestamps=opts.get("timestamps"),
        negative_prompt=opts.get("negative_prompt"),
        session_ctx=opts.get("session_ctx"),
        scene_dna=opts.get("scene_dna"),
        project_id=opts.get("project_id"),
        license=opts.get("license"),
    )
