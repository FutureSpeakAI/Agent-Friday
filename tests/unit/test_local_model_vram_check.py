"""Local model selection stops ranking by artifact size (D6 + the live defect).

The heuristic replaced here ranked candidates by artifact size, which on the
reference instance is inverted: gemma4:e2b is a 7.2 GB artifact occupying
1763 MiB of VRAM while gemma4:e4b is 9.6 GB occupying 3081 MiB. And when the
configured local_model was not installed — as `gemma3:4b` is not — the
CODE/RESEARCH branch fell through to "largest artifact wins", selecting the one
model on the box guaranteed to spill onto the CPU.
"""
from __future__ import annotations

import pytest

from agent_friday.routing.model_router import ModelRouter, TaskType

INSTALLED = [
    {"name": "gemma4:e2b", "size_gb": 7.2},
    {"name": "gemma4:e4b", "size_gb": 9.6},
    {"name": "gemma4:12b", "size_gb": 7.6},
    {"name": "gemma4:26b", "size_gb": 17.0},
]
VRAM = {"gemma4:e2b": 1763, "gemma4:e4b": 3081,
        "gemma4:12b": 8001, "gemma4:26b": 17391}


@pytest.fixture
def router(monkeypatch):
    r = ModelRouter.__new__(ModelRouter)
    r.config = {}
    r.last_local_refusal = None
    # 12282 MiB card, 1261 MiB compositor -> 9997 MiB budget
    monkeypatch.setattr(r, "_vram_fit",
                        lambda names: ([n for n in names
                                        if VRAM.get(n, 0) <= 9997],
                                       9997,
                                       {n: VRAM[n] for n in names
                                        if n in VRAM}))
    monkeypatch.setattr(
        "agent_friday.services.residency_catalog.installed_entries",
        lambda p: (_ for _ in ()).throw(RuntimeError("no plan in this test")))
    return r


# ── the live defect ──────────────────────────────────────────────────────────

def test_uninstalled_configured_model_no_longer_selects_the_biggest(router):
    """capability_routing.local / model_routing.local_model point at
    gemma3:4b, which is not installed. The old fallthrough picked gemma4:26b —
    17391 MiB against a 9997 MiB budget."""
    router.config = {"local_model": "gemma3:4b"}
    got = router._pick_local_model(INSTALLED, TaskType.CODE, "local_preferred")
    assert got != "gemma4:26b"
    assert VRAM[got] <= 9997


def test_the_uninstalled_pointer_produces_an_explained_refusal(router):
    router.config = {"local_model": "gemma3:4b"}
    router._pick_local_model(INSTALLED, TaskType.CODE, "local_preferred")
    ref = router.last_local_refusal
    assert ref is not None
    assert "gemma3:4b" in ref["explanation"]
    assert "not installed" in ref["explanation"]


def test_a_model_that_does_not_fit_is_refused_with_both_numbers(router):
    router.config = {"local_model": "gemma4:26b"}
    got = router._pick_local_model(INSTALLED, TaskType.CODE, "local_preferred")
    ref = router.last_local_refusal
    assert got != "gemma4:26b"
    assert "17391" in ref["explanation"] and "9997" in ref["explanation"]
    assert ref["rule_id"] == "R3"


def test_an_installed_model_that_fits_is_honoured(router):
    router.config = {"local_model": "gemma4:12b"}
    assert router._pick_local_model(
        INSTALLED, TaskType.CODE, "local_preferred") == "gemma4:12b"
    assert router.last_local_refusal is None


# ── D6: preferred_model is wired, and outranks the global setting ────────────

def test_workspace_preferred_model_outranks_the_global_setting(router):
    router.config = {"local_model": "gemma4:12b"}
    got = router._pick_local_model(INSTALLED, TaskType.CODE,
                                   "local_preferred", preferred="gemma4:e4b")
    assert got == "gemma4:e4b"


def test_a_bad_preferred_model_falls_back_but_says_why(router):
    """Never a silent ignore — that is what made preferred_model dead."""
    router.config = {"local_model": "gemma4:12b"}
    got = router._pick_local_model(INSTALLED, TaskType.CODE,
                                   "local_preferred", preferred="llama9:70b")
    assert got == "gemma4:12b", "falls through to the global setting"
    assert "llama9:70b" in router.last_local_refusal["explanation"]


# ── never refuse on missing data ─────────────────────────────────────────────

def test_unknown_hardware_does_not_filter_anything(monkeypatch):
    """fits=None means 'do not filter', not 'nothing fits'."""
    r = ModelRouter.__new__(ModelRouter)
    r.config = {"local_model": "gemma4:26b"}
    r.last_local_refusal = None
    monkeypatch.setattr(r, "_vram_fit", lambda names: (None, 0, {}))
    monkeypatch.setattr(
        "agent_friday.services.residency_catalog.installed_entries",
        lambda p: (_ for _ in ()).throw(RuntimeError("no plan")))
    assert r._pick_local_model(
        INSTALLED, TaskType.CODE, "local_preferred") == "gemma4:26b"


def test_an_unmeasured_model_is_still_selectable(router):
    """A newly pulled model has no measurements; it must not be excluded."""
    pool = INSTALLED + [{"name": "brand-new:7b", "size_gb": 5.0}]
    router.config = {"local_model": "brand-new:7b"}
    assert router._pick_local_model(
        pool, TaskType.CODE, "local_preferred") == "brand-new:7b"


# ── selection stays deterministic ────────────────────────────────────────────

def test_selection_is_stable_under_input_reordering(router):
    a = router._pick_local_model(INSTALLED, TaskType.SIMPLE, "local_preferred")
    b = router._pick_local_model(list(reversed(INSTALLED)), TaskType.SIMPLE,
                                 "local_preferred")
    assert a == b


def test_simple_tasks_prefer_the_cheapest_fitting_model(router):
    got = router._pick_local_model(INSTALLED, TaskType.SIMPLE,
                                   "local_preferred")
    assert VRAM[got] <= 9997
