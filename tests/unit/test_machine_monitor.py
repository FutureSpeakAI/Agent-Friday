"""machine_monitor — sample() and verdict() (docs/design/headroom.md §4.3).

The load-bearing test is the thrash signature against the REPORTED figures
from spec §3: a training run held 11,928 of 12,282 MiB on the reference RTX
4070, drew 51 of 200 W while reporting 100% utilisation, and step time went
from ~7s to ~57s. That is degradation, not refusal -- every existing refusal
rule in `residency_policy` would have PLACED that run; nothing until this
module notices it happening. A healthy-load fixture proves the signature
does not fire on ordinary high utilisation (a legitimately busy card at high
power looks nothing like this).

D1 (the full three-level Headroom Contract: working/away/yield, VRAM slack,
RAM-available floor) is not decided and not built here -- see
`machine_monitor.py`'s own docstring. `verdict()`'s `vram_slack` and
`ram_available` rows are pinned to `basis: "unknown"` throughout; nothing
here invents a floor to make every row read `ok` (HR1).
"""
from __future__ import annotations

import threading
import time

import pytest

from agent_friday.services import gpu_headroom as gh
from agent_friday.services import hardware_profile as hwp
from agent_friday.services import headroom_contract as hc
from agent_friday.services import liveness_audit as la
from agent_friday.services import machine_monitor as mm

# ── nvidia-smi row fixtures, as CSV `_run` would return ─────────────────────

# §3, REPORTED: the exact figures from Stephen's week. free_mib = 12282 -
# 11928 = 354, the margin §3.2 calls "less than the KV cache of a small
# context bump".
REPORTED_ROW = ("0, NVIDIA GeForce RTX 4070, 12282, 11928, 354, "
                "100, 51.00, 200.00, 2505\n")

# Ordinary high load: busy AND drawing real power -- nothing like the
# reported shape. Must NOT fire the signature.
HEALTHY_BUSY_ROW = ("0, NVIDIA GeForce RTX 4070, 12282, 9000, 3282, "
                    "95, 178.00, 200.00, 2505\n")

# Quiet idle card.
IDLE_ROW = ("0, NVIDIA GeForce RTX 4070, 12282, 1365, 10917, "
           "8, 30.32, 200.00, 2505\n")

NO_GPU = ""


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Every cache and rolling window here is process-global; a test must
    not see a prior test's reading (the same discipline
    test_headroom_contract.py's `_reset_cache` fixture applies)."""
    mm.reset_gpu_cache_for_tests()
    mm.reset_wddm_cache_for_tests()
    mm.reset_thrash_history_for_tests()
    mm.reset_verdict_log_for_tests()
    mm.reset_last_sample_for_tests()
    hc._CACHE.update({"ts": 0.0, "os_family": None, "result": None})
    yield
    mm.reset_gpu_cache_for_tests()
    mm.reset_wddm_cache_for_tests()
    mm.reset_thrash_history_for_tests()
    mm.reset_verdict_log_for_tests()
    mm.reset_last_sample_for_tests()
    hc._CACHE.update({"ts": 0.0, "os_family": None, "result": None})


def _smi(monkeypatch, text):
    monkeypatch.setattr(mm, "_run", lambda *a, **k: text)


def _no_wddm(monkeypatch):
    """The WDDM probe shells out to PowerShell; tests never should."""
    monkeypatch.setattr(mm, "_wddm_shared_mib", lambda: None)


# ── gpu_rows(): the one nvidia-smi call, extended ───────────────────────────

def test_gpu_rows_parses_every_field(monkeypatch):
    _smi(monkeypatch, REPORTED_ROW)
    rows = mm.gpu_rows()
    assert rows == [{
        "index": 0, "name": "NVIDIA GeForce RTX 4070",
        "total_mib": 12282, "used_mib": 11928, "free_mib": 354,
        "util_pct": 100, "power_w": 51.0, "power_limit_w": 200.0,
        "sm_mhz": 2505,
    }]


def test_gpu_rows_none_when_nvidia_smi_unavailable(monkeypatch):
    _smi(monkeypatch, NO_GPU)
    assert mm.gpu_rows() is None


