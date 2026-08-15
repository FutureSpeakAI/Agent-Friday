"""Structural tool-call conformance — a DIAGNOSTIC, not a gate.

Given the production tool registry, does a model emit real `tool_calls`, or
does it roleplay fabricated bracket-syntax pseudo-calls in prose (see
tool_integrity.py)? Ten canned prompts, each shaped to require exactly one
registry tool.

**Nothing here blocks anything.** As of 2026-08-15 the seat gate is removed:
any installed model can be bound to any seat and will actually serve. This
module measures and records so a curious human can look; it is never consulted
to refuse a binding or a turn.

Removed on Stephen's decision, and the evidence supports it. Gating a
user-selected model behind a homegrown eval is not standard practice, and the
failures this instrument fired on were a harness problem rather than a model
one — the same four models scored 1/10, 1/10, 4/10 and 0/10 under a broken
harness and 10/10 apiece once it was fixed. A model that is not emitting tool
calls is a prompting-and-template problem; fix the prompt, do not exile the
model.

The runtime protection that remains is `tool_integrity.find_pseudo_toolcalls`,
which catches a reply naming tools it never called at the moment it happens —
on any model, with no pre-flight roadblock.
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


# A gate call must be able to absorb a cold load plus a full generation. The
# old flat 120s could not: 9 of 10 cases for gemma4:12b timed out on
# 2026-08-15 and the model scored 1/10 having never actually been tested.
GATE_TIMEOUT_S = 600

# The context has to hold the TOOL DEFINITIONS, and they are not small.
# Measured 2026-08-15: 52 tools serialise to 34 138 characters ~= 8 534
# tokens, so the gate prompt is ~8 643 tokens before the model writes a word.
#
# An earlier value of 8192 was chosen from the KV/VRAM curve — the wrong input
# entirely. It sat BELOW the prompt, so the tool definitions were truncated and
# gemma4:e2b scored 8/10 at 8192 having scored 10/10 at the daemon default.
# A context too small for the tools produces exactly the symptom the gate is
# meant to detect (a model "failing" to call tools), which makes it worse than
# no limit at all.
#
# 32768 leaves roughly 4x headroom over the measured prompt. gemma4's KV is
# nearly free (7690 MiB at 4k vs 8001 MiB at 16k), so this costs little VRAM.
GATE_NUM_CTX = 32768


def min_tool_context(pad_tokens: int = 4096) -> int:
    """Smallest power-of-two context that actually holds the tool registry.

    Derived from the live schema rather than guessed, so it tracks the tool
    count instead of going stale the next time tools are added.
    """
    try:
        _names, tools = _tool_names_and_schema()
        approx = len(json.dumps(tools)) // 4 + pad_tokens
    except Exception:
        return GATE_NUM_CTX
    n = 4096
    while n < approx and n < 131072:
        n *= 2
    return n


def _is_harness_error(err: str) -> bool:
    """Did the HARNESS give up, or did the MODEL misbehave?

    Conflating the two is how an untested model earns a red. Timeouts,
    refused connections and unreachable daemons say nothing whatsoever about
    the model's tool-calling behaviour.
    """
    e = (err or "").lower()
    return any(t in e for t in ("timed out", "timeout", "connection",
                                "refused", "unreachable", "reset by peer",
                                "remote end closed", "urlopen error"))


def run_conformance_gate(model: str, *, provider: str = "local",
                          ollama_url: str = "http://localhost:11434",
                          num_ctx: int = GATE_NUM_CTX,
                          timeout: int = GATE_TIMEOUT_S,
                          on_progress=None) -> dict:
    """Run the 10-prompt conformance check against a live Ollama model.

    Returns a result dict; also persists it to GATE_DIR as the cached status
    consumed by is_seat_green(). Does not raise on a failing model — a red
    result is a valid, expected outcome.

    A run in which the harness itself failed is **inconclusive**, not red. On
    2026-08-15 concurrent gating made the models evict each other, every
    reload blew the 120s budget, and `gemma4:12b` was recorded as 1/10 with
    nine timeouts — then that record overwrote `gemma4:e2b`'s existing green.
    An inconclusive run is persisted for diagnosis but never overwrites a
    prior verdict and never counts as a red.
    """
    tool_names, oai_tools = _tool_names_and_schema()
    chat_fn, via = _gate_chat_fn(model, ollama_url, num_ctx=num_ctx,
                                 timeout=timeout)

    def _emit(line):
        if on_progress:
            try:
                on_progress(line)
            except Exception:
                pass

    total = len(CONFORMANCE_PROMPTS)
    _emit(f"structural: {total} prompts against {model} "
          f"(num_ctx={num_ctx}, timeout={timeout}s, via {via})")

    results = []
    for i, case in enumerate(CONFORMANCE_PROMPTS, 1):
        messages = [
            {"role": "system", "content": _GATE_SYSTEM_PROMPT},
            {"role": "user", "content": case["prompt"]},
        ]
        t0 = time.time()
        try:
            # temperature 0.2, unchanged. RECORDED, not fixed: this gate is not
            # reproducible at 0.2 — gemma4:e2b scored 10/10, then 8/10, then
            # 8/10 across three runs on 2026-08-15, and the "failing" cases
            # pass when replayed in isolation. honesty_battery.py:317 already
            # documents the same effect on the other axis ("gemma4:latest at
            # 0.2 swings between 9/10 and 7/10") and chose 0.0 for it.
            #
            # Setting 0.0 here was tried and is NOT an obvious improvement:
            # the `email` case then fails deterministically where it passed at
            # 0.2. Changing a gate's measurement semantics to get a nicer
            # number is exactly the wrong instinct, so the threshold stays put
            # and the variance is raised as a decision question instead.
            resp = chat_fn(messages, model=model, tools=oai_tools,
                           temperature=0.2, max_tokens=300)
            choice = (resp.get("choices") or [{}])[0]
            scored = _score_response(choice.get("message", {}) or {}, tool_names)
        except Exception as e:
            scored = {"real_call": False, "called": [], "prose_leaks": [],
                      "content_excerpt": "", "passed": False, "error": str(e),
                      "harness_error": _is_harness_error(str(e))}
        scored["id"] = case["id"]
        scored["prompt"] = case["prompt"]
        scored["expect_tool"] = case["expect_tool"]
        scored["elapsed_s"] = round(time.time() - t0, 1)
        results.append(scored)
        if scored.get("harness_error"):
            verdict = "HARNESS ERROR"
        elif scored.get("passed"):
            verdict = "pass"
        else:
            verdict = "FAIL"
        _emit("  [%d/%d] %-12s %-13s %5.1fs%s"
              % (i, total, case["id"], verdict, scored["elapsed_s"],
                 ("  called=" + ",".join(scored.get("called") or []))
                 if scored.get("called") else ""))

    passed_count = sum(1 for r in results if r["passed"])
    harness_errors = [r for r in results if r.get("harness_error")]
    all_leaks = [leak for r in results for leak in r["prose_leaks"]]
    inconclusive = bool(harness_errors)
    result = {
        "model": model,
        "provider": provider,
        "via": via,
        "timestamp": time.time(),
        "num_ctx": num_ctx,
        "score": f"{passed_count}/{len(results)}",
        "passed": (None if inconclusive else passed_count == len(results)),
        "inconclusive": inconclusive,
        "harness_errors": len(harness_errors),
        "prose_leaks": all_leaks,
        "results": results,
    }
    if inconclusive:
        _emit("structural: INCONCLUSIVE — %d/%d cases failed in the harness "
              "(not the model); prior verdict left untouched"
              % (len(harness_errors), total))
    else:
        _emit("structural: %s — %s" % (result["score"],
                                       "GREEN" if result["passed"] else "RED"))
    save_status(model, provider, result)
    return result


def _gate_chat_fn(model: str, ollama_url: str, *, num_ctx=GATE_NUM_CTX,
                  timeout=GATE_TIMEOUT_S):
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
            with _rq.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return chat_fn, prov.get("name") or "openai-compatible-local"

    from agent_friday.routing.ollama_manager import get_manager
    mgr = get_manager(ollama_url)

    def ollama_chat(messages, model, tools=None, temperature=0.2,
                    max_tokens=300):
        return mgr.chat_completion(messages, model, tools=tools,
                                   temperature=temperature,
                                   max_tokens=max_tokens,
                                   num_ctx=num_ctx, timeout=timeout)

    return ollama_chat, "ollama"


def save_status(model: str, provider: str, result: dict) -> Path:
    """Persist a gate verdict.

    An INCONCLUSIVE run (the harness timed out or could not reach the daemon)
    is written beside the authoritative record, never over it. On 2026-08-15 a
    run in which nine of ten cases timed out overwrote `gemma4:e2b`'s standing
    green with a red — destroying a real verdict with a measurement that never
    happened. Evidence of a failed measurement is not evidence about a model.
    """
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    base = _safe_name(model, provider)
    if result.get("inconclusive"):
        path = GATE_DIR / f"{base}.inconclusive.json"
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return path
    path = GATE_DIR / f"{base}.json"
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
    """Fail-closed: a model with no recorded conformance run is NOT green.

    `passed is None` means the run was inconclusive — the harness failed, not
    the model. That is 'ungated', which is already fail-closed; it must not be
    reported as a red, because a red is a claim about the model.
    """
    status = get_cached_status(model, provider)
    return bool(status and status.get("passed") is True)


# ── 2026-08-15: the honesty battery is GONE, and neither axis gates a seat. ──
#
# `axis_status` survives ONLY as a display: the picker chip may show whether a
# model has a structural record, and nothing consults it to decide anything.
# There is no `honesty` axis and no `dual_green` — a seat is never refused.

def axis_status(model: str, provider: str = "local") -> dict:
    """Informational only. Never gates a binding or a turn."""
    status = get_cached_status(model, provider)
    if status is None:
        structural = "ungated"
    elif status.get("passed") is None or status.get("inconclusive"):
        structural = "ungated"
    else:
        structural = "green" if status.get("passed") else "red"
    return {"structural": structural, "gates": False}


def get_last_known_green(provider: str = "local") -> str | None:
    """Removed. Always None.

    This existed solely to pick a substitute when the gate refused the user's
    model. With no refusal there is no substitution, and a "last known green"
    is exactly the silent swap that made a bound model not serve. It also aged
    badly on its own — it once returned a model that had been deleted from disk.
    """
    return None


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
    """Pass-through. The requested model ALWAYS holds the seat.

    This used to be the un-bypassable enforcement layer: on a red or ungated
    model it substituted the "last known green" model for the turn, or stripped
    tools entirely. That is precisely why binding a model did not mean being
    served by it. Kept as a function so callers and their tests state plainly
    that nothing is enforced, rather than the concept vanishing silently.
    """
    return {"model": requested_model, "seat_ok": True,
            "requested": requested_model, "reason": "no seat gate",
            "fallback": None}


def save_evidence(model: str, provider: str, result: dict) -> Path:
    """Write a documented red/green run into the repo (tests/conformance/results/)
    so the gate's behavior on known models is reviewable and regression-tested,
    independent of whatever happens to be cached under ~/.friday on this
    machine. This is the "document the run in-repo" requirement for FR-1."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{_safe_name(model, provider)}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path
