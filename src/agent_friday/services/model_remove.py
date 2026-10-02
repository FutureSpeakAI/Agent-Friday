"""Remove a model, and go back to the previous version, each in one click.

Removing frees the file and the record after the arbiter lets go of the
seat; a model that holds a role is not removed until the caller names the
replacement, so Friday is never left with a role bound to nothing. A
download that replaces a model keeps the previous file and record in the
previous-version slot; rolling back puts them back. Role bindings changed
from the screen keep one undo snapshot.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from agent_friday.core import runtime_dir


def previous_dir() -> Path:
    return runtime_dir() / "models" / "previous"


def previous_index_path() -> Path:
    return previous_dir() / "previous.json"


def roles_undo_path() -> Path:
    return runtime_dir() / "models" / "roles_undo.json"


def _load_previous() -> dict:
    try:
        return json.loads(previous_index_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_previous(d: dict) -> None:
    previous_dir().mkdir(parents=True, exist_ok=True)
    tmp = previous_index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, previous_index_path())


def roles_bound_to(model_id: str, settings: dict | None = None) -> list[str]:
    try:
        if settings is None:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        cr = settings.get("capability_routing") or {}
        return sorted(k for k, v in cr.items() if isinstance(v, dict) and v.get("model") == model_id)
    except Exception:
        return []


def keep_previous(model_id: str) -> dict | None:
    """Before a download replaces `model_id`, move its current file and record
    into the previous-version slot. Returns what was kept, or None."""
    from agent_friday.services import model_store
    rec = model_store.get(model_id)
    if not rec or not rec.get("path") or not Path(rec["path"]).exists():
        return None
    previous_dir().mkdir(parents=True, exist_ok=True)
    src = Path(rec["path"])
    dest = previous_dir() / src.name
    if dest.exists():
        dest.unlink()
    shutil.move(str(src), str(dest))
    kept = {"model_id": model_id, "record": rec, "file": str(dest), "kept_at": time.time(),
            "roles": roles_bound_to(model_id)}
    d = _load_previous()
    d[model_id] = kept
    _save_previous(d)
    return kept


def previous_version(model_id: str) -> dict | None:
    return _load_previous().get(model_id)


def rollback(model_id: str) -> dict:
    """Put the previous version back: the file to its place, the record as
    it was, and (when they were changed) the roles it held."""
    from agent_friday.services import model_store
    kept = previous_version(model_id)
    if not kept:
        return {"ok": False, "error": "no previous version of %s is kept" % model_id}
    rec = kept["record"]
    src = Path(kept["file"])
    if not src.exists():
        return {"ok": False, "error": "the previous file is gone: %s" % src}
    dest = Path(rec["path"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    shutil.move(str(src), str(dest))
    model_store.register(model_id, dest, source=rec.get("source", model_store.SOURCE_LOCAL),
                         mmproj=rec.get("mmproj"), lora=rec.get("lora"), chat_template=rec.get("chat_template"),
                         sha256=rec.get("sha256"), origin=rec.get("origin"), label=rec.get("label"),
                         engine=rec.get("engine"), serve_args=rec.get("serve_args"),
                         serve_num_ctx=rec.get("serve_num_ctx"))
    d = _load_previous()
    d.pop(model_id, None)
    _save_previous(d)
    return {"ok": True, "model_id": model_id, "restored": str(dest), "roles": kept.get("roles") or []}


def remove(model_id: str, *, replacement: str | None = None, force: bool = False,
           arbiter=None, settings: dict | None = None) -> dict:
    """Free the file and the record. A model holding a role is refused
    without a replacement (or `force`), and the roles are named so the
    screen can ask. Returns `{ok, freed_bytes, roles, ...}`."""
    from agent_friday.services import model_store
    rec = model_store.get(model_id)
    if not rec:
        return {"ok": False, "error": "%s is not in the store" % model_id}
    roles = roles_bound_to(model_id, settings)
    if roles and not replacement and not force:
        return {"ok": False, "needs_replacement": True, "roles": roles,
                "error": "%s answers for %s; pick a replacement first" % (model_id, ", ".join(roles))}
    size = 0
    try:
        size = Path(rec["path"]).stat().st_size
    except Exception:
        pass
    if arbiter is None:
        try:
            from agent_friday.services.residency_arbiter import get_arbiter
            arbiter = get_arbiter()
        except Exception:
            arbiter = None
    released = False
    if arbiter is not None:
        try:
            arbiter.llama.evict(model_id)
            try:
                arbiter.ollama.evict(model_id)
            except Exception:
                pass
            released = True
        except Exception:
            released = False
    out = model_store.forget(model_id, delete_file=True)
    try:
        d = _load_previous()
        if model_id in d:
            kept = d.pop(model_id)
            try:
                Path(kept["file"]).unlink()
            except OSError:
                pass
            _save_previous(d)
    except Exception:
        pass
    return {"ok": bool(out.get("ok", True)), "model_id": model_id, "freed_bytes": size,
            "freed_gib": round(size / 2 ** 30, 2), "roles": roles, "replacement": replacement,
            "seat_released": released, "deleted": out.get("deleted")}


def snapshot_roles(settings: dict) -> dict:
    """Keep one undo snapshot of the role bindings before the screen changes them."""
    cr = (settings or {}).get("capability_routing") or {}
    snap = {"capability_routing": json.loads(json.dumps(cr)), "at": time.time()}
    roles_undo_path().parent.mkdir(parents=True, exist_ok=True)
    roles_undo_path().write_text(json.dumps(snap, indent=1), encoding="utf-8")
    return snap


def roles_undo() -> dict | None:
    try:
        return json.loads(roles_undo_path().read_text(encoding="utf-8"))
    except Exception:
        return None
