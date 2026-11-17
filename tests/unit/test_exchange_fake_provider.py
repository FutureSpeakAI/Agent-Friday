"""The synthetic payment-provider double and the shared contract suite (NS-40.4-13, design D.6-D.7, PR 7).
Synthetic cash only."""
import inspect

import pytest

from agent_friday.services.exchange import contract
from agent_friday.services.exchange import fake_provider as fp
from agent_friday.services.exchange import provider as p

SEED = bytes(range(32))


class Clock:
    def __init__(self, now=contract.NOW):
        self.now = now

    def __call__(self):
        return self.now


class TestFakeProviderContract(contract.ProviderContract):
    def new_provider(self, tmp_path):
        return fp.FakeProvider(tmp_path / "fake.sqlite", SEED, Clock())

    def fund(self, provider, ref, payer, currency, amount_minor, expires_at):
        provider.fund(ref, payer, currency, amount_minor, expires_at)


def made(tmp_path, amount=10_000, clock=None):
    provider = fp.FakeProvider(tmp_path / "fake.sqlite", SEED, clock or Clock())
    provider.fund("bp-1", "buyer-1", "USD", amount, contract.LATER)
    return provider


def test_the_double_says_it_is_synthetic_and_cannot_be_taken_for_live(tmp_path):
    d = made(tmp_path).descriptor()
    assert d.test_only is True and d.id == "synthetic"
    registry = {}
    p.register_provider(d, registry)
    assert registry["synthetic"] is d


def test_the_books_survive_a_restart(tmp_path):
    first = made(tmp_path)
    reservation = first.reserve_cash("k1", "bp-1", 600, "USD")
    done = first.capture("k2", reservation.reservation_ref, 590, 2)
    books = first.books()
    again = fp.FakeProvider(tmp_path / "fake.sqlite", SEED, Clock())
    assert again.books() == books
    assert again.lookup("k2") is p.ProviderStatus.CAPTURED
    assert again.capture("k2", reservation.reservation_ref, 590, 2) == done


def test_it_has_no_clock_or_randomness_of_its_own(tmp_path):
    source = inspect.getsource(fp)
    for banned in ("import time", "import random", "import uuid", "time.time", "datetime", "secrets"):
        assert banned not in source, banned
    outputs = []
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        provider = made(folder)
        r = provider.reserve_cash("k1", "bp-1", 600, "USD")
        provider.capture("k2", r.reservation_ref, 590, 2)
        outputs.append((provider.deliver(), provider.books()))
    assert outputs[0] == outputs[1]


def test_timeout_after_the_effect_is_uncertain_and_lookup_tells_the_truth(tmp_path):
    provider = made(tmp_path)
    provider.arm(fp.Fault.TIMEOUT_AFTER_EFFECT)
    answer = provider.reserve_cash("k1", "bp-1", 600, "USD")
    assert answer == p.Uncertain("k1") and provider.lookup("k1") is p.ProviderStatus.RESERVED
    again = provider.reserve_cash("k1", "bp-1", 600, "USD")
    assert isinstance(again, p.CashReserved) and provider.books()["power"]["bp-1"]["reserved"] == 600


def test_timeout_before_the_effect_is_uncertain_and_nothing_happened(tmp_path):
    provider = made(tmp_path)
    provider.arm(fp.Fault.TIMEOUT_BEFORE_EFFECT)
    assert provider.reserve_cash("k1", "bp-1", 600, "USD") == p.Uncertain("k1")
    assert provider.lookup("k1") is p.ProviderStatus.UNKNOWN and provider.books()["power"]["bp-1"]["reserved"] == 0
    assert isinstance(provider.reserve_cash("k1", "bp-1", 600, "USD"), p.CashReserved)
    assert provider.books()["power"]["bp-1"]["reserved"] == 600


def test_a_duplicate_event_arrives_twice_under_different_message_ids(tmp_path):
    provider = made(tmp_path)
    provider.arm(fp.Fault.DUPLICATE_EVENT)
    provider.reserve_cash("k1", "bp-1", 600, "USD")
    events = [provider.authenticate_event(raw) for raw in provider.deliver()]
    assert len(events) == 2 and events[0].key == events[1].key == "k1"
    assert events[0].message_id != events[1].message_id and events[0].status == events[1].status


def test_reordered_events_arrive_confirmation_before_reservation(tmp_path):
    provider = made(tmp_path)
    r = provider.reserve_cash("k1", "bp-1", 600, "USD")
    provider.capture("k2", r.reservation_ref, 590, 2)
    provider.arm(fp.Fault.REORDERED_EVENTS)
    statuses = [provider.authenticate_event(raw).status for raw in provider.deliver()]
    assert statuses == [p.ProviderStatus.CAPTURED, p.ProviderStatus.RESERVED]


