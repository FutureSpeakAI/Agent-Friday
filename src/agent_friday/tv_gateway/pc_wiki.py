"""Reading, editing and creating wiki pages on the PC from the owner's phone or tablet (phone workspaces parity PW-G4;
``pc_act``).

Who: an owner-bound phone or tablet that holds ``pc_act`` (the job routes' guard). What:
* ``GET  /tv/v1/wiki/source/<ref>``: the page's Markdown and its ``rev`` (the desktop's own ``before_sha256``:
  SHA-256 of the text with LF line endings). A phone sends back the rev it was given and never recomputes it: editors
  normalise text, and the PC compares its own hash;
* ``PUT  /tv/v1/wiki/page/<ref>`` ``{base_rev, markdown}``: replaces the page through the desktop's wiki write path,
  which refuses (409 ``stale``) when the page no longer hashes to ``base_rev``; the phone then gets ``409 conflict``
  with the PC's current text and rev and chooses (keep mine, keep the PC's, save mine as a copy). Nothing is merged or
  overwritten silently;
* ``POST /tv/v1/wiki/page`` ``{path, title, markdown}``: a new page; an existing one is never replaced (409 ``exists``).
``<ref>`` is a wiki-relative path ending in ``.md`` or ``.txt`` (no ``..``, no backslash, no leading slash); the desktop
checks it again. A page that reads as the locked-vault placeholder is refused (409 ``locked``). Page text is never
logged and the receipt (``device_act``) names the action, the ref and the outcome only. Wiki text is data: nothing in it
is read as an instruction. Each rule is held by a test in tests/unit/test_tv_pc_wiki.py.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from flask import Flask, Response, g, jsonify, request

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import pc_act, pc_read, tailnet
from agent_friday.tv_gateway.pc_act_media import _desk_send
from agent_friday.tv_gateway.pc_common import refuse

_log = logging.getLogger(__name__)

MAX_PAGE_BYTES = 512 * 1024
MAX_REF, MAX_TITLE = 200, 200
REV = re.compile(r"^[0-9a-f]{64}$")
_SEGMENT = re.compile(r'^[^\x00-\x1f\\/:*?"<>|]+$')


def valid_ref(ref) -> bool:
    """A wiki-relative page path: segments with no control or Windows-reserved characters, none starting with a dot (so
    no ``.`` or ``..``), ending in ``.md`` or ``.txt``. Guarded by test_a_bad_ref_never_reaches_the_desktop."""
    if not isinstance(ref, str) or not (0 < len(ref) <= MAX_REF) or not ref.endswith((".md", ".txt")):
        return False
    return all(_SEGMENT.match(part) and not part.startswith(".") for part in ref.split("/"))


def _rev(text: str) -> str:
    from agent_friday.services.wiki_engine import wiki_before_sha256
    return wiki_before_sha256(text)


def _locked(text: str) -> bool:
    from agent_friday.services.wiki_engine import VAULT_LOCKED_PLACEHOLDER
    return text.startswith(VAULT_LOCKED_PLACEHOLDER)


def _read_page(ref: str):
    """(status, page or None): the desktop's page with its text and rev."""
    status, body, _ = pc_read._desk("/api/wiki/page", {"path": ref})
    if status != 200:
        return status, None
    try:
        data = json.loads(body)
    except ValueError:
        return 503, None
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        return 503, None
    return 200, {"markdown": content, "rev": _rev(content), "locked": _locked(content),
                 "modified": data.get("modified")}


def _receipt(action: str, ref: str, outcome: int) -> None:
    device_pairing._receipt("device_act", g.device, right=pc_act.RIGHT, action=action, wiki=ref, outcome=outcome,
                            via=tailnet.via())


def _unavailable(action: str, error: Exception) -> Response:
    _log.warning("pc_act wiki %s failed: %s", action, type(error).__name__)
    return refuse(503, "the PC could not do that right now", code="pc_unavailable")


def _text(body: dict, name: str) -> Optional[str]:
    value = body.get(name)
    return value if isinstance(value, str) and len(value.encode("utf-8")) <= MAX_PAGE_BYTES else None


