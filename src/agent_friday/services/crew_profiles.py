"""Persistent Crew identities, independent of their reasoning and speech seats.

Profiles are owner configuration. Model calls cannot create or widen profiles.
Every edit has a revision and preserves its predecessor; retirement keeps history.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from agent_friday.paths import friday_home
from agent_friday.user_errors import (
    UserFacingLookupError, UserFacingPermissionError, UserFacingRuntimeError,
    UserFacingValueError,
)

BROWSER_TOOLS = ("browser_open", "browser_read", "browser_click", "browser_type",
                 "browser_select", "browser_scroll", "browser_close")
SUPPORTED_TOOLS = ("read_file", "write_file", "search_web", "search_news", "browse_web") + BROWSER_TOOLS
MAX_PROFILE_BYTES = 160_000
MAX_MEMORY_BYTES = 100_000
MAX_MEMORY_ENTRIES = 12
MAX_MEMORY_ENTRY = 4000
LIMITS = {"memory_notes": 8000, "persona": 8000, "role": 1000,
          "max_steps": 200, "time_budget_s": 3600}
_ID = re.compile(r"crew-[a-f0-9]{16}\Z")
_LOCK = threading.RLock()
_EDITABLE = {"name", "role", "persona", "provider", "model", "voice", "caption",
             "project_ids", "grants", "skills", "allowed_tools", "memory",
             "max_steps", "time_budget_s", "status", "offline"}
_STORED = _EDITABLE | {"id", "revision", "created_at", "updated_at"}


class ProfileConflict(UserFacingValueError):
    def __init__(self, current):
        super().__init__("This agent changed. Reload it before saving your edit.", status=409)
        self.current = current


def _root() -> Path:
    return friday_home() / "crew" / "agents"


def _directory(profile_id: str) -> Path:
    if not isinstance(profile_id, str) or not _ID.fullmatch(profile_id):
        raise UserFacingValueError("That Crew agent ID is invalid.", status=400)
    root = _root().resolve()
    target = root / profile_id
    if target.is_symlink() or (target.exists() and target.resolve() != target):
        raise UserFacingPermissionError("This agent's storage location is unsafe.", status=403)
    return target


def _text(value, name, limit, *, required=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise UserFacingValueError(f"{name} must be text of at most {limit} characters.", status=400)
    value = value.strip()
    if required and not value:
        raise UserFacingValueError(f"{name} is required.", status=400)
    return value


def _object(value, name, keys):
    if not isinstance(value, dict) or set(value) - set(keys):
        raise UserFacingValueError(f"{name} contains unsupported settings.", status=400)
    return value


def _strings(value, name, *, limit=100):
    if not isinstance(value, list) or len(value) > limit:
        raise UserFacingValueError(f"{name} must be a list of at most {limit} entries.", status=400)
    result = [_text(v, name, 200, required=True) for v in value]
    if len(set(result)) != len(result):
        raise UserFacingValueError(f"{name} contains duplicate entries.", status=400)
    return result


def _boolean(value, name):
    if type(value) is not bool:
        raise UserFacingValueError(f"{name} must be on or off.", status=400)
    return value


def _integer(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise UserFacingValueError(f"{name} must be between 1 and {maximum}.", status=400)
    return value


def _voice(value, *, required=True):
    value = _object(value, "Voice", {"provider", "model", "voice_id"})
    result = {key: _text(value.get(key, ""), "Voice " + key, 128, required=required)
              for key in ("provider", "model", "voice_id")}
    if result["voice_id"] and not re.fullmatch(r"[A-Za-z0-9_-]+", result["voice_id"]):
        raise UserFacingValueError("Voice ID may contain only letters, numbers, underscores and hyphens.", status=400)
    return result


def _normalize(data, *, stored=False):
    _object(data, "Agent", _STORED if stored else _EDITABLE)
    if stored and set(data) != _STORED:
        raise UserFacingValueError("This agent's saved settings are incomplete.", status=409)
    out = {
        "name": _text(data.get("name", ""), "Name", 80, required=True),
        "role": _text(data.get("role", ""), "Role", LIMITS["role"]),
        "persona": _text(data.get("persona", ""), "Personality", LIMITS["persona"]),
        "provider": _text(data.get("provider", ""), "Reasoning provider", 100, required=True),
        "model": _text(data.get("model", ""), "Reasoning model", 200, required=True),
        "voice": _voice(data.get("voice", {})),
        "status": data.get("status", "active"),
        "project_ids": _strings(data.get("project_ids", []), "Projects"),
        "skills": _strings(data.get("skills", []), "Skills"),
        "allowed_tools": _strings(data.get("allowed_tools", []), "Tools", limit=len(SUPPORTED_TOOLS)),
        "max_steps": _integer(data.get("max_steps", 20), "Tool step limit", LIMITS["max_steps"]),
        "time_budget_s": _integer(data.get("time_budget_s", 300), "Time limit", LIMITS["time_budget_s"]),
    }
    if out["status"] not in ("active", "suspended", "retired"):
        raise UserFacingValueError("Choose active, suspended or retired.", status=400)
    if set(out["allowed_tools"]) - set(SUPPORTED_TOOLS):
        raise UserFacingValueError("That tool is not supported by Crew's access controls.", status=400)
    caption = _object(data.get("caption", {}), "Caption", {"label"})
    out["caption"] = {"label": _text(caption.get("label", out["name"]), "Caption", 80, required=True)}
    memory = _object(data.get("memory", {}), "Memory", {"notes", "read", "write"})
    out["memory"] = {
        "notes": _text(memory.get("notes", ""), "Own memory notes", LIMITS["memory_notes"]),
        "read": _boolean(memory.get("read", False), "Read own memory"),
        "write": _boolean(memory.get("write", False), "Keep own memory"),
    }
    grants = data.get("grants", [])
    if not isinstance(grants, list) or len(grants) > 50:
        raise UserFacingValueError("Choose at most 50 file or folder grants.", status=400)
    out["grants"] = []
    for grant in grants:
        grant = _object(grant, "File access", {"path", "access"})
        access = grant.get("access")
        if access not in ("read", "write"):
            raise UserFacingValueError("File access is read or write (which includes read).", status=400)
        out["grants"].append({"path": _text(grant.get("path", ""), "File path", 2000, required=True),
                              "access": access})
    offline = data.get("offline")
    if offline is not None:
        offline = _object(offline, "Offline binding", {"provider", "model", "voice"})
        out["offline"] = {"provider": _text(offline.get("provider", ""), "Offline provider", 100),
                          "model": _text(offline.get("model", ""), "Offline model", 200),
                          "voice": _voice(offline.get("voice", {}), required=False)}
    else:
        out["offline"] = None
    if stored:
        _directory(data.get("id"))
        out["id"] = data["id"]
        out["revision"] = _integer(data.get("revision"), "Revision", 2**31 - 1)
        for field in ("created_at", "updated_at"):
            value = data.get(field)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 < value < 10**12:
                raise UserFacingValueError("This agent's saved dates are invalid.", status=400)
            out[field] = value
    return out


def _registry():
    from agent_friday.services.provider_registry import get_provider_registry
    return get_provider_registry()


def _reasoning_options():
    registry = _registry()
    result = []
    for p in registry.list_providers():
        if (p.get("type") not in ("anthropic", "openai-compatible")
                or p.get("classification") == "local"):
            continue
        models = []
        for mid in p.get("models") or []:
            meta = (p.get("model_meta") or {}).get(mid) or {}
            roles = meta.get("roles", p.get("roles") or [])
            if "subagent" not in roles:
                continue
            models.append({"id": mid, "label": meta.get("label") or mid})
        if models:
            result.append({"id": p["name"], "label": p.get("label") or p["name"],
                           "available": registry.is_provider_available(p["name"]), "models": models})
    return result


def _skill_options():
    from agent_friday.skill_registry import list_skills
    return [{"id": s["name"], "name": s["name"], "description": s.get("description", "")}
            for s in list_skills()]


def _project_options():
    from agent_friday.services import projects
    return [{"id": p["id"], "name": p.get("name") or p["id"]} for p in projects.list_all()]


def _voice_options():
    from agent_friday.services import cloud_voice
    result = []
    for p in cloud_voice.available_providers():
        spec = cloud_voice.PROVIDERS[p["name"]]
        result.append({"id": p["name"], "label": p["label"], "available": p["selectable"],
                       "reason": p.get("reason"), "models": p["models"],
                       "voices": [{"id": spec["default_voice"], "label": spec["default_voice"]}]})
    return result


def capabilities():
    providers = _reasoning_options()
    return {"supported_tools": list(SUPPORTED_TOOLS), "skills": _skill_options(),
            "projects": _project_options(), "providers": providers,
            "models": [{**m, "provider": p["id"]} for p in providers for m in p["models"]],
            "voice_providers": _voice_options(), "limits": dict(LIMITS),
            "offline": {"operational": False, "message": "Offline bindings are saved only; this Crew uses cloud services."},
            "write_includes_read": True}


def _check_references(profile, previous=None):
    provider = next((p for p in _reasoning_options() if p["id"] == profile["provider"]), None)
    if provider is None or profile["model"] not in {m["id"] for m in provider["models"]}:
        raise UserFacingValueError("Choose a supported reasoning provider and one of its listed models.", status=400)
    voices = _voice_options()
    voice = next((p for p in voices if p["id"] == profile["voice"]["provider"]), None)
    if voice is None or profile["voice"]["model"] not in {m["id"] for m in voice["models"]}:
        raise UserFacingValueError("Choose a supported speech provider and one of its listed models.", status=400)
    if set(profile["skills"]) - {s["id"] for s in _skill_options()}:
        raise UserFacingValueError("An assigned skill no longer exists. Reload the available skills.", status=400)
    if set(profile["project_ids"]) - {p["id"] for p in _project_options()}:
        raise UserFacingValueError("An assigned project no longer exists. Reload the available projects.", status=400)
    from agent_friday.services.crew_access import validate_grant
    checked = []
    for grant in profile["grants"]:
        resolved = validate_grant(grant)
        # An unrelated edit does not re-grant a path whose target changed
        # from a file into a folder. Folder reach is explicitly persisted.
        if previous and any(old["path"] == grant["path"] for old in previous["grants"]):
            resolved["path"] = grant["path"]
        checked.append(resolved)
    profile["grants"] = checked


def _read_json(path: Path, limit: int):
    if path.is_symlink():
        raise ValueError("linked state file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("state file too large")
    return json.loads(raw)


def _atomic_json(path: Path, data):
    raw = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_PROFILE_BYTES:
        raise UserFacingValueError("This agent's settings are too large.", status=400)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / ("." + path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with tmp.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def get_profile(profile_id):
    path = _directory(profile_id) / "profile.json"
    if not path.exists():
        raise UserFacingLookupError("That Crew agent was not found.", status=404)
    try:
        data = _normalize(_read_json(path, MAX_PROFILE_BYTES), stored=True)
        if data["id"] != profile_id:
            raise ValueError("wrong profile identity")
        return data
    except Exception as exc:
        raise UserFacingRuntimeError("This agent's saved settings cannot be verified. Its work is paused.", status=409) from exc


def list_profiles(include_retired=False):
    if not _root().exists():
        return []
    result = []
    for path in sorted(_root().iterdir()):
        if not _ID.fullmatch(path.name):
            continue
        profile = get_profile(path.name)
        if include_retired or profile["status"] != "retired":
            result.append(profile)
    return result


def create_profile(data):
    profile = _normalize(data)
    if profile["status"] == "retired":
        raise UserFacingValueError("Create an active or suspended agent.", status=400)
    _check_references(profile)
    with _LOCK:
        profile.update(id="crew-" + secrets.token_hex(8), revision=1,
                       created_at=time.time(), updated_at=time.time())
        _atomic_json(_directory(profile["id"]) / "profile.json", profile)
    return copy.deepcopy(profile)


def update_profile(profile_id, data, expected_revision):
    _object(data, "Agent edit", _EDITABLE)
    with _LOCK:
        current = get_profile(profile_id)
        if type(expected_revision) is not int or expected_revision != current["revision"]:
            raise ProfileConflict(current)
        if current["status"] == "retired":
            raise UserFacingPermissionError("A retired agent's history is preserved and cannot be changed.", status=403)
        candidate = _normalize({**{k: current[k] for k in _EDITABLE if k in current}, **data})
        if candidate["status"] == "active":
            _check_references(candidate, previous=current)
        candidate.update(id=profile_id, revision=current["revision"] + 1,
                         created_at=current["created_at"], updated_at=time.time())
        directory = _directory(profile_id)
        _atomic_json(directory / "history" / (str(current["revision"]) + ".json"), current)
        _atomic_json(directory / "profile.json", candidate)
        return copy.deepcopy(candidate)


def retire_profile(profile_id, expected_revision):
    return update_profile(profile_id, {"status": "retired"}, expected_revision)


def memory_entries(profile_id):
    get_profile(profile_id)
    path = _directory(profile_id) / "memory.json"
    if not path.exists():
        return []
    try:
        rows = _read_json(path, MAX_MEMORY_BYTES)
        if not isinstance(rows, list) or len(rows) > MAX_MEMORY_ENTRIES:
            raise ValueError("invalid memory")
        for row in rows:
            required = {"text", "created_at", "authority", "project_id"}
            if (not isinstance(row, dict) or not required <= set(row)
                    or set(row) - (required | {"source_revisions"})):
                raise ValueError("invalid memory row")
            _text(row["text"], "Memory entry", MAX_MEMORY_ENTRY)
            if type(row["created_at"]) not in (float, int):
                raise ValueError("invalid memory timestamp")
            if not isinstance(row["authority"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["authority"]):
                raise ValueError("invalid memory authority")
            if row["project_id"] is not None and not isinstance(row["project_id"], str):
                raise ValueError("invalid memory project")
            row["source_revisions"] = _source_revisions(row.get("source_revisions", {}))
        return rows
    except Exception as exc:
        raise UserFacingRuntimeError("This agent's own memory cannot be verified.", status=409) from exc


def authority_fingerprint(profile):
    authority = {"project_ids": sorted(profile["project_ids"]),
                 "grants": sorted(profile["grants"], key=lambda g: (g["path"], g["access"])),
                 "skills": sorted(profile["skills"]), "allowed_tools": sorted(profile["allowed_tools"])}
    return hashlib.sha256(json.dumps(authority, sort_keys=True).encode("utf-8")).hexdigest()


def _source_revisions(sources):
    if (not isinstance(sources, dict) or len(sources) > 100
            or any(not isinstance(aid, str) or not _ID.fullmatch(aid)
                   or type(revision) is not int or not 1 <= revision < 2**31
                   for aid, revision in sources.items())):
        raise UserFacingValueError("This result's Crew sources cannot be verified.", status=400)
    return dict(sources)


def _sources_current(sources):
    try:
        for aid, revision in sources.items():
            source = get_profile(aid)
            if source["revision"] != revision or source["status"] != "active":
                return False
        return True
    except Exception:
        return False


def recall_memory(profile, project_id=None):
    if not profile["memory"]["read"]:
        return []
    authority = authority_fingerprint(profile)
    return [row for row in memory_entries(profile["id"])
            if row["authority"] == authority and row["project_id"] == project_id
            and _sources_current(row["source_revisions"])]


def recalled_source_revisions(profile, project_id=None):
    return {aid: revision for row in recall_memory(profile, project_id)[-4:]
            for aid, revision in row["source_revisions"].items()}


def remember_result(profile_id, text, expected_revision, project_id=None, source_revisions=None):
    """Persist a bounded worker result only under the still-current write grant."""
    from agent_friday.services import off_record
    if off_record.skip("crew_memory"):
        return False
    with _LOCK:
        profile = get_profile(profile_id)
        if profile["revision"] != expected_revision or profile["status"] != "active" or not profile["memory"]["write"]:
            return False
        if project_id is not None and project_id not in profile["project_ids"]:
            return False
        sources = _source_revisions({} if source_revisions is None else source_revisions)
        if not _sources_current(sources):
            return False
        text = _text(text, "Agent result", 100_000)[:MAX_MEMORY_ENTRY]
        rows = memory_entries(profile_id)
        rows.append({"text": text, "created_at": time.time(), "project_id": project_id,
                     "authority": authority_fingerprint(profile), "source_revisions": sources})
        rows = rows[-MAX_MEMORY_ENTRIES:]
        while len(json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")) > MAX_MEMORY_BYTES:
            rows.pop(0)
        _atomic_json(_directory(profile_id) / "memory.json", rows)
        return True
