"""A cached live model list must not hide a model we ship.

Anthropic is a hosted-native provider: when `hosted_catalog` has cached the
provider's own /v1/models response, the catalog builder preferred that list and
DROPPED the shipped statics. The cache has a 24-hour TTL.

So on the day Claude Opus 5.5 was added to the shipped list, the picker did not
offer it. The cached list predated the release, was not yet stale, and won. The
static entry's METADATA still applied -- the corrected Sonnet 5 rate appeared
immediately -- which made the missing id look like a typo rather than a cache
deciding what the product offers. Measured 2026-09-22: the cache held 11 ids,
including `claude-fable-5-1`, and no `claude-opus-5-5`.

Discovery still leads, because it is live truth and carries the long tail. A
statically shipped id is our own claim that the model exists, so it is appended
rather than dropped.
"""

import pytest

from agent_friday.services import model_catalog as mc


@pytest.fixture
def seeded(monkeypatch):
    """A cache that predates Opus 5.5, exactly as the real one did."""
    stale_live = [
        {"id": "claude-sonnet-5"},
        {"id": "claude-opus-5"},
        {"id": "claude-fable-5"},
        {"id": "claude-opus-4-8"},          # the live API still serves retired ids
    ]
    monkeypatch.setattr(mc, "_discovered_models",
                        lambda provider: (stale_live, False))
    mc.reset_context_window_cache()
    return stale_live


def _ids(entries):
    return [e.get("id") for e in entries]


def test_a_shipped_model_appears_even_when_the_cache_predates_it(seeded):
    from agent_friday.services.provider_registry import get_provider_registry
    prov = next(p for p in get_provider_registry().list_providers()
                if p.get("name") == "anthropic")
    entries = mc._model_entries_for(prov, get_provider_registry())
    ids = _ids(entries)
    assert "claude-opus-5-5" in ids, (
        "a model in the shipped list was hidden by a cached /v1/models "
        "response that predates it")


def test_discovery_still_leads_and_keeps_its_long_tail(seeded):
    """The fix must not demote live truth or drop ids only discovery knows."""
    from agent_friday.services.provider_registry import get_provider_registry
    prov = next(p for p in get_provider_registry().list_providers()
                if p.get("name") == "anthropic")
    ids = _ids(mc._model_entries_for(prov, get_provider_registry()))
    # an id ONLY the live list knows about survives
    assert "claude-opus-4-8" in ids
    # and the live list's own ordering comes first
    assert ids.index("claude-sonnet-5") < ids.index("claude-opus-5-5")


def test_no_duplicate_entries_when_an_id_is_in_both(seeded):
    from agent_friday.services.provider_registry import get_provider_registry
    prov = next(p for p in get_provider_registry().list_providers()
                if p.get("name") == "anthropic")
    ids = _ids(mc._model_entries_for(prov, get_provider_registry()))
    for mid in ("claude-opus-5", "claude-sonnet-5", "claude-opus-5-5"):
        assert ids.count(mid) == 1, "%s appears %d times" % (mid, ids.count(mid))


# ── declared context windows ────────────────────────────────────────────────

@pytest.mark.parametrize("mid,window", [
    ("claude-opus-5-5", 1_000_000),
    ("claude-opus-5", 1_000_000),
    ("claude-sonnet-5", 1_000_000),
    ("claude-haiku-4-5-20251001", 200_000),
])
def test_declared_context_windows_are_known(mid, window):
    """None means "the catalog has no value", and the context layer then falls
    back to a documented constant. For the DEFAULT model that meant its 1M
    window was never actually known to compaction or the router."""
    mc.reset_context_window_cache()
    assert mc.context_window_for(mid) == window
