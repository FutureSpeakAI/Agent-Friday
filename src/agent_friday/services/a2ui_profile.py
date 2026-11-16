"""The Friday profile of A2UI 1.0: a validator for generated views (Liquid UI layer L1; roadmap
section 41, slice A0). Canonical rules: Salon/Crews spec section D5.0 (P1-P6, V1-V14), adopted
2026-11-11 and approved by Stephen 2026-11-12.

A2UI is a format, not a trust policy: its basic catalog has inputs, a Modal, openUrl and
agent-written confirm buttons, and it cannot tell UI the app drew from UI an agent drew. This
module is the trust policy. A document that is valid A2UI but breaks any rule here is invalid
Friday L1 and is rejected WHOLE (`check` raises ProfileError(rule, detail); `violations` lists
them). It is pure: no I/O, no model, no network, no gate call. It never approves anything.

Honest scope of this first slice:
  * V1 here is a strict structural check of the message and component shapes the profile
    allows (spec 3.2/3.3), not yet the official A2UI 1.0 JSON Schemas copied and pinned by
    hash (AU-E07). That copy is a follow-up; every other rule is enforced now.
  * V9/V10 read verb facts from an injected `verbs` lookup (name -> {gate_class, effects,
    args}). Real Laya manifests do not carry effect flags yet, so wiring that lookup is its
    own slice; with no lookup, or a verb the lookup does not know, every button fails closed.
  * V11's phrase list is the English card header phrases plus a small built-in list of
    translations; the shell's per-locale card strings are passed in as `extra_reserved`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

CATALOG_ID = "https://agentfriday.ai/a2ui/catalogs/core/v1"   # placeholder name (spec 7.1)
VERSION = "1.0"

#: server -> renderer messages (P1); renderer -> server messages.
TO_RENDERER = frozenset({"createSurface", "updateComponents", "updateDataModel", "deleteSurface"})
TO_SERVER = frozenset({"action", "error"})
_SURFACE_KEYS = frozenset({"surfaceId", "catalogId", "sendDataModel", "components", "dataModel", "metadata"})

MAX_NODES, MAX_DEPTH, MAX_TEXT_BYTES, MAX_ROWS = 60, 6, 4096, 200
ALLOWED_FUNCTIONS = frozenset({"formatString", "formatNumber", "formatCurrency", "formatDate",
                               "pluralize", "@index"})

# component -> (allowed props, may have children, allowed parents or None for any)
_VALUE = "value"          # a literal or a binding; strings are checked by the text rules
COMPONENTS: dict[str, dict] = {
    "column": {"props": {"children"}, "children": True},
    "row": {"props": {"children"}, "children": True},
    "divider": {"props": set(), "children": False},
    "status_block": {"props": {"title", "children"}, "children": True},
    "text_block": {"props": {"text", "style"}, "children": False},
    "key_values": {"props": {"rows"}, "children": False},
    "record_table": {"props": {"rows", "columns"}, "children": False},
    "timeline": {"props": {"events"}, "children": False},
    "file_line": {"props": {"file"}, "children": False},
    "cost_line": {"props": {"amount", "unit", "label"}, "children": False},
    "token_line": {"props": {"tokens", "label"}, "children": False},
    "chart": {"props": {"kind", "series"}, "children": False},
    "source_list": {"props": {"sources"}, "children": False},
    "media_tile": {"props": {"media"}, "children": False},
    "media_grid": {"props": {"items"}, "children": False},
    "audio_clip": {"props": {"media"}, "children": False},
    "verb_button": {"props": {"label", "verb", "action"}, "children": False},
    "nav_link": {"props": {"label", "destination"}, "children": False},
    "approval_slot": {"props": {"filter"}, "children": False, "parents": {"column", "status_block"}},
}
_COMMON_KEYS = frozenset({"id", "component", "@catalogId", "@accessibility", "@metadata", "@checks"})
TEXT_STYLES = frozenset({"body", "caption", "heading"})
CHART_KINDS = frozenset({"bar", "line", "pie", "area"})

#: V10: a verb is refused if its manifest gate class or any effect is one of these.
BANNED_GATE_CLASSES = frozenset({"owner_only", "by_design", "loud"})
BANNED_EFFECTS = frozenset({
    "approves", "decides_card", "approval", "grants", "widens_authority", "set_right", "preset_apply",
    "spends_money", "cap_set", "cap_raise", "purchase", "checkout", "top_up",
    "secrets", "key_write", "credential", "vault", "unlock",
    "settings_write", "set_setting", "sends_as_owner", "publishes", "deletes_external",
    "governance", "claws_write", "trust_write", "policy_write"})


class ProfileError(ValueError):
    """The document breaks a profile rule; `rule` is its V-id (V1..V14) and the whole document is rejected."""

    def __init__(self, rule: str, detail: str = ""):
        super().__init__(f"{rule}: {detail}" if detail else rule)
        self.rule, self.detail = rule, detail


@dataclass
class Context:
    """What the validator needs from the shell. `verbs` maps a verb name to
    {"gate_class": str, "effects": [str], "args": [str]}; absent or unknown means refuse."""
    subject: str
    verbs: Callable[[str], dict | None] = lambda name: None
    authorship: str = "composer"            # "composer" | "friday_liquid" | anything else is outside content
    model_authored: bool = False
    extra_reserved: Iterable[str] = field(default_factory=tuple)   # the shell's card strings, every locale


# ── reserved labels (V11) ──────────────────────────────────────────────────────────

_RESERVED_WORDS = frozenset("""approve approved approves approving allow allowed allows accept accepted agree
deny denied grant granted grants authorize authorise authorized authorised confirm confirmed confirms pay pays
paying purchase buy buys checkout spend unlock password passcode pin key
aprobar aprobado permitir autorizar confirmar pagar comprar approuver autoriser confirmer payer acheter
genehmigen erlauben zulassen bestatigen zahlen kaufen autorizzare confermare pagare""".split())
_RESERVED_PHRASES = ("verify identity", "send money", "top up", "sign in", "log in", "needs you",
                     "waiting for you", "wants to", "needs your", "wants your")
_RESERVED_CJK = ("承認", "許可", "支払", "确认", "允许", "支付", "確認", "승인", "허용")
_CONFUSABLE = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "к": "k", "м": "m", "н": "h",
    "т": "t", "в": "b", "і": "i", "ѕ": "s", "ј": "j", "ԁ": "d", "ɡ": "g", "ℓ": "l", "ο": "o", "ν": "v",
    "α": "a", "ε": "e", "ι": "i", "κ": "k", "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "ꓲ": "l", "0": "o",
    "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a"})


def fold(text: str) -> str:
    """Lower-case letters only: NFKD, marks and invisible format characters dropped, look-alike
    letters and digits folded to Latin, everything else a space."""
    out = []
    for ch in unicodedata.normalize("NFKD", str(text)):
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Cf", "Cc") and ch not in "\t\n ":
            continue
        out.append(ch)
    s = "".join(out).lower().translate(_CONFUSABLE)
    return re.sub(r"[^\w]+|_", " ", s).strip()


def reserved_hit(text: Any, extra: Iterable[str] = ()) -> str | None:
    """The reserved word or phrase `text` uses, or None. Runs of single letters ("a p p r o v e")
    are joined first, so spacing does not hide a word."""
    folded = fold(text)
    if not folded:
        return None
    tokens = []
    run = []
    for tok in folded.split():
        if len(tok) == 1 and tok.isalpha():
            run.append(tok)
            continue
        if run:
            tokens.append("".join(run))
            run = []
        tokens.append(tok)
    if run:
        tokens.append("".join(run))
    joined = " ".join(tokens)
    for tok in tokens:
        if tok in _RESERVED_WORDS:
            return tok
    for phrase in _RESERVED_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", joined):
            return phrase
    for phrase in extra:
        f = fold(phrase)
        if f and re.search(rf"\b{re.escape(f)}\b", joined):
            return phrase
    raw = str(text)
    for cjk in _RESERVED_CJK:
        if cjk in raw:
            return cjk
    return None


# ── URLs (V8) ──────────────────────────────────────────────────────────────────────

_URL = re.compile(r"(?i)(?:\b(?:https?|javascript|data|file|mailto|intent|ftp|blob|tel)\s*:|\bwww\.|"
                  r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)+/[^\s]*|//[a-z0-9.-]+\.[a-z]{2,})")


def has_url(text: str) -> bool:
    return bool(_URL.search(text)) or bool(_URL.search(unicodedata.normalize("NFKC", text)))


# ── walking helpers ────────────────────────────────────────────────────────────────

def _is_binding(v) -> bool:
    return isinstance(v, dict) and set(v) == {"@path"} and isinstance(v["@path"], str)


def _is_call(v) -> bool:
    return isinstance(v, dict) and ("@call" in v or "callFunction" in v)


def _strings(node, path=""):
    """Every literal string under node, with a key path (keys count as strings too)."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield path + "/" + str(k), str(k)
            yield from _strings(v, path + "/" + str(k))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}/{i}")


