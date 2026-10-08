"""Local Home cards and reversible native workspace customization.

Text and voice share these handlers and their normal governance checkpoint.
Saved changes notify the desktop; saving never claims that a page displayed it.
"""
from __future__ import annotations

import json


def home_cards(inp):
    from agent_friday.services import desktop_cards as cards
    if not isinstance(inp, dict):
        return "home_cards error: provide an action object."
    op = inp.get("action", "list")
    fields = {"list": set(), "board": set(), "save": {"card"}, "remove": {"id"}, "change": {"change"}}
    if not isinstance(op, str) or op not in fields or set(inp) - ({"action"} | fields[op]):
        return "home_cards error: use only the fields for the selected action."
    try:
        if op == "list":
            return json.dumps({"cards": cards.list_cards()}, ensure_ascii=False)
        if op == "save":
            saved = cards.upsert_card(inp.get("card"))
            return json.dumps({"saved": True, "id": saved["id"], "message": "Card saved to Simple Home."})
        if op == "remove":
            return json.dumps({"removed": cards.remove_card(inp.get("id"))})
        if op == "board":
            return json.dumps(cards.read_board(), ensure_ascii=False)
        if op == "change":
            result = cards.change_board(inp.get("change"))
            return json.dumps({"saved": True, "revision": result["revision"],
                "message": "Home change saved. Source tracking updates from local activity while Home is open; underlying work was not changed."})
        return "home_cards error: action must be list, save, remove, board or change."
    except cards.CardError as exc:
        return "home_cards error: " + str(exc)
    except OSError:
        return "Home cards could not be saved or read. No success was confirmed."


def customize_workspace(inp):
    from agent_friday.services import workspace_registry, workspace_studio, off_record
    if off_record.active():
        return "Off the record is on. Workspace changes were not saved."
    target = workspace_registry.get(inp.get("workspace"))
    if not target or workspace_registry.is_held(target) or (target.get("boundary") or {}).get("kind", "native") != "native":
        return "Choose an available native workspace to customize."
    patch = inp.get("patch")
    if not isinstance(patch, dict) or not (set(patch) & {"note", "accent", "density", "actions"}) or set(patch) - {"note", "accent", "density", "actions", "summary"}:
        return "Provide a note, accent, density or quick actions for this workspace."
    try:
        _, version = workspace_studio.apply_customization(target["id"], patch)
        if not version:
            return "No workspace change was saved."
        return json.dumps({"saved": True, "workspace": target["id"], "revert_to": version["id"],
                           "message": "Workspace customization saved. Use revert_workspace to undo."})
    except (ValueError, PermissionError):
        return "That workspace change was refused or invalid."
    except OSError:
        return "The workspace change could not be saved. No success was confirmed."


_CARD_SCHEMA = {"type": "object", "properties": {
    "id": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"},
    "priority": {"type": "integer", "minimum": 0, "maximum": 100},
    "actions": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
        "label": {"type": "string"}, "workspace": {"type": "string"},
        "view": {"type": "string", "enum": ["projects", "activity"]}},
        "required": ["label"], "additionalProperties": False}}},
    "required": ["id", "title"], "additionalProperties": False}
_CHANGE_SCHEMA = {"type": "object", "properties": {
    "op": {"type": "string", "enum": ["save", "remove", "pin", "snooze", "dismiss", "restore", "reorder", "track", "stop_tracking", "reset_suggestions"]},
    "expected_revision": {"type": "integer", "minimum": 0}, "card": _CARD_SCHEMA,
    "id": {"type": "string"}, "pinned": {"type": "boolean"},
    "until": {"type": "number", "description": "Snooze until this Unix timestamp."},
    "ids": {"type": "array", "maxItems": 256, "items": {"type": "string"},
            "description": "Every visible card id in the desired order; pinned cards stay first."},
    "source": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["task", "calendar", "project", "routine", "schedule", "activity"]},
        "id": {"type": "string"}}, "required": ["kind", "id"], "additionalProperties": False}},
    "required": ["op", "expected_revision"], "additionalProperties": False}


TOOLS = [
    {"name": "home_cards", "description": (
        "Read or change Simple Home. list/save/remove manage saved text cards; board reads current cards, "
        "source ids and revision. change requires that revision and one operation. Track only exact listed "
        "local sources; updates occur while Home is open, never install monitoring. Snooze/dismiss hide; "
        "stop_tracking freezes a snapshot without stopping work; restore changes visibility only. "
        "reset_suggestions clears unpinned automatic history: dismissed suggestions may return. "
        "Actions need label and exactly one workspace or view. Use show_my_day to display Home; saved alone never confirms display."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "save", "remove", "board", "change"]},
         "id": {"type": "string"},
         "card": _CARD_SCHEMA, "change": _CHANGE_SCHEMA},
         "required": ["action"], "additionalProperties": False}},
    {"name": "customize_workspace", "description": (
        "Apply a reversible native workspace Salon customization immediately: density compact or comfortable, "
        "hex accent color, pinned note, or quick-action prompts. Pass null to clear a field. "
        "Changes are saved and the open desktop is notified in Simple and Classic. "
        "This is workspace presentation, not codebase editing; use improve_workspace for installed workspace codebases. "
        "Use revert_workspace or list_workspace_history to undo or inspect changes."),
     "input_schema": {"type": "object", "properties": {
         "workspace": {"type": "string"}, "patch": {"type": "object", "properties": {
             "note": {"type": ["string", "null"]}, "accent": {"type": ["string", "null"]},
             "density": {"type": ["string", "null"], "enum": ["compact", "comfortable", None]},
             "summary": {"type": "string"},
             "actions": {"type": ["array", "null"], "maxItems": 8, "items": {"type": "object", "properties": {
                 "label": {"type": "string"}, "prompt": {"type": "string"}},
                 "required": ["label", "prompt"]}}}, "additionalProperties": False}},
         "required": ["workspace", "patch"]}},
]
HANDLERS = {"home_cards": home_cards, "customize_workspace": customize_workspace}
RINGS = {"home_cards": 1, "customize_workspace": 1}


def register(claude_tools, handlers, rings):
    known = {tool["name"] for tool in claude_tools}
    claude_tools.extend(tool for tool in TOOLS if tool["name"] not in known)
    handlers.update(HANDLERS)
    rings.update(RINGS)
