"""
Agent Friday — Timeline Composition Engine (FFmpeg)
FutureSpeak.AI · Asimov's Mind

The one genuinely new heavy subsystem of Layer 1: assemble a finished work from
clips + audio/music tracks. Everything else in the creative stack composes
existing parts; stitching a timeline is new work.

A timeline is a TYPED, JSON, signable contract (not a raw ffmpeg string) — it
records exactly which signed clips and which music track composed the final work,
so Layer 2 can sign that edge list and Layer 3 can compute collaborative credit.

  • compose(timeline)      — render a timeline to one or more outputs via ffmpeg
                             filter graphs (concat / xfade / amix + ducking /
                             drawtext / scale), one invocation per export profile.
  • export_formats()       — the platform presets (YouTube 16:9, Reel/TikTok 9:16,
                             WebM, GIF preview, audio-only MP3).
  • validate_timeline()    — schema check (reuses the pipeline validator pattern).

FFmpeg is sourced from ``imageio-ffmpeg`` (bundles a binary — a clean
``pip install agent-friday[compose]`` just works on Windows), falling back to a
system ffmpeg on PATH. When neither is present, compose() degrades gracefully:
it still writes the signed timeline JSON + provenance and returns a clear
"install ffmpeg" message instead of breaking.

Design rules: lazy/optional ffmpeg, the filter-graph BUILDER is a pure function
(unit-testable without invoking ffmpeg), process orb on export, never raises.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import agent_friday.core as core
from agent_friday.core import CREATIONS_DIR

from agent_friday.services.creative_engine import (
    _orb_start, _orb_update, _orb_fail, _defer, _safe_remove,
    _write_metadata, _notify, _timestamp,
)


# ═══════════════════════════════════════════════════════════════════════════
#  EXPORT PROFILES — platform presets. (container, w, h, vcodec, acodec, fps?)
# ═══════════════════════════════════════════════════════════════════════════

EXPORT_PROFILES: Dict[str, Dict[str, Any]] = {
    "mp4-1080p":          {"container": "mp4",  "w": 1920, "h": 1080, "v": "libx264", "a": "aac"},
    "youtube-16x9":       {"container": "mp4",  "w": 1920, "h": 1080, "v": "libx264", "a": "aac"},
    "mp4-vertical-9x16":  {"container": "mp4",  "w": 1080, "h": 1920, "v": "libx264", "a": "aac"},
    "instagram-reel":     {"container": "mp4",  "w": 1080, "h": 1920, "v": "libx264", "a": "aac"},
    "tiktok-vertical":    {"container": "mp4",  "w": 1080, "h": 1920, "v": "libx264", "a": "aac"},
    "webm":               {"container": "webm", "w": 1920, "h": 1080, "v": "libvpx-vp9", "a": "libopus"},
    "gif-preview":        {"container": "gif",  "w": 640,  "h": 360,  "v": None, "a": None, "gif": True},
    "audio-mp3":          {"container": "mp3",  "w": None, "h": None, "v": None, "a": "libmp3lame", "audio_only": True},
}

# Friendly aliases the UI / pipeline may pass.
_PROFILE_ALIASES = {
    "mp4": "mp4-1080p", "1080p": "mp4-1080p", "youtube": "youtube-16x9",
    "vertical": "mp4-vertical-9x16", "reel": "instagram-reel",
    "tiktok": "tiktok-vertical", "gif": "gif-preview", "audio": "audio-mp3",
    "mp3": "audio-mp3",
}

TRANSITIONS = ("cut", "fade", "crossfade", "fadeblack", "dissolve")


def export_formats() -> List[str]:
    return list(EXPORT_PROFILES.keys())


def _resolve_profile(name: str) -> Tuple[str, Dict[str, Any]]:
    key = (name or "mp4-1080p").strip().lower()
    key = _PROFILE_ALIASES.get(key, key)
    return key, EXPORT_PROFILES.get(key, EXPORT_PROFILES["mp4-1080p"])


# ═══════════════════════════════════════════════════════════════════════════
#  FFMPEG DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════

def ffmpeg_exe() -> Optional[str]:
    """The bundled imageio-ffmpeg binary, else a system ffmpeg on PATH, else None."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def is_available() -> bool:
    return bool(ffmpeg_exe())


