"""Meters, calibration and the ledger entry for resource readings (NS-40.4-15, design Part C.4-C.5).

Nothing here touches money. Fields a meter cannot read are Unavailable, never 0. Energy is not
measured in this slice (design decision R1): the meter says so. A sampled power estimate, if one
is ever built, must be its own named meter with a stated uncertainty.

Rules and the tests that hold them (tests/unit/test_resource_measure.py):
- A field the meter cannot read is Unavailable: test_a_meter_that_reads_nothing_gives_unavailable_never_zero.
- Calibration median and variation are exact integers: test_calibration_takes_the_median_and_states_the_spread.
- A reading is one chained ledger entry with no content: test_a_measured_receipt_is_one_chained_ledger_entry.
- Recording readings creates no balance: test_recording_readings_leaves_every_journal_byte_identical.
"""
from __future__ import annotations

import statistics
import time
from typing import Protocol

from agent_friday.services import action_ledger
from agent_friday.services.resource_vector import (
    Calibration, Measured, MeasuredReceipt, ResourceVector, Unavailable)

KIND = "resource_measured"
PROCESS_METER = "process/psutil"


class Meter(Protocol):
    name: str

    def start(self): ...

    def stop(self, token) -> ResourceVector: ...


class NoEnergyMeter:
    """Energy is not measured here (decision R1)."""
    name = "energy/none"

    def start(self):
        return None

    def stop(self, token) -> ResourceVector:
        return ResourceVector(energy_mwh=Unavailable("no meter"))


class ProcessMeter:
    """This process's CPU time, peak memory, bytes read and written, and wall time."""
    name = PROCESS_METER
    calibration = "none"

    def __init__(self, process=None):
        import psutil
        self._process = process or psutil.Process()

    def start(self):
        p = self._process
        cpu = p.cpu_times()
        try:
            io = p.io_counters()
            io_start = (io.read_bytes, io.write_bytes)
        except (AttributeError, OSError):
            io_start = None
        return (time.perf_counter_ns(), cpu.user + cpu.system, io_start)

    def _measured(self, value: int):
        return Measured(max(0, int(value)), 0, self.name, self.calibration)

    def stop(self, token) -> ResourceVector:
        began, cpu_start, io_start = token
        p = self._process
        cpu = p.cpu_times()
        memory = p.memory_info()
        peak = getattr(memory, "peak_wset", None) or memory.rss
        try:
            io = p.io_counters()
            io_end = (io.read_bytes, io.write_bytes)
        except (AttributeError, OSError):
            io_end = None
        no_io = Unavailable("this system does not report process I/O")
        return ResourceVector(
            cpu_active_ms=self._measured(round((cpu.user + cpu.system - cpu_start) * 1000)),
            peak_host_memory_bytes=self._measured(peak),
            bytes_read=self._measured(io_end[0] - io_start[0]) if io_start and io_end else no_io,
            bytes_written=self._measured(io_end[1] - io_start[1]) if io_start and io_end else no_io,
            duration_ms=self._measured((time.perf_counter_ns() - began) // 1_000_000),
            gpu_active_ms=Unavailable("no meter"), peak_device_memory_bytes=Unavailable("no meter"),
            bytes_transferred=Unavailable("no meter"), energy_mwh=Unavailable("no meter"))


def calibrate(profile: str, benchmark_id: str, config_digest: str, device_active_ms, meter: str,
              calibrated_at: str) -> Calibration:
    """The median of whole-millisecond runs, and the spread as whole permille of the median."""
    runs = list(device_active_ms)
    if not runs or any(type(r) is not int or r < 1 for r in runs):
        raise ValueError("calibration needs whole-millisecond runs of at least 1")
    median = statistics.median_low(runs)
    return Calibration(profile, benchmark_id, config_digest, len(runs), median,
                       (max(runs) - min(runs)) * 1000 // median, meter, calibrated_at)


def record_measured(receipt: MeasuredReceipt) -> dict:
    """One chained, signed ledger entry; no content. Raises LedgerError when it cannot be written."""
    if not isinstance(receipt, MeasuredReceipt):
        raise ValueError("only a MeasuredReceipt is recorded")
    return action_ledger.append(KIND, receipt.to_wire())
