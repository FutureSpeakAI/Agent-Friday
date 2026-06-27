"""
Agent Friday — Provider Health Checks

Cheap, cached reachability/auth checks per provider, by type:
  * anthropic         — key present (env or encrypted store)
  * openai-compatible — key present; optional light GET {base_url}/models (deep)
  * ollama            — daemon reachable (ollama_manager.is_available)
  * google            — key present

A shallow check (default) never touches the network — it only reports whether a
key is configured / the local daemon is up — so it is offline- and test-safe. A
deep check does a light HTTP probe for openai-compatible endpoints. Results are
cached briefly so the wizard/Settings can poll without hammering anything.

Status values: "ok" | "missing" (no key) | "down" (unreachable) | "error" | "unknown".
"""
from __future__ import annotations

import os
import time
import urllib.request

_CACHE: dict = {}
_TTL = 20.0  # seconds


def _provider(name):
    from agent_friday.services.provider_registry import get_provider_registry
    return get_provider_registry().get_provider(name)


def _has_key(prov) -> bool:
    auth = (prov or {}).get("auth") or {}
    if auth.get("type") != "env_var":
        return True
    if os.environ.get(auth.get("key", "")):
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
        key = os.environ.get((prov.get("auth") or {}).get("key", ""), "")
        try:
            req = urllib.request.Request(base + "/models",
                                         headers={"Authorization": f"Bearer {key}"})
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