def test_gpu_rows_tolerates_na_on_the_optional_fields(monkeypatch):
    """Some drivers/virtualised cards don't report power or clocks. A row
    missing those fields is still a row, with None for what could not be
    read -- never a crash, never a 0 that reads as "no draw"."""
    _smi(monkeypatch, "0, Some Card, 8192, 1000, 7192, [N/A], [N/A], [N/A], [N/A]\n")
    rows = mm.gpu_rows()
    assert rows[0]["total_mib"] == 8192
    assert rows[0]["util_pct"] is None
    assert rows[0]["power_w"] is None


def test_gpu_rows_cached_briefly(monkeypatch):
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return REPORTED_ROW
    monkeypatch.setattr(mm, "_run", counting)
    mm.gpu_rows()
    mm.gpu_rows()
    assert calls["n"] == 1
    mm.gpu_rows(fresh=True)
    assert calls["n"] == 2


# ── gpu_headroom now reads machine_monitor, not a second nvidia-smi call ────

def test_gpu_headroom_reads_machine_monitor_rows(monkeypatch):
    gh._CACHE.update({"ts": 0.0, "data": None})
    _smi(monkeypatch, REPORTED_ROW)
    result = gh.gpu_memory()
    assert result == [{"name": "NVIDIA GeForce RTX 4070", "total_mib": 12282,
                       "used_mib": 11928, "free_mib": 354}]


def test_gpu_headroom_none_when_monitor_has_nothing(monkeypatch):
    gh._CACHE.update({"ts": 0.0, "data": None})
    _smi(monkeypatch, NO_GPU)
    assert gh.gpu_memory() is None


# ── sample() ─────────────────────────────────────────────────────────────

def test_sample_with_no_gpu_is_clean_not_a_crash(monkeypatch):
    _smi(monkeypatch, NO_GPU)
    _no_wddm(monkeypatch)
    monkeypatch.setattr(mm, "_ram_available_mib", lambda: 8000)
    monkeypatch.setattr(mm, "_disk_system_free_mib", lambda: 70000)
    s = mm.sample()
    assert s["gpus"] == []
    assert s["foreign_vram_mib"] is None
    assert s["ram_available_mib"] == 8000
    assert s["disk_system_free_mib"] == 70000
    assert "ts" in s


def test_sample_computes_foreign_vram_from_used_minus_ours(monkeypatch):
    _smi(monkeypatch, REPORTED_ROW)          # used_mib = 11928
    _no_wddm(monkeypatch)
    s = mm.sample(ours_resident_mib=3081)
    assert s["gpus"][0]["used_mib"] == 11928
    assert s["foreign_vram_mib"] == 11928 - 3081


def test_sample_a_field_that_cannot_be_read_is_none_not_zero(monkeypatch):
    """gpu_headroom's own rule, restated here: an unreadable field reports
    'cannot verify', never a value that would be read as 'plenty' or
    'nothing'."""
    _smi(monkeypatch, REPORTED_ROW)
    _no_wddm(monkeypatch)
    monkeypatch.setattr(mm, "_ram_available_mib", lambda: None)
    monkeypatch.setattr(mm, "_disk_system_free_mib", lambda: None)
    s = mm.sample()
    assert s["ram_available_mib"] is None
    assert s["disk_system_free_mib"] is None


# ── the WDDM Shared Usage field: displayed, never gates (U4) ────────────────

def test_wddm_field_is_present_but_inert(monkeypatch):
    """A fixture asserting the field surfaces in the sample and plays no
    part in any verdict, matching §4.3's explicit statement that it is
    displayed only until U4 is settled."""
    _smi(monkeypatch, HEALTHY_BUSY_ROW)
    monkeypatch.setattr(mm, "_wddm_shared_mib", lambda: 4096)
    monkeypatch.setattr(mm, "_ram_available_mib", lambda: 8000)
    monkeypatch.setattr(mm, "_disk_system_free_mib", lambda: 70000)
    monkeypatch.setattr(hwp, "get", lambda: {"os": {"family": "windows"}})
    monkeypatch.setattr(hc, "resolve_display_reserve",
                        lambda profile: {"mib": 2560, "basis": "floor_clamp"})
    s = mm.sample()
    assert s["wddm_shared_mib"] == 4096
    v = mm.verdict(s)
    # Present in the sample; absent from every verdict's own arithmetic.
    for row in v.values():
        assert "wddm" not in str(row).lower()
    assert v["display"]["status"] == "ok"       # unaffected by the 4096 value


