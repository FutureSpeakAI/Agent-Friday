"""
Agent Friday — Creative Pipeline Engine
FutureSpeak.AI · Asimov's Mind

Chain workspaces/agents into a multi-stage pipeline with TYPED input/output
contracts. A pipeline is an ordered list of stages; each stage names a workspace
(which sets its temperature + context), a run mode (text / agent / image), an
instruction template, an input schema (what it consumes) and an output schema
(what it produces). Stage output flows into a shared context dict the next
stage's instruction can reference via {{key}} placeholders.

  Research → Brief → Draft → Review

Execution surfaces MILESTONE progress, not a spinner: the process orb reports
"Stage 3/4 — Draft" (and, for item-producing stages, "scene 3 of 8"). Any stage
can be a CHECKPOINT: the run pauses in ``awaiting_checkpoint`` so the user can
inspect/edit the accumulated context before resuming — intervention at any step.

Runs persist to ~/.friday/pipelines/runs/<run_id>.json so a long pipeline
survives a checkpoint pause (and a server restart). Custom pipeline definitions
persist to ~/.friday/pipelines/defs/<id>.json alongside the built-in templates.

Design rules: lazy LLM imports (import-safe / offline), never raises out of a
stage (a stage failure marks the run ``failed`` with the error), and a synchronous
``run()`` for tests plus a threaded ``start_async()`` for the UI.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import core
from core import FRIDAY_DIR

PIPELINES_DIR = FRIDAY_DIR / "pipelines"
RUNS_DIR = PIPELINES_DIR / "runs"
DEFS_DIR = PIPELINES_DIR / "defs"

_LOCK = threading.RLock()

# Run lifecycle states.
PENDING = "pending"
RUNNING = "running"
AWAITING_CHECKPOINT = "awaiting_checkpoint"
COMPLETED = "completed"
FAILED = "failed"


# ═══════════════════════════════════════════════════════════════════════════
#  BUILT-IN PIPELINE TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════
# Each stage: id, name, workspace (drives temperature/context), mode, instruction
# (a template over the accumulated context), input_schema, output_schema, output_key,
# checkpoint (pause after this stage for user review).

_RESEARCH_BRIEF_DRAFT_REVIEW = {
    "id": "research-brief-draft-review",
    "name": "Research → Brief → Draft → Review",
    "description": "The classic content pipeline: gather facts, distill a brief, "
                   "write a draft, then critique and polish it.",
    "stages": [
        {
            "id": "research", "name": "Research", "workspace": "research",
            "mode": "text", "output_key": "research",
            "instruction": "Research the topic thoroughly and return a concise, "
                           "well-organized set of factual findings (bullet points, "
                           "key facts, angles, and any tensions or open questions).\n\n"
                           "TOPIC: {{topic}}",
            "input_schema": {"required": ["topic"],
                             "properties": {"topic": {"type": "string"}}},
            "output_schema": {"required": ["research"],
                              "properties": {"research": {"type": "string"}}},
            "checkpoint": False,
        },
        {
            "id": "brief", "name": "Brief", "workspace": "content",
            "mode": "text", "output_key": "brief",
            "instruction": "Using the research below, write a tight creative brief: "
                           "the core message, the audience, the desired tone, and "
                           "3–5 must-hit points.\n\nTOPIC: {{topic}}\n\n"
                           "RESEARCH:\n{{research}}",
            "input_schema": {"required": ["research"]},
            "output_schema": {"required": ["brief"]},
            "checkpoint": True,   # natural place to let the user steer
        },
        {
            "id": "draft", "name": "Draft", "workspace": "content",
            "mode": "text", "output_key": "draft",
            "instruction": "Write the full draft that delivers on the brief.\n\n"
                           "BRIEF:\n{{brief}}\n\nResearch for reference:\n{{research}}",
            "input_schema": {"required": ["brief"]},
            "output_schema": {"required": ["draft"]},
            "checkpoint": False,
        },
        {
            "id": "review", "name": "Review", "workspace": "review",
            "mode": "text", "output_key": "final",
            "instruction": "Critique the draft against the brief, then return the "
                           "polished final version (fix weak spots, tighten prose, "
                           "ensure every brief point is hit). Output the final text "
                           "only.\n\nBRIEF:\n{{brief}}\n\nDRAFT:\n{{draft}}",
            "input_schema": {"required": ["draft"]},
            "output_schema": {"required": ["final"]},
            "checkpoint": False,
        },
    ],
}

_STORYBOARD = {
    "id": "concept-storyboard-shots",
    "name": "Concept → Storyboard → Shot Prompts",
    "description": "Turn a logline into a storyboard, then into per-shot Scene DNA "
                   "prompts ready for image/video generation.",
    "stages": [
        {
            "id": "concept", "name": "Concept", "workspace": "studio",
            "mode": "text", "output_key": "concept",
            "instruction": "Expand this logline into a vivid one-paragraph concept "
                           "with a clear visual world and tone.\n\nLOGLINE: {{logline}}",
            "input_schema": {"required": ["logline"]},
            "output_schema": {"required": ["concept"]},
            "checkpoint": True,
        },
        {
            "id": "storyboard", "name": "Storyboard", "workspace": "studio",
            "mode": "text", "output_key": "storyboard",
            "instruction": "Break the concept into a numbered storyboard of distinct "
                           "beats/scenes. One line each.\n\nCONCEPT:\n{{concept}}",
            "input_schema": {"required": ["concept"]},
            "output_schema": {"required": ["storyboard"]},
            "checkpoint": False,
        },
        {
            "id": "shots", "name": "Shot Prompts", "workspace": "studio",
            "mode": "text", "output_key": "shots",
            "instruction": "For each storyboard beat, write a rich shot prompt "
                           "(setting, action, mood, camera) suitable for an image "
                           "generator.\n\nSTORYBOARD:\n{{storyboard}}",
            "input_schema": {"required": ["storyboard"]},
            "output_schema": {"required": ["shots"]},
            "checkpoint": False,
        },
    ],
}

# The full production chain (§4): script → scene DNA → keyframe → clip → score →
# timeline → export. Each stage reads the structured layers; the two cost-heavy
# steps (video gen, final export) are checkpoints so the user confirms spend.
# Without a cloud key the image/video/music stages produce demo previews and the
# timeline stage notes there's nothing to render — the chain never hard-fails.
_FULL_PRODUCTION = {
    "id": "full-production",
    "name": "Full Production — Script → Video → Music → Cut",
    "description": "The complete chain: a logline becomes a scored micro-film. "
                   "Script, then a Scene DNA, then a keyframe (Imagen), a video "
                   "clip seeded by that keyframe (Veo), a score (Lyria 3) driven "
                   "by the audio layer, then a timeline assembly + export (FFmpeg).",
    "stages": [
        {
            "id": "script", "name": "Script", "workspace": "studio",
            "mode": "text", "output_key": "script",
            "instruction": "Write a tight 4–6 beat micro-film script (≤200 words) "
                           "for this logline. Give it a clear arc and a strong "
                           "opening image.\n\nLOGLINE: {{logline}}",
            "input_schema": {"required": ["logline"]},
            "output_schema": {"required": ["script"]},
            "checkpoint": False,
        },
        {
            "id": "scene", "name": "Scene DNA", "workspace": "studio",
            "mode": "text", "output_key": "scene_dna",
            "instruction": "Return ONLY a JSON object (no prose) describing the "
                           "OPENING shot as a Scene DNA with these keys: setting, "
                           "characters (list), action, mood, audio, style, "
                           "technical. The 'audio' layer must describe the score "
                           "(instruments, tempo, feel) — it drives music "
                           "generation.\n\nSCRIPT:\n{{script}}",
            "input_schema": {"required": ["script"]},
            "output_schema": {"required": ["scene_dna"]},
            "checkpoint": True,    # human reviews the look before any spend
        },
        {
            "id": "keyframe", "name": "Keyframe", "workspace": "studio",
            "mode": "image", "output_key": "keyframe", "style": "cinematic",
            "aspect_ratio": "16:9",
            "instruction": "A single cinematic keyframe for the opening shot.\n\n"
                           "{{scene_dna}}",
            "input_schema": {"required": ["scene_dna"]},
            "output_schema": {"required": ["keyframe"]},
            "checkpoint": False,
        },
        {
            "id": "clip", "name": "Video clip", "workspace": "studio",
            "mode": "video", "output_key": "clip", "seed_from": "keyframe_file",
            "aspect_ratio": "16:9", "duration_seconds": 6,
            "instruction": "Animate the opening shot with subtle, cinematic "
                           "motion.\n\n{{scene_dna}}",
            "input_schema": {"required": ["keyframe"]},
            "output_schema": {"required": ["clip"]},
            "checkpoint": True,    # cost gate — video is the expensive call
        },
        {
            "id": "score", "name": "Music", "workspace": "studio",
            "mode": "music", "output_key": "score", "duration_seconds": 30,
            "instruction": "An original short score for this film.\n\n"
                           "SCRIPT:\n{{script}}",
            "input_schema": {"required": ["clip"]},
            "output_schema": {"required": ["score"]},
            "checkpoint": False,
        },
        {
            "id": "cut", "name": "Timeline", "workspace": "studio",
            "mode": "timeline", "output_key": "production",
            "exports": ["mp4-1080p", "mp4-vertical-9x16"],
            "instruction": "Assemble the clip and score into a finished, exported "
                           "cut.",
            "input_schema": {"required": ["score"]},
            "output_schema": {"required": ["production"]},
            "checkpoint": True,    # final review before the work is published
        },
    ],
}

_BUILTIN_TEMPLATES = {t["id"]: t for t in (
    _RESEARCH_BRIEF_DRAFT_REVIEW, _STORYBOARD, _FULL_PRODUCTION)}


# ═══════════════════════════════════════════════════════════════════════════
#  TEMPLATE / DEFINITION REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

def list_templates() -> List[Dict[str, Any]]:
    """Built-in + user-defined pipeline definitions (summaries)."""
    out = []
    seen = set()
    for d in list(_BUILTIN_TEMPLATES.values()) + _load_custom_defs():
        if d["id"] in seen:
            continue
        seen.add(d["id"])
        out.append({"id": d["id"], "name": d.get("name", d["id"]),
                    "description": d.get("description", ""),
                    "stages": [s["name"] for s in d.get("stages", [])],
                    "builtin": d["id"] in _BUILTIN_TEMPLATES})
    return out


def get_pipeline(pipeline_id: str) -> Optional[Dict[str, Any]]:
    """Full pipeline definition by id (built-in or custom)."""
    if pipeline_id in _BUILTIN_TEMPLATES:
        return json.loads(json.dumps(_BUILTIN_TEMPLATES[pipeline_id]))  # deep copy
    p = DEFS_DIR / f"{pipeline_id}.json"
    return _read_json(p)


def register_pipeline(definition: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a custom pipeline definition. Validates the stage shape.
    Returns {status, id} or {status:error, message}."""
    ok, err = _validate_definition(definition)
    if not ok:
        return {"status": "error", "message": err}
    pid = definition.get("id") or f"pipeline-{uuid.uuid4().hex[:8]}"
    definition["id"] = pid
    definition.setdefault("name", pid)
    _write_json(DEFS_DIR / f"{pid}.json", definition)
    return {"status": "ok", "id": pid}


