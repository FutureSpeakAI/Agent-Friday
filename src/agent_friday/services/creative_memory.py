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

Storage: ONE record with the chat sidebar's projects, ~/.friday/projects/
<project_id>/project.json, owned by services/projects; the Bible is its
`bible` field and this module reads and writes it through that store (atomic
writes, deletes confined to the project's own folder, the chats inside kept).
A legacy ~/.friday/projects/<id>/bible.json migrates on first sight and is left
in place. The active project pointer lives in ~/.friday/projects/active.json.
Pure JSON on disk — no DB, import-safe under FRIDAY_TESTING (home is redirected
to a temp dir by the test harness, so writes are isolated).
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.core import FRIDAY_DIR
from agent_friday.paths import contained, safe_name

PROJECTS_DIR = FRIDAY_DIR / "projects"          # the shared root (services/projects owns it)
_ACTIVE_FILE = PROJECTS_DIR / "active.json"

# Project types the UI offers. Free-text is allowed; this just seeds the picker.
PROJECT_TYPES = (
    "video-series", "short-film", "card", "card-deck", "album", "music",
    "storybook", "comic", "campaign", "brand", "general",
)

# Serialize bible writes so concurrent route handlers don't interleave a
# read-modify-write on the same project file.
_LOCK = threading.RLock()


# ══════════════════════════════════════════════
#  PATHS / IO (through the one store)
# ══════════════════════════════════════════════

def _slug(text: str, fallback: str = "project") -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or fallback)[:48]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _store():
    from agent_friday.services import projects
    return projects


def _project_dir(project_id: str) -> Path:
    return _store()._dir(project_id)


def _bible_path(project_id: str) -> Path:
    return _project_dir(project_id) / "bible.json"


def _active_file() -> Path:
    return _store()._root() / "active.json"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    _store()._atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _iso(epoch) -> str:
    try:
        return datetime.fromtimestamp(float(epoch)).isoformat(timespec="seconds")
    except Exception:
        return _now()


def _bible_view(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The Bible as this module's callers have always seen it: id, name, type,
    created, updated and the Bible's own fields, read off the project record."""
    b = rec.get("bible") or {}
    view = {
        "id": rec["id"],
        "name": rec.get("name"),
        "type": rec.get("type") or "general",
        "created": b.get("created") or _iso(rec.get("created_at")),
        "updated": b.get("updated") or _iso(rec.get("updated_at")),
    }
    for k in _store().BIBLE_KEYS:
        v = b.get(k)
        view[k] = v if v is not None else ({} if k in ("style_guide", "pipeline_status") else [])
    return view


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


# ══════════════════════════════════════════════
#  PROJECT CRUD (one store: services/projects)
# ══════════════════════════════════════════════

def create_project(name: str, ptype: str = "general", *,
                   style_guide: Optional[Dict[str, Any]] = None,
                   make_active: bool = True) -> Dict[str, Any]:
    """Create a new project + empty Series Bible. Returns the bible view.

    The record is the chat sidebar's project record; the id is the store's.
    """
    name = (name or "Untitled Project").strip()
    ptype = (ptype or "general").strip() or "general"
    with _LOCK:
        rec = _store().create(name, type=ptype, bible={"style_guide": dict(style_guide or {})})
        _store().patch_bible(rec["id"], {"created": _now(), "updated": _now()})
        if make_active:
            set_active_project(rec["id"])
        return get_project(rec["id"])


def list_projects() -> List[Dict[str, Any]]:
    """Lightweight summaries of every project, newest first."""
    out: List[Dict[str, Any]] = []
    active = get_active_project_id()
    for rec in _store().list_all(include_archived=False):
        view = _bible_view(rec)
        out.append({
            "id": view["id"],
            "name": view["name"],
            "type": view["type"],
            "created": view["created"],
            "updated": view["updated"],
            "characters": len(view["characters"]),
            "locations": len(view["locations"]),
            "assets": len(view["assets"]),
            "active": view["id"] == active,
        })
    out.sort(key=lambda p: p.get("updated") or "", reverse=True)
    return out


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Full Series Bible for a project, or None if it doesn't exist."""
    rec = _store().load(project_id) if project_id else None
    return _bible_view(rec) if rec else None


def update_project(project_id: str, *, name: Optional[str] = None,
                   ptype: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with _LOCK:
        if get_project(project_id) is None:
            return None
        fields = {}
        if name is not None and name.strip():
            fields["name"] = name.strip()
        if ptype is not None and ptype.strip():
            fields["type"] = ptype.strip()
        if fields:
            _store().patch(project_id, **fields)
        _store().patch_bible(project_id, {"updated": _now()})
        return get_project(project_id)


def delete_project(project_id: str) -> bool:
    """Delete a project: its record, its files and its Bible, never the chats
    filed in it (they are detached and kept). Clears the active pointer if it
    pointed here. Returns True if something was removed."""
    with _LOCK:
        if not project_id or _store().load(project_id) is None:
            return False
        _store().delete(project_id)
        if get_active_project_id() == project_id:
            set_active_project("")
        return True


def _save(bible: Dict[str, Any]) -> Dict[str, Any]:
    bible["updated"] = _now()
    _store().patch_bible(bible["id"], bible)
    return bible


# ══════════════════════════════════════════════
#  ACTIVE PROJECT POINTER
# ══════════════════════════════════════════════

def set_active_project(project_id: str) -> None:
    _write_json(_active_file(), {"active": project_id or ""})


def get_active_project_id() -> str:
    data = _read_json(_active_file()) or {}
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
