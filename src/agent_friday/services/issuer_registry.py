"""Issuers, their charters and exact amounts of their units (NS-40.4-1; design: program/specs/
DESIGN_economy_issuer_exchange_resource_2026-11-16.md, Part A). TEST CREDITS ONLY: a charter here
says "TEST P carry no cash claim"; nothing moves real money and nothing reads the legacy
services/economy.py (a separate, historical dataset that is not an issuer).

The charter protocol string and the digest prefix are proposed names (the bundle has no charter
schema); if a later bundle fixes them, the bundle wins.

Rules and the tests that hold them (tests/unit/test_issuer_registry.py):
- A-1 an issuer is the pair (issuer_id, charter_digest):
  test_the_same_id_with_another_charter_is_refused.
- A-2 a charter never changes:
  test_registering_identical_bytes_again_is_a_no_op_and_nothing_can_update_it.
- A-4 units of two issuers are never added, subtracted or ordered:
  test_amount_arithmetic_succeeds_only_between_equal_refs_for_every_small_case.
- Amounts are whole units, 0..2^53-1: test_an_amount_is_a_checked_whole_number.
- The digest is stable and covers every field:
  test_the_pilot_digest_is_stable_and_every_field_changes_it.
- A stored charter that no longer parses or names another issuer is refused:
  test_a_damaged_charter_file_is_refused. An edit that stays well formed is caught by the journal's
  stored digest: tests/unit/test_issuer_journal_charter.py.
- No public function here takes a bare integer of units:
  test_no_public_function_takes_a_bare_amount_integer.
- Legacy economy is apart: test_neither_the_registry_nor_legacy_economy_imports_the_other.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path

from agent_friday.services import peer_wire_v2 as wire

PROTOCOL = "asimov-issuer-charter/v1"
DIGEST_PREFIX = PROTOCOL.encode("ascii") + bytes([0])
MAX_UNITS = 2 ** 53 - 1
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_UNIT = re.compile(r"[A-Z0-9]{2,12}")
_ACCOUNT = re.compile(r"[a-z0-9][a-z0-9._:-]{0,63}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_STAMP = re.compile(r"[0-9TZ:.+-]{10,40}")


class MintPolicy(str, Enum):
    NEVER = "never"


class RecoveryRule(str, Enum):
    SINGLE_AUTHORITY_LOG = "single_authority_log"


class IssuerOutcome(str, Enum):
    REGISTERED = "registered"
    ALREADY_REGISTERED = "already-registered"
    CHARTER_MISMATCH = "charter-mismatch"


class CharterError(ValueError):
    """A charter that is not well formed, or a stored one that no longer matches what was registered."""


class MixedIssuers(ArithmeticError):
    """Units of two issuers were added, subtracted or compared (A-4)."""


@dataclass(frozen=True)
class IssuerCharter:
    issuer_id: str
    unit_code: str
    precision: int
    genesis_supply: int
    treasury_account: str
    cash_claim: bool
    genesis_authority: str
    created: str
    mint_after_genesis: MintPolicy = MintPolicy.NEVER
    recovery: RecoveryRule = RecoveryRule.SINGLE_AUTHORITY_LOG
    protocol: str = PROTOCOL

    def __post_init__(self):
        def need(ok, what):
            if not ok:
                raise CharterError(f"charter field {what} is not valid")
        need(self.protocol == PROTOCOL, "protocol")
        need(isinstance(self.issuer_id, str) and _ID.fullmatch(self.issuer_id), "issuer_id")
        need(isinstance(self.unit_code, str) and _UNIT.fullmatch(self.unit_code), "unit_code")
        need(type(self.precision) is int and 0 <= self.precision <= 18, "precision")
        need(type(self.genesis_supply) is int and 0 <= self.genesis_supply <= MAX_UNITS, "genesis_supply")
        need(isinstance(self.treasury_account, str) and _ACCOUNT.fullmatch(self.treasury_account),
             "treasury_account")
        need(type(self.cash_claim) is bool, "cash_claim")
        need(isinstance(self.genesis_authority, str) and _HEX64.fullmatch(self.genesis_authority),
             "genesis_authority")
        need(isinstance(self.created, str) and _STAMP.fullmatch(self.created), "created")
        need(isinstance(self.mint_after_genesis, MintPolicy), "mint_after_genesis")
        need(isinstance(self.recovery, RecoveryRule), "recovery")

    def as_dict(self) -> dict:
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = value.value if isinstance(value, Enum) else value
        return out

    def canonical_bytes(self) -> bytes:
        return wire.canonical(self.as_dict())

    @classmethod
    def from_bytes(cls, raw: bytes) -> "IssuerCharter":
        try:
            parsed = wire.decode(raw)
            return cls(**{**parsed, "mint_after_genesis": MintPolicy(parsed["mint_after_genesis"]),
                          "recovery": RecoveryRule(parsed["recovery"])})
        except (TypeError, KeyError, ValueError, wire.WireError) as err:
            raise CharterError("the charter bytes do not describe a charter") from err


def charter_digest(charter: IssuerCharter) -> str:
    return hashlib.sha256(DIGEST_PREFIX + charter.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class IssuerRef:
    issuer_id: str
    charter_digest: str
    unit_code: str

    @classmethod
    def of(cls, charter: IssuerCharter) -> "IssuerRef":
        return cls(charter.issuer_id, charter_digest(charter), charter.unit_code)


@dataclass(frozen=True)
class Amount:
    """Whole units of one issuer's unit. Never mixes with another issuer's."""
    ref: IssuerRef
    units: int

    def __post_init__(self):
        if (not isinstance(self.ref, IssuerRef) or type(self.units) is not int
                or not 0 <= self.units <= MAX_UNITS):
            raise ValueError("an amount is whole units, 0 to 2^53-1, of one issuer")

    def _units_of(self, other):
        if not isinstance(other, Amount):
            return None
        if other.ref != self.ref:
            raise MixedIssuers("units of two issuers are never combined or compared")
        return other.units

    def __add__(self, other):
        units = self._units_of(other)
        return NotImplemented if units is None else Amount(self.ref, self.units + units)

    def __sub__(self, other):
        units = self._units_of(other)
        return NotImplemented if units is None else Amount(self.ref, self.units - units)

    def __lt__(self, other):
        units = self._units_of(other)
        return NotImplemented if units is None else self.units < units

    def __le__(self, other):
        units = self._units_of(other)
        return NotImplemented if units is None else self.units <= units

    def __gt__(self, other):
        units = self._units_of(other)
        return NotImplemented if units is None else self.units > units

    def __ge__(self, other):
        units = self._units_of(other)
        return NotImplemented if units is None else self.units >= units


class IssuerRegistry:
    """Charters on disk, one folder per issuer id. A charter is written once and read back
    against what was registered."""

    def __init__(self, home):
        self._root = Path(home) / "issuers"

    def _path(self, issuer_id: str) -> Path:
        if not isinstance(issuer_id, str) or not _ID.fullmatch(issuer_id):
            raise CharterError("charter field issuer_id is not valid")
        return self._root / issuer_id / "charter.json"

    def register(self, charter: IssuerCharter) -> IssuerOutcome:
        path = self._path(charter.issuer_id)
        raw = charter.canonical_bytes()
        if path.exists():
            return (IssuerOutcome.ALREADY_REGISTERED if path.read_bytes() == raw
                    else IssuerOutcome.CHARTER_MISMATCH)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_bytes(raw)
        os.replace(temp, path)
        return IssuerOutcome.REGISTERED

    def get(self, issuer_id: str) -> IssuerCharter | None:
        path = self._path(issuer_id)
        if not path.exists():
            return None
        charter = IssuerCharter.from_bytes(path.read_bytes())
        if charter.issuer_id != issuer_id:
            raise CharterError("the stored charter names another issuer")
        return charter

    def ref(self, issuer_id: str) -> IssuerRef | None:
        charter = self.get(issuer_id)
        return IssuerRef.of(charter) if charter else None