def test_wddm_none_when_it_cannot_be_read(monkeypatch):
    _smi(monkeypatch, IDLE_ROW)
    _no_wddm(monkeypatch)
    s = mm.sample()
    assert s["wddm_shared_mib"] is None


# ── thrash signature: the load-bearing test ─────────────────────────────────

def test_thrash_signature_breaches_on_the_reported_shape():
    row = {"gpus": [{"used_mib": 11928, "util_pct": 100, "power_w": 51.0,
                    "power_limit_w": 200.0}]}
    result = mm.thrash_signature([row, row, row])
    assert result["status"] == "breached"
    assert result["util_power_signal"] is True


def test_thrash_signature_ok_on_healthy_high_load():
    """Busy AND drawing real power -- the proxy must not fire on ordinary
    contention, only on the specific low-power-at-high-utilisation shape."""
    row = {"gpus": [{"used_mib": 9000, "util_pct": 95, "power_w": 178.0,
                    "power_limit_w": 200.0}]}
    result = mm.thrash_signature([row, row, row])
    assert result["status"] == "ok"
    assert result["util_power_signal"] is False


def test_thrash_signature_ok_on_an_idle_card():
    row = {"gpus": [{"used_mib": 1365, "util_pct": 8, "power_w": 30.0,
                    "power_limit_w": 200.0}]}
    result = mm.thrash_signature([row, row, row])
    assert result["status"] == "ok"


def test_thrash_needs_three_consecutive_samples_not_one():
    row = {"gpus": [{"used_mib": 11928, "util_pct": 100, "power_w": 51.0,
                    "power_limit_w": 200.0}]}
    assert mm.thrash_signature([row])["status"] == "ok"
    assert mm.thrash_signature([row, row])["status"] == "ok"
    assert mm.thrash_signature([row, row, row])["status"] == "breached"


def test_thrash_needs_every_field_present_or_it_is_no_signal():
    """A card that can't report power must not be treated as thrashing --
    that would fire on hardware this module cannot actually judge."""
    row = {"gpus": [{"used_mib": 11928, "util_pct": 100, "power_w": None,
                    "power_limit_w": 200.0}]}
    assert mm.thrash_signature([row, row, row])["status"] == "ok"


def test_thrash_latency_signal_alone_is_at_risk():
    row = {"gpus": [{"used_mib": 9000, "util_pct": 95, "power_w": 178.0,
                    "power_limit_w": 200.0}]}
    result = mm.thrash_signature([row, row, row], latency_ratio=6.0)
    assert result["status"] == "at_risk"
    assert result["latency_signal"] is True


def test_thrash_below_latency_threshold_is_no_signal():
    row = {"gpus": [{"used_mib": 9000, "util_pct": 95, "power_w": 178.0,
                    "power_limit_w": 200.0}]}
    result = mm.thrash_signature([row, row, row], latency_ratio=2.0)
    assert result["status"] == "ok"
    assert result["latency_signal"] is False


def test_thrash_both_signals_together_breach():
    row = {"gpus": [{"used_mib": 11928, "util_pct": 100, "power_w": 51.0,
                    "power_limit_w": 200.0}]}
    result = mm.thrash_signature([row, row, row], latency_ratio=7.0)
    assert result["status"] == "breached"


# ── verdict() wired to the REPORTED and healthy fixtures via sample() ───────

def test_verdict_thrash_breaches_after_three_ticks_of_the_reported_shape(
        monkeypatch):
    _smi(monkeypatch, REPORTED_ROW)
    _no_wddm(monkeypatch)
    monkeypatch.setattr(hwp, "get", lambda: {"os": {"family": "windows"}})
    monkeypatch.setattr(hc, "resolve_display_reserve",
                        lambda profile: {"mib": 2560, "basis": "floor_clamp"})
    s = mm.sample()
    v1 = mm.verdict(s)
    v2 = mm.verdict(s)
    v3 = mm.verdict(s)
    assert v1["thrash"]["status"] == "ok"
    assert v2["thrash"]["status"] == "ok"
    assert v3["thrash"]["status"] == "breached"


