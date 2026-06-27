"""
Model Router — upstream fork that decides whether a request goes to
Ollama (local) or Anthropic (cloud). Default mode is "cloud_only",
meaning this module is a no-op unless the user explicitly enables it.
"""

import threading
import time


# ── Unified classifier — single source of truth ────────────────────────────────
# model_router.py previously maintained a duplicate VAULT_KEYWORDS list.
# Both the router and the egress gate now import from sensitivity_classifier,
# so they agree on tier boundaries by construction rather than by maintenance.
try:
    from agent_friday.services.sensitivity_classifier import (
        classify as _sc_classify,
        Tier as _SCTier,
        TIER_3_KEYWORDS,
        TIER_2_KEYWORDS,
    )
    # Legacy-compatible helper: classify with PUBLIC default for routing use.
    def _vault_classify(text: str) -> int:
        return _sc_classify(text, default=_SCTier.PUBLIC)
except Exception:
    # Graceful degradation if the classifier module is not yet available.
    _vault_classify = None
    TIER_3_KEYWORDS = (
        "vault", "health record", "medical record",
        "financial", "finance", "encrypted", "sovereign", "ssn", "social security",
        "custody", "legal", "court",
    )
    TIER_2_KEYWORDS = ("contact", "phone number", "family", "partner")


class TaskType:
    SIMPLE = "simple"
    TOOL_USE = "tool_use"
    CODE = "code"
    RESEARCH = "research"
    VOICE = "voice"
    VAULT_ACCESS = "vault_access"


# The top-priority cloud model and the ordered fallback chain. Claude Opus 4.8
# is Anthropic's most capable currently-available model. When a cloud route does
# not name a model explicitly, we use DEFAULT_CLOUD_MODEL; downstream callers can
# walk CLOUD_MODEL_FALLBACK_CHAIN if the primary is unavailable.
# (Claude Fable 5 and Mythos 5 were pulled/recalled and are no longer offered.)
DEFAULT_CLOUD_MODEL = "claude-opus-4-8"
CLOUD_MODEL_FALLBACK_CHAIN = (
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
)


# Cost estimates per 1K tokens (USD) — used for savings tracking.
CLOUD_COST_PER_1K = {
    "claude-opus-4-8": 0.075,
    "claude-sonnet-4-6": 0.015,
    "claude-haiku-4-5-20251001": 0.001,
}


def provider_family(model_id):
    """Infer which provider family a model id belongs to, purely from its name.

    Lets a model the user picks in the UI (orchestrator/subagent) drive the
    backend dispatch without a separate provider toggle. Returns one of
    'anthropic' | 'openai' | 'gemini' | 'local', or None when unknown.
    """
    m = (model_id or "").lower().strip()
    if not m:
        return None
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt-", "gpt4", "gpt-4", "o1", "o3", "o4-", "chatgpt", "davinci")):
        return "openai"
    if m.startswith("gemini") or "nano-banana" in m or m.startswith("veo"):
        return "gemini"
    # Local voice models (Tier-1 Piper/Whisper, Tier-2 NeMo) are on-device.
    if m.startswith(("piper-", "whisper-", "nemo-", "nemotron-")):
        return "local"
    # Ollama tags carry a ":" (gemma4:latest) or a known local family prefix.
    if ":" in m or m.startswith(("gemma", "llama", "mistral", "qwen", "phi",
                                 "deepseek", "codellama", "mixtral")):
        return "local"
    return None


class CostTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._requests = []  # [{provider, model, tokens, cost, ts}]

    def record(self, provider, model, prompt_tokens=0, completion_tokens=0):
        total_tokens = prompt_tokens + completion_tokens
        if provider == "local":
            cost = 0.0
        else:
            rate = CLOUD_COST_PER_1K.get(model, 0.015)
            cost = (total_tokens / 1000) * rate
        entry = {
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost": round(cost, 6),
            "ts": time.time(),
        }
        with self._lock:
            self._requests.append(entry)
            if len(self._requests) > 10000:
                self._requests = self._requests[-5000:]

    def stats(self, since=None):
        cutoff = since or (time.time() - 86400)  # default: last 24h
        with self._lock:
            recent = [r for r in self._requests if r["ts"] >= cutoff]
        # Any non-local provider (cloud Anthropic, openai-compatible, …) counts
        # as "cloud" for the savings comparison.
        local_count = sum(1 for r in recent if r["provider"] == "local")
        cloud_count = sum(1 for r in recent if r["provider"] != "local")
        local_tokens = sum(r["total_tokens"] for r in recent if r["provider"] == "local")
        cloud_tokens = sum(r["total_tokens"] for r in recent if r["provider"] != "local")
        cloud_cost = sum(r["cost"] for r in recent if r["provider"] != "local")
        by_model = {}
        for r in recent:
            key = r["model"]
            if key not in by_model:
                by_model[key] = {"requests": 0, "tokens": 0, "cost": 0.0}
            by_model[key]["requests"] += 1
            by_model[key]["tokens"] += r["total_tokens"]
            by_model[key]["cost"] += r["cost"]
        return {
            "local_requests": local_count,
            "cloud_requests": cloud_count,
            "local_tokens": local_tokens,
            "cloud_tokens": cloud_tokens,
            "cloud_cost": round(cloud_cost, 4),
            "estimated_savings": 0.0,  # removed: blended-rate figure was inaccurate
            "by_model": by_model,
            "total_requests": local_count + cloud_count,
        }


