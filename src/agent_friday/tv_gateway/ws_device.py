"""The `device` block of a workspace manifest: what a phone or tablet may draw of that workspace, and where
each verb's button sits (spec PHONE_WORKSPACES_PARITY section 1.3).

`problems(manifest)` is the one check. tests/unit/test_tv_ws_device.py runs it over every manifest the PC
ships, over a hand-broken copy of each rule below, and over the route that serves the block, so the phone
never receives a block this module would refuse.
"""
from __future__ import annotations

from typing import Optional

VIEWS = ("list", "grid", "timeline", "reader", "explorer", "graph3d", "board")
ITEM_VIEWS = ("reader", "explorer")
SENSITIVITIES = ("open", "personal", "private")
OFFLINE = ("never", "opened", "pinnable")
ROLES = ("title", "line", "when", "state", "thumb")
SLOTS = {"row_verbs": "row", "item_verbs": "item", "new_verb": "new"}
KIND_KEYS = {"roles", "item_view", "search", "preview", "stream", "offline", "row_verbs", "item_verbs", "new_verb"}
BLOCK_KEYS = {"expose", "sensitivity", "sections", "kinds"}
SECTION_KEYS = {"view", "kind", "label"}


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
