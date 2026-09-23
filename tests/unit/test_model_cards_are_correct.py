"""Every card the picker offers must be priced, and must not overstate itself.

Audited on 2026-09-23 by listing what the picker ACTUALLY offers rather than
what the shipped descriptor names -- which is what turned these up, because the
Anthropic list is discovered live and is longer than the shipped one:

  * Six retired-but-still-served ids (Opus 4.8/4.7/4.6/4.5, Sonnet 4.6/4.5) were
    offered with no price row at all. `price_for` fell through to the provider
    blended rate, which had no row either, and metered **$0** -- the same defect
    as the canonical-Haiku-id and Fable 5.1 zeros.
  * `gpt-4o` displayed 0.0375/1k and `gpt-4o-mini` 0.00225 -- **6x** the real
    rates ($2.50/$10 and $0.15/$0.60, midpoints 0.00625 and 0.000375).
  * `bonsai2:27b` reported a **65,536** context window. models.json declares
    262,144 (the architecture) and the live llama-server serves **49,152**. The
    card showed the residency PLAN, 33% above what the process accepts -- the
    same shape as the e4b 400s.
"""

import pytest

from agent_friday.services import cost_meter as cm
from agent_friday.services import model_catalog as mc
from agent_friday.services.provider_registry import get_provider_registry


@pytest.fixture(scope="module")
def catalog():
    return mc.build_catalog()


# ── every offered card can be priced ────────────────────────────────────────

#: Roles whose models are billed PER TOKEN. Creative and voice are excluded on
#: purpose: an image or video model is priced per image or per second of output,
#: so a per-1k-token rate of 0 is correct for Veo, Nano Banana, Flux and Kling
#: rather than a missing figure. Scoping this to the token-billed roles was a
#: correction to this test, not a weakening of it -- the first version swept in
#: eleven creative models and would have demanded a meaningless number.
TOKEN_BILLED_ROLES = ("orchestrator", "subagent")


def test_every_cloud_card_in_a_role_picker_has_a_rate(catalog):
    """A card with no rate is a card that meters at $0. Local models are
    legitimately free; token-billed cloud ones never are."""
    missing = []
    for role in TOKEN_BILLED_ROLES:
        for e in catalog["roles"].get(role) or []:
            if e.get("local"):
                continue
            if e.get("cost_per_1k") is None:
                missing.append("%s (%s)" % (e["id"], role))
    assert not missing, "offered without a displayed rate: %s" % sorted(set(missing))


def test_every_cloud_card_prices_above_zero_in_the_meter(catalog):
    """`cost_per_1k` is what the picker DISPLAYS; `price_for` is what bills. A
    card can have one and not the other, which is how Fable 5.1 metered $0
    while looking fine."""
    zero = []
    for role in TOKEN_BILLED_ROLES:
        for e in catalog["roles"].get(role) or []:
            if e.get("local"):
                continue
            p = cm.price_for(e["id"])
            if p is None:
                continue          # deliberately unpriced is a separate contract
            if not (p["in"] > 0 and p["out"] > 0):
                zero.append(e["id"])
    assert not zero, "cloud cards that meter at $0: %s" % sorted(set(zero))


@pytest.mark.parametrize("mid,rates", [
    ("claude-opus-5-5", (4.00, 20.00)),
    ("claude-opus-5", (5.00, 25.00)),
    ("claude-sonnet-5", (2.00, 10.00)),
    ("claude-fable-5-1", (10.00, 50.00)),
    ("claude-haiku-4-5", (1.00, 5.00)),
    # Retired but still served, so still offered and still billable.
    ("claude-opus-4-8", (5.00, 25.00)),
    ("claude-sonnet-4-6", (3.00, 15.00)),
])
def test_anthropic_rates_match_the_published_page(mid, rates):
    want_in, want_out = rates
    p = cm.price_for(mid)
    assert p["in"] == pytest.approx(want_in / 1000.0), mid
    assert p["out"] == pytest.approx(want_out / 1000.0), mid