def test_verdict_thrash_does_not_fire_on_healthy_load(monkeypatch):
    _smi(monkeypatch, HEALTHY_BUSY_ROW)
    _no_wddm(monkeypatch)
    monkeypatch.setattr(hwp, "get", lambda: {"os": {"family": "windows"}})
    monkeypatch.setattr(hc, "resolve_display_reserve",
                        lambda profile: {"mib": 2560, "basis": "floor_clamp"})
    s = mm.sample()
    for _ in range(5):
        v = mm.verdict(s)
    assert v["thrash"]["status"] == "ok"


# ── verdict(): display reserve (decided) ────────────────────────────────────

def test_display_verdict_breached_below_the_reconciled_reserve(monkeypatch):
    monkeypatch.setattr(hwp, "get", lambda: {"os": {"family": "windows"}})
    monkeypatch.setattr(hc, "resolve_display_reserve",
                        lambda profile: {"mib": 2560, "basis": "floor_clamp"})
    s = {"gpus": [{"free_mib": 354, "used_mib": 11928}],
        "ram_available_mib": 8000, "disk_system_free_mib": 70000,
        "foreign_vram_mib": None, "wddm_shared_mib": None}
    v = mm.verdict(s)
    assert v["display"]["status"] == "breached"
    assert v["display"]["reserve_mib"] == 2560
    assert v["display"]["basis"] == "measured"


def test_display_verdict_ok_with_room_to_spare(monkeypatch):
    monkeypatch.setattr(hwp, "get", lambda: {"os": {"family": "windows"}})
    monkeypatch.setattr(hc, "resolve_display_reserve",
                        lambda profile: {"mib": 2560, "basis": "floor_clamp"})
    s = {"gpus": [{"free_mib": 8000, "used_mib": 4000}],
        "ram_available_mib": 8000, "disk_system_free_mib": 70000,
        "foreign_vram_mib": None, "wddm_shared_mib": None}
    v = mm.verdict(s)
    assert v["display"]["status"] == "ok"


def test_display_verdict_unknown_with_no_gpu():
    s = {"gpus": [], "ram_available_mib": 8000,
        "disk_system_free_mib": 70000, "foreign_vram_mib": None,
        "wddm_shared_mib": None}
    v = mm.verdict(s)
    assert v["display"]["status"] == "unknown"
    assert v["display"]["basis"] == "unknown"


# ── verdict(): disk on the system volume (decided, HR5) ─────────────────────

def test_disk_system_verdict_breached_below_the_r8_floor():
    from agent_friday.services.residency_policy import DISK_FLOOR_MIB
    s = {"gpus": [], "ram_available_mib": 8000,
        "disk_system_free_mib": DISK_FLOOR_MIB - 1, "foreign_vram_mib": None,
        "wddm_shared_mib": None}
    v = mm.verdict(s)
    assert v["disk_system"]["status"] == "breached"
    assert v["disk_system"]["floor_mib"] == DISK_FLOOR_MIB


def test_disk_system_verdict_ok_with_plenty_free():
    s = {"gpus": [], "ram_available_mib": 8000,
        "disk_system_free_mib": 70000, "foreign_vram_mib": None,
        "wddm_shared_mib": None}
    v = mm.verdict(s)
    assert v["disk_system"]["status"] == "ok"


def test_disk_system_verdict_unknown_when_unreadable():
    s = {"gpus": [], "ram_available_mib": 8000,
        "disk_system_free_mib": None, "foreign_vram_mib": None,
        "wddm_shared_mib": None}
    v = mm.verdict(s)
    assert v["disk_system"]["status"] == "unknown"


# ── verdict(): D1-gated rows never guess a floor (HR1) ───────────────────────

def test_vram_slack_and_ram_available_are_always_unknown_pending_d1(
        monkeypatch):
    """Every input, including a machine with plenty of everything, must not
    produce a guessed 'ok' on these two rows -- that would be exactly the
    invented-D1-number this phase was told not to build."""
    monkeypatch.setattr(hwp, "get", lambda: {"os": {"family": "windows"}})
    monkeypatch.setattr(hc, "resolve_display_reserve",
                        lambda profile: {"mib": 2560, "basis": "floor_clamp"})
    s = {"gpus": [{"free_mib": 8000, "used_mib": 1000}],
        "ram_available_mib": 30000, "disk_system_free_mib": 500000,
        "foreign_vram_mib": None, "wddm_shared_mib": None}
    v = mm.verdict(s)
    assert v["vram_slack"] == {
        "status": "unknown", "basis": "unknown",
        "explanation": v["vram_slack"]["explanation"],
    }
    assert v["ram_available"]["status"] == "unknown"
    assert v["ram_available"]["basis"] == "unknown"
    assert v["vram_slack"]["status"] == "unknown"
    assert v["vram_slack"]["basis"] == "unknown"


