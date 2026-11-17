"""The contract any payment provider must pass before it can be registered (design D.7).
SYNTHETIC CASH ONLY here. Today it runs against FakeProvider; a real adapter, when one is ever
chosen, must pass the same suite in that provider's sandbox, and the choice then changes one
directory and no workflow code.

A test module subclasses ProviderContract and supplies `new_provider(tmp_path)` and
`fund(provider, ref, payer, currency, amount_minor, expires_at)`; pytest then collects every
`test_*` method below against that provider.

A provider also supplies four hooks: `make_unreachable`, `force_uncertain`, `reverse_capture` and `return_payout`.

Rules the suite holds, and the test that holds each (a test that checks the two lists agree is in
tests/unit/test_exchange_fake_provider.py::test_every_rule_the_suite_names_has_a_test_and_every_test_names_a_rule):
- RULE descriptor: a descriptor that names a test double or a live provider honestly:
  test_the_descriptor_is_well_formed_and_says_what_the_provider_is.
- RULE buying-power: buying power is verified, unknown references are refused:
  test_buying_power_is_verified_and_an_unknown_reference_is_refused, and expired buying power is refused at reserve and
  verify: test_expired_buying_power_is_refused_at_reserve_and_at_verify.
- RULE reserve: reserve is limited to what the buyer has, in the right currency:
  test_reserving_more_than_the_buyer_has_is_refused_and_a_wrong_currency_too.
- RULE keys: one key is one request, and a key reused for another request is refused:
  test_a_repeated_key_is_one_request, test_a_key_reused_for_another_request_is_refused.
- RULE capture: capture once, within its reservation: test_a_reservation_is_captured_once_and_looked_up,
  test_a_capture_cannot_exceed_its_reservation.
- RULE release: release once and never after capture: test_a_reservation_is_released_once_and_never_after_capture.
- RULE payout: a payout names its capture, is one request per key, and cannot exceed what was captured:
  test_a_payout_needs_a_capture_and_is_one_request_per_key,
  test_a_payout_names_its_capture_and_cannot_exceed_what_was_captured.
- RULE refund: a refund follows a capture, stays within it, happens at most once in total, returns cash to the buyer, and
  cannot take back paid-out cash: test_a_refund_follows_a_capture_and_stays_within_it,
  test_a_capture_is_refunded_at_most_once_in_total_and_the_cash_returns_to_the_buyer,
  test_a_refund_cannot_take_back_cash_that_was_paid_out.
- RULE reversal: a reversed capture is not refundable and the buyer's cash is back:
  test_a_reversed_capture_is_not_refundable_and_the_buyers_cash_is_back.
- RULE uncertain: Uncertain is settled by lookup and a retry reserves once:
  test_one_forced_uncertain_is_settled_by_lookup_and_a_retry_reserves_once.
- RULE unknown-and-unreachable: an unknown key looks up as unknown, and a provider that cannot be asked says unreachable:
  test_an_unknown_key_looks_up_as_unknown, test_a_provider_that_cannot_be_asked_says_unreachable_never_unknown.
- RULE returned-payout: a returned payout requested again under the same key is pending again, within its capture:
  test_a_returned_payout_requested_again_under_the_same_key_is_pending_again_within_the_capture.
- RULE events: events are authenticated: test_a_genuine_event_is_authenticated_and_a_forged_one_is_not.
"""
from __future__ import annotations

from agent_friday.services.exchange import provider as p

NOW = 1_000
LATER = 10_000


def _ok(result, kind):
    assert isinstance(result, kind), result
    return result