def _calls(node):
    if isinstance(node, dict):
        if _is_call(node):
            yield node
        for v in node.values():
            yield from _calls(v)
    elif isinstance(node, list):
        for v in node:
            yield from _calls(v)


def _resolve(model: dict, pointer: str):
    cur: Any = model
    for part in [p for p in pointer.split("/") if p != ""]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


# ── the document ───────────────────────────────────────────────────────────────────

def _flatten(messages) -> tuple[list[dict], dict, dict, str | None]:
    """(components, data model, metadata, surface id) from a message list already shape-checked."""
    comps: list[dict] = []
    data: dict = {}
    meta: dict = {}
    sid = None
    for m in messages:
        key = next(k for k in m if k != "version")
        body = m[key]
        sid = body.get("surfaceId", sid)
        if key == "createSurface":
            comps += list(body.get("components") or [])
            data.update(body.get("dataModel") or {})
            meta = body.get("metadata") or meta
        elif key == "updateComponents":
            comps += list(body["components"])
        elif key == "updateDataModel":
            path = body.get("path", "/")
            if path in ("/", ""):
                if isinstance(body.get("value"), dict):
                    data.update(body["value"])
            else:
                cur = data
                parts = [p for p in path.split("/") if p]
                for p in parts[:-1]:
                    cur = cur.setdefault(p, {})
                if parts:
                    cur[parts[-1]] = body.get("value")
    return comps, data, meta, sid


