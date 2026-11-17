"""The contract any payment provider must pass before it can be registered (design D.7).
SYNTHETIC CASH ONLY here. Today it runs against FakeProvider; a real adapter, when one is ever
chosen, must pass the same suite in that provider's sandbox, and the choice then changes one
directory and no workflow code.

A test module subclasses ProviderContract and supplies `new_provider(tmp_path)` and
`fund(provider, ref, payer, currency, amount_minor, expires_at)`; pytest then collects every
`test_*` method below against that provider.

Rules the suite holds, one test each: a descriptor that names a test double or a live provider
honestly; buying power that is verified, expiring and unknown-refused; reserve, capture, release,
payout and refund that each follow one key to one answer; a key reused for another request refused;
no double release or capture; `Uncertain` that `lookup(key)` settles; events that are authenticated.
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
        assert _ok(provider.payout("k-pay", "seller-1", 500), p.Refused).code is p.RefusalCode.INVALID
        reservation = self.reserved(provider)
        _ok(provider.capture("k-cap", reservation.reservation_ref, 900, 2), p.Captured)
        pending = _ok(provider.payout("k-pay2", "seller-1", 900), p.PayoutPending)
        assert provider.payout("k-pay2", "seller-1", 900) == pending
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
