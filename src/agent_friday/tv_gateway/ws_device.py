"""The `device` block of a workspace manifest: what a phone or tablet may draw of that workspace, and where
each verb's button sits (spec PHONE_WORKSPACES_PARITY section 1.3).

`problems(manifest)` is the one check. tests/unit/test_tv_ws_device.py runs it over every manifest the PC
ships, over a hand-broken copy of each rule below, and over the route that serves the block, so the phone
never receives a block this module would refuse.
"""
from __future__ import annotations

import re
from typing import Optional

VIEWS = ("list", "grid", "timeline", "reader", "explorer", "graph3d", "board")
ITEM_VIEWS = ("reader", "explorer")
SENSITIVITIES = ("open", "personal", "private")
OFFLINE = ("never", "opened", "pinnable")
ROLES = ("title", "line", "when", "state", "thumb")
SLOTS = {"row_verbs": "row", "item_verbs": "item", "new_verb": "new"}
KIND_KEYS = {"roles", "item_view", "search", "preview", "stream", "offline", "row_verbs", "item_verbs", "new_verb"}
BLOCK_KEYS = {"expose", "sensitivity", "sections", "kinds", "verb_args"}
SECTION_KEYS = {"view", "kind", "label"}
ARG_TYPES = ("ref", "string", "text", "int", "bool", "enum", "datetime")
ARG_KEYS = {"name", "type", "required", "max", "values", "kind", "from", "default"}
ARG_TEXT_MAX = 4096
ENUM_VALUES_MAX = 20
ACT_GROUPS = ("create", "organize", "settings")        # a placed verb of these changes something, so it declares its arguments
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def device_block(manifest: Optional[dict]) -> Optional[dict]:
    block = (manifest or {}).get("device")
    return block if isinstance(block, dict) and block.get("expose") is True else None


def placement(verb_id: str, block: Optional[dict]) -> list[str]:
    """The places a device draws this verb's button: any of "row", "item", "new". Empty when the manifest
    gives it none (then the phone offers it only as a spoken request)."""
    if block is None:
        return []
    slots = set()
    for kind in (block.get("kinds") or {}).values():
        for key, slot in SLOTS.items():
            placed = kind.get(key)
            if verb_id in (placed if isinstance(placed, list) else [placed]):
                slots.add(slot)
    return sorted(slots)


def args_of(verb_id: str, block: Optional[dict]) -> list[dict]:
    """The arguments a device may send with this verb, as the manifest declares them; empty for none or unplaced."""
    if block is None or not placement(verb_id, block):
        return []
    return [dict(a) for a in (block.get("verb_args") or {}).get(verb_id) or []]


def problems(manifest: dict) -> list[str]:
    """Every reason this manifest's device block cannot be served, in plain words; empty when it can."""
    block = manifest.get("device")
    if block is None:
        return []
    ws = manifest.get("id")
    if not isinstance(block, dict):
        return [f"{ws}: device is not an object"]
    found = []
    found += [f"{ws}: device has an unknown key {k!r}" for k in sorted(set(block) - BLOCK_KEYS)]
    if not isinstance(block.get("expose"), bool):
        found.append(f"{ws}: device.expose must be true or false")
    if block.get("sensitivity") not in SENSITIVITIES:
        found.append(f"{ws}: device.sensitivity must be one of {SENSITIVITIES}")
    verbs = {v.get("id") for v in manifest.get("verbs") or []}
    kinds = block.get("kinds") if isinstance(block.get("kinds"), dict) else {}
    sections = block.get("sections") if isinstance(block.get("sections"), dict) else {}
    if block.get("expose") is True and not (kinds and sections):
        found.append(f"{ws}: an exposed workspace needs sections and kinds")
    for sid, sec in sections.items():
        found += _section_problems(ws, sid, sec, kinds)
    for name, kind in kinds.items():
        found += _kind_problems(ws, name, kind, verbs)
    found += _verb_args_problems(ws, manifest, block, kinds, verbs)
    return found


def _section_problems(ws, sid, sec, kinds) -> list[str]:
    if not isinstance(sec, dict):
        return [f"{ws}: section {sid!r} is not an object"]
    found = [f"{ws}: section {sid!r} has an unknown key {k!r}" for k in sorted(set(sec) - SECTION_KEYS)]
    if sec.get("view") not in VIEWS:
        found.append(f"{ws}: section {sid!r} has a view that is not one of {VIEWS}")
    if sec.get("kind") not in kinds:
        found.append(f"{ws}: section {sid!r} names a kind the workspace does not declare")
    if not isinstance(sec.get("label"), str) or not sec["label"]:
        found.append(f"{ws}: section {sid!r} needs a label")
    return found


