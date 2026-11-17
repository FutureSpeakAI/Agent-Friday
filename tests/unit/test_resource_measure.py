"""Meters, calibration and the ledger entry for resource readings (NS-40.4-15). No money moves."""
import time
from types import SimpleNamespace

import pytest

from agent_friday.services import action_ledger as L
from agent_friday.services import issuer_journal as ij
from agent_friday.services import resource_measure as rm
from agent_friday.services import resource_vector as rv

HASH = "cd" * 32


class FakeProcess:
    def __init__(self, io=True, memory=None):
        self.cpu = 1.0
        self.io = io
        self.reads = 100
        self.memory = memory or SimpleNamespace(rss=5000)

    def cpu_times(self):
        return SimpleNamespace(user=self.cpu, system=0.5)

    def memory_info(self):
        return self.memory

    def io_counters(self):
        if not self.io:
            raise AttributeError("no io counters here")
        return SimpleNamespace(read_bytes=self.reads, write_bytes=7)


def test_a_meter_that_reads_nothing_gives_unavailable_never_zero():
    process = FakeProcess(io=False)
    meter = rm.ProcessMeter(process)
    token = meter.start()
    vector = meter.stop(token)
    assert isinstance(vector.bytes_read, rv.Unavailable) and isinstance(vector.bytes_written, rv.Unavailable)
    for field in ("gpu_active_ms", "peak_device_memory_bytes", "bytes_transferred", "energy_mwh"):
        assert isinstance(getattr(vector, field), rv.Unavailable), field
    wire = vector.to_wire()
    assert wire["energy_mwh"] == {"unavailable": "no meter"} and "v" not in wire["bytes_read"]


def test_the_process_meter_reports_differences_in_whole_numbers():
    process = FakeProcess()
    meter = rm.ProcessMeter(process)
    token = meter.start()
    process.cpu = 2.25
    process.reads = 160
    vector = meter.stop(token)
    assert vector.cpu_active_ms.value == 1250
    assert vector.bytes_read.value == 60 and vector.bytes_written.value == 0
    assert vector.peak_host_memory_bytes.value == 5000
    assert vector.cpu_active_ms.meter == rm.PROCESS_METER and vector.cpu_active_ms.calibration == "none"


def test_a_peak_working_set_is_preferred_when_the_system_reports_one():
    process = FakeProcess(memory=SimpleNamespace(rss=5000, peak_wset=9000))
    meter = rm.ProcessMeter(process)
    assert meter.stop(meter.start()).peak_host_memory_bytes.value == 9000


def test_the_real_process_meter_sees_real_time_pass():
    meter = rm.ProcessMeter()
    token = meter.start()
    time.sleep(0.05)
    sum(i * i for i in range(200_000))
    vector = meter.stop(token)
    assert vector.duration_ms.value >= 50 and vector.cpu_active_ms.value >= 0
    assert vector.peak_host_memory_bytes.value > 0


def test_the_energy_meter_says_it_has_nothing():
    meter = rm.NoEnergyMeter()
    assert meter.stop(meter.start()).energy_mwh == rv.Unavailable("no meter")


def test_calibration_takes_the_median_and_states_the_spread():
    cal = rm.calibrate("N1:a", "bench", "cfg", [4000, 4100, 3900, 4000, 5000], "process/psutil", "2026-11-16T00:00:00Z")
    assert cal.median_device_active_ms == 4000 and cal.runs == 5
    assert cal.variation_permille == (5000 - 3900) * 1000 // 4000
    for bad in ([], [0], [1.5], [True], [3, -1]):
        with pytest.raises(ValueError):
            rm.calibrate("N1:a", "bench", "cfg", bad, "m", "2026-11-16T00:00:00Z")


def _receipt(task="t1"):
    return rv.MeasuredReceipt(quote_hash=HASH, task_id=task, profile="N1:a",
                              vector=rv.ResourceVector(duration_ms=rv.Measured(5, 0, "m", "none")),
                              n1=rv.N1Quantity("N1:a", 5, 2), status="measured")


def test_a_measured_receipt_is_one_chained_ledger_entry(friday_dir):
    before = len(L.entries(None))
    entry = rm.record_measured(_receipt())
    assert entry["kind"] == rm.KIND and len(L.entries(None)) == before + 1
    assert entry["rec"]["quote_hash"] == HASH and entry["rec"]["n1"] == {"n": 5, "d": 2}
    assert L.verify(force=True)["ok"] is True
    assert not {"prompt", "result", "message", "text", "content"} & set(entry["rec"])


def test_only_a_receipt_object_is_recorded(friday_dir):
    for bad in ({"quote_hash": HASH}, None, "x"):
        with pytest.raises(ValueError):
            rm.record_measured(bad)


def test_recording_readings_leaves_every_journal_byte_identical(tmp_path, monkeypatch):
    path = tmp_path / "j.sqlite"
    journal = ij.IssuerJournal(path, "test.one", 1000)
    journal.execute({"issuer": "test.one", "effect": "e1", "action": "a1", "postings": {"treasury": -5, "a": 5}})
    before = path.read_bytes()
    seen = []
    monkeypatch.setattr(L, "append", lambda kind, rec, **kw: seen.append((kind, rec)) or {"kind": kind, "rec": rec})
    for i in range(1000):
        rm.record_measured(_receipt(task=f"t{i}"))
    assert len(seen) == 1000 and path.read_bytes() == before
    assert journal.balances() == {"treasury": 995, "a": 5}