# ═══════════════════════════════════════════════════════════════════════════
#  VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def validate_timeline(tl: Optional[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """Validate a timeline contract. Returns (ok, [errors]). Never raises."""
    errs: List[str] = []
    if not isinstance(tl, dict):
        return False, ["timeline must be an object"]
    tracks = tl.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        errs.append("timeline needs a non-empty 'tracks' list")
        return False, errs
    has_renderable = False
    for ti, track in enumerate(tracks):
        if not isinstance(track, dict):
            errs.append(f"track {ti} must be an object")
            continue
        kind = track.get("kind")
        if kind not in ("video", "audio", "overlay"):
            errs.append(f"track {ti} kind must be video|audio|overlay (got {kind!r})")
        clips = track.get("clips")
        if not isinstance(clips, list):
            errs.append(f"track {ti} needs a 'clips' list")
            continue
        for ci, clip in enumerate(clips):
            if kind in ("video", "audio"):
                if not clip.get("file"):
                    errs.append(f"track {ti} clip {ci} needs a 'file'")
                else:
                    has_renderable = True
                    if not _resolve_clip_path(clip["file"]):
                        errs.append(f"track {ti} clip {ci}: file not found ({clip['file']})")
            tr = (clip.get("transition_in") or {}).get("type")
            if tr and tr not in TRANSITIONS:
                errs.append(f"track {ti} clip {ci}: unknown transition {tr!r}")
    if not has_renderable:
        errs.append("timeline has no video/audio clips to render")
    return (not errs), errs


def _resolve_clip_path(file: str) -> Optional[Path]:
    """Resolve a clip reference to an absolute path (creation filename or path)."""
    try:
        p = Path(file).expanduser()
        if p.exists() and p.is_file():
            return p
        cand = CREATIONS_DIR / Path(file).name
        if cand.exists():
            return cand
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  FILTER-GRAPH BUILDER  (pure — unit-testable without invoking ffmpeg)
# ═══════════════════════════════════════════════════════════════════════════