def _load_custom_defs() -> List[Dict[str, Any]]:
    out = []
    if DEFS_DIR.exists():
        for f in DEFS_DIR.glob("*.json"):
            d = _read_json(f)
            if d:
                out.append(d)
    return out


def _validate_definition(d: Dict[str, Any]) -> tuple:
    if not isinstance(d, dict):
        return False, "definition must be an object"
    stages = d.get("stages")
    if not isinstance(stages, list) or not stages:
        return False, "definition needs a non-empty 'stages' list"
    for i, s in enumerate(stages):
        if not isinstance(s, dict):
            return False, f"stage {i} must be an object"
        if not s.get("instruction"):
            return False, f"stage {i} ({s.get('name', '?')}) needs an 'instruction'"
        if not s.get("output_key"):
            return False, f"stage {i} ({s.get('name', '?')}) needs an 'output_key'"
    return True, None


# ═══════════════════════════════════════════════════════════════════════════
#  TYPED CONTRACT VALIDATION  (lightweight; no jsonschema dependency)
# ═══════════════════════════════════════════════════════════════════════════

_TYPE_MAP = {
    "string": str, "str": str, "number": (int, float), "integer": int,
    "int": int, "boolean": bool, "bool": bool, "array": list, "list": list,
    "object": dict, "dict": dict,
}