def _v1_v2_messages(messages, direction: str) -> None:
    if not isinstance(messages, list) or not messages:
        raise ProfileError("V2", "a document is a non-empty list of messages")
    allowed = TO_RENDERER if direction == "to_renderer" else TO_SERVER
    created = 0
    for m in messages:
        if not isinstance(m, dict) or m.get("version") != VERSION:
            raise ProfileError("V2", 'every message has "version": "1.0"')
        keys = [k for k in m if k != "version"]
        if len(keys) != 1:
            raise ProfileError("V2", "exactly one envelope key per message")
        if keys[0] not in allowed:
            raise ProfileError("V2", f"message {keys[0]!r} is not allowed in this direction")
        body = m[keys[0]]
        if not isinstance(body, dict):
            raise ProfileError("V1", f"{keys[0]} must be an object")
        if keys[0] == "createSurface":
            created += 1
            if set(body) - _SURFACE_KEYS or "surfaceId" not in body:
                raise ProfileError("V1", "createSurface has unknown or missing fields")
            if body.get("sendDataModel") is not False:
                raise ProfileError("V7", "sendDataModel must be false")
            if body.get("catalogId") != CATALOG_ID:
                raise ProfileError("V3", "every surface sets the Friday catalog as its default")
        if keys[0] == "updateComponents" and (not isinstance(body.get("components"), list) or not body["components"]):
            raise ProfileError("V1", "updateComponents needs a non-empty components array")
    if direction == "to_renderer":
        if created != 1:
            raise ProfileError("V6", "one surface per view")
        ids = {m[next(k for k in m if k != "version")].get("surfaceId") for m in messages}
        if len(ids) != 1:
            raise ProfileError("V6", "one surface per view")


