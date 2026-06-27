"""
Agent Friday — Creative Generation Engine
FutureSpeak.AI · Asimov's Mind

The real image- and video-generation pipeline, wired onto Google's Gemini APIs.

  • generate_image() — prompt-to-image via Gemini's native image models
                       ("Nano Banana Pro" / "Nano Banana 2"). Style + aspect
                       ratio parameters. Saves PNG(s) to the creations folder.
  • generate_video() — text-to-video AND image-to-video via Google Veo. Polls
                       the long-running operation, downloads the MP4, saves it
                       to the creations folder.

MODEL ROLES (do NOT mix these up — enforced by the resolver below):
  • Gemini 2.5 Flash / Pro  → voice / text / reasoning. NEVER creative output.
  • Gemini Nano Banana Pro  → IMAGE generation.
  • Gemini Nano Banana 2    → IMAGE generation.
  • Google Veo              → VIDEO generation.

Design rules (consistent with the rest of the codebase):
  • The google-genai SDK and the Gemini client are imported LAZILY inside the
    call sites, so importing this module never requires the SDK or a key — it
    stays import-safe under FRIDAY_TESTING and offline.
  • The API key is read from core.GEMINI_API_KEY (loaded from env / credential store).
    No keys in source.
  • Every generation surfaces a holographic process orb (register → progress →
    complete → fade) exactly like the self-improvement / daily-creation loops.
  • Content safety (Asimov's cLaws) gates EVERY prompt before any model call.
  • Output lands in CREATIONS_DIR (~/Desktop/friday-creations/) with a metadata
    sidecar in ~/.friday/creations_meta/ so the gallery stays clean, and the
    standard completion notification fires.
  • The friendly catalog ids ("gemini-nano-banana-pro", "veo", …) resolve to the
    real Gemini API model strings through a map that ~/.friday/settings.json can
    override (creative_models) — so an API model rename needs no code change.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import agent_friday.core as core
from agent_friday.core import CREATIONS_DIR, FRIDAY_DIR

# Metadata lives OUTSIDE the creations folder so the Studio gallery (which lists
# every file in CREATIONS_DIR) is not polluted with .json sidecars.
CREATIVE_META_DIR = FRIDAY_DIR / "creations_meta"


# ═══════════════════════════════════════════════════════════════════════════
#  MODEL RESOLUTION — friendly catalog id → real Gemini API model string.
#  Overridable via ~/.friday/settings.json {"creative_models": {...}} so a model
#  rename on Google's side is a config edit, not a code change.
# ═══════════════════════════════════════════════════════════════════════════

# Nano Banana = Google's nickname for the Gemini native image models.
_IMAGE_MODEL_MAP = {
    "gemini-nano-banana-pro": "gemini-3-pro-image-preview",
    "nano-banana-pro":        "gemini-3-pro-image-preview",
    "nano_banana_pro":        "gemini-3-pro-image-preview",
    "gemini-nano-banana-2":   "gemini-2.5-flash-image",
    "nano-banana-2":          "gemini-2.5-flash-image",
    "nano_banana_2":          "gemini-2.5-flash-image",
    "nano-banana":            "gemini-2.5-flash-image",
}
_VIDEO_MODEL_MAP = {
    "veo":          "veo-3.0-generate-preview",
    "gemini-veo":   "veo-3.0-generate-preview",
    "google-veo":   "veo-3.0-generate-preview",
    "veo-3":        "veo-3.0-generate-preview",
    "veo-3.0":      "veo-3.0-generate-preview",
    "veo-3-fast":   "veo-3.0-fast-generate-preview",
    "veo-2":        "veo-2.0-generate-001",
    "veo-2.0":      "veo-2.0-generate-001",
}

DEFAULT_IMAGE_MODEL = "gemini-nano-banana-pro"
DEFAULT_VIDEO_MODEL = "veo"

# Voice / text models that must NEVER be used for creative output.
_FORBIDDEN_CREATIVE = (
    "gemini-2.5-flash", "gemini-2.5-pro",
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-preview-tts",
)

# Aspect ratios the UI offers. Anything else falls back to the default.
IMAGE_ASPECT_RATIOS = ("1:1", "3:4", "4:3", "9:16", "16:9")
VIDEO_ASPECT_RATIOS = ("16:9", "9:16")


def _settings_overrides() -> Dict[str, str]:
    try:
        from agent_friday.core import _load_settings
        s = _load_settings() or {}
        ov = s.get("creative_models") or {}
        return {str(k).lower(): str(v) for k, v in ov.items() if v}
    except Exception:
        return {}


def _resolve_model(requested: Optional[str], default: str, table: Dict[str, str]) -> str:
    """Resolve a friendly/UI model id to the real Gemini API model string.

    Resolution order: settings override → built-in table → if the caller passed
    a string that already looks like a raw API id (e.g. 'imagen-4.0-generate-001'
    or 'veo-3.0-generate-preview'), trust it verbatim → default.
    """
    key = (requested or "").strip().lower()
    overrides = _settings_overrides()
    if key and key in overrides:
        return overrides[key]
    if not key:
        key = default
        if key in overrides:
            return overrides[key]
    if key in table:
        return table[key]
    # A raw API id the caller already knows (passthrough) — but never a known
    # voice/text model masquerading as creative.
    if key in _FORBIDDEN_CREATIVE:
        return table[default]
    if requested and re.match(r"^(imagen|veo|gemini-)[\w.\-]+$", key):
        return requested.strip()
    return table[default]


def resolve_image_model(requested: Optional[str] = None) -> str:
    return _resolve_model(requested, DEFAULT_IMAGE_MODEL, _IMAGE_MODEL_MAP)


def resolve_video_model(requested: Optional[str] = None) -> str:
    return _resolve_model(requested, DEFAULT_VIDEO_MODEL, _VIDEO_MODEL_MAP)


# ═══════════════════════════════════════════════════════════════════════════
#  CONTENT SAFETY — Asimov's cLaws. A heuristic gate run on EVERY prompt before
#  any model call. Scoped to clearly-prohibited categories so ordinary creative
#  prompts pass untouched. Returns (allowed, reason).
# ═══════════════════════════════════════════════════════════════════════════

# Each rule pairs a category label with a compiled regex. A match blocks the
# generation. Patterns are intentionally narrow (multi-token) to avoid nuking
# legitimate art ("a child reading a book", "a war memorial") while still
# catching the genuinely prohibited combinations.
_SAFETY_RULES: List[tuple] = [
    ("sexual content involving minors", re.compile(
        r"\b(child|children|kid|kids|minor|minors|underage|pre-?teen|teen(age)?|"
        r"toddler|infant|baby|girl|boy|loli|shota|schoolgirl|schoolboy)\b"
        r"[^.]{0,40}\b(nude|naked|nsfw|sexual|sexually|porn|pornographic|erotic|"
        r"explicit|fondl|molest|in lingerie|in underwear|bikini)\b", re.I)),
    ("sexual content involving minors", re.compile(
        r"\b(nude|naked|sexual|sexually|porn|erotic|explicit)\b[^.]{0,40}"
        r"\b(child|children|minor|underage|pre-?teen|toddler|infant)\b", re.I)),
    ("non-consensual sexual content", re.compile(
        r"\b(rape|raping|non-?consensual|forced)\b[^.]{0,30}\b(sex|sexual|nude|naked)\b", re.I)),
    ("real-person sexual deepfake", re.compile(
        r"\b(nude|naked|sex tape|porn|explicit|in lingerie)\b[^.]{0,40}"
        r"\b(of|depicting|featuring)\b[^.]{0,40}\b(celebrity|politician|president|"
        r"actor|actress|real person|public figure)\b", re.I)),
    ("instructions to build a weapon of mass destruction", re.compile(
        r"\b(build|make|construct|synthesi[sz]e|assemble|schematic|blueprint|diagram)\b"
        r"[^.]{0,40}\b(nuclear bomb|atomic bomb|dirty bomb|bioweapon|biological weapon|"
        r"nerve agent|sarin|chemical weapon|pipe bomb|ied|explosive device)\b", re.I)),
    ("graphic real-world gore depicting an identifiable person", re.compile(
        r"\b(gory|graphic|mutilated|dismembered|decapitat)\w*\b[^.]{0,40}"
        r"\b(corpse|body of|murder of)\b[^.]{0,40}\b(real|actual|named)\b", re.I)),
]


# Age-appropriate filter rules (minor mode ONLY). These are NOT part of the
# adult harm floor — they are an extra layer applied only when settings.minor_mode
# is on, so a child's Friday won't *generate* adult material. This filters what
# the minor sees, not what exists on the platform (§7, family-mode design).
_MINOR_FILTER_RULES: List[tuple] = [
    ("adult / sexual content", re.compile(
        r"\b(nude|naked|nudity|sexual|sexually|porn|pornographic|erotic|nsfw|"
        r"lingerie|fetish|bdsm|strip(per|tease)?)\b", re.I)),
    ("graphic violence / gore", re.compile(
        r"\b(gore|gory|graphic violence|dismember\w*|decapitat\w*|mutilat\w*|"
        r"blood(y|bath)|brutal killing)\b", re.I)),
    ("hard drugs", re.compile(
        r"\b(cocaine|heroin|meth(amphetamine)?|fentanyl|crack pipe|injecting drugs)\b", re.I)),
    ("self-harm", re.compile(
        r"\b(self-?harm|suicide|cutting (myself|herself|himself))\b", re.I)),
]


def _minor_mode_active(minor_mode: Optional[bool]) -> bool:
    """Resolve whether the age-appropriate filter applies. Explicit arg wins;
    otherwise read settings.minor_mode (best-effort, defaults off)."""
    if minor_mode is not None:
        return bool(minor_mode)
    try:
        from agent_friday.core import _load_settings
        return bool((_load_settings() or {}).get("minor_mode", False))
    except Exception:
        return False


def check_minor_appropriate(prompt: str) -> tuple:
    """Age-appropriate filter for minor-mode Friday. Returns (allowed, reason)."""
    text = (prompt or "")
    for category, rx in _MINOR_FILTER_RULES:
        if rx.search(text):
            return False, (
                f"Friday is in family / minor mode, so it won't create "
                f"{category}. A parent can adjust this in Settings. Try a "
                f"different idea.")
    return True, None


def check_content_safety(prompt: str, *, minor_mode: Optional[bool] = None) -> tuple:
    """Asimov's cLaws gate. Returns (allowed: bool, reason: str|None).

    A block is a refusal, not an error — callers should surface `reason` to the
    user plainly. Pure / side-effect-free so it unit-tests without a model.

    The adult harm floor (H1–H4) always applies. When ``minor_mode`` is on (arg
    or settings), an additional age-appropriate filter runs on top of it.
    """
    text = (prompt or "").strip()
    if not text:
        return False, "Empty prompt — nothing to generate."
    if len(text) > 8000:
        return False, "Prompt is too long (max 8000 characters)."
    for category, rx in _SAFETY_RULES:
        if rx.search(text):
            return False, (
                f"This request is blocked by Friday's content safety (cLaws): it "
                f"appears to request {category}. I can't generate that. Try a "
                f"different prompt."
            )
    if _minor_mode_active(minor_mode):
        ok, reason = check_minor_appropriate(text)
        if not ok:
            return False, reason
    return True, None


# ═══════════════════════════════════════════════════════════════════════════
#  PROCESS ORB — holographic in-flight indicator. Mirrors introspection.py /
#  creations.py: register → update(progress,label) → complete → fade. Every
#  helper is best-effort; a missing/failed orb never blocks generation.
# ═══════════════════════════════════════════════════════════════════════════

def _orb_start(label: str, icon: str = "🎨", name: str = "Creating"):
    try:
        pid = f"create-{uuid.uuid4().hex[:8]}"
        core.process_register(pid, name=name, label=label,
                              category="monitoring", icon=icon)
        return pid
    except Exception:
        return None


def _orb_update(pid, **kw) -> None:
    if not pid:
        return
    try:
        core.process_update(pid, **kw)
    except Exception:
        pass


def _defer(seconds: float, fn, *args) -> None:
    """Run `fn(*args)` after a delay so a completed/failed orb lingers briefly
    before fading. Under FRIDAY_TESTING it runs synchronously so no background
    timer thread survives the call (the smoke suite asserts a low thread count)."""
    if os.environ.get("FRIDAY_TESTING"):
        try:
            fn(*args)
        except Exception:
            pass
        return
    t = threading.Timer(seconds, fn, args=args)
    t.daemon = True
    t.start()


def _orb_fail(pid) -> None:
    if not pid:
        return
    try:
        core.process_update(pid, status="error", progress=1.0, label="Failed")
        _defer(3.0, core.process_remove, pid)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
#  OUTPUT + METADATA
# ═══════════════════════════════════════════════════════════════════════════

def _slug(text: str, fallback: str = "creation") -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or fallback)[:40]


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _ext_for_mime(mime: str, default: str) -> str:
    if not mime:
        return default
    sub = mime.split("/")[-1].lower()
    return {"jpeg": "jpg", "x-png": "png"}.get(sub, sub) or default


def _write_metadata(filename: str, meta: Dict[str, Any]) -> None:
    """Write a metadata sidecar for a generated file (outside CREATIONS_DIR)."""
    try:
        CREATIVE_META_DIR.mkdir(parents=True, exist_ok=True)
        (CREATIVE_META_DIR / f"{filename}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8")
    except Exception as e:
        print(f"  [creative] metadata write failed for {filename}: {e}")


def creation_metadata(filename: str) -> Optional[Dict[str, Any]]:
    """Read the generation metadata for a creation file, or None."""
    try:
        p = CREATIVE_META_DIR / f"{Path(filename).name}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _save_bytes(data: bytes, filename: str) -> Path:
    CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
    dest = CREATIONS_DIR / filename
    dest.write_bytes(data)
    return dest


def _file_record(filename: str, kind: str) -> Dict[str, str]:
    return {
        "filename": filename,
        "kind": kind,
        "url": f"/api/creations/{filename}",
        "framed_url": f"/creation/{filename}",
        "path": str(CREATIONS_DIR / filename),
    }


def _notify(filename: str) -> None:
    """Fire the standard creation-complete notification (orb handled here)."""
    try:
        from agent_friday.services.creations import _notify_creation
        _notify_creation(filename, orb_pid=None)
    except Exception as e:
        print(f"  [creative] notify failed for {filename}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  CLIENT
# ═══════════════════════════════════════════════════════════════════════════

def is_available() -> bool:
    """True if a Gemini API key is configured (creative generation is possible)."""
    return bool(getattr(core, "GEMINI_API_KEY", ""))


def _client():
    """Construct a google-genai client, or raise a clear error. Lazy import."""
    if not is_available():
        raise RuntimeError(
            "No Gemini API key configured. Creative generation needs GEMINI_API_KEY "
            "(set via Settings → API Keys or as an environment variable).")
    from google import genai  # lazy — import-safe without the SDK
    return genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret


def _unavailable(kind: str) -> Dict[str, Any]:
    return {
        "status": "unavailable",
        "message": (f"{kind} generation needs a Gemini API key. Set GEMINI_API_KEY "
                    f"via Settings → API Keys or as an environment variable, then try again."),
    }


def _write_creative_provenance(file_rec: Dict[str, str], kind: str, prompt: str,
                               model: str, api_model: str,
                               demo: bool = False, license=None) -> None:
    """Sign a C2PA ContentCredential for a generated artifact (Layer 2).
    Best-effort — a provenance failure never breaks a generation. ``license`` is
    the creator's per-piece choice (terms + optional price); None → the
    conservative all-rights-reserved default."""
    try:
        from services import provenance
        tool = {"tool": f"creative_engine.generate_{kind}", "model": model,
                "api_model": api_model,
                "prompt_hash": provenance.hash_text(prompt)}
        if demo:
            tool["demo"] = True
        provenance.write(file_rec["path"], tool_chain=[tool], media_type=kind,
                         license=license)
    except Exception:
        pass


def _demo_creation(kind: str, prompt: str, model: str, api_model: str,
                   project_id: Optional[str] = None, license=None) -> Dict[str, Any]:
    """Graceful degradation when cloud generation is unavailable: write a real
    artifact describing what WOULD be generated (instead of breaking), sign its
    provenance, and surface it in the gallery. Used by the autonomous daily
    creation and the full-production pipeline, never by the bare no-key API call
    (which keeps returning 'unavailable')."""
    orb = _orb_start(f"{kind.title()} (demo mode)…", icon="🎨",
                     name=f"{kind.title()} — demo")
    try:
        lines = [
            f"# 🎨 Friday — {kind.title()} (demo preview)",
            "",
            f"> Cloud {kind} generation is unavailable (no Gemini key). This "
            f"describes what Friday *would* render with **{model}** "
            f"({api_model}). Add a Gemini key to produce the real {kind}.",
            "",
            f"**Prompt:** {prompt}",
        ]
        fname = f"friday-{kind}-demo-{_timestamp()}-{uuid.uuid4().hex[:4]}.md"
        _save_bytes("\n".join(lines).encode("utf-8"), fname)
        rec = _file_record(fname, kind)
        _write_metadata(fname, {"kind": kind, "demo": True, "prompt": prompt,
                                "model": model, "api_model": api_model,
                                "created": datetime.now().isoformat()})
        _write_creative_provenance(rec, kind, prompt, model, api_model, demo=True,
                                   license=license)
        if project_id:
            try:
                from services import creative_memory
                creative_memory.add_asset(project_id, fname)
            except Exception:
                pass
        _orb_update(orb, status="completed", progress=1.0, label="Demo ready")
        _defer(3.0, _safe_remove, orb)
        _notify(fname)
        return {"status": "demo", "kind": kind, "files": [rec], "model": model,
                "api_model": api_model, "prompt": prompt,
                "message": (f"Cloud {kind} is unavailable — wrote a demo preview "
                            f"describing it instead.")}
    except Exception as e:
        _orb_fail(orb)
        return {"status": "unavailable",
                "message": f"{kind} unavailable; demo mode failed: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
#  IMAGE GENERATION  (Nano Banana Pro / Nano Banana 2)
# ═══════════════════════════════════════════════════════════════════════════

# Named style presets appended to the prompt. "none" leaves the prompt verbatim.
IMAGE_STYLES = {
    "none": "",
    "photorealistic": "Photorealistic, natural lighting, high detail, 35mm photograph.",
    "cinematic": "Cinematic, dramatic lighting, shallow depth of field, film still.",
    "digital-art": "Polished digital art, vivid colors, clean rendering.",
    "watercolor": "Soft watercolor painting, gentle washes, textured paper.",
    "oil-painting": "Rich oil painting, visible brushstrokes, classical composition.",
    "anime": "Anime / manga illustration style, crisp linework, cel shading.",
    "3d-render": "High-quality 3D render, physically-based materials, soft global illumination.",
    "neon": "Neon-noir, glowing accents, dark background, cyberpunk palette.",
    "minimalist": "Minimalist, flat design, lots of negative space, limited palette.",
    "sketch": "Pencil sketch, hand-drawn linework, monochrome.",
}


def _compose_scene_dna_prompt(prompt: str, scene_dna: Optional[dict],
                              project_id: Optional[str]) -> str:
    """Fold a Scene DNA (layered prompt) and the project's Series Bible into the
    flat prompt. Character names in the DNA are expanded to their canonical
    visual descriptions from the Bible so a named character renders consistently.

    Returns the combined prompt (the explicit ``prompt`` is appended as an extra
    constraint when both are present). Best-effort: a malformed DNA or a missing
    project never breaks generation — it just falls back to the plain prompt.
    """
    if not scene_dna:
        return prompt
    try:
        from services import scene_dna as _sd
        char_desc = {}
        names = _sd.SceneDNA.from_dict(scene_dna).character_names()
        if project_id and names:
            try:
                from services import creative_memory
                char_desc = creative_memory.character_context(project_id, names)
            except Exception:
                char_desc = {}
        rendered = _sd.render(scene_dna, character_descriptions=char_desc)
        if rendered and prompt.strip():
            return f"{rendered} {prompt.strip()}"
        return rendered or prompt
    except Exception:
        return prompt


def _compose_image_prompt(prompt: str, style: Optional[str], aspect_ratio: str) -> str:
    parts = [prompt.strip()]
    style_text = IMAGE_STYLES.get((style or "none").strip().lower())
    if style_text is None and style:                 # custom free-text style
        style_text = style.strip()
    if style_text:
        parts.append(f"Style: {style_text}")
    if aspect_ratio and aspect_ratio in IMAGE_ASPECT_RATIOS:
        parts.append(f"Aspect ratio {aspect_ratio}.")
    return " ".join(parts)


def _image_config(types, aspect_ratio: str):
    """Build a GenerateContentConfig for image output, degrading gracefully when
    the installed SDK lacks the newer image_config / ImageConfig fields."""
    base = {"response_modalities": ["TEXT", "IMAGE"]}
    if aspect_ratio in IMAGE_ASPECT_RATIOS:
        try:
            return types.GenerateContentConfig(
                image_config=types.ImageConfig(aspect_ratio=aspect_ratio), **base)
        except Exception:
            pass
    return types.GenerateContentConfig(**base)


def generate_image(prompt: str, *, model: Optional[str] = None,
                   aspect_ratio: str = "1:1", style: Optional[str] = None,
                   n: int = 1, session_ctx: Optional[dict] = None,
                   scene_dna: Optional[dict] = None,
                   project_id: Optional[str] = None,
                   allow_demo: bool = False, license=None) -> Dict[str, Any]:
    """Generate one or more images from a text prompt via a Gemini image model.

    scene_dna: an optional layered Scene DNA (services/scene_dna) — its setting/
        characters/action/mood/style layers compose into the prompt, and named
        characters are expanded to their Series-Bible looks (via project_id).
    project_id: when given, generated files are also attached to that creative
        project's asset gallery (services/creative_memory).

    Returns {status:'ok', files:[...], model, api_model, prompt, ...} on success,
    or {status:'blocked'|'unavailable'|'error', ...}. Never raises.
    """
    prompt = _compose_scene_dna_prompt(prompt or "", scene_dna, project_id)
    allowed, reason = check_content_safety(prompt)
    if not allowed:
        return {"status": "blocked", "reason": reason}
    if not is_available():
        if allow_demo:
            return _demo_creation("image", prompt, model or DEFAULT_IMAGE_MODEL,
                                  resolve_image_model(model), project_id, license)
        return _unavailable("Image")

    try:
        n = max(1, min(int(n), 4))
    except (TypeError, ValueError):
        n = 1
    if aspect_ratio not in IMAGE_ASPECT_RATIOS:
        aspect_ratio = "1:1"
    api_model = resolve_image_model(model)
    full_prompt = _compose_image_prompt(prompt, style, aspect_ratio)

    orb = _orb_start(f"Generating image — {(model or DEFAULT_IMAGE_MODEL)}…",
                     icon="🎨", name="Generating image")
    try:
        from google.genai import types
        client = _client()
        _orb_update(orb, progress=0.3, label="Painting…")

        files: List[Dict[str, str]] = []
        for i in range(n):
            response = client.models.generate_content(
                model=api_model,
                contents=full_prompt,
                config=_image_config(types, aspect_ratio),
            )
            saved = _extract_and_save_images(response, prompt, single=True)
            files.extend(saved)
            _orb_update(orb, progress=0.3 + 0.6 * ((i + 1) / n),
                       label=f"Saved {len(files)} image(s)…")

        if not files:
            _orb_fail(orb)
            return {"status": "error",
                    "message": "The model returned no image. Try rephrasing the prompt."}

        created = datetime.now().isoformat()
        for f in files:
            _write_metadata(f["filename"], {
                "kind": "image", "prompt": prompt, "full_prompt": full_prompt,
                "model": model or DEFAULT_IMAGE_MODEL, "api_model": api_model,
                "aspect_ratio": aspect_ratio, "style": style or "none",
                "created": created,
            })
            _write_creative_provenance(f, "image", prompt,
                                       model or DEFAULT_IMAGE_MODEL, api_model,
                                       license=license)
        # Attach to the creative project's asset gallery when one is targeted.
        if project_id:
            try:
                from services import creative_memory
                for f in files:
                    creative_memory.add_asset(project_id, f["filename"])
            except Exception:
                pass
        # Complete the orb and notify per file (notification handles its own UX).
        _orb_update(orb, status="completed", progress=1.0, label="Done")
        _defer(3.0, _safe_remove, orb)
        for f in files:
            _notify(f["filename"])

        return {"status": "ok", "kind": "image", "files": files,
                "model": model or DEFAULT_IMAGE_MODEL, "api_model": api_model,
                "prompt": prompt, "aspect_ratio": aspect_ratio,
                "style": style or "none"}
    except Exception as e:
        _orb_fail(orb)
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"Image generation failed: {e}"}


def _extract_and_save_images(response, prompt: str, single: bool = False) -> List[Dict[str, str]]:
    """Pull inline image parts out of a generate_content response and save them."""
    out: List[Dict[str, str]] = []
    try:
        candidates = response.candidates or []
    except Exception:
        candidates = []
    for cand in candidates:
        parts = getattr(getattr(cand, "content", None), "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if not inline or not getattr(inline, "data", None):
                continue
            mime = getattr(inline, "mime_type", "") or ""
            if mime and not mime.startswith("image/"):
                continue
            ext = _ext_for_mime(mime, "png")
            fname = f"friday-image-{_timestamp()}-{uuid.uuid4().hex[:4]}.{ext}"
            data = inline.data
            if isinstance(data, str):                # some SDKs hand back base64
                import base64
                data = base64.b64decode(data)
            _save_bytes(data, fname)
            out.append(_file_record(fname, "image"))
            if single:
                return out
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  VIDEO GENERATION  (Google Veo — text-to-video & image-to-video)
# ═══════════════════════════════════════════════════════════════════════════

# Veo runs as a long-running operation. We poll it and drive the orb's progress
# bar toward a soft estimate so the UI shows motion during the (often 1-3 min)
# render. _VIDEO_EST_SECONDS is the estimate shown to the user, not a timeout.
_VIDEO_EST_SECONDS = 90
_VIDEO_POLL_SECONDS = 8
_VIDEO_MAX_WAIT_SECONDS = 600


def generate_video(prompt: str, *, model: Optional[str] = None,
                   aspect_ratio: str = "16:9", duration_seconds: Optional[int] = None,
                   image_path: Optional[str] = None, image_bytes: Optional[bytes] = None,
                   image_mime: Optional[str] = None,
                   session_ctx: Optional[dict] = None,
                   scene_dna: Optional[dict] = None,
                   project_id: Optional[str] = None,
                   allow_demo: bool = False, license=None) -> Dict[str, Any]:
    """Generate a video from a text prompt (and optionally a seed image) via Veo.

    Pass image_path or image_bytes for image-to-video. scene_dna/project_id are
    handled exactly as in generate_image() — the layered prompt + Series Bible
    compose into the prompt, and outputs attach to the project gallery. Returns
    the same envelope shape as generate_image(). Never raises.
    """
    prompt = _compose_scene_dna_prompt(prompt or "", scene_dna, project_id)
    allowed, reason = check_content_safety(prompt)
    if not allowed:
        return {"status": "blocked", "reason": reason}
    if not is_available():
        if allow_demo:
            return _demo_creation("video", prompt, model or DEFAULT_VIDEO_MODEL,
                                  resolve_video_model(model), project_id, license)
        return _unavailable("Video")

    if aspect_ratio not in VIDEO_ASPECT_RATIOS:
        aspect_ratio = "16:9"
    api_model = resolve_video_model(model)

    # Resolve a seed image (image-to-video) from a path if bytes weren't given.
    seed_bytes, seed_mime = image_bytes, image_mime
    if seed_bytes is None and image_path:
        seed_bytes, seed_mime = _load_seed_image(image_path)
        if seed_bytes is None:
            return {"status": "error",
                    "message": f"Could not read seed image: {image_path}"}

    mode = "image-to-video" if seed_bytes else "text-to-video"
    orb = _orb_start(f"Generating video (~{_VIDEO_EST_SECONDS // 60 or 1}-2 min)…",
                     icon="🎬", name="Generating video")
    try:
        from google.genai import types
        client = _client()
        _orb_update(orb, progress=0.1, label="Submitting to Veo…")

        cfg_kwargs: Dict[str, Any] = {"aspect_ratio": aspect_ratio,
                                      "number_of_videos": 1}
        if duration_seconds:
            try:
                cfg_kwargs["duration_seconds"] = int(duration_seconds)
            except (TypeError, ValueError):
                pass
        # person_generation default keeps Veo's own policy in force.
        config = _build_video_config(types, cfg_kwargs)

        gen_kwargs: Dict[str, Any] = {"model": api_model, "prompt": prompt,
                                      "config": config}
        if seed_bytes:
            gen_kwargs["image"] = _seed_image_obj(types, seed_bytes, seed_mime)

        operation = client.models.generate_videos(**gen_kwargs)

        # ── Poll the long-running operation ──
        waited = 0
        while not _op_done(operation) and waited < _VIDEO_MAX_WAIT_SECONDS:
            time.sleep(_VIDEO_POLL_SECONDS)
            waited += _VIDEO_POLL_SECONDS
            operation = client.operations.get(operation)
            # Soft progress: ramp 0.1 → 0.9 over the estimate, then hold.
            frac = min(0.9, 0.1 + 0.8 * (waited / max(1, _VIDEO_EST_SECONDS)))
            _orb_update(orb, progress=frac,
                       label=f"Rendering… {waited}s elapsed")

        if not _op_done(operation):
            _orb_fail(orb)
            return {"status": "error",
                    "message": f"Video generation timed out after {_VIDEO_MAX_WAIT_SECONDS}s."}

        _orb_update(orb, progress=0.92, label="Downloading video…")
        files = _extract_and_save_videos(operation, client, prompt)
        if not files:
            _orb_fail(orb)
            return {"status": "error",
                    "message": "Veo finished but returned no video (it may have been "
                               "filtered). Try a different prompt."}

        created = datetime.now().isoformat()
        for f in files:
            _write_metadata(f["filename"], {
                "kind": "video", "mode": mode, "prompt": prompt,
                "model": model or DEFAULT_VIDEO_MODEL, "api_model": api_model,
                "aspect_ratio": aspect_ratio,
                "duration_seconds": duration_seconds,
                "seed_image": Path(image_path).name if image_path else None,
                "created": created,
            })
            _write_creative_provenance(f, "video", prompt,
                                       model or DEFAULT_VIDEO_MODEL, api_model,
                                       license=license)
        if project_id:
            try:
                from services import creative_memory
                for f in files:
                    creative_memory.add_asset(project_id, f["filename"])
            except Exception:
                pass
        _orb_update(orb, status="completed", progress=1.0, label="Done")
        _defer(3.0, _safe_remove, orb)
        for f in files:
            _notify(f["filename"])

        return {"status": "ok", "kind": "video", "mode": mode, "files": files,
                "model": model or DEFAULT_VIDEO_MODEL, "api_model": api_model,
                "prompt": prompt, "aspect_ratio": aspect_ratio,
                "duration_seconds": duration_seconds}
    except Exception as e:
        _orb_fail(orb)
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"Video generation failed: {e}"}


def _build_video_config(types, cfg_kwargs: Dict[str, Any]):
    """Construct a GenerateVideosConfig, dropping any kwarg the installed SDK
    doesn't accept (older SDKs lack duration_seconds, etc.)."""
    try:
        return types.GenerateVideosConfig(**cfg_kwargs)
    except TypeError:
        for k in ("duration_seconds", "number_of_videos", "aspect_ratio"):
            cfg_kwargs.pop(k, None)
            try:
                return types.GenerateVideosConfig(**cfg_kwargs)
            except TypeError:
                continue
        return types.GenerateVideosConfig()


