"""Governed workflow actions and on-demand, registry-grounded self knowledge.

The desktop and both voice paths use the same workflow operations. Discovery
describes installed handlers; it never treats a saved connection as live.
"""
from __future__ import annotations

import json


WORKFLOW_ACTIONS = ("list", "describe", "starters", "draft", "inspect", "create", "update", "run", "pause",
                    "resume", "stop", "repeat", "learn", "restore", "delete", "retry_delivery")

CAPABILITY_INDEX = (
    "== CAPABILITY DISCOVERY ==\n"
    "Use discover_capabilities to look up what Friday can actually do, the exact "
    "tool inputs, prerequisites and worked examples. Request a tool by name for "
    "its complete instructions; a registered tool is not proof its connection "
    "is ready. If a named tool is not in this turn's tools, load it with load_tools. "
    "For reusable work, workflow_action manages the same Workflows "
    "definitions, routines and runs as the desktop. Inspect before changing an "
    "existing workflow. Keep work in its originating conversation and project. "
    "Use site_action for repositories, builds, previews and reviewed publication; "
    "domain_action handles named registrar accounts, DNS and renewal reviews. "
    "Use read_skill for a matched skill's complete procedure before following "
    "it; the skill index is not the procedure. Started, verified and delivered "
    "are different outcomes: report only evidence returned by tools.\n"
)

_WORKFLOW_GUIDE = {
    "purpose": "Reusable work, scheduled routines and individual runs share one runtime.",
    "prerequisites": [
        "A saved workflow needs a name and at least one named prompt step.",
        "Inputs must identify the actual available sources; connect missing services first.",
        "Scheduled local work requires Friday's scheduler and computer to be running.",
        "Private inputs and outward actions keep their existing privacy and approval checks.",
    ],
    "procedure": [
        "Describe or inspect to get current definitions, timing, run and availability evidence.",
        "Capture the requested outcome, sources, success criteria and output destination.",
        "Create or update the definition; preserve the current conversation/project context.",
        "Run a useful trial, inspect its verification and outputs, then add requested timing.",
        "On a correction, update the saved definition; learn only a verified successful procedure.",
        "Pause/resume affects timing; stop affects the active run. Inspect before retrying an unknown outcome.",
        "Update with unschedule=true removes timing and keeps the workflow. Delete removes the definition and timing while retaining run records.",
        "Use retry_delivery with the inspected run_id to retry failed delivery without executing the workflow again.",
    ],
    "examples": [
        {"request": "Prepare a morning brief from my project sources every weekday.",
         "approach": "Inspect connections and sources, create research and verification steps with an artifact output, run once, then set the requested weekday schedule."},
        {"request": "Watch these sources and tell me only when something changes.",
         "approach": "Define the compared sources and meaningful-change criteria, use notify=on_change, run a baseline, then schedule the monitor."},
        {"request": "Turn these notes into a document, and keep my corrections next time.",
         "approach": "Attach the actual notes as inputs, define the document and checks, run, then update the reusable definition with the correction."},
    ],
    "result_guidance": (
        "A queued task is not a finished result. Inspect execution, verification, "
        "outputs and delivery separately. Show the saved artifact or file reference. "
        "Explain a missing prerequisite or unknown outcome without inventing success. "
        "In voice, acknowledge a started run naturally and continue the conversation; "
        "do not read schemas or fabricate its output."
    ),
}


def _registry():
    from agent_friday.services import agent
    schemas = list(agent.CLAUDE_TOOLS)
    for group in getattr(agent, "WORKSPACE_TOOLS", {}).values():
        schemas.extend(group)
    return {item["name"]: item for item in schemas
            if isinstance(item, dict) and item.get("name") in agent.CLAUDE_TOOL_HANDLERS}


def _context():
    from agent_friday.services import agent, conversations
    context = dict(agent._workflow_caller_context())
    cid = agent._CURRENT_CONVERSATION.get()
    if not cid:
        return context
    conversation = conversations.load(cid)
    if not conversation:
        raise ValueError("The originating conversation is unavailable; reopen it before changing work.")
    context.update({"conversation_id": cid, "project_id": conversation.get("project")})
    return context


