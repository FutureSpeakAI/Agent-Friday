"""
Agent Friday — Scoped Subagents
Inspired by Goose's isolated subagent spawning with restricted tool sets
(Apache-2.0). All code is original.

A *scope* is a named capability profile applied to a delegated background
task: which tools it may call (allow-list and/or deny-list plus a privilege
ring ceiling), how many tool steps it may take, and a wall-clock budget.

Enforcement happens in two layers:
  1. Prompt contract — the spawned task's prompt is suffixed with the scope's
     limits so the model self-restricts.
  2. Governance gate — services.agent._governance_check passes the task_id of
     background tasks here via scope_check(); a scoped task that requests a
     tool outside its scope is denied before execution and the denial lands in
     the signed decision BOM like any other policy outcome.

Tasks spawned through the ordinary path (no scope registered) are unaffected:
scope_check() returns (True, "") for unknown task ids.
"""
import json
import threading
import time
from pathlib import Path

SCOPES_FILE = Path.home() / ".friday" / "subagent_scopes.json"

# Ring 3 (OS control) is never available to a subagent, whatever the scope says.
_SUBAGENT_RING_CEILING = 2


class SubagentScope:
    def __init__(self, data: dict):
        self.name = data.get("name", "unnamed")
        self.description = data.get("description", "")
        self.allowed_tools = data.get("allowed_tools")        # None = no allow-list
        self.denied_tools = list(data.get("denied_tools", []))
        self.max_ring = min(int(data.get("max_ring", 2)), _SUBAGENT_RING_CEILING)
        self.max_steps = int(data.get("max_steps", 25))
        self.time_budget_s = int(data.get("time_budget_s", 900))

    def allows(self, tool_name: str, ring: int) -> tuple:
        """Return (allowed, reason)."""
        if ring > self.max_ring:
            return False, (f"subagent scope '{self.name}': tool '{tool_name}' is "
                           f"ring-{ring}, scope ceiling is ring-{self.max_ring}")
        if tool_name in self.denied_tools:
            return False, f"subagent scope '{self.name}': tool '{tool_name}' is deny-listed"
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return False, (f"subagent scope '{self.name}': tool '{tool_name}' is "
                           f"not in the scope's allow-list")
        return True, ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "allowed_tools": self.allowed_tools,
            "denied_tools": self.denied_tools,
            "max_ring": self.max_ring,
            "max_steps": self.max_steps,
            "time_budget_s": self.time_budget_s,
        }

    def contract_text(self) -> str:
        """Human-readable scope contract appended to the subagent prompt."""
        lines = [
            "\n\n== SCOPE CONTRACT ==",
            f"You are a scoped subagent operating under the '{self.name}' scope.",
            f"Privilege ceiling: ring-{self.max_ring}. "
            f"Step budget: {self.max_steps} tool calls. "
            f"Time budget: {self.time_budget_s}s.",
        ]
        if self.allowed_tools is not None:
            lines.append("You may ONLY use these tools: " + ", ".join(self.allowed_tools) + ".")
        if self.denied_tools:
            lines.append("You may NOT use: " + ", ".join(self.denied_tools) + ".")
        lines.append("Tool calls outside this scope will be denied by the governance gate.")
        return "\n".join(lines)


BUILTIN_SCOPES = {
    "readonly": {
        "name": "readonly",
        "description": "Local reads only — no mutation, no network",
        "max_ring": 0,
        "max_steps": 15,
        "time_budget_s": 300,
    },
    "researcher": {
        "name": "researcher",
        "description": "Read + search across local and network sources; no writes",
        "allowed_tools": [
            "read_file", "read_wiki", "search_wiki", "query_calendar",
            "query_trust_graph", "get_briefing", "get_career_pipeline",
            "search_web", "search_news", "browse_web", "search_email",
        ],
        "max_ring": 2,
        "max_steps": 25,
        "time_budget_s": 900,
    },
    "writer": {
        "name": "writer",
        "description": "Local reads and writes; no network access",
        "max_ring": 1,
        "max_steps": 20,
        "time_budget_s": 600,
    },
    "recipe-runner": {
        "name": "recipe-runner",
        "description": "Recipe execution — full read/search/write, no shell or task spawn",
        "denied_tools": ["run_command", "install_package", "spawn_task"],
        "max_ring": 2,
        "max_steps": 30,
        "time_budget_s": 1200,
    },
}

