"""Workspaces as A2UI documents: the builtin documents Friday ships, the current view of a workspace, and how an
owner's change (a signed proposal from a phone, or a press at the PC) becomes a new version.

Contract draft 3, sections 1-2 and 4. A shipped workspace with no stored file is served from its builtin document,
whose revision is `builtin-<sha256 of its canonical JSON>`; the first change stores the builtin document first and the
change second, both reversible versions in services/a2ui_store. His customizations (accent, density, summary, note,
hidden parts) are properties of the stored document. Every document here is authored `builtin` (V14) and carries no
data of its own (V7): data comes only through declared bindings, resolved by services/a2ui_broker.

tests/unit/test_a2ui_conformance.py and tests/unit/test_tv_ws_view.py hold the rules.
"""
from __future__ import annotations

import copy
import hashlib
import json

from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_store as store

BUILTIN_PREFIX = "builtin-"


def _surface(components: list, bindings: dict) -> list:
    return [{"version": prof.VERSION, "createSurface": {
        "surfaceId": "workspace", "catalogId": prof.CATALOG_ID, "sendDataModel": False,
        "components": components, "metadata": {"friday": {"bindings": bindings}}}}]


def _knowledge() -> list:
    """Knowledge, the first workspace in the single format: his sections and what changed lately, live from the PC."""
    return _surface([
        {"id": "root", "component": "column", "children": ["title", "browse", "sections", "recent"]},
        {"id": "title", "component": "text_block", "style": "heading", "text": "Knowledge"},
        # the shell's own page browser (list, reader, editor) by internal id: "<ws>.<section>", never a URL (V8)
        {"id": "browse", "component": "nav_link", "label": "Browse all pages", "destination": "knowledge.pages"},
        {"id": "sections", "component": "status_block", "title": "Sections", "children": ["sections_table"]},
        {"id": "sections_table", "component": "record_table", "rows": {"@path": "/knowledge/sections"},
         "columns": ["name", "count"], "@accessibility": {"label": "Sections of your wiki and how many pages each has"}},
        {"id": "recent", "component": "status_block", "title": "Recently changed", "children": ["recent_table"]},
        {"id": "recent_table", "component": "record_table", "rows": {"@path": "/knowledge/recent"},
         "columns": ["title", "section", "when"], "@accessibility": {"label": "Pages changed most recently"}},
    ], {
        "sections": {"path": "/knowledge/sections", "query": "knowledge.sections", "params": {}},
        "recent": {"path": "/knowledge/recent", "query": "knowledge.recent", "params": {"limit": 12}},
    })


BUILTINS = {"knowledge": _knowledge}
LABELS = {"knowledge": "Knowledge"}


def builtin(ws: str) -> list:
    """A fresh copy of the builtin document. KeyError for a workspace with none."""
    return copy.deepcopy(BUILTINS[ws]())


def subject(ws: str) -> str:
    return "workspace:%s" % ws


def ctx(ws: str) -> prof.Context:
    return prof.Context(subject=subject(ws), authorship="builtin")


def builtin_rev(ws: str) -> str:
    raw = json.dumps(builtin(ws), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return BUILTIN_PREFIX + hashlib.sha256(raw).hexdigest()


def serves(ws: str, declared_sections) -> bool:
    """True when ws has a view and every section its bindings read is among the device block's declared sections."""
    from agent_friday.services import a2ui_broker
    try:
        view = current(ws)
        return view is not None and a2ui_broker.sections_read(view["doc"]) <= set(declared_sections)
    except (ValueError, store.ViewCorrupt, a2ui_broker.BrokerRefused):
        return False


def current(ws: str) -> dict | None:
    """{view, label, rev, doc, customization, versions, stored} or None. Raises store.ViewCorrupt for a damaged file."""
    stored = store.load(ws) if store._VIEW_ID.fullmatch(ws or "") else None
    if stored is not None:
        return {"view": ws, "label": stored.get("label") or LABELS.get(ws, ws), "rev": stored["rev"],
                "doc": stored["doc"], "customization": stored["customization"],
                "versions": len(stored["versions"]), "stored": True}
    if ws not in BUILTINS:
        return None
    return {"view": ws, "label": LABELS.get(ws, ws), "rev": builtin_rev(ws), "doc": builtin(ws),
            "customization": {}, "versions": 0, "stored": False}


def apply_change(ws: str, change, *, base_rev: str, by: str) -> dict:
    """Apply an owner's change as a new version: `{"customization": patch}` or `{"restore": version_id}`.
    Returns {rev, version}. Raises store.ViewRefused, store.ViewStale, store.ViewCorrupt, PermissionError."""
    if not isinstance(change, dict) or len(change) != 1 or not (set(change) <= {"customization", "restore"}):
        raise store.ViewRefused("change", "a change is {customization} or {restore}")
    view = current(ws)
    if view is None:
        raise store.ViewRefused("version", "there is no such view")
    if not view["stored"]:
        if base_rev != view["rev"]:
            raise store.ViewStale(view["rev"])
        if "restore" in change:
            raise store.ViewRefused("version", "there is no such version")
        return _first_change(ws, view["doc"], change["customization"], by)
    if "customization" in change:
        got = store.set_customization(ws, change["customization"], base_rev=base_rev, by=by)
    else:
        got = store.restore(ws, str(change["restore"]), base_rev=base_rev, by=by, ctx=ctx(ws))
    return {"rev": got["rev"], "version": got["versions"]}


def _first_change(ws: str, doc: list, patch, by: str) -> dict:
    """The first change to a builtin view is ONE write: the builtin document kept as the earlier version and his
    customization current. Nothing is stored when the change is refused, stale or off the record.
    tests/unit/test_tv_ws_view.py: test_the_first_change_is_one_write_the_builtin_kept_as_its_earlier_version."""
    store._validated(doc, ctx(ws))
    look = store._typed_look(patch, doc)

    def build(previous):
        base = {"id": ws, "label": LABELS.get(ws, ws), "subject": subject(ws), "authorship": "builtin", "doc": doc,
                "customization": {}, "versions": [], "updated_by": "builtin", "updated": None}
        return {**base, "customization": look, "versions": [store._snapshot(base, store.KIND_CHANGE)], "updated_by": by}
    got = store._commit(ws, build, store.NEW_REVISION)
    return {"rev": got["rev"], "version": got["versions"]}