def _check_component(c: dict, ctx: Context, model: dict) -> None:
    cid = c.get("id")
    if not isinstance(cid, str) or not cid:
        raise ProfileError("V1", "a component needs an id")
    typ = c.get("component")
    if not isinstance(typ, str):
        raise ProfileError("V1", f"{cid}: component type missing")
    cat = c.get("@catalogId")
    if cat is not None and cat != CATALOG_ID:
        raise ProfileError("V3", f"{cid}: catalog {cat!r} is not a Friday catalog")
    spec = COMPONENTS.get(typ)
    if spec is None:
        raise ProfileError("V4", f"{cid}: component {typ!r} is not in the Friday allowlist")
    extra = set(c) - _COMMON_KEYS - spec["props"] - {"child"}
    if extra:
        raise ProfileError("V4", f"{cid}: {typ} has no property {sorted(extra)[0]!r}")
    if "child" in c:
        raise ProfileError("V5", f"{cid}: single child references are not used by the Friday catalog")
    if "children" in c and not spec["children"]:
        raise ProfileError("V5", f"{cid}: {typ} cannot have children")
    for call in _calls(c):
        name = call.get("@call")
        if "callFunction" in call or name not in ALLOWED_FUNCTIONS:
            raise ProfileError("V4", f"{cid}: function call {name or 'callFunction'!r} is not allowed")
        if call.get("catalogId", CATALOG_ID) != CATALOG_ID:
            raise ProfileError("V3", f"{cid}: function from a non-Friday catalog")
    for where, s in _strings({k: v for k, v in c.items() if k not in ("id", "component")}):
        if has_url(s):
            raise ProfileError("V8", f"{cid}{where}: a URL in text")
    _check_access(c, ctx)
    _check_typed(c, typ, ctx, model)


def _check_access(c: dict, ctx: Context) -> None:
    acc = c.get("@accessibility")
    if acc is None:
        return
    if not isinstance(acc, dict) or set(acc) - {"label", "description", "live", "hidden"}:
        raise ProfileError("V13", f"{c['id']}: unknown accessibility field")
    if c["component"] == "approval_slot":
        raise ProfileError("V12", "approval_slot has no accessibility label of its own")
    if acc.get("hidden") not in (None, False) and c["component"] == "verb_button":
        raise ProfileError("V13", "a verb_button cannot be hidden")
    if acc.get("live", "off") not in ("off", "polite"):
        raise ProfileError("V13", "live regions are off or polite, never assertive")
    for key in ("label", "description"):
        v = acc.get(key)
        if isinstance(v, str) and (hit := reserved_hit(v, ctx.extra_reserved)):
            raise ProfileError("V11", f"{c['id']}: accessibility {key} uses reserved word {hit!r}")


def _text_value(v, c, key, ctx, model):
    """V11 on a literal, or on a bound value when the model already holds it."""
    if _is_binding(v):
        if v["@path"].startswith("/"):
            got = _resolve(model, v["@path"])
            if isinstance(got, str):
                return got
        return None
    if _is_call(v):
        return None
    if not isinstance(v, str):
        raise ProfileError("V1", f"{c['id']}: {key} must be text or a binding")
    return v


def _check_typed(c: dict, typ: str, ctx: Context, model: dict) -> None:
    def labelled(key):
        text = _text_value(c.get(key), c, key, ctx, model)
        if text is not None and (hit := reserved_hit(text, ctx.extra_reserved)):
            raise ProfileError("V11", f"{c['id']}: {key} uses reserved word {hit!r}")
        return text
    if typ == "text_block":
        style = c.get("style", "body")
        if style not in TEXT_STYLES:
            raise ProfileError("V1", f"{c['id']}: unknown text style")
        text = _text_value(c.get("text"), c, "text", ctx, model)
        if text is not None and style == "heading" and (hit := reserved_hit(text, ctx.extra_reserved)):
            raise ProfileError("V11", f"{c['id']}: heading uses reserved word {hit!r}")
        if text is not None:
            # a body text that reads as a card header is the look-alike attack (spoofed "needs you")
            for phrase in _RESERVED_PHRASES[5:]:
                if re.search(rf"\b{re.escape(phrase)}\b", fold(text)):
                    raise ProfileError("V11", f"{c['id']}: text imitates a card header ({phrase!r})")
    elif typ == "status_block":
        labelled("title")
    elif typ == "nav_link":
        labelled("label")
        if not isinstance(c.get("destination"), str) or has_url(c["destination"]) or "/" in c["destination"]:
            raise ProfileError("V8", f"{c['id']}: destination is an internal registry id")
    elif typ == "chart" and c.get("kind") not in CHART_KINDS:
        raise ProfileError("V1", f"{c['id']}: unknown chart kind")
    elif typ == "verb_button":
        _check_button(c, ctx, labelled)
    elif typ == "approval_slot":
        flt = c.get("filter")
        if (not isinstance(flt, dict) or set(flt) - {"subject", "kind"} or "subject" not in flt
                or not isinstance(flt["subject"], str) or not isinstance(flt.get("kind", ""), str)):
            raise ProfileError("V12", f"{c['id']}: approval_slot takes only filter {{subject, kind?}}")
        if flt["subject"] != ctx.subject:
            raise ProfileError("V12", f"{c['id']}: approval_slot subject is not this view's subject")
    elif typ in ("media_tile", "audio_clip", "file_line", "media_grid"):
        for key in c.keys() & {"media", "file", "items"}:
            if not (_is_binding(c[key]) or _is_call(c[key])):
                raise ProfileError("V8", f"{c['id']}: media and files are by store id through a binding")