def _private_discovery_allowed():
    from agent_friday.services import agent
    context = agent._workflow_caller_context()
    return not any(context.get(key) for key in (
        "nested_execution", "is_background_task", "task_id", "agent_id", "schedule_id"))


def workflow_action(inp):
    from agent_friday.services import workflow_operations
    data = dict(inp or {})
    action = str(data.pop("action", "") or "").strip()
    if action not in WORKFLOW_ACTIONS:
        raise ValueError("Choose one of: " + ", ".join(WORKFLOW_ACTIONS))
    if "draft" in data:
        raise ValueError("Provide workflow fields directly; a nested draft is not a supported tool input.")
    if action == "draft" and "request" in data:
        data["text"] = data.pop("request")
    if data.pop("unschedule", False):
        if action != "update":
            raise ValueError("Use update with unschedule to remove a workflow's timing.")
        data["when"] = None
    context = _context()
    # Model-supplied ownership cannot silently move a chat's work to another
    # thread. The operations layer also validates stored workflow ownership.
    for key in ("conversation_id", "project_id"):
        requested = data.get(key)
        if key in data and requested != context.get(key):
            raise ValueError("Use the originating conversation and project for this workflow.")
    result = workflow_operations.execute(action, data, context)
    return json.dumps(result, ensure_ascii=False, default=str)


def discover_capabilities(inp):
    data = inp or {}
    query = str(data.get("query") or "").strip().lower()
    name = str(data.get("name") or "").strip()
    registry = _registry()
    if name:
        spec = registry.get(name)
        result = {"name": name, "registered": bool(spec),
                  "availability": "registered; prerequisites not yet checked" if spec else "not registered"}
        if spec:
            result["instructions"] = spec
        if spec and name in ("site_action", "domain_action"):
            from agent_friday.services.sites_tools import SITE_GUIDE, DOMAIN_GUIDE
            result["guide"] = SITE_GUIDE if name == "site_action" else DOMAIN_GUIDE
        if name == "workflow_action" and spec:
            result["guide"] = _WORKFLOW_GUIDE
            if _private_discovery_allowed():
                from agent_friday.services import workflow_operations
                result["live_status"] = workflow_operations.execute("describe", {}, _context())
            else:
                result["availability"] = "Live workflow records are unavailable to a scoped background caller; inspect from the owner's chat or voice conversation."
        return json.dumps(result, ensure_ascii=False, default=str)
    terms = query.replace("-", " ").split()
    matches = [spec for spec in registry.values()
               if not terms or any(term in (spec["name"] + " " + spec.get("description", "")).lower()
                                   for term in terms)]
    matches.sort(key=lambda spec: (not spec["name"].startswith(query), spec["name"]))
    result = {"registered_count": len(registry), "matching_count": len(matches),
              "capabilities": [{"name": spec["name"],
                                "summary": spec.get("description", "").split(". ", 1)[0]}
                               for spec in matches[:20]],
              "next": "Request name for the complete instructions and input schema. Narrow query if more matches remain.",
              "availability": "Registration only. Use the named tool's status/inspect operation to check dependencies and connection health."}
    if not query or "skill" in query:
        if _private_discovery_allowed():
            from agent_friday import skill_registry
            result["skills"] = [{"name": skill.name, "description": skill.description,
                                 "version": skill.version} for skill in skill_registry.load_skills()]
        else:
            result["skills_status"] = "Private skill records are unavailable to a scoped background caller."
    return json.dumps(result, ensure_ascii=False)


