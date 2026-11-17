"""Workspaces the owner builds on the phone or tablet (phone workspaces parity PW-G6; reads ``pc_read``, writes ``pc_act``).

A user workspace is a file on the PC, ``<Friday home>/workspaces/user/<id>.json``, schema ``friday.user_workspace/1``. It
**composes what already exists**: a section's ``source`` is a (workspace, kind, query, facet) triple over kinds the PC's
manifests publish, and a ``board`` section is an A2UI Friday-profile document. It carries no code, no verb, no URL, no
gate: rows keep the verbs and gates of the workspace they came from. Routes (owner-bound phone or tablet):

* ``GET /tv/v1/workspaces/user`` and ``.../user/<id>``: list and read, with ``rev``;
* ``GET /tv/v1/workspaces/palette``: the workspaces, kinds and views a section may use;
* ``PUT /tv/v1/workspaces/user/<id>`` ``{base_rev, manifest}`` (``pc_act``): validate, then write a new version;
* ``POST .../user/<id>/validate`` (``pc_act``): the same checks, no write, for the builder's live errors;
* ``DELETE .../user/<id>`` (``pc_act``): to the PC's trash, restorable; ``GET .../versions``; ``POST .../rollback``.

All or nothing: a manifest that breaks any rule is refused whole with the rule and the path of the fault. Every save carries
``base_rev``; the PC accepts only when it equals the stored ``rev`` (``0`` for a new workspace); a stale save is
``409 conflict`` carrying the PC's version, an edit of a workspace deleted on the PC is ``410 gone``. The server sets
``rev``, ``base_rev``, ``updated_by`` and ``updated_at``; the device never does. Every version is kept. Each rule is held by
a test in tests/unit/test_tv_pc_userws.py.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
import time
from pathlib import Path
from typing import List, Optional

from flask import Flask, Response, g, jsonify, request

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import pc_act, pc_read, tailnet
from agent_friday.tv_gateway.pc_common import refuse

_log = logging.getLogger(__name__)

SCHEMA = "friday.user_workspace/1"
VIEWS = ("list", "grid", "timeline", "reader", "explorer", "board")
MAX_SECTIONS, MAX_BYTES, MAX_LABEL, MAX_QUERY, MAX_FACETS = 20, 256 * 1024, 60, 200, 5
ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
ICON = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
#: Names Windows treats as devices: a file or folder called ``con`` or ``nul`` cannot be made there.
RESERVED = frozenset(["con", "prn", "aux", "nul"] + ["com%d" % n for n in range(1, 10)] + ["lpt%d" % n for n in range(1, 10)])
#: Control, format (zero-width, bidirectional, tag characters, soft hyphen) and line or paragraph separators: never part of
#: a name or a query.
_HIDDEN_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")


def _hidden(text: str) -> bool:
    return any(unicodedata.category(ch) in _HIDDEN_CATEGORIES for ch in text)
_LOCK = threading.Lock()
_TOP = {"schema", "id", "label", "icon", "group", "sections"}
_SECTION = {"id", "label", "view", "source", "a2ui"}
_SOURCE = {"ws", "kind", "query", "facet"}


class Fault(ValueError):
    """A rule the manifest breaks; ``path`` says where."""

    def __init__(self, rule: str, path: str):
        super().__init__("%s at %s" % (rule, path))
        self.rule, self.path = rule, path


def _root() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "workspaces" / "user"


def palette() -> dict:
    """The kinds a section may draw, from the PC's own manifests (held workspaces left out)."""
    from agent_friday.services import laya_manifests as lm
    kinds = {}
    for ws in lm.ids():
        manifest = lm.get(ws) or {}
        if manifest.get("held"):
            continue
        found = sorted({o.get("kind") for o in lm.objects(ws) if isinstance(o.get("kind"), str)})
        if found:
            kinds[ws] = found
    return {"views": list(VIEWS), "kinds": kinds}


def _plain(value, path: str, limit: int) -> str:
    from agent_friday.services import a2ui_profile
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _hidden(value):
        raise Fault("a plain text of 1 to %d characters, with no control or hidden characters" % limit, path)
    if a2ui_profile.has_url(value) or pc_read.hide_host_paths(value) != value:
        raise Fault("no link or file path in a name", path)
    return value


def _source(source, path: str, kinds: dict) -> None:
    if not isinstance(source, dict) or set(source) - _SOURCE or not {"ws", "kind"} <= set(source):
        raise Fault("a source is {ws, kind, query?, facet?}", path)
    if source["ws"] not in kinds or source["kind"] not in kinds[source["ws"]]:
        raise Fault("a workspace and kind this PC offers", path)
    if "query" in source:
        _plain(source["query"], path + ".query", MAX_QUERY)
    facet = source.get("facet")
    if facet is not None:
        if not isinstance(facet, dict) or len(facet) > MAX_FACETS:
            raise Fault("a facet of at most %d text pairs" % MAX_FACETS, path + ".facet")
        for key, value in facet.items():
            _plain(key, path + ".facet", MAX_LABEL)
            _plain(value, path + ".facet." + key, MAX_QUERY)


