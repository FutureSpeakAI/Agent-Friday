"""The A2UI document store: one stored, versioned, reversible document per workspace view.

A2UI is the single workspace format for the desktop and the phone. This is where a view's document lives
(`~/.friday/liquid/views/<id>.json`) and how it changes:

* every save runs the WHOLE document through the V1-V14 profile (services/a2ui_profile) first; a document that
  breaks a rule is refused with that rule and nothing is written;
* a save carries the revision it was made from (the sha256 of the stored bytes, "new" for a view that does not exist);
  a stale one is refused with the current revision and never merged;
* the state a change replaces is snapshotted first, at most MAX_VERSIONS are kept, and any version can be restored
  (what it replaces is kept too, so nothing a person did is thrown away); undo walks back one change at a time;
* his customizations (accent, density, note, summary, and component ids to hide) are properties of the document,
  typed at the edge and checked against it; they carry no CSS;
* a write is atomic, a damaged file is an error and never an empty view, a view id is one plain name, and a privacy
  context that changed refuses the write (the same admission rule as Workspace Studio).

Who may save is the caller's business (the desktop route needs the owner's press; a phone edit is a signed proposal
applied here): this module decides only whether a document is valid, current and reversible. tests/unit/
test_a2ui_store.py holds each rule, including the shared hostile documents run through `save` itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent_friday import paths
from agent_friday.core import FRIDAY_DIR
from agent_friday.services import a2ui_profile as prof

_log = logging.getLogger(__name__)

VIEWS_DIR = FRIDAY_DIR / "liquid" / "views"
MAX_VERSIONS = 40
MAX_DOC_BYTES = 256 * 1024
MAX_FILE_BYTES = (MAX_VERSIONS + 2) * MAX_DOC_BYTES
NEW_REVISION = "new"
KIND_CHANGE, KIND_RESTORE = "change", "restore"
NOTE_MAX, SUMMARY_MAX = 1500, 200
DENSITIES = ("comfortable", "compact")
LOOK_KEYS = frozenset({"accent", "density", "note", "summary", "hidden"})
_VIEW_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_ACCENT = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOCK = threading.Lock()


class ViewRefused(ValueError):
    """The change is refused: `rule` is a V-id from the profile, or one of size, customization, version, nothing."""

    def __init__(self, rule: str, detail: str = ""):
        super().__init__("%s: %s" % (rule, detail) if detail else rule)
        self.rule, self.detail = rule, detail


class ViewStale(OSError):
    """The view changed since the revision the caller worked from; `current_rev` is what to re-read."""

    def __init__(self, current_rev: str):
        super().__init__("the view changed since you read it")
        self.current_rev = current_rev


class ViewCorrupt(OSError):
    """The stored file cannot be read as this view. Never answered as an empty view."""


def check_view_id(view_id) -> str:
    text = str(view_id or "")
    if not _VIEW_ID.fullmatch(text):
        raise ValueError("a view id is one plain lowercase name")
    return text


def _path(view_id: str) -> Path:
    return paths.contained(VIEWS_DIR, "%s.json" % check_view_id(view_id))


def _admit_write():
    from agent_friday.services import workspace_studio
    return workspace_studio._admit_write()


def _check_write(origin) -> None:
    from agent_friday.services import workspace_studio
    workspace_studio._check_write(origin)


def _announce(view_id: str) -> None:
    try:
        from agent_friday.services import notice_registry
        notice_registry.bus_broadcast("E74", {"type": "liquid_view_changed", "view": view_id}, kind="desktop")
    except Exception:
        _log.exception("Could not tell the desktop a view changed")


def _now() -> str:
    return datetime.now().isoformat()


# ── reading ───────────────────────────────────────────────────────────────────

def _read(view_id: str) -> Optional[tuple]:
    """(record, raw bytes) or None when the view does not exist."""
    path = _path(view_id)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > MAX_FILE_BYTES:
        raise ViewCorrupt("the stored view is too large to read")
    try:
        record = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ViewCorrupt("the stored view could not be read") from exc
    if (not isinstance(record, dict) or record.get("id") != view_id or not isinstance(record.get("doc"), list)
            or not isinstance(record.get("versions"), list) or not isinstance(record.get("customization"), dict)):
        raise ViewCorrupt("the stored view is not a view")
    return record, raw


def _rev(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(view_id: str) -> Optional[dict]:
    """The stored view with its revision, or None. Raises ViewCorrupt for a file that cannot be read."""
    found = _read(check_view_id(view_id))
    if found is None:
        return None
    record, raw = found
    return {**record, "rev": _rev(raw)}


def list_views() -> list:
    out = []
    if not VIEWS_DIR.is_dir():
        return out
    for path in sorted(VIEWS_DIR.glob("*.json")):
        try:
            view = load(path.stem)
        except (ViewCorrupt, ValueError):
            continue
        if view is not None:
            out.append({"id": view["id"], "label": view.get("label"), "rev": view["rev"],
                        "updated": view.get("updated")})
    return out


def history(view_id: str) -> list:
    """The reversible versions, newest first: ids and names only, never a document."""
    view = load(view_id)
    if view is None:
        return []
    return [{"id": v["id"], "when": v["ts"], "label": v.get("label"), "kind": v["kind"], "by": v.get("by")}
            for v in reversed(view["versions"])]


# ── writing ───────────────────────────────────────────────────────────────────

def _component_ids(doc: list) -> set:
    ids = set()
    for message in doc:
        if not isinstance(message, dict):
            continue
        for key in ("createSurface", "updateComponents"):
            body = message.get(key)
            for comp in (body.get("components") if isinstance(body, dict) else None) or []:
                if isinstance(comp, dict) and isinstance(comp.get("id"), str):
                    ids.add(comp["id"])
    return ids


def _validated(doc, ctx: prof.Context) -> list:
    if not isinstance(doc, list):
        raise ViewRefused("V1", "a document is a list of messages")
    if len(json.dumps(doc, ensure_ascii=False).encode("utf-8")) > MAX_DOC_BYTES:
        raise ViewRefused("size", "a view document is at most %d bytes" % MAX_DOC_BYTES)
    try:
        prof.check(doc, ctx)
    except prof.ProfileError as exc:
        raise ViewRefused(exc.rule, exc.detail) from exc
    return doc


def _snapshot(record: dict, kind: str) -> dict:
    return {"id": uuid.uuid4().hex[:12], "ts": record.get("updated") or _now(), "kind": kind,
            "label": record.get("label"), "by": record.get("updated_by"),
            "doc": record["doc"], "customization": record["customization"]}


def _commit(view_id: str, build, base_rev: str):
    """Under the write locks: read the current state, check the revision, build the next record, write it."""
    from agent_friday import core
    origin = _admit_write()
    with core._SETTINGS_WRITE_LOCK, _LOCK:
        _check_write(origin)
        found = _read(view_id)
        current_rev = _rev(found[1]) if found else NEW_REVISION
        if base_rev != current_rev:
            raise ViewStale(current_rev)
        record = build(found[0] if found else None)
        record["updated"] = _now()
        data = json.dumps(record, ensure_ascii=False, indent=1).encode("utf-8")
        _write_atomic(_path(view_id), data)
    _announce(view_id)
    return {"rev": _rev(data), "versions": len(record["versions"])}


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".view-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _kept_hidden(customization: dict, doc: list) -> dict:
    hidden = [i for i in customization.get("hidden", []) if i in _component_ids(doc)]
    kept = {k: v for k, v in customization.items() if k != "hidden"}
    if hidden:
        kept["hidden"] = hidden
    return kept


def save(view_id: str, doc, *, ctx: prof.Context, base_rev: str, label: str, by: str) -> dict:
    """Store `doc` as the view's new current document. Raises ViewRefused (the document breaks a rule, nothing
    written), ViewStale (re-read and retry), ViewCorrupt, or PermissionError (the privacy context changed)."""
    view_id = check_view_id(view_id)
    _validated(doc, ctx)

    def build(previous):
        versions = list(previous["versions"]) + [_snapshot(previous, KIND_CHANGE)] if previous else []
        return {"id": view_id, "label": label, "subject": ctx.subject, "authorship": ctx.authorship, "doc": doc,
                "customization": _kept_hidden(previous["customization"], doc) if previous else {},
                "versions": versions[-MAX_VERSIONS:], "updated_by": by}
    return _commit(view_id, build, base_rev)


def _default_ctx(view: dict) -> prof.Context:
    return prof.Context(subject=view.get("subject") or "", authorship=view.get("authorship") or "composer")


def restore(view_id: str, version_id: str, *, base_rev: str, by: str, ctx: Optional[prof.Context] = None) -> dict:
    """Make an earlier version current again, through the profile again. The state it replaces is kept."""
    view_id = check_view_id(view_id)

    def build(previous):
        if previous is None:
            raise ViewRefused("version", "there is no such view")
        target = next((v for v in previous["versions"] if v["id"] == version_id), None)
        if target is None:
            raise ViewRefused("version", "there is no such version")
        _validated(target["doc"], ctx or _default_ctx(previous))
        versions = [dict(v, undone=True) if v["id"] == version_id else v for v in previous["versions"]]
        versions.append(_snapshot(previous, KIND_RESTORE))
        return {**previous, "doc": target["doc"], "customization": target["customization"],
                "label": target.get("label") or previous.get("label"), "versions": versions[-MAX_VERSIONS:],
                "updated_by": by}
    return _commit(view_id, build, base_rev)


def undo(view_id: str, *, base_rev: str, by: str, ctx: Optional[prof.Context] = None) -> dict:
    """Walk back one change: restore the newest change snapshot that has not already been undone."""
    view = load(view_id)
    target = next((v for v in reversed(view["versions"]) if v["kind"] == KIND_CHANGE and not v.get("undone")), None) \
        if view else None
    if target is None:
        raise ViewRefused("nothing", "there is nothing to undo")
    return restore(view_id, target["id"], base_rev=base_rev, by=by, ctx=ctx)


def _typed_look(patch, doc: list) -> dict:
    if not isinstance(patch, dict) or not patch:
        raise ViewRefused("customization", "send the look to change")
    unknown = sorted(set(patch) - LOOK_KEYS)
    if unknown:
        raise ViewRefused("customization", "not a customization: %s" % ", ".join(map(str, unknown)))
    out = {}
    if "accent" in patch:
        if not (isinstance(patch["accent"], str) and _ACCENT.fullmatch(patch["accent"])):
            raise ViewRefused("customization", "an accent is a #rrggbb colour")
        out["accent"] = patch["accent"]
    if "density" in patch:
        if patch["density"] not in DENSITIES:
            raise ViewRefused("customization", "density is comfortable or compact")
        out["density"] = patch["density"]
    for key, cap in (("note", NOTE_MAX), ("summary", SUMMARY_MAX)):
        if key in patch:
            if not isinstance(patch[key], str) or len(patch[key]) > cap:
                raise ViewRefused("customization", "%s is text of at most %d characters" % (key, cap))
            out[key] = patch[key]
    if "hidden" in patch:
        ids = patch["hidden"]
        if not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)) or not set(ids) <= _component_ids(doc):
            raise ViewRefused("customization", "hidden names parts of this view")
        out["hidden"] = list(dict.fromkeys(ids))
    return out


def set_customization(view_id: str, patch, *, base_rev: str, by: str) -> dict:
    """Change the view's look (the owner's customization). One more reversible version; the document itself is unchanged."""
    view_id = check_view_id(view_id)

    def build(previous):
        if previous is None:
            raise ViewRefused("version", "there is no such view")
        look = _typed_look(patch, previous["doc"])
        versions = list(previous["versions"]) + [_snapshot(previous, KIND_CHANGE)]
        return {**previous, "customization": {**previous["customization"], **look},
                "versions": versions[-MAX_VERSIONS:], "updated_by": by}
    return _commit(view_id, build, base_rev)