def validate_against_schema(data: Dict[str, Any],
                            schema: Optional[Dict[str, Any]]) -> tuple:
    """Validate ``data`` against a tiny subset of JSON Schema: ``required`` keys
    and per-property ``type``. Returns (ok, [errors]). A null/empty schema passes.
    """
    if not schema:
        return True, []
    errors: List[str] = []
    data = data or {}
    for key in schema.get("required", []):
        if key not in data or data[key] in (None, ""):
            errors.append(f"missing required field '{key}'")
    props = schema.get("properties") or {}
    for key, spec in props.items():
        if key not in data or data[key] is None:
            continue
        t = (spec or {}).get("type")
        py = _TYPE_MAP.get(t)
        if py and not isinstance(data[key], py):
            errors.append(f"field '{key}' should be {t}")
    return (not errors), errors


# ═══════════════════════════════════════════════════════════════════════════
#  TEMPLATING
# ═══════════════════════════════════════════════════════════════════════════

_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _render(template: str, context: Dict[str, Any]) -> str:
    """Fill {{key}} placeholders from the context. Unknown keys are left as an
    empty string so a template never crashes on a missing upstream value."""
    def sub(m):
        val = context.get(m.group(1))
        if val is None:
            return ""
        return val if isinstance(val, str) else json.dumps(val, default=str)
    return _PLACEHOLDER.sub(sub, template or "")