def _board(document, path: str, workspace_id: str) -> None:
    from agent_friday.services import a2ui_profile
    if not isinstance(document, list) or not document:
        raise Fault("an A2UI document is a non-empty list of messages", path)
    context = a2ui_profile.Context(subject="user_workspace:%s" % workspace_id, verbs=lambda name: None,
                                   authorship="composer",      # the owner's own, on an owner-bound device with pc_act
                                   model_authored=True)        # or Friday's composer drafted it: held to the stricter rules
    try:
        a2ui_profile.check(document, context)
    except a2ui_profile.ProfileError as error:
        raise Fault("A2UI %s: %s" % (error.rule, error.detail or "refused"), path) from error


def validate(manifest, workspace_id: str) -> None:
    """Raise Fault for the first rule broken. Closed schema: nothing the rules do not name is accepted. Input so odd
    that a rule itself raises (an unhashable value, nesting too deep) is a fault too, never a server error."""
    try:
        _validate(manifest, workspace_id)
    except (TypeError, RecursionError, KeyError, AttributeError, ValueError) as error:
        if isinstance(error, Fault):
            raise
        raise Fault("a well-formed manifest", "$") from error


def _validate(manifest, workspace_id: str) -> None:
    if not isinstance(manifest, dict) or set(manifest) - _TOP or not {"schema", "id", "label", "sections"} <= set(manifest):
        raise Fault("a manifest is {schema, id, label, icon?, group?, sections}", "$")
    if len(json.dumps(manifest).encode("utf-8")) > MAX_BYTES:
        raise Fault("at most %d KB" % (MAX_BYTES // 1024), "$")
    if manifest["schema"] != SCHEMA:
        raise Fault("schema %s" % SCHEMA, "$.schema")
    if manifest["id"] != workspace_id or not _good_id(manifest["id"]):
        raise Fault("an id of lower-case letters, digits, - and _ that matches the address", "$.id")
    _plain(manifest["label"], "$.label", MAX_LABEL)
    if "icon" in manifest and not (isinstance(manifest["icon"], str) and ICON.match(manifest["icon"])):
        raise Fault("an icon word", "$.icon")
    if manifest.get("group", "mine") != "mine":
        raise Fault("group mine", "$.group")
    sections = manifest["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        raise Fault("1 to %d sections" % MAX_SECTIONS, "$.sections")
    kinds, seen = palette()["kinds"], set()
    for index, section in enumerate(sections):
        path = "$.sections[%d]" % index
        if not isinstance(section, dict) or set(section) - _SECTION or not {"id", "label", "view"} <= set(section):
            raise Fault("a section is {id, label, view, source | a2ui}", path)
        if not ID.match(str(section["id"])) or section["id"] in seen:
            raise Fault("a unique id", path + ".id")
        seen.add(section["id"])
        _plain(section["label"], path + ".label", MAX_LABEL)
        if section["view"] not in VIEWS:
            raise Fault("a view from the palette", path + ".view")
        if (section["view"] == "board") != ("a2ui" in section) or ("a2ui" in section) == ("source" in section):
            raise Fault("a board has an a2ui document and no source; every other view has a source and no a2ui", path)
        if "source" in section:
            _source(section["source"], path + ".source", kinds)
        else:
            _board(section["a2ui"], path + ".a2ui", workspace_id)


def _file(workspace_id: str) -> Path:
    return _root() / ("%s.json" % workspace_id)


def _versions(workspace_id: str) -> Path:
    return _root() / ".versions" / workspace_id


def _load(workspace_id: str) -> Optional[dict]:
    try:
        data = json.loads(_file(workspace_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _stored(manifest: dict, previous_rev: int, device_id: str) -> dict:
    out = {key: manifest[key] for key in ("schema", "id", "label", "icon", "group", "sections") if key in manifest}
    out.setdefault("group", "mine")
    out.update(rev=previous_rev + 1, base_rev=previous_rev, updated_by="device:%s" % device_id,
               updated_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()))
    return out


def _receipt(action: str, workspace_id: str, outcome: int) -> None:
    device_pairing._receipt("device_act", g.device, right=pc_act.RIGHT, action=action, workspace=workspace_id,
                            outcome=outcome, via=tailnet.via())


def _fault_answer(error: Fault) -> Response:
    return refuse(422, "that workspace breaks a rule", code="invalid", rule=error.rule, path=error.path)


def _good_id(value) -> bool:
    return isinstance(value, str) and bool(ID.match(value)) and value not in RESERVED


def _checked(workspace_id: str) -> Optional[Response]:
    return None if _good_id(workspace_id) else refuse(400, "not a workspace id", code="bad_id")


def _too_big() -> Optional[Response]:
    """A body far over the manifest cap is refused before it is parsed."""
    if (request.content_length or 0) > MAX_BYTES + 4096:
        return refuse(413, "that workspace is too big", code="too_big")
    return None


def _has_history(workspace_id: str) -> bool:
    folder = _versions(workspace_id)
    return folder.is_dir() and any(folder.glob("*.json"))


def _trashed(workspace_id: str) -> bool:
    trash = _root() / ".trash"
    return trash.is_dir() and any(e.name[len(workspace_id) + 1:].isdigit() for e in trash.glob("%s-*" % workspace_id))


def _trash_name(workspace_id: str) -> str:
    return "%s-%d" % (workspace_id, time.time_ns())


def _row(data: dict) -> dict:
    return {key: data.get(key) for key in ("id", "label", "icon", "group", "rev", "updated_by", "updated_at")}


def register(app: Flask) -> None:
    @app.get("/tv/v1/workspaces/palette")
    def pc_ws_palette():
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        return jsonify({"ok": True, **palette()})

    @app.get("/tv/v1/workspaces/user")
    def pc_user_ws_list():
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        rows: List[dict] = []
        for path in sorted(_root().glob("*.json")) if _root().is_dir() else []:
            data = _load(path.stem)
            if data:
                rows.append(_row(data))
        return jsonify({"ok": True, "workspaces": rows})

    @app.get("/tv/v1/workspaces/user/<wid>")
    def pc_user_ws_read(wid):
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        bad = _checked(wid)
        if bad is not None:
            return bad
        data = _load(wid)
        if data is None:
            return refuse(404, "no such workspace", code="no_workspace")
        return jsonify({"ok": True, "manifest": data, "rev": data.get("rev")})

    @app.post("/tv/v1/workspaces/user/<wid>/validate")
    def pc_user_ws_validate(wid):
        denied = pc_act._guard()
        if denied is not None:
            return denied
        bad = _checked(wid)
        if bad is not None:
            return bad
        too_big = _too_big()
        if too_big is not None:
            return too_big
        try:
            validate((request.get_json(silent=True) or {}).get("manifest"), wid)
        except Fault as error:
            return _fault_answer(error)
        return jsonify({"ok": True, "valid": True})

    @app.put("/tv/v1/workspaces/user/<wid>")
    def pc_user_ws_save(wid):
        denied = pc_act._guard()
        if denied is not None:
            return denied
        bad = _checked(wid)
        if bad is not None:
            return bad
        too_big = _too_big()
        if too_big is not None:
            return too_big
        body = request.get_json(silent=True) or {}
        base_rev = body.get("base_rev")
        if not isinstance(base_rev, int) or isinstance(base_rev, bool) or base_rev < 0:
            return refuse(400, "send {base_rev, manifest}", code="bad_request")
        try:
            validate(body.get("manifest"), wid)
        except Fault as error:
            return _fault_answer(error)
        with _LOCK:
            current = _load(wid)
            if current is None and base_rev > 0:
                if not _has_history(wid) and not _trashed(wid):
                    return refuse(404, "no such workspace", code="no_workspace")
                _receipt("userws_save", wid, 410)
                return refuse(410, "this workspace was deleted on the PC", code="gone")
            if current is None and _has_history(wid):
                return refuse(409, "a deleted workspace with this id still has its history; restore it or pick another id",
                              code="id_in_use")
            stored_rev = int(current.get("rev") or 0) if current else 0
            if base_rev != stored_rev:
                _receipt("userws_save", wid, 409)
                answer = jsonify({"ok": False, "error": "the workspace changed on the PC", "code": "conflict",
                                  "current": current})
                answer.status_code = 409
                return answer
            saved = _stored(body["manifest"], stored_rev, g.device["id"])
            version = _versions(wid) / ("%06d.json" % saved["rev"])
            try:
                _write(version, saved)
                _write(_file(wid), saved)
            except OSError as error:
                _log.warning("user workspace save failed: %s", type(error).__name__)
                version.unlink(missing_ok=True)          # a version that never went live must not lock the id
                return refuse(503, "the PC could not save that right now", code="pc_unavailable")
        _receipt("userws_save", wid, 200)
        return jsonify({"ok": True, "rev": saved["rev"], "manifest": saved})

    @app.delete("/tv/v1/workspaces/user/<wid>")
    def pc_user_ws_delete(wid):
        denied = pc_act._guard()
        if denied is not None:
            return denied
        bad = _checked(wid)
        if bad is not None:
            return bad
        base_rev = (request.get_json(silent=True) or {}).get("base_rev")
        if base_rev is not None and (not isinstance(base_rev, int) or isinstance(base_rev, bool) or base_rev < 0):
            return refuse(400, "base_rev is a whole number", code="bad_request")
        with _LOCK:
            current = _load(wid)
            if current is None:
                return refuse(404, "no such workspace", code="no_workspace")
            if base_rev is not None and base_rev != current.get("rev"):
                _receipt("userws_delete", wid, 409)
                return refuse(409, "the workspace changed on the PC since you opened it", code="conflict")
            entry = _root() / ".trash" / _trash_name(wid)
            try:
                entry.mkdir(parents=True)
                _file(wid).replace(entry / "workspace.json")
                try:
                    if _versions(wid).is_dir():              # the history goes with it, so a new workspace of the same id starts clean
                        _versions(wid).replace(entry / "versions")
                except OSError:
                    (entry / "workspace.json").replace(_file(wid))       # all or nothing: put the workspace back
                    raise
            except OSError as error:
                _log.warning("user workspace delete failed: %s", type(error).__name__)
                return refuse(503, "the PC could not do that right now", code="pc_unavailable")
        _receipt("userws_delete", wid, 200)
        return jsonify({"ok": True, "restorable": True})

    @app.post("/tv/v1/workspaces/user/<wid>/restore")
    def pc_user_ws_restore(wid):
        """The newest trashed copy of this workspace comes back, with its history, if no workspace has its id now."""
        denied = pc_act._guard()
        if denied is not None:
            return denied
        bad = _checked(wid)
        if bad is not None:
            return bad
        with _LOCK:
            if _load(wid) is not None or _has_history(wid):
                return refuse(409, "a workspace with this id exists", code="id_in_use")
            trash = _root() / ".trash"
            entries = sorted(trash.glob("%s-*" % wid), key=lambda p: p.name) if trash.is_dir() else []
            entries = [e for e in entries if (e / "workspace.json").is_file()
                       and e.name[len(wid) + 1:].isdigit()]
            if not entries:
                return refuse(404, "nothing of that name in the trash", code="not_in_trash")
            newest = entries[-1]
            try:
                (newest / "workspace.json").replace(_file(wid))
                try:
                    if (newest / "versions").is_dir():
                        (newest / "versions").replace(_versions(wid))
                except OSError:
                    _file(wid).replace(newest / "workspace.json")        # all or nothing: leave it in the trash
                    raise
            except OSError as error:
                _log.warning("user workspace restore failed: %s", type(error).__name__)
                return refuse(503, "the PC could not do that right now", code="pc_unavailable")
        _receipt("userws_restore", wid, 200)
        return jsonify({"ok": True, "rev": (_load(wid) or {}).get("rev")})

    @app.get("/tv/v1/workspaces/user/<wid>/versions")
    def pc_user_ws_versions(wid):
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        bad = _checked(wid)
        if bad is not None:
            return bad
        folder, rows = _versions(wid), []
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                rows.append(_row(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, AttributeError):
                continue                                    # one unreadable version never hides the others
        return jsonify({"ok": True, "versions": rows})

    @app.post("/tv/v1/workspaces/user/<wid>/rollback")
    def pc_user_ws_rollback(wid):
        denied = pc_act._guard()
        if denied is not None:
            return denied
        bad = _checked(wid)
        if bad is not None:
            return bad
        body = request.get_json(silent=True) or {}
        target, base_rev = body.get("rev"), body.get("base_rev")
        if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in (target, base_rev)):
            return refuse(400, "send {rev, base_rev}", code="bad_request")
        with _LOCK:
            current = _load(wid)
            if current is None:
                return refuse(410, "this workspace was deleted on the PC", code="gone")
            if int(current.get("rev") or 0) != base_rev:
                answer = jsonify({"ok": False, "error": "the workspace changed on the PC", "code": "conflict",
                                  "current": current})
                answer.status_code = 409
                return answer
            try:
                kept = json.loads((_versions(wid) / ("%06d.json" % target)).read_text(encoding="utf-8"))
                old = {key: kept[key] for key in _TOP if key in kept}
            except (OSError, ValueError, TypeError):
                return refuse(404, "no such version", code="no_version")
            try:
                validate(old, wid)                      # a version that stopped validating is not brought back
            except Fault as error:                      # (Fault is a ValueError: it must be caught on its own)
                return _fault_answer(error)
            saved = _stored(old, base_rev, g.device["id"])
            try:
                _write(_versions(wid) / ("%06d.json" % saved["rev"]), saved)
                _write(_file(wid), saved)
            except OSError as error:
                _log.warning("user workspace rollback failed: %s", type(error).__name__)
                return refuse(503, "the PC could not save that right now", code="pc_unavailable")
        _receipt("userws_rollback", wid, 200)
        return jsonify({"ok": True, "rev": saved["rev"], "manifest": saved})