def build_ffmpeg_command(timeline: Dict[str, Any], profile_name: str,
                         ffmpeg: str, output_path: str,
                         include_overlays: bool = True) -> List[str]:
    """Build the full ffmpeg argv for one export profile. Pure function: takes a
    timeline + profile, returns the command list. Covers the common path —
    concat (cuts), xfade (crossfades), amix with music ducking, drawtext title
    cards, and scale/pad to the profile resolution."""
    _key, prof = _resolve_profile(profile_name)
    w, h = prof.get("w"), prof.get("h")
    fps = int(timeline.get("fps") or 30)

    video_clips = _track_clips(timeline, "video")
    audio_clips = _track_clips(timeline, "audio")
    overlays = _track_clips(timeline, "overlay")

    filt_parts: List[str] = []
    audio_only = prof.get("audio_only")
    # Whether THIS profile carries audio. GIF has none, so we must not even build
    # the audio filter chain for it (an unconnected filter output errors ffmpeg).
    wants_audio = bool(prof.get("a")) or bool(audio_only)
    if not wants_audio:
        audio_clips = []

    inputs: List[str] = []          # -i args, in order: all video, then audio
    input_paths: List[Path] = []
    for clip in video_clips + audio_clips:
        p = _resolve_clip_path(clip["file"])
        if p:
            inputs.append(str(p))
            input_paths.append(p)

    # ── VIDEO chain ──
    vmap = None
    n_vid = len(video_clips)
    if not audio_only and n_vid:
        labels = []
        for i, clip in enumerate(video_clips):
            t_in = float(clip.get("in", 0.0) or 0.0)
            t_out = clip.get("out")
            trim = f"trim=start={t_in}" + (f":end={t_out}" if t_out else "")
            scale = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1") if (w and h) else "setsar=1"
            lbl = f"v{i}"
            filt_parts.append(f"[{i}:v]{trim},setpts=PTS-STARTPTS,fps={fps},{scale}[{lbl}]")
            labels.append((lbl, clip))
        vmap = _chain_video(filt_parts, labels, fps)

    # ── AUDIO chain (dialogue + music + ambient; music ducks under dialogue) ──
    amap = None
    a_inputs = []
    base = n_vid
    for j, clip in enumerate(audio_clips):
        idx = base + j
        gain = float(clip.get("gain_db", 0.0) or 0.0)
        role = (clip.get("role") or "music").lower()
        lbl = f"a{j}"
        chain = ["aresample=44100", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if gain:
            chain.append(f"volume={gain}dB")
        if clip.get("fade_out"):
            chain.append(f"afade=t=out:st=0:d={float(clip['fade_out'])}")
        if clip.get("fade_in"):
            chain.append(f"afade=t=in:st=0:d={float(clip['fade_in'])}")
        filt_parts.append(f"[{idx}:a]{','.join(chain)}[{lbl}]")
        a_inputs.append((lbl, role))
    amap = _chain_audio(filt_parts, a_inputs)

    # ── OVERLAY title cards (drawtext) over the video map ──
    # drawtext needs a font; ffmpeg's Windows build has no fontconfig default, so
    # we pass an explicit fontfile. If no usable font is found we SKIP overlays
    # rather than fail the whole render (the cut still ships, just without titles).
    font = _drawtext_fontfile()
    if vmap and overlays and font and include_overlays:
        # Single-quote the (colon-escaped) font path — the form ffmpeg accepts on
        # Windows where a bare drive-colon path is mis-parsed as an option break.
        font_clause = f"fontfile='{font}':"
        cur = vmap
        for k, ov in enumerate(overlays):
            text = str(ov.get("text", "")).replace("\\", "").replace(":", r"\:").replace("'", "")
            t = float(ov.get("t", 0.0) or 0.0)
            dur = float(ov.get("dur", 3.0) or 3.0)
            out_lbl = f"ov{k}"
            draw = (f"drawtext={font_clause}text='{text}':fontcolor=white:fontsize=54:"
                    f"box=1:boxcolor=black@0.4:boxborderw=12:"
                    f"x=(w-text_w)/2:y=(h-text_h)/2:"
                    f"enable='between(t,{t},{t + dur})'")
            filt_parts.append(f"[{cur}]{draw}[{out_lbl}]")
            cur = out_lbl
        vmap = cur

    # ── Assemble argv ──
    cmd = [ffmpeg, "-y"]
    for ip in inputs:
        cmd += ["-i", ip]

    if filt_parts:
        cmd += ["-filter_complex", ";".join(filt_parts)]
    if vmap and not audio_only:
        cmd += ["-map", f"[{vmap}]"]
    # Only map audio when the profile actually carries an audio codec (GIF, for
    # one, has none — mapping audio into it would fail the whole export).
    if amap and (prof.get("a") or audio_only):
        cmd += ["-map", f"[{amap}]"]

    if prof.get("gif"):
        cmd += ["-t", str(timeline.get("gif_seconds", 6))]
    if not audio_only and prof.get("v"):
        cmd += ["-c:v", prof["v"], "-pix_fmt", "yuv420p", "-r", str(fps)]
    if amap and prof.get("a"):
        cmd += ["-c:a", prof["a"]]
    if audio_only:
        cmd += ["-vn"]
    cmd += ["-shortest", output_path]
    return cmd


def _drawtext_fontfile() -> Optional[str]:
    """An ffmpeg-escaped path to a usable TTF/TTC, or None. ffmpeg's drawtext
    needs an explicit fontfile on platforms without a fontconfig default."""
    import os
    for cand in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf",
                 r"C:\Windows\Fonts\calibri.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if os.path.exists(cand):
            # Escape for the ffmpeg filter mini-language: backslashes → slashes,
            # and the drive-letter colon must be escaped.
            return cand.replace("\\", "/").replace(":", r"\:")
    return None


def _has_overlays(timeline: Dict[str, Any]) -> bool:
    return any(t.get("kind") == "overlay" and t.get("clips")
              for t in timeline.get("tracks", []))


def _track_clips(timeline: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    out = []
    for track in timeline.get("tracks", []):
        if track.get("kind") == kind:
            out.extend(track.get("clips", []) or [])
    return out


def _chain_video(filt_parts: List[str], labels, fps: int) -> Optional[str]:
    """Concatenate (cuts) or xfade-chain (crossfades) the prepared video labels."""
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0][0]
    # If every transition is a plain cut → fast concat.
    any_xfade = any((c.get("transition_in") or {}).get("type") in
                    ("crossfade", "fade", "dissolve", "fadeblack") for _l, c in labels)
    if not any_xfade:
        ins = "".join(f"[{lbl}]" for lbl, _c in labels)
        filt_parts.append(f"{ins}concat=n={len(labels)}:v=1:a=0[vout]")
        return "vout"
    # Crossfade chain: xfade each successive clip onto the running result.
    cur = labels[0][0]
    offset = max(0.1, float(labels[0][1].get("out", 5.0) or 5.0))
    for i in range(1, len(labels)):
        lbl, clip = labels[i]
        tr = (clip.get("transition_in") or {})
        dur = float(tr.get("dur", 0.5) or 0.5)
        kind = "fadeblack" if tr.get("type") == "fadeblack" else "fade"
        out_lbl = f"vx{i}"
        filt_parts.append(
            f"[{cur}][{lbl}]xfade=transition={kind}:duration={dur}:"
            f"offset={max(0.1, offset - dur)}[{out_lbl}]")
        cur = out_lbl
        offset += max(0.1, float(clip.get("out", 5.0) or 5.0)) - dur
    return cur


def _chain_audio(filt_parts: List[str], a_inputs) -> Optional[str]:
    """Mix audio tracks. When both dialogue and music exist, the music is ducked
    under the dialogue via sidechaincompress before the final amix."""
    if not a_inputs:
        return None
    if len(a_inputs) == 1:
        return a_inputs[0][0]
    dialogue = [l for l, r in a_inputs if r in ("dialogue", "voice", "vo")]
    music = [l for l, r in a_inputs if r in ("music", "score")]
    if dialogue and music:
        d = dialogue[0]
        ducked = []
        for k, m in enumerate(music):
            out = f"duck{k}"
            filt_parts.append(
                f"[{m}][{d}]sidechaincompress=threshold=0.03:ratio=8:"
                f"attack=20:release=300[{out}]")
            ducked.append(out)
        mix_in = dialogue + ducked + [l for l, r in a_inputs
                                      if r not in ("dialogue", "voice", "vo", "music", "score")]
    else:
        mix_in = [l for l, _r in a_inputs]
    ins = "".join(f"[{l}]" for l in mix_in)
    filt_parts.append(f"{ins}amix=inputs={len(mix_in)}:normalize=0:"
                     f"duration=longest[aout]")
    return "aout"


# ═══════════════════════════════════════════════════════════════════════════
#  COMPOSE
# ═══════════════════════════════════════════════════════════════════════════

def compose(timeline: Dict[str, Any], *, project_id: Optional[str] = None,
            session_ctx: Optional[dict] = None, license=None) -> Dict[str, Any]:
    """Render a timeline to one or more outputs. Returns the standard creative
    envelope. Never raises. status ∈ {ok, demo, blocked, error}."""
    ok, errs = validate_timeline(timeline)
    if not ok:
        return {"status": "error", "message": "Invalid timeline: " + "; ".join(errs)}

    profiles = timeline.get("exports") or ["mp4-1080p"]
    timeline_id = timeline.get("timeline_id") or f"tl-{uuid.uuid4().hex[:10]}"

    exe = ffmpeg_exe()
    if not exe:
        return _demo_compose(timeline, timeline_id, profiles, project_id, license)

    orb = _orb_start("Composing timeline…", icon="🎞️", name="Composing timeline")
    files: List[Dict[str, str]] = []
    try:
        sources = _source_edges(timeline)
        n = len(profiles)
        for i, prof_name in enumerate(profiles):
            key, prof = _resolve_profile(prof_name)
            _orb_update(orb, progress=0.1 + 0.8 * (i / max(1, n)),
                       label=f"Exporting {key} ({i + 1}/{n})…")
            ext = prof["container"]
            fname = f"friday-production-{_timestamp()}-{uuid.uuid4().hex[:4]}.{ext}"
            out_path = CREATIONS_DIR / fname
            CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
            cmd = build_ffmpeg_command(timeline, key, exe, str(out_path))
            res = subprocess.run(cmd, capture_output=True, timeout=900)
            if (res.returncode != 0 or not out_path.exists()) and _has_overlays(timeline):
                # Title-card drawtext is environment-fragile (fonts/escaping). Never
                # lose the whole cut over a title — retry once without overlays.
                err1 = (res.stderr or b"").decode("utf-8", "ignore")[-300:]
                print(f"  [timeline] {key} failed with overlays; retrying without "
                      f"title cards: {err1}")
                cmd = build_ffmpeg_command(timeline, key, exe, str(out_path),
                                           include_overlays=False)
                res = subprocess.run(cmd, capture_output=True, timeout=900)
            if res.returncode != 0 or not out_path.exists():
                err = (res.stderr or b"").decode("utf-8", "ignore")[-600:]
                print(f"  [timeline] ffmpeg {key} failed: {err}")
                continue
            rec = {"filename": fname, "kind": "video" if ext != "mp3" else "music",
                   "url": f"/api/creations/{fname}", "framed_url": f"/creation/{fname}",
                   "path": str(out_path), "profile": key}
            files.append(rec)
            _write_metadata(fname, {
                "kind": "production", "timeline_id": timeline_id, "profile": key,
                "container": ext, "tracks": len(timeline.get("tracks", [])),
                "created": datetime.now().isoformat(),
            })
            _write_provenance(rec, timeline, timeline_id, key, sources, project_id,
                              license)

        if not files:
            _orb_fail(orb)
            return {"status": "error",
                    "message": "Timeline export produced no files (ffmpeg failed). "
                               "Check that the source clips exist and are valid."}

        _persist_timeline(timeline, timeline_id)
        _attach_project(project_id, files)
        _orb_update(orb, status="completed", progress=1.0, label="Done")
        _defer(3.0, _safe_remove, orb)
        for f in files:
            _notify(f["filename"])
        return {"status": "ok", "kind": "production", "timeline_id": timeline_id,
                "files": files, "exports": [f["profile"] for f in files]}
    except subprocess.TimeoutExpired:
        _orb_fail(orb)
        return {"status": "error", "message": "Timeline export timed out (>15 min)."}
    except Exception as e:
        _orb_fail(orb)
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"Timeline composition failed: {e}"}