def register(app: Flask) -> None:
    @app.get("/tv/v1/wiki/source/<path:ref>")
    def pc_wiki_source(ref):
        denied = pc_act._guard()
        if denied is not None:
            return denied
        if not valid_ref(ref):
            return refuse(400, "not a wiki page path", code="bad_ref")
        try:
            status, page = _read_page(ref)
        except Exception as error:
            return _unavailable("wiki_source", error)
        if status == 404:
            return refuse(404, "this PC has no wiki page %r" % ref, code="no_page")
        if page is None:
            return refuse(503, "the PC could not do that right now", code="pc_unavailable")
        if page["locked"]:
            return refuse(409, "this page is encrypted and the vault is locked on the PC", code="locked")
        return jsonify({"ok": True, "ref": ref, "markdown": page["markdown"], "rev": page["rev"],
                        "modified": page["modified"]})

    @app.put("/tv/v1/wiki/page/<path:ref>")
    def pc_wiki_save(ref):
        denied = pc_act._guard()
        if denied is not None:
            return denied
        body = request.get_json(silent=True) or {}
        base_rev, markdown = body.get("base_rev"), _text(body, "markdown")
        if not valid_ref(ref):
            return refuse(400, "not a wiki page path", code="bad_ref")
        if not (isinstance(base_rev, str) and REV.match(base_rev)) or markdown is None:
            return refuse(400, "send {base_rev, markdown}", code="bad_request")
        if not markdown.strip():
            return refuse(400, "an empty page is not saved; delete the page instead", code="empty_page")
        try:
            status, page = _read_page(ref)
            if status == 404:
                return refuse(404, "this PC has no wiki page %r" % ref, code="no_page")
            if page is None:
                return refuse(503, "the PC could not do that right now", code="pc_unavailable")
            if page["locked"]:
                return refuse(409, "this page is encrypted and the vault is locked on the PC", code="locked")
            code, _ = _desk_send("POST", "/api/wiki/update", {"file": ref, "new_value": markdown,
                                                              "mode": "replace_file", "before_sha256": base_rev})
            if code == 409:
                current_status, current = _read_page(ref)
                _receipt("wiki_save", ref, 409)
                if current_status == 404:
                    return refuse(404, "this page was removed on the PC", code="no_page")
                if current is None:
                    return refuse(503, "the PC could not do that right now", code="pc_unavailable")
                if current["locked"]:
                    return refuse(409, "this page is encrypted and the vault is locked on the PC", code="locked")
                shaped = jsonify({"ok": False, "error": "the page changed on the PC since you opened it",
                                  "code": "conflict",
                                  "current": {"markdown": current["markdown"], "rev": current["rev"]}})
                shaped.status_code = 409
                return shaped
        except Exception as error:
            return _unavailable("wiki_save", error)
        _receipt("wiki_save", ref, code)
        if code >= 500:
            return refuse(503, "the PC could not do that right now", code="pc_unavailable")
        if code >= 300:
            return refuse(400, "the PC did not accept that page", code="not_saved")
        # The rev of exactly what was written (the desktop ends a page with one newline), not of a second read: another
        # writer could change the page between the two, and the phone would then hold that writer's rev as its own.
        return jsonify({"ok": True, "ref": ref, "rev": _rev(markdown if markdown.endswith("\n") else markdown + "\n")})

    @app.post("/tv/v1/wiki/page")
    def pc_wiki_create():
        denied = pc_act._guard()
        if denied is not None:
            return denied
        body = request.get_json(silent=True) or {}
        path, title, markdown = body.get("path"), body.get("title"), _text(body, "markdown")
        title_ok = isinstance(title, str) and 0 < len(title.strip()) <= MAX_TITLE
        if not valid_ref(path) or markdown is None or not title_ok:
            return refuse(400, "send {path, title, markdown}", code="bad_request")
        if not markdown.lstrip().startswith("#"):
            markdown = "# %s\n\n%s" % (" ".join(title.split()), markdown)
        try:
            code, _ = _desk_send("PUT", "/api/wiki/edit", {"file": path, "content": markdown, "create": True})
        except Exception as error:
            return _unavailable("wiki_create", error)
        _receipt("wiki_create", path, code)
        if code == 409:
            return refuse(409, "a page with that name already exists", code="exists")
        if code >= 500:
            return refuse(503, "the PC could not do that right now", code="pc_unavailable")
        if code >= 300:
            return refuse(400, "the PC did not accept that page", code="not_saved")
        return jsonify({"ok": True, "ref": path, "rev": _rev(markdown)})
