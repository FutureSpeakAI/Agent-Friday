"""The sampled GPU meter and merging readings from two meters (NS-40.4-15, design C.4, PR 9)."""
import itertools

import pytest

from agent_friday.services import resource_gpu as gpu
from agent_friday.services import resource_vector as rv


def meter(interval=0.25):
    return gpu.GpuSampleMeter(reader=lambda: None, interval_s=interval)


def test_the_readings_are_estimates_that_name_their_limits():
    vector = meter().vector([(0.0, 1000, 10), (0.25, 4000, 50), (0.5, 3000, 100)])
    assert isinstance(vector.peak_device_memory_bytes, rv.Estimated) and isinstance(vector.gpu_active_ms, rv.Estimated)
    assert vector.peak_device_memory_bytes.value == 4000 * gpu.MIB
    for reading in (vector.peak_device_memory_bytes, vector.gpu_active_ms):
        assert "nvidia-smi" in reading.basis and "other programs" in reading.basis
    assert "shorter peak can be missed" in vector.peak_device_memory_bytes.basis
    assert not any(isinstance(getattr(vector, f), rv.Measured) for f in ("peak_device_memory_bytes", "gpu_active_ms"))


def test_active_time_is_utilisation_over_the_gaps():
    vector = meter().vector([(0.0, 100, 10), (0.25, 100, 50), (0.75, 100, 100)])
    assert vector.gpu_active_ms.value == round(0.10 * 250 + 0.50 * 500)
    idle = meter().vector([(0.0, 100, 0), (1.0, 100, 0)])
    assert idle.gpu_active_ms.value == 0, "a card that reported 0% really was idle: a reading, not an unknown"
    full = meter().vector([(0.0, 100, 100), (2.0, 100, 100)])
    assert full.gpu_active_ms.value == 2000


def test_an_unreadable_card_is_unavailable_never_zero():
    for samples in ([], [(0.0, None, 5)], [(0.0, None, None), (1.0, None, None)]):
        vector = meter().vector(samples)
        assert isinstance(vector.peak_device_memory_bytes, rv.Unavailable)
        assert isinstance(vector.gpu_active_ms, rv.Unavailable)
        assert b'"v":0' not in __import__("json").dumps(vector.to_wire()).encode().replace(b" ", b"")
    no_util = meter().vector([(0.0, 100, None), (1.0, 100, None)])
    assert isinstance(no_util.gpu_active_ms, rv.Unavailable) and no_util.peak_device_memory_bytes.value == 100 * gpu.MIB
    one = meter().vector([(0.0, 100, 50)])
    assert isinstance(one.gpu_active_ms, rv.Unavailable)


def test_energy_is_not_read():
    assert isinstance(meter().vector([(0.0, 1, 1), (1.0, 1, 1)]).energy_mwh, rv.Unavailable)
    source = open(gpu.__file__, encoding="utf-8").read()
    assert "power_w" not in source and "power_limit" not in source


def test_start_and_stop_sample_around_the_work_and_survive_a_failing_reader():
    ticks = itertools.count()
    rows = iter([{"used_mib": 100, "util_pct": 20}, {"used_mib": 900, "util_pct": 80}])
    calls = []

    def reader():
        calls.append(1)
        try:
            return next(rows)
        except StopIteration:
            raise RuntimeError("nvidia-smi went away")
    m = gpu.GpuSampleMeter(reader=reader, interval_s=0.01, clock=lambda: next(ticks) / 10)
    vector = m.stop(m.start())
    assert len(calls) >= 2
    assert vector.peak_device_memory_bytes.value == 900 * gpu.MIB


def test_a_reader_that_always_fails_gives_unavailable():
    def broken():
        raise OSError("no nvidia-smi")
    m = gpu.GpuSampleMeter(reader=broken, interval_s=0.01)
    vector = m.stop(m.start())
    assert isinstance(vector.peak_device_memory_bytes, rv.Unavailable)


def test_merging_two_meters_fills_each_field_once_and_never_adds():
    a = rv.ResourceVector(cpu_active_ms=rv.Measured(5, 0, "m", "c"))
    b = rv.ResourceVector(gpu_active_ms=rv.Estimated(7, "basis"))
    merged = a.merged(b)
    assert merged.cpu_active_ms.value == 5 and merged.gpu_active_ms.value == 7
    assert isinstance(merged.energy_mwh, rv.Unavailable)
    with pytest.raises(ValueError):
        a.merged(rv.ResourceVector(cpu_active_ms=rv.Measured(1, 0, "m2", "c")))
    assert b.merged(a) == merged


def test_a_real_read_of_this_machines_card_is_an_estimate_or_unavailable():
    m = gpu.GpuSampleMeter(interval_s=0.05)
    vector = m.stop(m.start())
    for reading in (vector.peak_device_memory_bytes, vector.gpu_active_ms):
        assert isinstance(reading, (rv.Estimated, rv.Unavailable))


# ── second-review notes (PR #917), written red first ───────────────────────────────────────────────

def test_the_basis_gives_the_real_span_not_the_configured_interval():
    vector = meter(interval=0.25).vector([(0.0, 1000, 10), (0.7, 4000, 50), (1.5, 3000, 100)])
    for reading in (vector.peak_device_memory_bytes, vector.gpu_active_ms):
        assert "1500 ms" in reading.basis and "250 ms apart" not in reading.basis, reading.basis


def test_the_basis_names_the_card_that_was_read_and_the_load_the_sampling_puts_on_it():
    rows = iter([{"index": 1, "name": "Test Card X", "used_mib": 100, "util_pct": 20},
                 {"index": 1, "name": "Test Card X", "used_mib": 900, "util_pct": 80}])
    m = gpu.GpuSampleMeter(reader=lambda: next(rows, None), interval_s=0.01)
    vector = m.stop(m.start())
    for reading in (vector.peak_device_memory_bytes, vector.gpu_active_ms):
        if isinstance(reading, rv.Estimated):
            assert "Test Card X" in reading.basis and "card 1" in reading.basis, reading.basis
            assert "nvidia-smi afresh" in reading.basis, "each sample is a call that loads the machine being measured"
