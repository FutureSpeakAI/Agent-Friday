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
import json as _json
import logging
import time as _time

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


# Verified endpoints.json lookups, briefly. Dispatch asks per call and the
# probe is a loopback round trip; without this a grinding loop pays it every
# time. Short enough that a seat dying is noticed within seconds.
_EP_CACHE: dict = {}
_EP_TTL_S = 5.0


def _serves(url: str, model: str) -> bool:
    """Does the server at `url` actually hold `model` right now?

    endpoints.json is a record of intent written by whoever spawned the seat,
    and it goes stale: measured 2026-08-18, the file claimed `gemma4:e4b` was
    on :8090 while :8090 was serving `gemma4:12b`. Trusting it unverified
    would answer as a model the caller did not ask for -- the silent
    substitution this whole layer exists to prevent -- so the file is a HINT
    and the server itself is the authority.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/models", timeout=2) as r:
            rows = (_json.loads(r.read().decode()) or {})
        rows = rows.get("models") or rows.get("data") or []
        return any((m.get("id") or m.get("name")) == model for m in rows)
    except Exception:
        return False


def _endpoints_file() -> dict:
    """`model_id -> url` as last recorded by whoever spawned the seats."""
    try:
        from agent_friday.services.residency_arbiter import endpoints_path
        raw = endpoints_path().read_text(encoding="utf-8")
        return (_json.loads(raw) or {}).get("endpoints") or {}
    except Exception:
        return {}


def seat_endpoint(model: str) -> str | None:
    """A seat the Arbiter runs as an owned process is NOT in the daemon.

    Once a model's GGUF is extracted and served by llama-server on its own
    port, asking :11434 for it finds nothing.

    TWO sources, in order of authority:

      1. This process's own Arbiter. If we spawned it, we know.
      2. `runtime/residency/endpoints.json`, VERIFIED against the server.

    (2) is the fix for the drift that put Ollama back in the local path. The
    Arbiter's `procs` dict lives in memory, so after any restart the new
    process holds nothing, every lookup returned None, and dispatch fell
    through to the Ollama daemon -- which only has what was `ollama pull`ed.
    That is how `gemma4:12b` was logged as "not found" while it was serving on
    127.0.0.1:8090. `liveness_audit` already stated the contract as "dispatch
    resolves seats through endpoints.json"; it simply was not doing it.

    Verified, not trusted: see `_serves`.
    """
    if not model:
        return None
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        procs = getattr(arb, "procs", None) or {} if arb is not None else {}
        # The Arbiter stores `{model_id: (proc, port)}`. This used to test
        # `isinstance(info, dict)` and read info["port"], which matches that
        # shape never -- so even the process that SPAWNED a seat could not
        # find it, and fell through to the Ollama daemon like everyone else.
        # Both shapes are accepted now so a future refactor cannot re-break it
        # silently.
        for key, info in procs.items():
            port = None
            if isinstance(info, (tuple, list)) and len(info) >= 2:
                port = info[1]
            elif isinstance(info, dict):
                if info.get("model_id") not in (None, model):
                    continue
                port = info.get("port")
            elif isinstance(info, int):
                port = info
            if port and (key == model or (isinstance(info, dict)
                                          and info.get("model_id") == model)):
                return f"http://127.0.0.1:{port}/v1"
    except Exception:
        pass

    now = _time.time()
    hit = _EP_CACHE.get(model)
    if hit and (now - hit[0]) < _EP_TTL_S:
        return hit[1]

    url = _endpoints_file().get(model)
    good = url if (url and _serves(url, model)) else None
    if url and not good:
        _log.info("endpoints.json points %s at %s, which is not serving it",
                  model, url)
    _EP_CACHE[model] = (now, good)
    return good


def _daemon_tags() -> set:
    """Model names the Ollama daemon will actually serve, or an empty set."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{ollama_url()}/api/tags", timeout=4) as r:
            rows = (_json.loads(r.read().decode()) or {}).get("models") or []
        return {m.get("name") for m in rows if m.get("name")}
    except Exception:
        return set()


