"""`local_model_status`: which local model is serving, its window, its load.

Read-only and ring 0: it reads the residency Arbiter's plan and an in-process
counter (services/local_brain.py) and changes nothing.

Its schema is Settings' own workspace tool rather than part of the always-on
catalogue, so it costs nothing on a turn that does not ask for it
(tests/unit/test_latency_budget.py). The loader hands it over by name from any
workspace, and a local seat's system prompt names it.
"""
from __future__ import annotations

import json

NAME = "local_model_status"

TOOLS = [
    {"name": NAME,
     "description": (
         "Report the local model serving Friday's brain on this PC: its seat, "
         "model, context window in tokens, and whether it is busy and why. "
         "Read-only."),
     "input_schema": {"type": "object", "properties": {}}},
]


def _tool_local_model_status(_inp=None):
    from agent_friday.services import local_brain
    return json.dumps(local_brain.status(), ensure_ascii=False)


HANDLERS = {NAME: _tool_local_model_status}

#: Reads only (ring 0).
RINGS = {NAME: 0}

#: The workspace whose turns carry the schema without asking for it.
WORKSPACE = "settings"


def register(claude_tools, handlers, rings, workspace_tools=None):
    """Handler and ring always; the schema goes to Settings' workspace tools,
    or to `claude_tools` only when no workspace registry is given."""
    target = (workspace_tools.setdefault(WORKSPACE, [])
              if workspace_tools is not None else claude_tools)
    known = {t["name"] for t in target}
    for t in TOOLS:
        if t["name"] not in known:
            target.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
