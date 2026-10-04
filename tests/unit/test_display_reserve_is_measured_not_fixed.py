"""The desktop's VRAM reserve is measured, not a fixed 2,560 MiB.

A fixed Windows reserve of 2,560 MiB is 31% of an 8 GB card: it is what keeps
a 5,950 MiB brain off a card whose desktop actually draws 900 MiB. Once the
Arbiter has measured the idle draw (`vram_baseline_mib`, taken at boot before
anything of ours is resident), the desktop is granted that draw plus a
512 MiB margin, never under the OS family's unmeasured default (1 GiB on
Windows). A machine with no measurement keeps the fixed minimum, and the
`display_reserve_mode: fixed` setting pins it everywhere.
"""
from __future__ import annotations

import pytest

from agent_friday.services import hardware_profile as hp
from agent_friday.services import headroom_contract as hc


@pytest.fixture(autouse=True)
def _adaptive(monkeypatch):
    monkeypatch.setattr(hp, "_display_reserve_mode", lambda: "adaptive")
    hc._CACHE.update({"ts": 0.0, "os_family": None, "result": None})
    yield
    hc._CACHE.update({"ts": 0.0, "os_family": None, "result": None})


def test_a_measured_idle_draw_sets_the_floor():
    gpu = {"vram_total_mib": 8188, "vram_baseline_mib": 900}
    assert hp.display_reserve_floor_mib("windows", gpu) == 1412


def test_the_floor_never_drops_under_one_gib_on_windows():
    gpu = {"vram_total_mib": 8188, "vram_baseline_mib": 100}
    assert hp.display_reserve_floor_mib("windows", gpu) == 1024


def test_an_unmeasured_machine_keeps_the_fixed_minimum():
    assert hp.display_reserve_floor_mib("windows", {"vram_baseline_mib": None}) == 2560
    assert hp.display_reserve_floor_mib("windows", None) == 2560


def test_a_two_display_desktop_is_granted_more_than_the_old_constant():
    """Measured goes both ways: 2,778 MiB of compositor earns 3,290."""
    gpu = {"vram_total_mib": 12282, "vram_baseline_mib": 2778}
    assert hp.display_reserve_floor_mib("windows", gpu) == 3290


def test_fixed_mode_pins_the_old_constant(monkeypatch):
    monkeypatch.setattr(hp, "_display_reserve_mode", lambda: "fixed")
    gpu = {"vram_total_mib": 8188, "vram_baseline_mib": 900}
    assert hp.display_reserve_floor_mib("windows", gpu) == 2560


def test_the_gate_reserve_follows_the_measured_floor(monkeypatch):
    """headroom_contract is the one figure every gate reads; it moves too."""
    monkeypatch.setattr(hp, "display_reserve_mib", lambda *a, **k: 256)
    prof = {"os": {"family": "windows"},
            "gpus": [{"index": 0, "vram_total_mib": 8188, "vram_baseline_mib": 900}]}
    out = hc.resolve_display_reserve(prof)
    assert out["mib"] == 1412
    assert out["basis"] == "floor_clamp"
    assert out["sources"]["min_display_reserve_mib"] == 1412


def test_the_gate_cache_does_not_serve_one_cards_floor_to_another(monkeypatch):
    monkeypatch.setattr(hp, "display_reserve_mib", lambda *a, **k: 256)
    small = {"os": {"family": "windows"},
             "gpus": [{"index": 0, "vram_total_mib": 8188, "vram_baseline_mib": 900}]}
    unmeasured = {"os": {"family": "windows"}, "gpus": [{"index": 0}]}
    assert hc.resolve_display_reserve(small)["mib"] == 1412
    assert hc.resolve_display_reserve(unmeasured)["mib"] == 2560


def test_a_live_reading_is_clamped_to_the_measured_floor_not_2560(monkeypatch):
    """refresh_display_reserve books what the desktop draws, not the constant."""
    monkeypatch.setattr(hp, "_DISPLAY_CACHE", (0.0, None))
    monkeypatch.setattr(hp, "live_display_mib", lambda fam: 1100)
    prof = {"os_family": "windows",
            "gpus": [{"index": 0, "vram_total_mib": 8188, "vram_baseline_mib": 900}]}
    hp.refresh_display_reserve(prof)
    gpu = prof["gpus"][0]
    assert gpu["vram_display_reserve_mib"] == 1412
    assert hp.effective_baseline_mib(gpu, "windows") == 1412


def test_the_planner_gains_the_difference_on_an_8gb_card():
    """The fit this exists for: 8,188 - 1,412 leaves room for a 5,950 MiB
    brain plus KV; 8,188 - 2,560 did not."""
    gpu = {"vram_total_mib": 8188, "vram_baseline_mib": 900}
    usable = 8188 - hp.display_reserve_floor_mib("windows", gpu)
    assert usable >= 5950 + 600
    assert 8188 - 2560 < 5950