def describe_dispatch(model: str, daemon_tags: set | None = None) -> dict:
    """Where a call to `model` would ACTUALLY land, right now.

    `call()` picks between two destinations and says nothing about which, so a
    seat that quietly died reads exactly like a seat that is working: the same
    function, the same model name, an answer either way. Measured on this
    machine 2026-08-24 — the pinned `gemma4:12b` seat on :8090 died with the
    11:49 restart and was never respawned, `endpoints.json` went on naming
    :8090 for the rest of the day, and every local role silently resolved to an
    Ollama tag instead. Nothing in the log said so, because nothing was asked to.

    Returns the same decision `call()` makes, as data:
      route     — "seat" | "daemon" | "unreachable"
      endpoint  — the base URL those requests go to
      answering — what that endpoint says it is holding (the seat's own
                  /v1/models id, not the name we asked for)
    """
    tags = _daemon_tags() if daemon_tags is None else daemon_tags
    base = seat_endpoint(model)
    if base:
        return {"model": model, "route": "seat", "endpoint": base,
                "answering": _model_on(base) or model}
    if model in tags:
        return {"model": model, "route": "daemon", "endpoint": ollama_url(),
                "answering": model}
    return {"model": model, "route": "unreachable",
            "endpoint": ollama_url(), "answering": None}


def _model_on(base: str) -> str | None:
    """The id a server reports for itself. The server, not our record of it."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/models", timeout=2) as r:
            rows = (_json.loads(r.read().decode()) or {})
        for m in (rows.get("models") or rows.get("data") or []):
            mid = m.get("id") or m.get("name")
            if mid:
                return str(mid)
    except Exception:
        pass
    return None


def log_dispatch_table(models, expect_up=None) -> list:
    """One line per model naming the endpoint and the model answering on it.

    Called at boot so an unreachable seat is a thing somebody can see in the
    log rather than a thing inferred hours later from a latency complaint.
    Logs — not prints: the tray sends the server's stdout to DEVNULL, which is
    why the Arbiter's own boot messages have never been readable.

    `expect_up` names the models the plan says should be reachable RIGHT NOW —
    the pinned and resident seats. Only those warn. A leased seat is supposed
    to be absent until something takes a lease, and a check that cries about
    the normal case is one people learn to scroll past, which would leave this
    no better than the silence it replaces.

    Pass model names only. Non-LLM seats (`faster-whisper`, `kokoro`, the
    ComfyUI image seat) are not reachable at an OpenAI-style endpoint by
    design, so reporting on them here says nothing true.
    """
    tags, rows = _daemon_tags(), []
    expect_up = set(expect_up or ())
    for model in dict.fromkeys(m for m in models if m):
        row = describe_dispatch(model, daemon_tags=tags)
        rows.append(row)
        if row["route"] == "seat":
            answering = row["answering"]
            # A seat answering as something else is worse than no seat: the
            # caller gets a fluent reply from a model it did not choose.
            drift = ("" if answering == model
                     else f" (server calls itself {answering!r})")
            _log.info("local dispatch: %s -> llama.cpp seat %s%s",
                      model, row["endpoint"], drift)
        elif row["route"] == "daemon":
            _log.info("local dispatch: %s -> Ollama daemon %s",
                      model, row["endpoint"])
        elif model in expect_up:
            # The failure this exists to surface: the plan says this seat is up,
            # and nothing is serving it. Every call 404s and escalates to the
            # cloud (see commit 8a30831).
            _log.warning("local dispatch: %s is UNREACHABLE — the plan has it "
                         "up, but there is no llama.cpp seat and no Ollama "
                         "tag; calls will 404 and escalate to the cloud", model)
        else:
            _log.info("local dispatch: %s is not loaded (on demand)", model)
    return rows


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
            # The body is where llama-server puts the actual reason (e.g. the
            # exact token count of an over-length request). Same fix as the
            # Ollama path above: a bare "" here is a diagnosis nobody can make.
            _log.warning("local_call (openai-style) HTTP %s from %s: %s",
                         r.status_code, model, (r.text or "")[:300])
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