# task_id -> {"scope": SubagentScope, "name": str, "spawned": float, "steps": int}
_SCOPED_TASKS: dict = {}
_LOCK = threading.Lock()


def _load_custom_scopes() -> dict:
    try:
        if SCOPES_FILE.exists():
            data = json.loads(SCOPES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def get_scope(name: str) -> SubagentScope:
    """Resolve a scope by name — custom scopes shadow built-ins."""
    custom = _load_custom_scopes()
    if name in custom:
        return SubagentScope(custom[name])
    if name in BUILTIN_SCOPES:
        return SubagentScope(BUILTIN_SCOPES[name])
    raise KeyError(f"Unknown subagent scope: {name}")


def list_scopes() -> list:
    out = []
    custom = _load_custom_scopes()
    for name, data in BUILTIN_SCOPES.items():
        if name not in custom:
            out.append({**SubagentScope(data).to_dict(), "builtin": True})
    for name, data in custom.items():
        out.append({**SubagentScope(data).to_dict(), "builtin": False})
    return out


def save_custom_scope(data: dict) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("scope requires a 'name'")
    scope = SubagentScope(data)  # normalizes + clamps the ring ceiling
    custom = _load_custom_scopes()
    custom[name] = scope.to_dict()
    SCOPES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCOPES_FILE.write_text(json.dumps(custom, indent=2), encoding="utf-8")
    return scope.to_dict()


def spawn_scoped_subagent(name: str, prompt: str, scope: str = "researcher",
                          description: str = "") -> dict:
    """Spawn a background task whose tool access is restricted to `scope`.

    Uses the canonical task plumbing (services.agent._spawn_task) so the task
    shows up in the normal task list/UI; the scope is registered against the
    returned task_id and enforced by the governance gate.
    """
    sc = get_scope(scope)
    from agent_friday.services.agent import _spawn_task
    task_id = _spawn_task(name, prompt + sc.contract_text(), description)
    with _LOCK:
        _SCOPED_TASKS[task_id] = {
            "scope": sc, "name": name, "spawned": time.time(), "steps": 0,
        }
    return {"task_id": task_id, "scope": sc.to_dict()}


def scope_check(task_id: str, tool_name: str, ring: int) -> tuple:
    """Governance hook: is this tool call allowed for this task's scope?

    Unknown task ids (i.e. unscoped tasks) always pass. Each call against a
    scoped task consumes one step of its budget.
    """
    with _LOCK:
        rec = _SCOPED_TASKS.get(task_id)
        if rec is None:
            return True, ""
        rec["steps"] += 1
        steps = rec["steps"]
        sc = rec["scope"]
        spawned = rec["spawned"]
    if steps > sc.max_steps:
        return False, (f"subagent scope '{sc.name}': step budget "
                       f"({sc.max_steps}) exhausted")
    if time.time() - spawned > sc.time_budget_s:
        return False, (f"subagent scope '{sc.name}': time budget "
                       f"({sc.time_budget_s}s) exhausted")
    return sc.allows(tool_name, ring)


def get_task_scope(task_id: str):
    with _LOCK:
        rec = _SCOPED_TASKS.get(task_id)
        return rec["scope"] if rec else None


def list_scoped_tasks() -> list:
    with _LOCK:
        return [
            {"task_id": tid, "name": rec["name"], "scope": rec["scope"].name,
             "spawned": rec["spawned"], "steps_used": rec["steps"]}
            for tid, rec in _SCOPED_TASKS.items()
        ]
