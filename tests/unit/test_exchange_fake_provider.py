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

    def make_unreachable(self, provider):
        provider.arm(fp.Fault.OUTAGE, until=contract.LATER * 10)

    def force_uncertain(self, provider):
        provider.arm(fp.Fault.TIMEOUT_AFTER_EFFECT)

    def reverse_capture(self, provider, capture_key):
        provider.chargeback(capture_key)

    def return_payout(self, provider, payout_key):
        provider.return_payout(payout_key)


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
    done = provider.capture("k2", r.reservation_ref, 590, 2)
    provider.payout("k-pay-ok", done.capture_ref, "seller-1", 300)
    provider.settle_payout("k-pay-ok")
    assert provider.lookup("k-pay-ok") is p.ProviderStatus.PAYOUT_CONFIRMED
    provider.arm(fp.Fault.PAYOUT_RETURNED)
    provider.payout("k-pay-bad", done.capture_ref, "seller-1", 290)
    provider.settle_payout("k-pay-bad")
    assert provider.lookup("k-pay-bad") is p.ProviderStatus.PAYOUT_RETURNED
    assert provider.payout("k-pay-bad", done.capture_ref, "seller-1", 290).payout_ref == "pay-k-pay-bad", "same key, same payout"


def test_a_chargeback_after_payout_returns_the_buyers_cash_and_blocks_any_refund(tmp_path):
    provider = made(tmp_path)
    r = provider.reserve_cash("k1", "bp-1", 600, "USD")
    done = provider.capture("k2", r.reservation_ref, 590, 2)
    provider.payout("k-pay", done.capture_ref, "seller-1", 590)
    provider.chargeback("k2")
    assert provider.lookup("k2") is p.ProviderStatus.REVERSED
    events = [provider.authenticate_event(raw) for raw in provider.deliver()]
    assert events[-1].status is p.ProviderStatus.REVERSED and events[-1].key == "k2"
    assert provider.refund("k-ref", done.capture_ref, 592, "chargeback") == p.Refused(p.RefusalCode.INVALID)
    assert provider.books()["power"]["bp-1"]["amount"] == 10_000, "the buyer's cash came back once"
    provider.chargeback("k2")
    assert provider.books()["power"]["bp-1"]["amount"] == 10_000, "a second reversal changes nothing"


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


def test_every_rule_the_suite_names_has_a_test_and_every_test_names_a_rule():
    """The contract's docstring lists the test that holds each rule; the list and the suite must agree."""
    import re
    doc = re.sub(r"tests/unit/test_\w+\.py(::\w+)?", "", contract.__doc__)   # the paths are not rules
    named = set(re.findall(r"(test_[a-z0-9_]+)", doc))
    present = {n for n in dir(contract.ProviderContract) if n.startswith("test_")}
    assert present - named == set(), "a test no rule names"
    assert named - present == set(), "a rule names a test that does not exist"


def _money_in_the_books(provider):
    """The buyer's free cash and reserved cash, and what each capture holds."""
    with provider._db() as db:
        power = db.execute("SELECT COALESCE(SUM(amount),0), COALESCE(SUM(reserved),0) FROM power").fetchone()
        keys = [r[0] for r in db.execute("SELECT key FROM ops WHERE kind='capture'")]
        held = {k: provider._holdings(db, "cap-" + k) for k in keys}
    return power, held


def test_the_books_balance_after_every_short_sequence_of_operations(tmp_path):
    """Whatever up to four operations run, in any order, the cash that left the buyer equals the cash held, paid out or
    refunded: nothing is made or lost (design 10.8). The operations include ones that must be refused."""
    import itertools
    start = 10_000
    steps = ["reserve", "capture", "payout", "payout_all", "refund", "chargeback", "return_payout"]
    count = 0
    for length in range(1, 5):
        for sequence in itertools.product(steps, repeat=length):
            count += 1
            provider = fp.FakeProvider(tmp_path / f"seq{count}.sqlite", SEED, Clock(), synchronous="OFF")
            provider.fund("bp-1", "buyer-1", "USD", start, contract.LATER)
            reservation = capture = None
            for n, step in enumerate(sequence):
                key = f"k{n}"
                if step == "reserve":
                    reservation = provider.reserve_cash(key, "bp-1", 1_000, "USD")
                elif step == "capture" and isinstance(reservation, p.CashReserved):
                    capture = provider.capture(key, reservation.reservation_ref, 900, 3)
                elif step.startswith("payout") and isinstance(capture, p.Captured):
                    provider.payout(key, capture.capture_ref, "seller-1", 903 if step == "payout_all" else 500)
                elif step == "refund" and isinstance(capture, p.Captured):
                    provider.refund(key, capture.capture_ref, 400, "x")
                elif step == "chargeback" and isinstance(capture, p.Captured):
                    provider.chargeback(capture.capture_ref[4:])
                elif step == "return_payout":
                    for op, (kind, _status) in provider.books()["ops"].items():
                        if kind == "payout":
                            provider.return_payout(op)
            (free, reserved), held = _money_in_the_books(provider)
            left_the_buyer = 0
            for status, total, paid, refunded, _ref in held.values():
                assert paid >= 0 and refunded >= 0 and paid + refunded <= total, (sequence, "over-disbursed")
                if status != p.ProviderStatus.REVERSED.value:
                    left_the_buyer += total - refunded       # a reversed capture's cash is all back with the buyer
            # `amount` is the buyer's cash; a reservation is a hold on it, never more than it
            assert free == start - left_the_buyer, (sequence, "cash not conserved")
            assert 0 <= reserved <= free, (sequence, "hold exceeds cash")
    assert count >= 340