def test_every_verdict_row_carries_a_basis(monkeypatch):
    """HR1: a verdict with no basis is not a verdict."""
    monkeypatch.setattr(hwp, "get", lambda: {"os": {"family": "windows"}})
    monkeypatch.setattr(hc, "resolve_display_reserve",
                        lambda profile: {"mib": 2560, "basis": "floor_clamp"})
    s = {"gpus": [{"free_mib": 8000, "used_mib": 1000}],
        "ram_available_mib": 30000, "disk_system_free_mib": 500000,
        "foreign_vram_mib": None, "wddm_shared_mib": None}
    v = mm.verdict(s)
    for resource, row in v.items():
        assert "basis" in row, resource
        assert row["basis"] in ("measured", "derived", "declared", "unknown")


# ── liveness: RAN / PRODUCED / CONSUMED ─────────────────────────────────────

def test_liveness_empty_when_no_sample_taken():
    mm.reset_last_sample_for_tests()
    findings = la._probe_machine_monitor()
    assert findings[0]["status"] == la.EMPTY
    assert findings[0]["ran"] is False


def test_liveness_orphaned_when_a_gpu_sample_exists_but_nothing_reads_it(
        monkeypatch):
    """PRODUCED is true (a real GPU row); CONSUMED is false because Phase 3
    (the chain boundary check) does not exist yet. ORPHANED is the expected
    reading right now, not a bug -- and it stops being silently 'ok'."""
    monkeypatch.setattr(hwp, "get",
                        lambda: {"gpus": [{"index": 0}]})
    mm.set_last_sample({"ts": time.time(),
                        "gpus": [{"index": 0, "free_mib": 3000}]})
    findings = la._probe_machine_monitor()
    assert findings[0]["ran"] is True
    assert findings[0]["produced"] is True
    assert findings[0]["consumed"] is False
    assert findings[0]["status"] == la.ORPHANED


def test_liveness_stale_reading_reads_empty(monkeypatch):
    mm.set_last_sample({"ts": time.time() - 999, "gpus": []})
    findings = la._probe_machine_monitor()
    assert findings[0]["status"] == la.EMPTY
    assert findings[0]["ran"] is False


# ── the loop: cadence tightens under a lease ─────────────────────────────────

def test_cadence_is_60s_at_rest_with_no_arbiter(monkeypatch):
    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.get_arbiter", lambda: None)
    assert mm._cadence_s() == 60.0


def test_cadence_is_5s_under_a_held_lease(monkeypatch):
    class _FakeArbiter:
        lease = {"kind": "heavy_turn"}
    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.get_arbiter",
        lambda: _FakeArbiter())
    assert mm._cadence_s() == 5.0


def test_loop_ticks_at_boot_then_stops_cleanly(monkeypatch):
    """The loop samples immediately (not only after the first interval) and
    exits promptly once `stop_event` is set -- a hung monitor thread must
    never outlive the test process."""
    _smi(monkeypatch, IDLE_ROW)
    _no_wddm(monkeypatch)
    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.get_arbiter", lambda: None)
    monkeypatch.setattr(hwp, "get", lambda: {"os": {"family": "windows"}})
    monkeypatch.setattr(hc, "resolve_display_reserve",
                        lambda profile: {"mib": 2560, "basis": "floor_clamp"})
    ev = threading.Event()
    t = threading.Thread(target=mm.loop, args=(ev,), daemon=True)
    t.start()
    # The boot-time tick happens before the first wait(); give it a moment.
    deadline = time.time() + 5.0
    while mm.last_sample() is None and time.time() < deadline:
        time.sleep(0.05)
    assert mm.last_sample() is not None
    ev.set()
    t.join(timeout=5.0)
    assert not t.is_alive()