def _seed_image_obj(types, data: bytes, mime: Optional[str]):
    return types.Image(image_bytes=data, mime_type=mime or "image/png")


def _load_seed_image(image_path: str):
    """Load a seed image for image-to-video. Accepts an absolute path, a
    home-relative path, or a bare creation filename in CREATIONS_DIR."""
    try:
        p = Path(image_path).expanduser()
        if not p.exists():
            cand = CREATIONS_DIR / Path(image_path).name
            if cand.exists():
                p = cand
        if not p.exists() or not p.is_file():
            return None, None
        data = p.read_bytes()
        ext = p.suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")
        return data, mime
    except Exception:
        return None, None


def _op_done(operation) -> bool:
    return bool(getattr(operation, "done", False))


def _extract_and_save_videos(operation, client, prompt: str) -> List[Dict[str, str]]:
    """Pull generated videos out of a finished Veo operation and save them.

    Handles SDK shape differences: operation.response vs operation.result, and
    inline video_bytes vs a downloadable file handle.
    """
    container = (getattr(operation, "response", None)
                 or getattr(operation, "result", None))
    videos = getattr(container, "generated_videos", None) or []
    out: List[Dict[str, str]] = []
    for gv in videos:
        video = getattr(gv, "video", None) or gv
        data = _video_bytes(video, client)
        if not data:
            continue
        fname = f"friday-video-{_timestamp()}-{uuid.uuid4().hex[:4]}.mp4"
        _save_bytes(data, fname)
        out.append(_file_record(fname, "video"))
    return out


