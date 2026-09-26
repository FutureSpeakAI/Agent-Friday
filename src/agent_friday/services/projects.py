"""Projects — folders for conversations, and the defaults that come with them.

A project groups chats and carries what they should default to. It does NOT
change what Friday knows. `conversations.py` states the rule this store is
built under: transcripts are isolated, memory is shared. ChromaDB, the wiki,
the knowledge graph and the vault stay global, and a project does not fork
them. The reasoning: most of what makes ChatGPT's projects useful is that
they fake a memory their product does not otherwise have. Friday has the real
thing, so narrowing it per folder would be paying a cost to buy something
already owned - and it would mean Friday knew less inside a project than
outside one, with no sign on the door saying so.

What a project is for here, then, is DEFAULTS and ORGANISATION:

  * a name, so a hundred threads are navigable
  * an optional default seat, which on this machine is the load-bearing one
  * optional standing instructions prepended to chats inside it

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
every open.

Layout mirrors conversations, including the atomic write:

    ~/.friday/projects/<project_id>/project.json
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

from agent_friday.core import FRIDAY_DIR
from agent_friday.paths import contained, safe_name

_LOCK = threading.RLock()

#: Standing instructions are prepended to every turn in the project, so they
#: are charged as input tokens on every single one. A cap keeps a runaway
#: paste from quietly doubling the cost of a cloud-seated project.
MAX_INSTRUCTIONS = 4000


def _root() -> Path:
    return Path(FRIDAY_DIR) / "projects"


def _dir(pid: str) -> Path:
    """The project's folder. Raises ValueError unless the id is one plain name
    inside the projects root; ids arrive in URLs."""
    return contained(_root(), safe_name(pid, what="project id"))


def new_id() -> str:
    return "proj-" + secrets.token_hex(4)


def _atomic_write(path: Path, text: str) -> None:
    """tmp + fsync + replace, the same durability the conversation store uses.

    A half-written project.json reads as a missing project, and a missing
    project silently unparents every chat in it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    except Exception:
        pass
    tmp.replace(path)


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
        "created_at": now,
        "updated_at": now,
        "archived": False,
    }


# ── Metadata ────────────────────────────────────────────────────────────────

def load(pid: str) -> dict | None:
    if not pid:
        return None
    try:
        p = _dir(pid) / "project.json"
    except ValueError:
        return None
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def save(proj: dict) -> dict:
    with _LOCK:
        proj["updated_at"] = time.time()
        _atomic_write(_dir(proj["id"]) / "project.json",
                      json.dumps(proj, indent=2))
    return proj


def create(name: str = "New project", seat: dict | None = None,
           instructions: str = "", color: str | None = None,
           pid: str | None = None) -> dict:
    with _LOCK:
        proj = _blank(pid or new_id(), (name or "New project").strip()[:80]
                      or "New project")
        if isinstance(seat, dict):
            proj["seat"] = seat
        if instructions:
            proj["instructions"] = str(instructions)[:MAX_INSTRUCTIONS]
        if color:
            proj["color"] = str(color)[:32]
        _dir(proj["id"]).mkdir(parents=True, exist_ok=True)
        return save(proj)


def list_all(include_archived: bool = False) -> list[dict]:
    out = []
    root = _root()
    if root.exists():
        for d in root.iterdir():
            if not d.is_dir():
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
        return save(proj)


def delete(pid: str) -> int:
    """Delete the folder. Never the work inside it.

    Returns how many conversations were detached. Deleting a project sets
    `project = null` on every chat that pointed at it and leaves the
    transcripts exactly where they are, because a folder and its contents are
    different things and only one of them was asked about. A project delete
    that took the chats with it would be the most expensive undo in the app.
    """
    from agent_friday.services import conversations as _conv
    detached = 0
    with _LOCK:
        for conv in _conv.list_all(include_archived=True):
            if conv.get("project") == pid:
                _conv.patch(conv["id"], project=None)
                detached += 1
        try:
            d = _dir(pid)
            p = d / "project.json"
            if p.exists():
                p.unlink()
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        except Exception:
            pass
    return detached


def member_count(pid: str, include_archived: bool = False) -> int:
    from agent_friday.services import conversations as _conv
    return sum(1 for c in _conv.list_all(include_archived=include_archived)
               if c.get("project") == pid)
