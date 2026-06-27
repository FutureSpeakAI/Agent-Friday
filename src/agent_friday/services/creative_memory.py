"""
Agent Friday — Creative Memory / Series Bible
FutureSpeak.AI · Asimov's Mind

Persistent, per-project creative memory. A *project* (a video series, a card
deck, an album, a storybook…) owns a *Series Bible*:

  • characters  — name, visual_description, voice_profile, aliases, notes
  • locations   — name, description, notes
  • continuity  — an append-only log of established facts ("Maya lost her hat in
                  scene 4") so later generations stay consistent
  • style_guide — project-wide look/tone: palette, lighting, render style, genre

The defining behavior: a character's *visual description propagates to every
downstream generation*. When a scene names "Maya", the Bible supplies Maya's
canonical look so every image/video renders the SAME Maya. That propagation is
exposed via ``character_context()`` (name → description map consumed by
scene_dna.render_prompt) and ``project_prompt_context()`` (a text block the
context-injection middleware folds into the system prompt).

Storage: ~/.friday/projects/<project_id>/bible.json  (one JSON per project).
The active project pointer lives in ~/.friday/projects/active.json. Pure JSON on
disk — no DB, import-safe under FRIDAY_TESTING (home is redirected to a temp dir
by the test harness, so writes are isolated).
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.core import FRIDAY_DIR

PROJECTS_DIR = FRIDAY_DIR / "projects"
_ACTIVE_FILE = PROJECTS_DIR / "active.json"

# Project types the UI offers. Free-text is allowed; this just seeds the picker.
PROJECT_TYPES = (
    "video-series", "short-film", "card", "card-deck", "album", "music",
    "storybook", "comic", "campaign", "brand", "general",
)

# Serialize bible writes so concurrent route handlers don't interleave a
# read-modify-write on the same project file.
_LOCK = threading.RLock()


# ═══════════════════════════════════════════════════════════════════════════
#  PATHS / IO
# ═══════════════════════════════════════════════════════════════════════════

def _slug(text: str, fallback: str = "project") -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or fallback)[:48]


def _now() -> str:
    return datetime.now().isoformat()


def _project_dir(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def _bible_path(project_id: str) -> Path:
    return _project_dir(project_id) / "bible.json"


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


def _empty_bible(project_id: str, name: str, ptype: str) -> Dict[str, Any]:
    return {
        "id": project_id,
        "name": name,
        "type": ptype,
        "created": _now(),
        "updated": _now(),
        "characters": [],     # [{name, visual_description, voice_profile, aliases, notes}]
        "locations": [],      # [{name, description, notes}]
        "continuity": [],     # [{ts, scene, note}]
        "style_guide": {},    # {palette, lighting, render_style, genre, tone, ...}
        "assets": [],         # [filename, ...] — creations belonging to this project
        "pipeline_status": {},  # {pipeline_id, stage, state} — last pipeline run
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PROJECT CRUD
# ═══════════════════════════════════════════════════════════════════════════

def create_project(name: str, ptype: str = "general", *,
                   style_guide: Optional[Dict[str, Any]] = None,
                   make_active: bool = True) -> Dict[str, Any]:
    """Create a new project + empty Series Bible. Returns the bible dict.

    The project id is a slug of the name plus a short uuid suffix so two
    same-named projects never collide.
    """
    name = (name or "Untitled Project").strip()
    ptype = (ptype or "general").strip() or "general"
    with _LOCK:
        project_id = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
        bible = _empty_bible(project_id, name, ptype)
        if style_guide:
            bible["style_guide"] = dict(style_guide)
        _write_json(_bible_path(project_id), bible)
        if make_active:
            set_active_project(project_id)
        return bible


def list_projects() -> List[Dict[str, Any]]:
    """Lightweight summaries of every project, newest first."""
    out: List[Dict[str, Any]] = []
    if not PROJECTS_DIR.exists():
        return out
    active = get_active_project_id()
    for child in PROJECTS_DIR.iterdir():
        if not child.is_dir():
            continue
        bible = _read_json(child / "bible.json")
        if not bible:
            continue
        out.append({
            "id": bible.get("id", child.name),
            "name": bible.get("name", child.name),
            "type": bible.get("type", "general"),
            "created": bible.get("created"),
            "updated": bible.get("updated"),
            "characters": len(bible.get("characters", [])),
            "locations": len(bible.get("locations", [])),
            "assets": len(bible.get("assets", [])),
            "active": bible.get("id") == active,
        })
    out.sort(key=lambda p: p.get("updated") or "", reverse=True)
    return out


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Full Series Bible for a project, or None if it doesn't exist."""
    return _read_json(_bible_path(project_id))


