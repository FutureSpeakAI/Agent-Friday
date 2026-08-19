"""
routing/model_router.py — ROUTING LAYER (WHERE to send a request).

This module decides which provider (Anthropic / Ollama / OpenAI-compatible)
and which model should handle a given request, based on:
  • The user's model_routing.mode setting (cloud_only / smart / local_preferred / local_only)
  • The request's vault tier (TIER_2/3 → local only)
  • The current network state (offline → local only)
  • Task-type overrides in model_routing.task_overrides

It does NOT execute any model calls.  Execution happens in:
  services/model_router.py — _call_claude, _call_ollama, _call_openai, _generate_text

Canonical imports for callers::

    from agent_friday.routing.model_router import get_router, provider_family
    from agent_friday.routing.model_router import anthropic_to_openai_tools

Do NOT import `get_router` or `provider_family` from both modules — prefer
this file (routing/) when you need routing decisions; services/model_router.py
re-exports provider_family for backwards compatibility but the canonical source
is always here.
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


# The top-priority cloud model and the ordered fallback chain. Claude Sonnet 5
# is Anthropic's flagship frontier model (best cost/quality ratio). When a cloud
# route does not name a model explicitly, we use DEFAULT_CLOUD_MODEL; downstream
# callers can walk CLOUD_MODEL_FALLBACK_CHAIN if the primary is unavailable.
DEFAULT_CLOUD_MODEL = "claude-sonnet-5"
CLOUD_MODEL_FALLBACK_CHAIN = (
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-opus-5",
    "claude-haiku-4-5-20251001",
)


# Cost estimates per 1K tokens (USD) — used for savings tracking.
CLOUD_COST_PER_1K = {
    "claude-sonnet-5": 0.030,
    "claude-fable-5": 0.030,
    "claude-opus-5": 0.075,
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
    # Ollama tags carry a ":" (gemma4:latest). No first-party Anthropic/OpenAI/
    # Gemini id contains one, and this must outrank the cloud prefix checks: a
    # locally installed "claude-x:latest" or "gemini-tuned:7b" that pattern-
    # matched a cloud family would dispatch (and egress the prompt) to the
    # wrong provider. Caveat: aggregator ids (OpenRouter "org/model:tag") also
    # land here — those are routed by the explicit model_routing.cloud_provider
    # setting at dispatch time, never by family inference.
    if ":" in m:
        return "local"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt-", "gpt4", "gpt-4", "o1", "o3", "o4-", "chatgpt", "davinci")):
        return "openai"
    if m.startswith("gemini") or "nano-banana" in m or m.startswith("veo"):
        return "gemini"
    # Local voice models (Tier-1 Piper/Whisper, Tier-2 NeMo) are on-device.
    if m.startswith(("piper-", "whisper-", "nemo-", "nemotron-")):
        return "local"
    # Known local model family prefixes (untagged Ollama ids).
    if m.startswith(("gemma", "llama", "mistral", "qwen", "phi",
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

    def _chosen_seat(self, ctx=None):
        """The model bound to this turn's seat, or None.

        Precedence, per docs/design/conversations-and-concurrency.md §3.2:

            vault-forced local  >  per-turn route_mode  >  CONVERSATION seat
                                >  global capability_routing.reasoning

        The vault route and route_mode are handled above this in `route()` and
        are unchanged. What is new is the middle term: a conversation may bind
        its own model, and when it has, that wins over the global default.
        A conversation with no binding follows the global default resolved at
        DISPATCH time — so a user who never opens a second chat sees exactly
        today's behaviour.

        `capability_routing.reasoning` is what the model picker writes, what
        Settings -> Intelligence displays, and what capability_router.resolve()
        reads. Until now the ROUTER consulted it in exactly one branch —
        unattended tool work — so an interactive turn never looked at it.

        That is why neither control appeared to work on 2026-08-18: the write
        was correct, the setting persisted, the UI showed the new model, and
        interactive dispatch went to the cloud regardless, because
        `_route_basic` ended TOOL_USE with "Tool use requires cloud model".
        Selecting gemma4:e4b and being answered by claude-sonnet-4-6 is not a
        save that failed; it is a save nothing read.

        An explicit binding is an instruction, not a hint. The classifier
        decides when he has expressed no preference.
        """
        seat = (ctx or {}).get("conversation_seat")
        if isinstance(seat, dict) and (seat.get("model") or "").strip():
            return ((seat.get("model") or "").strip(),
                    (seat.get("provider") or "").strip().lower())
        try:
            from agent_friday.core import _load_settings
            cr = (_load_settings() or {}).get("capability_routing") or {}
            entry = cr.get("reasoning")
            if isinstance(entry, dict):
                model = (entry.get("model") or "").strip()
                # The FACTORY value is not a binding — it is the absence of
                # one. This method's own rule is that an explicit binding is an
                # instruction and the classifier decides when no preference was
                # expressed; an untouched default is the second case, and
                # treating it as the first made `local_preferred` unreachable
                # for interactive turns (settings ship with a cloud reasoning
                # seat, so the seat always "won"). Stephen runs local_preferred.
                #
                # Cost, stated plainly: if he explicitly picks the same model
                # the factory ships, that choice is indistinguishable from
                # never having chosen, and local_preferred will route local.
                # The error runs toward keeping his turn on this machine.
                if model and model != self._factory_reasoning_model():
                    return model, (entry.get("provider") or "").strip().lower()
        except Exception:
            pass
        return None, None

    @staticmethod
    def _factory_reasoning_model():
        """The reasoning model DEFAULT_SETTINGS ships with, or None."""
        try:
            from agent_friday.core import DEFAULT_SETTINGS
            return ((DEFAULT_SETTINGS.get("capability_routing") or {})
                    .get("reasoning") or {}).get("model")
        except Exception:
            return None

    # Providers that mean "this runs on this machine".
    _LOCAL_PROVIDERS = {"ollama-local", "llama-cpp-local", "local", "local-comfyui",
                        "local-voice-lite", "arbiter-local", "nvidia-nemo"}

    def _route_chosen_seat(self, model_id, task_type, provider=None):
        """Route to the seat he named.

        The candidate probe is ADVISORY, not decisive. `_local_candidates()`
        asks the Ollama daemon over HTTP, and on 2026-08-18 that call timed out
        while the daemon was busy loading the previous turn's model — so a seat
        he had explicitly bound to qwen3.5:9b was declared unserveable and the
        turn was answered by claude-sonnet-5 instead.

        That is worse than an ignored preference. A transient local hiccup
        silently moved his private conversation to Anthropic. When the binding
        itself names a local provider, the seat is local, and a local failure
        must surface as a local failure rather than as a quiet trip to the
        cloud.
        """
        if not model_id:
            return None
        if provider in self._LOCAL_PROVIDERS:
            return {
                "provider": "local",
                "model": model_id,
                "task_type": task_type,
                "reason": "the model seat he chose (capability_routing.reasoning)",
            }
        names = {m["name"] for m in self._local_candidates()}
        if model_id in names or self._is_registry_local(model_id):
            return {
                "provider": "local",
                "model": model_id,
                "task_type": task_type,
                "reason": "the model seat he chose (capability_routing.reasoning)",
            }
        # A cloud id he chose is equally a choice — carry it rather than
        # collapsing to the Anthropic default further down the stack.
        if model_id.startswith(("claude", "gpt", "gemini")) or (
                "/" in model_id and not model_id.startswith("hf.co/")):
            return {
                "provider": "cloud",
                "model": model_id,
                "task_type": task_type,
                "reason": "the model seat he chose (capability_routing.reasoning)",
            }

        # He named a model nothing here can serve. Returning None used to drop
        # through to the ordinary cloud branch, which is a SILENT SUBSTITUTION —
        # the same defect being fixed, one layer down. On 2026-08-18 gemma4:12b
        # and gemma4:e4b left the Ollama daemon mid-session (moved to
        # llama-server), and a seat still pointing at them answered as Claude
        # without a word. Route cloud, because refusing the turn helps nobody,
        # but say plainly that this is not what he asked for; `reason` reaches
        # the badge and the work log.
        return {
            "provider": "cloud",
            "model": None,
            "task_type": task_type,
            "substituted_for": model_id,
            "reason": ("you chose %s, but nothing on this machine can serve it "
                       "right now — answered in the cloud instead" % model_id),
        }

    def _local_candidates(self):
        """Local generation models Friday can actually serve right now.

        Her OWN store first, the Ollama daemon second for anything not yet
        imported. Extracted so the vault route and the unattended-tool route
        cannot drift apart on the question of whether a local model exists —
        they disagreed once already, and the disagreement sent vault content to
        the cloud.
        """
        models = []
        try:
            from agent_friday.services import model_store as _ms
            for mid, rec in sorted(_ms.available().items()):
                if rec.get("can_generate"):
                    models.append({"name": mid,
                                   "size_gb": (rec.get("size_bytes") or 0)
                                   / 1024 ** 3})
        except Exception:
            pass
        try:
            from agent_friday.routing.ollama_manager import get_manager
            ollama = get_manager(
                self.config.get("ollama_url", "http://localhost:11434"))
            have = {m["name"] for m in models}
            if ollama.is_available():
                models += [m for m in ollama.list_models()
                           if m.get("name") not in have]
        except Exception:
            pass
        return models

    def _route_vault(self, ctx):
        """Force a vault-touching request onto a local model.

        Falls back per `vault_cloud_fallback` when no local model is available:
          "redact" → route cloud (vault content is gated/redacted downstream)
          "deny"   → refuse outright
          "warn"   → refuse and ask the user to enable a local model
        """
        # Friday's OWN seats first, then the Ollama daemon.
        #
        # This asked only Ollama, which became a sovereignty defect the moment
        # the residency layer started serving seats as processes Friday owns:
        # with the daemon stopped, `list_models()` returns [] on a machine
        # holding four loaded local models, "no local model available" is
        # false, and vault-tier content falls through to
        # `vault_cloud_fallback` — cloud with redaction. Redacted is not local.
        # A rule that exists so private material never leaves the machine must
        # not be defeated by asking the wrong component whether a local model
        # exists.
        #
        # Verified 2026-08-16 with the daemon stopped: this route returned
        # "Vault access required but no local model — cloud with redaction"
        # while gemma4:12b and gemma4:e2b were resident and answering.
        models = self._local_candidates()

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
            "No model is available in Friday's own store and the Ollama "
            "daemon is not reachable either."
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
        # Say which seat won and why. Three sessions have now debugged "the
        # model I picked is not the model that answered" without this line.
        try:
            _m, _p = self._chosen_seat(ctx)
            _src = ("conversation" if isinstance(ctx.get("conversation_seat"), dict)
                    and (ctx.get("conversation_seat") or {}).get("model")
                    else "global default")
            print("  [ROUTER] chose %s/%s | seat bound: %s (%s, from the %s) | %s"
                  % (result.get("provider"), result.get("model"), _m, _p, _src,
                     result.get("reason")))
        except Exception:
            pass
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
            for p in get_provider_registry().list_providers():
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
        """Retag a 'cloud' decision by RESOLVING the model to the provider that
        actually owns it (registry-first, GAP-4 fix), so the server dispatches
        to the right executor.

        Resolution goes through routing/provider_descriptors.resolve_model():
        aggregator ids (`org/model[:variant]`) route to OpenRouter/HuggingFace,
        Ollama tags to the local daemon, `claude-*` to Anthropic, and any model
        listed by exactly one enabled provider to that provider — no more
        substring guessing. The decision gains `provider_name` (the registry
        name, e.g. "openrouter"/"groq") while `provider` keeps the legacy enum
        {cloud, openai, local} for un-migrated callers.

        The legacy single-slot behavior (settings.model_routing.cloud_provider
        = openai/openrouter/compatible + openai_model) is preserved for ids the
        resolver attributes to Anthropic or cannot attribute at all. is_local
        stays False in _finalize for cloud results, so PII scrubbing and vault
        gating apply exactly as before. Vault routing is intentionally left on
        _route_vault.
        """
        if result.get("provider") != "cloud":
            return result
        model = str(result.get("model") or "")
        cp = str(self.config.get("cloud_provider") or "anthropic").lower()
        fam = provider_family(model)
        explicit_oai = cp in ("openai", "openrouter", "openai_compatible", "compatible")

        def _legacy_openai_retag():
            result["provider"] = "openai"
            result["model"] = (
                ctx.get("openai_model")
                or (model if fam == "openai" else None)
                or self.config.get("openai_model")
                or model
            )
            result["reason"] = (result.get("reason") or "") + " (openai-compatible)"
            return result

        # Registry check: if the model is explicitly listed under an Ollama (or
        # other local-type) provider, it's local regardless of its name — an
        # Ollama model named "claude-x" must not bypass the egress gate.
        if self._is_registry_local(model):
            result["provider"] = "local"
            result["reason"] = (result.get("reason") or "") + " (local per registry)"
            return result

        # ── Registry-first resolution (GAP-4 fix) ──────────────────────────
        resolved = None
        try:
            from agent_friday.routing.provider_descriptors import (
                resolve_model, adapter_of, classification_of)
            resolved = resolve_model(model, config=self.config)
        except Exception:
            resolved = None
        if resolved is not None:
            prov, actual_model = resolved
            pname = prov.get("name") or ""
            adapter = adapter_of(prov)
            if adapter in ("ollama",) or classification_of(prov) == "local":
                # Resolved to an on-device provider — the picker chose a local
                # brain. Safe: vault detection already ran (non-vault request).
                result["provider"] = "local"
                result["model"] = actual_model
                result["provider_name"] = pname
                result["reason"] = (result.get("reason") or "") + \
                    f" (local per resolver: {pname})"
                return result
            if explicit_oai:
                # The user explicitly pointed the cloud slot at an
                # OpenAI-compatible endpoint (legacy single-slot config, kept
                # working per the migration contract) — that wins for every
                # non-local result, exactly as before this refactor.
                return _legacy_openai_retag()
            if adapter == "anthropic":
                result["provider_name"] = pname
                return result
            if adapter == "openai-compatible":
                # Any OpenAI-compatible provider — OpenAI itself, OpenRouter,
                # HuggingFace, Groq, a LAN vLLM… — dispatches to _call_openai
                # against ITS OWN base_url/credentials (multi-provider, GAP-3).
                result["provider"] = "openai"
                result["model"] = actual_model
                result["provider_name"] = pname
                result["reason"] = (result.get("reason") or "") + \
                    f" (openai-compatible: {pname})"
                return result
            # google + anything else: no text dispatch yet — fall through to
            # the legacy handling below.

        # ── Legacy heuristics (unresolved ids) ─────────────────────────────
        # The model picker is authoritative: a selected model id that clearly
        # belongs to a local family (gemma4:…, llama3.1:…) routes on-device even
        # in cloud_only mode — the user explicitly chose a local brain.
        if fam == "local":
            result["provider"] = "local"
            result["reason"] = (result.get("reason") or "") + " (local model selected)"
            return result

        # An OpenAI-family model id (gpt-4o, o3, …) — or an explicitly configured
        # OpenAI-compatible cloud_provider (OpenRouter/Together/Groq/vLLM/etc.) —
        # dispatches to _call_openai.
        if fam == "openai" or explicit_oai:
            return _legacy_openai_retag()
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

        # His explicit seat is consulted BEFORE the speed/size heuristics for
        # every ordinary class. Voice keeps its own pipeline and the vault
        # route has already run and taken precedence above; a task_override is
        # a deliberate per-class rule and still wins over a general seat.
        overrides = self.config.get("task_overrides", {})
        if task_type not in overrides and task_type != TaskType.VOICE:
            _m, _p = self._chosen_seat(ctx)
            chosen = self._route_chosen_seat(_m, task_type, _p)
            if chosen and task_type != TaskType.TOOL_USE:
                if chosen.get("model") is None:
                    chosen["model"] = ctx.get("cloud_model") or self.config.get(
                        "default_cloud_model", DEFAULT_CLOUD_MODEL)
                return chosen
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
            # Unattended work prefers a local seat.
            #
            # This rule is from 2026-06-27, when local tool calling did not
            # work. It does now: 15/15 dependent five-call chains across e2b,
            # e4b and 12b, and 6/6 with the Ollama daemon stopped entirely.
            # Meanwhile every hourly heartbeat was spending ~42,654 input
            # tokens on a cloud model to produce ~130 output tokens — about a
            # million input tokens a day to read Stephen's own calendar and
            # inbox, which is exactly the private material that should not be
            # leaving the machine in the first place.
            #
            # Scoped to SCHEDULED and BACKGROUND work on Stephen's decision
            # (2026-08-16). Interactive chat keeps today's cloud behaviour,
            # because that is where he would feel the speed difference and he
            # did not ask for that trade.
            if ctx.get("is_background_task") or ctx.get("scheduled"):
                local = self._local_candidates()
                if local:
                    # The BRAIN, not the fastest thing that fits. Stephen named
                    # the 12b ("Can't Gemma 4:12b handle that?") and he is
                    # right about the seat: a heartbeat reads a calendar and an
                    # inbox and has to decide what is worth telling him, which
                    # is judgement work, not reflex. _pick_local_model
                    # optimises for speed and chose the 2B sidekick.
                    names = {m["name"] for m in local}
                    chosen = None
                    try:
                        from agent_friday.core import _load_settings as _ls
                        cr = (_ls() or {}).get("capability_routing") or {}
                        want = (cr.get("reasoning") or {}).get("model")
                        if want in names:
                            chosen = want
                    except Exception:
                        pass
                    chosen = chosen or self._pick_local_model(
                        local, task_type, self.mode) or local[0]["name"]
                    return {
                        "provider": "local",
                        "model": chosen,
                        "task_type": task_type,
                        "reason": "Unattended tool work — kept on a local seat",
                    }
            # An explicit seat wins for INTERACTIVE work too.
            #
            # The 2026-08-16 scoping ("interactive chat keeps today's cloud
            # behaviour") was about not silently changing the speed trade he
            # had not asked for. Choosing a model in the picker IS asking for
            # it, and the whole point of the redesigned picker is that the
            # choice takes effect. Leaving this branch cloud-only meant every
            # selection he made was cosmetic.
            _m, _p = self._chosen_seat(ctx)
            chosen = self._route_chosen_seat(_m, task_type, _p)
            if chosen:
                if chosen.get("model") is None:
                    chosen["model"] = ctx.get("cloud_model") or self.config.get(
                        "default_cloud_model", DEFAULT_CLOUD_MODEL)
                return chosen
            return {
                "provider": "cloud",
                "model": ctx.get("cloud_model") or self.config.get(
                    "default_cloud_model", DEFAULT_CLOUD_MODEL
                ),
                "task_type": task_type,
                "reason": "Tool use, and no seat was explicitly chosen",
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

        # D6: .fridayhints `preferred_model` finally reaches dispatch. It was
        # parsed, merged, and served over HTTP while nothing read it.
        local_model = self._pick_local_model(
            models, task_type, mode, preferred=ctx.get("preferred_model"))
        if local_model:
            out = {
                "provider": "local",
                "model": local_model,
                "task_type": task_type,
                "reason": f"Routing {task_type} to local model",
            }
            # An override that could not be honoured travels WITH the result,
            # so the caller can say why it did not get what it asked for.
            if self.last_local_refusal:
                out["override_refused"] = self.last_local_refusal
            return out

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

    def _vram_fit(self, model_names):
        """Which of these models actually fit this machine's GPU, and by how much.

        Returns (fits, budget_mib, needs) — `fits` is None when the hardware or
        the measurements are unknown, which means "do not filter", not "nothing
        fits". Refusing to route on missing data would be worse than the size
        heuristic it replaces.
        """
        try:
            from agent_friday.services import hardware_profile as hwp
            from agent_friday.services import residency_catalog as rc
            from agent_friday.services import residency_policy as rp
            profile = hwp.get()
            budgets = rp.gpu_budgets(profile)
            if not budgets:
                return None, 0, {}
            budget = max(b["available_mib"] for b in budgets)
            fp = rc.profile_fingerprint(profile)
            needs, fits = {}, []
            for n in model_names:
                v = rc.vram_at(n, fp, 16384)
                if v is None:
                    continue                  # unmeasured: neither in nor out
                needs[n] = v
                if v <= budget:
                    fits.append(n)
            if not needs:
                return None, budget, {}
            return fits, budget, needs
        except Exception:
            return None, 0, {}

    def _pick_local_model(self, models, task_type, mode, preferred=None):
        """Choose the on-device model, with a VRAM check rather than a size sort.

        The heuristic this replaces ranked candidates by ARTIFACT SIZE, which on
        the reference instance is not merely imprecise but inverted: gemma4:e2b
        is a 7.2 GB artifact occupying 1763 MiB of VRAM, while gemma4:e4b is
        9.6 GB occupying 3081 MiB. Worse, when the configured local_model was
        not installed — as `gemma3:4b` is not — the CODE/RESEARCH branch fell
        through to "largest artifact wins", selecting the one model on the box
        guaranteed to spill onto the CPU.

        Selection refusals are recorded on `self.last_local_refusal` so a caller
        can surface WHY it did not get what it asked for. A silent substitution
        is how `preferred_model` and `capability_routing.embedding.model`
        became settings people could change while nothing happened.
        """
        self.last_local_refusal = None
        model_names = [m["name"] for m in models]
        if not model_names:
            return None
        sizes = {m["name"]: m.get("size_gb", 0) for m in models}
        fits, budget, needs = self._vram_fit(model_names)

        def _ok(name):
            """Fits the card, or is unmeasured (never refuse on missing data)."""
            return fits is None or name not in needs or name in fits

        def _refuse(name, why):
            self.last_local_refusal = {
                "requested": name, "rule_id": "R3", "explanation": why}

        # 1. An explicit per-workspace override (.fridayhints preferred_model)
        #    outranks the global setting. D6: wired, not silently ignored.
        for src, pref in (("preferred_model", preferred),
                          ("local_model", self.config.get("local_model"))):
            if not pref:
                continue
            if pref not in model_names:
                _refuse(pref, "%s names %r, which is not installed; installed: "
                              "%s" % (src, pref, ", ".join(sorted(model_names))))
                continue
            if not _ok(pref):
                _refuse(pref, "%s names %r, which needs %d MiB but only %d MiB "
                              "of GPU budget is available"
                              % (src, pref, needs.get(pref, 0), budget))
                continue
            return pref

        # 2. Prefer what the residency policy pinned as the interactive seat.
        try:
            from agent_friday.services import hardware_profile as hwp
            from agent_friday.services import residency_catalog as rc
            from agent_friday.services import residency_policy as rp
            profile = hwp.get()
            plan = rp.plan(profile, rc.installed_entries(profile))
            seat = (plan.get("seats") or {}).get("interactive_brain")
            if seat and seat.get("model_id") in model_names:
                return seat["model_id"]
        except Exception:
            pass

        # 3. Fall back to the old shape, but only over models that FIT.
        pool = [n for n in model_names if _ok(n)] or model_names
        if task_type in (TaskType.CODE, TaskType.RESEARCH):
            for name in sorted(pool, key=lambda n: (-sizes.get(n, 0), n)):
                if sizes.get(name, 0) >= 4:
                    return name
        if task_type == TaskType.SIMPLE:
            return sorted(pool, key=lambda n: (sizes.get(n, 0), n))[0]
        if mode in ("local_only", "local_preferred", "smart"):
            return sorted(pool, key=lambda n: (-sizes.get(n, 0), n))[0]
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