# ═══════════════════════════════════════════════════════════════════════════
#  RUN IO
# ═══════════════════════════════════════════════════════════════════════════

def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")


def _run_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    return _read_json(_run_path(run_id))


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    out = []
    if RUNS_DIR.exists():
        for f in RUNS_DIR.glob("*.json"):
            r = _read_json(f)
            if not r:
                continue
            out.append({"run_id": r.get("run_id"), "pipeline_id": r.get("pipeline_id"),
                        "name": r.get("name"), "state": r.get("state"),
                        "stage_index": r.get("stage_index"),
                        "total_stages": len(r.get("stages", [])),
                        "created": r.get("created"), "updated": r.get("updated"),
                        "project_id": r.get("project_id")})
    out.sort(key=lambda r: r.get("updated") or "", reverse=True)
    return out[:limit]


def _save_run(run: Dict[str, Any]) -> Dict[str, Any]:
    run["updated"] = datetime.now().isoformat()
    _write_json(_run_path(run["run_id"]), run)
    return run


# ═══════════════════════════════════════════════════════════════════════════
#  RUN CREATION
# ═══════════════════════════════════════════════════════════════════════════

def create_run(pipeline_id: str, initial_input: Optional[Dict[str, Any]] = None,
               *, project_id: str = "") -> Dict[str, Any]:
    """Create a pipeline run from a definition + initial context. Does NOT start
    executing — call run()/advance()/start_async(). Returns the run dict, or
    {status:error} if the pipeline is unknown."""
    definition = get_pipeline(pipeline_id)
    if not definition:
        return {"status": "error", "message": f"Unknown pipeline: {pipeline_id!r}"}
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    run = {
        "run_id": run_id,
        "pipeline_id": pipeline_id,
        "name": definition.get("name", pipeline_id),
        "project_id": (project_id or "").strip(),
        "state": PENDING,
        "stage_index": 0,                       # next stage to execute
        "stages": definition["stages"],
        "context": dict(initial_input or {}),   # accumulated typed context
        "milestones": [],                       # human-readable progress trail
        "stage_results": [],                    # per-stage record
        "error": None,
        "orb_id": None,
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
    }
    _save_run(run)
    return run


