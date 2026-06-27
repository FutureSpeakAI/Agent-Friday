"""Unit tests for model_router — the routing layer that decides which provider
handles each request.  Security-critical: a misroute can send vault data to
the cloud.  Every test uses synthetic data; no real network calls are made.

Key invariants under test:
  * classify_task   — deterministic classification from message content
  * needs_vault_access — keyword + context flag detection
  * _finalize       — control flags (is_local, vault_allowed, scrub_pii…)
  * _pick_local_model — preference / task-size heuristics
  * anthropic_to_openai_tools — lossless schema conversion
  * CostTracker / get_stats — accounting correctness
  * route() integration — cloud_only, local_preferred, vault force-local
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.routing.model_router as mr
from agent_friday.routing.model_router import (
    ModelRouter,
    TaskType,
    CostTracker,
    TIER_3_KEYWORDS as VAULT_KEYWORDS,  # unified source: sensitivity_classifier
    anthropic_to_openai_tools,
    openai_response_to_friday,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _msgs(*texts: str) -> list:
    return [_user(t) for t in texts]


def _router(mode="cloud_only", **extra) -> ModelRouter:
    cfg = {"mode": mode, **extra}
    return ModelRouter(config=cfg)


def _fake_models(*names: str, size_gb: float = 2.0) -> list:
    return [{"name": n, "size_gb": size_gb} for n in names]


# ── TaskType classification ─────────────────────────────────────────────────--

class TestClassifyTask:
    """classify_task must return the right TaskType from message content."""

    def test_empty_messages_is_simple(self):
        r = _router()
        assert r.classify_task([]) == TaskType.SIMPLE

    def test_short_plain_message_is_simple(self):
        r = _router()
        assert r.classify_task(_msgs("hi there")) == TaskType.SIMPLE

    def test_has_tools_overrides_everything(self):
        r = _router()
        # even a CODE-looking message routes as TOOL_USE when has_tools=True
        assert r.classify_task(_msgs("write code for me"), has_tools=True) == TaskType.TOOL_USE

    @pytest.mark.parametrize("text", [
        "write code to sort a list",
        "implement a binary search",
        "refactor this function",
        "debug why this class crashes",
        "def my_function(): pass",
        "import os; import sys",
        "explain this ``` python snippet",
        "design an algorithm",
    ])
    def test_code_keywords(self, text):
        r = _router()
        assert r.classify_task(_msgs(text)) == TaskType.CODE

    @pytest.mark.parametrize("text", [
        "research the best databases",
        "analyze the trends in AI",
        "compare these two options thoroughly",
        "deep dive into neural networks",
        "explain in detail how TCP/IP works",
        "comprehensive overview of LLMs",
        "thorough investigation needed",
        "investigate the root cause",
    ])
    def test_research_keywords(self, text):
        r = _router()
        assert r.classify_task(_msgs(text)) == TaskType.RESEARCH

    def test_long_message_without_keywords_is_research(self):
        r = _router()
        long_msg = "x " * 200  # > 200 chars, no special keywords
        assert r.classify_task(_msgs(long_msg)) == TaskType.RESEARCH

    def test_uses_last_user_message_only(self):
        r = _router()
        msgs = [
            {"role": "assistant", "content": "write code for a billion things"},
            _user("hi"),  # last user message — short, no keywords → SIMPLE
        ]
        assert r.classify_task(msgs) == TaskType.SIMPLE

    def test_non_string_content_safe(self):
        # content can be a list (multi-modal) — should not crash
        r = _router()
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        result = r.classify_task(msgs)
        assert result == TaskType.SIMPLE  # list content → empty string fallback

    def test_no_user_message_is_simple(self):
        r = _router()
        msgs = [{"role": "assistant", "content": "hello"}]
        assert r.classify_task(msgs) == TaskType.SIMPLE


# ── needs_vault_access ─────────────────────────────────────────────────────────

class TestNeedsVaultAccess:
    """Vault detection is security-critical — false negative = cloud data leak."""

    def test_vault_context_flag_true(self):
        r = _router()
        assert r.needs_vault_access([], {"vault_access": True}) is True

    def test_vault_context_flag_false(self):
        r = _router()
        assert r.needs_vault_access([], {"vault_access": False}) is False

    def test_vault_tool_name_triggers(self):
        r = _router()
        assert r.needs_vault_access([], {"tool_names": ["vault_read", "other"]}) is True

    def test_non_vault_tool_names_safe(self):
        r = _router()
        assert r.needs_vault_access([], {"tool_names": ["search_web", "send_email"]}) is False

    @pytest.mark.parametrize("kw", list(VAULT_KEYWORDS))
    def test_vault_keyword_in_message(self, kw):
        r = _router()
        assert r.needs_vault_access(_msgs(f"please check my {kw} data"), {}) is True

    def test_innocuous_message_no_vault(self):
        r = _router()
        assert r.needs_vault_access(_msgs("what's the weather today?"), {}) is False

    def test_none_messages_no_crash(self):
        r = _router()
        assert r.needs_vault_access(None, {}) is False

    def test_none_ctx_no_crash(self):
        r = _router()
        assert r.needs_vault_access(_msgs("hello"), None) is False

    def test_vault_keyword_case_insensitive(self):
        r = _router()
        assert r.needs_vault_access(_msgs("Show me my VAULT data"), {}) is True

    def test_partial_match_not_triggered(self):
        """'vault' as substring of unrelated word should only trigger if the word contains kw."""
        # 'financial' IS a keyword — test that full keyword match works
        r = _router()
        assert r.needs_vault_access(_msgs("financial planning tips"), {}) is True

    def test_empty_messages_no_vault(self):
        r = _router()
        assert r.needs_vault_access([], {}) is False


# ── _finalize control flags ────────────────────────────────────────────────────

class TestFinalize:
    """_finalize must set all downstream control flags consistently."""

    def test_local_provider_flags(self):
        r = _router()
        result = r._finalize({"provider": "local", "model": "llama3"})
        assert result["is_local"] is True
        assert result["vault_allowed"] is True
        assert result["scrub_pii"] is False
        assert result["refuse"] is False
        assert result["warning"] is None
        assert result["vault_access"] is False

    def test_cloud_provider_flags(self):
        r = _router()
        result = r._finalize({"provider": "cloud", "model": "claude-opus-4-8"})
        assert result["is_local"] is False
        assert result["vault_allowed"] is False
        assert result["scrub_pii"] is True

    def test_vault_access_flag_passed_through(self):
        r = _router()
        result = r._finalize({"provider": "local", "model": "x"}, vault_access=True)
        assert result["vault_access"] is True

    def test_refuse_flag(self):
        r = _router()
        result = r._finalize({"provider": "cloud", "model": "x"}, refuse=True)
        assert result["refuse"] is True

    def test_warning_flag(self):
        r = _router()
        result = r._finalize({"provider": "cloud", "model": "x"}, warning="needs local model")
        assert result["warning"] == "needs local model"

    def test_openai_provider_is_not_local(self):
        r = _router()
        result = r._finalize({"provider": "openai", "model": "gpt-4o"})
        assert result["is_local"] is False
        assert result["scrub_pii"] is True


# ── _pick_local_model ──────────────────────────────────────────────────────────

class TestPickLocalModel:
    """Model-selection heuristics — prefers large models for CODE/RESEARCH,
    small models for SIMPLE, user preference always wins."""

    def test_preferred_model_wins(self):
        r = _router(local_model="preferred:latest")
        models = _fake_models("preferred:latest", "other:latest")
        assert r._pick_local_model(models, TaskType.SIMPLE, "local_preferred") == "preferred:latest"

    def test_preferred_not_installed_skips(self):
        r = _router(local_model="missing:model")
        models = _fake_models("gemma4:latest")
        # should not return missing model; falls through to heuristics
        result = r._pick_local_model(models, TaskType.SIMPLE, "local_preferred")
        assert result == "gemma4:latest"

    def test_code_task_prefers_large_model(self):
        models = [
            {"name": "small:3b", "size_gb": 2.0},
            {"name": "large:13b", "size_gb": 8.0},
        ]
        r = _router()
        result = r._pick_local_model(models, TaskType.CODE, "local_preferred")
        assert result == "large:13b"

    def test_research_task_prefers_large_model(self):
        models = [
            {"name": "tiny:1b", "size_gb": 1.0},
            {"name": "big:30b", "size_gb": 20.0},
        ]
        r = _router()
        result = r._pick_local_model(models, TaskType.RESEARCH, "local_preferred")
        assert result == "big:30b"

    def test_code_no_large_model_returns_none(self):
        """If no model >= 4 GB, CODE task returns None (caller will cloud-fallback)."""
        models = [{"name": "tiny:1b", "size_gb": 1.0}]
        r = _router()
        # local_preferred or smart must still return first model in the fallback branch
        # but pure heuristic (no preferred, no >=4GB for CODE) returns None before fallback
        result = r._pick_local_model(models, TaskType.CODE, "cloud_only")
        # cloud_only mode → None (no fallback clause triggers for CODE with no big model)
        assert result is None

    def test_simple_task_picks_smallest(self):
        models = [
            {"name": "medium:7b", "size_gb": 4.0},
            {"name": "tiny:1b", "size_gb": 1.0},
            {"name": "large:13b", "size_gb": 8.0},
        ]
        r = _router()
        result = r._pick_local_model(models, TaskType.SIMPLE, "local_preferred")
        assert result == "tiny:1b"

    def test_local_only_fallback_returns_first(self):
        models = _fake_models("model_a", "model_b")
        r = _router()
        result = r._pick_local_model(models, TaskType.VAULT_ACCESS, "local_only")
        assert result == "model_a"

    def test_local_preferred_fallback_returns_first(self):
        models = _fake_models("first_model")
        r = _router()
        result = r._pick_local_model(models, TaskType.VAULT_ACCESS, "local_preferred")
        assert result == "first_model"

    def test_smart_mode_fallback_returns_first(self):
        models = _fake_models("smart_model")
        r = _router()
        result = r._pick_local_model(models, TaskType.VAULT_ACCESS, "smart")
        assert result == "smart_model"

    def test_empty_models_returns_none(self):
        r = _router()
        assert r._pick_local_model([], TaskType.SIMPLE, "local_preferred") is None

    def test_model_with_missing_size_treated_as_zero(self):
        models = [{"name": "no_size_model"}]  # no size_gb key
        r = _router()
        # Should not crash; size defaults to 0
        result = r._pick_local_model(models, TaskType.SIMPLE, "local_preferred")
        assert result == "no_size_model"


# ── anthropic_to_openai_tools ─────────────────────────────────────────────────

class TestAnthropicToOpenAITools:
    """Tool schema conversion — lossless preservation of name / description /
    parameters is required for OpenAI-compatible routing to work correctly."""

    def test_none_returns_none(self):
        assert anthropic_to_openai_tools(None) is None

    def test_empty_list_returns_none(self):
        assert anthropic_to_openai_tools([]) is None

    def test_single_tool_structure(self):
        tools = [{
            "name": "search_web",
            "description": "Search the internet",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }]
        result = anthropic_to_openai_tools(tools)
        assert len(result) == 1
        fn = result[0]
        assert fn["type"] == "function"
        assert fn["function"]["name"] == "search_web"
        assert fn["function"]["description"] == "Search the internet"
        assert fn["function"]["parameters"]["type"] == "object"
        assert "query" in fn["function"]["parameters"]["properties"]

    def test_multiple_tools_all_converted(self):
        tools = [
            {"name": "tool_a", "description": "A", "input_schema": {}},
            {"name": "tool_b", "description": "B", "input_schema": {}},
            {"name": "tool_c", "description": "C", "input_schema": {}},
        ]
        result = anthropic_to_openai_tools(tools)
        assert len(result) == 3
        names = [t["function"]["name"] for t in result]
        assert names == ["tool_a", "tool_b", "tool_c"]

    def test_missing_name_defaults_empty_string(self):
        tools = [{"description": "no name tool", "input_schema": {}}]
        result = anthropic_to_openai_tools(tools)
        assert result[0]["function"]["name"] == ""

    def test_missing_description_defaults_empty_string(self):
        tools = [{"name": "tool_x", "input_schema": {}}]
        result = anthropic_to_openai_tools(tools)
        assert result[0]["function"]["description"] == ""

    def test_missing_input_schema_defaults_empty_dict(self):
        tools = [{"name": "tool_y", "description": "y"}]
        result = anthropic_to_openai_tools(tools)
        assert result[0]["function"]["parameters"] == {}

    def test_complex_nested_schema_preserved(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search term"},
                "max_results": {"type": "integer", "default": 10},
                "filters": {
                    "type": "object",
                    "properties": {"date_from": {"type": "string"}},
                },
            },
            "required": ["query"],
        }
        tools = [{"name": "advanced_search", "description": "adv", "input_schema": schema}]
        result = anthropic_to_openai_tools(tools)
        assert result[0]["function"]["parameters"] == schema

    def test_output_type_is_list(self):
        tools = [{"name": "t", "description": "d", "input_schema": {}}]
        result = anthropic_to_openai_tools(tools)
        assert isinstance(result, list)


# ── openai_response_to_friday ─────────────────────────────────────────────────

class TestOpenAIResponseToFriday:
    """Normalizer for OAI responses — must not crash on malformed input."""

    def test_normal_response(self):
        oai = {
            "choices": [{"message": {"role": "assistant", "content": "hello"}}]
        }
        text, tool_calls = openai_response_to_friday(oai, "gpt-4o")
        assert text == "hello"
        assert tool_calls == []

    def test_strips_whitespace(self):
        oai = {"choices": [{"message": {"content": "  trimmed  "}}]}
        text, _ = openai_response_to_friday(oai, "model")
        assert text == "trimmed"

    def test_empty_choices_returns_empty(self):
        text, tool_calls = openai_response_to_friday({"choices": []}, "model")
        assert text == ""
        assert tool_calls == []

    def test_missing_choices_returns_empty(self):
        text, tool_calls = openai_response_to_friday({}, "model")
        assert text == ""
        assert tool_calls == []

    def test_none_content_returns_empty(self):
        oai = {"choices": [{"message": {"content": None}}]}
        text, _ = openai_response_to_friday(oai, "model")
        assert text == ""

    def test_missing_message_key_returns_empty(self):
        oai = {"choices": [{}]}
        text, _ = openai_response_to_friday(oai, "model")
        assert text == ""


# ── CostTracker / get_stats ───────────────────────────────────────────────────

class TestCostTracker:
    """Accounting must be correct; local cost = $0; cloud uses per-model rate."""

    def test_local_cost_is_zero(self):
        ct = CostTracker()
        ct.record("local", "gemma4:latest", prompt_tokens=1000, completion_tokens=500)
        stats = ct.stats(since=0)
        assert stats["cloud_cost"] == 0.0
        assert stats["local_requests"] == 1

    def test_cloud_cost_nonzero(self):
        ct = CostTracker()
        ct.record("anthropic", "claude-sonnet-4-6", prompt_tokens=1000, completion_tokens=0)
        stats = ct.stats(since=0)
        assert stats["cloud_cost"] > 0.0
        assert stats["cloud_requests"] == 1

    def test_known_model_rate(self):
        ct = CostTracker()
        # claude-sonnet-4-6 rate = $0.015/1K tokens
        ct.record("anthropic", "claude-sonnet-4-6", prompt_tokens=1000, completion_tokens=0)
        stats = ct.stats(since=0)
        assert abs(stats["cloud_cost"] - 0.015) < 1e-5

    def test_unknown_model_uses_default_rate(self):
        ct = CostTracker()
        ct.record("anthropic", "unknown-model-xyz", prompt_tokens=1000, completion_tokens=0)
        stats = ct.stats(since=0)
        # default rate is 0.015
        assert abs(stats["cloud_cost"] - 0.015) < 1e-5

    def test_estimated_savings_local(self):
        ct = CostTracker()
        ct.record("local", "gemma4:latest", prompt_tokens=1000, completion_tokens=0)
        stats = ct.stats(since=0)
        # estimated_savings is 0 — blended-rate dollar figure was removed (inaccurate)
        assert stats["estimated_savings"] == 0.0
        # local_tokens reflects the on-device work instead
        assert stats["local_tokens"] == 1000

    def test_total_requests(self):
        ct = CostTracker()
        ct.record("local", "gemma4", prompt_tokens=100)
        ct.record("local", "gemma4", prompt_tokens=100)
        ct.record("anthropic", "claude-opus-4-8", prompt_tokens=100)
        stats = ct.stats(since=0)
        assert stats["total_requests"] == 3
        assert stats["local_requests"] == 2
        assert stats["cloud_requests"] == 1

    def test_by_model_aggregation(self):
        ct = CostTracker()
        ct.record("local", "gemma4:latest", prompt_tokens=500, completion_tokens=100)
        ct.record("local", "gemma4:latest", prompt_tokens=200, completion_tokens=50)
        stats = ct.stats(since=0)
        assert "gemma4:latest" in stats["by_model"]
        assert stats["by_model"]["gemma4:latest"]["requests"] == 2
        assert stats["by_model"]["gemma4:latest"]["tokens"] == 850

    def test_stats_shape_keys(self):
        ct = CostTracker()
        stats = ct.stats(since=0)
        expected = {
            "local_requests", "cloud_requests", "local_tokens", "cloud_tokens",
            "cloud_cost", "estimated_savings", "by_model", "total_requests",
        }
        assert expected.issubset(stats.keys())

    def test_empty_stats(self):
        ct = CostTracker()
        stats = ct.stats(since=0)
        assert stats["total_requests"] == 0
        assert stats["cloud_cost"] == 0.0
        assert stats["estimated_savings"] == 0.0

    def test_ring_buffer_trim(self):
        """Over 10000 entries the ring buffer trims to 5000."""
        ct = CostTracker()
        for _ in range(10001):
            ct.record("local", "model", prompt_tokens=1)
        # After trim, internal list <= 5000
        assert len(ct._requests) <= 5000


# ── ModelRouter.route — integration (mocked Ollama) ──────────────────────────

class TestRouteIntegration:
    """Integration tests for route().  Ollama calls are monkeypatched so no
    network access is made.  Focus: routing mode determines provider selection."""

    def _patch_ollama(self, monkeypatch, available=False, models=None):
        """Patch get_manager in model_router so route() sees a fake Ollama.

        model_router imports ollama_manager lazily inside methods with
        `from agent_friday.routing.ollama_manager import get_manager`, so we must patch
        ollama_manager.get_manager directly on the already-imported module.
        """
        import agent_friday.routing.ollama_manager as ollama_manager

        _avail = available
        _models = list(models or [])

        class FakeOllama:
            def is_available(self):
                return _avail
            def list_models(self):
                return _models

        fake = FakeOllama()
        monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **kw: fake)

    # cloud_only mode ────────────────────────────────────────────────────────--

    def test_cloud_only_goes_cloud(self, monkeypatch):
        self._patch_ollama(monkeypatch, available=False)
        r = _router(mode="cloud_only")
        result = r.route(_msgs("what is 2+2?"))
        assert result["provider"] == "cloud"
        assert result["is_local"] is False
        assert result["scrub_pii"] is True

    def test_cloud_only_vault_message_forces_local_or_refuse(self, monkeypatch):
        """Even in cloud_only mode, a vault message must NOT go unguarded to cloud."""
        self._patch_ollama(monkeypatch, available=True,
                           models=[{"name": "gemma4:latest", "size_gb": 5.0}])
        r = _router(mode="cloud_only")
        result = r.route(_msgs("show me my vault data"))
        # Vault routing always forces local when Ollama is available
        assert result["vault_access"] is True
        assert result["provider"] == "local"
        assert result["vault_allowed"] is True

    def test_cloud_only_vault_no_ollama_redact_fallback(self, monkeypatch):
        """Vault request + no local model: default is redact (cloud, no refuse)."""
        self._patch_ollama(monkeypatch, available=False)
        r = _router(mode="cloud_only", vault_cloud_fallback="redact")
        result = r.route(_msgs("my ssn is in the vault"))
        assert result["vault_access"] is True
        assert result["provider"] == "cloud"
        assert result["refuse"] is False

    def test_cloud_only_vault_no_ollama_deny_fallback(self, monkeypatch):
        """Vault request + no local model + deny policy → refuse=True."""
        self._patch_ollama(monkeypatch, available=False)
        r = _router(mode="cloud_only", vault_cloud_fallback="deny")
        result = r.route(_msgs("my custody records"))
        assert result["vault_access"] is True
        assert result["refuse"] is True

    # local_preferred mode ───────────────────────────────────────────────────-

    def test_local_preferred_uses_local_when_available(self, monkeypatch):
        self._patch_ollama(monkeypatch, available=True,
                           models=[{"name": "gemma4:latest", "size_gb": 5.0}])
        r = _router(mode="local_preferred")
        result = r.route(_msgs("hello"))
        assert result["provider"] == "local"
        assert result["is_local"] is True
        assert result["vault_allowed"] is True

    def test_local_preferred_falls_back_to_cloud_when_unavailable(self, monkeypatch):
        self._patch_ollama(monkeypatch, available=False)
        r = _router(mode="local_preferred", fallback_to_cloud=True)
        result = r.route(_msgs("hello"))
        assert result["provider"] == "cloud"

    # local_only mode ────────────────────────────────────────────────────────-

    def test_local_only_no_ollama_falls_back(self, monkeypatch):
        """local_only without ollama should still return a result (cloud fallback)."""
        self._patch_ollama(monkeypatch, available=False)
        r = _router(mode="local_only", fallback_to_cloud=False)
        result = r.route(_msgs("hello"))
        # Either cloud or local, but must not crash and must have required keys
        assert "provider" in result
        assert "is_local" in result

    # _apply_cloud_provider (OpenAI-compatible re-routing) ───────────────────-

    def test_openai_provider_config(self, monkeypatch):
        self._patch_ollama(monkeypatch, available=False)
        r = _router(mode="cloud_only", cloud_provider="openai", openai_model="gpt-4o")
        result = r.route(_msgs("hello"))
        assert result["provider"] == "openai"
        assert result["model"] == "gpt-4o"

    def test_openrouter_provider_config(self, monkeypatch):
        self._patch_ollama(monkeypatch, available=False)
        r = _router(mode="cloud_only", cloud_provider="openrouter", openai_model="mistral:7b")
        result = r.route(_msgs("hello"))
        assert result["provider"] == "openai"

    def test_anthropic_provider_stays_cloud(self, monkeypatch):
        self._patch_ollama(monkeypatch, available=False)
        r = _router(mode="cloud_only", cloud_provider="anthropic")
        result = r.route(_msgs("hello"))
        assert result["provider"] == "cloud"

    # Required keys always present ───────────────────────────────────────────-

    @pytest.mark.parametrize("mode", ["cloud_only", "local_preferred", "local_only", "smart"])
    def test_route_always_returns_required_keys(self, monkeypatch, mode):
        self._patch_ollama(monkeypatch, available=False)
        r = _router(mode=mode)
        result = r.route(_msgs("test message"))
        for key in ("provider", "model", "task_type", "reason", "is_local",
                    "vault_allowed", "scrub_pii", "vault_access", "refuse", "warning"):
            assert key in result, f"Missing key '{key}' in route result for mode={mode}"

    # task_overrides config ──────────────────────────────────────────────────-

    def test_task_override_applied(self, monkeypatch):
        self._patch_ollama(monkeypatch, available=True,
                           models=[{"name": "gemma4:latest", "size_gb": 5.0}])
        overrides = {TaskType.SIMPLE: {"provider": "cloud", "model": "claude-haiku-4-5-20251001"}}
        r = _router(mode="local_preferred", task_overrides=overrides)
        result = r.route(_msgs("hi"))
        assert result["provider"] == "cloud"
        assert result["model"] == "claude-haiku-4-5-20251001"


# ── ModelRouter.get_stats / reload_config ────────────────────────────────────

class TestRouterStats:
    def test_get_stats_returns_dict(self):
        r = _router()
        stats = r.get_stats()
        assert isinstance(stats, dict)
        assert "total_requests" in stats

    def test_reload_config_updates_mode(self):
        r = _router(mode="cloud_only")
        assert r.mode == "cloud_only"
        r.reload_config({"mode": "local_preferred"})
        assert r.mode == "local_preferred"

    def test_default_mode_is_cloud_only(self):
        r = ModelRouter()
        assert r.mode == "cloud_only"

    def test_default_fallback_to_cloud_is_true(self):
        r = ModelRouter()
        assert r.fallback_to_cloud is True

    def test_fallback_configurable(self):
        r = _router(fallback_to_cloud=False)
        assert r.fallback_to_cloud is False


# ── VAULT_KEYWORDS sanity ─────────────────────────────────────────────────────

class TestVaultKeywords:
    """TIER_3_KEYWORDS (imported as VAULT_KEYWORDS) are the routing security boundary.

    Authoritative source: services/sensitivity_classifier.TIER_3_KEYWORDS.
    """

    def test_all_critical_keywords_present(self):
        important = {"vault", "ssn", "custody", "financial", "health record"}
        assert important.issubset(set(VAULT_KEYWORDS))

    def test_vault_keywords_all_lowercase(self):
        """All keywords must be lowercase so the case-fold comparison works."""
        for kw in VAULT_KEYWORDS:
            assert kw == kw.lower(), f"Keyword '{kw}' is not lowercase"

    def test_vault_keywords_nonempty(self):
        assert len(VAULT_KEYWORDS) > 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