def _video_bytes(video, client) -> Optional[bytes]:
    """Best-effort extraction of raw MP4 bytes across SDK versions."""
    data = getattr(video, "video_bytes", None)
    if data:
        return data
    # Newer SDK: the bytes live behind a Files download.
    try:
        client.files.download(file=video)
        data = getattr(video, "video_bytes", None)
        if data:
            return data
    except Exception:
        pass
    # Last resort: save() to a temp path then read it back.
    try:
        tmp = CREATIONS_DIR / f".veo-tmp-{uuid.uuid4().hex[:8]}.mp4"
        video.save(str(tmp))
        if tmp.exists():
            data = tmp.read_bytes()
            try:
                tmp.unlink()
            except Exception:
                pass
            return data
    except Exception:
        pass
    return None


def _safe_remove(pid) -> None:
    try:
        core.process_remove(pid)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
#  DISPATCHER — single entry point used by routes + tools.
# ═══════════════════════════════════════════════════════════════════════════

def generate(kind: str, prompt: str, **opts) -> Dict[str, Any]:
    """Dispatch to image/video generation by kind ('image' | 'video')."""
    k = (kind or "").strip().lower()
    if k in ("image", "img", "picture", "photo"):
        return generate_image(
            prompt,
            model=opts.get("model"),
            aspect_ratio=opts.get("aspect_ratio") or "1:1",
            style=opts.get("style"),
            n=opts.get("n", 1),
            session_ctx=opts.get("session_ctx"),
            scene_dna=opts.get("scene_dna"),
            project_id=opts.get("project_id"),
            allow_demo=opts.get("allow_demo", False),
            license=opts.get("license"),
        )
    if k in ("video", "vid", "clip", "movie"):
        return generate_video(
            prompt,
            model=opts.get("model"),
            aspect_ratio=opts.get("aspect_ratio") or "16:9",
            duration_seconds=opts.get("duration_seconds"),
            image_path=opts.get("image_path"),
            image_bytes=opts.get("image_bytes"),
            image_mime=opts.get("image_mime"),
            session_ctx=opts.get("session_ctx"),
            scene_dna=opts.get("scene_dna"),
            project_id=opts.get("project_id"),
            allow_demo=opts.get("allow_demo", False),
            license=opts.get("license"),
        )
    if k in ("music", "song", "audio", "track"):
        from services import music_engine
        return music_engine.generate(prompt, **opts)
    return {"status": "error", "message": f"Unknown creative kind: {kind!r}"}
