"""FR-1 — orchestrator seat conformance gate (docs: toolcall-integrity-v5).

A local model may only hold Friday's tool-using seat (advertise/receive the
CLAUDE_TOOLS registry) if it passes a structural conformance check: given the
production tool registry, does it emit real tool_calls, or does it roleplay
fabricated bracket-syntax pseudo-calls in prose (see tool_integrity.py)?

Ten canned prompts, each shaped to require exactly one registry tool. Pass =
10/10 real structured tool_calls, zero prose leaks anywhere in the run.

This module measures and records. Two independent enforcement points consume
it — routes/core_routes.py::api_settings (blocks a NEW ungated seat at
settings-save time, UI path only) and resolve_local_seat() below (re-checked
on every tool-using dispatch in model_router.py::_call_ollama, regardless of
how settings.json changed — UI, a direct file edit, or any other writer).
The second one is the un-bypassable layer: settings.json is re-read fresh
every ~2s (core._SETTINGS_CACHE_TTL) with no requirement that a change went
through the API, so the settings-save gate alone can be walked around by
anything with filesystem access. resolve_local_seat() closes that gap.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

_seat_logger = logging.getLogger("friday.model_seat_gate")

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
    tool_names, oai_tools = _tool_names_and_schema()
    chat_fn, via = _gate_chat_fn(model, ollama_url)

    results = []
    for case in CONFORMANCE_PROMPTS:
        messages = [
            {"role": "system", "content": _GATE_SYSTEM_PROMPT},
            {"role": "user", "content": case["prompt"]},
        ]
        try:
            resp = chat_fn(messages, model=model, tools=oai_tools,
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
        "via": via,
        "timestamp": time.time(),
        "score": f"{passed_count}/{len(results)}",
        "passed": passed_count == len(results),
        "prose_leaks": all_leaks,
        "results": results,
    }
    save_status(model, provider, result)
    return result


def _gate_chat_fn(model: str, ollama_url: str):
    """The chat-completion callable the gate should use for `model`, plus a
    'via' label for the stored record.

    2026-08-14 alias wrinkle: the gate historically spoke ONLY Ollama, so a
    model served by an OpenAI-compatible local provider (the llama.cpp
    brain) could never earn green under its own id — enforcement then
    refused every tool-using turn with 'never run'. If an enabled
    local-classification openai-compatible descriptor declares the model,
    the gate talks to THAT endpoint; otherwise the Ollama daemon."""
    prov = _local_openai_descriptor(model)
    if prov is not None:
        import urllib.request as _rq

        base = (prov.get("base_url") or "").rstrip("/")

        def chat_fn(messages, model, tools=None, temperature=0.2,
                    max_tokens=300):
            body = {"model": model, "messages": messages,
                    "temperature": temperature, "max_tokens": max_tokens}
            if tools:
                body["tools"] = tools
            req = _rq.Request(
                f"{base}/chat/completions",
                data=json.dumps(body).encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json"})
            with _rq.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return chat_fn, prov.get("name") or "openai-compatible-local"

    from agent_friday.routing.ollama_manager import get_manager
    return get_manager(ollama_url).chat_completion, "ollama"


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


# ── A4/A5: the second gate axis (honesty battery) + dual-gate seating. ──

def axis_status(model: str, provider: str = "local") -> dict:
    """Per-axis gate state for the picker chips (A5):
    {structural: green|red|ungated, honesty: green|red|ungated,
     dual_green: bool}. Fail-closed on both axes."""
    from agent_friday.services.honesty_battery import get_honesty_status

    def _axis(status):
        if status is None:
            return "ungated"
        return "green" if status.get("passed") else "red"

    structural = _axis(get_cached_status(model, provider))
    honesty = _axis(get_honesty_status(model, provider))
    return {
        "structural": structural,
        "honesty": honesty,
        "dual_green": structural == "green" and honesty == "green",
    }


def is_seat_dual_green(model: str, provider: str = "local") -> bool:
    """A3/A5: a local model may hold the orchestrator seat ONLY when both
    axes are green. Fail-closed like each individual axis."""
    return axis_status(model, provider)["dual_green"]


def get_last_known_green(provider: str = "local") -> str | None:
    """The most recently passed model for `provider` — the fallback seat when
    the currently-configured local_model isn't (or is no longer) green.

    Checks GATE_DIR (~/.friday/model_seat_conformance — this machine's own
    live runs, most authoritative) first, then falls back to EVIDENCE_DIR
    (the repo-committed reference results) so a fresh machine with no local
    gate history yet still has a documented-green fallback (gemma4:latest)
    rather than none at all.
    """
    best_model, best_ts = None, -1.0
    for directory in (GATE_DIR, EVIDENCE_DIR):
        if not directory.exists():
            continue
        for path in directory.glob(f"{provider}__*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("provider") != provider or not data.get("passed"):
                continue
            ts = data.get("timestamp") or 0
            if ts > best_ts:
                best_model, best_ts = data.get("model"), ts
        if best_model:
            return best_model  # GATE_DIR (live) wins outright over EVIDENCE_DIR
    return best_model


def _installed_local_models():
    """Names currently servable by an on-device provider: the Ollama daemon's
    installed tags (5s-TTL cached) PLUS models declared by enabled
    local-classification descriptors (the llama.cpp brain lists its model in
    ~/.friday/providers/*.json — it is 'installed' even though it isn't an
    Ollama tag). Returns None when nothing is verifiable — callers must
    treat None as 'unverifiable', not 'empty'."""
    names = set()
    daemon_ok = False
    try:
        from agent_friday.core import _load_settings
        from agent_friday.routing.ollama_manager import get_manager
        url = (_load_settings().get("model_routing") or {}).get(
            "ollama_url", "http://localhost:11434")
        mgr = get_manager(url)
        if mgr.is_available():
            models = mgr.list_models() or []
            names |= {m.get("name") for m in models
                      if isinstance(m, dict) and m.get("name")}
            daemon_ok = True
    except Exception:
        pass
    try:
        from agent_friday.services.provider_registry import get_provider_registry as get_registry
        for prov in get_registry().get_enabled_providers():
            if (prov.get("classification") == "local"
                    and prov.get("type") != "ollama"):
                names |= {str(m) for m in (prov.get("models") or []) if m}
                daemon_ok = True  # descriptor inventory is verifiable
    except Exception:
        pass
    return names if daemon_ok else None


def _local_openai_descriptor(model: str):
    """The enabled local-classification, OpenAI-compatible descriptor that
    declares `model` (the llama.cpp brain wrinkle: the gate historically
    spoke only Ollama while the brain speaks llama-server). None when the
    model belongs to the Ollama daemon or nothing declares it."""
    try:
        from agent_friday.services.provider_registry import get_provider_registry as get_registry
        for prov in get_registry().get_enabled_providers():
            if (prov.get("classification") == "local"
                    and prov.get("type") == "openai-compatible"
                    and model in (prov.get("models") or [])):
                return prov
    except Exception:
        pass
    return None


def _similar_gate_records(model: str, provider: str = "local"):
    """Gate records whose model id looks like the same model under another
    id (normalized: lowercase, alphanumerics only, prefix relationship —
    'qwen3.6-35b-a3b-iq4nl' ~ 'qwen3.6:35b'). Used ONLY to write a helpful
    refusal message: a green earned on one provider/quantization NEVER
    transfers to another id — tool-calling reliability can change with the
    quant, so the equivalence is surfaced to the human, not acted on."""
    def norm(s):
        return "".join(c for c in str(s).lower() if c.isalnum())

    target = norm(model)
    hits = []
    for directory in (GATE_DIR, EVIDENCE_DIR):
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            other = data.get("model")
            if not other or other == model:
                continue
            n = norm(other)
            shorter, longer = sorted((target, n), key=len)
            if len(shorter) >= 6 and longer.startswith(shorter):
                hits.append({"model": other,
                             "provider": data.get("provider", "local"),
                             "passed": bool(data.get("passed")),
                             "via": data.get("via")})
    return hits


def resolve_local_seat(requested_model: str, *, provider: str = "local") -> dict:
    """Un-bypassable, per-dispatch seat enforcement (FR-1 hardening).

    Called on EVERY tool-using local dispatch, not just at settings-save
    time — so a red/ungated model reaching model_routing.local_model by any
    means (UI, direct settings.json edit, a future writer nobody's thought of
    yet) is still refused a tool-using seat on its very next turn, no
    restart required (settings.json is re-read every request; this function
    is re-evaluated every request too).

    Returns {
      "model": the model actually cleared to hold the seat, or None,
      "seat_ok": bool — True iff requested_model itself was used unmodified,
      "requested": requested_model,
      "reason": human-readable explanation,
      "fallback": None | "last_known_green:<model>" | "tool_free",
    }
    """
    if not requested_model:
        return {"model": requested_model, "seat_ok": True, "requested": requested_model,
                "reason": "no model requested", "fallback": None}
    if is_seat_green(requested_model, provider):
        return {"model": requested_model, "seat_ok": True, "requested": requested_model,
                "reason": "gated green", "fallback": None}

    # Human-readable gate summary — the reason string travels into
    # notifications verbatim, and the previous f-string dumped the ENTIRE
    # status dict (results array included) into it. Score, not blob.
    status = get_cached_status(requested_model, provider)
    if status:
        gate_summary = f"failed at {status.get('score')}"
    else:
        gate_summary = "never run under this id"
        # 2026-08-14 alias wrinkle: the brain is seated as
        # 'qwen3.6-35b-a3b-iq4nl' (llama-cpp-brain) while its old green
        # lives under 'qwen3.6:35b' (Ollama). Name the likely-same-model
        # record so the bare "never run" stops reading as nonsense — but
        # never act on it: a green earned under another provider or
        # quantization does not transfer.
        similar = _similar_gate_records(requested_model, provider)
        if similar:
            s = similar[0]
            gate_summary += (
                f"; a {'green' if s['passed'] else 'red'} record exists for "
                f"'{s['model']}' on {s.get('via') or s['provider']} — likely "
                f"the same model under another provider id. Gates don't "
                f"transfer across providers/quantizations: run the gate for "
                f"this seat")
    base_reason = (f"'{requested_model}' is not a gated-green tool-calling "
                   f"seat (conformance gate: {gate_summary})")

    fallback_model = get_last_known_green(provider)
    if fallback_model and fallback_model != requested_model:
        # 2026-08-14 incident: the last-known-green seat (gemma4:latest) had
        # been DELETED from the daemon — substituting it produced a local
        # 404 every hour all night. The dynamic catalog knows what's
        # installed; consult it before offering a fallback. Daemon
        # unreachable (None) → can't verify → keep legacy behavior.
        installed = _installed_local_models()
        if installed is not None and fallback_model not in installed:
            return {
                "model": None, "seat_ok": False, "requested": requested_model,
                "reason": (base_reason +
                           f", and the last known-green local seat "
                           f"'{fallback_model}' is no longer installed "
                           f"(removed from the Ollama daemon) — no green "
                           f"fallback available"),
                "fallback": "tool_free",
            }
        return {
            "model": fallback_model, "seat_ok": False, "requested": requested_model,
            "reason": base_reason,
            "fallback": f"last_known_green:{fallback_model}",
        }
    return {
        "model": None, "seat_ok": False, "requested": requested_model,
        "reason": (base_reason + " and no known-green local fallback seat "
                   "exists"),
        "fallback": "tool_free",
    }


def save_evidence(model: str, provider: str, result: dict) -> Path:
    """Write a documented red/green run into the repo (tests/conformance/results/)
    so the gate's behavior on known models is reviewable and regression-tested,
    independent of whatever happens to be cached under ~/.friday on this
    machine. This is the "document the run in-repo" requirement for FR-1."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{_safe_name(model, provider)}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path