@pytest.mark.parametrize("pname,mid", [
    ("openai", "gpt-4o"), ("openai", "gpt-4o-mini"),
    ("anthropic", "claude-opus-5-5"), ("anthropic", "claude-sonnet-5"),
    ("anthropic", "claude-opus-4-8"), ("anthropic", "claude-sonnet-4-6"),
])
def test_the_displayed_rate_is_the_midpoint_of_the_billed_rate(pname, mid):
    """The picker's number and the meter's number must describe one price. The
    convention across this codebase is the midpoint of in/out."""
    prov = next(p for p in get_provider_registry().list_providers()
                if p.get("name") == pname)
    shown = (prov.get("cost_per_1k") or {}).get(mid)
    assert shown is not None, "%s has no displayed rate for %s" % (pname, mid)
    p = cm.price_for(mid)
    assert shown == pytest.approx((p["in"] + p["out"]) / 2.0), mid


# ── a card must not overstate the machine ───────────────────────────────────

def test_a_served_window_beats_a_planned_one(monkeypatch):
    """bonsai2:27b: declared 262,144, planned 65,536, served 49,152. The number
    a prompt is budgeted against has to be the served one, or the server
    rejects the request -- exactly how the e4b 400s happened."""
    monkeypatch.setattr(mc, "_served_context_window_uncached",
                        lambda mid: 49152 if mid == "bonsai2:27b" else None)
    from agent_friday.services import swr_cache
    swr_cache.invalidate("models:served_ctx")
    from agent_friday.services import machine_probe as mp
    mp.snapshot("models:served_ctx:bonsai2:27b",
                lambda: mc._served_context_window_uncached("bonsai2:27b"),
                fresh_for=60.0, budget=5.0)
    mc.reset_context_window_cache()
    assert mc.context_window_for("bonsai2:27b") == 49152


def test_an_absent_seat_falls_back_rather_than_reporting_zero(monkeypatch):
    """No seat up means no served answer, and the declared value must still be
    used. Returning 0 or None here would make compaction think the window was
    tiny."""
    monkeypatch.setattr(mc, "_served_context_window_uncached", lambda mid: None)
    from agent_friday.services import swr_cache
    swr_cache.invalidate("models:served_ctx")
    mc.reset_context_window_cache()
    got = mc.context_window_for("claude-opus-5-5")
    assert got == 1_000_000, got


def test_no_card_claims_ready_for_something_not_installed(catalog):
    """A local card is only `available` when something actually serves it."""
    for e in catalog["models"]:
        if not e.get("local") or not e.get("available"):
            continue
        # A live local row must name the thing serving it: a seat, residency, or
        # Friday's own store. `available` with none of those is a claim with
        # nothing behind it.
        assert (e.get("seat") or e.get("resident") or e.get("curated")), \
            "%s claims available with nothing serving it" % e["id"]


def test_the_kimi_k3_revert_left_nothing_behind(catalog):
    """95f3d21 was reverted by aa445d7. The only Kimi ids that may appear are
    OpenRouter's real hosted ones, never Friday's own experimental engine."""
    ours = [e["id"] for e in catalog["models"]
            if e.get("source") == "experimental-engine"
            or e.get("provider") == "kimi-k3-cli"]
    assert ours == [], "Kimi K3 residue in the catalog: %s" % ours
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agent_friday.services.kimi_k3")


def test_an_unreadable_engine_row_says_so_rather_than_claiming_ready(monkeypatch):
    """The cold path for the two torch-backed voice probes. Unknown must render
    as unavailable WITH a reason -- calling an unimportable package ready is the
    lie `kokoro_health` was written to prevent."""
    from agent_friday.services import swr_cache
    swr_cache.invalidate("voice:")
    monkeypatch.setattr(mc, "_kokoro_health_uncached",
                        lambda: (__import__("time").sleep(30), {})[1])
    rows = mc._tts_engines()
    kok = next(r for r in rows if r["id"] == "kokoro")
    assert kok["available"] is False
    assert kok.get("reading") == "unknown"
    assert "couldn't read" in (kok.get("hint") or "").lower()
    # Piper is on-device and always offered, so the list is never empty.
    assert any(r["id"] == "piper" and r["available"] for r in rows)