def _demo_compose(timeline, timeline_id, profiles, project_id, license=None) -> Dict[str, Any]:
    """No ffmpeg available → still persist the signed timeline + a description,
    so the work is never lost and Layer 2 provenance is intact."""
    try:
        _persist_timeline(timeline, timeline_id)
        sources = _source_edges(timeline)
        fname = f"friday-production-{_timestamp()}-{uuid.uuid4().hex[:4]}.md"
        n_clips = sum(len(t.get("clips", [])) for t in timeline.get("tracks", []))
        lines = [
            "# 🎞️ Friday — Timeline (demo / no ffmpeg)",
            "",
            "> ffmpeg is not installed, so the final video was not rendered. The "
            "signed timeline below records exactly which clips + tracks compose "
            "the work. Install the `compose` extra "
            "(`pip install agent-friday[compose]`) to render it.",
            "",
            f"- **Timeline:** `{timeline_id}`",
            f"- **Tracks:** {len(timeline.get('tracks', []))}  ·  **Clips:** {n_clips}",
            f"- **Requested exports:** {', '.join(profiles)}",
            "",
            "```json",
            json.dumps(timeline, indent=2, default=str)[:4000],
            "```",
        ]
        out_path = CREATIONS_DIR / fname
        CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        rec = {"filename": fname, "kind": "production",
               "url": f"/api/creations/{fname}", "framed_url": f"/creation/{fname}",
               "path": str(out_path)}
        _write_metadata(fname, {"kind": "production", "demo": True,
                                "timeline_id": timeline_id,
                                "created": datetime.now().isoformat()})
        _write_provenance(rec, timeline, timeline_id, "demo", sources, project_id,
                          license)
        _attach_project(project_id, [rec])
        _notify(fname)
        return {"status": "demo", "kind": "production", "timeline_id": timeline_id,
                "files": [rec], "message": (
                    "ffmpeg is not installed — wrote the signed timeline + a "
                    "description instead of a rendered video. Install "
                    "agent-friday[compose] to render.")}
    except Exception as e:
        return {"status": "error", "message": f"Timeline demo failed: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
#  PROVENANCE + PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════

def _source_edges(timeline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every source clip's content hash becomes a provenance input edge (§3.3)."""
    edges = []
    try:
        from agent_friday.services import provenance
        for track in timeline.get("tracks", []):
            role = {"video": "clip", "audio": "score"}.get(track.get("kind"))
            if not role:
                continue
            for clip in track.get("clips", []):
                p = _resolve_clip_path(clip.get("file", ""))
                if not p:
                    continue
                ch = provenance.hash_file(p)
                if ch:
                    edges.append(provenance.source_edge(ch, role))
    except Exception:
        pass
    return edges


def _write_provenance(file_rec, timeline, timeline_id, profile, sources, project_id,
                      license=None):
    try:
        from agent_friday.services import provenance
        tool = {"tool": "timeline_engine.compose", "version": "1.0",
                "timeline_id": timeline_id, "profile": profile}
        provenance.write(file_rec["path"], tool_chain=[tool], sources=sources,
                         media_type="production", license=license)
    except Exception:
        pass


def _persist_timeline(timeline: Dict[str, Any], timeline_id: str) -> None:
    """Save the timeline JSON itself (it is an ownership artifact)."""
    try:
        d = core.FRIDAY_DIR / "timelines"
        d.mkdir(parents=True, exist_ok=True)
        timeline = dict(timeline)
        timeline["timeline_id"] = timeline_id
        (d / f"{timeline_id}.json").write_text(
            json.dumps(timeline, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def _attach_project(project_id, files):
    if not project_id:
        return
    try:
        from agent_friday.services import creative_memory
        for f in files:
            creative_memory.add_asset(project_id, f["filename"])
    except Exception:
        pass
