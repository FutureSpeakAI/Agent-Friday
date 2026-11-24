"""Workspaces as A2UI documents: the builtin documents Friday ships, the current view of a workspace, and how an
owner's change (a signed proposal from a phone, or a press at the PC) becomes a new version.

Contract draft 3, sections 1-2 and 4. A shipped workspace with no stored file is served from its builtin document,
whose revision is `builtin-<sha256 of its canonical JSON>`; the first change stores the builtin document first and the
change second, both reversible versions in services/a2ui_store. His customizations (accent, density, summary, note,
hidden parts) are properties of the stored document. Every document here is authored `builtin` (V14) and carries no
data of its own (V7): data comes only through declared bindings, resolved by services/a2ui_broker.

His custom workspaces (D-D) are re-authored here as builtin documents, never converted from HTML or CSS. Until the first
A2UI change, a workspace's look is carried from its Studio customization (services/workspace_studio): accent, density,
note and summary, each only where this format takes it; Studio's styles, quick actions and hidden selectors are not
carried. The carried look is part of the builtin rev, so a phone holding the rev from before a Studio change is stale,
never merged. The first change stores the carried look as the earlier version; from then on the stored document is the
record, and Studio changes no longer reach it.

tests/unit/test_a2ui_conformance.py, test_tv_ws_view.py and test_a2ui_custom_workspaces.py hold the rules.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging

from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_store as store

_log = logging.getLogger(__name__)

BUILTIN_PREFIX = "builtin-"
#: The Studio look a builtin document carries until its first A2UI change (contract D-D).
CARRIED_KEYS = ("accent", "density", "note", "summary")


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


def _futurespeak() -> list:
    """FutureSpeak (Sites), his first custom workspace in the single format: his projects and how each one's deploy
    stands, live from the PC. Pipeline, revenue and legal are not in it."""
    return _surface([
        {"id": "root", "component": "column", "children": ["title", "browse", "projects"]},
        {"id": "title", "component": "text_block", "style": "heading", "text": "Sites"},
        {"id": "browse", "component": "nav_link", "label": "All projects", "destination": "futurespeak.projects"},
        {"id": "projects", "component": "status_block", "title": "Projects", "children": ["projects_table"]},
        {"id": "projects_table", "component": "record_table", "rows": {"@path": "/futurespeak/projects"},
         "columns": ["name", "status", "when"],
         "@accessibility": {"label": "Your projects and how each one's deploy stands"}},
    ], {
        "projects": {"path": "/futurespeak/projects", "query": "futurespeak.projects", "params": {}},
    })


BUILTINS = {"knowledge": _knowledge, "futurespeak": _futurespeak}
#: The registry's names (static/workspace_registry.js).
LABELS = {"knowledge": "Knowledge", "futurespeak": "Sites"}


def builtin(ws: str) -> list:
    """A fresh copy of the builtin document. KeyError for a workspace with none."""
    return copy.deepcopy(BUILTINS[ws]())


def subject(ws: str) -> str:
    return "workspace:%s" % ws


def ctx(ws: str) -> prof.Context:
    return prof.Context(subject=subject(ws), authorship="builtin")


#: The phone's approval-card strings; a document he approves may not echo them (V11 extra_reserved).
CARD_STRINGS = ("Approve", "Deny", "Not now", "Friday needs you", "On your PC only")
LABEL_MAX = 80


def doc_ctx(ws: str) -> prof.Context:
    """The one context for a document he approves, and for every restore, undo and reset of a view: checked as
    Friday's own documents are (composer, model_authored: no data of its own), no verb lookup (V9 fails closed), and
    the card strings reserved. Design: ftv/program/specs/A2UI_SLICE3_OWNER_DOCUMENTS_2026-11-24.md."""
    return prof.Context(subject=subject(ws), authorship="composer", model_authored=True, extra_reserved=CARD_STRINGS)


#: Props that carry rows of data: in a document from a device they are always a binding, never literal rows.
_DATA_PROPS = frozenset({"rows", "events", "series", "sources", "items"})
#: Keys whose values are structure, not words anyone reads.
_STRUCTURE = frozenset({"id", "component", "children", "style", "kind", "destination", "@path", "variant"})


def _words(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if k not in _STRUCTURE:
                yield from _words(v)
    elif isinstance(value, list):
        for v in value:
            yield from _words(v)


def check_document(ws: str, doc) -> list:
    """A document that may become a view's current one from a device: the profile under doc_ctx, then two rules the
    profile keeps only for headings and the data model: every data prop is a binding (V7), and no literal string
    anywhere echoes a reserved word or card string (V11). Raises store.ViewRefused.
    tests/unit/test_tv_ws_view_documents.py: test_data_in_a_document_he_approves_only_ever_comes_from_a_binding,
    test_a_card_word_anywhere_in_the_document_is_refused."""
    store._validated(doc, doc_ctx(ws))
    for message in doc:
        for c in ((message.get("createSurface") or {}).get("components") or []) if isinstance(message, dict) else []:
            if not isinstance(c, dict):
                continue
            for prop in _DATA_PROPS & c.keys():
                v = c[prop]
                if not (isinstance(v, dict) and set(v) == {"@path"}):
                    raise store.ViewRefused("V7", "%s: %s must come from a binding" % (c.get("id"), prop))
            for text in _words(c):
                if hit := prof.reserved_hit(text, CARD_STRINGS):
                    raise store.ViewRefused("V11", "%s uses reserved word %r" % (c.get("id"), hit))
    return doc


def _check_reads(doc: list, sections) -> None:
    """Every binding is in the broker's vocabulary and reads a section the device block serves; otherwise the view
    would be hidden after the write and every later proposal, restore included, would 404."""
    from agent_friday.services import a2ui_broker
    try:
        read = a2ui_broker.sections_read(doc)
    except a2ui_broker.BrokerRefused as e:
        raise store.ViewRefused("bindings", str(e)) from e
    if sections is None or not read <= set(sections):
        raise store.ViewRefused("bindings", "this workspace does not serve what that document reads")


def _label(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > LABEL_MAX \
            or any(ord(c) < 32 or 0x7f <= ord(c) < 0xa0 for c in value):
        raise store.ViewRefused("label", "a label is one line of at most %d characters" % LABEL_MAX)
    return value.strip()


def carried_look(ws: str, doc: list) -> dict:
    """His Studio look for ws as the document properties this format takes: each of CARRIED_KEYS the A2UI look
    accepts on its own. An unreadable Studio file carries nothing."""
    from agent_friday.services import workspace_studio
    try:
        look = workspace_studio.load_ws_doc(ws).get("customization") or {}
    except (OSError, ValueError) as e:
        _log.warning("a2ui view %s: Studio look unreadable: %s", ws, type(e).__name__)
        return {}
    out: dict = {}
    for key in CARRIED_KEYS:
        if look.get(key) in (None, ""):
            continue
        try:
            out.update(store._typed_look({key: look[key]}, doc))
        except store.ViewRefused:
            continue
    return out


def _rev_of(doc: list, look: dict) -> str:
    """The builtin rev: the document alone (as before slice 2) when nothing is carried, else document and look."""
    body = {"doc": doc, "customization": look} if look else doc
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return BUILTIN_PREFIX + hashlib.sha256(raw).hexdigest()


def builtin_rev(ws: str) -> str:
    doc = builtin(ws)
    return _rev_of(doc, carried_look(ws, doc))


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
    doc = builtin(ws)
    look = carried_look(ws, doc)
    return {"view": ws, "label": LABELS.get(ws, ws), "rev": _rev_of(doc, look), "doc": doc,
            "customization": look, "versions": 0, "stored": False}


def _kind(change) -> str:
    if isinstance(change, dict):
        if set(change) == {"document", "label"}:
            return "document"
        if change == {"reset": True}:
            return "reset"
        if len(change) == 1 and set(change) <= {"customization", "restore"}:
            return next(iter(change))
    raise store.ViewRefused("change", "a change is {customization}, {restore}, {document, label} or {reset: true}")


def apply_change(ws: str, change, *, base_rev: str, by: str, sections=None) -> dict:
    """Apply an owner's change as a new version: `{"customization": patch}`, `{"restore": version_id}`,
    `{"document": [...], "label": str}` or `{"reset": true}`. `sections` are the device block's declared sections;
    a document or a restore that reads anything else is refused. Returns {rev, version[, dropped_hidden]}.
    Raises store.ViewRefused, store.ViewStale, store.ViewCorrupt, PermissionError."""
    kind = _kind(change)
    view = current(ws)
    if view is None:
        raise store.ViewRefused("version", "there is no such view")
    if kind == "document":
        return _document(ws, view, change["document"], _label(change["label"]), base_rev, by, sections)
    if kind == "reset":
        return _reset(ws, view, base_rev, by)
    if not view["stored"]:
        if base_rev != view["rev"]:
            raise store.ViewStale(view["rev"])
        if "restore" in change:
            raise store.ViewRefused("version", "there is no such version")
        return _first_change(ws, view["doc"], view["customization"], change["customization"], by)
    if "customization" in change:
        got = store.set_customization(ws, change["customization"], base_rev=base_rev, by=by)
    else:
        target = next((v for v in (store.load(ws) or {}).get("versions", []) if v.get("id") == str(change["restore"])),
                      None)
        if target is not None:
            check_document(ws, target["doc"])
            _check_reads(target["doc"], sections)
        got = store.restore(ws, str(change["restore"]), base_rev=base_rev, by=by, ctx=doc_ctx(ws))
    return {"rev": got["rev"], "version": got["versions"]}


def _document(ws: str, view: dict, doc, label: str, base_rev: str, by: str, sections) -> dict:
    """His approved document as the view's new current one. The first one keeps the builtin (with its carried look)
    as the earlier version, in the same write. His customization stays; hidden ids the new document no longer has are
    dropped and named."""
    check_document(ws, doc)
    _check_reads(doc, sections)
    ids = store._component_ids(doc)
    if not view["stored"] and base_rev != view["rev"]:
        raise store.ViewStale(view["rev"])
    hidden = list((view["customization"] or {}).get("hidden") or [])
    dropped = [h for h in hidden if h not in ids]

    def build(previous):
        if previous is None:
            previous = {"id": ws, "label": LABELS.get(ws, ws), "subject": subject(ws), "authorship": "builtin",
                        "doc": view["doc"], "customization": dict(view["customization"]), "versions": [],
                        "updated_by": "builtin", "updated": None}
        versions = list(previous["versions"]) + [store._snapshot(previous, store.KIND_CHANGE)]
        return {**previous, "label": label, "subject": subject(ws), "authorship": "composer", "model_authored": True,
                "approved_by": by, "doc": doc, "customization": store._kept_hidden(previous["customization"], doc),
                "versions": versions[-store.MAX_VERSIONS:], "updated_by": by}
    got = store._commit(ws, build, base_rev if view["stored"] else store.NEW_REVISION)
    return {"rev": got["rev"], "version": got["versions"], "dropped_hidden": dropped}


def _reset(ws: str, view: dict, base_rev: str, by: str) -> dict:
    """Back to the builtin document and its carried Studio look, rebuilt from code, as a new version (so the version
    cap can never put the builtin out of reach). With nothing stored it changes nothing and answers the builtin rev."""
    if ws not in BUILTINS:
        raise store.ViewRefused("version", "this view has no builtin to go back to")
    if not view["stored"]:
        if base_rev != view["rev"]:
            raise store.ViewStale(view["rev"])
        return {"rev": view["rev"], "version": 0}
    doc = check_document(ws, builtin(ws))

    def build(previous):
        versions = list(previous["versions"]) + [store._snapshot(previous, store.KIND_CHANGE)]
        return {**previous, "label": LABELS.get(ws, ws), "authorship": "builtin", "model_authored": False,
                "approved_by": by, "doc": doc, "customization": carried_look(ws, doc),
                "versions": versions[-store.MAX_VERSIONS:], "updated_by": by}
    got = store._commit(ws, build, base_rev)
    return {"rev": got["rev"], "version": got["versions"]}


def _first_change(ws: str, doc: list, carried: dict, patch, by: str) -> dict:
    """The first change to a builtin view is ONE write: the builtin document with its carried look kept as the earlier
    version, and his change merged onto that look current. Nothing is stored when the change is refused, stale or off
    the record. tests/unit/test_tv_ws_view.py: test_the_first_change_is_one_write_the_builtin_kept_as_its_earlier_version;
    tests/unit/test_a2ui_custom_workspaces.py: test_the_first_change_keeps_the_carried_look_as_the_earlier_version_and_merges_onto_it."""
    store._validated(doc, ctx(ws))
    look = {**carried, **store._typed_look(patch, doc)}

    def build(previous):
        base = {"id": ws, "label": LABELS.get(ws, ws), "subject": subject(ws), "authorship": "builtin", "doc": doc,
                "customization": dict(carried), "versions": [], "updated_by": "builtin", "updated": None}
        return {**base, "customization": look, "versions": [store._snapshot(base, store.KIND_CHANGE)], "updated_by": by}
    got = store._commit(ws, build, store.NEW_REVISION)
    return {"rev": got["rev"], "version": got["versions"]}
