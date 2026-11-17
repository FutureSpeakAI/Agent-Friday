"""Resource readings: what a piece of work used on one declared machine setup (NS-40.4-15; design:
program/specs/DESIGN_economy_issuer_exchange_resource_2026-11-16.md, Part C).

A reading is NOT money. It earns nothing, sets no price and changes no balance. This module is
pure: it imports no money module and no money module imports it. It never calls a value a
currency; the calibrated number is an "N1 quantity".

Every value is a whole integer (milliseconds, bytes, milliwatt-hours), so a record fits the
canonical wire form, which has no floats.

Rules and the tests that hold them (tests/unit/test_resource_vector.py):
- C-1 a measured receipt cannot exist without its quote's hash:
  test_a_receipt_cannot_be_built_without_a_quote_hash.
- C-2 an unknown value is Unavailable, never a 0:
  test_an_unmeasured_field_is_unavailable_and_the_record_has_no_zero_there.
- C-3 N1 is job device-active time over the calibrated median, exact:
  test_the_bundle_negatron_vector_is_exactly_five_halves.
- C-4 unlike profiles are never summed or compared; CPU and GPU time are never added:
  test_n1_quantities_combine_only_within_one_profile, test_nothing_adds_gpu_time_to_cpu_time.
- C-5 no link to money, both ways: test_resource_and_money_modules_import_nothing_of_each_other.
- C-7 a reading under another configuration has no N1: test_another_configuration_gets_no_n1.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from fractions import Fraction

from agent_friday.services import peer_wire_v2 as wire

QUOTE_PREFIX = b"asimov-resource-quote/v1" + bytes([0])
_STATUSES = ("measured", "uncalibrated", "failed")
_HEX64_CHARS = set("0123456789abcdef")


def _whole(value, what, low=0):
    if type(value) is not int or value < low:
        raise ValueError(f"{what} must be a whole number")
    return value


def _text(value, what):
    if not isinstance(value, str) or not value or len(value) > 120 or not value.isascii() or not value.isprintable():
        raise ValueError(f"{what} must be short printable ASCII text")
    return value


@dataclass(frozen=True)
class Measured:
    value: int
    uncertainty: int
    meter: str
    calibration: str

    def __post_init__(self):
        _whole(self.value, "value")
        _whole(self.uncertainty, "uncertainty")
        _text(self.meter, "meter")
        _text(self.calibration, "calibration")

    def to_wire(self) -> dict:
        return {"v": self.value, "u": self.uncertainty, "meter": self.meter, "cal": self.calibration}


@dataclass(frozen=True)
class Estimated:
    value: int
    basis: str

    def __post_init__(self):
        _whole(self.value, "value")
        _text(self.basis, "basis")

    def to_wire(self) -> dict:
        return {"v": self.value, "basis": self.basis}


@dataclass(frozen=True)
class Unavailable:
    reason: str

    def __post_init__(self):
        _text(self.reason, "reason")

    def to_wire(self) -> dict:
        return {"unavailable": self.reason}


Reading = (Measured, Estimated, Unavailable)
NOT_MEASURED = Unavailable("not measured")


@dataclass(frozen=True)
class ResourceVector:
    """Nine independent readings. There is deliberately no total: unlike things are not summed."""
    gpu_active_ms: object = NOT_MEASURED
    cpu_active_ms: object = NOT_MEASURED
    peak_host_memory_bytes: object = NOT_MEASURED
    peak_device_memory_bytes: object = NOT_MEASURED
    bytes_read: object = NOT_MEASURED
    bytes_written: object = NOT_MEASURED
    bytes_transferred: object = NOT_MEASURED
    duration_ms: object = NOT_MEASURED
    energy_mwh: object = NOT_MEASURED

    def __post_init__(self):
        for f in fields(self):
            if not isinstance(getattr(self, f.name), Reading):
                raise ValueError(f"{f.name} must be a Measured, Estimated or Unavailable reading")

    def to_wire(self) -> dict:
        return {f.name: getattr(self, f.name).to_wire() for f in fields(self)}

    def merged(self, other: "ResourceVector") -> "ResourceVector":
        """One vector from two meters that read different things. A field both meters filled is refused,
        never added or averaged: unlike readings are not combined (C-4)."""
        out = {}
        for f in fields(self):
            mine, theirs = getattr(self, f.name), getattr(other, f.name)
            if not isinstance(mine, Unavailable) and not isinstance(theirs, Unavailable):
                raise ValueError(f"{f.name} was read by both meters")
            out[f.name] = theirs if isinstance(mine, Unavailable) else mine
        return ResourceVector(**out)


class MixedProfiles(ArithmeticError):
    """N1 quantities of two profiles were added or compared (C-4)."""


@dataclass(frozen=True)
class N1Quantity:
    profile: str
    numerator: int
    denominator: int

    def __post_init__(self):
        _text(self.profile, "profile")
        _whole(self.numerator, "numerator")
        _whole(self.denominator, "denominator", low=1)
        whole = Fraction(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", whole.numerator)
        object.__setattr__(self, "denominator", whole.denominator)

    @property
    def exact(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def _same(self, other):
        if not isinstance(other, N1Quantity):
            return None
        if other.profile != self.profile:
            raise MixedProfiles("quantities of two profiles are never combined or compared")
        return other.exact

    def __add__(self, other):
        exact = self._same(other)
        if exact is None:
            return NotImplemented
        total = self.exact + exact
        return N1Quantity(self.profile, total.numerator, total.denominator)

    def __lt__(self, other):
        exact = self._same(other)
        return NotImplemented if exact is None else self.exact < exact

    def __le__(self, other):
        exact = self._same(other)
        return NotImplemented if exact is None else self.exact <= exact

    def __gt__(self, other):
        exact = self._same(other)
        return NotImplemented if exact is None else self.exact > exact

    def __ge__(self, other):
        exact = self._same(other)
        return NotImplemented if exact is None else self.exact >= exact

    def shown(self, places: int) -> str:
        """A decimal that names its precision, e.g. '2.5 N1 (to 1 place)'."""
        scaled = (self.exact * 10 ** places).__round__()
        text = f"{scaled // 10 ** places}.{str(scaled % 10 ** places).zfill(places)}" if places else str(scaled)
        return f"{text} N1 (to {places} place{'s' if places != 1 else ''})"


@dataclass(frozen=True)
class Calibration:
    profile: str
    benchmark_id: str
    config_digest: str
    runs: int
    median_device_active_ms: int
    variation_permille: int
    meter: str
    calibrated_at: str

    def __post_init__(self):
        for name in ("profile", "benchmark_id", "config_digest", "meter", "calibrated_at"):
            _text(getattr(self, name), name)
        _whole(self.runs, "runs", low=1)
        _whole(self.median_device_active_ms, "median_device_active_ms", low=1)
        _whole(self.variation_permille, "variation_permille")

    def to_wire(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def n1_for(calibration: Calibration, config_digest: str, job_device_active_ms: int):
    """Job device-active time over the profile's calibrated median, exact; None under another
    configuration, because a calibration belongs to one exact configuration (C-7)."""
    _whole(job_device_active_ms, "job_device_active_ms")
    if config_digest != calibration.config_digest:
        return None
    return N1Quantity(calibration.profile, job_device_active_ms, calibration.median_device_active_ms)


@dataclass(frozen=True)
class ResourceQuote:
    """The estimate before the work. Hashed; the hash goes beside the price, in the offer."""
    operation: str
    operation_version: str
    model_digest: str
    quantization: str
    benchmark_id: str
    hardware: str
    driver: str
    power_settings: str
    workload_size: int
    concurrency: int
    profile: str
    expected: ResourceVector

    def __post_init__(self):
        for name in ("operation", "operation_version", "model_digest", "quantization", "benchmark_id",
                     "hardware", "driver", "power_settings", "profile"):
            _text(getattr(self, name), name)
        _whole(self.workload_size, "workload_size")
        _whole(self.concurrency, "concurrency", low=1)
        if not isinstance(self.expected, ResourceVector):
            raise ValueError("expected must be a ResourceVector")
        for f in fields(self.expected):
            if isinstance(getattr(self.expected, f.name), Measured):
                raise ValueError("a quote holds estimates, not measurements")

    def to_wire(self) -> dict:
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "expected"}
        out["expected"] = self.expected.to_wire()
        return out


def quote_hash(quote: ResourceQuote) -> str:
    return hashlib.sha256(QUOTE_PREFIX + wire.canonical(quote.to_wire())).hexdigest()


@dataclass(frozen=True)
class MeasuredReceipt:
    """The reading after the work. It names the quote it answers (C-1)."""
    quote_hash: str
    task_id: str
    profile: str
    vector: ResourceVector
    n1: N1Quantity | None
    status: str

    def __post_init__(self):
        if (not isinstance(self.quote_hash, str) or len(self.quote_hash) != 64
                or not set(self.quote_hash) <= _HEX64_CHARS):
            raise ValueError("a measured receipt names the hash of its quote")
        _text(self.task_id, "task_id")
        _text(self.profile, "profile")
        if not isinstance(self.vector, ResourceVector):
            raise ValueError("vector must be a ResourceVector")
        if self.n1 is not None and (not isinstance(self.n1, N1Quantity) or self.n1.profile != self.profile):
            raise ValueError("n1 must be a quantity of this receipt's profile")
        if self.status not in _STATUSES:
            raise ValueError("status must be one of " + ", ".join(_STATUSES))
        if self.status == "measured" and self.n1 is None:
            raise ValueError("a measured receipt with no N1 is uncalibrated")

    def to_wire(self) -> dict:
        return {"quote_hash": self.quote_hash, "task_id": self.task_id, "profile": self.profile,
                "vector": self.vector.to_wire(),
                "n1": None if self.n1 is None else {"n": self.n1.numerator, "d": self.n1.denominator},
                "status": self.status}