def _check_button(c: dict, ctx: Context, labelled) -> None:
    labelled("label")
    verb = c.get("verb")
    if not isinstance(verb, str) or not verb:
        raise ProfileError("V9", f"{c['id']}: verb_button needs a verb")
    manifest = ctx.verbs(verb)
    if not manifest:
        raise ProfileError("V9", f"{c['id']}: verb {verb!r} has no manifest (fails closed)")
    action = c.get("action")
    event = action.get("event") if isinstance(action, dict) else None
    if (not isinstance(event, dict) or set(action) != {"event"} or event.get("name") != verb
            or set(event) - {"name", "context"}):
        raise ProfileError("V9", f"{c['id']}: the action event name must equal the verb")
    allowed_args = set(manifest.get("args") or ())
    context = event.get("context") or {}
    if not isinstance(context, dict) or set(context) - allowed_args:
        raise ProfileError("V9", f"{c['id']}: context keys are not in the verb's argument template")
    if manifest.get("gate_class") in BANNED_GATE_CLASSES:
        raise ProfileError("V10", f"{c['id']}: verb {verb!r} is {manifest.get('gate_class')}")
    bad = set(manifest.get("effects") or ()) & BANNED_EFFECTS
    if bad:
        raise ProfileError("V10", f"{c['id']}: verb {verb!r} has effect {sorted(bad)[0]!r}")
    if "gate_class" not in manifest or "effects" not in manifest:
        raise ProfileError("V10", f"{c['id']}: verb {verb!r} manifest is incomplete (fails closed)")


def _check_tree(comps: list[dict], ctx: Context) -> None:
    by_id: dict[str, dict] = {}
    for c in comps:
        if c["id"] in by_id:
            raise ProfileError("V5", f"duplicate component id {c['id']!r}")
        by_id[c["id"]] = c
    if "root" not in by_id:
        raise ProfileError("V5", "exactly one component has the id 'root'")
    if len(by_id) > MAX_NODES:
        raise ProfileError("V6", f"more than {MAX_NODES} components")
    parents: dict[str, str] = {}
    templated: set[str] = set()
    for c in comps:
        kids = c.get("children")
        if kids is None:
            continue
        if isinstance(kids, dict):                       # a template over a data array
            if set(kids) != {"path", "componentId"}:
                raise ProfileError("V5", f"{c['id']}: malformed template")
            ref = kids["componentId"]
            ids = [ref]
            templated.add(ref)
        elif isinstance(kids, list) and all(isinstance(k, str) for k in kids):
            ids = kids
        else:
            raise ProfileError("V5", f"{c['id']}: children must be ids or a template")
        for k in ids:
            if k not in by_id:
                raise ProfileError("V5", f"{c['id']}: child {k!r} does not exist")
            if k in parents and k not in templated:
                raise ProfileError("V5", f"{k!r} has two parents")
            parents[k] = c["id"]

    def depth(cid: str, seen: tuple) -> int:
        if cid in seen:
            raise ProfileError("V5", "the component tree has a cycle")
        kids = by_id[cid].get("children")
        ids = [kids["componentId"]] if isinstance(kids, dict) else (kids or [])
        return 1 + max((depth(k, seen + (cid,)) for k in ids), default=0)

    if depth("root", ()) > MAX_DEPTH:
        raise ProfileError("V6", f"deeper than {MAX_DEPTH} levels")
    reach = set()
    stack = ["root"]
    while stack:
        cur = stack.pop()
        if cur in reach:
            continue
        reach.add(cur)
        kids = by_id[cur].get("children")
        stack += [kids["componentId"]] if isinstance(kids, dict) else list(kids or [])
    if reach != set(by_id):
        raise ProfileError("V5", "a component is not reachable from root")
    slots = [c for c in comps if c["component"] == "approval_slot"]
    if len(slots) > 2:
        raise ProfileError("V5", "at most two approval_slots per surface")

    def in_template(cid: str) -> bool:
        while cid in parents:
            if cid in templated:
                return True
            cid = parents[cid]
        return cid in templated

    for s in slots:
        if s["id"] not in parents or by_id[parents[s["id"]]]["component"] not in COMPONENTS["approval_slot"]["parents"]:
            raise ProfileError("V5", "approval_slot sits only in a column or status_block")
        if in_template(s["id"]):
            raise ProfileError("V5", "approval_slot never sits inside a template")


