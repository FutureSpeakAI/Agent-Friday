"""A display reserve above half the card is a phantom and is never budgeted.

The reference machine's cached profile carried `vram_display_reserve_mib:
10401` on a 12,282 MiB card: a fallback had booked our own seat as "foreign
occupancy" into the display-reserve field, and because that field could only
ever be raised, no later reading could lower it. The planner was left 857 MiB
and refused every fit. Two rules fix it: the pure budget function ignores a
display reserve above MAX_DISPLAY_FRACTION of the card, and foreign occupancy
lives in its own field that every refresh REPLACES, so it is never sticky.
"""
from __future__ import annotations

from agent_friday.services import hardware_profile as hp

CARD = 12282
PHANTOM = 10401
FLOOR = 1412


def _gpu(**extra):
    g = {"index": 0, "vram_total_mib": CARD, "vram_baseline_mib": FLOOR}
    g.update(extra)
    return g


def test_a_phantom_reserve_is_not_adopted_by_the_budget():
    gpu = _gpu(vram_display_reserve_mib=PHANTOM)
    assert hp.effective_baseline_mib(gpu, "windows") == FLOOR


def test_a_plausible_reserve_still_is():
    gpu = _gpu(vram_display_reserve_mib=2778)
    assert hp.effective_baseline_mib(gpu, "windows") == 2778


def test_the_planner_budget_on_the_card_is_restored():
    gpu = _gpu(vram_display_reserve_mib=PHANTOM)
    assert CARD - hp.effective_baseline_mib(gpu, "windows") >= 8000


def test_a_refresh_heals_the_stored_phantom(monkeypatch):
    """The cached profile is repaired in place, not merely worked around."""
    monkeypatch.setattr(hp, "live_display_mib", lambda fam: 2600)
    prof = {"os_family": "windows", "gpus": [_gpu(vram_display_reserve_mib=PHANTOM)]}
    hp.refresh_display_reserve(prof)
    assert prof["gpus"][0]["vram_display_reserve_mib"] == 2600


def test_foreign_occupancy_is_replaced_every_refresh_never_ratcheted(monkeypatch):
    """A rejected counter reading falls back to device truth; when the tenant
    leaves, the next refresh says so instead of keeping the high-water mark."""
    monkeypatch.setattr(hp, "live_display_mib", lambda fam: 18376)
    prof = {"os_family": "windows", "gpus": [_gpu()]}

    monkeypatch.setattr(hp, "detect_gpus",
                        lambda: [{"index": 0, "vram_total_mib": CARD,
                                  "vram_used_mib": 11557}])
    hp.refresh_display_reserve(prof, ours_resident_mib=0)
    g = prof["gpus"][0]
    assert g["vram_foreign_mib"] == 11557
    assert hp.effective_baseline_mib(g, "windows") == 11557
    assert "vram_display_reserve_mib" not in g, "occupancy is not a display reserve"

    monkeypatch.setattr(hp, "detect_gpus",
                        lambda: [{"index": 0, "vram_total_mib": CARD,
                                  "vram_used_mib": 1300}])
    hp.refresh_display_reserve(prof, ours_resident_mib=0)
    assert g["vram_foreign_mib"] == 1300
    assert hp.effective_baseline_mib(g, "windows") == FLOOR


def test_a_sane_reading_clears_foreign_occupancy(monkeypatch):
    prof = {"os_family": "windows", "gpus": [_gpu(vram_foreign_mib=9000)]}
    monkeypatch.setattr(hp, "live_display_mib", lambda fam: 2600)
    hp.refresh_display_reserve(prof)
    assert "vram_foreign_mib" not in prof["gpus"][0]
    assert hp.effective_baseline_mib(prof["gpus"][0], "windows") == 2600
