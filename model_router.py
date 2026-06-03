"""
Model Router — upstream fork that decides whether a request goes to
Ollama (local) or Anthropic (cloud). Default mode is "cloud_only",
meaning this module is a no-op unless the user explicitly enables it.
"""

import threading
import time


class TaskType:
    SIMPLE = "simple"
    TOOL_USE = "tool_use"
    CODE = "code"
    RESEARCH = "research"
    VOICE = "voice"
    VAULT_ACCESS = "vault_access"


# Requests that touch the Sovereign Vault must run on a local model. These
# keywords (and any vault-related tool definitions) flag a request as needing
# vault access, which force-routes it to Ollama regardless of routing mode.
VAULT_KEYWORDS = (
    "vault", "health record", "medical record", "ofw", "our family wizard",
    "financial", "finance", "encrypted", "sovereign", "ssn", "social security",
    "custody", "co-parent", "coparent",
)


# Cost estimates per 1K tokens (USD) — used for savings tracking.
CLOUD_COST_PER_1K = {
    "claude-opus-4-7": 0.075,
    "claude-sonnet-4-6": 0.015,
    "claude-haiku-4-5-20251001": 0.001,
}


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
        local_count = sum(1 for r in recent if r["provider"] == "local")
        cloud_count = sum(1 for r in recent if r["provider"] == "cloud")
        local_tokens = sum(r["total_tokens"] for r in recent if r["provider"] == "local")
        cloud_tokens = sum(r["total_tokens"] for r in recent if r["provider"] == "cloud")
        cloud_cost = sum(r["cost"] for r in recent if r["provider"] == "cloud")
        saved = sum(
            (r["total_tokens"] / 1000) * 0.015
            for r in recent if r["provider"] == "local"
        )
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
            "estimated_savings": round(saved, 4),
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
        low = last_msg.lower()
        return any(kw in low for kw in VAULT_KEYWORDS)

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
        from ollama_manager import get_manager
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
                "model": self.config.get("default_cloud_model", "claude-opus-4-7"),
                "task_type": TaskType.VAULT_ACCESS,
                "reason": f"Vault access required but no local model ({fallback})",
            }, vault_access=True, warning=warning, refuse=True)

        # "redact" — proceed on cloud, but vault content is gated downstream.
        return self._finalize({
            "provider": "cloud",
            "model": self.config.get("default_cloud_model", "claude-opus-4-7"),
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

        return self._finalize(self._route_basic(messages, ctx), vault_access=False)

    def _route_basic(self, messages, ctx):
        """Original (non-vault) routing decision. Returns a bare result dict."""
        mode = self.mode
        has_tools = bool(ctx.get("has_tools"))
        workspace = ctx.get("workspace", "")

        if mode == "cloud_only":
            model = ctx.get("cloud_model") or self.config.get(
                "default_cloud_model", "claude-opus-4-7"
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
                "model": override.get("model", "claude-opus-4-7"),
                "task_type": task_type,
                "reason": f"User override for {task_type}",
            }

        if task_type == TaskType.VOICE:
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model", "claude-opus-4-7"),
                "task_type": task_type,
                "reason": "Voice stays on cloud/Gemini pipeline",
            }

        if task_type == TaskType.TOOL_USE:
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model") or self.config.get(
                    "default_cloud_model", "claude-opus-4-7"
                ),
                "task_type": task_type,
                "reason": "Tool use requires cloud model",
            }

        from ollama_manager import get_manager
        ollama = get_manager(self.config.get("ollama_url", "http://localhost:11434"))

        if not ollama.is_available():
            if self.fallback_to_cloud:
                return {
                    "provider": "cloud",
                    "model": ctx.get("cloud_model") or self.config.get(
                        "default_cloud_model", "claude-opus-4-7"
                    ),
                    "task_type": task_type,
                    "reason": "Ollama not available, falling back to cloud",
                }
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model", "claude-opus-4-7"),
                "task_type": task_type,
                "reason": "Ollama not available",
            }

        models = ollama.list_models()
        if not models:
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model") or self.config.get(
                    "default_cloud_model", "claude-opus-4-7"
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
                    "default_cloud_model", "claude-opus-4-7"
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
