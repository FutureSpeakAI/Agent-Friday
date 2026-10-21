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

from agent_friday.paths import friday_home
from agent_friday.user_errors import UserFacingLookupError, UserFacingValueError

SCOPES_FILE = friday_home() / "subagent_scopes.json"

# Ring 3 (OS control) is never available to a subagent, whatever the scope says.
_SUBAGENT_RING_CEILING = 2

#: Tools that start more work of their own (§17.14-3): a scoped subagent may
#: call them only when its scope allows subagents.
SPAWN_TOOLS = frozenset({"spawn_task", "run_workflow", "deep_research", "schedule_job",
                         "ask_crew", "steer_crew", "talk_crew"})


class SubagentScope:
    def __init__(self, data: dict):
        self.name = data.get("name", "unnamed")
        self.description = data.get("description", "")
        self.allowed_tools = data.get("allowed_tools")        # None = no allow-list
        self.denied_tools = list(data.get("denied_tools", []))
        self.max_ring = min(int(data.get("max_ring", 2)), _SUBAGENT_RING_CEILING)
        # A step budget belongs to a SCOPE, not to the machine: each named scope
        # below declares its own, because a scope is a narrow role and the
        # number is part of what the role means. What is gone is the fallback --
        # a scope that names no budget is now unbounded rather than inheriting
        # one from this module, because an inherited figure is exactly the kind
        # of cap nobody set and nobody can find.
        from agent_friday.services.turn_budget import SUBAGENT_STEP_DEFAULT
        _steps = data.get("max_steps", SUBAGENT_STEP_DEFAULT)
        self.max_steps = int(_steps) if _steps else None
        # Same rule as the step budget: a scope may declare a time budget, and a
        # scope that declares none has none. 900 was a fallback nobody chose.
        _secs = data.get("time_budget_s")
        self.time_budget_s = int(_secs) if _secs else None
        # §17.14-3: a subagent may start subagents (or schedules, workflows,
        # crew asks, research runs) only when its scope says so.
        self.allow_subagents = bool(data.get("allow_subagents", False))

    def allows(self, tool_name: str, ring: int) -> tuple:
        """Return (allowed, reason)."""
        if tool_name in SPAWN_TOOLS and not self.allow_subagents:
            return False, (f"subagent scope '{self.name}': tool '{tool_name}' starts more work, "
                           "and this scope does not allow subagents")
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
            "allow_subagents": self.allow_subagents,
        }

    def broader_than(self, parent: "SubagentScope") -> list:
        """The ways this scope would give a child more than `parent` has."""
        why = []
        if self.max_ring > parent.max_ring:
            why.append(f"ring {self.max_ring} above the parent's {parent.max_ring}")
        if parent.allowed_tools is not None and (
                self.allowed_tools is None or not set(self.allowed_tools) <= set(parent.allowed_tools)):
            why.append("tools outside the parent's allow-list")
        if set(parent.denied_tools) - set(self.denied_tools) and (
                self.allowed_tools is None or set(self.allowed_tools) & set(parent.denied_tools)):
            why.append("tools the parent is denied")
        if parent.max_steps and (not self.max_steps or self.max_steps > parent.max_steps):
            why.append("a larger step budget")
        if parent.time_budget_s and (not self.time_budget_s or self.time_budget_s > parent.time_budget_s):
            why.append("a longer time budget")
        if self.allow_subagents and not parent.allow_subagents:
            why.append("leave to start subagents")
        return why

    def contract_text(self) -> str:
        """Human-readable scope contract appended to the subagent prompt."""
        lines = [
            "\n\n== SCOPE CONTRACT ==",
            f"You are a scoped subagent operating under the '{self.name}' scope.",
            (f"Privilege ceiling: ring-{self.max_ring}. "
             + (f"Step budget: {self.max_steps} tool calls. "
                if self.max_steps else "Step budget: unlimited. ")
             + (f"Time budget: {self.time_budget_s}s."
                if self.time_budget_s else "Time budget: unlimited.")),
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
        "denied_tools": ["run_command", "run_sandboxed", "install_package", "spawn_task"],
        "max_ring": 2,
        "max_steps": 30,
        "time_budget_s": 1200,
    },
    "goal-milestone": {
        "name": "goal-milestone",
        "description": (
            "Default dispatch scope for an autonomous goal-milestone task "
            "(services.goals) that was NOT explicitly classified hard/gated "
            "and human-approved through the Q3 approval queue: local reads/"
            "writes and read-only research only, via an ALLOW-list (an "
            "outward/spend/irreversible/shell/task-spawn tool added later is "
            "excluded by default, not by having to be remembered on a deny-"
            "list). See services.goals._default_executor / "
            "AUTONOMY_SPEC.md A3 finding on goal-milestone approval bypass."
        ),
        "allowed_tools": [
            "read_file", "read_wiki", "search_wiki", "query_trust_graph",
            "query_calendar", "get_career_pipeline", "get_briefing",
            "knowledge_query", "knowledge_related", "knowledge_communities",
            "write_file", "propose_wiki_update", "correct_wiki",
            "search_web", "search_news", "browse_web", "search_email",
        ],
        "max_ring": 2,
        "max_steps": 40,
        "time_budget_s": 3600,
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
    raise UserFacingLookupError(f"Unknown subagent scope: {name}")


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
        raise UserFacingValueError("scope requires a 'name'")
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
    # The scope is registered by _spawn_task BEFORE the worker starts: no
    # window in which the subagent's first tool call runs unscoped.
    task_id = _spawn_task(name, prompt + sc.contract_text(), description, scope=scope)
    with _LOCK:
        rec = _SCOPED_TASKS.get(task_id)
        if task_id and rec is None:
            # A stand-in spawner that did not register it (never a window for
            # the real one, which registers before its worker starts).
            _SCOPED_TASKS[task_id] = {"scope": sc, "name": name, "spawned": time.time(), "steps": 0}
        elif rec is not None:
            rec["name"] = name
    return {"task_id": task_id, "scope": sc.to_dict()}


def nested_spawn_check(parent_task_id: str, child_scope: str | None) -> tuple:
    """(ok, reason, scope to inherit) for a task spawned by `parent_task_id`.
    An unscoped parent is Friday's own work and may spawn freely (its
    children are still checked by the governance gate). A scoped parent must
    allow subagents, and the child may be no broader than it; a child given
    no scope runs under the parent's."""
    parent = get_task_scope(parent_task_id)
    if parent is None:
        return True, "", None
    if not child_scope or child_scope == parent.name:
        # The same work going on (a chain step, a follow-up, a retry) under
        # the same scope. A scoped task's own model cannot get here by
        # spawning: the spawn tools are refused by its scope (allows()).
        return True, "", parent.name
    if not parent.allow_subagents:
        return False, f"the '{parent.name}' scope this work runs under does not allow subagents", None
    try:
        child = get_scope(child_scope)
    except Exception:
        return False, f"unknown scope {child_scope!r}", None
    why = child.broader_than(parent)
    if why:
        return False, f"the '{child.name}' scope would give more than '{parent.name}': " + "; ".join(why), None
    return True, "", None


def register_scope_for_task(task_id: str, scope: str) -> SubagentScope:
    """Apply a named scope to a task_id, atomically, BEFORE that task's
    worker thread can execute any tool call.

    Used by callers that need their own polling loop around
    services.agent._spawn_task (rather than spawn_scoped_subagent's fire-
    and-forget wrapper) — e.g. services.goals.py's milestone executor. Raises
    KeyError for an unknown scope name (never silently no-ops: a caller that
    asked for a safety scope must know if it wasn't applied).

    Callers that want the registration to happen BEFORE the task's thread
    starts (closing the TOCTOU window a post-hoc registration would leave
    open) should instead pass scope= directly to services.agent._spawn_task,
    which calls this function itself ahead of starting the worker thread.
    """
    sc = get_scope(scope)  # raises KeyError for an unknown scope — fail loud
    with _LOCK:
        _SCOPED_TASKS[task_id] = {
            "scope": sc, "name": scope, "spawned": time.time(), "steps": 0,
        }
    return sc


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
    if sc.max_steps and steps > sc.max_steps:
        return False, (f"subagent scope '{sc.name}': step budget "
                       f"({sc.max_steps}) exhausted")
    if sc.time_budget_s and time.time() - spawned > sc.time_budget_s:
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
