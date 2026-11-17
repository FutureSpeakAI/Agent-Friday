"""The buyer's cash fee on an exchange order (NS-40.4-12): 0.25% of settled principal, rounded to the
nearest minor unit on the CUMULATIVE principal, so a split order pays what one order would.

Rules and the tests that hold them (tests/unit/test_exchange_orders.py):
- Each fill is charged the increment, and the increments sum to the one-order fee:
  test_the_bundle_cash_vectors_pass_through_fees.
- Replaying a fill accrues zero: test_replaying_a_fill_accrues_zero.
- Only a confirmed fill may accrue, and only through FeeBook.accrue (the settlement workflow is its
  one call site; that is held by the settlement tests when that PR lands).
"""
from __future__ import annotations

from agent_friday.services.issuer_journal import cash_fee


def fee_increment(cumulative_before: int, principal: int) -> int:
    """The fee owed for `principal` more, given what was already settled on this order."""
    if type(cumulative_before) is not int or type(principal) is not int or cumulative_before < 0 or principal < 0:
        raise ValueError("principal is whole non-negative minor units")
    return cash_fee(cumulative_before + principal) - cash_fee(cumulative_before)


class FeeBook:
    """Fees accrued per exchange order, once per fill."""

    def __init__(self):
        self._principal: dict[str, int] = {}
        self._accrued: dict[str, dict[str, int]] = {}

    def accrue(self, exchange_id: str, fill_id: str, principal: int) -> int:
        """The increment for this fill the first time it is seen; 0 for the same fill again."""
        done = self._accrued.setdefault(exchange_id, {})
        if fill_id in done:
            return 0
        increment = fee_increment(self._principal.get(exchange_id, 0), principal)
        self._principal[exchange_id] = self._principal.get(exchange_id, 0) + principal
        done[fill_id] = increment
        return increment

    def total(self, exchange_id: str) -> int:
        return sum(self._accrued.get(exchange_id, {}).values())

    def settled_principal(self, exchange_id: str) -> int:
        return self._principal.get(exchange_id, 0)