def test_a_forged_event_fails_authentication_and_changes_no_books(tmp_path):
    provider = made(tmp_path)
    provider.arm(fp.Fault.FORGED_EVENT)
    provider.reserve_cash("k1", "bp-1", 600, "USD")
    before = provider.books()
    assert [type(provider.authenticate_event(raw)) for raw in provider.deliver()] == [p.Forged]
    assert provider.books() == before


def test_an_outage_refuses_every_call_until_its_time_is_up(tmp_path):
    clock = Clock()
    provider = made(tmp_path, clock=clock)
    r = provider.reserve_cash("k1", "bp-1", 600, "USD")
    provider.arm(fp.Fault.OUTAGE, until=contract.NOW + 50)
    refused = p.Refused(p.RefusalCode.OUTAGE)
    assert provider.verify_buying_power("bp-1") == refused
    assert provider.release("k-rel", r.reservation_ref, 600) == refused
    assert provider.capture("k-cap", r.reservation_ref, 100, 0) == refused
    assert provider.books()["power"]["bp-1"]["reserved"] == 600
    clock.now = contract.NOW + 50
    assert isinstance(provider.release("k-rel", r.reservation_ref, 600), p.Released)
    assert provider.release("k-rel-again", r.reservation_ref, 600) == p.Refused(p.RefusalCode.INVALID)
    assert provider.books()["power"]["bp-1"]["reserved"] == 0


def test_a_returned_payout_is_reported_and_a_confirmed_one_too(tmp_path):
    provider = made(tmp_path)
    r = provider.reserve_cash("k1", "bp-1", 600, "USD")
    provider.capture("k2", r.reservation_ref, 590, 2)
    provider.payout("k-pay-ok", "seller-1", 300)
    provider.settle_payout("k-pay-ok")
    assert provider.lookup("k-pay-ok") is p.ProviderStatus.PAYOUT_CONFIRMED
    provider.arm(fp.Fault.PAYOUT_RETURNED)
    provider.payout("k-pay-bad", "seller-1", 290)
    provider.settle_payout("k-pay-bad")
    assert provider.lookup("k-pay-bad") is p.ProviderStatus.PAYOUT_RETURNED
    assert provider.payout("k-pay-bad", "seller-1", 290).payout_ref == "pay-k-pay-bad", "same key, same payout"


def test_a_chargeback_reverses_a_capture_after_payout_and_still_allows_a_refund(tmp_path):
    provider = made(tmp_path)
    r = provider.reserve_cash("k1", "bp-1", 600, "USD")
    done = provider.capture("k2", r.reservation_ref, 590, 2)
    provider.payout("k-pay", "seller-1", 590)
    provider.chargeback("k2")
    assert provider.lookup("k2") is p.ProviderStatus.REVERSED
    events = [provider.authenticate_event(raw) for raw in provider.deliver()]
    assert events[-1].status is p.ProviderStatus.REVERSED and events[-1].key == "k2"
    assert isinstance(provider.refund("k-ref", done.capture_ref, 592, "chargeback"), p.Refunded)


def test_buying_power_that_expires_between_order_and_match_is_refused_at_the_match(tmp_path):
    provider = made(tmp_path)
    provider.arm(fp.Fault.EXPIRED_BUYING_POWER)
    assert isinstance(provider.verify_buying_power("bp-1"), p.BuyingPower)
    assert provider.verify_buying_power("bp-1") == p.Refused(p.RefusalCode.EXPIRED)
    assert provider.reserve_cash("k1", "bp-1", 600, "USD") == p.Refused(p.RefusalCode.EXPIRED)


def test_buying_power_that_passes_its_time_is_refused(tmp_path):
    clock = Clock()
    provider = made(tmp_path, clock=clock)
    clock.now = contract.LATER
    assert provider.verify_buying_power("bp-1") == p.Refused(p.RefusalCode.EXPIRED)


def test_a_fault_fires_once_and_only_a_named_fault_can_be_armed(tmp_path):
    provider = made(tmp_path)
    provider.arm(fp.Fault.TIMEOUT_BEFORE_EFFECT)
    assert provider.reserve_cash("k1", "bp-1", 100, "USD") == p.Uncertain("k1")
    assert isinstance(provider.reserve_cash("k2", "bp-1", 100, "USD"), p.CashReserved)
    with pytest.raises(ValueError):
        provider.arm("not-a-fault")


def test_the_contract_suite_can_fail_against_a_broken_provider(tmp_path):
    """A provider that lets one key reserve twice must fail the shared suite."""
    class Broken(fp.FakeProvider):
        def reserve_cash(self, key, buying_power_ref, amount_minor, currency):
            return super().reserve_cash(key + str(self._clock()) + "x" * (len(self.books()["ops"])),
                                        buying_power_ref, amount_minor, currency)

    class BrokenContract(TestFakeProviderContract):
        def new_provider(self, tmp_path):
            return Broken(tmp_path / "broken.sqlite", SEED, Clock())

    suite = BrokenContract()
    with pytest.raises(AssertionError):
        suite.test_a_repeated_key_is_one_request(tmp_path)
