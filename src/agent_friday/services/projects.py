"""Projects — folders for conversations, and what the chats inside them inherit.

A project groups chats and carries what they should default to. It does NOT
change what Friday knows. `conversations.py` states the rule this store is
built under: transcripts are isolated, memory is shared. ChromaDB, the wiki,
the knowledge graph and the vault stay global, and a project does not fork
them. The reasoning: most of what makes ChatGPT's projects useful is that
they fake a memory their product does not otherwise have. Friday has the real
thing, so narrowing it per folder would be paying a cost to buy something
already owned - and it would mean Friday knew less inside a project than
outside one, with no sign on the door saying so.

What a project is for here, then, is DEFAULTS, ORGANISATION and the WORK:

  * a name, so a hundred threads are navigable
  * an optional default seat, which on this machine is the load-bearing one
  * optional standing instructions, sent with every turn inside it
  * files: notes, briefs, data the chats inside can read (`files/`)
  * connected codebases: what the Build panel works on from any chat inside
  * the Series Bible (characters, locations, continuity, style guide, assets):
    the creative project, one record with the rest rather than a second store

ONE STORE. The creative projects (`services/creative_memory`) used to keep a
`bible.json` in a folder of their own under the same root, and a listing on
either side skipped the other's folders by file name. They are one record now,
`project.json`, and `creative_memory` reads and writes it through this module.
A legacy `bible.json` folder migrates on first sight (`migrate_legacy`): the
legacy file is left exactly as it was, what the migration wrote is listed in a
manifest, and `rollback` removes only that.

THE SEAT DEFAULT EARNS ITS KEEP. Only one local model fits in 12 GB at a time
(measured: two bonsai2:27b servers take 11,605 MiB of 12,282 and every turn
into that state hangs). Picking a seat per chat is therefore a chore
with a real constraint behind it. A project that declares "this one runs on
Bonsai" gives every chat inside it that seat without asking, and because they
are the SAME model they share one server rather than contending for the GPU.

MEMBERSHIP IS OWNED BY THE CONVERSATION, not by the project. `conversation.json`
carries a `project` id; a project keeps no list of its chats. One writer per
fact, so the two cannot drift - the failure where a project lists a chat that
was deleted, or a chat claims a project that dropped it, is unreachable rather
than merely unlikely. The cost is that listing a project's chats scans the
conversations directory, which is exactly what the switcher already does on
every open. A codebase is the one exception: the project lists its codebases
AND the codebase names its project, written together by `connect_codebase`.

Off the record, nothing is written: every file write asks `off_record` first.

Layout mirrors conversations, including the atomic write:

    ~/.friday/projects/<project_id>/project.json
    ~/.friday/projects/<project_id>/files/<name>
    ~/.friday/projects/_migrations/<timestamp>.json
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent_friday.core import FRIDAY_DIR
from agent_friday.paths import contained, safe_name

_LOCK = threading.RLock()

#: Standing instructions are prepended to every turn in the project, so they
#: are charged as input tokens on every single one. A cap keeps a runaway
#: paste from quietly doubling the cost of a cloud-seated project.
MAX_INSTRUCTIONS = 4000

#: A project's files are knowledge for its chats, not a backup drive.
MAX_FILE_BYTES = 2_000_000
MAX_PROJECT_FILE_BYTES = 20_000_000

#: How much of the files reaches the prompt each turn: text files are excerpted
#: up to this many characters each, and this many in all; everything else is
#: named with its size.
EXCERPT_CHARS = 600
EXCERPT_TOTAL_CHARS = 1500
EXCERPT_MAX_FILE_BYTES = 64_000
TEXT_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".html", ".htm", ".css",
                 ".js", ".ts", ".py", ".toml", ".ini", ".cfg", ".xml", ".svg", ".rst", ".log"}

CONTEXT_HEADER = "## PROJECT"

#: The Series Bible's own keys (services/creative_memory).
BIBLE_KEYS = ("characters", "locations", "continuity", "style_guide", "assets", "pipeline_status")


def _root() -> Path:
    return Path(FRIDAY_DIR) / "projects"


def _dir(pid: str) -> Path:
    return contained(_root(), safe_name(pid, what="project id"))


def new_id() -> str:
    return "proj-" + secrets.token_hex(4)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    tmp.replace(path)


def _off_record() -> bool:
    try:
        from agent_friday.core import _load_settings
        from agent_friday.services import off_record
        return off_record.active(_load_settings() or {})
    except Exception:
        return False


def _empty_bible() -> dict:
    return {"characters": [], "locations": [], "continuity": [], "style_guide": {},
            "assets": [], "pipeline_status": {}}


def _blank(pid: str, name: str) -> dict:
    now = time.time()
    return {
        "id": pid,
        "name": name,
        # null means "inherit the global default", resolved per turn exactly
        # as a null conversation seat is. Never a snapshot.
        "seat": None,
        "instructions": "",
        "color": None,
        "type": "general",
        "bible": _empty_bible(),
        "files": [],
        "codebases": [],
        "created_at": now,
        "updated_at": now,
        "archived": False,
    }


def _with_defaults(proj: Optional[dict]) -> Optional[dict]:
    """An older record reads with every key a new one is written with."""
    if proj is None:
        return None
    proj.setdefault("type", "general")
    bible = proj.get("bible")
    if not isinstance(bible, dict):
        bible = {}
    for k, v in _empty_bible().items():
        bible.setdefault(k, v)
    proj["bible"] = bible
    if not isinstance(proj.get("files"), list):
        proj["files"] = []
    if not isinstance(proj.get("codebases"), list):
        proj["codebases"] = []
    proj.setdefault("archived", False)
    return proj


# ── Metadata ────────────────────────────────────────────

def load(pid: str) -> dict | None:
    if not pid:
        return None
    try:
        d = _dir(pid)
    except ValueError:
        return None
    p = d / "project.json"
    if not p.exists():
        # A creative project from before the stores were one: migrate it now.
        if (d / "bible.json").exists():
            _migrate_dir(d)
        if not p.exists():
            return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
        return _with_defaults(rec) if isinstance(rec, dict) else None
    except Exception:
        return None


def save(proj: dict) -> dict:
    with _LOCK:
        proj["updated_at"] = time.time()
        _atomic_write(_dir(proj["id"]) / "project.json",
                      json.dumps(proj, indent=2, ensure_ascii=False, default=str))
    return proj


def create(name: str = "New project", seat: dict | None = None,
           instructions: str = "", color: str | None = None,
           pid: str | None = None, type: str = "general",
           bible: dict | None = None) -> dict:
    with _LOCK:
        proj = _blank(pid or new_id(), (name or "New project").strip()[:80]
                      or "New project")
        if isinstance(seat, dict):
            proj["seat"] = seat
        if instructions:
            proj["instructions"] = str(instructions)[:MAX_INSTRUCTIONS]
        if color:
            proj["color"] = str(color)[:32]
        if type:
            proj["type"] = str(type).strip()[:40] or "general"
        if isinstance(bible, dict):
            for k in BIBLE_KEYS:
                if k in bible:
                    proj["bible"][k] = bible[k]
        _dir(proj["id"]).mkdir(parents=True, exist_ok=True)
        return save(proj)


_MIGRATED_ONCE = False


def list_all(include_archived: bool = False) -> list[dict]:
    global _MIGRATED_ONCE
    if not _MIGRATED_ONCE:
        _MIGRATED_ONCE = True
        try:
            migrate_legacy()
        except Exception:
            pass
    out = []
    root = _root()
    if root.exists():
        for d in root.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            proj = load(d.name)
            if proj is None:
                continue
            if not include_archived and proj.get("archived"):
                continue
            out.append(proj)
    # Alphabetical. A project list is navigated by eye, not by recency - unlike
    # the chat list, where the thing you touched last is the thing you want.
    out.sort(key=lambda p: (p.get("name") or "").lower())
    return out


def patch(pid: str, **fields) -> dict | None:
    with _LOCK:
        proj = load(pid)
        if proj is None:
            return None
        if "name" in fields:
            proj["name"] = (str(fields["name"]).strip()[:80] or proj["name"])
        if "seat" in fields:
            seat = fields["seat"]
            proj["seat"] = seat if isinstance(seat, dict) and seat else None
        if "instructions" in fields:
            proj["instructions"] = \
                str(fields["instructions"] or "")[:MAX_INSTRUCTIONS]
        if "color" in fields:
            c = fields["color"]
            proj["color"] = str(c)[:32] if c else None
        if "archived" in fields:
            proj["archived"] = bool(fields["archived"])
        if "type" in fields:
            proj["type"] = str(fields["type"] or "").strip()[:40] or proj.get("type") or "general"
        return save(proj)


def patch_bible(pid: str, bible: dict) -> dict | None:
    """Store the Series Bible's fields on the project (services/creative_memory
    writes through here). Only the Bible's own keys are taken."""
    with _LOCK:
        proj = load(pid)
        if proj is None:
            return None
        for k in BIBLE_KEYS:
            if k in bible:
                proj["bible"][k] = bible[k]
        for k in ("created", "updated"):
            if bible.get(k):
                proj["bible"][k] = bible[k]
        if bible.get("name"):
            proj["name"] = str(bible["name"]).strip()[:80] or proj["name"]
        if bible.get("type"):
            proj["type"] = str(bible["type"]).strip()[:40] or proj["type"]
        return save(proj)


