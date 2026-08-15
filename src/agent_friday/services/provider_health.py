"""
Agent Friday — Provider Health: point-in-time checks + a measurement plane.

Point-in-time checks (the original surface — wizard/Settings polling):
  * anthropic         — key present (env or encrypted store)
  * openai-compatible — key present; optional light GET {base_url}/models (deep)
  * ollama            — daemon reachable (ollama_manager.is_available)
  * google            — key present

A shallow check (default) never touches the network — it only reports whether a
key is configured / the local daemon is up — so it is offline- and test-safe. A
deep check does a light HTTP probe for openai-compatible endpoints. Results are
cached briefly so the wizard/Settings can poll without hammering anything.

Status values: "ok" | "missing" (no key) | "down" (unreachable) | "error" | "unknown".

Measurement plane (spec §8 — model-agnostic provider layer, P2):
  * record(provider, ok, latency_ms, status) — called from every adapter
    completion/failure (the same seam as cost_meter)
  * stats(provider) — rolling 15-minute window: request/error counts, error
    rate, p50/p95 latency, availability, consecutive failures
  * availability(provider) — "ok" (<5% err) | "degraded" (<25%) | "down"
  * circuit breaker — 5 consecutive failures trips "down" for a 60s cooldown,
    then half-open. Routing consults this to prefer healthy providers.

In-memory ring buffers only (last 256 calls per provider) — session-local,
zero new deps. Cross-session trends are cost_meter's job.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.request
from collections import deque

_CACHE: dict = {}
_TTL = 20.0  # seconds

# ── Measurement plane ────────────────────────────────────────────────────────
_RING_SIZE = 256
_WINDOW_S = 15 * 60.0
_BREAKER_THRESHOLD = 5     # consecutive failures that trip the breaker
_BREAKER_COOLDOWN_S = 60.0

_STATS_LOCK = threading.Lock()
_RINGS: dict = {}          # provider -> deque[(ts, ok, latency_ms, status)]
_CONSECUTIVE: dict = {}    # provider -> consecutive failure count
_TRIPPED_AT: dict = {}     # provider -> ts the breaker last tripped
_LAST_OK: dict = {}        # provider -> ts of last success
_LAST_ERR: dict = {}       # provider -> (ts, status) of last failure


def record(provider, ok, latency_ms=0, status=None, kind="chat"):
    """Record one call outcome for a provider. Never raises."""
    try:
        name = str(provider or "").strip()
        if not name:
            return
        now = time.time()
        with _STATS_LOCK:
            ring = _RINGS.get(name)
            if ring is None:
                ring = _RINGS[name] = deque(maxlen=_RING_SIZE)
            ring.append((now, bool(ok), int(latency_ms or 0), status))
            if ok:
                _CONSECUTIVE[name] = 0
                _LAST_OK[name] = now
                _TRIPPED_AT.pop(name, None)
            else:
                _CONSECUTIVE[name] = _CONSECUTIVE.get(name, 0) + 1
                _LAST_ERR[name] = (now, status)
                if _CONSECUTIVE[name] >= _BREAKER_THRESHOLD:
                    _TRIPPED_AT[name] = now
    except Exception:
        pass


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round((pct / 100.0)
                                                     * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def stats(provider):
    """Rolling-window stats for one provider. Providers with no recorded calls
    return availability "unknown" with zeroed counters."""
    name = str(provider or "").strip()
    now = time.time()
    with _STATS_LOCK:
        ring = list(_RINGS.get(name) or ())
        consecutive = _CONSECUTIVE.get(name, 0)
        tripped_at = _TRIPPED_AT.get(name)
        last_ok = _LAST_OK.get(name)
        last_err = _LAST_ERR.get(name)
    window = [r for r in ring if (now - r[0]) <= _WINDOW_S]
    requests_n = len(window)
    errors_n = sum(1 for r in window if not r[1])
    error_rate = (errors_n / requests_n) if requests_n else 0.0
    lat = sorted(r[2] for r in window if r[1] and r[2] > 0)

    # Circuit breaker: consecutive failures force "down" for the cooldown,
    # then half-open (report "degraded" so routing may retry with one probe).
    if tripped_at is not None:
        avail = "down" if (now - tripped_at) < _BREAKER_COOLDOWN_S else "degraded"
    elif requests_n == 0:
        avail = "unknown"
    elif error_rate < 0.05:
        avail = "ok"
    elif error_rate < 0.25:
        avail = "degraded"
    else:
        avail = "down"
    # Recovery path: when the MOST RECENT call succeeded (breaker closed), a
    # bad trailing window reports at worst "degraded" — one good probe restores
    # routability instead of waiting out the whole 15-minute window.
    if avail == "down" and tripped_at is None and window and window[-1][1]:
        avail = "degraded"

    return {
        "provider": name,
        "window": "15m",
        "requests": requests_n,
        "errors": errors_n,
        "error_rate": round(error_rate, 4),
        "latency_p50_ms": _percentile(lat, 50),
        "latency_p95_ms": _percentile(lat, 95),
        "availability": avail,
        "consecutive_failures": consecutive,
        "last_ok_at": last_ok,
        "last_error_at": last_err[0] if last_err else None,
        "last_error_status": last_err[1] if last_err else None,
    }


def availability(provider):
    """'ok' | 'degraded' | 'down' | 'unknown' — the routing-facing signal."""
    return stats(provider)["availability"]


def all_stats():
    with _STATS_LOCK:
        names = list(_RINGS.keys())
    return {n: stats(n) for n in names}


def reset_stats(provider=None):
    """Clear recorded stats (tests + explicit provider re-configuration)."""
    with _STATS_LOCK:
        if provider is None:
            _RINGS.clear(); _CONSECUTIVE.clear(); _TRIPPED_AT.clear()
            _LAST_OK.clear(); _LAST_ERR.clear()
        else:
            _RINGS.pop(provider, None); _CONSECUTIVE.pop(provider, None)
            _TRIPPED_AT.pop(provider, None); _LAST_OK.pop(provider, None)
            _LAST_ERR.pop(provider, None)


def _provider(name):
    from agent_friday.services.provider_registry import get_provider_registry
    return get_provider_registry().get_provider(name)


def _has_key(prov) -> bool:
    auth = (prov or {}).get("auth") or {}
    if auth.get("type") != "env_var":
        return True
    try:
        from agent_friday.routing.provider_descriptors import provider_env_keys
        env_keys = provider_env_keys(prov)
    except Exception:
        env_keys = [auth.get("key", "")]
    if any(os.environ.get(k) for k in env_keys if k):
        return True
    try:
        from agent_friday.services.credential_store import provider_key_status
        return provider_key_status(prov.get("name", "")) == "connected"
    except Exception:
        return False


def _check(name, deep=False) -> dict:
    prov = _provider(name)
    if not prov:
        return {"provider": name, "status": "unknown", "detail": "no such provider"}
    ptype = prov.get("type", "")

    if ptype == "ollama":
        try:
            from agent_friday.routing.ollama_manager import get_manager
            ok = get_manager(prov.get("base_url") or "http://localhost:11434").is_available()
            return {"provider": name, "status": "ok" if ok else "down",
                    "detail": "daemon reachable" if ok else "Ollama not running"}
        except Exception as e:
            return {"provider": name, "status": "down", "detail": str(e)[:120]}

    if ptype == "local-voice":
        # Tier-1 on-device voice. "ok" only when deps are importable AND the
        # ASR/TTS checkpoints are downloaded; else an actionable missing/needs.
        try:
            from agent_friday.services.local_voice import get_local_voice_engine
            h = get_local_voice_engine().health()
            return {"provider": name, "status": h.get("status", "unknown"),
                    "detail": h.get("detail", "")}
        except Exception as e:
            return {"provider": name, "status": "missing", "detail": str(e)[:120]}

    if ptype == "nemo-local":
        # Tier-2 GPU premium voice. "ok" only when torch+NeMo are installed AND a
        # CUDA GPU with enough VRAM is present AND the checkpoints are downloaded;
        # else an actionable missing/down/needs status from agent_friday.services.nemo_voice.
        try:
            from agent_friday.services.nemo_voice import nemo_health
            h = nemo_health()
            return {"provider": name, "status": h.get("status", "unknown"),
                    "detail": h.get("detail", "")}
        except Exception as e:
            return {"provider": name, "status": "missing", "detail": str(e)[:120]}

    if not _has_key(prov):
        return {"provider": name, "status": "missing", "detail": "no API key",
                "config": "missing", "proved_inference": False}

    if deep:
        # Decision D1: a key is CONFIGURATION, not health. Prove inference.
        probed = inference_probe(name, prov=prov)
        if probed is not None:
            return probed

    if deep and ptype == "openai-compatible":
        base = (prov.get("base_url") or "").rstrip("/")
        try:
            from agent_friday.routing.provider_descriptors import (
                provider_api_key, auth_headers)
            headers = auth_headers(prov, provider_api_key(prov))
        except Exception:
            key = os.environ.get((prov.get("auth") or {}).get("key", ""), "")
            headers = {"Authorization": f"Bearer {key}"}
        try:
            req = urllib.request.Request(base + "/models", headers=headers)
            with urllib.request.urlopen(req, timeout=6) as r:
                code = getattr(r, "status", 200)
                return {"provider": name, "status": "ok" if code < 400 else "error",
                        "detail": f"HTTP {code}"}
        except Exception as e:
            return {"provider": name, "status": "error", "detail": str(e)[:120]}

    # Shallow path. A present key proves CONFIGURATION only — never that
    # inference works (decision D1). `proved_inference: False` says so
    # explicitly so no caller can mistake this for a working backend.
    return {"provider": name, "status": "ok", "detail": "key present",
            "config": "ok", "proved_inference": False}


# ── Inference probe (decision D1) ────────────────────────────────────────────
# Before this, a revoked, rate-limited or zero-credit key reported "ok" on
# every health surface Friday exposed, because the check was `bool(key)`. The
# probe below sends a genuine one-token generation and reports what actually
# came back.
_PROBE_TTL_S = 60.0        # probes cost tokens; /api/health is polled often
_PROBE_CACHE: dict = {}    # provider -> (ts, result)
_PROBE_PROMPT = "hi"
# A ONE-token budget cannot reliably prove inference works. VERIFIED live
# 2026-08-15: /api/health reported `anthropic: down — empty completion` while
# Anthropic was serving chat perfectly well, because a 1-token completion can
# stop before any text block is emitted. Same failure shape as the thinking
# models, which spend a small budget reasoning and return content=''.
#
# 16 tokens is still a trivial spend (this is cached for _PROBE_TTL_S) and is
# enough for every provider to actually say something. A health check that
# reports down on a healthy system destroys trust in the signal just as surely
# as one that reports ok on a broken one — and this one was doing it to BOTH
# providers at once.
_PROBE_MAX_TOKENS = 16


def resident_model_for(prov) -> str | None:
    """The model a probe should exercise: what this provider would actually use.

    Ollama prefers the configured local model, else the smallest installed one
    (cheapest to load if nothing is resident). Cloud providers use the first
    declared model in their descriptor.
    """
    ptype = (prov or {}).get("type", "")
    try:
        from agent_friday.core import _load_settings
        cfg = (_load_settings() or {})
    except Exception:
        cfg = {}
    if ptype == "ollama":
        chosen = ((cfg.get("model_routing") or {}).get("local_model")
                  or cfg.get("local_model"))
        try:
            from agent_friday.routing.ollama_manager import get_manager
            installed = get_manager(prov.get("base_url")
                                    or "http://localhost:11434").list_models()
        except Exception:
            installed = None
        if installed:
            names = {m.get("name") for m in installed}
            # 2026-08-14: local_model pointed at DELETED gemma4:latest, so
            # the probe reported the healthy daemon as down ("no output").
            # Probe a model that actually exists — the configured one when
            # installed, else the smallest installed.
            #
            # 2026-08-14 (second defect, same line): "smallest installed" is
            # how the probe ended up sending qwen3-embedding:0.6b — a 639 MB
            # EMBEDDING model — to /api/generate. An embedding model cannot
            # produce text, so that probe returns empty forever and the entire
            # local provider reports `down` while the daemon is healthy.
            # Exclude anything that cannot generate before choosing.
            def _gen(name):
                try:
                    from agent_friday.services.residency_catalog import (
                        can_generate)
                    return can_generate(name)
                except Exception:
                    return "embed" not in (name or "").lower()

            if chosen and chosen in names and _gen(chosen):
                return chosen
            usable = [m for m in installed if _gen(m.get("name"))]
            if usable:
                return sorted(usable,
                              key=lambda m: m.get("size_gb") or 0)[0].get("name")
            return None
        return chosen or None
    if ptype == "anthropic":
        # 2026-08-14: orchestrator_model held the llama.cpp brain's alias,
        # and the probe sent it to Anthropic → 404 'model: qwen3.6-…' → the
        # anthropic provider shown DOWN while perfectly healthy. Same law as
        # the dispatch ladder: never send a foreign id to Anthropic.
        orch = cfg.get("orchestrator_model")
        if orch and str(orch).startswith("claude"):
            return orch
        return (prov.get("models") or [None])[0]
    return (prov.get("models") or [None])[0]


# A model this much slower than its own recorded baseline is not healthy, it is
# paging. Measured on the reference instance: llama.cpp throughput fell from
# 21.6 to 5.5 tok/s (~4x) at the point the allocator began thrashing rather
# than erroring, and Ollama's 26b spilled from 100% GPU to 79% CPU. 5x sits
# just past the worst honest slowdown and well inside a collapse.
LATENCY_UNHEALTHY_MULTIPLE = 5.0


def _timings_are_real() -> bool:
    """Is a measured latency trustworthy enough to judge?

    Under FRIDAY_TESTING the provider transports are wire-shaped doubles
    (tests/fake_backends.py, the D9 inversion). They return canned payloads
    whose `eval_count`/`eval_duration` are fabricated — the offline suite's
    stub implies 142.86 ms/token — and comparing that against a baseline
    measured on the real host reports a healthy stub as paging.

    The rule is gated here rather than inside `_latency_verdict` so the verdict
    itself stays pure and fully unit-tested; only its application to a
    fabricated measurement is suppressed. It also keeps the offline suite from
    shelling out to nvidia-smi, which it has no business doing.
    """
    return os.environ.get("FRIDAY_TESTING") != "1"


def _thinking(model_id: str) -> bool:
    """Thinking models spend a small token budget entirely on reasoning."""
    try:
        from agent_friday.services.residency_catalog import (
            needs_think_disabled)
        return needs_think_disabled(model_id)
    except Exception:
        return False


def _latency_verdict(model_id: str, ms_per_token) -> str | None:
    """The RAM-paging detector. Returns an explanation, or None if fine.

    Compares against the model's own recorded baseline rather than a global
    constant, because 6 ms/token is healthy for the e2b and impossible for the
    26b. No baseline means no verdict: an unmeasured model reports on liveness
    alone rather than being failed on a guess.

    The input is generation speed only — Ollama reports load_duration
    separately from eval_duration — so a 55s cold load cannot trip this.
    """
    if not ms_per_token:
        return None
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services.residency_catalog import (
            baseline_ms_per_token, baseline_probe_ms_per_token,
            profile_fingerprint)
        fp = profile_fingerprint(hwp.get())
        # Compare like with like. A 10-token probe pays fixed per-request
        # overhead that a 200-token throughput run amortises away: measured, a
        # healthy e2b probes at 8.68 ms/token against a 6.02 sustained
        # baseline. Using the sustained figure spends ~1.4x of the 5x margin
        # before anything is wrong, and on a contended box that is enough to
        # fire on a healthy model.
        baseline = baseline_probe_ms_per_token(model_id, fp)
        kind = "probe"
        if not baseline:
            baseline = baseline_ms_per_token(model_id, fp)
            kind = "sustained"
    except Exception:
        return None
    if not baseline:
        return None
    if ms_per_token > baseline * LATENCY_UNHEALTHY_MULTIPLE:
        return ("generation is %.1fx its recorded %s baseline (%.2f vs %.2f "
                "ms/token) — consistent with the model paging against RAM "
                "rather than running resident"
                % (ms_per_token / baseline, kind, ms_per_token, baseline))
    return None


def inference_probe(name, prov=None, use_cache=True) -> dict | None:
    """Send a one-token generation and report whether inference actually works.

    Returns a check dict, or None when this provider type has no probe (the
    caller then falls back to its previous behaviour). Results are cached for
    `_PROBE_TTL_S` because every probe spends real tokens and `/api/health` is
    polled by the tray watchdog.

    Never raises: a probe that blows up is a `down`, not a 500.
    """
    if use_cache:
        hit = _PROBE_CACHE.get(name)
        if hit and (time.time() - hit[0]) < _PROBE_TTL_S:
            return hit[1]

    prov = prov if prov is not None else _provider(name)
    if not prov:
        return None
    ptype = prov.get("type", "")
    model = resident_model_for(prov)

    def _result(status, detail, proved):
        # classification rides the health payload so the UI can label an
        # on-device seat as local (defect #7) — ollama is local by nature.
        res = {"provider": name, "status": status, "detail": detail,
               "config": "ok", "proved_inference": proved, "model": model,
               "classification": (prov.get("classification")
                                  or ("local" if ptype == "ollama"
                                      else "cloud"))}
        _PROBE_CACHE[name] = (time.time(), res)
        return res

    t0 = time.time()
    try:
        if ptype == "ollama":
            if not model:
                return _result("down", "no local model installed", False)
            from agent_friday.routing.ollama_manager import get_manager
            mgr = get_manager(prov.get("base_url") or "http://localhost:11434")
            # This is the call site decision D1 requires: `health_check` did a
            # real generation but had ZERO callers anywhere in the tree.
            out = mgr.probe_generate(
                model, disable_thinking=_thinking(model))
            ms = int((time.time() - t0) * 1000)
            if not out.get("ok"):
                return _result("down",
                               out.get("error")
                               or "no output from a real generation", False)
            slow = (_latency_verdict(model, out.get("ms_per_token"))
                    if _timings_are_real() else None)
            if slow:
                return _result("unhealthy", slow, True)
            return _result("ok", f"generated in {ms}ms"
                                 f" ({out.get('ms_per_token')} ms/token)", True)

        if ptype == "anthropic":
            from agent_friday.core import get_anthropic_client
            client = get_anthropic_client()
            if client is None:
                return _result("missing", "no client", False)
            resp = client.messages.create(
                model=model, max_tokens=_PROBE_MAX_TOKENS,
                messages=[{"role": "user", "content": _PROBE_PROMPT}])
            ms = int((time.time() - t0) * 1000)
            # ANY content block proves inference ran — not specifically a text
            # one. claude-sonnet-5 emits a `thinking` block first, so a modest
            # budget can produce a response whose only block is thinking. That
            # is a model that demonstrably generated, and calling it `down`
            # reported a provider that was serving chat as dead (VERIFIED live
            # 2026-08-15, intermittently: the same probe returned ok in 1614ms
            # and `empty completion` minutes later, purely on whether thinking
            # consumed the budget).
            #
            # Third instance of one pattern — Ollama thinking models, the
            # 1-token budget, and now Claude's thinking block. The lesson is
            # the same each time: prove that generation HAPPENED; do not
            # demand a particular shape of output to prove it.
            blocks = list(getattr(resp, "content", None) or [])
            kinds = [getattr(b, "type", None) for b in blocks]
            got = bool(blocks)
            detail = f"generated in {ms}ms"
            if got and "text" not in kinds:
                detail += f" ({'+'.join(k for k in kinds if k)} only)"
            return _result("ok" if got else "down",
                           detail if got else "empty completion", bool(got))

        if ptype == "openai-compatible":
            import requests
            from agent_friday.routing.provider_descriptors import (
                auth_headers, provider_api_key)
            base = (prov.get("base_url") or "").rstrip("/")
            if not base or not model:
                return _result("down", "no base_url or model", False)
            # A thinking model needs room to think before it can say anything;
            # a 1-token ceiling makes a healthy one look empty (measured on
            # gemma4 via llama-server: 200 OK, content='').
            budget = 64 if _thinking(model) else _PROBE_MAX_TOKENS
            payload = {"model": model, "max_tokens": budget,
                       "messages": [{"role": "user",
                                     "content": _PROBE_PROMPT}]}
            if _thinking(model):
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            r = requests.post(
                f"{base}/chat/completions",
                headers={"Content-Type": "application/json",
                         **auth_headers(prov, provider_api_key(prov))},
                json=payload, timeout=30)
            ms = int((time.time() - t0) * 1000)
            if r.status_code >= 400:
                return _result("down", f"HTTP {r.status_code}", False)
            body = r.json() or {}
            choices = body.get("choices") or []
            got = bool(choices)
            if not got:
                return _result("down", "empty completion", False)
            # llama-server reports real per-token timings; use them rather than
            # wall clock, which would include queueing and the HTTP round trip.
            per_tok = ((body.get("timings") or {})
                       .get("predicted_per_token_ms"))
            slow = (_latency_verdict(model, per_tok)
                    if _timings_are_real() else None)
            if slow:
                return _result("unhealthy", slow, True)
            return _result("ok", f"generated in {ms}ms", True)
    except Exception as e:
        return _result("down", f"{type(e).__name__}: {str(e)[:100]}", False)

    return None


def reset_probe_cache(provider=None):
    """Clear probe results (tests, and after a key/provider change)."""
    if provider is None:
        _PROBE_CACHE.clear()
    else:
        _PROBE_CACHE.pop(provider, None)


def inference_health(providers=None) -> dict:
    """Aggregate probe verdict across the providers that matter for chat.

    Returns {"status": ok|degraded|down, "providers": [...]}.
    `down` means every probed provider failed — Friday cannot currently think.
    `degraded` means at least one, but not all, failed.
    """
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        allp = get_provider_registry().list_providers()
    except Exception:
        return {"status": "unknown", "providers": []}

    probe_types = {"ollama", "anthropic", "openai-compatible"}
    results = []
    for p in allp:
        pname = p.get("name", "")
        if providers is not None and pname not in providers:
            continue
        if p.get("type", "") not in probe_types:
            continue
        if not _has_key(p):
            continue          # unconfigured is not unhealthy
        res = inference_probe(pname, prov=p)
        if res is not None:
            results.append(res)

    if not results:
        return {"status": "unknown", "providers": [],
                "detail": "no configured inference provider to probe"}
    oks = [r for r in results if r.get("status") == "ok"]
    if not oks:
        status = "down"
    elif len(oks) < len(results):
        status = "degraded"
    else:
        status = "ok"
    return {"status": status, "providers": results}


def check_provider(name, deep=False, use_cache=True) -> dict:
    ck = ("d" if deep else "s") + str(name)
    if use_cache:
        hit = _CACHE.get(ck)
        if hit and (time.time() - hit[0]) < _TTL:
            return hit[1]
    res = _check(name, deep=deep)
    _CACHE[ck] = (time.time(), res)
    return res


def check_all(deep=False) -> list:
    from agent_friday.services.provider_registry import get_provider_registry
    return [check_provider(p.get("name", ""), deep=deep)
            for p in get_provider_registry().list_providers()]
