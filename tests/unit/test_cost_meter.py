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
    # Opus 5: 0.005 in / 0.025 out per 1K. 1000 in + 1000 out = 0.005 + 0.025.
    # (These numbers used to read 0.015/0.075 — the test pinned the overcharge
    # rather than catching it. See test_anthropic_rates_match_published_pricing.)
    assert cm.cost_for("claude-opus-5", 1000, 1000) == pytest.approx(0.03)
    # Output costs ~5× input — verify directions aren't blended.
    assert cm.cost_for("claude-opus-5", 2000, 0) == pytest.approx(0.01)
    assert cm.cost_for("claude-opus-5", 0, 2000) == pytest.approx(0.05)


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
    assert summ["total_usd"] == pytest.approx(0.03 + 0.018)
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


# ─────────────────────────────────────────────────────────────────────────────
#  Published rates (2026-08-28)
# ─────────────────────────────────────────────────────────────────────────────
#: USD per 1M tokens, as published. The table in cost_meter is per 1K, so these
#: are divided by 1000 on comparison — keeping this in the published unit is
#: deliberate, because that is the unit a reader can check against the price
#: page without doing arithmetic in their head first.
PUBLISHED_PER_MTOK = {
    "claude-fable-5":   (10.00, 50.00),
    "claude-opus-5":    (5.00, 25.00),
    "claude-sonnet-5":  (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@pytest.mark.parametrize("mid,rates", sorted(PUBLISHED_PER_MTOK.items()))
def test_anthropic_rates_match_published_pricing(mid, rates):
    """The meter charges what Anthropic charges.

    Every one of these was wrong in the expensive direction or the cheap one
    for the whole 5.6.x line, and every downstream number inherited it: the
    Cost & Usage panel, the daily/monthly budget tripwires, the per-schedule
    breakdown, and the install report's estimate of what self-repair cost.

    Opus 5 metered at 15/75 against a real 5/25 — a 3x overcharge on the
    model Friday defaults to. Fable 5 metered at 3/15 against a real 10/50,
    so the most expensive model in the lineup was billed as the cheapest.
    """
    want_in, want_out = rates
    p = cm.price_for(mid)
    assert p["in"] == pytest.approx(want_in / 1000.0), f"{mid} input rate"
    assert p["out"] == pytest.approx(want_out / 1000.0), f"{mid} output rate"


def test_canonical_haiku_id_is_not_metered_free():
    """`claude-haiku-4-5` is the model id; the dated one is a legacy alias.

    PRICING was keyed ONLY on `claude-haiku-4-5-20251001`, so a call using the
    canonical id missed the table, fell through the registry fallback (Haiku is
    not in the anthropic provider's cost_per_1k), and metered at exactly $0.
    A silent zero reads as "local, on-device, free" — the one thing a cloud
    call is not. Both ids must price, and price identically.
    """
    canonical = cm.price_for("claude-haiku-4-5")
    dated = cm.price_for("claude-haiku-4-5-20251001")
    assert canonical["in"] > 0 and canonical["out"] > 0
    assert canonical == dated
    assert cm.cost_for("claude-haiku-4-5", 10_000, 10_000) > 0


def test_fast_mode_bills_at_the_premium_rate():
    """Opus 5 in fast mode is a different price, not a faster same price.

    Fast mode runs the same model at up to 2.5x output speed and bills 10/50
    per MTok instead of 5/25. Nothing in Friday requests it today, so this is
    the meter being correct in advance rather than a bug being fixed: if a
    caller ever passes speed='fast', it must not be billed as standard Opus.
    """
    std = cm.cost_for("claude-opus-5", 1_000_000, 0)
    fast = cm.cost_for("claude-opus-5", 1_000_000, 0, speed="fast")
    assert std == pytest.approx(5.00)
    assert fast == pytest.approx(10.00)
    # An unknown speed is not a licence to invent a rate — fall back to standard.
    assert cm.cost_for("claude-opus-5", 1_000_000, 0,
                       speed="warp") == pytest.approx(5.00)
    # Fast mode is Opus-5/4.8 only; asking for it on Sonnet changes nothing.
    assert (cm.cost_for("claude-sonnet-5", 1_000_000, 0, speed="fast")
            == cm.cost_for("claude-sonnet-5", 1_000_000, 0))


def test_the_other_two_price_tables_agree_with_this_one():
    """Three tables price the same models. They must not drift apart.

    `cost_meter.PRICING` is per-direction and is what the Cost & Usage panel
    and the budget tripwires read. Two others carry a single blended per-1K
    rate for the same ids:

      * `routing.model_router.CLOUD_COST_PER_1K` — the savings-vs-all-cloud stat
      * `provider_registry`'s anthropic `cost_per_1k` — the picker's displayed
        rate, and `price_for`'s fallback for anything not in PRICING

    Both still carried Claude-3-Opus-era numbers (Opus at 0.075/1K, i.e. 15x the
    real input rate), so correcting PRICING alone would have left the panel
    honest and the savings figure overstating local routing by 3-15x. The blend
    convention is the midpoint of the two directions, matching what
    `model_catalog` already computes for discovered models.
    """
    from agent_friday.routing.model_router import CLOUD_COST_PER_1K
    from agent_friday.services.provider_registry import get_provider_registry

    def midpoint(mid):
        p = cm.PRICING[mid]
        return (p["in"] + p["out"]) / 2.0

    registry_rates = (get_provider_registry()
                      .get_provider("anthropic") or {}).get("cost_per_1k") or {}

    for mid in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5"):
        assert CLOUD_COST_PER_1K[mid] == pytest.approx(midpoint(mid)), (
            f"{mid}: savings tracker disagrees with the meter")
        assert registry_rates[mid] == pytest.approx(midpoint(mid)), (
            f"{mid}: registry blended rate disagrees with the meter")
