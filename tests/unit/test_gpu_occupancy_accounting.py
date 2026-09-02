"""The arbiter must not plan into VRAM another tenant is holding.

Measured 2026-09-01: the arbiter believed 8,451 MiB were available while
nvidia-smi reported 11,557 of 12,282 MiB in use by a fine-tuning run. The WDDM
display-reserve counter read 18,376 MiB -- impossible on that card -- so it was
correctly discarded, and then incorrectly fell back to a cached IDLE floor of
2,807 MiB. Discarding a broken reading is right; landing on a number that
describes an empty card, when the reason you are here is that the card is full,
is not.
"""

import logging

from agent_friday.services import hardware_profile as hwp


CARD = 12282
IMPOSSIBLE = 18376          # what the WDDM counter reported
ACTUAL_USED = 11557         # what the device reported
CACHED_IDLE_FLOOR = 2807    # what it used to fall back to


def _profile(baseline=CACHED_IDLE_FLOOR):
    return {"os_family": "windows",
            "gpus": [{"index": 0, "name": "NVIDIA GeForce RTX 4070",
                      "vram_total_mib": CARD, "vram_baseline_mib": baseline}]}


def _fake_device(used):
    return lambda: [{"index": 0, "vram_total_mib": CARD, "vram_used_mib": used}]


def test_impossible_reading_is_still_rejected(monkeypatch):
    """The existing guard must survive: a broken counter is never trusted."""
    monkeypatch.setattr(hwp, "live_display_mib", lambda fam: IMPOSSIBLE)
    monkeypatch.setattr(hwp, "detect_gpus", _fake_device(ACTUAL_USED))
    g = hwp.refresh_display_reserve(_profile())["gpus"][0]
    assert g["vram_display_reserve_rejected"]["raw_mib"] == IMPOSSIBLE
    assert g.get("vram_display_reserve_mib") != IMPOSSIBLE


def test_rejection_falls_back_to_device_truth_not_a_stale_floor(monkeypatch):
    """The regression: 8,451 MiB of imaginary headroom."""
    monkeypatch.setattr(hwp, "live_display_mib", lambda fam: IMPOSSIBLE)
    monkeypatch.setattr(hwp, "detect_gpus", _fake_device(ACTUAL_USED))

    prof = hwp.refresh_display_reserve(_profile(), ours_resident_mib=0)
    g = prof["gpus"][0]
    baseline = hwp.effective_baseline_mib(g, "windows")

    assert baseline >= ACTUAL_USED, (
        "arbiter would budget against %d MiB while the card holds %d"
        % (baseline, ACTUAL_USED))
    assert baseline > CACHED_IDLE_FLOOR
    assert CARD - baseline < 1000, "must not invent multi-GB of headroom"


def test_our_own_seats_are_not_double_counted(monkeypatch):
    """`memory.used` includes us. Counting our seats as foreign would refuse
    every placement -- the failure detect_gpus' baseline comment warns about."""
    monkeypatch.setattr(hwp, "live_display_mib", lambda fam: IMPOSSIBLE)
    monkeypatch.setattr(hwp, "detect_gpus", _fake_device(ACTUAL_USED))

    ours = 4000
    g = hwp.refresh_display_reserve(_profile(), ours_resident_mib=ours)["gpus"][0]
    reserve = g.get("vram_display_reserve_mib")
    assert reserve == ACTUAL_USED - ours, (
        f"expected {ACTUAL_USED}-{ours} foreign, got {reserve}")
    # Without the subtraction the reserve would be the full 11,557 and the
    # arbiter would refuse to place anything at all, including our own seats.
    assert reserve < ACTUAL_USED


def test_floor_still_wins_when_foreign_is_smaller(monkeypatch):
    """Never return LESS than the cached floor -- being wrong downward here is
    what takes a monitor off the desktop."""
    monkeypatch.setattr(hwp, "live_display_mib", lambda fam: IMPOSSIBLE)
    monkeypatch.setattr(hwp, "detect_gpus", _fake_device(8000))

    g = hwp.refresh_display_reserve(_profile(), ours_resident_mib=6000)["gpus"][0]
    # foreign is 2,000 -- below the 2,807 floor, so the floor governs.
    assert hwp.effective_baseline_mib(g, "windows") == CACHED_IDLE_FLOOR


def test_unreadable_device_leaves_previous_behaviour(monkeypatch):
    """Strictly additive: a card we cannot read is no worse off than before."""
    monkeypatch.setattr(hwp, "live_display_mib", lambda fam: IMPOSSIBLE)
    monkeypatch.setattr(hwp, "detect_gpus", lambda: [])
    g = hwp.refresh_display_reserve(_profile())["gpus"][0]
    assert hwp.effective_baseline_mib(g, "windows") == CACHED_IDLE_FLOOR


def test_a_sane_reading_is_untouched(monkeypatch):
    monkeypatch.setattr(hwp, "live_display_mib", lambda fam: 2778)
    monkeypatch.setattr(hwp, "detect_gpus", _fake_device(3000))
    g = hwp.refresh_display_reserve(_profile(baseline=542))["gpus"][0]
    assert g["vram_display_reserve_mib"] == 2778
    assert "vram_display_reserve_rejected" not in g


# ── log-rate cap ────────────────────────────────────────────────────────────

def test_rejection_log_is_rate_capped(monkeypatch, caplog):
    """1,038 identical ERRORs in one day buried the lines that mattered."""
    monkeypatch.setattr(hwp, "live_display_mib", lambda fam: IMPOSSIBLE)
    monkeypatch.setattr(hwp, "detect_gpus", _fake_device(ACTUAL_USED))
    hwp._REJECTION_LOG_STATE.clear()

    with caplog.at_level(logging.ERROR, logger="friday.hardware_profile"):
        for _ in range(120):          # two hours of once-a-minute sampling
            hwp.refresh_display_reserve(_profile())

    errors = [r for r in caplog.records if "discarded as impossible" in r.getMessage()]
    assert len(errors) == 1, f"expected 1 capped ERROR, got {len(errors)}"


def test_suppressed_count_is_reported(monkeypatch, caplog):
    """Capping must not destroy the frequency -- say how many were dropped."""
    monkeypatch.setattr(hwp, "live_display_mib", lambda fam: IMPOSSIBLE)
    monkeypatch.setattr(hwp, "detect_gpus", _fake_device(ACTUAL_USED))
    hwp._REJECTION_LOG_STATE.clear()
    monkeypatch.setattr(hwp, "_REJECTION_LOG_INTERVAL_S", 0.0)

    with caplog.at_level(logging.ERROR, logger="friday.hardware_profile"):
        hwp.refresh_display_reserve(_profile())     # first: immediate
        hwp._REJECTION_LOG_STATE[0] = (1.0, 41)     # 41 suppressed since
        monkeypatch.setattr(hwp, "_REJECTION_LOG_INTERVAL_S", 0.0)
        hwp.refresh_display_reserve(_profile())

    assert any("41 identical readings suppressed" in r.getMessage()
               for r in caplog.records)
