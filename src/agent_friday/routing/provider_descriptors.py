"""
routing/provider_descriptors.py — Provider Descriptor Schema v2 + Model Resolver.

The foundation of the model-agnostic provider layer. Three responsibilities:

  1. **Descriptor schema v2** — `normalize_descriptor()` upgrades any v1
     descriptor (the shipped defaults and every JSON in ~/.friday/providers/)
     to the v2 shape in place-safe fashion, and `validate_descriptor()` gives
     actionable errors instead of today's silent `except: pass` skip.

  2. **Built-in descriptors** — the ten additional cloud providers that ship
     with Friday (OpenRouter first-class, HuggingFace, Groq, Together,
     Fireworks, Mistral, DeepSeek, xAI, Perplexity, Cohere). The six original
     providers (anthropic, openai, ollama-local, google-gemini, local-voice-lite,
     nvidia-nemo) stay defined in services/provider_registry.py and are
     normalized through this module at registry load.

  3. **The model resolver** — `resolve_model(model_id)` maps a model id to the
     provider that actually owns it, registry-first (GAP-4 fix). This replaces
     name-substring guessing (`provider_family`) as the dispatch authority:
     `meta-llama/llama-4-maverick:free` resolves to OpenRouter (not "local"
     because of the ':'), `gemma3:4b` resolves to Ollama, `claude-sonnet-5`
     resolves to Anthropic, and `provider::model` is always explicit.

Security notes (spec §9):
  * `classification` defaults to **cloud**. "local" is only honored when the
    adapter is local-capable AND the base_url resolves to a loopback/RFC1918/
    .local host — checked at load (`normalize_descriptor` demotes and warns)
    and re-checked at call time by the executor (`is_private_host`).
  * Descriptors are DATA, never secrets: `validate_descriptor` rejects raw
    api_key material, and `extra_headers` may not set Authorization.

This module must not import agent_friday.services at module level (services
imports routing); all such imports are lazy inside functions.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time
from urllib.parse import urlparse

_log = logging.getLogger("friday.providers")

# ── Adapter vocabulary ──────────────────────────────────────────────────────
# `adapter` names WHICH SHIPPED TRANSPORT speaks to the provider. The v1 field
# `type` used the same vocabulary, so the two stay aliased 1:1.
ADAPTER_ANTHROPIC = "anthropic"
ADAPTER_GOOGLE = "google"
ADAPTER_OPENAI_COMPAT = "openai-compatible"
ADAPTER_OLLAMA = "ollama"
ADAPTER_LOCAL_VOICE = "local-voice"
ADAPTER_NEMO = "nemo-local"
# On-device image generation (ComfyUI HTTP API on loopback). Local-capable for
# the same reason ollama is: the transport terminates on this machine, so a
# prompt sent to it never leaves. It still has to prove a private base_url
# below — the adapter alone does not grant "local".
ADAPTER_COMFYUI = "comfyui"
ADAPTER_LITELLM = "litellm"  # optional escape hatch (spec §5.3) — not shipped yet

ADAPTER_TYPES = (
    ADAPTER_ANTHROPIC, ADAPTER_GOOGLE, ADAPTER_OPENAI_COMPAT,
    ADAPTER_OLLAMA, ADAPTER_LOCAL_VOICE, ADAPTER_NEMO, ADAPTER_COMFYUI,
    ADAPTER_LITELLM,
)

# Adapters whose transport CAN keep data on-device. Only these may earn
# classification "local" (an anthropic/google SDK call is cloud by definition).
LOCAL_CAPABLE_ADAPTERS = {
    ADAPTER_OLLAMA, ADAPTER_LOCAL_VOICE, ADAPTER_NEMO, ADAPTER_OPENAI_COMPAT,
    ADAPTER_COMFYUI,
}

CLASSIFICATION_CLOUD = "cloud"
CLASSIFICATION_LOCAL = "local"

SCHEMA_VERSION = 2

# ── Private-host verification (spec §9.2) ───────────────────────────────────

_PRIVATE_HOST_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa")

# Short-TTL cache for DNS-backed host checks so the egress gate (which calls
# per outbound request) doesn't resolve the same LAN hostname repeatedly.
_HOST_CACHE: dict = {}
_HOST_CACHE_TTL = 60.0
_HOST_CACHE_LOCK = threading.Lock()


def _ip_is_private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def is_private_host(url: str | None, resolve_dns: bool = True) -> bool:
    """True when `url` points at this device or the local network.

    Rules (fail-closed: anything unverifiable is NOT local, so it gets gated):
      * empty/None        → True (in-process engines: local-voice, nemo)
      * localhost / *.local / *.lan / *.internal / *.home.arpa → True
      * IP literal        → loopback / RFC1918 / link-local
      * DNS hostname      → resolves AND every returned address is private;
                            resolution failure or any public A/AAAA → False
    """
    if url is None or str(url).strip() == "":
        return True
    raw = str(url).strip()
    parsed = urlparse(raw if "://" in raw else "http://" + raw)
    host = (parsed.hostname or "").strip().strip("[]").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(_PRIVATE_HOST_SUFFIXES):
        return True
    # IP literal — no DNS needed.
    try:
        ipaddress.ip_address(host)
        return _ip_is_private(host)
    except ValueError:
        pass
    if not resolve_dns:
        return False
    now = time.time()
    with _HOST_CACHE_LOCK:
        hit = _HOST_CACHE.get(host)
        if hit and (now - hit[0]) < _HOST_CACHE_TTL:
            return hit[1]
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        addrs = {i[4][0] for i in infos}
        result = bool(addrs) and all(_ip_is_private(a) for a in addrs)
    except Exception:
        result = False  # cannot verify → treat as cloud (gated)
    with _HOST_CACHE_LOCK:
        _HOST_CACHE[host] = (now, result)
    return result


# ── Descriptor helpers ──────────────────────────────────────────────────────

def adapter_of(prov: dict | None) -> str:
    """The effective adapter for a descriptor (v2 `adapter`, task-spec
    `adapter_type`, or v1 `type` — first present wins)."""
    p = prov or {}
    return str(p.get("adapter") or p.get("adapter_type") or p.get("type")
               or ADAPTER_OPENAI_COMPAT)


def classification_of(prov: dict | None) -> str:
    """Effective egress classification. Default **cloud** (spec G5).

    "local" must be earned: local-capable adapter AND a private base_url.
    A descriptor claiming local with a public URL is treated as cloud here —
    this is the second (call-time) half of the double verification; the first
    half happens at load in `normalize_descriptor`.
    """
    p = prov or {}
    claimed = str(p.get("classification") or p.get("egress_classification")
                  or "").lower()
    if claimed != CLASSIFICATION_LOCAL:
        # No claim → infer local only for inherently-local adapter types at a
        # private address (v1 descriptors carry no classification field).
        if adapter_of(p) in (ADAPTER_OLLAMA, ADAPTER_LOCAL_VOICE, ADAPTER_NEMO) \
                and is_private_host(p.get("base_url")):
            return CLASSIFICATION_LOCAL
        return CLASSIFICATION_CLOUD
    if adapter_of(p) not in LOCAL_CAPABLE_ADAPTERS:
        return CLASSIFICATION_CLOUD
    if not is_private_host(p.get("base_url")):
        return CLASSIFICATION_CLOUD
    return CLASSIFICATION_LOCAL


def provider_env_keys(prov: dict | None) -> list:
    """All env var names this provider's auth accepts (primary + aliases +
    the task-spec `api_key_env` list form), in precedence order."""
    p = prov or {}
    auth = p.get("auth") or {}
    keys = []
    primary = auth.get("key")
    if isinstance(primary, (list, tuple)):  # api_key_env-style list
        keys.extend(str(k) for k in primary if k)
    elif primary:
        keys.append(str(primary))
    for alias in (auth.get("key_aliases") or []):
        if alias:
            keys.append(str(alias))
    for alias in (p.get("api_key_env") or []):  # task-spec top-level form
        if alias:
            keys.append(str(alias))
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _stored_key(prov: dict | None) -> str | None:
    """The key saved through the product, or None. Never raises.

    A locked or corrupt store must not take a working environment key down
    with it -- that would turn a recoverable problem into a dead provider.
    """
    try:
        from agent_friday.services.credential_store import get_provider_key
        return get_provider_key((prov or {}).get("name", "")) or None
    except Exception:
        return None


def _env_key(prov: dict | None) -> str | None:
    """The key the ambient environment supplies, or None."""
    for env in provider_env_keys(prov or {}):
        val = os.environ.get(env)
        if val:
            return val
    return None


def provider_api_key(prov: dict | None) -> str | None:
    """Resolve a provider's API key: the SAVED key first, then the environment.

    Returns None when the provider needs no key (auth.type != env_var) or
    none is configured. Keys NEVER come from the descriptor or settings.json.

    THE ORDER IS THE FEATURE, AND IT USED TO BE THE OTHER WAY ROUND.

    Environment-first made a swapped key silently revert on the next launch,
    because the two halves of the product write to different places:

        setup wizard -> config.yaml + settings.json + START.BAT (never the store)
        Settings UI  -> encrypted credential store              (never start.bat)

    and Friday re-bootstraps her environment from start.bat every launch
    (_bootstrap_env_from_launch_scripts). So the wizard's first key outlived
    every replacement: paste a new one in Settings, watch it work -- the hot
    reload sets os.environ live -- restart, and the dead key from start.bat
    is back in front of it, with the panel still reporting "connected".

    Stephen, 2026-08-26: "we need to fix the installer so the Friday that
    ships to users can swap API keys from the settings menu". Swapping was
    the half that did not survive, and a user cannot tell a key that never
    worked from one that stopped.

    A key saved through the product is a deliberate, later instruction. An
    environment variable is ambient configuration. That is the same
    distinction `model_router._chosen_seat` draws between an explicit binding
    and an untouched factory default, and `seat_binding` draws between "a
    value he changed" and "the factory value".

    Safe to flip: with an empty store the two rules are identical, and the
    store was unreachable for anthropic/google-gemini until the Providers tab
    was wired on 2026-08-26 -- this machine's store held only atlascloud and
    firecrawl. And nothing shadows silently in either direction now, because
    /api/providers reports `key_source`: a start.bat rotation that does not
    take is visible rather than baffling.
    """
    p = prov or {}
    if (p.get("auth") or {}).get("type", "env_var") != "env_var":
        return None
    return _stored_key(p) or _env_key(p)


def provider_key_source(prov: dict | None) -> str:
    """Which source is actually supplying this provider's key.

    "settings" | "environment" | "none". Exists so neither source can shadow
    the other invisibly -- the failure this whole module's precedence rule
    used to produce.
    """
    p = prov or {}
    if (p.get("auth") or {}).get("type", "env_var") != "env_var":
        return "none"
    if _stored_key(p):
        return "settings"
    if _env_key(p):
        return "environment"
    return "none"


def mask_key(key: str | None) -> str:
    """Enough of a key to recognise it. Never enough to use it.

    A user with two keys needs to tell them apart -- that is the whole job.
    Four trailing characters do it; anything more is a courtesy to whoever
    reads the screenshot. Keys too short to mask safely return the ellipsis
    alone rather than leaking a meaningful fraction of themselves.
    """
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) < 12:
        return "…"
    return "…" + k[-4:]


def auth_headers(prov: dict | None, api_key: str | None = None) -> dict:
    """HTTP headers for a provider request: auth (header/scheme from the
    descriptor, default `Authorization: Bearer …`) + static extra_headers.
    extra_headers can never override the auth header (spec §15.2)."""
    p = prov or {}
    auth = p.get("auth") or {}
    headers = {}
    for k, v in (p.get("extra_headers") or {}).items():
        if str(k).lower() == "authorization":
            continue  # auth flows only through `auth`
        headers[str(k)] = str(v)
    key = api_key if api_key is not None else provider_api_key(p)
    if key:
        header = str(auth.get("header") or "Authorization")
        scheme = auth.get("scheme")
        scheme = "Bearer" if scheme is None else str(scheme)
        headers[header] = (scheme + " " + key).strip() if scheme else key
    return headers


# ── Built-in descriptors (the ten new cloud providers) ──────────────────────
# The six original providers stay in services/provider_registry.DEFAULT_PROVIDERS;
# everything loads through normalize_descriptor() so all sixteen share the v2
# shape. OpenRouter ships ENABLED (T1, spec §12.1); the rest are one-click
# templates (enabled: False) that Settings can switch on.

# ── Where a person without a key goes to get one ────────────────────────────
#
# The console page that ISSUES the credential, not the marketing homepage and
# not a docs index: someone who has decided to use a provider wants the form,
# and one more hop is where people give up.
#
# This table exists because the Providers panel used to ask for a key by
# naming the environment variable it would be stored under — "needs
# ANTHROPIC_API_KEY". That sentence is complete only if you already have the
# key. Stephen installed Friday on Janet's laptop on 2026-08-26 and had to
# open a code editor to put a Gemini key in, because nothing anywhere in the
# product connected "Friday needs a key" to "here is where keys come from".
#
# Applied in `normalize_descriptor`, so it reaches built-ins, the JSON files
# in ~/.friday/providers/, and anything typed into Add Provider alike. A
# descriptor that names its own `signup_url` keeps it; an unknown provider
# that names none is left empty rather than sent somewhere invented.
SIGNUP_URLS = {
    "anthropic":   "https://console.anthropic.com/settings/keys",
    "openai":      "https://platform.openai.com/api-keys",
    "google-gemini": "https://aistudio.google.com/app/apikey",
    "openrouter":  "https://openrouter.ai/keys",
    "huggingface": "https://huggingface.co/settings/tokens",
    "groq":        "https://console.groq.com/keys",
    "together":    "https://api.together.xyz/settings/api-keys",
    "fireworks":   "https://fireworks.ai/account/api-keys",
    "mistral":     "https://console.mistral.ai/api-keys",
    "deepseek":    "https://platform.deepseek.com/api_keys",
    "xai":         "https://console.x.ai",
    "perplexity":  "https://www.perplexity.ai/settings/api",
    "cohere":      "https://dashboard.cohere.com/api-keys",
}


def _mk(name, label, base_url, env_key, *, aliases=(), enabled=False,
        priority=50, discovery_parser="openai", discovery=True, models=(),
        extra_headers=None, features=None, rate_limit_style="none",
        model_format="plain", capabilities=("tools",), max_models=0,
        model_meta=None):
    d = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "label": label,
        "type": ADAPTER_OPENAI_COMPAT,      # v1 compat mirror of `adapter`
        "adapter": ADAPTER_OPENAI_COMPAT,
        "base_url": base_url,
        "auth": {"type": "env_var", "key": env_key,
                 "key_aliases": list(aliases)},
        "network": {"timeout_s": 180, "retries": 1,
                    "rate_limit_style": rate_limit_style},
        "classification": CLASSIFICATION_CLOUD,
        "discovery": ({"mode": "api", "endpoint": "/models",
                       "parser": discovery_parser, "ttl_s": 86400,
                       "max_models": max_models}
                      if discovery else {"mode": "static"}),
        "models": list(models),
        "model_format": model_format,
        "model_meta": dict(model_meta or {}),
        "capabilities": list(capabilities),
        "roles": ["orchestrator", "subagent"],
        "cost_per_1k": {},
        "pricing": {"source": "discovery" if discovery else "static",
                    "currency": "USD", "static": {}},
        "budget": {"daily_usd": None, "monthly_usd": None, "on_exceed": "block"},
        "features": dict(features or {}),
        "enabled": enabled,
        "priority": priority,
    }
    if extra_headers:
        d["extra_headers"] = dict(extra_headers)
    return d


BUILTIN_EXTRA_PROVIDERS = [
    # ── OpenRouter — first-class aggregator (T1, promoted; spec §12.1) ──────
    _mk("openrouter", "OpenRouter",
        "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        aliases=("OR_API_KEY",), enabled=True, priority=40,
        discovery_parser="openrouter", model_format="org/model",
        capabilities=("tools", "vision"), rate_limit_style="openrouter",
        extra_headers={"HTTP-Referer": "https://futurespeak.ai",
                       "X-Title": "Agent Friday"},
        features={"fallback_models_param": True, "usage_accounting": True,
                  "keyless_discovery": True, "aggregator": True,
                  "streaming": True,
                  "model_suffixes": [":free", ":beta", ":nitro", ":floor",
                                      ":thinking"]},
        # The Auto Router arrives in the live catalog as a bare id among ~400
        # others. Name it for what it is, so "let Friday decide" is findable
        # by the person looking for it rather than by someone who already
        # knows OpenRouter's slug.
        model_meta={"openrouter/auto": {
            "label": "Let Friday decide (OpenRouter Auto)",
            "short": "Auto-route"}}),
    # ── HuggingFace Inference Providers router (T2; spec §12.2) ─────────────
    _mk("huggingface", "Hugging Face (Inference Providers)",
        "https://router.huggingface.co/v1", "HF_TOKEN",
        aliases=("HUGGINGFACE_API_KEY", "HUGGING_FACE_HUB_TOKEN"),
        priority=45, discovery_parser="hf-router", model_format="org/model",
        max_models=100,
        features={"hub_search": True, "aggregator": True,
                  "model_suffixes": [":hf-inference", ":groq", ":together",
                                      ":fireworks-ai"]}),
    # ── Direct OpenAI-compatible clouds (T2 templates) ──────────────────────
    _mk("groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
        priority=50),
    _mk("together", "Together AI", "https://api.together.xyz/v1",
        "TOGETHER_API_KEY", priority=55, model_format="org/model"),
    _mk("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1",
        "FIREWORKS_API_KEY", priority=55, model_format="org/model"),
    _mk("mistral", "Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
        priority=55, models=("mistral-large-latest", "mistral-small-latest")),
    _mk("deepseek", "DeepSeek", "https://api.deepseek.com/v1",
        "DEEPSEEK_API_KEY", priority=55,
        models=("deepseek-chat", "deepseek-reasoner")),
    _mk("xai", "xAI (Grok)", "https://api.x.ai/v1", "XAI_API_KEY",
        priority=55),
    # Perplexity has no /models endpoint — the statics-only cloud showcase.
    _mk("perplexity", "Perplexity", "https://api.perplexity.ai",
        "PERPLEXITY_API_KEY", priority=60, discovery=False,
        models=("sonar", "sonar-pro", "sonar-reasoning", "sonar-reasoning-pro",
                "sonar-deep-research")),
    _mk("cohere", "Cohere", "https://api.cohere.ai/compatibility/v1",
        "COHERE_API_KEY", priority=60),
]


# ── Normalization (v1 → v2, defaults, load-time demotion) ───────────────────

def normalize_descriptor(data: dict) -> dict:
    """Return a v2-normalized copy of a descriptor. Never mutates the input.

    * `type` / `adapter` / `adapter_type` are aliased both ways.
    * `api_key_env: [..]` (task-spec form) folds into auth.key/key_aliases.
    * `egress_classification` aliases to `classification`.
    * classification defaults to cloud; an explicit "local" claim that fails
      the private-host or adapter check is DEMOTED to cloud with a logged
      warning (spec §9.2 — the first half of the double verification).
    * v2 sub-blocks (network, discovery, pricing, budget, features) get
      defaults so downstream readers never need `.get` chains.
    """
    d = dict(data or {})
    d.setdefault("schema_version", 1)

    # Adapter/type aliasing (both directions — legacy readers check `type`).
    adapter = adapter_of(d)
    d["adapter"] = adapter
    d.setdefault("type", adapter)

    # Auth block. The task-spec form allows api_key_env as a LIST of env names.
    auth = dict(d.get("auth") or {})
    api_key_env = d.pop("api_key_env", None)
    if api_key_env:
        names = ([api_key_env] if isinstance(api_key_env, str)
                 else [str(k) for k in api_key_env if k])
        if names:
            auth.setdefault("type", "env_var")
            auth.setdefault("key", names[0])
            aliases = list(auth.get("key_aliases") or [])
            aliases += [n for n in names[1:] if n not in aliases]
            auth["key_aliases"] = aliases
    if isinstance(auth.get("key"), (list, tuple)):
        names = [str(k) for k in auth["key"] if k]
        auth["key"] = names[0] if names else ""
        aliases = list(auth.get("key_aliases") or [])
        auth["key_aliases"] = aliases + [n for n in names[1:] if n not in aliases]
    if not auth:
        auth = {"type": "none"}
    d["auth"] = auth

    # A descriptor that demands a key carries the page that issues one. Its
    # own value always wins — the table is a convenience for the providers
    # Friday ships, not an override of what a descriptor's author said. An
    # unknown provider with no link keeps an empty string: honest, and the UI
    # simply renders no button. Guessing a URL would be worse than none.
    if auth.get("type") == "env_var":
        d["signup_url"] = (d.get("signup_url")
                           or SIGNUP_URLS.get(d.get("name") or "", ""))
    else:
        # Ollama, ComfyUI, the in-process voice engines: no account exists to
        # sign up for, and a button there teaches the wrong thing.
        d.pop("signup_url", None)

    # Classification: alias, default cloud, demote unearned "local".
    claimed = str(d.get("classification") or d.get("egress_classification")
                  or "").lower()
    d.pop("egress_classification", None)
    if claimed == CLASSIFICATION_LOCAL:
        effective = classification_of({**d, "classification": "local"})
        if effective != CLASSIFICATION_LOCAL:
            _log.warning(
                "provider %r claims classification=local but adapter=%s / "
                "base_url=%r is not verifiably on-device — demoted to cloud "
                "(egress-gated)", d.get("name"), adapter, d.get("base_url"))
        d["classification"] = effective
    elif claimed == CLASSIFICATION_CLOUD:
        d["classification"] = CLASSIFICATION_CLOUD
    else:
        d["classification"] = classification_of(d)

    # Sub-block defaults.
    d.setdefault("label", d.get("name"))
    d.setdefault("models", [])
    d.setdefault("model_meta", {})
    d.setdefault("capabilities", [])
    d.setdefault("cost_per_1k", {})
    d.setdefault("enabled", True)
    net = dict(d.get("network") or {})
    net.setdefault("timeout_s", 180)
    net.setdefault("retries", 1)
    net.setdefault("rate_limit_style", "none")
    d["network"] = net
    disc = dict(d.get("discovery") or {})
    disc.setdefault("mode", "static")
    if disc["mode"] == "api":
        disc.setdefault("endpoint", "/models")
        disc.setdefault("parser", "openai")
        disc.setdefault("ttl_s", 86400)
        disc.setdefault("max_models", 0)
    d["discovery"] = disc
    pricing = dict(d.get("pricing") or {})
    pricing.setdefault("source", "dataset")
    pricing.setdefault("currency", "USD")
    pricing.setdefault("static", {})
    d["pricing"] = pricing
    budget = dict(d.get("budget") or {})
    budget.setdefault("daily_usd", None)
    budget.setdefault("monthly_usd", None)
    budget.setdefault("on_exceed", "block")
    d["budget"] = budget
    d.setdefault("features", {})
    try:
        d["priority"] = int(d.get("priority", 50))
    except (TypeError, ValueError):
        d["priority"] = 50
    d["schema_version"] = SCHEMA_VERSION
    return d


# Fields that would smuggle secret material into a descriptor. POST /api/providers
# and file loads reject these outright (spec §15.1).
_SECRET_FIELDS = ("api_key", "apikey", "secret", "token", "password")

_NAME_RE_OK = None


def _name_ok(name: str) -> bool:
    global _NAME_RE_OK
    if _NAME_RE_OK is None:
        import re
        _NAME_RE_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
    return bool(_NAME_RE_OK.match(name or ""))


def validate_descriptor(data: dict) -> tuple:
    """Validate a descriptor. Returns (ok, errors[], warnings[]).

    Errors block persistence; warnings surface in the UI but don't block.
    """
    errors, warnings = [], []
    if not isinstance(data, dict):
        return False, ["descriptor must be a JSON object"], []

    name = str(data.get("name") or "").strip()
    if not name:
        errors.append("'name' is required")
    elif not _name_ok(name):
        errors.append("'name' must match ^[a-z0-9][a-z0-9-]{0,63}$ "
                      "(lowercase letters, digits, hyphens)")

    adapter = adapter_of(data)
    if adapter not in ADAPTER_TYPES:
        errors.append(
            f"unknown adapter {adapter!r} — must be one of: "
            + ", ".join(ADAPTER_TYPES))

    # Secret material never rides in a descriptor.
    for field in _SECRET_FIELDS:
        if data.get(field) or (data.get("auth") or {}).get(field):
            errors.append(
                f"descriptor contains raw credential field {field!r} — API keys "
                f"are stored encrypted via POST /api/providers/<name>/key, "
                f"never in the descriptor")
            break
    for hk in (data.get("extra_headers") or {}):
        if str(hk).lower() == "authorization":
            errors.append("extra_headers may not set Authorization — auth flows "
                          "only through the 'auth' block")
            break

    auth = data.get("auth")
    if auth is not None:
        atype = (auth or {}).get("type", "env_var")
        if atype not in ("env_var", "none"):
            errors.append(f"auth.type must be 'env_var' or 'none' (got {atype!r})")
        elif atype == "env_var" and not provider_env_keys(data):
            warnings.append("auth.type is env_var but no env var name is set — "
                            "only a stored credential-store key will work")

    base_url = str(data.get("base_url") or "").strip()
    networked = adapter in (ADAPTER_OPENAI_COMPAT, ADAPTER_OLLAMA,
                            ADAPTER_ANTHROPIC, ADAPTER_GOOGLE)
    if networked and not base_url:
        errors.append(f"'base_url' is required for adapter {adapter!r}")
    if base_url:
        parsed = urlparse(base_url if "://" in base_url else "http://" + base_url)
        if parsed.scheme not in ("http", "https"):
            errors.append(f"base_url scheme must be http(s), got {parsed.scheme!r}")
        elif parsed.scheme == "http" and not is_private_host(base_url):
            claimed = str(data.get("classification")
                          or data.get("egress_classification") or "cloud").lower()
            if claimed == CLASSIFICATION_CLOUD or claimed not in (
                    CLASSIFICATION_LOCAL,):
                errors.append("cloud providers require https base_url "
                              "(plain http is only allowed for private/LAN hosts)")

    claimed = str(data.get("classification")
                  or data.get("egress_classification") or "").lower()
    if claimed and claimed not in (CLASSIFICATION_CLOUD, CLASSIFICATION_LOCAL):
        errors.append(f"classification must be 'cloud' or 'local' (got {claimed!r})")
    if claimed == CLASSIFICATION_LOCAL:
        if adapter not in LOCAL_CAPABLE_ADAPTERS:
            warnings.append(f"classification 'local' is not honored for adapter "
                            f"{adapter!r} — it will be treated as cloud (gated)")
        elif not is_private_host(base_url):
            warnings.append("classification 'local' requires a loopback/RFC1918/"
                            "*.local base_url — this provider will be treated "
                            "as cloud (gated) until the URL is private")

    disc = data.get("discovery") or {}
    if disc.get("mode") not in (None, "static", "api", "merge"):
        errors.append("discovery.mode must be 'static', 'api', or 'merge'")

    prio = data.get("priority")
    if prio is not None:
        try:
            p = int(prio)
            if not (0 <= p <= 100):
                errors.append("priority must be 0–100")
        except (TypeError, ValueError):
            errors.append("priority must be an integer")

    return (not errors), errors, warnings


# ── Model resolver (GAP-4 fix, spec §7.2) ───────────────────────────────────

def _registry_providers():
    """Enabled provider descriptors from the live registry (lazy import)."""
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        return list(get_provider_registry().get_enabled_providers())
    except Exception:
        return []


def _cached_ids(prov: dict) -> set:
    """Model ids known for a provider: statics ∪ discovery cache. Never network."""
    ids = set(prov.get("models") or [])
    if (prov.get("discovery") or {}).get("mode") == "api":
        try:
            from agent_friday.services.model_discovery import cached_model_ids
            ids |= set(cached_model_ids(prov.get("name", "")))
        except Exception:
            pass
    return ids


def _live_ollama_has(prov: dict, model_id: str) -> bool:
    try:
        from agent_friday.routing.ollama_manager import get_manager
        mgr = get_manager(prov.get("base_url") or "http://localhost:11434")
        if not mgr.is_available():
            return False
        for m in (mgr.list_models() or []):
            name = m.get("name") if isinstance(m, dict) else str(m)
            if name == model_id:
                return True
    except Exception:
        pass
    return False


def _is_aggregator(prov: dict) -> bool:
    feats = prov.get("features") or {}
    return bool(feats.get("aggregator")) or prov.get("model_format") == "org/model"


def _sort_candidates(cands: list) -> list:
    """Deterministic precedence: effective-local first, then ascending
    priority, then registry declaration order (spec §7.2 rule 3)."""
    def key(pair):
        idx, prov = pair
        local = 0 if classification_of(prov) == CLASSIFICATION_LOCAL else 1
        return (local, int(prov.get("priority", 50)), idx)
    return [p for _, p in sorted(enumerate(cands), key=lambda pair: key(pair))]


def resolve_model(model_id, config=None):
    """Resolve a model id to (provider_descriptor, actual_model_name).

    Registry-first — this is the authority that replaces provider_family()
    name-guessing for dispatch decisions. Returns None when the id cannot be
    attributed to any enabled provider (callers then keep their legacy
    behavior rather than guessing).

    Resolution order (spec §7.2):
      1. explicit "provider::model"
      2. exact id match in exactly one enabled provider's catalog
         (statics ∪ discovery cache ∪ live Ollama daemon)
      3. exact match in several → local-classified first, then priority,
         then declaration order
      4. id contains "/" → enabled aggregators (cache hit first, else the
         highest-priority aggregator)
      5. id contains ":" with no "/" → the Ollama provider iff the daemon
         lists it
      6. provider_family() heuristics as last resort
    """
    mid = str(model_id or "").strip()
    if not mid:
        return None
    providers = _registry_providers()
    if not providers:
        return None
    by_name = {p.get("name"): p for p in providers}

    # 1. Explicit provider::model — "::" because both "/" and ":" occur INSIDE
    #    aggregator model ids.
    if "::" in mid:
        pname, _, actual = mid.partition("::")
        prov = by_name.get(pname.strip())
        if prov and actual.strip():
            return prov, actual.strip()
        return None

    # 2/3. Exact id match across enabled providers' known models.
    exact = []
    for prov in providers:
        if mid in _cached_ids(prov):
            exact.append(prov)
    # The live Ollama daemon is ground truth for local tags even when the
    # static list is stale (a custom-named local model MUST resolve local).
    for prov in providers:
        if adapter_of(prov) == ADAPTER_OLLAMA and prov not in exact \
                and _live_ollama_has(prov, mid):
            exact.append(prov)
    if exact:
        return _sort_candidates(exact)[0], mid

    # 4. org/model ids belong to aggregators — never to Ollama, no matter how
    #    many ":" variants they carry (the GAP-4 misroute).
    if "/" in mid:
        aggs = [p for p in providers if _is_aggregator(p)]
        if aggs:
            base = mid.split(":", 1)[0]
            with_hit = [p for p in aggs
                        if mid in _cached_ids(p) or base in _cached_ids(p)]
            pool = with_hit or aggs
            return _sort_candidates(pool)[0], mid
        return None

    # 5. Bare Ollama tag (name:tag, no "/") — local iff the daemon lists it.
    if ":" in mid:
        for prov in providers:
            if adapter_of(prov) == ADAPTER_OLLAMA and _live_ollama_has(prov, mid):
                return prov, mid

    # 6. Name heuristics — last resort, mapped through the registry so the
    #    result is still a real descriptor.
    try:
        from agent_friday.routing.model_router import provider_family
        fam = provider_family(mid)
    except Exception:
        fam = None
    fam_to_name = {"anthropic": "anthropic", "openai": "openai",
                   "gemini": "google-gemini", "local": "ollama-local"}
    prov = by_name.get(fam_to_name.get(fam or ""))
    if prov is not None:
        return prov, mid
    return None


def resolve_provider_name(model_id, config=None):
    """Convenience: the registry NAME that owns a model id, or None."""
    resolved = resolve_model(model_id, config=config)
    return resolved[0].get("name") if resolved else None
