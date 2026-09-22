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
    # Opus 5: 0.005 + 0.025. Sonnet 5: 0.002 + 0.010 -- this was 0.018,
    # which was the old 3/15 rate the published page no longer charges.
    assert summ["total_usd"] == pytest.approx(0.03 + 0.012)
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
#  Published rates (verified against the published price page 2026-09-22)
# ─────────────────────────────────────────────────────────────────────────────
#: USD per 1M tokens, as published. The table in cost_meter is per 1K, so these
#: are divided by 1000 on comparison — keeping this in the published unit is
#: deliberate, because that is the unit a reader can check against the price
#: page without doing arithmetic in their head first.
PUBLISHED_PER_MTOK = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5":   (10.00, 50.00),
    "claude-opus-5-5":  (4.00, 20.00),
    "claude-opus-5":    (5.00, 25.00),
    # Was pinned at 3/15 here and in all three tables. The published page says
    # the 2/10 launch price is now the standard price and the rise to 3/15 will
    # not occur -- so the pin was enforcing a 50% overcharge on the model
    # DEFAULT_CLOUD_MODEL points at.
    "claude-sonnet-5":  (2.00, 10.00),
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
    # Fast mode is Opus-only; asking for it on Sonnet changes nothing.
    assert (cm.cost_for("claude-sonnet-5", 1_000_000, 0, speed="fast")
            == cm.cost_for("claude-sonnet-5", 1_000_000, 0))
    # Opus 5.5 has its own fast rate (8/40), not Opus 5's (10/50).
    assert cm.cost_for("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.00)
    assert cm.cost_for("claude-opus-5-5", 1_000_000, 0,
                       speed="fast") == pytest.approx(8.00)


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



def test_opus_5_5_cache_reads_bill_at_a_twentieth_not_a_tenth():
    """The cache-read multiplier is per-model now, because the page says so.

    Opus 5.5 reads cache at 0.05x ($0.20 against a $4 base); every other model
    here reads at the standard 0.1x. Cache reads are not a minor term: the
    4.09M-token turn audited on 2026-09-22 was 96.4% cache reads, so this one
    multiplier decides nearly the whole bill. A flat tenth would overstate
    every cached Opus 5.5 read by exactly 2x.
    """
    assert cm.cache_read_mult("claude-opus-5-5") == pytest.approx(0.05)
    assert cm.cache_read_mult("claude-sonnet-5") == pytest.approx(0.1)
    assert cm.cache_read_mult("claude-opus-5") == pytest.approx(0.1)
    # An unknown model gets the standard multiplier, not a guess.
    assert cm.cache_read_mult("acme-whatever-1") == pytest.approx(0.1)

    # $4/MTok base, 1M cached read tokens -> $0.20, not $0.40.
    got = cm.cost_for("claude-opus-5-5", 0, 0, cache_read_tokens=1_000_000)
    assert got == pytest.approx(0.20)
    # Sonnet 5 at $2/MTok base still reads at a tenth -> $0.20.
    assert cm.cost_for("claude-sonnet-5", 0, 0,
                       cache_read_tokens=1_000_000) == pytest.approx(0.20)


def test_opus_5_5_is_offered_and_is_cheaper_than_the_opus_it_supersedes():
    """It goes in the picker, and the reason it takes the tier is the price.

    Opus 5.5 is both more capable and cheaper than Opus 5, which is why it
    leads the Opus entries. Opus 5 must stay listed and priced: a model
    somebody already selected does not vanish underneath them.
    """
    from agent_friday.services.provider_registry import get_provider_registry
    prov = next(p for p in get_provider_registry().list_providers()
                if p.get("name") == "anthropic")
    assert "claude-opus-5-5" in prov["models"], "Opus 5.5 is not offered"
    assert "claude-opus-5" in prov["models"], "Opus 5 was dropped, not kept"
    assert prov["model_meta"]["claude-opus-5-5"]["context_window"] == 1_000_000
    assert prov["model_meta"]["claude-opus-5-5"]["max_output"] == 128_000

    new_p = cm.price_for("claude-opus-5-5")
    old_p = cm.price_for("claude-opus-5")
    assert new_p["in"] < old_p["in"] and new_p["out"] < old_p["out"]


def test_the_default_cloud_model_was_not_repointed():
    """Opus 5.5 replaces the older OPUS. It does not become the default.

    Promoting it to DEFAULT_CLOUD_MODEL would double the cost of every
    unrouted cloud turn ($2/$10 -> $4/$20) without anyone asking for that.
    """
    from agent_friday.routing.model_router import (
        DEFAULT_CLOUD_MODEL, CLOUD_MODEL_FALLBACK_CHAIN)
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_CLOUD_MODEL == "claude-sonnet-5"
    cr = DEFAULT_SETTINGS["capability_routing"]
    assert cr["reasoning"]["model"] == "claude-sonnet-5"
    assert cr["subagent"]["model"] == "claude-sonnet-5"
    # But in the chain Friday walks on its own, the newer Opus comes first.
    chain = list(CLOUD_MODEL_FALLBACK_CHAIN)
    assert chain.index("claude-opus-5-5") < chain.index("claude-opus-5")


def test_fable_5_1_is_not_metered_free():
    """It was already selectable, and it was already billing at zero.

    Fable 5.1 is in the live /v1/models list, so the picker already offered it,
    and it was in none of the price tables -- so price_for fell through to the
    anthropic provider's cost_per_1k, found no row there either, and returned
    0/0. Measured 2026-09-22: 1M input tokens on the most expensive model in
    the lineup metered $0.00. A silent zero reads as "local, on-device, free",
    which is the one thing a cloud call is not. Same defect as the
    canonical-Haiku-id bug above, found the same way.
    """
    p = cm.price_for("claude-fable-5-1")
    assert p["in"] > 0 and p["out"] > 0, "Fable 5.1 meters free"
    assert cm.cost_for("claude-fable-5-1", 1_000_000, 0) == pytest.approx(10.00)
    assert cm.cost_for("claude-fable-5-1", 0, 1_000_000) == pytest.approx(50.00)
    # Its cache reads are 0.025x, not the standard tenth and not Opus 5.5's 0.05x.
    assert cm.cache_read_mult("claude-fable-5-1") == pytest.approx(0.025)
    assert cm.cost_for("claude-fable-5-1", 0, 0,
                       cache_read_tokens=1_000_000) == pytest.approx(0.25)


def test_the_derived_pricing_surface_agrees_with_the_meter():
    """services/pricing.py reads cost_meter.PRICING as its "dataset" tier, so
    it is a fourth place these numbers show up. It must not report None for a
    model the picker offers -- None means "unknown", and unknown is what let
    Fable 5.1 bill at nothing."""
    from agent_friday.services import pricing
    for mid, (want_in, want_out) in PUBLISHED_PER_MTOK.items():
        got = pricing.price("anthropic", mid)
        assert got is not None, mid + " is unknown to the derived price surface"
        assert got["in_per_1m"] == pytest.approx(want_in), mid
        assert got["out_per_1m"] == pytest.approx(want_out), mid
