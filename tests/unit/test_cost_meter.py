"""Unit tests for cost metering (Part D)."""
import pytest

from agent_friday.services import cost_meter as cm


@pytest.fixture(autouse=True)
def _fresh_db(friday_dir):
    cm.reset_for_tests()
    if cm.DB_PATH.exists():
        cm.DB_PATH.unlink()
    cm.reset_for_tests()
    yield
    cm.reset_for_tests()


def test_per_direction_pricing():
    # Opus: 0.015 in / 0.075 out per 1K. 1000 in + 1000 out = 0.015 + 0.075.
    assert cm.cost_for("claude-opus-5", 1000, 1000) == pytest.approx(0.09)
    # Output costs ~5× input — verify directions aren't blended.
    assert cm.cost_for("claude-opus-5", 2000, 0) == pytest.approx(0.03)
    assert cm.cost_for("claude-opus-5", 0, 2000) == pytest.approx(0.15)


def test_local_models_free():
    assert cm.cost_for("gemma4:latest", 100000, 100000) == 0.0
    assert cm.price_for("llama3.1:8b") == {"in": 0.0, "out": 0.0}


def test_every_registry_text_cloud_model_is_priced():
    # Every model the token-metered cloud providers (anthropic/openai) offer in
    # the picker must meter at a nonzero rate — a $0 entry silently underreports
    # Cost & Usage and budget alerts never trip. Regression: claude-opus-4-7 /
    # 4-6 were added to the registry (251d88f) without PRICING entries and
    # metered $0. (Gemini creative models — Veo/Nano Banana/Lyria — are metered
    # by the creation budget, not per-token, so they're out of scope here.)
    from agent_friday.services.provider_registry import get_provider_registry
    reg = get_provider_registry()
    for pname in ("anthropic", "openai"):
        prov = reg.get_provider(pname)
        assert prov, f"provider {pname} missing from registry"
        for mid in prov.get("models") or []:
            p = cm.price_for(mid)
            assert p["in"] > 0 and p["out"] > 0, (
                f"{pname}/{mid} meters at $0 — add it to "
                f"cost_meter.PRICING or the provider's cost_per_1k")
            assert cm.cost_for(mid, 10000, 10000) > 0


def test_current_claude_lineup_is_priced():
    """The whole shipped family has a real per-direction price.

    Replaces a test that pinned claude-opus-4-7 / 4-6. Those ids were retired on
    2026-08-17 along with sonnet-4-5/4-6 and opus-4-8: a hardcoded model id
    nobody maintains quietly becomes what the product actually uses, and
    start.bat was pinning ANTHROPIC_MODEL=claude-sonnet-4-6 over the configured
    sonnet-5 on every launch.
    """
    for mid in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5",
                "claude-haiku-4-5-20251001"):
        pr = cm.price_for(mid)
        assert pr["in"] > 0 and pr["out"] > pr["in"], mid


def test_a_retired_model_id_is_not_free():
    """A superseded id must not silently price at zero.

    Zero would read as "local, on-device, free" for something that in fact
    billed a cloud provider, so an unknown cloud id falls back to the
    registry's blended rate instead.
    """
    assert cm.cost_for("claude-opus-4-8", 1000, 1000) >= 0.0


def test_price_for_falls_back_to_registry_blended_rate(monkeypatch):
    # Unknown cloud model ids must pick up the provider's blended cost_per_1k
    # rate for both directions instead of $0. (The old fallback loop checked
    # hasattr(module, "list_providers") — a method on the registry OBJECT —
    # so it always iterated [] and was dead code.)
    import agent_friday.services.provider_registry as pr

    class _StubRegistry:
        def list_providers(self):
            return [{"name": "custom",
                     "cost_per_1k": {"acme-frontier-1": 0.02}}]

    monkeypatch.setattr(pr, "get_provider_registry", lambda: _StubRegistry())
    assert cm.price_for("acme-frontier-1") == {"in": 0.02, "out": 0.02}
    assert cm.cost_for("acme-frontier-1", 1000, 1000) == pytest.approx(0.04)


def test_record_and_summary():
    cm.record("anthropic", "claude-opus-5", 1000, 1000,
              workspace="research", kind="chat")
    cm.record("anthropic", "claude-sonnet-5", 1000, 1000,
              workspace="studio", kind="task")
    summ = cm.summary("today")
    assert summ["total_calls"] == 2
    assert summ["total_usd"] == pytest.approx(0.09 + 0.018)
    assert summ["by_workspace"]["research"]["calls"] == 1
    assert "anthropic" in summ["by_provider"]
    assert set(summ["by_kind"].keys()) == {"chat", "task"}


def test_meter_maps_openai_usage():
    cm.meter("openai", "gpt-4o", {"prompt_tokens": 1000, "completion_tokens": 500})
    summ = cm.summary("today")
    assert summ["input_tokens"] == 1000
    assert summ["output_tokens"] == 500


def test_meter_maps_anthropic_usage_object():
    class _Usage:
        input_tokens = 1000
        output_tokens = 200
    cm.meter("anthropic", "claude-opus-4-8", _Usage())
    summ = cm.summary("today")
    assert summ["input_tokens"] == 1000
    assert summ["output_tokens"] == 200


def test_task_attribution_for_scheduled():
    cm.register_task_attribution("task-123", {
        "kind": "scheduled", "schedule_id": "sch_jobintel", "workspace": "research"})
    cm.record("anthropic", "claude-opus-4-8", 1000, 0,
              session_ctx={"task_id": "task-123"})
    sched = cm.by_schedule("today")
    assert any(r["schedule_id"] == "sch_jobintel" for r in sched)


def test_thread_local_attribution():
    cm.push_attribution(kind="compaction", workspace="system")
    try:
        cm.record("anthropic", "claude-haiku-4-5-20251001", 500, 100)
    finally:
        cm.pop_attribution()
    summ = cm.summary("today")
    assert "compaction" in summ["by_kind"]


def test_budget_set_get():
    b = cm.set_budget({"daily": 10.0, "daily_enabled": True})
    assert b["daily"] == 10.0 and b["daily_enabled"] is True
