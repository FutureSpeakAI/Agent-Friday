"""Reference renderer for the Friday A2UI profile (Liquid UI L1; roadmap section 41, slice A0).

It turns a document that passed `a2ui_profile.check` into an inert tree and an escaped HTML
string, and REJECTS a hostile document whole: any profile failure, at validation or while data
is bound in, returns the deterministic default view instead, with a receipt naming the rule.
It is the executable contract the desktop React renderer (slice A2) and the Android renderers
(slice A7) must match, and it never trusts the document for anything it draws:

  * text is escaped; no HTML, markdown, auto-linking or URL is ever emitted;
  * the only controls it can emit are `verb_button` (a `<button>` carrying a verb) and
    `nav_link` (a `<button>` carrying an internal destination); no `<a>`, `<input>`, `<form>`,
    `<img>`, `<iframe>`, `<script>`; media and files appear only as store ids;
  * `approval_slot` draws NOTHING from the document: it asks the shell (`pending_approvals`)
    for the approval ids that exist for this subject and emits the shell-only marker
    `data-shell-card` for each, for the shell to fill with its own card component. No
    catalog component can emit that marker;
  * the taint line ("proposed after reading ...") is drawn by the shell around the view;
  * `action_to_verb` turns a renderer action into (verb, context) only for a button that was
    actually drawn, re-checks the verb against its manifest, and never calls the gate itself:
    the caller passes the result to `action_gate.authorize`.
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_friday.services import a2ui_profile as prof
from agent_friday.services.a2ui_profile import Context, ProfileError

DEFAULT_VIEW = [{"type": "text_block", "style": "body", "text": "This view could not be shown. Friday's standard view is used instead."}]


@dataclass
class Rendered:
    ok: bool
    tree: list
    html: str
    receipt: dict | None = None
    taint_line: str = ""
    permitted_actions: dict = field(default_factory=dict)   # component id -> (verb, allowed context keys)


def _lookup(path: str, root: dict, scope):
    if path.startswith("/"):
        cur: Any = root
        parts = [p for p in path.split("/") if p]
    else:
        cur, parts = scope, [p for p in path.split("/") if p]
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _value(v, model: dict, scope):
    """Resolve a literal, a binding, or one of the allowed pure formatting calls to plain data."""
    if isinstance(v, dict) and set(v) == {"@path"}:
        return _lookup(v["@path"], model, scope)
    if isinstance(v, dict) and "@call" in v:
        args = v.get("args") or {}
        got = _value(args.get("value"), model, scope) if isinstance(args, dict) else None
        if v["@call"] == "@index":
            return scope.get("@index") if isinstance(scope, dict) else None
        if v["@call"] == "pluralize" and isinstance(args, dict):
            n = _value(args.get("count"), model, scope)
            return args.get("one", "") if n == 1 else args.get("other", "")
        return "" if got is None else str(got)
    return v


def _check_bound_text(text: Any, comp: dict, key: str, ctx: Context) -> str:
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    if prof.has_url(s):
        raise ProfileError("V8", f"{comp['id']}: bound {key} contains a URL")
    typ = comp["component"]
    if (key in ("label", "title") and typ in ("verb_button", "nav_link", "status_block")) or (
            typ == "text_block" and comp.get("style") == "heading"):
        if hit := prof.reserved_hit(s, ctx.extra_reserved):
            raise ProfileError("V11", f"{comp['id']}: bound {key} uses reserved word {hit!r}")
    return s


def _build(comp_id: str, by_id: dict, model: dict, scope, ctx: Context, pending, actions: dict, depth: int = 0) -> dict:
    c = by_id[comp_id]
    typ = c["component"]
    node: dict = {"type": typ, "id": comp_id}
    acc = c.get("@accessibility") or {}
    for k in ("label", "description"):
        if k in acc:
            node.setdefault("a11y", {})[k] = _check_bound_text(_value(acc[k], model, scope), c, k, ctx)
    if typ == "text_block":
        node["style"] = c.get("style", "body")
        node["text"] = _check_bound_text(_value(c.get("text"), model, scope), c, "text", ctx)
    elif typ == "status_block":
        node["title"] = _check_bound_text(_value(c.get("title"), model, scope), c, "title", ctx)
    elif typ in ("verb_button", "nav_link"):
        node["label"] = _check_bound_text(_value(c.get("label"), model, scope), c, "label", ctx)
        if typ == "verb_button":
            event = c["action"]["event"]
            node["verb"] = c["verb"]
            node["context"] = dict(event.get("context") or {})
            actions[comp_id] = (c["verb"], set(node["context"]))
        else:
            node["destination"] = c["destination"]
    elif typ == "approval_slot":
        flt = c["filter"]
        node["cards"] = [{"approval_id": str(i)} for i in pending(flt["subject"], flt.get("kind"))]
    elif typ not in ("column", "row", "divider"):
        for key in c.keys() - prof._COMMON_KEYS:
            data = _value(c[key], model, scope)
            node[key] = _plain(data, c, ctx)
    kids = c.get("children")
    if kids is not None:
        children = []
        if isinstance(kids, dict):
            rows = _lookup(kids["path"], model, scope) or []
            if not isinstance(rows, list) or len(rows) > prof.MAX_ROWS:
                raise ProfileError("V6", f"{comp_id}: template rows missing or over {prof.MAX_ROWS}")
            for i, row in enumerate(rows):
                item = dict(row) if isinstance(row, dict) else {"value": row}
                item["@index"] = i
                children.append(_build(kids["componentId"], by_id, model, item, ctx, pending, actions, depth + 1))
        else:
            children = [_build(k, by_id, model, scope, ctx, pending, actions, depth + 1) for k in kids]
        node["children"] = children
    return node


def _plain(data: Any, comp: dict, ctx: Context, depth: int = 0):
    """Bound records become plain data: strings checked for URLs, nothing executable."""
    if depth > 6:
        raise ProfileError("V6", f"{comp['id']}: bound data is too deep")
    if isinstance(data, str):
        if prof.has_url(data):
            raise ProfileError("V8", f"{comp['id']}: bound data contains a URL")
        return data
    if isinstance(data, list):
        if len(data) > prof.MAX_ROWS:
            raise ProfileError("V6", f"{comp['id']}: more than {prof.MAX_ROWS} rows")
        return [_plain(v, comp, ctx, depth + 1) for v in data]
    if isinstance(data, dict):
        return {str(k): _plain(v, comp, ctx, depth + 1) for k, v in data.items()}
    return data


def to_html(tree: list) -> str:
    """Escaped HTML for a tree. The only elements are div, span, p, ul, li, table parts, button, hr."""
    def e(v) -> str:
        return _html.escape("" if v is None else str(v), quote=True)

    def one(n: dict) -> str:
        t = n["type"]
        inner = "".join(one(k) for k in n.get("children", []))
        if t in ("column", "row"):
            return f'<div data-a2ui="{t}">{inner}</div>'
        if t == "divider":
            return '<hr data-a2ui="divider">'
        if t == "status_block":
            return f'<div data-a2ui="status_block"><p data-a2ui-title>{e(n.get("title"))}</p>{inner}</div>'
        if t == "text_block":
            tag = "p"
            return f'<{tag} data-a2ui="text_block" data-style="{e(n.get("style"))}">{e(n.get("text"))}</{tag}>'
        if t == "verb_button":
            return (f'<button type="button" data-a2ui="verb_button" data-verb="{e(n["verb"])}">'
                    f'{e(n.get("label"))}</button>')
        if t == "nav_link":
            return (f'<button type="button" data-a2ui="nav_link" data-nav="{e(n["destination"])}">'
                    f'{e(n.get("label"))}</button>')
        if t == "approval_slot":
            cards = "".join(f'<div data-shell-card="approval" data-approval-id="{e(c["approval_id"])}"></div>'
                            for c in n["cards"])
            return f'<div data-shell-slot="approval">{cards or "<p data-shell-empty></p>"}</div>'
        body = {k: v for k, v in n.items() if k not in ("type", "id", "a11y", "children")}
        return f'<div data-a2ui="{e(t)}"><pre data-a2ui-data>{e(_dump(body))}</pre></div>'

    return "".join(one(n) for n in tree)


def _dump(v) -> str:
    import json
    return json.dumps(v, sort_keys=True, ensure_ascii=True)


def render(messages, ctx: Context, *, data: dict | None = None,
           pending_approvals: Callable[[str, str | None], list] = lambda subject, kind: [],
           taint: str = "", default_view: list | None = None,
           receipt_sink: Callable[[dict], None] | None = None) -> Rendered:
    """Validate and draw a view. `data` is what the server's closed-query resolver returned
    for the declared bindings; a model-authored document carries none of its own."""
    fallback = default_view if default_view is not None else DEFAULT_VIEW
    try:
        doc = prof.check(messages, ctx)
        model = dict(doc["data"])
        model.update(data or {})
        by_id = {c["id"]: c for c in doc["components"]}
        actions: dict = {}
        tree = [_build("root", by_id, model, {}, ctx, pending_approvals, actions)]
    except ProfileError as e:
        receipt = {"rule": e.rule, "detail": e.detail, "subject": ctx.subject, "authorship": ctx.authorship}
        if receipt_sink:
            receipt_sink(receipt)
        return Rendered(False, list(fallback), to_html(list(fallback)), receipt)
    line = ("proposed after reading: " + taint) if taint else ""
    return Rendered(True, tree, to_html(tree), None, line, actions)


def action_to_verb(message: dict, rendered: Rendered, ctx: Context) -> tuple[str, dict]:
    """The verb and context a renderer `action` stands for, or ProfileError. Only a button that
    was drawn can act, its name must be its verb, and the manifest is read again now. The caller
    passes the result to action_gate.authorize; this function authorizes nothing."""
    body = message.get("action") if isinstance(message, dict) else None
    if not isinstance(body, dict) or message.get("version") != prof.VERSION:
        raise ProfileError("V2", "not an action message")
    src = body.get("sourceComponentId")
    if src not in rendered.permitted_actions:
        raise ProfileError("V9", "no such button was drawn")
    verb, allowed = rendered.permitted_actions[src]
    if body.get("name") != verb:
        raise ProfileError("V9", "the action name is not the button's verb")
    context = body.get("context") or {}
    if not isinstance(context, dict) or set(context) - allowed:
        raise ProfileError("V9", "context keys the button did not carry")
    manifest = ctx.verbs(verb) or {}
    if (not manifest or manifest.get("gate_class") in prof.BANNED_GATE_CLASSES
            or set(manifest.get("effects") or ()) & prof.BANNED_EFFECTS):
        raise ProfileError("V10", f"verb {verb!r} may not be driven from a generated view")
    return verb, context