class ModelRouter:
    def __init__(self, config=None):
        self.config = config or {}
        self.cost_tracker = CostTracker()

    def reload_config(self, config):
        self.config = config or {}

    @property
    def mode(self):
        return self.config.get("mode", "cloud_only")

    @property
    def fallback_to_cloud(self):
        return self.config.get("fallback_to_cloud", True)

    def classify_task(self, messages, has_tools=False, workspace=None):
        if not messages:
            return TaskType.SIMPLE
        last_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    last_msg = content
                break
        msg_len = len(last_msg)
        msg_lower = last_msg.lower()

        if has_tools:
            return TaskType.TOOL_USE
        if any(kw in msg_lower for kw in [
            "write code", "implement", "refactor", "debug", "function",
            "class ", "def ", "import ", "```", "algorithm",
        ]):
            return TaskType.CODE
        if any(kw in msg_lower for kw in [
            "research", "analyze", "compare", "deep dive", "explain in detail",
            "comprehensive", "thorough", "investigate",
        ]):
            return TaskType.RESEARCH
        if msg_len < 200 and not has_tools:
            return TaskType.SIMPLE
        return TaskType.RESEARCH

    # ── Vault access detection ──────────────────────────────────────────

    def needs_vault_access(self, messages, ctx):
        """True if this request will touch the Sovereign Vault.

        Triggers on vault-related tool definitions or vault keywords in the
        latest user message. Vault requests are force-routed to a local model.
        """
        ctx = ctx or {}
        if ctx.get("vault_access") is True:
            return True
        for t in (ctx.get("tool_names") or []):
            if "vault" in str(t).lower():
                return True
        last_msg = ""
        for m in reversed(messages or []):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    last_msg = content
                break
        if _vault_classify is not None:
            try:
                return _vault_classify(last_msg) > 1  # PRIVATE or SENSITIVE
            except Exception:
                pass
        low = last_msg.lower()
        return any(kw in low for kw in TIER_3_KEYWORDS) or \
               any(kw in low for kw in TIER_2_KEYWORDS)

    def _finalize(self, result, vault_access=False, warning=None, refuse=False):
        """Attach the downstream control flags the chat pipeline checks.

        is_local      — provider is Ollama (on-device)
        vault_allowed — raw vault content may be sent (True only for local)
        scrub_pii     — PII scrubber must run (True only for cloud)
        vault_access  — this request was flagged as vault-touching
        refuse        — caller must refuse outright (no model call)
        warning       — user-facing message to surface, if any
        """
        is_local = result.get("provider") == "local"
        result["is_local"] = is_local
        result["vault_allowed"] = is_local
        result["scrub_pii"] = not is_local
        result["vault_access"] = vault_access
        result["refuse"] = refuse
        result["warning"] = warning
        return result

    def _route_vault(self, ctx):
        """Force a vault-touching request onto a local model.

        Falls back per `vault_cloud_fallback` when no local model is available:
          "redact" → route cloud (vault content is gated/redacted downstream)
          "deny"   → refuse outright
          "warn"   → refuse and ask the user to enable a local model
        """
        from agent_friday.routing.ollama_manager import get_manager
        ollama = get_manager(self.config.get("ollama_url", "http://localhost:11434"))
        models = ollama.list_models() if ollama.is_available() else []

        if models:
            local_model = self._pick_local_model(models, TaskType.VAULT_ACCESS, self.mode) \
                or models[0]["name"]
            return self._finalize({
                "provider": "local",
                "model": local_model,
                "task_type": TaskType.VAULT_ACCESS,
                "reason": "Vault access — force-routed to local model",
            }, vault_access=True)

        warning = (
            "This request needs vault access which requires a local model. "
            "Please install Ollama or switch to local routing mode."
        )
        fallback = self.config.get("vault_cloud_fallback", "redact")
        if fallback in ("deny", "warn"):
            return self._finalize({
                "provider": "cloud",
                "model": self.config.get("default_cloud_model", DEFAULT_CLOUD_MODEL),
                "task_type": TaskType.VAULT_ACCESS,
                "reason": f"Vault access required but no local model ({fallback})",
            }, vault_access=True, warning=warning, refuse=True)

        # "redact" — proceed on cloud, but vault content is gated downstream.
        return self._finalize({
            "provider": "cloud",
            "model": self.config.get("default_cloud_model", DEFAULT_CLOUD_MODEL),
            "task_type": TaskType.VAULT_ACCESS,
            "reason": "Vault access required but no local model — cloud with redaction",
        }, vault_access=True, warning=warning)

    def route(self, messages, task_context=None):
        """Decide which provider/model to use.

        Returns a dict with provider/model/task_type/reason plus the control
        flags added by `_finalize` (is_local, vault_allowed, scrub_pii,
        vault_access, refuse, warning).

        Vault detection runs first and takes precedence over the routing mode —
        even in cloud_only mode a vault request is force-routed local or refused,
        so vault data never reaches the cloud.
        """
        ctx = task_context or {}

        if self.needs_vault_access(messages, ctx):
            return self._route_vault(ctx)

        result = self._apply_cloud_provider(self._route_basic(messages, ctx), ctx)
        return self._finalize(result, vault_access=False)

    def _is_registry_local(self, model_id: str) -> bool:
        """True if model_id is explicitly listed under a local-type provider
        (type 'ollama', 'local-voice', or 'nemo-local') in the provider registry.

        This catches custom-named Ollama models (e.g. 'claude-x:latest') that
        would otherwise be misidentified as cloud by the name-heuristic in
        provider_family(), bypassing the egress gate.
        """
        try:
            from agent_friday.services.provider_registry import get_provider_registry
            for p in get_provider_registry():
                if p.get("type") in ("ollama", "local-voice", "nemo-local"):
                    if model_id in (p.get("models") or []):
                        return True
        except Exception:
            pass
        # Also honour an explicit local-model allowlist in settings.
        for m in (self.config.get("local_model_names") or []):
            if m == model_id:
                return True
        return False

    def _apply_cloud_provider(self, result, ctx):
        """Retag a 'cloud' decision as 'openai' when an OpenAI-compatible cloud
        provider is configured, so the server dispatches to _call_openai.

        Covers OpenRouter (hundreds of models) and any /v1 base_url endpoint
        (Together, Groq, Fireworks, vLLM, LM Studio, OpenAI itself). is_local
        stays False in _finalize, so PII scrubbing and vault gating still apply
        exactly as they do for Anthropic. Vault routing is intentionally left on
        the trusted Anthropic 'cloud' path (handled in _route_vault).
        """
        if result.get("provider") != "cloud":
            return result
        model = str(result.get("model") or "")
        cp = str(self.config.get("cloud_provider") or "anthropic").lower()
        fam = provider_family(model)

        # Registry check: if the model is explicitly listed under an Ollama (or
        # other local-type) provider, it's local regardless of its name — an
        # Ollama model named "claude-x" must not bypass the egress gate.
        if self._is_registry_local(model):
            result["provider"] = "local"
            result["reason"] = (result.get("reason") or "") + " (local per registry)"
            return result

        # The model picker is authoritative: a selected model id that clearly
        # belongs to a local family (gemma4:…, llama3.1:…) routes on-device even
        # in cloud_only mode — the user explicitly chose a local brain. Safe here
        # because vault detection already ran (this is a non-vault request).
        if fam == "local":
            result["provider"] = "local"
            result["reason"] = (result.get("reason") or "") + " (local model selected)"
            return result

        # An OpenAI-family model id (gpt-4o, o3, …) — or an explicitly configured
        # OpenAI-compatible cloud_provider (OpenRouter/Together/Groq/vLLM/etc.) —
        # dispatches to _call_openai. is_local stays False so PII scrubbing and
        # vault gating apply exactly as for Anthropic.
        explicit_oai = cp in ("openai", "openrouter", "openai_compatible", "compatible")
        if fam == "openai" or explicit_oai:
            result["provider"] = "openai"
            result["model"] = (
                ctx.get("openai_model")
                or (model if fam == "openai" else None)
                or self.config.get("openai_model")
                or model
            )
            result["reason"] = (result.get("reason") or "") + " (openai-compatible)"
        return result

    def _route_basic(self, messages, ctx):
        """Original (non-vault) routing decision. Returns a bare result dict."""
        mode = self.mode
        has_tools = bool(ctx.get("has_tools"))
        workspace = ctx.get("workspace", "")

        if mode == "cloud_only":
            model = ctx.get("cloud_model") or self.config.get(
                "default_cloud_model", DEFAULT_CLOUD_MODEL
            )
            return {
                "provider": "cloud",
                "model": model,
                "task_type": "cloud_only",
                "reason": "Routing mode is cloud_only",
            }

        task_type = self.classify_task(messages, has_tools=has_tools, workspace=workspace)

        overrides = self.config.get("task_overrides", {})
        if task_type in overrides:
            override = overrides[task_type]
            return {
                "provider": override.get("provider", "cloud"),
                "model": override.get("model", DEFAULT_CLOUD_MODEL),
                "task_type": task_type,
                "reason": f"User override for {task_type}",
            }

        if task_type == TaskType.VOICE:
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model", DEFAULT_CLOUD_MODEL),
                "task_type": task_type,
                "reason": "Voice stays on cloud/Gemini pipeline",
            }

        if task_type == TaskType.TOOL_USE:
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model") or self.config.get(
                    "default_cloud_model", DEFAULT_CLOUD_MODEL
                ),
                "task_type": task_type,
                "reason": "Tool use requires cloud model",
            }

        from agent_friday.routing.ollama_manager import get_manager
        ollama = get_manager(self.config.get("ollama_url", "http://localhost:11434"))

        if not ollama.is_available():
            if self.fallback_to_cloud:
                return {
                    "provider": "cloud",
                    "model": ctx.get("cloud_model") or self.config.get(
                        "default_cloud_model", DEFAULT_CLOUD_MODEL
                    ),
                    "task_type": task_type,
                    "reason": "Ollama not available, falling back to cloud",
                }
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model", DEFAULT_CLOUD_MODEL),
                "task_type": task_type,
                "reason": "Ollama not available",
            }

        models = ollama.list_models()
        if not models:
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model") or self.config.get(
                    "default_cloud_model", DEFAULT_CLOUD_MODEL
                ),
                "task_type": task_type,
                "reason": "No local models installed",
            }

        local_model = self._pick_local_model(models, task_type, mode)
        if local_model:
            return {
                "provider": "local",
                "model": local_model,
                "task_type": task_type,
                "reason": f"Routing {task_type} to local model",
            }

        if self.fallback_to_cloud:
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model") or self.config.get(
                    "default_cloud_model", DEFAULT_CLOUD_MODEL
                ),
                "task_type": task_type,
                "reason": "No suitable local model, falling back to cloud",
            }

        return {
            "provider": "local",
            "model": models[0]["name"],
            "task_type": task_type,
            "reason": "local_only mode, using first available model",
        }

    def _pick_local_model(self, models, task_type, mode):
        model_names = [m["name"] for m in models]
        sizes = {m["name"]: m.get("size_gb", 0) for m in models}

        # A user-configured default local model always wins when it's installed,
        # regardless of task type. This is the on-device model the user actually
        # chose (settings.model_routing.local_model, default "gemma4:latest").
        pref = self.config.get("local_model")
        if pref and pref in model_names:
            return pref

        if task_type in (TaskType.CODE, TaskType.RESEARCH):
            for name in sorted(model_names, key=lambda n: -sizes.get(n, 0)):
                if sizes.get(name, 0) >= 4:
                    return name
        if task_type == TaskType.SIMPLE:
            for name in sorted(model_names, key=lambda n: sizes.get(n, 0)):
                return name

        if mode == "local_only" and model_names:
            return model_names[0]
        if mode in ("local_preferred", "smart") and model_names:
            return model_names[0]
        return None

    def get_stats(self):
        return self.cost_tracker.stats()


def anthropic_to_openai_tools(claude_tools):
    """Convert Anthropic tool definitions to OpenAI-compatible format."""
    if not claude_tools:
        return None
    oai_tools = []
    for tool in claude_tools:
        oai_tools.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        })
    return oai_tools


def openai_response_to_friday(oai_response, model_name):
    """Normalize an OpenAI-format response to match what _call_claude_agent returns."""
    choices = oai_response.get("choices", [])
    if not choices:
        return "", []
    msg = choices[0].get("message", {})
    text = msg.get("content", "") or ""
    return text.strip(), []


_router_instance = None
_router_lock = threading.Lock()


def get_router(config=None):
    global _router_instance
    if _router_instance is None:
        with _router_lock:
            if _router_instance is None:
                _router_instance = ModelRouter(config)
    if config is not None:
        _router_instance.reload_config(config)
    return _router_instance
