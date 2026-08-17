"""
local_call — a minimal, tool-free call to a local seat.

WHY NOT `model_router._call_ollama`. That helper is the chat path: it resolves
seats, handles descriptors, and runs a full agentic tool loop. Measured
2026-08-17: asking it to classify three spans with a purpose-built prompt and
no `tools` argument still ran the loop and came back after 48.9s with

    [Agent hit max tool iterations without completing.]

A single span happened to escape it, which is why the judgment layer's earlier
single-span tests passed and the first batched call failed — a whole code path
that had never been exercised.

That machinery is wrong for this job on three counts:
  * it is the 20,000-token ceremony the design explicitly refuses to pay per
    step (RS8: no research or gate stage carries the 52-tool registry);
  * it can consume the call, turning a classification into a loop-exhaustion
    message that no JSON parser will ever accept;
  * it puts the entire tool-calling apparatus inside the privacy-critical path,
    where the only thing wanted is "text in, JSON out".

So this module talks to the Ollama daemon directly: one message, one system
prompt, `format: json` when structured output is wanted, no tools, no loop.
"""
from __future__ import annotations

import json
import logging

_log = logging.getLogger("friday.local_call")

# MEASURED 2026-08-17, and the old 120s was wrong in both directions of harm:
# extraction on a large page ran 121s and the 12b's conversation step 192.6s,
# so real work was being killed by the clock — and the timeout surfaced as
# "returned nothing usable", which the research pipeline then delivered as a
# finding-of-absence report. A model that timed out is not a web that had no
# answer. 300s is above every step measured, with headroom.
DEFAULT_TIMEOUT_S = 300


def ollama_url() -> str:
    try:
        from agent_friday.core import _load_settings
        rc = (_load_settings() or {}).get("model_routing") or {}
        return (rc.get("ollama_url") or "http://localhost:11434").rstrip("/")
    except Exception:
        return "http://localhost:11434"


def seat_endpoint(model: str) -> str | None:
    """A seat the Arbiter runs as an owned process is NOT in the daemon.

    Once a model's GGUF is extracted and served by llama-server on its own
    port, asking :11434 for it finds nothing. The Arbiter is the only authority
    that knows a live seat's port, so it is asked first.
    """
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        if arb is None:
            return None
        procs = getattr(arb, "procs", None) or {}
        for _seat, info in procs.items():
            if isinstance(info, dict) and info.get("model_id") == model and info.get("port"):
                return f"http://127.0.0.1:{info['port']}/v1"
    except Exception:
        pass
    return None


def call(system: str, user: str, model: str, *, json_mode: bool = False,
         max_tokens: int = 2048, timeout: int = DEFAULT_TIMEOUT_S,
         num_ctx: int | None = None) -> str:
    """One local completion. Returns text ("" on failure — never raises).

    json_mode uses Ollama's native constrained decoding, which is a far
    stronger guarantee than asking a small model nicely for JSON.
    """
    import requests

    base = seat_endpoint(model)
    if base:
        return _openai_style(base, system, user, model, json_mode, max_tokens,
                             timeout)

    payload: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "think": False,          # gemma/qwen reasoning preamble is not wanted
        "options": {"num_predict": max_tokens, "temperature": 0.1},
    }
    if num_ctx:
        payload["options"]["num_ctx"] = num_ctx
    if json_mode:
        payload["format"] = "json"
    try:
        r = requests.post(f"{ollama_url()}/api/chat", json=payload, timeout=timeout)
        if r.status_code >= 400:
            _log.warning("local_call HTTP %s from %s: %s", r.status_code, model,
                         r.text[:200])
            return ""
        return ((r.json().get("message") or {}).get("content") or "").strip()
    except Exception as e:
        _log.warning("local_call to %s failed: %s", model, e)
        return ""


def _openai_style(base: str, system: str, user: str, model: str,
                  json_mode: bool, max_tokens: int, timeout: int) -> str:
    """An Arbiter-owned seat speaks the OpenAI-compatible dialect.

    Note num_ctx is deliberately NOT sent here: the /v1 surface ignores
    `options`, and a context set at load time is the only one that counts.
    """
    import requests
    payload: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": 0.1, "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        r = requests.post(f"{base}/chat/completions", json=payload, timeout=timeout)
        if r.status_code >= 400:
            return ""
        ch = (r.json().get("choices") or [{}])[0]
        return ((ch.get("message") or {}).get("content") or "").strip()
    except Exception as e:
        _log.warning("local_call (openai-style) to %s failed: %s", model, e)
        return ""


def call_json(system: str, user: str, model: str, *, max_tokens: int = 2048,
              retries: int = 1, timeout: int = DEFAULT_TIMEOUT_S) -> dict | None:
    """Structured call. None means the model would not produce usable JSON.

    None is returned rather than a guessed shape — a stage that substitutes an
    empty result for a failed one is the green-job-doing-nothing failure.
    """
    for _ in range(retries + 1):
        raw = call(system, user, model, json_mode=True, max_tokens=max_tokens,
                   timeout=timeout)
        parsed = extract_json(raw)
        if parsed is not None:
            return parsed
    return None


def extract_json(raw: str) -> dict | None:
    if not raw or not isinstance(raw, str):
        return None
    t = raw.strip()
    if t.startswith("```"):
        import re
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t).strip()
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        d = json.loads(t[s:e + 1])
        return d if isinstance(d, dict) else None
    except Exception:
        return None
