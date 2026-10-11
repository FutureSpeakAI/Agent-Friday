"""What the local models are working on, by voice and chat: local_work.

A text tool, loaded on demand like the other tools a turn reaches by name
(``WORKSPACE_TOOLS["on_demand"]``), so it adds nothing to the always-on
catalogue or to the voice contract. The voice front does not declare it; voice
reaches it through the router and ask_friday.

``local_work`` reads the local work scheduler (``services/local_scheduler``):
the jobs running and waiting for the local models, each with what it needs and
the reason it waits, said plainly ("the podcast waits for you to be idle").
``action=cancel`` takes a WAITING job out of the line before it reaches a
model, by part of its name ("cancel the podcast job"). A running job is not
stopped here (its own Stop does that), and a person's turn is never touched.
It is the same list the Local AI chip shows (``GET /api/local-queue``).
"""
from __future__ import annotations

#: The schema goes to this workspace's tools, outside the always-on catalogue.
WORKSPACE = "on_demand"


def handle_local_work(inp=None) -> str:
    inp = inp if isinstance(inp, dict) else {}
    from agent_friday.services import local_scheduler as _ls
    action = str(inp.get("action") or "status").strip().lower()
    if action == "cancel":
        return _ls.cancel_by_words(inp.get("job") or "")["message"]
    return _ls.status()["speech"]


TOOLS = [
    {"name": "local_work",
     "description": (
         "What the local models are working on and what waits for them (the podcast, "
         "knowledge-graph learning, a long job), each with the reason it is waiting. "
         "action=status (default) says it; action=cancel with job (part of its name, "
         "like podcast) takes a waiting job out before it starts. A running job is not stopped."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["status", "cancel"]},
         "job": {"type": "string", "description": "Part of the waiting job's name, for cancel."}}}},
]
RINGS = {"local_work": 1}
HANDLERS = {"local_work": handle_local_work}


def register(claude_tools, handlers, rings, workspace_tools=None):
    """Handler and ring always; the schema goes to the on-demand tools, or to
    `claude_tools` only when no workspace registry is given."""
    target = (workspace_tools.setdefault(WORKSPACE, [])
              if workspace_tools is not None else claude_tools)
    known = {t["name"] for t in target}
    for t in TOOLS:
        if t["name"] not in known:
            target.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
