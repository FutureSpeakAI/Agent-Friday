"""Slim prompts for routine background jobs.

Routine background jobs (the Front Page editor, the weekly digest, the
afternoon briefing, and the heartbeat's one model call after its pre-check,
services/heartbeat.py) used to borrow the whole chat prompt: persona, self-knowledge, the capability index, the
user model, vault and wiki context and, for some, the full tool catalogue. That is ~16.7k tokens of system
prompt (plus ~22k of tool schemas when tools ride along) sent to do a job
whose real input is the material it was handed. On a local seat with an 8k
window every such call was refused before it started ("exceeds the available
context size"); on a cloud seat it was paid for, every run.

A job here declares the minimum it needs, explicitly:

  * its instructions (a short `Job`),
  * the material it is handed (the user message),
  * the agent's name (the identity helper, `brand.her_name`),
  * today's date,
  * ONLY the tools it actually calls, or none.

There is no persona dump, no tool catalogue, no memory or vault block. The
material is the only personal content in the request, and it is the same
material the old path sent in its user message, so the privacy posture of
each job is unchanged or narrower (the system prompt carries nothing
personal at all).

Interactive chat and voice never come through here: they keep the full prompt
from `model_router._get_friday_system_prompt`, which this module does not
touch.

Setting: `job_prompts.slim` (default True). Off sends a job's old full prompt
(the caller's `full_system`), for an owner who wants the old behaviour back.

Every build logs one line to friday.log (`friday.job_prompt`) with the job's
name and its token estimate, so the cost of a run is visible without a meter.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

_log = logging.getLogger("friday.job_prompt")

#: Same basis as services/compaction.py and scheduled_cloud._tokens.
_CHARS_PER_TOKEN = 4

#: The rules every job prompt carries. Kept short on purpose: this is the
#: whole system prompt for a job, not a preamble to a bigger one.
_RULES = (
    "Work only from the material in the request. Do not invent facts, "
    "people, dates, links or numbers that are not in it. If the material is "
    "thin, say less rather than filling the gap. Reply in exactly the format "
    "the request asks for, with no preamble."
)


@dataclass(frozen=True)
class Job:
    """What a routine job is, in a sentence or two.

    name: shown in friday.log and the token line ("Front Page editor").
    instructions: the job's own standing instructions (its role). The
        per-run request, with its material, goes in the user message.
    """
    name: str
    instructions: str = ""


@dataclass
class JobPrompt:
    """A built job request: what to send, and how big it is."""
    job: str
    system: str
    messages: list
    tools: list = field(default_factory=list)
    slim: bool = True
    system_tokens: int = 0
    material_tokens: int = 0
    tool_tokens: int = 0

    @property
    def estimated_tokens(self) -> int:
        return self.system_tokens + self.material_tokens + self.tool_tokens


def _tokens(obj) -> int:
    if not obj:
        return 0
    try:
        text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    except Exception:
        text = str(obj)
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def enabled() -> bool:
    """`job_prompts.slim`: True unless the owner turned it off."""
    try:
        import agent_friday.core as core
        blk = (core._load_settings() or {}).get("job_prompts") or {}
        if isinstance(blk, dict) and "slim" in blk:
            return bool(blk.get("slim"))
    except Exception:
        pass
    return True


def agent_name() -> str:
    """The agent's name as the owner gave it (the identity helper)."""
    try:
        from agent_friday.brand import her_name
        import agent_friday.core as core
        return her_name((core._load_settings() or {}).get("agent_name"))
    except Exception:
        from agent_friday.brand import her_name
        return her_name("")


def _today() -> str:
    try:
        from agent_friday.services.clock import now_local
        now = now_local()
    except Exception:
        from datetime import datetime
        now = datetime.now()
    return now.strftime("%Y-%m-%d (%A)")


def _tool_schemas(names: Iterable[str]) -> list:
    """The schemas of exactly the named tools, from the real registry.

    An unknown name is dropped and logged, never widened to the registry:
    a job that names no usable tool gets none."""
    wanted = [str(n) for n in (names or ()) if n]
    if not wanted:
        return []
    try:
        from agent_friday.services.agent import CLAUDE_TOOLS
    except Exception as exc:
        _log.warning("job prompt: tool registry unavailable (%s); sending no tools", exc)
        return []
    by_name = {t.get("name"): t for t in CLAUDE_TOOLS if isinstance(t, dict)}
    picked = [by_name[n] for n in wanted if n in by_name]
    missing = [n for n in wanted if n not in by_name]
    if missing:
        _log.warning("job prompt: unknown tool(s) %s dropped", ", ".join(missing))
    return picked


def system_prompt(job: Job, *, tools: Iterable[str] = ()) -> str:
    """The slim system prompt for `job`: name, date, instructions, rules."""
    tools = [str(t) for t in (tools or ()) if t]
    parts = [
        f"You are {agent_name()}, running the background job \"{job.name}\" "
        "for your owner. This is not a conversation; nobody is reading along.",
        f"Today is {_today()}.",
    ]
    if job.instructions:
        parts.append(job.instructions.strip())
    parts.append(_RULES)
    if tools:
        parts.append("You may use only these tools: " + ", ".join(tools) + ".")
    else:
        parts.append("You have no tools in this job.")
    text = "\n\n".join(parts)
    if tools:
        # A job that can call tools carries the action policy, last, exactly
        # like every other prompt that can reach a tool.
        try:
            from agent_friday.services.action_policy import seal_system_prompt
            text = seal_system_prompt(text, f"job prompt: {job.name}")
        except Exception as exc:
            _log.warning("job prompt [%s]: action policy not appended (%s)", job.name, exc)
    return text


def build(job: Job, material: str = "", tools: Iterable[str] = (), *,
          full_system: Optional[Callable[[], str]] = None) -> JobPrompt:
    """Build one job request and log its size.

    material: the request the job is handed (its data and the per-run ask).
        Sent as the user message.
    tools: the NAMES of the tools this job calls, or nothing.
    full_system: the job's old full-prompt builder, used only when the owner
        turned `job_prompts.slim` off. Without one, the slim prompt is used
        regardless.
    """
    if isinstance(job, str):
        job = Job(name=job)
    tools = tuple(str(t) for t in (tools or ()) if t)
    messages = [{"role": "user", "content": str(material or "")}]
    slim = enabled() or full_system is None
    if slim:
        system = system_prompt(job, tools=tools)
    else:
        system = full_system() or ""
    schemas = _tool_schemas(tools)
    jp = JobPrompt(job=job.name, system=system, messages=messages, tools=schemas,
                   slim=slim, system_tokens=_tokens(system),
                   material_tokens=_tokens(material), tool_tokens=_tokens(schemas))
    _log.info("job prompt [%s]: ~%d tokens in (%s system %d, material %d, "
              "tools %d)", job.name, jp.estimated_tokens,
              "slim" if slim else "FULL (job_prompts.slim is off)",
              jp.system_tokens, jp.material_tokens, jp.tool_tokens)
    return jp