def delete(pid: str) -> int:
    """Delete the folder. Never the work inside it.

    Returns how many conversations were detached. Deleting a project sets
    `project = null` on every chat that pointed at it and leaves the
    transcripts exactly where they are, because a folder and its contents are
    different things and only one of them was asked about. A project delete
    that took the chats with it would be the most expensive undo in the app.
    The project's own folder (its record, its files, a legacy Bible) goes; a
    connected codebase is unlinked, not touched.
    """
    from agent_friday.services import conversations as _conv
    detached = 0
    with _LOCK:
        proj = load(pid)
        for conv in _conv.list_all(include_archived=True):
            if conv.get("project") == pid:
                _conv.patch(conv["id"], project=None)
                detached += 1
        if proj is not None:
            for cid in list(proj.get("codebases") or []):
                try:
                    from agent_friday.services import codebases as _cb
                    _cb.set_project(cid, None)
                except Exception:
                    pass
        try:
            d = _dir(pid)
            if d.exists():
                shutil.rmtree(d)
        except Exception:
            pass
        try:
            from agent_friday.services import creative_memory as _cm
            if _cm.get_active_project_id() == pid:
                _cm.set_active_project("")
        except Exception:
            pass
    return detached


def member_count(pid: str, include_archived: bool = False) -> int:
    from agent_friday.services import conversations as _conv
    return sum(1 for c in _conv.list_all(include_archived=include_archived)
               if c.get("project") == pid)


