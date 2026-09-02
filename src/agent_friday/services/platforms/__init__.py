"""
Platform adapter registry + lifecycle (docs/CONTENT_PIPELINE_SPEC.md §4.1).

Mirrors ``services/channels/manager.py``: lazy singleton adapters behind
double-checked locking, non-secret config in ``~/.friday/platforms.json``,
aggregate ``status()`` for the Accounts tab and /api/connectors/health.

Every adapter module is pre-declared here with a TOLERANT lazy import — a
missing or broken module means the adapter is reported unavailable, never an
import error. Adapter modules are developed independently and must never need
to touch this file: drop ``<name>.py`` into this package with a
``PlatformAdapter`` subclass (or a module-level ``ADAPTER`` class attribute)
and the registry picks it up.

Secrets never ride config — they live in the credential store
(``platform_<name>`` provider keys / ``~/.friday/platforms/<name>.cred`` blobs).
"""
from __future__ import annotations

import importlib
import json
import os
import threading
from typing import Any, Dict, List, Optional

from agent_friday.services.platforms.base import PlatformAdapter  # noqa: F401
from agent_friday.paths import friday_home

# Friday's state root. Resolved centrally so FRIDAY_HOME redirects this
# module along with everything else (see agent_friday/paths.py).
FRIDAY_DIR = friday_home()
CONFIG_PATH = FRIDAY_DIR / "platforms.json"

_LOCK = threading.Lock()
_ADAPTERS: Dict[str, Any] = {}        # module name -> adapter instance (lazy)
_IMPORT_ERRORS: Dict[str, str] = {}   # module name -> why it's unavailable

# Every adapter module ships (or will ship) under this package. Pre-declared so
# parallel adapter work never needs to edit the registry. Keys are the module
# names; values are the canonical platform id the pipeline stores on targets.
ADAPTER_MODULES: Dict[str, str] = {
    "linkedin":       "linkedin",
    "x_twitter":      "twitter",
    "instagram":      "instagram",
    "youtube":        "youtube",
    "tiktok":         "tiktok",
    "bluesky":        "bluesky",
    "mastodon":       "mastodon",
    "reddit":         "reddit",
    "substack":       "substack",
    "medium":         "medium",
    "federation_pub": "federation",
    "mock":           "mock",
}

# Accepted aliases → module name (platform ids, common spellings).
_ALIASES: Dict[str, str] = {
    "twitter": "x_twitter",
    "x": "x_twitter",
    "x-twitter": "x_twitter",
    "federation": "federation_pub",
}


def _norm(name: str) -> str:
    n = (name or "").strip().lower().replace("-", "_")
    return _ALIASES.get(n, _ALIASES.get((name or "").strip().lower(), n))


# ── config I/O (non-secret) ───────────────────────────────────────────────────
def _default_config() -> Dict[str, Any]:
    return {"pause_all": False}


def load_config() -> Dict[str, Any]:
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            base = _default_config()
            base.update(cfg or {})
            return base
    except Exception:
        pass
    return _default_config()


def save_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _sanitize_opts(opts: Any, depth: int = 0) -> Dict[str, Any]:
    """Bound caller-supplied options — these arrive straight from JSON bodies,
    so types and sizes are attacker-controllable."""
    out: Dict[str, Any] = {}
    if not isinstance(opts, dict) or depth > 2:
        return out
    for k, v in list(opts.items())[:50]:
        k = str(k)[:64]
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:512]
        elif isinstance(v, list):
            out[k] = [str(x)[:256] for x in v[:100]]
        elif isinstance(v, dict):
            out[k] = _sanitize_opts(v, depth + 1)
        elif v is None:
            out[k] = None
    return out


