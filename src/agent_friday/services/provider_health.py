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
        return {"provider": name, "status": "missing", "detail": "no API key"}

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

    return {"provider": name, "status": "ok", "detail": "key present"}


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