def _check_data(comps, data, meta, ctx: Context, messages) -> None:
    declared = ((meta.get("friday") or {}).get("bindings") or {}) if isinstance(meta, dict) else {}
    prefixes = [b.get("path", "") for b in declared.values() if isinstance(b, dict)]
    if ctx.model_authored:
        for m in messages:
            key = next(k for k in m if k != "version")
            if key == "updateDataModel" or (key == "createSurface" and m[key].get("dataModel")):
                raise ProfileError("V7", "a model-authored document carries no data of its own")
    total = 0
    for c in comps:
        for _, s in _strings({k: v for k, v in c.items() if k not in ("id", "component", "@catalogId")}):
            total += len(s.encode("utf-8"))
        for node in _paths(c):
            p = node["@path"]
            if p.startswith("/") and not any(p == pre or p.startswith(pre.rstrip("/") + "/") for pre in prefixes if pre):
                raise ProfileError("V7", f"{c['id']}: {p!r} is not under a declared binding")
    if total > MAX_TEXT_BYTES:
        raise ProfileError("V6", f"more than {MAX_TEXT_BYTES} bytes of literal text")
    for where, s in _strings(data):
        if has_url(s):
            raise ProfileError("V8", f"data{where}: a URL in the data model")
    for rows in _lists(data):
        if len(rows) > MAX_ROWS:
            raise ProfileError("V6", f"more than {MAX_ROWS} template rows")


def _paths(node):
    if isinstance(node, dict):
        if _is_binding(node):
            yield node
        else:
            for v in node.values():
                yield from _paths(v)
    elif isinstance(node, list):
        for v in node:
            yield from _paths(v)


def _lists(node):
    if isinstance(node, list):
        yield node
        for v in node:
            yield from _lists(v)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _lists(v)


def check(messages, ctx: Context, direction: str = "to_renderer") -> dict:
    """Validate a whole document; return {"components", "data", "metadata", "surface"} or raise ProfileError."""
    if ctx.authorship not in ("composer", "friday_liquid"):
        raise ProfileError("V14", "only Friday's composer or the friday-liquid fence may author a view; "
                                  "outside content is shown as one plain line")
    _v1_v2_messages(messages, direction)
    if direction != "to_renderer":
        return {"components": [], "data": {}, "metadata": {}, "surface": None}
    comps, data, meta, sid = _flatten(messages)
    for c in comps:
        if not isinstance(c, dict):
            raise ProfileError("V1", "a component is an object")
    for c in comps:
        _check_component(c, ctx, data)
    _check_tree(comps, ctx)
    _check_data(comps, data, meta, ctx, messages)
    return {"components": comps, "data": data, "metadata": meta, "surface": sid}


def violations(messages, ctx: Context) -> list[str]:
    """Empty when the document is valid Friday L1; otherwise the first rule it breaks."""
    try:
        check(messages, ctx)
    except ProfileError as e:
        return [str(e)]
    return []
