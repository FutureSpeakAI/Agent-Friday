"""Local Home cards and reversible native workspace customization.

Text and voice share these handlers and their normal governance checkpoint.
Saved changes notify the desktop; saving never claims that a page displayed it.
"""
from __future__ import annotations

import json


def home_cards(inp):
    from agent_friday.services import desktop_cards as cards
    op = inp.get("action", "list")
    try:
        if op == "list":
            return json.dumps({"cards": cards.list_cards()}, ensure_ascii=False)
        if op == "save":
            saved = cards.upsert_card(inp.get("card"))
            return json.dumps({"saved": True, "id": saved["id"], "message": "Card saved to Simple Home."})
        if op == "remove":
            return json.dumps({"removed": cards.remove_card(inp.get("id"))})
        return "home_cards error: action must be list, save or remove."
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


TOOLS = [
    {"name": "home_cards", "description": (
        "Read, write or remove Friday's plain-text cards on Simple Home. Save uses a stable id "
        "and replaces that card; list first to preserve existing content when updating it. "
        "Actions only open native workspaces; cards cannot run code, send messages or approve anything. "
        "Use show_my_day to bring Home forward. Saved does not mean the page confirmed display."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "save", "remove"]},
         "id": {"type": "string"},
         "card": {"type": "object", "properties": {
             "id": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"},
             "priority": {"type": "integer", "minimum": 0, "maximum": 100},
             "actions": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
                 "label": {"type": "string"}, "workspace": {"type": "string"}},
                 "required": ["label", "workspace"], "additionalProperties": False}}},
             "required": ["id", "title"], "additionalProperties": False}},
         "required": ["action"]}},
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
