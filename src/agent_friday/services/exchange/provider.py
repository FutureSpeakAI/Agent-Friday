"""The payment-provider interface and its result types, with no implementation (design D.4).

TEST CREDITS AND SYNTHETIC CASH ONLY. `test_only` on a descriptor is the provider's OWN word: the import guard in
scripts/check_exchange_imports.py (no network under services/exchange) is the other half of the rule that no live
provider is reachable. Every reference is an opaque string. No type here has a field
for a card number, a bank account, a routing number or a name on an account (spec A08), held by
tests/unit/test_exchange_guards.py. A provider whose descriptor says it is not a test double cannot
be registered while live cash is held, and no setting changes that.

Rules and the tests that hold them (tests/unit/test_exchange_orders.py, test_exchange_guards.py):
- A non-test provider is refused while LIVE_CASH is held: test_a_live_provider_is_refused_under_every_federation_flag.
- BuyingPower is built only by a provider: test_only_provider_code_builds_buying_power.
- An idempotency key is stable and names its exchange, fill and leg: test_an_idempotency_key_is_stable_and_names_its_leg.
- Uncertain is its own answer, not a failure or a success: test_uncertain_is_not_a_refusal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from agent_friday.services import held_features

_REF = re.compile(r"[A-Za-z0-9._:/-]{1,120}")
_CURRENCY = re.compile(r"[A-Z]{3}")


def opaque(value, what="reference") -> str:
    if not isinstance(value, str) or not _REF.fullmatch(value):
        raise ValueError(f"{what} must be an opaque reference")
    return value


def _minor(value, what):
    if type(value) is not int or value < 0:
        raise ValueError(f"{what} must be whole non-negative minor units")
    return value


class Leg(str, Enum):
    RESERVE = "reserve"
    CAPTURE = "capture"
    RELEASE = "release"
    PAYOUT = "payout"
    REFUND = "refund"


def idempotency_key(exchange_id: str, fill_id: str, leg: Leg, attempt_scope: str = "once") -> str:
    """The same request always has the same key; a retry never invents a new one."""
    return "/".join((opaque(exchange_id, "exchange id"), opaque(fill_id, "fill id"), Leg(leg).value,
                     opaque(attempt_scope, "scope")))


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    test_only: bool
    currencies: tuple
    confirmation_threshold: int
    reversal_bearer: str
    payout_supported: bool

    def __post_init__(self):
        opaque(self.id, "provider id")
        if type(self.test_only) is not bool or type(self.payout_supported) is not bool:
            raise ValueError("flags are booleans")
        if not self.currencies or not all(isinstance(c, str) and _CURRENCY.fullmatch(c) for c in self.currencies):
            raise ValueError("currencies are ISO-style codes")
        _minor(self.confirmation_threshold, "confirmation threshold")
        if self.reversal_bearer not in ("provider", "exchange", "seller", "buyer"):
            raise ValueError("reversal bearer is one of provider, exchange, seller, buyer")


class LiveProviderRefused(PermissionError):
    """A non-test provider was offered while live cash is held."""


def register_provider(descriptor: ProviderDescriptor, registry: dict) -> None:
    """Adds the descriptor; refuses a non-test provider while LIVE_CASH is held (it always is in this release)."""
    if not descriptor.test_only and held_features.LIVE_CASH in held_features.NEVER_IN_THIS_RELEASE:
        raise LiveProviderRefused("a live provider cannot be registered while live cash is held")
    if descriptor.id in registry:
        raise ValueError("a provider with this id is already registered")
    registry[descriptor.id] = descriptor


@dataclass(frozen=True)
class BuyingPower:
    """Independently verified, expiring cash a buyer may spend, from ONE provider (an order for another provider
    refuses it). Only a provider builds one."""
    provider_id: str
    provider_ref: str
    payer: str
    currency: str
    amount_minor: int
    expires_at: int
    verified_at: int

    def __post_init__(self):
        opaque(self.provider_id, "provider id")
        opaque(self.provider_ref, "provider ref")
        opaque(self.payer, "payer")
        if not _CURRENCY.fullmatch(self.currency or ""):
            raise ValueError("currency is an ISO-style code")
        _minor(self.amount_minor, "amount")
        _minor(self.expires_at, "expires_at")
        _minor(self.verified_at, "verified_at")


class RefusalCode(str, Enum):
    UNKNOWN_REFERENCE = "unknown-reference"
    INSUFFICIENT = "insufficient"
    EXPIRED = "expired"
    CURRENCY = "currency"
    NOT_SUPPORTED = "not-supported"
    OUTAGE = "outage"
    INVALID = "invalid"


@dataclass(frozen=True)
class Refused:
    code: RefusalCode


@dataclass(frozen=True)
class Uncertain:
    """The provider did not say. Reconcile with `lookup(key)`; never treat as failure or success."""
    key: str

    def __post_init__(self):
        opaque(self.key, "key")


@dataclass(frozen=True)
class CashReserved:
    key: str
    reservation_ref: str
    amount_minor: int


@dataclass(frozen=True)
class Captured:
    key: str
    capture_ref: str
    principal_minor: int
    fee_minor: int


@dataclass(frozen=True)
class Released:
    key: str
    amount_minor: int


@dataclass(frozen=True)
class PayoutPending:
    key: str
    payout_ref: str
    amount_minor: int


@dataclass(frozen=True)
class Refunded:
    key: str
    refund_ref: str
    amount_minor: int


class ProviderStatus(str, Enum):
    UNKNOWN = "unknown"          # the provider has never heard of this key: nothing happened
    UNREACHABLE = "unreachable"  # the provider could not be asked: nothing is known
    RESERVED = "reserved"
    CAPTURED = "captured"
    RELEASED = "released"
    PAYOUT_PENDING = "payout-pending"
    PAYOUT_CONFIRMED = "payout-confirmed"
    PAYOUT_RETURNED = "payout-returned"
    REFUNDED = "refunded"
    REVERSED = "reversed"


@dataclass(frozen=True)
class ProviderEvent:
    """An authenticated message from the provider. `message_id` is the provider's own, so a duplicate
    delivery under another id is still the same `key`."""
    message_id: str
    key: str
    status: ProviderStatus


@dataclass(frozen=True)
class Forged:
    """An event whose signature did not verify. It changes nothing and is only recorded."""


class Provider(Protocol):
    def descriptor(self) -> ProviderDescriptor: ...

    def verify_buying_power(self, ref: str) -> "BuyingPower | Refused": ...

    def reserve_cash(self, key: str, buying_power_ref: str, amount_minor: int, currency: str
                     ) -> "CashReserved | Refused | Uncertain": ...

    def capture(self, key: str, reservation_ref: str, principal_minor: int, fee_minor: int
                ) -> "Captured | Refused | Uncertain": ...

    def release(self, key: str, reservation_ref: str, amount_minor: int) -> "Released | Refused | Uncertain": ...

    def payout(self, key: str, seller_ref: str, amount_minor: int) -> "PayoutPending | Refused | Uncertain": ...

    def refund(self, key: str, capture_ref: str, amount_minor: int, reason: str
               ) -> "Refunded | Refused | Uncertain": ...

    def lookup(self, key: str) -> ProviderStatus: ...

    def authenticate_event(self, raw: bytes) -> "ProviderEvent | Forged": ...