# ── Files ───────────────────────────────────────────────

def _files_dir(pid: str) -> Path:
    return _dir(pid) / "files"


def _file_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name != safe_name(name, what="file name") or name.startswith(".") \
            or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError("a file name is one plain name, no folders")
    return name[:120]


def list_files(pid: str) -> list:
    proj = load(pid)
    return list(proj.get("files") or []) if proj else []


def add_file(pid: str, name: str, data: bytes) -> dict:
    """Keep a file on the project. Replaces a file of the same name."""
    if _off_record():
        raise RuntimeError("off the record: nothing is written")
    name = _file_name(name)
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("a file is bytes")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("that file is too large for a project (%d bytes; the cap is %d)" % (len(data), MAX_FILE_BYTES))
    with _LOCK:
        proj = load(pid)
        if proj is None:
            raise KeyError("project")
        others = [f for f in proj["files"] if f.get("name") != name]
        if sum(int(f.get("bytes") or 0) for f in others) + len(data) > MAX_PROJECT_FILE_BYTES:
            raise ValueError("the project's files are at their cap (%d bytes)" % MAX_PROJECT_FILE_BYTES)
        fdir = _files_dir(pid)
        fdir.mkdir(parents=True, exist_ok=True)
        path = contained(fdir, name)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(bytes(data))
        tmp.replace(path)
        entry = {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                 "added_at": time.time()}
        proj["files"] = others + [entry]
        save(proj)
        return entry


def read_file(pid: str, name: str) -> Optional[bytes]:
    try:
        name = _file_name(name)
        path = contained(_files_dir(pid), name)
    except ValueError:
        return None
    proj = load(pid)
    if proj is None or not any(f.get("name") == name for f in proj["files"]):
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def remove_file(pid: str, name: str) -> bool:
    if _off_record():
        raise RuntimeError("off the record: nothing is written")
    try:
        name = _file_name(name)
        path = contained(_files_dir(pid), name)
    except ValueError:
        return False
    with _LOCK:
        proj = load(pid)
        if proj is None or not any(f.get("name") == name for f in proj["files"]):
            return False
        try:
            path.unlink()
        except OSError:
            pass
        proj["files"] = [f for f in proj["files"] if f.get("name") != name]
        save(proj)
        return True


# ── Codebases ───────────────────────────────────────────

def connect_codebase(pid: str, cid: str) -> dict:
    from agent_friday.services import codebases as _cb
    with _LOCK:
        proj = load(pid)
        if proj is None:
            raise KeyError("project")
        if not cid or _cb.load(cid) is None:
            raise KeyError("codebase")
        if cid not in proj["codebases"]:
            proj["codebases"].append(cid)
        _cb.set_project(cid, pid)
        return save(proj)


def disconnect_codebase(pid: str, cid: str) -> dict:
    from agent_friday.services import codebases as _cb
    with _LOCK:
        proj = load(pid)
        if proj is None:
            raise KeyError("project")
        proj["codebases"] = [c for c in proj["codebases"] if c != cid]
        try:
            if _cb.load(cid) is not None and _cb.load(cid).get("project") == pid:
                _cb.set_project(cid, None)
        except Exception:
            pass
        return save(proj)


# ── What a chat in the project is told ──────────────────

def _is_text(name: str) -> bool:
    return Path(name).suffix.lower() in TEXT_SUFFIXES