def update_project(project_id: str, *, name: Optional[str] = None,
                   ptype: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with _LOCK:
        bible = get_project(project_id)
        if not bible:
            return None
        if name is not None:
            bible["name"] = name.strip() or bible["name"]
        if ptype is not None:
            bible["type"] = ptype.strip() or bible["type"]
        return _save(bible)


def delete_project(project_id: str) -> bool:
    """Delete a project and its Bible. Clears the active pointer if it pointed
    here. Returns True if something was removed."""
    with _LOCK:
        d = _project_dir(project_id)
        if not d.exists():
            return False
        import shutil
        try:
            shutil.rmtree(d)
        except Exception:
            return False
        if get_active_project_id() == project_id:
            _write_json(_ACTIVE_FILE, {"active": ""})
        return True


def _save(bible: Dict[str, Any]) -> Dict[str, Any]:
    bible["updated"] = _now()
    _write_json(_bible_path(bible["id"]), bible)
    return bible


# ═══════════════════════════════════════════════════════════════════════════
#  ACTIVE PROJECT POINTER
# ═══════════════════════════════════════════════════════════════════════════

def set_active_project(project_id: str) -> None:
    _write_json(_ACTIVE_FILE, {"active": project_id or ""})


def get_active_project_id() -> str:
    data = _read_json(_ACTIVE_FILE) or {}
    return (data.get("active") or "").strip()


def get_active_project() -> Optional[Dict[str, Any]]:
    pid = get_active_project_id()
    return get_project(pid) if pid else None


# ═══════════════════════════════════════════════════════════════════════════
#  CHARACTERS
# ═══════════════════════════════════════════════════════════════════════════

def _find(items: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """Match a character/location by name OR alias, case-insensitively."""
    key = (name or "").strip().lower()
    if not key:
        return None
    for it in items:
        if (it.get("name") or "").strip().lower() == key:
            return it
        for alias in (it.get("aliases") or []):
            if (alias or "").strip().lower() == key:
                return it
    return None


def add_character(project_id: str, name: str, visual_description: str = "",
                  voice_profile: str = "", *, aliases: Optional[List[str]] = None,
                  notes: str = "") -> Optional[Dict[str, Any]]:
    """Add or UPDATE a character in the Bible (upsert by name). The visual
    description is what propagates to every downstream generation."""
    name = (name or "").strip()
    if not name:
        return None
    with _LOCK:
        bible = get_project(project_id)
        if not bible:
            return None
        existing = _find(bible["characters"], name)
        record = existing or {"name": name}
        if visual_description:
            record["visual_description"] = visual_description.strip()
        if voice_profile:
            record["voice_profile"] = voice_profile.strip()
        if aliases is not None:
            record["aliases"] = [a.strip() for a in aliases if a and a.strip()]
        if notes:
            record["notes"] = notes.strip()
        record.setdefault("visual_description", "")
        record.setdefault("voice_profile", "")
        record.setdefault("aliases", [])
        if not existing:
            bible["characters"].append(record)
        _save(bible)
        return record


def list_characters(project_id: str) -> List[Dict[str, Any]]:
    bible = get_project(project_id)
    return list(bible.get("characters", [])) if bible else []


def get_character(project_id: str, name: str) -> Optional[Dict[str, Any]]:
    bible = get_project(project_id)
    return _find(bible["characters"], name) if bible else None


def remove_character(project_id: str, name: str) -> bool:
    with _LOCK:
        bible = get_project(project_id)
        if not bible:
            return False
        rec = _find(bible["characters"], name)
        if not rec:
            return False
        bible["characters"].remove(rec)
        _save(bible)
        return True


# ═══════════════════════════════════════════════════════════════════════════
#  LOCATIONS
# ═══════════════════════════════════════════════════════════════════════════

def add_location(project_id: str, name: str, description: str = "",
                 *, notes: str = "") -> Optional[Dict[str, Any]]:
    name = (name or "").strip()
    if not name:
        return None
    with _LOCK:
        bible = get_project(project_id)
        if not bible:
            return None
        existing = _find(bible["locations"], name)
        record = existing or {"name": name}
        if description:
            record["description"] = description.strip()
        if notes:
            record["notes"] = notes.strip()
        record.setdefault("description", "")
        if not existing:
            bible["locations"].append(record)
        _save(bible)
        return record


def list_locations(project_id: str) -> List[Dict[str, Any]]:
    bible = get_project(project_id)
    return list(bible.get("locations", [])) if bible else []


def remove_location(project_id: str, name: str) -> bool:
    with _LOCK:
        bible = get_project(project_id)
        if not bible:
            return False
        rec = _find(bible["locations"], name)
        if not rec:
            return False
        bible["locations"].remove(rec)
        _save(bible)
        return True


# ═══════════════════════════════════════════════════════════════════════════
#  CONTINUITY LOG
# ═══════════════════════════════════════════════════════════════════════════

def add_continuity(project_id: str, note: str, *, scene: str = "") -> Optional[Dict[str, Any]]:
    """Append an established-fact entry to the continuity log."""
    note = (note or "").strip()
    if not note:
        return None
    with _LOCK:
        bible = get_project(project_id)
        if not bible:
            return None
        entry = {"ts": _now(), "scene": (scene or "").strip(), "note": note}
        bible["continuity"].append(entry)
        _save(bible)
        return entry


def list_continuity(project_id: str) -> List[Dict[str, Any]]:
    bible = get_project(project_id)
    return list(bible.get("continuity", [])) if bible else []


# ═══════════════════════════════════════════════════════════════════════════
#  STYLE GUIDE + ASSETS + PIPELINE STATUS
# ═══════════════════════════════════════════════════════════════════════════

def set_style_guide(project_id: str, style_guide: Dict[str, Any],
                    *, merge: bool = True) -> Optional[Dict[str, Any]]:
    with _LOCK:
        bible = get_project(project_id)
        if not bible:
            return None
        if merge:
            bible["style_guide"] = {**(bible.get("style_guide") or {}),
                                    **(style_guide or {})}
        else:
            bible["style_guide"] = dict(style_guide or {})
        _save(bible)
        return bible["style_guide"]


def add_asset(project_id: str, filename: str) -> Optional[List[str]]:
    """Attach a generated creation filename to the project's asset gallery."""
    filename = (filename or "").strip()
    if not filename:
        return None
    with _LOCK:
        bible = get_project(project_id)
        if not bible:
            return None
        if filename not in bible["assets"]:
            bible["assets"].append(filename)
            _save(bible)
        return list(bible["assets"])


def list_assets(project_id: str) -> List[str]:
    bible = get_project(project_id)
    return list(bible.get("assets", [])) if bible else []


def set_pipeline_status(project_id: str, status: Dict[str, Any]) -> None:
    with _LOCK:
        bible = get_project(project_id)
        if not bible:
            return
        bible["pipeline_status"] = dict(status or {})
        _save(bible)


# ═══════════════════════════════════════════════════════════════════════════
#  PROPAGATION — the reason the Bible exists
# ═══════════════════════════════════════════════════════════════════════════

def character_context(project_id: str,
                      names: Optional[List[str]] = None) -> Dict[str, str]:
    """name → visual description map for the requested characters (or ALL when
    ``names`` is None). Fed straight into scene_dna.render_prompt so a named
    character's canonical look propagates into the generation prompt.

    Resolution is alias-aware and keyed by the *requested* spelling so the
    caller can look the value back up by the name they passed.
    """
    bible = get_project(project_id)
    if not bible:
        return {}
    chars = bible.get("characters", [])
    out: Dict[str, str] = {}
    if names is None:
        for c in chars:
            desc = (c.get("visual_description") or "").strip()
            if desc:
                out[c["name"]] = desc
        return out
    for raw in names:
        rec = _find(chars, raw)
        if rec and (rec.get("visual_description") or "").strip():
            out[raw] = rec["visual_description"].strip()
    return out


def voice_context(project_id: str,
                  names: Optional[List[str]] = None) -> Dict[str, str]:
    """name → voice profile map (for TTS / video dialogue layers)."""
    bible = get_project(project_id)
    if not bible:
        return {}
    chars = bible.get("characters", [])
    out: Dict[str, str] = {}
    pool = chars if names is None else [_find(chars, n) for n in names]
    for c in pool:
        if c and (c.get("voice_profile") or "").strip():
            out[c["name"]] = c["voice_profile"].strip()
    return out


def project_prompt_context(project_id: str, *, max_chars: int = 1800) -> str:
    """A compact text block describing the project's Bible, for folding into a
    system prompt (context-injection middleware). Summarizes the style guide,
    the cast (name + look), key locations, and the most recent continuity facts.
    Capped so it never dominates the context window.
    """
    bible = get_project(project_id)
    if not bible:
        return ""
    lines: List[str] = [f"Active creative project: {bible.get('name')} "
                        f"({bible.get('type', 'general')})."]

    sg = bible.get("style_guide") or {}
    if sg:
        sg_bits = "; ".join(f"{k}: {v}" for k, v in sg.items() if v)
        if sg_bits:
            lines.append(f"Style guide — {sg_bits}.")

    chars = bible.get("characters", [])
    if chars:
        lines.append("Cast (keep these consistent across every generation):")
        for c in chars[:12]:
            desc = (c.get("visual_description") or "").strip()
            vp = (c.get("voice_profile") or "").strip()
            piece = f"  • {c['name']}"
            if desc:
                piece += f" — {desc}"
            if vp:
                piece += f" [voice: {vp}]"
            lines.append(piece)

    locs = bible.get("locations", [])
    if locs:
        lines.append("Locations:")
        for loc in locs[:8]:
            d = (loc.get("description") or "").strip()
            lines.append(f"  • {loc['name']}" + (f" — {d}" if d else ""))

    cont = bible.get("continuity", [])
    if cont:
        lines.append("Established continuity (do not contradict):")
        for entry in cont[-8:]:
            scene = entry.get("scene")
            prefix = f"[{scene}] " if scene else ""
            lines.append(f"  • {prefix}{entry.get('note')}")

    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[:max_chars].rsplit("\n", 1)[0] + "\n  …(truncated)"
    return block
