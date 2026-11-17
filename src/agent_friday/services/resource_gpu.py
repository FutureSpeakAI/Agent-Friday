"""A sampled reading of the graphics card around a piece of work (NS-40.4-15, design C.4 GpuMeter).

It reads what the tree already reads: `machine_monitor.gpu_rows`, the one place that calls
`nvidia-smi`. No new package (design decision R2: none was needed). It is a SAMPLER, so what it
returns is an estimate, never a measurement: peak device memory is the highest sampled value (a
shorter peak can be missed, and the figure is the whole card's, other programs included), and active
time is the sampled utilisation percentage summed over the gaps between samples. Both say so in
their basis text. Energy is not read here: that is decision R1 and stays Unavailable.

Rules and the tests that hold them (tests/unit/test_resource_gpu.py):
- The readings are Estimated and their basis names the limits, the real span, the card and the load:
  test_the_readings_are_estimates_that_name_their_limits, test_the_basis_gives_the_real_span_not_the_configured_interval,
  test_the_basis_names_the_card_that_was_read_and_the_load_the_sampling_puts_on_it.
- A card that cannot be read, or reports no utilisation, gives Unavailable, never 0:
  test_an_unreadable_card_is_unavailable_never_zero.
- Active time is utilisation over the gaps, in whole milliseconds: test_active_time_is_utilisation_over_the_gaps.
- Energy stays unavailable: test_energy_is_not_read.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from agent_friday.services.resource_vector import Estimated, ResourceVector, Unavailable

MIB = 1024 * 1024
NAME = "gpu/nvidia-smi-sampled"


def _default_reader():
    from agent_friday.services import machine_monitor
    rows = machine_monitor.gpu_rows(fresh=True)
    return rows[0] if rows else None


class GpuSampleMeter:
    name = NAME

    def __init__(self, reader: Callable[[], dict | None] = _default_reader, interval_s: float = 0.25,
                 clock: Callable[[], float] = time.monotonic):
        self._reader, self._interval, self._clock = reader, interval_s, clock
        self.card = None        # "card <index> <name>", from the first row the reader returned

    def _sample(self, samples: list):
        try:
            row = self._reader()
        except Exception:
            row = None
        if row is not None:
            if self.card is None:
                self.card = f"card {row.get('index', 0)} {row.get('name', 'unknown')}"
            samples.append((self._clock(), row.get("used_mib"), row.get("util_pct")))

    def start(self):
        samples, stop = [], threading.Event()
        self._sample(samples)

        def poll():
            while not stop.wait(self._interval):
                self._sample(samples)
        thread = threading.Thread(target=poll, daemon=True)
        thread.start()
        return samples, stop, thread

    def stop(self, token) -> ResourceVector:
        samples, stop, thread = token
        stop.set()
        thread.join(timeout=5)
        self._sample(samples)
        return self.vector(samples)

    def vector(self, samples: list) -> ResourceVector:
        """The vector for a list of (time, used MiB, utilisation %) samples."""
        span = int(round((samples[-1][0] - samples[0][0]) * 1000)) if samples else 0
        how = (f"{len(samples)} samples over {span} ms from {self.card or 'the first card'} (each sample starts "
               "nvidia-smi afresh, which loads the machine being measured); whole card, other programs included")
        used = [u for _, u, _ in samples if type(u) is int]
        if not samples or not used:
            why = Unavailable("nvidia-smi could not be read")
            return ResourceVector(gpu_active_ms=why, peak_device_memory_bytes=why)
        peak = Estimated(max(used) * MIB, f"highest of {how}; a shorter peak can be missed")
        if len(samples) < 2 or any(type(u) is not int for _, _, u in samples):
            return ResourceVector(peak_device_memory_bytes=peak,
                                  gpu_active_ms=Unavailable("utilisation was not reported or only one sample was taken"))
        active = 0.0
        for (t0, _, util0), (t1, _, _) in zip(samples, samples[1:]):
            active += util0 / 100.0 * (t1 - t0) * 1000.0
        return ResourceVector(peak_device_memory_bytes=peak, gpu_active_ms=Estimated(
            int(round(active)), f"sampled utilisation over the gaps between {how}"))
