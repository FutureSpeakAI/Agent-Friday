"""An idle baseline that holds a model is not the desktop.

The Arbiter measures the card's idle floor at boot net of the seats it keeps.
When a resident seat's footprint was not known, the brain was booked as the
desktop: a 10,324 MiB "idle" draw on a 12,282 MiB card, so the display reserve
became 10,836 MiB and the GPU voice mouth was refused with 8,294 MiB free
(local voice ran Kokoro on the CPU). The live display reading already has a
half-the-card ceiling; the baseline now has the same one, read and written.
"""
from __future__ import annotations

from agent_friday.services import hardware_profile as hwp

CARD = 12282


def test_a_baseline_above_half_the_card_falls_back_to_the_fixed_floor(monkeypatch):
    monkeypatch.setattr(hwp, "_display_reserve_mode", lambda: "adaptive")
    gpu = {"vram_baseline_mib": 10324, "vram_total_mib": CARD}
    assert hwp.display_reserve_floor_mib("windows", gpu) == hwp.MIN_DISPLAY_RESERVE_MIB["windows"]


def test_a_real_desktop_baseline_still_sets_the_adaptive_floor(monkeypatch):
    monkeypatch.setattr(hwp, "_display_reserve_mode", lambda: "adaptive")
    gpu = {"vram_baseline_mib": 1813, "vram_total_mib": CARD}
    assert hwp.display_reserve_floor_mib("windows", gpu) == max(
        hwp.ADAPTIVE_RESERVE_MIN_MIB["windows"], 1813 + hwp.ADAPTIVE_RESERVE_MARGIN_MIB)


def test_an_impossible_baseline_is_not_recorded(monkeypatch):
    saved = []
    monkeypatch.setattr(hwp, "detect_gpus", lambda: [
        {"index": 0, "vram_used_mib": 10324, "vram_total_mib": CARD}])
    monkeypatch.setattr(hwp, "save", lambda p: saved.append(p))
    profile = {"gpus": [{"index": 0, "vram_total_mib": CARD, "vram_baseline_mib": 1813}]}
    hwp.refresh_baseline(profile, assert_idle=True, ours_resident_mib=0)
    assert profile["gpus"][0]["vram_baseline_mib"] == 1813, "the previous floor is kept"


def test_a_possible_baseline_is_recorded_net_of_our_seats(monkeypatch):
    monkeypatch.setattr(hwp, "detect_gpus", lambda: [
        {"index": 0, "vram_used_mib": 10324, "vram_total_mib": CARD}])
    monkeypatch.setattr(hwp, "save", lambda p: None)
    profile = {"gpus": [{"index": 0, "vram_total_mib": CARD}]}
    hwp.refresh_baseline(profile, assert_idle=True, ours_resident_mib=8511)
    assert profile["gpus"][0]["vram_baseline_mib"] == 1813
