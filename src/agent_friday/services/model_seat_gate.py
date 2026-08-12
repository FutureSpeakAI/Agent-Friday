"""FR-1 — orchestrator seat conformance gate (docs: toolcall-integrity-v5).

A local model may only hold Friday's tool-using seat (advertise/receive the
CLAUDE_TOOLS registry) if it passes a structural conformance check: given the
production tool registry, does it emit real tool_calls, or does it roleplay
fabricated bracket-syntax pseudo-calls in prose (see tool_integrity.py)?

Ten canned prompts, each shaped to require exactly one registry tool. Pass =
10/10 real structured tool_calls, zero prose leaks anywhere in the run.

This module measures and records; it does not itself block a live chat
turn — see routes/core_routes.py::api_settings for the seat-change gate, and
model_router.py's response-validator hook for the runtime backstop that
protects a seat that was assigned before this gate existed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from agent_friday.core import FRIDAY_DIR
from agent_friday.services.tool_integrity import find_pseudo_toolcalls

GATE_DIR = FRIDAY_DIR / "model_seat_conformance"

# Repo-committed evidence directory (relative to this file: services/ -> agent_friday
# -> src -> repo root -> tests/conformance/results).
_REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = _REPO_ROOT / "tests" / "conformance" / "results"

RECOMMENDED_LOCAL_MODEL = "gemma4:latest"

# Each prompt is shaped to require exactly one real tool call from the
# production registry (agent.py CLAUDE_TOOLS) — the same tools whose names
# leaked as bracket prose in the 2026-08-12 "start my day" confabulation.
CONFORMANCE_PROMPTS = [
    {"id": "calendar", "prompt": "What's on my calendar today?", "expect_tool": "query_calendar"},
    {"id": "email", "prompt": "Do I have any unread priority emails?", "expect_tool": "search_email"},
    {"id": "web_search", "prompt": "Search the web for today's top AI news.", "expect_tool": "search_web"},
    {"id": "open_url", "prompt": "Open reddit.com in my browser.", "expect_tool": "open_url"},
    {"id": "draft_email", "prompt": "Draft an email to my landlord at landlord@rosewoodapts.com — the heater's been out since last night, ask them to send someone today.", "expect_tool": "draft_email"},
    {"id": "news", "prompt": "What's in the news about the stock market right now?", "expect_tool": "search_news"},
    {"id": "navigate", "prompt": "Switch the interface to the finance workspace.", "expect_tool": "navigate"},
    {"id": "read_wiki", "prompt": "Read my wiki page at professional/job-search.md.", "expect_tool": "read_wiki"},
    {"id": "search_wiki", "prompt": "Search my wiki for anything about 'roadmap'.", "expect_tool": "search_wiki"},
    {"id": "open_path", "prompt": "Open my Downloads folder.", "expect_tool": "open_path"},
]

_GATE_SYSTEM_PROMPT = (
    "You are Friday, a personal assistant with access to tools. When the "
    "user's request requires real-time, personal, or filesystem data, call "
    "the matching tool — do not describe, guess, or narrate what a tool "
    "would return. If you are not going to call a tool, answer in plain "
    "prose with no tool names in it."
)


def _safe_name(model: str, provider: str) -> str:
    return f"{provider}__{model}".replace("/", "_").replace(":", "_")


def _tool_names_and_schema():
    # Lazy import: agent.py is large and this module must stay importable
    # from lightweight contexts (settings route, tests) without pulling in
    # the full agent stack at import time.
    from agent_friday.services.agent import CLAUDE_TOOLS
    from agent_friday.routing.model_router import anthropic_to_openai_tools
    names = [t["name"] for t in CLAUDE_TOOLS if t.get("name")]
    return names, anthropic_to_openai_tools(CLAUDE_TOOLS)


def _score_response(oai_message: dict, tool_names) -> dict:
    tool_calls = oai_message.get("tool_calls") or []
    content = oai_message.get("content") or ""
    real_call = bool(tool_calls) and all(
        isinstance(tc, dict) and (tc.get("function") or {}).get("name") in tool_names
        for tc in tool_calls
    )
    leaks = find_pseudo_toolcalls(content, tool_names)
    return {
        "real_call": real_call,
        "called": [(tc.get("function") or {}).get("name") for tc in tool_calls],
        "prose_leaks": leaks,
        "content_excerpt": content[:300],
        "passed": real_call and not leaks,
    }


def run_conformance_gate(model: str, *, provider: str = "local",
                          ollama_url: str = "http://localhost:11434") -> dict:
    """Run the 10-prompt conformance check against a live Ollama model.

    Returns a result dict; also persists it to GATE_DIR as the cached status
    consumed by is_seat_green(). Does not raise on a failing model — a red
    result is a valid, expected outcome.
    """
    from agent_friday.routing.ollama_manager import get_manager

    tool_names, oai_tools = _tool_names_and_schema()
    mgr = get_manager(ollama_url)

    results = []
    for case in CONFORMANCE_PROMPTS:
        messages = [
            {"role": "system", "content": _GATE_SYSTEM_PROMPT},
            {"role": "user", "content": case["prompt"]},
        ]
        try:
            resp = mgr.chat_completion(messages, model=model, tools=oai_tools,
                                        temperature=0.2, max_tokens=300)
            choice = (resp.get("choices") or [{}])[0]
            scored = _score_response(choice.get("message", {}) or {}, tool_names)
        except Exception as e:
            scored = {"real_call": False, "called": [], "prose_leaks": [],
                      "content_excerpt": "", "passed": False, "error": str(e)}
        scored["id"] = case["id"]
        scored["prompt"] = case["prompt"]
        scored["expect_tool"] = case["expect_tool"]
        results.append(scored)

    passed_count = sum(1 for r in results if r["passed"])
    all_leaks = [leak for r in results for leak in r["prose_leaks"]]
    result = {
        "model": model,
        "provider": provider,
        "timestamp": time.time(),
        "score": f"{passed_count}/{len(results)}",
        "passed": passed_count == len(results),
        "prose_leaks": all_leaks,
        "results": results,
    }
    save_status(model, provider, result)
    return result


def save_status(model: str, provider: str, result: dict) -> Path:
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    path = GATE_DIR / f"{_safe_name(model, provider)}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path


def get_cached_status(model: str, provider: str = "local") -> dict | None:
    path = GATE_DIR / f"{_safe_name(model, provider)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_seat_green(model: str, provider: str = "local") -> bool:
    """Fail-closed: a model with no recorded conformance run is NOT green."""
    status = get_cached_status(model, provider)
    return bool(status and status.get("passed"))


def save_evidence(model: str, provider: str, result: dict) -> Path:
    """Write a documented red/green run into the repo (tests/conformance/results/)
    so the gate's behavior on known models is reviewable and regression-tested,
    independent of whatever happens to be cached under ~/.friday on this
    machine. This is the "document the run in-repo" requirement for FR-1."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{_safe_name(model, provider)}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path