class ProviderContract:
    def new_provider(self, tmp_path):
        raise NotImplementedError

    def fund(self, provider, ref, payer, currency, amount_minor, expires_at):
        raise NotImplementedError

    def make_unreachable(self, provider):
        """Make the provider unable to answer (its sandbox's way of simulating an outage)."""
        raise NotImplementedError

    def force_uncertain(self, provider):
        """Make the NEXT call return Uncertain (a timeout), as its sandbox can."""
        raise NotImplementedError

    def reverse_capture(self, provider, capture_key):
        """The buyer's side reverses a capture after the fact (a chargeback)."""
        raise NotImplementedError

    def return_payout(self, provider, payout_key):
        """The provider returns a pending payout."""
        raise NotImplementedError

    def ready(self, tmp_path, amount=10_000):
        provider = self.new_provider(tmp_path)
        self.fund(provider, "bp-1", "buyer-1", "USD", amount, LATER)
        return provider

    def reserved(self, provider, key="k-res", amount=1_000):
        return _ok(provider.reserve_cash(key, "bp-1", amount, "USD"), p.CashReserved)

    def test_the_descriptor_is_well_formed_and_says_what_the_provider_is(self, tmp_path):
        descriptor = self.new_provider(tmp_path).descriptor()
        assert isinstance(descriptor, p.ProviderDescriptor) and "USD" in descriptor.currencies

    def test_buying_power_is_verified_and_an_unknown_reference_is_refused(self, tmp_path):
        provider = self.ready(tmp_path)
        power = _ok(provider.verify_buying_power("bp-1"), p.BuyingPower)
        assert power.amount_minor == 10_000 and power.currency == "USD" and power.payer == "buyer-1"
        assert _ok(provider.verify_buying_power("nope"), p.Refused).code is p.RefusalCode.UNKNOWN_REFERENCE

    def test_reserving_more_than_the_buyer_has_is_refused_and_a_wrong_currency_too(self, tmp_path):
        provider = self.ready(tmp_path, amount=500)
        assert _ok(provider.reserve_cash("k1", "bp-1", 501, "USD"), p.Refused).code is p.RefusalCode.INSUFFICIENT
        assert _ok(provider.reserve_cash("k2", "bp-1", 100, "EUR"), p.Refused).code is p.RefusalCode.CURRENCY
        assert _ok(provider.reserve_cash("k3", "nope", 100, "USD"), p.Refused).code is p.RefusalCode.UNKNOWN_REFERENCE

    def test_a_repeated_key_is_one_request(self, tmp_path):
        provider = self.ready(tmp_path, amount=1_000)
        first = provider.reserve_cash("k1", "bp-1", 600, "USD")
        assert provider.reserve_cash("k1", "bp-1", 600, "USD") == first
        assert _ok(provider.reserve_cash("k2", "bp-1", 600, "USD"), p.Refused).code is p.RefusalCode.INSUFFICIENT
        assert _ok(provider.verify_buying_power("bp-1"), p.BuyingPower).amount_minor == 400

    def test_a_key_reused_for_another_request_is_refused(self, tmp_path):
        provider = self.ready(tmp_path)
        self.reserved(provider, "k1", 100)
        assert _ok(provider.reserve_cash("k1", "bp-1", 200, "USD"), p.Refused).code is p.RefusalCode.INVALID

    def test_a_reservation_is_captured_once_and_looked_up(self, tmp_path):
        provider = self.ready(tmp_path)
        reservation = self.reserved(provider, amount=1_000)
        done = _ok(provider.capture("k-cap", reservation.reservation_ref, 900, 2), p.Captured)
        assert (done.principal_minor, done.fee_minor) == (900, 2)
        assert provider.capture("k-cap", reservation.reservation_ref, 900, 2) == done
        assert provider.lookup("k-cap") is p.ProviderStatus.CAPTURED
        assert _ok(provider.capture("k-cap2", reservation.reservation_ref, 900, 2), p.Refused).code is p.RefusalCode.INVALID

    def test_a_capture_cannot_exceed_its_reservation(self, tmp_path):
        provider = self.ready(tmp_path)
        reservation = self.reserved(provider, amount=1_000)
        assert _ok(provider.capture("k-cap", reservation.reservation_ref, 1_000, 1), p.Refused).code is p.RefusalCode.INSUFFICIENT
        assert _ok(provider.capture("k-cap0", "res-unknown", 10, 0), p.Refused).code is p.RefusalCode.INVALID

    def test_a_reservation_is_released_once_and_never_after_capture(self, tmp_path):
        provider = self.ready(tmp_path)
        first = self.reserved(provider, "k-a", 1_000)
        _ok(provider.release("k-rel", first.reservation_ref, 1_000), p.Released)
        assert _ok(provider.release("k-rel2", first.reservation_ref, 1_000), p.Refused).code is p.RefusalCode.INVALID
        assert _ok(provider.verify_buying_power("bp-1"), p.BuyingPower).amount_minor == 10_000
        second = self.reserved(provider, "k-b", 1_000)
        _ok(provider.capture("k-cap", second.reservation_ref, 900, 2), p.Captured)
        assert _ok(provider.release("k-rel3", second.reservation_ref, 1_000), p.Refused).code is p.RefusalCode.INVALID

    def test_a_payout_needs_a_capture_and_is_one_request_per_key(self, tmp_path):
        provider = self.ready(tmp_path)
        assert _ok(provider.payout("k-pay", "cap-none", "seller-1", 500), p.Refused).code is p.RefusalCode.INVALID
        reservation = self.reserved(provider)
        done = _ok(provider.capture("k-cap", reservation.reservation_ref, 900, 2), p.Captured)
        pending = _ok(provider.payout("k-pay2", done.capture_ref, "seller-1", 900), p.PayoutPending)
        assert provider.payout("k-pay2", done.capture_ref, "seller-1", 900) == pending
        assert provider.lookup("k-pay2") is p.ProviderStatus.PAYOUT_PENDING

    def test_a_refund_follows_a_capture_and_stays_within_it(self, tmp_path):
        provider = self.ready(tmp_path)
        reservation = self.reserved(provider)
        done = _ok(provider.capture("k-cap", reservation.reservation_ref, 900, 2), p.Captured)
        assert _ok(provider.refund("k-r0", "cap-unknown", 10, "x"), p.Refused).code is p.RefusalCode.INVALID
        assert _ok(provider.refund("k-r1", done.capture_ref, 903, "x"), p.Refused).code is p.RefusalCode.INSUFFICIENT
        _ok(provider.refund("k-r2", done.capture_ref, 902, "buyer asked"), p.Refunded)
        assert provider.lookup("k-r2") is p.ProviderStatus.REFUNDED

    def test_an_unknown_key_looks_up_as_unknown(self, tmp_path):
        assert self.ready(tmp_path).lookup("never-sent") is p.ProviderStatus.UNKNOWN

    def test_a_genuine_event_is_authenticated_and_a_forged_one_is_not(self, tmp_path):
        provider = self.ready(tmp_path)
        self.reserved(provider)
        sent = provider.deliver()
        assert sent, "the provider sends an event for what it did"
        event = provider.authenticate_event(sent[0])
        assert isinstance(event, p.ProviderEvent) and event.status is p.ProviderStatus.RESERVED
        at = sent[0].index(b'"sig":"') + 7
        flipped = sent[0][:at] + (b"0" if sent[0][at:at + 1] != b"0" else b"1") + sent[0][at + 1:]
        assert isinstance(provider.authenticate_event(flipped), p.Forged), "one changed signature digit"
        assert isinstance(provider.authenticate_event(b"not an event"), p.Forged)
        assert isinstance(provider.authenticate_event(b""), p.Forged)

    def test_a_provider_that_cannot_be_asked_says_unreachable_never_unknown(self, tmp_path):
        provider = self.ready(tmp_path)
        reservation = self.reserved(provider, "k-res", 500)
        self.make_unreachable(provider)
        assert provider.lookup("k-res") is p.ProviderStatus.UNREACHABLE, "an outage is not 'never heard of it'"
        assert provider.lookup("never-sent") is p.ProviderStatus.UNREACHABLE
        answer = provider.capture("k-cap", reservation.reservation_ref, 400, 1)
        assert isinstance(answer, (p.Refused, p.Uncertain)) and not isinstance(answer, p.Captured)

    # ── cash is conserved per capture (design 10.8) ────────────────────────────────────────────────

    def captured(self, provider, key="k-cap", amount=1_000, principal=900, fee=2):
        reservation = self.reserved(provider, "k-res", amount)
        return _ok(provider.capture(key, reservation.reservation_ref, principal, fee), p.Captured)

    def available(self, provider):
        return _ok(provider.verify_buying_power("bp-1"), p.BuyingPower).amount_minor

    def test_a_payout_names_its_capture_and_cannot_exceed_what_was_captured(self, tmp_path):
        provider = self.ready(tmp_path)
        done = self.captured(provider)                                   # 902 captured
        assert _ok(provider.payout("k-p0", "cap-unknown", "seller-1", 10), p.Refused).code is p.RefusalCode.INVALID
        assert _ok(provider.payout("k-p1", done.capture_ref, "seller-1", 50_000), p.Refused).code is p.RefusalCode.INSUFFICIENT
        _ok(provider.payout("k-p2", done.capture_ref, "seller-1", 900), p.PayoutPending)
        assert _ok(provider.payout("k-p3", done.capture_ref, "seller-1", 3), p.Refused).code is p.RefusalCode.INSUFFICIENT
        _ok(provider.payout("k-p4", done.capture_ref, "seller-1", 2), p.PayoutPending)
        assert _ok(provider.payout("k-p5", done.capture_ref, "seller-1", 1), p.Refused).code is p.RefusalCode.INSUFFICIENT

    def test_a_capture_is_refunded_at_most_once_in_total_and_the_cash_returns_to_the_buyer(self, tmp_path):
        provider = self.ready(tmp_path)
        done = self.captured(provider)
        assert self.available(provider) == 10_000 - 902
        _ok(provider.refund("k-r1", done.capture_ref, 400, "buyer asked"), p.Refunded)
        assert self.available(provider) == 10_000 - 902 + 400, "a refund returns cash to the buyer's books"
        assert _ok(provider.refund("k-r2", done.capture_ref, 503, "x"), p.Refused).code is p.RefusalCode.INSUFFICIENT
        _ok(provider.refund("k-r3", done.capture_ref, 502, "rest"), p.Refunded)
        assert self.available(provider) == 10_000
        assert _ok(provider.refund("k-r4", done.capture_ref, 902, "again"), p.Refused).code is p.RefusalCode.INSUFFICIENT
        assert _ok(provider.refund("k-r5", done.capture_ref, 1, "again"), p.Refused).code is p.RefusalCode.INSUFFICIENT
        assert self.available(provider) == 10_000, "a refused refund changed nothing"

    def test_a_refund_cannot_take_back_cash_that_was_paid_out(self, tmp_path):
        provider = self.ready(tmp_path)
        done = self.captured(provider)
        _ok(provider.payout("k-p", done.capture_ref, "seller-1", 900), p.PayoutPending)
        assert _ok(provider.refund("k-r1", done.capture_ref, 3, "x"), p.Refused).code is p.RefusalCode.INSUFFICIENT
        _ok(provider.refund("k-r2", done.capture_ref, 2, "x"), p.Refunded)

    def test_a_reversed_capture_is_not_refundable_and_the_buyers_cash_is_back(self, tmp_path):
        provider = self.ready(tmp_path)
        done = self.captured(provider)
        self.reverse_capture(provider, "k-cap")
        assert provider.lookup("k-cap") is p.ProviderStatus.REVERSED
        assert self.available(provider) == 10_000, "a reversal returns the buyer's cash"
        assert _ok(provider.refund("k-r1", done.capture_ref, 902, "x"), p.Refused).code is p.RefusalCode.INVALID
        assert self.available(provider) == 10_000

    def test_expired_buying_power_is_refused_at_reserve_and_at_verify(self, tmp_path):
        provider = self.ready(tmp_path)
        self.fund(provider, "bp-old", "buyer-1", "USD", 5_000, NOW)          # expires at the provider's own now
        assert _ok(provider.verify_buying_power("bp-old"), p.Refused).code is p.RefusalCode.EXPIRED
        assert _ok(provider.reserve_cash("k-old", "bp-old", 100, "USD"), p.Refused).code is p.RefusalCode.EXPIRED

    def test_one_forced_uncertain_is_settled_by_lookup_and_a_retry_reserves_once(self, tmp_path):
        provider = self.ready(tmp_path, amount=1_000)
        self.force_uncertain(provider)
        answer = provider.reserve_cash("k-u", "bp-1", 600, "USD")
        assert answer == p.Uncertain("k-u")
        assert provider.lookup("k-u") in (p.ProviderStatus.RESERVED, p.ProviderStatus.UNKNOWN)
        again = provider.reserve_cash("k-u", "bp-1", 600, "USD")
        assert isinstance(again, p.CashReserved) and provider.lookup("k-u") is p.ProviderStatus.RESERVED
        assert _ok(provider.reserve_cash("k-u2", "bp-1", 600, "USD"), p.Refused).code is p.RefusalCode.INSUFFICIENT, \
            "the retry did not reserve twice"

    def test_a_returned_payout_requested_again_under_the_same_key_is_pending_again_within_the_capture(self, tmp_path):
        provider = self.ready(tmp_path)
        done = self.captured(provider)
        _ok(provider.payout("k-p", done.capture_ref, "seller-1", 900), p.PayoutPending)
        self.return_payout(provider, "k-p")
        assert provider.lookup("k-p") is p.ProviderStatus.PAYOUT_RETURNED
        _ok(provider.payout("k-p", done.capture_ref, "seller-1", 900), p.PayoutPending)
        assert provider.lookup("k-p") is p.ProviderStatus.PAYOUT_PENDING
        assert _ok(provider.payout("k-q", done.capture_ref, "seller-1", 3), p.Refused).code is p.RefusalCode.INSUFFICIENT, \
            "the re-requested payout still counts against the capture"
