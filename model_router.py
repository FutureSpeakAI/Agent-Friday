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

    def route(self, messages, task_context=None):
        """Decide which provider/model to use.

        Returns: {
            "provider": "local" | "cloud",
            "model": str,
            "task_type": str,
            "reason": str,
        }
        """
        ctx = task_context or {}
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