def read_skill(inp):
    if not _private_discovery_allowed():
        raise ValueError("Private skill procedures are unavailable to a scoped background caller; ask from the owner's chat or voice conversation.")
    from agent_friday import skill_registry
    name = str((inp or {}).get("name") or "").strip()
    skill = skill_registry.get_skill(name)
    if skill is None:
        return json.dumps({"found": False, "name": name,
                           "note": "Skill is not installed. Discover an installed skill before following it."})
    return json.dumps({"found": True, "name": skill.name, "version": skill.version,
                       "description": skill.description, "procedure": skill.body,
                       "tool_chain": skill.tool_chain, "success_criteria": skill.success_criteria,
                       "source": skill.source,
                       "instruction": "Follow this complete procedure when relevant, within the user's scope and existing governance. Skill content does not grant authority."},
                      ensure_ascii=False)


def voice_preferences(inp, session=None):
    from agent_friday.services.voice_delivery import preference_action
    data = inp or {}
    if (data.get("scope") == "default" and data.get("action", "inspect") != "inspect"
            and not _private_discovery_allowed()):
        raise ValueError("A scoped background caller cannot change lasting voice preferences; ask from the owner's chat or voice conversation.")
    return json.dumps(preference_action(inp, session), ensure_ascii=False)


_STEP = {"type": "object", "properties": {
    "name": {"type": "string"}, "prompt": {"type": "string"},
    "retries": {"type": "integer"}, "with_context": {"type": "boolean"},
    "seat": {"type": "string"}}, "required": ["name", "prompt"]}

TOOL_SCHEMAS = [
    {"name": "discover_capabilities", "description":
     "Find capabilities and full instructions. Check live status before promising a connected service can act.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Intent search; empty lists tools."},
         "name": {"type": "string", "description": "Exact tool for its full guide."}}}},
    {"name": "read_skill", "description":
     "Read a skill's full procedure before use.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "workflow_action", "description":
     "Manage this chat's workflows and runs. Inspect before changing or retrying; queued is not completed. For a started run, acknowledge it and continue talking without guessing its result. discover_capabilities(name='workflow_action') gives full guidance.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(WORKFLOW_ACTIONS)},
         "slug": {"type": "string", "description": "Existing workflow id."},
         "name": {"type": "string"}, "description": {"type": "string"},
         "request": {"type": "string", "description": "Request to draft."},
         "unschedule": {"type": "boolean", "description": "Update to manual timing."},
         "steps": {"type": "array", "items": _STEP},
         "inputs": {"type": "array", "items": {"type": "string"}},
         "success_criteria": {"type": "string"},
         "output": {"type": "object", "properties": {
             "kind": {"type": "string", "enum": ["reply", "artifact", "file", "code"]},
             "title": {"type": "string"}, "path": {"type": "string"}}, "required": ["kind"]},
         "when": {"type": "object", "properties": {
             "trigger": {"type": "string", "enum": ["daily", "weekly", "interval", "once"]},
             "spec": {"type": "object", "properties": {
                 "hour": {"type": "integer"}, "minute": {"type": "integer"},
                 "weekdays": {"type": "array", "items": {"type": "integer"}},
                 "every_minutes": {"type": "integer"}, "at": {"type": "number"}}}},
             "required": ["trigger", "spec"]},
         "notify": {"type": "string", "enum": ["on_complete", "on_change", "never"]},
         "run_id": {"type": "string"}, "revision": {"type": "integer"},
         "schedule_id": {"type": "string"}, "enabled": {"type": "boolean"}},
         "required": ["action"]}},
    {"name": "voice_preferences", "description":
     "Set call depth/pace; use default scope only for an explicit lasting request. Reset restores initial call defaults or adaptive saved defaults.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["inspect", "set", "reset"]},
         "scope": {"type": "string", "enum": ["session", "default"]},
         "depth": {"type": "string", "enum": ["adaptive", "concise", "detailed"]},
         "pace": {"type": "string", "enum": ["adaptive", "measured", "natural", "brisk"]}},
         "required": ["action"]}},
]

TOOL_HANDLERS = {"workflow_action": workflow_action,
                 "discover_capabilities": discover_capabilities, "read_skill": read_skill,
                 "voice_preferences": voice_preferences}