def context_block(cid: str) -> str:
    """The project this conversation is filed in, for its system prompt: the
    standing instructions, the files (text excerpted, the rest named), the
    connected codebases and the Bible. '' for a chat outside any project."""
    from agent_friday.services import conversations as _conv
    conv = _conv.load(cid) if cid else None
    pid = (conv or {}).get("project")
    proj = load(pid) if pid else None
    if proj is None:
        return ""
    lines = ["", CONTEXT_HEADER, "Project: %s (%s)." % (proj.get("name") or pid, proj.get("type") or "general"),
             "This chat is filed in the project; what follows applies to every turn in it."]
    instr = (proj.get("instructions") or "").strip()
    if instr:
        lines += ["Standing instructions:", instr]
    files = proj.get("files") or []
    if files:
        lines.append("Files (%d):" % len(files))
        budget = EXCERPT_TOTAL_CHARS
        for f in files:
            name = f.get("name") or ""
            size = int(f.get("bytes") or 0)
            excerpt = ""
            if budget > 0 and _is_text(name) and size <= EXCERPT_MAX_FILE_BYTES:
                blob = read_file(pid, name) or b""
                text = blob.decode("utf-8", "replace").strip()
                excerpt = text[:min(EXCERPT_CHARS, budget)]
                budget -= len(excerpt)
            if excerpt:
                more = " …" if len(excerpt) < size else ""
                lines.append("- %s (%d bytes): %s%s" % (name, size, excerpt.replace("\n", " / "), more))
            else:
                lines.append("- %s (%d bytes)" % (name, size))
    cbs = proj.get("codebases") or []
    if cbs:
        from agent_friday.services import codebases as _cb
        names = []
        for c in cbs:
            rec = _cb.load(c)
            names.append("%s (%s)" % ((rec or {}).get("title") or c, c))
        lines.append("Connected codebases: " + "; ".join(names) + ". The Build panel works on them from any chat in the project.")
    try:
        from agent_friday.services import creative_memory as _cm
        bible = _cm.project_prompt_context(pid, max_chars=1200)
        b = proj.get("bible") or {}
        if bible and (b.get("characters") or b.get("locations") or b.get("continuity") or b.get("style_guide")):
            lines.append(bible)
    except Exception:
        pass
    return "\n".join(lines) + "\n"


# ── Migration from the two stores ───────────────────────

def _iso_to_epoch(s: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(s)).timestamp() if s else None
    except Exception:
        return None


def _migrate_dir(d: Path) -> Optional[Path]:
    """One legacy Bible folder → project.json beside it. Returns the path written."""
    bible_path = d / "bible.json"
    if (d / "project.json").exists() or not bible_path.exists():
        return None
    try:
        bible = json.loads(bible_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(bible, dict):
        return None
    rec = _blank(d.name, str(bible.get("name") or d.name).strip()[:80] or d.name)
    rec["type"] = str(bible.get("type") or "general").strip()[:40] or "general"
    for k in BIBLE_KEYS:
        if k in bible:
            rec["bible"][k] = bible[k]
    for k in ("created", "updated"):
        if bible.get(k):
            rec["bible"][k] = bible[k]
    rec["created_at"] = _iso_to_epoch(bible.get("created")) or rec["created_at"]
    rec["updated_at"] = _iso_to_epoch(bible.get("updated")) or rec["updated_at"]
    path = d / "project.json"
    _atomic_write(path, json.dumps(rec, indent=2, ensure_ascii=False, default=str))
    return path


def migrate_legacy() -> dict:
    """Every creative project folder that has a Bible and no project record gets
    one. The Bible file is left as it was. Idempotent. Returns the manifest,
    which `rollback` undoes."""
    created, sources = [], []
    root = _root()
    with _LOCK:
        if root.exists():
            for d in sorted(root.iterdir()):
                if not d.is_dir() or d.name.startswith("_"):
                    continue
                written = _migrate_dir(d)
                if written is not None:
                    created.append(str(written))
                    sources.append(str(d / "bible.json"))
        manifest = {"at": time.time(), "created": created, "from": sources, "manifest": None}
        if created:
            mdir = root / "_migrations"
            mdir.mkdir(parents=True, exist_ok=True)
            mpath = mdir / ("%d.json" % int(manifest["at"] * 1000))
            manifest["manifest"] = str(mpath)
            _atomic_write(mpath, json.dumps(manifest, indent=2))
    return manifest


def rollback(manifest: dict | str | Path) -> int:
    """Remove exactly what a migration wrote. Returns how many files went."""
    if not isinstance(manifest, dict):
        manifest = json.loads(Path(manifest).read_text(encoding="utf-8"))
    root = _root().resolve()
    removed = 0
    with _LOCK:
        for p in manifest.get("created") or []:
            path = Path(p)
            try:
                path.resolve().relative_to(root)
            except Exception:
                continue
            if path.name == "project.json" and path.exists():
                path.unlink()
                removed += 1
    return removed