def configure_platform(name: str, opts: Dict[str, Any], *,
                       secret_value: Optional[str] = None) -> Dict[str, Any]:  # pragma: allowlist secret
    """Set a platform's non-secret options; store a simple secret (app password
    / personal token) in the credential store when supplied."""
    mod = _norm(name)
    if mod not in ADAPTER_MODULES:
        return {"ok": False, "error": f"unknown platform: {name}"}
    cfg = load_config()
    section = cfg.get(mod) if isinstance(cfg.get(mod), dict) else {}
    section.update(_sanitize_opts(opts))
    cfg[mod] = section
    res = save_config(cfg)
    if secret_value:
        try:
            from agent_friday.services import credential_store
            credential_store.set_provider_key(f"platform_{mod}", secret_value)
            credential_store.audit_event("platform", "secret_stored", platform=mod)
        except Exception as e:
            return {"ok": False, "error": f"secret store failed: {e}"}
    # push options to a live adapter if one exists
    a = _ADAPTERS.get(mod)
    if a is not None:
        a.configure(section)
    return res


def publishing_paused() -> bool:
    """Global pause-all kill switch (§6.6) — honored by the publisher tick."""
    return bool(load_config().get("pause_all", False))


# ── registry / lifecycle ──────────────────────────────────────────────────────
def _find_adapter_class(module) -> Optional[type]:
    cls = getattr(module, "ADAPTER", None)
    if isinstance(cls, type) and issubclass(cls, PlatformAdapter):
        return cls
    for obj in vars(module).values():
        if (isinstance(obj, type) and issubclass(obj, PlatformAdapter)
                and obj is not PlatformAdapter
                and obj.__module__ == module.__name__):
            return obj
    return None


def _make_adapter(mod: str):
    """Tolerant lazy import: a missing/broken module = adapter unavailable,
    never an ImportError out of the registry."""
    try:
        module = importlib.import_module(f"agent_friday.services.platforms.{mod}")
    except Exception as e:
        _IMPORT_ERRORS[mod] = f"import failed: {e}"
        return None
    try:
        cls = _find_adapter_class(module)
        if cls is None:
            _IMPORT_ERRORS[mod] = "no PlatformAdapter subclass in module"
            return None
        return cls()
    except Exception as e:
        _IMPORT_ERRORS[mod] = f"construction failed: {e}"
        return None


def get_adapter(name: str):
    mod = _norm(name)
    if mod not in ADAPTER_MODULES:
        return None
    a = _ADAPTERS.get(mod)
    if a is not None:
        return a
    # Double-checked locking (channels/manager.py pattern): Flask is threaded,
    # and two concurrent requests must not build two singletons.
    with _LOCK:
        a = _ADAPTERS.get(mod)
        if a is not None:
            return a
        a = _make_adapter(mod)
        if a is None:
            return None
        cfg = load_config().get(mod)
        a.configure(cfg if isinstance(cfg, dict) else {})
        _ADAPTERS[mod] = a
        _IMPORT_ERRORS.pop(mod, None)
        return a


def available(name: str) -> bool:
    return get_adapter(name) is not None


def import_error(name: str) -> Optional[str]:
    return _IMPORT_ERRORS.get(_norm(name))


def list_adapters() -> List[str]:
    """Module names of adapters that import and construct cleanly."""
    return [m for m in ADAPTER_MODULES if get_adapter(m) is not None]


def status() -> Dict[str, Any]:
    """Aggregate status for the Accounts tab / connectors health — one entry
    per declared adapter, available or not. Never raises."""
    out: Dict[str, Any] = {"pause_all": publishing_paused(), "platforms": {}}
    for mod, platform_id in ADAPTER_MODULES.items():
        a = get_adapter(mod)
        if a is None:
            out["platforms"][mod] = {
                "name": mod, "platform": platform_id, "available": False,
                "connected": False,
                "error": _IMPORT_ERRORS.get(mod, "unavailable"),
            }
            continue
        try:
            st = a.status()
            st = st if isinstance(st, dict) else {"name": mod}
        except Exception as e:
            st = {"name": mod, "connected": False, "last_error": str(e)}
        st["available"] = True
        st["platform"] = platform_id
        out["platforms"][mod] = st
    return out


def _reset_for_tests() -> None:
    """Drop singletons + cached import errors (test isolation only)."""
    with _LOCK:
        _ADAPTERS.clear()
        _IMPORT_ERRORS.clear()
