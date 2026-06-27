"""
Agent Friday — Context Injection Middleware
FutureSpeak.AI · Asimov's Mind

Every AI call should automatically carry the context Friday already has, so the
user never has to say "remember, we're working on X". This middleware assembles
a compact AUTO-CONTEXT block from three sources and folds it into the system
prompt at the provider-call level (model_router._get_friday_system_prompt) —
individual workspaces / routes never wire it up themselves.

Sources:
  1. Active creative project — the Series Bible (cast, style guide, continuity)
     from services/creative_memory, so a named character renders consistently
     and Friday knows the project without being reminded.
  2. User preferences — communication style, response length, agent name, news
     priorities, plus an optional user-intelligence profile
     (~/.friday/user_profile.json) for durable facts about the user.
  3. Workspace state — the active workspace, its creative role, and the
     temperature profile applied to it.

Cheap, defensive, and capped — a failure in any source is swallowed so context
injection can never break a generation. The whole block is bounded by
``max_chars`` so it never crowds out the real prompt.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agent_friday.core import FRIDAY_DIR, _load_settings

_USER_PROFILE_FILE = FRIDAY_DIR / "user_profile.json"

# Workspace → its creative role, mirrored from the temperature taxonomy. Used
# only to annotate the workspace-state line ("a research workspace — be precise").
_WORKSPACE_ROLE = {
    "studio":   "a creative studio — favor bold, vivid, original output",
    "creative": "a creative studio — favor bold, vivid, original output",
    "research": "a research workspace — be precise, sourced, and conservative",
    "news":     "a research/news workspace — be precise and sourced",
    "code":     "a code/dev workspace — be exact and deterministic",
    "dev":      "a code/dev workspace — be exact and deterministic",
    "content":  "a content-writing workspace — balance polish and creativity",
    "chat":     "conversation — natural and adaptive",
}


def _user_profile() -> Dict[str, Any]:
    """Durable user-intelligence profile, if the user/Friday has built one.
    Optional file; absence is normal."""
    try:
        if _USER_PROFILE_FILE.exists():
            data = json.loads(_USER_PROFILE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _preferences_block(settings: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    name = settings.get("agent_name")
    style = settings.get("communication_style")
    length = settings.get("response_length")
    prefs = []
    if style:
        prefs.append(f"communication style: {style}")
    if length:
        prefs.append(f"response length: {length}")
    if prefs:
        lines.append("User preferences — " + "; ".join(prefs) + ".")

    profile = _user_profile()
    if profile:
        facts = profile.get("facts") or profile.get("about") or []
        if isinstance(facts, dict):
            facts = [f"{k}: {v}" for k, v in facts.items()]
        if isinstance(facts, list) and facts:
            lines.append("About the user: " + "; ".join(str(f) for f in facts[:10]) + ".")
        goals = profile.get("goals") or []
        if isinstance(goals, list) and goals:
            lines.append("Current goals: " + "; ".join(str(g) for g in goals[:6]) + ".")
    return lines


def _workspace_block(workspace: str, settings: Dict[str, Any]) -> List[str]:
    ws = (workspace or "").strip().lower()
    if not ws:
        return []
    role = _WORKSPACE_ROLE.get(ws)
    line = f"Active workspace: {ws}"
    if role:
        line += f" ({role})"
    # Annotate with the temperature profile if one is configured for this ws.
    try:
        temps = settings.get("workspace_temperatures") or {}
        if ws in temps and temps[ws] is not None:
            line += f" [creativity ≈ {temps[ws]}]"
    except Exception:
        pass
    return [line + "."]


def _project_block(max_chars: int) -> List[str]:
    try:
        from agent_friday.services import creative_memory
        pid = creative_memory.get_active_project_id()
        if not pid:
            return []
        block = creative_memory.project_prompt_context(pid, max_chars=max_chars)
        return [block] if block else []
    except Exception:
        return []


def build_injected_context(workspace: str = "", message: str = "",
                           *, max_chars: int = 2400) -> str:
    """Assemble the AUTO-CONTEXT block for a system prompt.

    Returns "" when there's nothing to inject. The block is clearly delimited so
    it reads as ambient context, not user instruction.
    """
    settings = {}
    try:
        settings = _load_settings() or {}
    except Exception:
        settings = {}

    parts: List[str] = []
    # Project context gets the largest share of the budget (it's the highest-value
    # "remember what we're working on" signal).
    parts += _project_block(max_chars=int(max_chars * 0.6))
    parts += _preferences_block(settings)
    parts += _workspace_block(workspace, settings)

    body = "\n".join(p for p in parts if p).strip()
    if not body:
        return ""
    if len(body) > max_chars:
        body = body[:max_chars].rsplit("\n", 1)[0] + "\n…(truncated)"
    return "== AUTO-CONTEXT (what Friday already knows; no need to be reminded) ==\n" + body


# ── User-intelligence profile helpers (optional; used by routes/tools) ─────────

def get_user_profile() -> Dict[str, Any]:
    return _user_profile()


def update_user_profile(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge updates into the durable user profile and persist it."""
    profile = _user_profile()
    profile.update(updates or {})
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        _USER_PROFILE_FILE.write_text(
            json.dumps(profile, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8")
    except Exception:
        pass
    return profile