def _kind_problems(ws, name, kind, verbs) -> list[str]:
    if not isinstance(kind, dict):
        return [f"{ws}: kind {name!r} is not an object"]
    found = [f"{ws}: kind {name!r} has an unknown key {k!r}" for k in sorted(set(kind) - KIND_KEYS)]
    roles = kind.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        found.append(f"{ws}: kind {name!r} roles must name exactly {ROLES}")
    elif not isinstance(roles.get("title"), str):
        found.append(f"{ws}: kind {name!r} needs a title role")
    if kind.get("item_view") not in ITEM_VIEWS:
        found.append(f"{ws}: kind {name!r} item_view must be one of {ITEM_VIEWS}")
    if kind.get("offline") not in OFFLINE:
        found.append(f"{ws}: kind {name!r} offline must be one of {OFFLINE}")
    for flag in ("search", "preview", "stream"):
        if not isinstance(kind.get(flag), bool):
            found.append(f"{ws}: kind {name!r} {flag} must be true or false")
    for key in SLOTS:
        placed = kind.get(key)
        for verb in placed if isinstance(placed, list) else [placed]:
            if verb is not None and verb not in verbs:
                found.append(f"{ws}: kind {name!r} {key} names {verb!r}, which is not a verb of this workspace")
    if not isinstance(kind.get("row_verbs"), list) or not isinstance(kind.get("item_verbs"), list):
        found.append(f"{ws}: kind {name!r} row_verbs and item_verbs must be lists")
    return found


def _verb_args_problems(ws, manifest, block, kinds, verbs) -> list[str]:
    declared = block.get("verb_args") if isinstance(block.get("verb_args"), dict) else {}
    if "verb_args" in block and not isinstance(block["verb_args"], dict):
        return [f"{ws}: verb_args must be an object"]
    found = []
    groups = {v.get("id"): v.get("group") for v in manifest.get("verbs") or []}
    for verb_id, args in declared.items():
        if verb_id not in verbs:
            found.append(f"{ws}: verb_args names {verb_id!r}, which is not a verb of this workspace")
        elif not placement(verb_id, block):
            found.append(f"{ws}: verb_args names {verb_id!r}, which no row, item or new button uses")
        found += _args_problems(ws, verb_id, args, kinds, block)
    for verb_id in sorted(v for v in verbs if placement(v, block) and groups.get(v) in ACT_GROUPS):
        if verb_id not in declared:
            found.append(f"{ws}: {verb_id!r} is placed and changes something, so it must declare its arguments "
                         "(an empty list means none)")
    return found


def _args_problems(ws, verb_id, args, kinds, block) -> list[str]:
    if not isinstance(args, list):
        return [f"{ws}: {verb_id!r} arguments must be a list"]
    found, seen = [], set()
    for arg in args:
        if not isinstance(arg, dict):
            found.append(f"{ws}: {verb_id!r} has an argument that is not an object")
            continue
        name = arg.get("name")
        label = f"{ws}: {verb_id!r} argument {name!r}"
        found += [f"{label} has an unknown key {k!r}" for k in sorted(set(arg) - ARG_KEYS)]
        if not isinstance(name, str) or not _NAME.match(name):
            found.append(f"{ws}: {verb_id!r} has an argument whose name is not a lower-case identifier")
        elif name in seen:
            found.append(f"{label} is declared twice")
        seen.add(name)
        found += _arg_type_problems(label, arg, kinds, block, verb_id)
    return found


def _arg_type_problems(label, arg, kinds, block, verb_id) -> list[str]:
    kind = arg.get("type")
    if kind not in ARG_TYPES:
        return [f"{label} has a type that is not one of {ARG_TYPES}"]
    found = []
    if "required" in arg and not isinstance(arg["required"], bool):
        found.append(f"{label} required must be true or false")
    limit = arg.get("max")
    if kind in ("string", "text", "int"):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 0 < limit <= (ARG_TEXT_MAX if kind != "int" else 10**9):
            found.append(f"{label} needs a max between 1 and {ARG_TEXT_MAX if kind != 'int' else 10**9}")
    elif "max" in arg:
        found.append(f"{label} may not have a max")
    if kind == "enum":
        values = arg.get("values")
        if not (isinstance(values, list) and 0 < len(values) <= ENUM_VALUES_MAX and all(isinstance(v, str) and v for v in values)):
            found.append(f"{label} needs 1 to {ENUM_VALUES_MAX} non-empty values")
    elif "values" in arg:
        found.append(f"{label} may not have values")
    if kind == "ref":
        if arg.get("kind") not in kinds:
            found.append(f"{label} names a kind the workspace does not declare")
        if arg.get("required", True) is False and arg.get("from") == "item":
            found.append(f"{label} is filled from the item, so it cannot be optional")
    elif "kind" in arg or "from" in arg:
        found.append(f"{label} may not have a kind or from: only a ref does")
    if arg.get("from") not in (None, "item"):
        found.append(f"{label} from must be item")
    if "default" in arg and not _default_ok(arg):
        found.append(f"{label} has a default that does not fit its type")
    if arg.get("from") == "item" and arg.get("kind") in kinds and verb_id not in (kinds[arg["kind"]].get("item_verbs") or []):
        found.append(f"{label} is filled from an item of kind {arg['kind']!r}, but the verb is not on that kind's items")
    return found


def _default_ok(arg) -> bool:
    value, kind = arg["default"], arg.get("type")
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "enum":
        return value in (arg.get("values") or [])
    if kind in ("string", "text"):
        return isinstance(value, str) and len(value) <= (arg.get("max") or 0)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= (arg.get("max") or 0)
    return False
