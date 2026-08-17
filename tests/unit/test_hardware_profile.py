"""HardwareProfile — the detector the residency policy plans against (D4).

The load-bearing test here is multi-GPU parsing. `nvidia-smi --query-gpu`
prints one line PER GPU; `routing/ollama_manager.detect_hardware` splits the
whole blob on "," and reads parts[0]/parts[1], so on a two-GPU host it reads
one name and one VRAM figure and silently discards every other device. Fixture
P4 (asymmetric 24 GB + 12 GB) cannot be planned at all under that bug.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import hardware_profile as hp

TWO_GPU = (
    "0, NVIDIA GeForce RTX 4090, 24564, 812, 610.88\n"
    "1, NVIDIA GeForce RTX 4070, 12282, 1261, 610.88\n"
)
ONE_GPU = "0, NVIDIA GeForce RTX 4070, 12282, 1261, 610.88\n"


@pytest.fixture
def smi(monkeypatch):
    """Drive detect_gpus from canned nvidia-smi output."""
    def _set(text):
        monkeypatch.setattr(hp, "_run", lambda *a, **k: text)
    return _set


# ── multi-GPU: the regression this module exists to fix ──────────────────────

def test_every_gpu_is_parsed_not_just_the_first(smi):
    smi(TWO_GPU)
    gpus = hp.detect_gpus()
    assert len(gpus) == 2, "one line per GPU — both must survive parsing"


def test_asymmetric_pair_keeps_its_own_vram_per_device(smi):
    """The whole point of P4: the two cards are NOT interchangeable."""
    smi(TWO_GPU)
    by_index = {g["index"]: g for g in hp.detect_gpus()}
    assert by_index[0]["vram_total_mib"] == 24564
    assert by_index[1]["vram_total_mib"] == 12282


def test_gpus_are_returned_in_index_order(smi):
    smi("1, NVIDIA GeForce RTX 4070, 12282, 1261, 610.88\n"
        "0, NVIDIA GeForce RTX 4090, 24564, 812, 610.88\n")
    assert [g["index"] for g in hp.detect_gpus()] == [0, 1]


def test_the_old_comma_split_would_have_lost_a_gpu():
    """Documents the defect, so a refactor cannot quietly reintroduce it.

    This is exactly what ollama_manager.detect_hardware:199 does.
    """
    parts = TWO_GPU.strip().split(",")
    assert parts[0].strip() == "0"
    # The "VRAM" it would read is the *name* of GPU 0, not a number at all.
    with pytest.raises(ValueError):
        int(parts[1].strip())


# ── degenerate hosts ─────────────────────────────────────────────────────────

def test_no_gpu_yields_an_empty_list_not_a_crash(smi):
    """Fixture P5 is CPU-only; detection must simply report zero devices."""
    smi("")
    assert hp.detect_gpus() == []


def test_malformed_lines_are_skipped_not_fatal(smi):
    smi("garbage\n" + ONE_GPU + "0, incomplete\n")
    gpus = hp.detect_gpus()
    assert len(gpus) == 1
    assert gpus[0]["vram_total_mib"] == 12282


# ── the idle floor must not be poisoned by a live model ──────────────────────

def test_detection_reports_live_usage_not_a_baseline(smi):
    """`memory.used` mid-load is not an idle floor.

    Caught live: detecting while a model was resident recorded a 'baseline' of
    11120 MiB on a 12282 MiB card. Budgeting against that leaves ~1 GB and
    refuses every placement — and it gets cached, so it stays wrong.
    """
    smi(ONE_GPU)
    g = hp.detect_gpus()[0]
    assert g["vram_used_mib"] == 1261
    assert g["vram_baseline_mib"] is None, "a floor is never inferred from a moment"


def test_baseline_is_refused_without_an_idle_assertion(smi, monkeypatch,
                                                       tmp_path):
    monkeypatch.setattr(hp, "cache_path", lambda: tmp_path / "hw.json")
    smi(ONE_GPU)
    p = hp.detect()
    hp.refresh_baseline(p)                       # no assert_idle
    assert p["gpus"][0]["vram_baseline_mib"] is None


def test_baseline_is_recorded_when_the_caller_asserts_idle(smi, monkeypatch,
                                                           tmp_path):
    """The Arbiter calls this at boot, before it loads anything."""
    monkeypatch.setattr(hp, "cache_path", lambda: tmp_path / "hw.json")
    smi(ONE_GPU)
    p = hp.refresh_baseline(hp.detect(), assert_idle=True)
    assert p["gpus"][0]["vram_baseline_mib"] == 1261
    assert p["gpus"][0]["vram_baseline_at"]


def test_unmeasured_floor_falls_back_to_a_conservative_default():
    """Under-reserving overcommits the card and fails at load, not at plan."""
    gpu = {"vram_baseline_mib": None}
    assert hp.effective_baseline_mib(gpu, "windows") == 1024
    assert hp.effective_baseline_mib(gpu, "linux") == 256


def test_measured_floor_beats_the_default():
    assert hp.effective_baseline_mib({"vram_baseline_mib": 1261},
                                     "windows") == 1261


def test_a_measured_floor_survives_re_detection(smi, monkeypatch, tmp_path):
    """Re-detecting mid-load must not destroy a good baseline."""
    monkeypatch.setattr(hp, "cache_path", lambda: tmp_path / "hw.json")
    smi(ONE_GPU)
    hp.refresh_baseline(hp.get(force=True), assert_idle=True)
    # now a model is resident: 9000 MiB in use
    smi("0, NVIDIA GeForce RTX 4070, 12282, 9000, 610.88\n")
    again = hp.get(force=True)
    assert again["gpus"][0]["vram_baseline_mib"] == 1261
    assert again["gpus"][0]["vram_used_mib"] == 9000


def test_get_is_memoised_because_detection_is_expensive(smi, monkeypatch,
                                                        tmp_path):
    """detect_cpu() spawns PowerShell and detect_gpus() spawns nvidia-smi, so a
    bare get() costs seconds on Windows. `_pick_local_model` consults the
    profile on EVERY routing decision — measured at 4.6s per call before this
    memo existed, which made model selection the slowest step in a turn.

    The trade is explicit and is why the test above now passes force=True:
    within the TTL you get a slightly stale snapshot. Callers that need live
    VRAM call detect_gpus() directly, as the arbiter does.
    """
    monkeypatch.setattr(hp, "cache_path", lambda: tmp_path / "hw.json")
    hp._MEMO["profile"] = None
    calls = {"n": 0}

    def counting_run(*a, **k):
        calls["n"] += 1
        return ONE_GPU

    monkeypatch.setattr(hp, "_run", counting_run)
    hp.get(force=True)
    first = calls["n"]
    for _ in range(20):
        hp.get()
    assert calls["n"] == first, "20 further get() calls must not re-detect"
    hp.get(force=True)
    assert calls["n"] > first, "force=True still re-detects"


# ── compute class ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("NVIDIA GeForce RTX 4070", "consumer-fp8"),
    ("NVIDIA GeForce RTX 3090", "consumer-bf16"),
    ("NVIDIA H100 PCIe", "datacenter-fp8"),
    ("Some Unknown Accelerator", "unknown"),
])
def test_compute_class_buckets(name, expected):
    """Z-Image ships an FP8 build; whether it loads is a class question."""
    assert hp._compute_class(name) == expected


# ── profile identity ─────────────────────────────────────────────────────────

def _profile(**over):
    base = dict(
        os_d={"family": "windows"},
        cpu={"threads": 16, "physical_cores": 8, "model": "i7-10700F"},
        ram={"total_mib": 32620, "available_mib": 3460},
        gpus=[{"index": 0, "name": "RTX 4070", "vram_total_mib": 12282}],
        mem={"class": "ddr4"},
    )
    base.update(over)
    return hp._profile_id(base["os_d"], base["cpu"], base["ram"],
                          base["gpus"], base["mem"])


def test_profile_id_is_stable_across_volatile_readings():
    """Free RAM moves every second; it must not invalidate the cache."""
    a = _profile()
    b = _profile(ram={"total_mib": 32620, "available_mib": 9999})
    assert a == b


def test_profile_id_changes_when_a_gpu_is_added():
    a = _profile()
    b = _profile(gpus=[
        {"index": 0, "name": "RTX 4070", "vram_total_mib": 12282},
        {"index": 1, "name": "RTX 4090", "vram_total_mib": 24564}])
    assert a != b, "a new card is a different machine for placement purposes"


def test_profile_id_changes_when_ram_capacity_changes():
    assert _profile() != _profile(ram={"total_mib": 65536,
                                       "available_mib": 3460})


# ── OS reserve (policy rule R1) ──────────────────────────────────────────────

def test_windows_reserves_more_than_linux():
    """R1: 6 GB Windows, 4 GB Linux. Windows pages destructively sooner."""
    assert hp.OS_RESERVE_MIB["windows"] == 6144
    assert hp.OS_RESERVE_MIB["linux"] == 4096
    assert hp.OS_RESERVE_MIB["windows"] > hp.OS_RESERVE_MIB["linux"]


# ── method provenance ────────────────────────────────────────────────────────

def test_bandwidth_estimate_records_its_method():
    """A number without its method cannot later be distrusted correctly."""
    mem = hp.detect_memory_bandwidth()
    assert mem["method"] in {"heuristic-smbios", "declared-platform"}
    assert "class" in mem


def test_unavailable_disk_rate_is_reported_not_faked(tmp_path):
    out = hp.measure_disk_read_mib_s(sample_path=tmp_path / "nope.bin")
    assert out["read_mib_s"] is None
    assert out["method"] == "unavailable"


def test_disk_rate_is_measured_from_a_real_file(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\0" * (2 * 1024 * 1024))
    out = hp.measure_disk_read_mib_s(sample_path=blob,
                                     target_bytes=1024 * 1024)
    assert out["read_mib_s"] > 0
    assert out["method"] == "sequential-blob-read-warm"


# ── serialization ────────────────────────────────────────────────────────────

def test_profile_round_trips_as_json(monkeypatch, tmp_path):
    monkeypatch.setattr(hp, "cache_path", lambda: tmp_path / "hw.json")
    monkeypatch.setattr(hp, "_run", lambda *a, **k: ONE_GPU)
    p = hp.detect()
    hp.save(p)
    assert json.loads((tmp_path / "hw.json").read_text()) == p


def test_save_is_atomic_leaving_no_partial_file(monkeypatch, tmp_path):
    monkeypatch.setattr(hp, "cache_path", lambda: tmp_path / "hw.json")
    monkeypatch.setattr(hp, "_run", lambda *a, **k: ONE_GPU)
    hp.save(hp.detect())
    assert not list(tmp_path.glob("*.tmp"))


# ── the display reserve ─────────────────────────────────────────────────────
# Regression cover for 2026-08-17: the arbiter budgeted 542 MiB for a desktop
# whose compositor actually held 2,778 MiB, planned seats into memory Windows
# needed to draw the screen, and a monitor dropped off. The floor is now the
# larger of the cached idle measurement and a sampled live display draw.

def test_a_live_display_draw_beats_a_stale_idle_floor(monkeypatch):
    monkeypatch.setattr(hp, "live_display_mib", lambda fam: 2778)
    prof = {"os_family": "windows",
            "gpus": [{"index": 0, "vram_baseline_mib": 542}]}
    hp.refresh_display_reserve(prof)
    gpu = prof["gpus"][0]
    assert gpu["vram_display_reserve_mib"] == 2778
    assert hp.effective_baseline_mib(gpu, "windows") == 2778


def test_the_reserve_never_lowers_the_cached_floor(monkeypatch):
    """Being wrong downward breaks screens; wrong upward only costs a seat."""
    monkeypatch.setattr(hp, "live_display_mib", lambda fam: 800)
    prof = {"os_family": "windows",
            "gpus": [{"index": 0, "vram_baseline_mib": 3000}]}
    hp.refresh_display_reserve(prof)
    assert hp.effective_baseline_mib(prof["gpus"][0], "windows") == 3000


def test_a_machine_that_cannot_sample_is_no_worse_off(monkeypatch):
    monkeypatch.setattr(hp, "live_display_mib", lambda fam: None)
    prof = {"os_family": "linux",
            "gpus": [{"index": 0, "vram_baseline_mib": 256}]}
    hp.refresh_display_reserve(prof)
    gpu = prof["gpus"][0]
    assert "vram_display_reserve_mib" not in gpu
    assert hp.effective_baseline_mib(gpu, "linux") == 256


def test_planning_stays_pure_no_probe_from_the_baseline_call(monkeypatch):
    """effective_baseline_mib must never shell out -- golden plans replay."""
    def _boom(fam):
        raise AssertionError("effective_baseline_mib probed the machine")
    monkeypatch.setattr(hp, "live_display_mib", _boom)
    assert hp.effective_baseline_mib({"vram_baseline_mib": 542}, "windows") == 542


def test_the_live_probe_is_windows_only(monkeypatch):
    monkeypatch.setattr(hp, "_DISPLAY_CACHE", (0.0, None))
    assert hp.live_display_mib("linux") is None
    assert hp.live_display_mib("darwin") is None