# ═══════════════════════════════════════════════════════════════════════════
#  STAGE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════

def _stage_executor(mode: str) -> Callable:
    return {
        "text": _exec_text_stage,
        "agent": _exec_agent_stage,
        "image": _exec_image_stage,
        "music": _exec_music_stage,        # NEW — Lyria 3 score (reads SceneDNA.audio)
        "video": _exec_video_stage,        # NEW — Veo clip (image→video continuity)
        "timeline": _exec_timeline_stage,  # NEW — FFmpeg assembly + export
    }.get((mode or "text").lower(), _exec_text_stage)


# Media file extensions a timeline stage can actually render (excludes the .md
# demo artifacts a no-cloud stage produces, so the cut never feeds ffmpeg junk).
_REAL_VIDEO_EXT = (".mp4", ".webm", ".mov")
_REAL_AUDIO_EXT = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


def _scene_dna_from_context(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull a Scene DNA dict out of the run context. A storyboard stage may have
    produced it as a JSON string (possibly fenced) — parse it best-effort so the
    keyframe/clip/music stages get a real layered prompt + the audio layer."""
    sd = context.get("scene_dna")
    if isinstance(sd, dict):
        return sd
    if isinstance(sd, str) and sd.strip():
        m = re.search(r"\{.*\}", sd, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _exec_text_stage(stage, prompt, context):
    """Single-shot text generation, routed through the user's provider with the
    stage's workspace (→ temperature) applied. Returns the produced text."""
    from services.model_router import _generate_text, _get_friday_system_prompt
    ws = stage.get("workspace") or ""
    try:
        system = _get_friday_system_prompt(keywords=prompt[:400], workspace=ws)
    except Exception:
        system = None
    temperature = stage.get("temperature")
    return _generate_text([{"role": "user", "content": prompt}],
                          system=system, max_tokens=stage.get("max_tokens", 4096),
                          temperature=temperature, workspace=ws)


def _exec_agent_stage(stage, prompt, context):
    """Tool-using agentic stage (background-task style). Falls back to text gen
    if the agent entry point is unavailable."""
    try:
        from services.agent import _generate_agent
        ws = stage.get("workspace") or ""
        text, _trace = _generate_agent(
            [{"role": "user", "content": prompt}],
            max_tokens=stage.get("max_tokens", 8192),
            temperature=stage.get("temperature"), workspace=ws,
            orb_label=f"Pipeline · {stage.get('name', 'stage')}")
        return text or ""
    except Exception:
        return _exec_text_stage(stage, prompt, context)


def _exec_image_stage(stage, prompt, context):
    """Generate an image for the stage; returns a short descriptor string and
    stashes the file record into the context under '<output_key>_file'."""
    from services import creative_engine
    res = creative_engine.generate_image(
        prompt, style=stage.get("style"),
        aspect_ratio=stage.get("aspect_ratio") or "1:1",
        scene_dna=_scene_dna_from_context(context),
        project_id=context.get("project_id") or None,
        allow_demo=True)
    if res.get("status") in ("ok", "demo") and res.get("files"):
        f = res["files"][0]
        context[f"{stage['output_key']}_file"] = f
        return f"Generated image: {f.get('filename')} ({f.get('url')})"
    return f"[image generation {res.get('status')}] {res.get('message') or res.get('reason') or ''}"


def _exec_video_stage(stage, prompt, context):
    """Generate a Veo clip. For cross-scene continuity, seeds from an upstream
    keyframe (image→video) when ``seed_from`` names a file record in context."""
    from services import creative_engine
    seed_key = stage.get("seed_from")
    seed = None
    if seed_key and isinstance(context.get(seed_key), dict):
        seed = context[seed_key].get("filename")
    res = creative_engine.generate_video(
        prompt, model=stage.get("model"),
        aspect_ratio=stage.get("aspect_ratio") or "16:9",
        duration_seconds=stage.get("duration_seconds"),
        image_path=seed,
        scene_dna=_scene_dna_from_context(context),
        project_id=context.get("project_id") or None,
        allow_demo=True)
    if res.get("status") in ("ok", "demo") and res.get("files"):
        f = res["files"][0]
        context[f"{stage['output_key']}_file"] = f
        return f"Generated video: {f.get('filename')} ({f.get('url')})"
    return f"[video generation {res.get('status')}] {res.get('message') or res.get('reason') or ''}"


def _exec_music_stage(stage, prompt, context):
    """Generate a Lyria 3 score. Reads the EXISTING SceneDNA.audio layer as the
    seed (zero new fields), so a storyboard's 'audio:' clause drives the score."""
    from services import music_engine
    res = music_engine.generate_music(
        prompt, model=stage.get("model"),
        mode=stage.get("music_mode") or "instrumental",
        lyrics=context.get("lyrics") or stage.get("lyrics"),
        duration_seconds=stage.get("duration_seconds"),
        scene_dna=_scene_dna_from_context(context),
        project_id=context.get("project_id") or None)
    if res.get("status") in ("ok", "demo") and res.get("files"):
        f = res["files"][0]
        context[f"{stage['output_key']}_file"] = f
        return f"Generated music: {f.get('filename')} ({f.get('url')})"
    return f"[music generation {res.get('status')}] {res.get('message') or res.get('reason') or ''}"


def _exec_timeline_stage(stage, prompt, context):
    """Assemble every real video clip + music track accumulated in the context
    into a finished cut and export it (FFmpeg). The edge list of source clips is
    signed into the production's provenance (Layer 2)."""
    from services import timeline_engine
    video_clips, music_clips = [], []
    for k, v in context.items():
        if not (k.endswith("_file") and isinstance(v, dict) and v.get("filename")):
            continue
        ext = ("." + v["filename"].rsplit(".", 1)[-1]).lower()
        if v.get("kind") == "video" and ext in _REAL_VIDEO_EXT:
            video_clips.append(v)
        elif v.get("kind") == "music" and ext in _REAL_AUDIO_EXT:
            music_clips.append(v)
    tracks = []
    if video_clips:
        tracks.append({"kind": "video", "clips": [
            {"file": v["filename"], "in": 0.0,
             "out": stage.get("clip_seconds", 6),
             "transition_in": {"type": "crossfade", "dur": 0.5}} for v in video_clips]})
    if music_clips:
        tracks.append({"kind": "audio", "clips": [
            {"file": music_clips[0]["filename"], "role": "music",
             "gain_db": -4.0, "fade_out": 1.5}]})
    if not tracks:
        return ("[timeline] no renderable clips/music in context yet — the "
                "upstream image/video stages produced demo previews (no cloud key).")
    timeline = {"fps": 30, "resolution": [1920, 1080], "tracks": tracks,
                "exports": stage.get("exports") or ["mp4-1080p"]}
    res = timeline_engine.compose(timeline, project_id=context.get("project_id") or None)
    if res.get("status") in ("ok", "demo") and res.get("files"):
        f = res["files"][0]
        context[f"{stage['output_key']}_file"] = f
        return f"Composed production: {f.get('filename')} ({f.get('url')})"
    return f"[timeline {res.get('status')}] {res.get('message') or ''}"


def _run_one_stage(run: Dict[str, Any], idx: int,
                   progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    """Execute stage ``idx`` of the run. Validates the input contract, renders +
    runs the stage, validates the output contract, merges output into context,
    and records a stage result + milestone. Returns the stage-result record."""
    stages = run["stages"]
    stage = stages[idx]
    total = len(stages)
    name = stage.get("name", stage.get("id", f"stage{idx}"))
    label = f"Stage {idx + 1}/{total} — {name}"
    if progress:
        progress((idx) / total, label)

    # ── input contract ──
    ok, errs = validate_against_schema(run["context"], stage.get("input_schema"))
    if not ok:
        raise PipelineStageError(
            f"{name}: input contract not satisfied ({'; '.join(errs)})")

    prompt = _render(stage.get("instruction", ""), run["context"])
    output_key = stage.get("output_key") or stage.get("id") or f"stage{idx}"

    executor = _stage_executor(stage.get("mode"))
    produced = executor(stage, prompt, run["context"])
    produced = produced if produced is not None else ""

    # Store the produced value under the stage's output key.
    run["context"][output_key] = produced

    # ── output contract ──
    ok, errs = validate_against_schema(run["context"], stage.get("output_schema"))
    if not ok:
        raise PipelineStageError(
            f"{name}: output contract not satisfied ({'; '.join(errs)})")

    preview = produced if isinstance(produced, str) else json.dumps(produced, default=str)
    record = {
        "stage_id": stage.get("id"), "name": name, "output_key": output_key,
        "preview": preview[:600], "chars": len(preview),
        "ts": datetime.now().isoformat(),
    }
    run["stage_results"].append(record)
    run["milestones"].append(f"✓ {label}")
    if progress:
        progress((idx + 1) / total, f"Completed {label}")
    return record


class PipelineStageError(Exception):
    """A typed-contract or execution failure inside a stage."""


# ═══════════════════════════════════════════════════════════════════════════
#  RUN DRIVER  (sync — stops at checkpoints; resumable)
# ═══════════════════════════════════════════════════════════════════════════

def advance(run_id: str,
            progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    """Execute exactly ONE stage of the run and persist. Returns the run.

    Sets state to AWAITING_CHECKPOINT when the just-finished stage is a
    checkpoint (and isn't the last), COMPLETED at the end, or FAILED on error.
    """
    with _LOCK:
        run = get_run(run_id)
        if not run:
            return {"status": "error", "message": "run not found"}
        if run["state"] in (COMPLETED, FAILED):
            return run
        idx = run["stage_index"]
        if idx >= len(run["stages"]):
            run["state"] = COMPLETED
            return _save_run(run)
        run["state"] = RUNNING
        _save_run(run)
        try:
            _run_one_stage(run, idx, progress)
        except Exception as e:
            run["state"] = FAILED
            run["error"] = str(e)
            run["milestones"].append(f"✗ Stage {idx + 1} failed: {e}")
            return _save_run(run)

        run["stage_index"] = idx + 1
        is_last = run["stage_index"] >= len(run["stages"])
        if is_last:
            run["state"] = COMPLETED
            run["final"] = run["context"].get(run["stages"][-1].get("output_key"))
        elif run["stages"][idx].get("checkpoint"):
            run["state"] = AWAITING_CHECKPOINT
        else:
            run["state"] = RUNNING
        return _save_run(run)


def run(run_id: str, *, until_checkpoint: bool = True,
        progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    """Drive a run forward: execute stages until a checkpoint pause, completion,
    or failure. With until_checkpoint=False, checkpoints are auto-passed (run to
    the end). Returns the final run state of this leg."""
    r = get_run(run_id)
    if not r:
        return {"status": "error", "message": "run not found"}
    guard = 0
    while guard < 100:
        guard += 1
        r = advance(run_id, progress)
        state = r.get("state")
        if state in (COMPLETED, FAILED):
            break
        if state == AWAITING_CHECKPOINT:
            if until_checkpoint:
                break
            # auto-continue past the checkpoint
            r = resume(run_id)
    return r


def resume(run_id: str, edited_context: Optional[Dict[str, Any]] = None,
           *, progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    """Resume a checkpoint-paused run. ``edited_context`` (optional) lets the
    user intervene — edit/override any accumulated value before continuing."""
    with _LOCK:
        r = get_run(run_id)
        if not r:
            return {"status": "error", "message": "run not found"}
        if r["state"] != AWAITING_CHECKPOINT:
            return r
        if edited_context:
            r["context"].update(edited_context)
            r["milestones"].append("✎ User edited context at checkpoint")
        r["state"] = RUNNING
        _save_run(r)
    return r


def intervene(run_id: str, context_updates: Dict[str, Any]) -> Dict[str, Any]:
    """Edit a run's accumulated context at ANY point (not only at a checkpoint).
    Useful for correcting a stage output before re-running downstream."""
    with _LOCK:
        r = get_run(run_id)
        if not r:
            return {"status": "error", "message": "run not found"}
        r["context"].update(context_updates or {})
        r["milestones"].append("✎ User intervened (context edited)")
        return _save_run(r)


# ═══════════════════════════════════════════════════════════════════════════
#  ASYNC DRIVER  (background thread + process orb milestone progress)
# ═══════════════════════════════════════════════════════════════════════════

def start_async(run_id: str, *, until_checkpoint: bool = True) -> Dict[str, Any]:
    """Run the pipeline on a background thread, surfacing milestone progress via
    a holographic process orb. Returns immediately with {status, run_id, orb_id}.

    Under FRIDAY_TESTING the work runs synchronously (no daemon thread) so the
    smoke suite's thread-count assertion holds and tests are deterministic.
    """
    r = get_run(run_id)
    if not r:
        return {"status": "error", "message": "run not found"}

    orb_id = _orb_start(r.get("name", "Pipeline"))
    with _LOCK:
        r = get_run(run_id)
        r["orb_id"] = orb_id
        _save_run(r)

    def _progress(frac: float, label: str):
        if orb_id:
            try:
                core.process_update(orb_id, progress=max(0.0, min(1.0, frac)),
                                   label=label)
            except Exception:
                pass

    def _drive():
        final = run(run_id, until_checkpoint=until_checkpoint, progress=_progress)
        if orb_id:
            try:
                state = final.get("state")
                if state == COMPLETED:
                    core.process_update(orb_id, status="completed", progress=1.0,
                                       label="Pipeline complete")
                elif state == AWAITING_CHECKPOINT:
                    core.process_update(orb_id, progress=final.get("stage_index", 0)
                                       / max(1, len(final.get("stages", []))),
                                       label="Awaiting your review")
                else:
                    core.process_update(orb_id, status="error",
                                       label=f"Pipeline {state}")
                _defer_remove(orb_id)
            except Exception:
                pass

    import os
    if os.environ.get("FRIDAY_TESTING"):
        _drive()
    else:
        t = threading.Thread(target=_drive, daemon=True)
        t.start()
    return {"status": "ok", "run_id": run_id, "orb_id": orb_id}


def _orb_start(name: str) -> Optional[str]:
    try:
        pid = f"pipeline-{uuid.uuid4().hex[:8]}"
        core.process_register(pid, name="Pipeline", label=f"Starting {name}…",
                             category="monitoring", icon="🧩")
        return pid
    except Exception:
        return None


def _defer_remove(orb_id: str) -> None:
    import os
    if os.environ.get("FRIDAY_TESTING"):
        try:
            core.process_remove(orb_id)
        except Exception:
            pass
        return
    t = threading.Timer(3.0, lambda: _safe_remove(orb_id))
    t.daemon = True
    t.start()


def _safe_remove(orb_id: str) -> None:
    try:
        core.process_remove(orb_id)
    except Exception:
        pass
