"""The settlement workflow: states, crash matrix, invariants (NS-40.4-13, design D.5 as corrected by section 10).
Synthetic cash and test credits only."""
import ast
import itertools
import sqlite3
from pathlib import Path

import pytest

from agent_friday.services import issuer_authority as auth
from agent_friday.services.exchange import contract
from agent_friday.services.exchange import fake_provider as fp
from agent_friday.services.exchange import provider as p
from agent_friday.services.exchange import settlement as st
from agent_friday.services.exchange.orders import ExchangeId, Fill, FillId
from agent_friday.services.issuer_journal import IssuerJournal, Outcome
from agent_friday.services.issuer_registry import IssuerCharter

SEED = bytes(range(32))
KEY = auth.Ed25519Key(bytes(range(32)))
SUPPLY, UNITS = 1_000_000, 100
PRINCIPAL, FEE = 5000, 13
CHARTER = IssuerCharter(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=SUPPLY,
                        treasury_account="treasury", cash_claim=False, genesis_authority=KEY.public_key,
                        created="2026-11-16T00:00:00Z")
SALE = "sale:x1:seller-1"
ACCOUNTS = st.Accounts(seller="seller-1", buyer="buyer")
PARTIES = st.Parties("bp-1", "seller-1", "USD")
SETTLEMENT = Path(st.__file__)
PRESS = st.RefundPress()


def fill_acct(fill_id="f1"):
    return f"fill:x1:{fill_id}"


class Crash(Exception):
    """A node killed at a chosen point."""


class Clock:
    def __init__(self):
        self.now = contract.NOW

    def __call__(self):
        return self.now


class Env:
    def __init__(self, tmp):
        self.tmp, self.clock = Path(tmp), Clock()
        self.journal_path = self.tmp / "j.sqlite"
        self.authority = None
        self.boot()
        outcome, _ = self.authority.execute({"issuer": "test.one", "effect": "reserve-x1", "action": "reserve-x1",
                                             "postings": {"treasury": -UNITS, SALE: UNITS}})
        assert outcome is Outcome.POSTED
        self.provider = fp.FakeProvider(self.tmp / "p.sqlite", SEED, self.clock, synchronous="OFF")
        self.provider.fund("bp-1", "buyer-1", "USD", 10_000, contract.LATER)
        self.boot()

    def boot(self):
        """(Re)start the node: a new authority and settlement over the same files."""
        if self.authority is not None:
            self.authority.close()
        self.authority = auth.Authority(self.journal_path, CHARTER, KEY)
        if hasattr(self, "provider"):
            self.settlement = st.Settlement(self.tmp / "s.sqlite", self.authority, self.provider, synchronous="OFF")

    def fill(self, fill_id="f1", exchange="x1", principal=PRINCIPAL, fee=FEE, units=UNITS, planned_before=0):
        return Fill(FillId(fill_id), ExchangeId(exchange), "q" * 8, units, principal // units, principal, fee, 1,
                    planned_before)

    def open(self, fill_id="f1", accounts=ACCOUNTS, **kw):
        return self.settlement.open_fill(self.fill(fill_id, **kw), accounts, PARTIES)

    def open_retry(self, fill_id="f1", **kw):
        """Open, retrying while the provider is uncertain or down, as a caller would."""
        for _ in range(10):
            result = self.open(fill_id, **kw)
            if result is not st.OpenResult.PENDING:
                return result
            self.clock.now += 10
        return result

    def payout_keys(self):
        return [key for key, (kind, status) in self.provider.books()["ops"].items()
                if kind == "payout" and status == p.ProviderStatus.PAYOUT_PENDING.value]

    def drive(self, fill_id="f1", rounds=60):
        """Let the world run: events arrive, the node ticks, payouts settle, time passes."""
        return self.drive_many((fill_id,), rounds)[fill_id]

    def drive_many(self, fill_ids, rounds=60):
        for _ in range(rounds):
            self.settlement.pump(self.provider.deliver())
            for fill_id in fill_ids:
                self.settlement.tick(fill_id)
            for key in self.payout_keys():
                self.provider.settle_payout(key)
            self.clock.now += 10
            if all(self.settlement.settled(f) for f in fill_ids) and not self.payout_keys():
                self.settlement.pump(self.provider.deliver())
                for fill_id in fill_ids:
                    self.settlement.tick(fill_id)
                break
        return {f: self.settlement.state(f) for f in fill_ids}

    def balances(self):
        return self.authority.balances()

    def effects(self):
        with sqlite3.connect(self.journal_path) as db:
            return [r[0] for r in db.execute("SELECT effect FROM journal_effects ORDER BY seq")]

    def ops(self, kind):
        return [k for k, (kind_, _) in self.provider.books()["ops"].items() if kind_ == kind]


def violations(env, fill_id="f1"):
    found = []
    balances = env.balances()
    holders = [a for a in (SALE, fill_acct(fill_id), f"recovered:x1:{fill_id}", "buyer") if balances.get(a, 0)]
    if len(holders) != 1 or balances[holders[0]] != UNITS:
        found.append(f"S-1: the fill's P is not wholly in one place: {balances}")
    names = env.effects()
    mine = [e for e in names if e.startswith(f"fill:x1:{fill_id}:")]
    for what in ("p-hold", "p-transfer", "p-release", "p-return"):
        if mine.count(f"fill:x1:{fill_id}:{what}") > 1:
            found.append(f"S-3: {what} happened twice")
    if f"fill:x1:{fill_id}:p-transfer" in mine and f"fill:x1:{fill_id}:p-release" in mine:
        found.append("S-1: both transferred and released")
    state = env.settlement.state(fill_id)
    if state is None:
        return found
    early = (st.State.INTENT, st.State.P_HELD, st.State.MATCHED, st.State.CAPTURE_REQUESTED, st.State.CASH_CONFIRMED,
             st.State.RELEASED, st.State.REFUSED)
    if balances.get("buyer") and state in early:
        found.append(f"S-1: P reached the buyer before the cash was confirmed ({state.value})")
    charged = env.settlement.fee_charged(fill_id)
    if charged not in (0, FEE) or (charged and state in early[:4] + early[5:]):
        found.append(f"S-3: fee {charged} charged in {state.value}")
    ops = env.provider.books()["ops"]
    kinds = [kind for kind, _ in ops.values()]
    if "capture" in kinds and any(kind == "release" and status == "released" for kind, status in ops.values()):
        found.append("S-2: cash both captured and released")
    if state is st.State.RELEASED and any(kind == "capture" for kind, _ in ops.values()):
        found.append("S-2: the fill was released but its cash was captured")
    if state in (st.State.INTENT, st.State.P_HELD, st.State.REFUSED) and ("capture" in kinds or "reserve" in kinds
                                                                          and state is not st.State.P_HELD):
        found.append(f"S-9: cash touched for a fill that is {state.value}")
    for kind in ("reserve", "capture", "payout", "release", "refund"):
        if kinds.count(kind) > 1:
            found.append(f"a second {kind} reached the provider")
    if env.provider.books()["power"]["bp-1"]["reserved"] < 0:
        found.append("negative reservation")
    return found


def settled_ok(env, fill_id="f1"):
    state = env.settlement.state(fill_id)
    assert state is st.State.PAYOUT_CONFIRMED, state
    assert env.balances() == {"treasury": SUPPLY - UNITS, "buyer": UNITS}
    assert env.settlement.fee_charged(fill_id) == FEE
    power = env.provider.books()["power"]["bp-1"]
    assert power == {"amount": 10_000 - PRINCIPAL - FEE, "reserved": 0}, power


@pytest.fixture
def env_factory(tmp_path):
    counter = itertools.count()
    made = []

    def make():
        folder = tmp_path / f"e{next(counter)}"
        folder.mkdir()
        env = Env(folder)
        made.append(env)
        return env
    yield make
    for env in made:
        env.authority.close()


def test_a_sale_runs_from_match_to_confirmed_payout(env_factory):
    env = env_factory()
    assert env.open() is st.OpenResult.OPENED and env.open() is st.OpenResult.ALREADY
    assert env.drive() is st.State.PAYOUT_CONFIRMED
    settled_ok(env)
    assert violations(env) == []
    assert env.settlement.view("f1") == {"state": "payout-confirmed", "stage": "payout confirmed", "p_at": "buyer"}


def test_a_bare_journal_is_not_accepted(tmp_path):
    journal = IssuerJournal(tmp_path / "bare.sqlite", "test.one", SUPPLY)
    with pytest.raises(TypeError):
        st.Settlement(tmp_path / "s.sqlite", journal, object())


FAULTS = [
    ("none", None, {}),
    ("timeout-after+0", fp.Fault.TIMEOUT_AFTER_EFFECT, {"after": 0}),
    ("timeout-after+1", fp.Fault.TIMEOUT_AFTER_EFFECT, {"after": 1}),
    ("timeout-after+2", fp.Fault.TIMEOUT_AFTER_EFFECT, {"after": 2}),
    ("timeout-before+0", fp.Fault.TIMEOUT_BEFORE_EFFECT, {"after": 0}),
    ("timeout-before+1", fp.Fault.TIMEOUT_BEFORE_EFFECT, {"after": 1}),
    ("duplicate-event+1", fp.Fault.DUPLICATE_EVENT, {"after": 1}),
    ("reordered", fp.Fault.REORDERED_EVENTS, {}),
    ("forged+1", fp.Fault.FORGED_EVENT, {"after": 1}),
    ("outage", fp.Fault.OUTAGE, {"until": contract.NOW + 25}),
    ("payout-returned", fp.Fault.PAYOUT_RETURNED, {}),
]


def armed(env, fault, options):
    if fault is not None:
        env.provider.arm(fault, **options)


def points_of(env_factory, fault, options):
    env = env_factory()
    armed(env, fault, options)
    seen = []
    env.settlement._point = seen.append
    env.open_retry()
    env.drive()
    return seen


def test_every_crash_point_under_every_fault_converges_and_keeps_the_invariants(env_factory):
    failures, runs = [], 0
    for name, fault, options in FAULTS:
        points = points_of(env_factory, fault, options)
        assert {"intent", "p-hold-posted", "p-held", "dispatched:reserve", "moved:matched", "dispatched:capture",
                "p-posted", "moved:p-transferred", "dispatched:payout"} <= set(points), (name, points)
        for index in range(len(points)):
            env = env_factory()
            armed(env, fault, options)
            count = itertools.count()

            def crash(point, target=index, counter=count):
                if next(counter) == target:
                    raise Crash(point)
            env.settlement._point = crash
            try:
                env.open_retry()
                env.drive()
            except Crash:
                pass
            env.boot()
            runs += 1
            try:
                assert env.open_retry() in (st.OpenResult.OPENED, st.OpenResult.ALREADY)
                state = env.drive()
                assert state is st.State.PAYOUT_CONFIRMED, state
                settled_ok(env)
                assert violations(env) == []
            except AssertionError as err:
                failures.append((name, index, points[index], str(err)[:200]))
    assert runs > 150
    assert failures == []


def two_fills(env, fund=True):
    if fund:         # funding replaces the buyer's books, so a restarted node must not fund again
        env.provider.fund("bp-1", "buyer-1", "USD", 20_000, contract.LATER)
    for fill_id, fee, before in (("f1", 13, 0), ("f2", 12, 5000)):
        assert env.open_retry(fill_id, principal=5000, fee=fee, units=50, planned_before=before) in (
            st.OpenResult.OPENED, st.OpenResult.ALREADY)


def test_the_crash_matrix_with_two_fills_on_one_order_converges(env_factory):
    """T17: the generated matrix with two fills drawing on one reservation, not one."""
    chosen = [FAULTS[i] for i in (0, 1, 4, 9, 10)]
    failures, runs = [], 0
    for name, fault, options in chosen:
        env = env_factory()
        armed(env, fault, options)
        seen = []
        env.settlement._point = seen.append
        two_fills(env)
        env.drive_many(("f1", "f2"))
        for index in range(len(seen)):
            env = env_factory()
            armed(env, fault, options)
            counter = itertools.count()

            def crash(point, target=index, c=counter):
                if next(c) == target:
                    raise Crash(point)
            env.settlement._point = crash
            try:
                two_fills(env)
                env.drive_many(("f1", "f2"))
            except Crash:
                pass
            env.boot()
            runs += 1
            try:
                two_fills(env, fund=False)
                states = env.drive_many(("f1", "f2"))
                assert set(states.values()) == {st.State.PAYOUT_CONFIRMED}, states
                assert env.balances() == {"treasury": SUPPLY - UNITS, "buyer": UNITS}
                assert env.settlement.fee_charged("f1") + env.settlement.fee_charged("f2") == 25
                assert env.provider.books()["power"]["bp-1"] == {"amount": 20_000 - 10_000 - 25, "reserved": 0}
            except AssertionError as err:
                failures.append((name, index, seen[index], str(err)[:200]))
    assert runs > 150
    assert failures == []


def test_a_crash_matrix_that_misses_a_point_would_be_noticed(env_factory):
    assert len(points_of(env_factory, None, {})) >= 14


def test_an_uncertain_capture_is_looked_up_never_released(env_factory):
    env = env_factory()
    env.open()
    env.provider.arm(fp.Fault.TIMEOUT_AFTER_EFFECT)
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    assert env.settlement.state("f1") is st.State.CAPTURE_REQUESTED
    assert env.settlement.cancel("f1") is st.CancelResult.NOT_ALLOWED, "the capture is looked up and found captured"
    assert env.settlement.state("f1") is st.State.CASH_CONFIRMED or env.settlement.state("f1") is st.State.P_TRANSFERRED
    assert env.drive() is st.State.PAYOUT_CONFIRMED
    assert not env.ops("release")
    settled_ok(env)


def test_a_cancel_before_capture_releases_the_cash_and_returns_the_p_once(env_factory):
    env = env_factory()
    env.open()
    assert env.settlement.cancel("f1") is st.CancelResult.CANCELLED
    assert env.settlement.state("f1") is st.State.RELEASED
    assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS}
    assert env.provider.books()["power"]["bp-1"] == {"amount": 10_000, "reserved": 0}
    assert env.settlement.fee_charged("f1") == 0
    for _ in range(3):
        env.settlement.tick("f1")
        env.settlement.pump(env.provider.deliver())
    assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS}
    assert violations(env) == []
    assert env.settlement.cancel("f1") is st.CancelResult.NOT_ALLOWED


def test_cancel_survives_a_crash_at_every_point_and_an_outage(env_factory):
    for fault, options in ((None, {}), (fp.Fault.TIMEOUT_AFTER_EFFECT, {"after": 0}),
                           (fp.Fault.OUTAGE, {"until": contract.NOW + 25}), (fp.Fault.TIMEOUT_BEFORE_EFFECT, {})):
        env = env_factory()
        env.open()
        armed(env, fault, options)
        seen = []
        env.settlement._point = seen.append
        env.settlement.cancel("f1")
        env.drive()
        for index in range(len(seen)):
            env = env_factory()
            env.open()
            armed(env, fault, options)
            counter = itertools.count()

            def crash(point, target=index, c=counter):
                if next(c) == target:
                    raise Crash(point)
            env.settlement._point = crash
            try:
                env.settlement.cancel("f1")
            except Crash:
                pass
            env.boot()
            env.drive()
            assert env.settlement.state("f1") is st.State.RELEASED, (fault, index, seen[index])
            assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS}, (fault, index)
            assert env.provider.books()["power"]["bp-1"] == {"amount": 10_000, "reserved": 0}, (fault, index)
            assert violations(env) == [], (fault, index)


def test_an_outage_cannot_release_the_reserved_cash_twice(env_factory):
    env = env_factory()
    env.open()
    env.provider.arm(fp.Fault.OUTAGE, until=contract.NOW + 100)
    env.settlement.cancel("f1")
    for _ in range(5):
        env.settlement.tick("f1")
    assert env.provider.books()["power"]["bp-1"]["reserved"] == PRINCIPAL + FEE, "nothing released during the outage"
    env.clock.now += 200
    for _ in range(3):
        env.settlement.tick("f1")
    assert len(env.ops("release")) == 1 and env.provider.books()["power"]["bp-1"]["reserved"] == 0


def test_a_returned_payout_is_retried_under_the_same_key_and_p_moves_once(env_factory):
    env = env_factory()
    env.provider.arm(fp.Fault.PAYOUT_RETURNED)
    env.open()
    assert env.drive() is st.State.PAYOUT_CONFIRMED
    history = [r[0] for r in sqlite3.connect(env.tmp / "s.sqlite").execute("SELECT to_state FROM history ORDER BY n")]
    assert "payout-returned" in history and history.count("payout-requested") == 2
    assert env.ops("payout") == ["x1/f1/payout/once"]
    assert env.effects().count("fill:x1:f1:p-transfer") == 1 and violations(env) == []
    settled_ok(env)


def test_a_forged_event_changes_nothing_and_is_recorded(env_factory):
    env = env_factory()
    env.open()
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    raws = env.provider.deliver()
    capture = [r for r in raws if b'"status":"captured"' in r]
    assert capture, "the provider confirmed the capture"
    at = capture[0].index(b'"sig":"') + 7
    forged = capture[0][:at] + (b"0" if capture[0][at:at + 1] != b"0" else b"1") + capture[0][at + 1:]
    before = env.settlement.state("f1")
    counts = env.settlement.pump([forged, b"garbage", b""])
    assert counts["refused"] == 3 and env.settlement.state("f1") is before is st.State.CAPTURE_REQUESTED
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM inbox WHERE outcome='refused'").fetchone()[0] == 3
    assert env.balances() == {"treasury": SUPPLY - UNITS, fill_acct(): UNITS} and env.settlement.fee_charged("f1") == 0
    assert env.settlement.pump(capture)["applied"] == 1 and env.settlement.state("f1") is st.State.CASH_CONFIRMED


def test_p_does_not_leave_the_fill_account_before_the_cash_is_confirmed(env_factory):
    env = env_factory()
    env.open()
    env.provider.arm(fp.Fault.OUTAGE, until=contract.NOW + 1_000)
    for _ in range(4):
        env.settlement.tick("f1")
        assert env.settlement.state("f1") is st.State.CAPTURE_REQUESTED
        assert env.balances() == {"treasury": SUPPLY - UNITS, fill_acct(): UNITS}
        assert violations(env) == []


def test_an_uncertain_capture_that_cannot_be_looked_up_is_never_released(env_factory):
    env = env_factory()
    env.open()
    env.provider.arm(fp.Fault.TIMEOUT_AFTER_EFFECT, after=1)
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    env.provider.arm(fp.Fault.OUTAGE, until=contract.NOW + 100)
    assert env.settlement.cancel("f1") is st.CancelResult.PENDING
    assert env.settlement.state("f1") is st.State.CAPTURE_REQUESTED
    assert not env.ops("release")
    assert env.balances() == {"treasury": SUPPLY - UNITS, fill_acct(): UNITS}
    env.clock.now += 200
    assert env.drive() is st.State.PAYOUT_CONFIRMED
    settled_ok(env)


def test_a_duplicate_event_is_applied_once(env_factory):
    env = env_factory()
    env.provider.arm(fp.Fault.DUPLICATE_EVENT, after=1)
    env.open()
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    raws = env.provider.deliver()
    first = env.settlement.pump(raws)
    again = env.settlement.pump(raws)
    assert len(raws) >= 3 and first["duplicate"] == 0, "the double sends one event twice under two message ids"
    assert again["applied"] == 0 and again["duplicate"] == len(raws), "a replay of the same ids is dropped"
    assert env.settlement.state("f1") is st.State.CASH_CONFIRMED, "the repeated confirmation moved it once"
    assert env.settlement.fee_charged("f1") == FEE
    assert env.drive() is st.State.PAYOUT_CONFIRMED and violations(env) == []
    assert env.settlement.fee_charged("f1") == FEE


def test_an_event_that_changes_nothing_is_recorded_as_ignored_with_its_reason(env_factory):
    env = env_factory()
    env.provider.arm(fp.Fault.DUPLICATE_EVENT, after=1)
    env.open()
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    counts = env.settlement.pump(env.provider.deliver())
    assert counts["applied"] == 1 and counts["ignored"] >= 1, counts
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        rows = db.execute("SELECT outcome, reason FROM inbox").fetchall()
    assert all(reason for outcome, reason in rows if outcome == "ignored"), rows
    assert sum(1 for outcome, _ in rows if outcome == "applied") == counts["applied"]
    assert any("captured" in reason for outcome, reason in rows if outcome == "ignored")


def test_reordered_events_do_not_confirm_early_or_twice(env_factory):
    env = env_factory()
    env.provider.arm(fp.Fault.REORDERED_EVENTS)
    env.open()
    assert env.drive() is st.State.PAYOUT_CONFIRMED
    settled_ok(env)


def paid_out(env):
    env.open()
    assert env.drive() is st.State.PAYOUT_CONFIRMED


def test_a_reversed_capture_needs_only_the_p_back_and_no_refund_is_sent(env_factory):
    """S-11: a chargeback before the seller was paid returns the buyer's cash itself; on a press only the P comes back,
    to the seller's reserve, and no cash refund is ever sent."""
    env = env_factory()
    env.open()
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        db.execute("UPDATE legs SET status='sent' WHERE leg='capture'")
    env.provider.chargeback("x1/f1/capture/once")
    reversal = [r for r in env.provider.deliver() if b'"status":"reversed"' in r]
    env.settlement.pump(reversal)
    assert env.settlement.state("f1") is st.State.HELD
    held_balances = env.balances()
    assert env.settlement.refund("f1", press=False) is st.State.HELD and env.balances() == held_balances
    assert env.settlement.refund("f1", press=PRESS) is st.State.REFUNDED
    assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS}
    assert env.settlement.refund("f1", press=PRESS) is st.State.REFUNDED
    assert env.ops("refund") == [], "a refund of a reversed capture is never sent"
    assert env.provider.books()["power"]["bp-1"]["amount"] == 10_000, "the buyer's cash came back once"
    assert env.effects().count("fill:x1:f1:p-return") == 1
    assert env.settlement.liabilities() == []


def test_a_reversal_after_the_payout_recovers_the_p_into_a_held_account_not_the_sellers_reserve(env_factory):
    """T21 / S-18: the seller holds the payout, so the P is not given back to them; it waits in recovered:<ex>:<fill>."""
    env = env_factory()
    paid_out(env)
    env.provider.chargeback("x1/f1/capture/once")
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.state("f1") is st.State.HELD
    assert env.settlement.refund("f1", press=PRESS) is st.State.HELD
    assert env.balances() == {"treasury": SUPPLY - UNITS, "recovered:x1:f1": UNITS}, "not in the seller's reserve"
    assert env.ops("refund") == []
    assert env.settlement.liabilities() == [{"fill_id": "f1", "state": "held", "owed_minor": PRINCIPAL}]
    assert env.settlement.settled("f1") is False
    for _ in range(3):
        env.settlement.tick("f1")
    assert env.settlement.state("f1") is st.State.HELD and env.balances()["recovered:x1:f1"] == UNITS
    assert violations(env) == []


def test_a_reversal_while_the_payout_is_in_flight_is_the_same_and_stays_held_when_it_confirms_later(env_factory):
    """T21, second half: the payout was only pending when the capture was reversed."""
    env = env_factory()
    env.open()
    env.settlement.tick("f1")
    assert env.settlement.state("f1") is st.State.PAYOUT_REQUESTED and env.payout_keys()
    env.provider.chargeback("x1/f1/capture/once")
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.state("f1") is st.State.HELD
    env.settlement.refund("f1", press=PRESS)
    assert env.balances()["recovered:x1:f1"] == UNITS and env.ops("refund") == []
    env.provider.settle_payout(env.payout_keys()[0])
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.state("f1") is st.State.HELD and env.settlement.settled("f1") is False
    assert env.balances()["recovered:x1:f1"] == UNITS


def test_a_payout_returned_and_asked_for_again_stands_again_when_the_capture_is_then_reversed(env_factory):
    env = env_factory()
    env.provider.arm(fp.Fault.PAYOUT_RETURNED)
    env.open()
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    env.settlement.pump(env.provider.deliver())
    env.settlement.tick("f1")
    env.provider.settle_payout(env.payout_keys()[0])                       # returned
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.state("f1") is st.State.PAYOUT_RETURNED
    env.settlement.tick("f1")                                              # asked for again under the same key
    assert env.settlement.state("f1") is st.State.PAYOUT_REQUESTED and env.payout_keys()
    env.provider.chargeback("x1/f1/capture/once")
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.state("f1") is st.State.HELD
    env.settlement.refund("f1", press=PRESS)
    assert env.balances()["recovered:x1:f1"] == UNITS, "the second payout stands, so the P is not given back to the seller"


def test_the_recovered_p_goes_back_to_the_seller_once_the_payout_that_stood_is_returned(env_factory):
    env = env_factory()
    env.open()
    env.settlement.tick("f1")
    env.provider.chargeback("x1/f1/capture/once")
    env.settlement.pump(env.provider.deliver())
    env.settlement.refund("f1", press=PRESS)
    assert env.balances()["recovered:x1:f1"] == UNITS
    env.provider.return_payout(env.payout_keys()[0])
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.tick("f1") is st.State.REFUNDED
    assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS} and env.ops("refund") == []
    assert env.effects().count("fill:x1:f1:p-restore") == 1


def test_a_p_in_the_reserve_with_a_payout_standing_never_triggers_a_cash_refund(env_factory):
    """Not reachable by the workflow itself; held by a stored state a crash or a bug could leave."""
    env = env_factory()
    paid_out(env)
    env.provider.return_payout("x1/f1/payout/once")
    env.settlement.pump(env.provider.deliver())          # held, payout returned
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        db.execute("UPDATE fills SET p_at='reserve', payout_returned=0")
    assert env.settlement.refund("f1", press=PRESS) is st.State.HELD
    assert env.ops("refund") == []


def test_a_payout_queued_while_the_provider_was_down_is_never_sent_for_a_held_fill(env_factory):
    """T22 / S-19: checked against a provider that would have paid."""
    env = env_factory()
    env.open()
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.state("f1") is st.State.CASH_CONFIRMED
    calls = []
    real_payout = env.provider.payout
    env.provider.payout = lambda *args, **kw: calls.append(args) or real_payout(*args, **kw)
    env.provider.arm(fp.Fault.OUTAGE, until=contract.NOW + 1_000)
    env.settlement.tick("f1")
    assert env.settlement.state("f1") is st.State.PAYOUT_REQUESTED
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        assert db.execute("SELECT status FROM legs WHERE leg='payout'").fetchone()[0] == "pending"
    env.provider.chargeback("x1/f1/capture/once")
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.state("f1") is st.State.HELD
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        assert db.execute("SELECT status FROM legs WHERE leg='payout'").fetchone()[0] == "cancelled"
    calls.clear()                                          # the attempt made during the outage was refused
    env.clock.now += 2_000
    for _ in range(3):
        env.settlement.tick("f1")
    assert [c for c in calls if c[0].endswith("/payout/once")] == [], "no payout call was made for a held fill"
    assert env.ops("payout") == []


def test_a_payout_leg_that_slipped_past_the_cancel_is_still_not_sent_for_a_reversed_capture(env_factory):
    """S-19 at the point of sending, not only at the move to HELD."""
    env = env_factory()
    env.open()
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    env.settlement.pump(env.provider.deliver())
    env.provider.arm(fp.Fault.OUTAGE, until=contract.NOW + 1_000)
    env.settlement.tick("f1")
    env.provider.chargeback("x1/f1/capture/once")
    with sqlite3.connect(env.tmp / "s.sqlite") as db:     # a node that learned of the reversal but did not cancel the leg
        db.execute("UPDATE fills SET capture_reversed=1")
    calls = []
    real_payout = env.provider.payout
    env.provider.payout = lambda *args, **kw: calls.append(args) or real_payout(*args, **kw)
    env.clock.now += 2_000
    env.settlement.dispatch("f1")
    assert calls == []


def late_return(env):
    """A payout confirmed, then returned by the provider (S-14): the fill is held and the seller was not paid."""
    paid_out(env)
    env.provider.return_payout("x1/f1/payout/once")
    env.settlement.pump(env.provider.deliver())


def test_a_payout_returned_after_confirmation_holds_the_fill_and_lists_it(env_factory):
    env = env_factory()
    late_return(env)
    assert env.settlement.state("f1") is st.State.HELD
    assert env.settlement.liabilities() == [{"fill_id": "f1", "state": "held", "owed_minor": PRINCIPAL}]
    assert env.settlement.settled("f1") is True, "held with no refund pressed: nothing the workflow can do alone"


def test_a_refund_returns_the_p_first_and_sends_no_cash_while_it_is_unrecovered(env_factory):
    """S-10: the buyer has moved the P on; the press changes nothing until it can be recovered, then the cash follows."""
    env = env_factory()
    late_return(env)
    outcome, _ = env.authority.execute({"issuer": "test.one", "effect": "elsewhere", "action": "elsewhere",
                                        "postings": {"buyer": -UNITS, "somewhere": UNITS}})
    assert outcome is Outcome.POSTED
    assert env.settlement.refund("f1", press=PRESS) is st.State.HELD
    assert env.ops("refund") == [], "no cash moves while the P is unrecovered"
    assert env.provider.books()["power"]["bp-1"]["amount"] == 10_000 - PRINCIPAL - FEE
    assert env.settlement.liabilities() and env.settlement.settled("f1") is False
    outcome, _ = env.authority.execute({"issuer": "test.one", "effect": "back", "action": "back",
                                        "postings": {"somewhere": -UNITS, "buyer": UNITS}})
    assert outcome is Outcome.POSTED
    assert env.settlement.tick("f1") is st.State.REFUNDED
    assert env.ops("refund") == ["x1/f1/refund/once"]
    assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS}
    assert env.provider.books()["power"]["bp-1"]["amount"] == 10_000, "the cash went back to the buyer"
    assert env.settlement.liabilities() == []


def test_a_chargeback_after_payout_with_the_p_moved_on_is_held_listed_and_not_settled(env_factory):
    """T14."""
    env = env_factory()
    paid_out(env)
    env.provider.chargeback("x1/f1/capture/once")
    env.settlement.pump(env.provider.deliver())
    env.authority.execute({"issuer": "test.one", "effect": "elsewhere", "action": "elsewhere",
                           "postings": {"buyer": -UNITS, "somewhere": UNITS}})
    assert env.settlement.refund("f1", press=PRESS) is st.State.HELD
    assert env.ops("refund") == []
    assert [x["fill_id"] for x in env.settlement.liabilities()] == ["f1"]
    assert env.settlement.settled("f1") is False


def test_settled_is_false_for_a_held_fill_whose_refund_is_pressed_but_not_complete(env_factory):
    env = env_factory()
    late_return(env)
    assert env.settlement.settled("f1") is True
    with sqlite3.connect(env.tmp / "s.sqlite") as db:        # a completed refund leg with the P still with the buyer
        db.execute("UPDATE fills SET refund_pressed=1")
    assert env.settlement.settled("f1") is False


def test_a_refund_needs_a_typed_press(env_factory):
    env = env_factory()
    late_return(env)
    for not_a_press in (True, "yes", 1, None, object(), {"press": True}):
        assert env.settlement.refund("f1", press=not_a_press) is st.State.HELD
    assert env.ops("refund") == []
    assert env.settlement.refund("f1", press=PRESS) is st.State.REFUNDED


def test_a_refund_is_its_own_leg_and_returns_p_once(env_factory):
    env = env_factory()
    late_return(env)
    columns = "fill_id, exchange_id, quote_hash, p_units, principal, fee_increment, fee_charged"
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        before = db.execute(f"SELECT {columns} FROM fills").fetchall()
    env.settlement.refund("f1", press=PRESS)
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        after = db.execute(f"SELECT {columns} FROM fills").fetchall()
        legs = [r[0] for r in db.execute("SELECT leg FROM legs ORDER BY rowid")]
    assert after == before, "the fill's own record is never edited by a refund"
    assert legs.count("refund") == 1 and legs.count("capture") == 1
    assert env.effects().count("fill:x1:f1:p-return") == 1


def test_outstanding_liabilities_are_shown(env_factory):
    env = env_factory()
    env.open()
    env.settlement.tick("f1")
    assert env.settlement.state("f1") in (st.State.PAYOUT_REQUESTED, st.State.P_TRANSFERRED)
    assert env.settlement.liabilities() == [{"fill_id": "f1", "state": env.settlement.state("f1").value,
                                             "owed_minor": PRINCIPAL}]
    env.drive()
    assert env.settlement.liabilities() == []


@pytest.mark.parametrize("order", [("f1", "f2"), ("f2", "f1")])
def test_two_planned_fills_confirm_in_either_order_and_the_fee_total_is_the_one_order_fee(env_factory, order):
    """S-12 / T15: planned 13 then 12; confirmed in either order, both complete and the fee total is cash_fee(10000)."""
    env = env_factory()
    two_fills(env)
    for fill_id in order:
        assert env.drive(fill_id) is st.State.PAYOUT_CONFIRMED
    assert (env.settlement.fee_charged("f1"), env.settlement.fee_charged("f2")) == (13, 12)
    for _ in range(3):
        env.settlement.pump(env.provider.deliver())
        env.settlement.tick("f1"), env.settlement.tick("f2")
    assert (env.settlement.fee_charged("f1"), env.settlement.fee_charged("f2")) == (13, 12)
    assert env.balances() == {"treasury": SUPPLY - UNITS, "buyer": UNITS}


def test_two_fills_planned_from_one_starting_point_the_second_to_confirm_is_held(env_factory):
    env = env_factory()
    env.provider.fund("bp-1", "buyer-1", "USD", 20_000, contract.LATER)
    for fill_id in ("f1", "f2"):             # both planned as if first: their plan intervals overlap
        env.open_retry(fill_id, principal=5000, fee=13, units=50)
    assert env.drive("f1") is st.State.PAYOUT_CONFIRMED
    assert env.drive("f2") is st.State.HELD and env.settlement.fee_charged("f2") == 0


def test_a_fee_that_is_not_the_planned_increment_holds_the_fill(env_factory):
    env = env_factory()
    env.open_retry("f1", fee=14)                    # the plan from 0 for 5000 charges 13
    assert env.drive("f1") is st.State.HELD and env.settlement.fee_charged("f1") == 0


@pytest.mark.parametrize("hundreds", [(100, 100, 100)])
def test_a_fill_planned_to_pay_one_more_than_its_own_fee_is_not_held_for_confirming_first(env_factory, hundreds):
    """The case that showed section 10's S-12 wrong: fills of 100, 100, 100 are planned 0, 1, 0."""
    from agent_friday.services.exchange import fees
    env = env_factory()
    env.provider.fund("bp-1", "buyer-1", "USD", 1_000_000, contract.LATER)
    planned, before = fees.planned_increments(hundreds), [0, 100, 200]
    assert planned == [0, 1, 0]
    for n in range(3):
        env.open_retry(f"f{n + 1}", principal=hundreds[n], fee=planned[n], units=10, planned_before=before[n])
    assert env.drive("f2") is st.State.PAYOUT_CONFIRMED, "planned 1, confirmed first: not held"
    assert env.drive("f3") is st.State.PAYOUT_CONFIRMED and env.drive("f1") is st.State.PAYOUT_CONFIRMED


def test_every_plan_of_up_to_three_fills_confirmed_in_every_order_completes_with_the_one_order_fee(env_factory):
    """T20 / S-12'."""
    from agent_friday.services.exchange import fees
    from agent_friday.services.issuer_journal import cash_fee
    runs = 0
    for count in (2, 3):
        for principals in itertools.product((100, 300, 1000), repeat=count):
            planned = fees.planned_increments(principals)
            before = [sum(principals[:i]) for i in range(count)]
            for order in itertools.permutations(range(count)):
                env = env_factory()
                env.provider.fund("bp-1", "buyer-1", "USD", 1_000_000, contract.LATER)
                for n in range(count):
                    assert env.open_retry(f"f{n + 1}", principal=principals[n], fee=planned[n], units=10,
                                          planned_before=before[n]) is st.OpenResult.OPENED
                for n in order:
                    state = env.drive(f"f{n + 1}")
                    assert state is st.State.PAYOUT_CONFIRMED, (principals, order, n, state)
                total = sum(env.settlement.fee_charged(f"f{n + 1}") for n in range(count))
                assert total == cash_fee(sum(principals)), (principals, order, total)
                runs += 1
    assert runs == 9 * 2 + 27 * 6


def test_a_fill_cannot_be_opened_against_any_reserve_but_its_sellers(env_factory):
    """T23 / S-20: naming the treasury as the seller sells nothing of the treasury's."""
    env = env_factory()
    before = env.balances()
    assert env.open("f1", accounts=st.Accounts(seller="treasury", buyer="buyer")) is st.OpenResult.REFUSED
    assert env.balances() == before and env.provider.books()["ops"] == {}
    row = sqlite3.connect(env.tmp / "s.sqlite").execute("SELECT reserve_account FROM fills").fetchone()[0]
    assert row == "sale:x1:treasury", "the reserve is derived from the seller, never passed"
    assert "reserve" not in st.Accounts.__dataclass_fields__


def test_a_refunded_fills_principal_no_longer_counts_toward_the_fee_check(env_factory):
    """S-13: f1's capture was reversed, so its charge does not count; f2, planned as if first, completes."""
    env = env_factory()
    env.provider.fund("bp-1", "buyer-1", "USD", 20_000, contract.LATER)
    for fill_id in ("f1", "f2"):
        env.open_retry(fill_id, principal=5000, fee=13, units=50)
    assert env.drive("f1") is st.State.PAYOUT_CONFIRMED
    env.provider.chargeback("x1/f1/capture/once")
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.state("f1") is st.State.HELD
    assert env.drive("f2") is st.State.PAYOUT_CONFIRMED and env.settlement.fee_charged("f2") == 13


def test_a_refunded_fills_fee_no_longer_counts_toward_the_fee_check(env_factory):
    """S-13, the refunded half: f1 is paid out, returned, held and refunded; f2, planned as if first, completes."""
    env = env_factory()
    env.provider.fund("bp-1", "buyer-1", "USD", 20_000, contract.LATER)
    for fill_id in ("f1", "f2"):
        env.open_retry(fill_id, principal=5000, fee=13, units=50)
    assert env.drive("f1") is st.State.PAYOUT_CONFIRMED
    env.provider.return_payout("x1/f1/payout/once")
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.refund("f1", press=PRESS) is st.State.REFUNDED
    assert env.drive("f2") is st.State.PAYOUT_CONFIRMED and env.settlement.fee_charged("f2") == 13


def test_cash_can_only_be_reserved_after_the_p_is_held_and_captured_only_after_that():
    """S-9 by structure: the table gives no way to MATCHED except through P_HELD, and none to a capture except from MATCHED."""
    into = lambda target: sorted(s.value for s in st.State if target in st.TRANSITIONS[s])
    assert into(st.State.P_HELD) == ["intent"]
    assert into(st.State.MATCHED) == ["p-held"]
    assert into(st.State.CAPTURE_REQUESTED) == ["matched"]
    assert st.TRANSITIONS[st.State.INTENT] == {st.State.P_HELD, st.State.REFUSED}


def test_the_fee_is_charged_at_one_place_only():
    source = SETTLEMENT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and getattr(call.func, "attr", getattr(call.func, "id", "")) in (
                        "fee_increment", "accrue", "within_cumulative"):
                    sites.append(node.name)
    assert sites == ["_confirm_cash"]
    writers = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and "SET fee_charged" in (ast.get_source_segment(source, n) or "")]
    assert writers == ["_confirm_cash"]
    assert st.FEE_ACCRUES_AT is st.State.CASH_CONFIRMED


def test_nothing_but_a_confirmed_capture_ever_charges_a_fee(env_factory):
    env = env_factory()
    env.open()
    for _ in range(2):
        assert env.settlement.fee_charged("f1") == 0
        env.settlement.advance("f1")
    env.settlement.cancel("f1")
    env.drive()
    assert env.settlement.fee_charged("f1") == 0 and env.settlement.state("f1") is st.State.RELEASED


def test_cash_never_appears_in_the_p_journal(env_factory):
    env = env_factory()
    env.open()
    env.drive()
    assert sum(env.balances().values()) == SUPPLY
    with sqlite3.connect(env.journal_path) as db:
        accounts = {r[0] for r in db.execute("SELECT account FROM journal_balances")}
    assert accounts <= {"treasury", SALE, fill_acct(), "buyer"}


def test_a_replayed_fill_on_another_node_pays_nothing(env_factory):
    first, other = env_factory(), env_factory()
    first.open()
    first.drive()
    other.provider = fp.FakeProvider(other.tmp / "q.sqlite", SEED, other.clock, synchronous="OFF")    # never funded this buyer
    other.boot()
    assert other.open() is st.OpenResult.REFUSED
    assert other.settlement.state("f1") is st.State.REFUSED
    assert other.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS}, "the P went back to the sale's reserve"
    assert other.provider.books()["ops"] == {}
    assert other.settlement.settled("f1") is True


# -- opening (design 10.6) ------------------------------------------------------------------------------

def test_a_second_fill_against_one_reservation_is_refused_at_open_and_touches_no_cash(env_factory):
    """T12: two fills of 100 units against a reservation of 100."""
    env = env_factory()
    assert env.open("f1") is st.OpenResult.OPENED
    assert env.open("f2") is st.OpenResult.REFUSED
    assert env.settlement.state("f2") is st.State.REFUSED
    assert env.ops("reserve") == ["x1/f1/reserve/once"], "no cash of the second fill's buyer was reserved"
    assert env.provider.books()["power"]["bp-1"]["reserved"] == PRINCIPAL + FEE
    assert env.drive("f1") is st.State.PAYOUT_CONFIRMED and env.ops("capture") == ["x1/f1/capture/once"]


def test_a_seller_with_nothing_reserved_is_refused_at_open(env_factory):
    env = env_factory()
    nothing = st.Accounts(seller="nobody", buyer="buyer")
    assert env.open("f1", accounts=nothing) is st.OpenResult.REFUSED
    assert env.provider.books()["ops"] == {} and env.provider.books()["power"]["bp-1"]["reserved"] == 0
    assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS}


def test_a_fills_units_come_from_its_lots_and_lot_size(env_factory):
    env = env_factory()
    fill = Fill(FillId("f1"), ExchangeId("x1"), "q" * 8, 20, 250, 5000, FEE, 5, 0)       # 20 lots of 5 units = 100 units
    assert env.settlement.open_fill(fill, ACCOUNTS, PARTIES) is st.OpenResult.OPENED
    assert env.balances() == {"treasury": SUPPLY - UNITS, fill_acct(): 100}
    for bad in (Fill(FillId("f2"), ExchangeId("x1"), "q" * 8, 0, 250, 0, 0, 5, 0),
                Fill(FillId("f3"), ExchangeId("x1"), "q" * 8, 20, 250, 5000, FEE, 0, 0),
                Fill(FillId("f4"), ExchangeId("x1"), "q" * 8, 20, 250, 5000, FEE, 5, -1)):
        with pytest.raises(ValueError):
            env.settlement.open_fill(bad, ACCOUNTS, PARTIES)


def test_every_opening_step_leaves_a_local_row_before_any_provider_call(env_factory):
    """T13: kill the node after every step of opening and never retry."""
    env = env_factory()
    seen = []
    env.settlement._point = seen.append
    env.open()
    opening = seen[:seen.index("opened") if "opened" in seen else len(seen)]
    assert "intent" in seen and len(opening) >= 4
    for index in range(len(seen)):
        env = env_factory()
        counter = itertools.count()

        def crash(point, target=index, c=counter):
            if next(c) == target:
                raise Crash(point)
        env.settlement._point = crash
        try:
            env.open()
        except Crash:
            pass
        assert env.settlement.state("f1") is not None, (index, seen[index])
        if env.ops("reserve"):
            assert env.settlement.state("f1") is not None, "a provider reservation without a local row"
        if seen[index] in ("intent", "p-hold-posted"):
            assert env.ops("reserve") == [], "no provider call before the P is held"


def test_no_capture_is_sent_for_a_fill_whose_p_is_not_in_its_own_account(env_factory):
    """S-9."""
    env = env_factory()
    env.open()
    env.settlement.advance("f1")
    with sqlite3.connect(env.tmp / "s.sqlite") as db:     # the node believes the P is back in the reserve
        db.execute("UPDATE fills SET p_at='reserve'")
    env.settlement.dispatch("f1")
    assert env.ops("capture") == []


# -- the table and the rest -------------------------------------------------------------------------------

def test_every_move_not_in_the_table_is_refused(env_factory):
    env = env_factory()
    env.open()
    for before, after in itertools.product(st.State, repeat=2):
        with sqlite3.connect(env.tmp / "s.sqlite") as db:
            db.execute("UPDATE fills SET state=?", (before.value,))
        try:
            with env.settlement._tx() as db:
                env.settlement._move(db, "f1", after)
            moved = True
        except st.IllegalTransition:
            moved = False
        assert moved == (after in st.TRANSITIONS[before]), (before, after)


def test_released_and_refunded_are_final(env_factory):
    assert st.TRANSITIONS[st.State.RELEASED] == {st.State.HELD} and st.TRANSITIONS[st.State.REFUNDED] == set()
    assert st.TRANSITIONS[st.State.REFUSED] == set()
    env = env_factory()
    env.open()
    env.settlement.cancel("f1")
    env.provider.chargeback("x1/f1/capture/once")
    env.settlement.pump(env.provider.deliver())
    assert env.settlement.state("f1") is st.State.RELEASED


OPS = ["tick", "cancel", "settle", "refund", "pump"]


def apply(env, op):
    if op == "tick":
        env.settlement.tick("f1")
    elif op == "cancel":
        env.settlement.cancel("f1")
    elif op == "settle":
        for key in env.payout_keys():
            env.provider.settle_payout(key)
    elif op == "refund":
        env.settlement.refund("f1", press=PRESS)
    elif op == "pump":
        env.settlement.pump(env.provider.deliver())


def test_every_short_sequence_of_events_keeps_the_invariants_and_terminal_states_are_stable(env_factory):
    stable = {st.State.RELEASED, st.State.REFUNDED}
    sequences = [ops for length in (1, 2) for ops in itertools.product(OPS, repeat=length)]
    sequences += list(itertools.product(("tick", "cancel", "settle", "refund"), repeat=3))
    for ops in sequences:
        env = env_factory()
        env.open()
        for op in ops:
            before = env.settlement.state("f1")
            apply(env, op)
            assert violations(env) == [], (ops, op)
            if before in stable:
                assert env.settlement.state("f1") is before, (ops, op)
        final = env.drive()
        assert violations(env) == [], ops
        assert final in st.TERMINAL | {st.State.HELD}, (ops, final)


def test_cancel_with_a_capture_still_waiting_never_sends_that_capture(env_factory):
    """Found by an independent review: cancel moved the fill to RELEASED but left the pending capture to be
    sent, so the buyer's cash was captured and the P went back to the seller."""
    env = env_factory()
    env.open()
    env.settlement.advance("f1")
    assert env.settlement.cancel("f1") is st.CancelResult.CANCELLED
    assert env.ops("capture") == []
    assert env.provider.books()["power"]["bp-1"] == {"amount": 10_000, "reserved": 0}
    assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS} and violations(env) == []
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        assert db.execute("SELECT status FROM legs WHERE leg='capture'").fetchone()[0] == "cancelled"


def test_cash_captured_after_the_fill_was_released_holds_the_fill_for_a_refund(env_factory):
    env = env_factory()
    env.open()
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")                      # the capture really happens at the provider
    with sqlite3.connect(env.tmp / "s.sqlite") as db:   # while the node believes the fill was released
        db.execute("UPDATE fills SET state='released'")
        db.execute("UPDATE legs SET status='cancelled' WHERE leg='capture'")
    env.settlement.tick("f1")
    assert env.settlement.state("f1") is st.State.HELD
    assert env.settlement.refund("f1", press=PRESS) is st.State.REFUNDED
    assert env.effects().count("fill:x1:f1:p-return") <= 1
    assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS}


def test_the_p_is_recorded_where_the_journal_put_it_even_if_the_fill_was_held_in_between(env_factory):
    env = env_factory()
    env.open()

    def hold_after_posting(point):
        if point == "p-posted":
            with sqlite3.connect(env.tmp / "s.sqlite") as db:
                db.execute("UPDATE fills SET state='held'")
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        db.execute("UPDATE fills SET state='cash-confirmed'")
    env.settlement._point = hold_after_posting
    env.settlement.advance("f1")
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        assert db.execute("SELECT p_at FROM fills").fetchone()[0] == "buyer"
    env.settlement._point = lambda name: None
    assert env.settlement.refund("f1", press=PRESS) is st.State.REFUNDED
    assert env.balances() == {"treasury": SUPPLY - UNITS, SALE: UNITS} and env.effects().count("fill:x1:f1:p-return") == 1


def test_a_reversal_while_the_capture_is_still_being_confirmed_holds_the_fill(env_factory):
    env = env_factory()
    env.open()
    env.settlement.advance("f1")
    env.settlement.dispatch("f1")
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        db.execute("UPDATE legs SET status='sent' WHERE leg='capture'")
    env.provider.chargeback("x1/f1/capture/once")
    reversal = [r for r in env.provider.deliver() if b'"status":"reversed"' in r]
    assert len(reversal) == 1 and env.settlement.state("f1") is st.State.CAPTURE_REQUESTED
    env.settlement.pump(reversal)
    assert env.settlement.state("f1") is st.State.HELD


def test_two_openers_of_one_fill_get_opened_and_already_never_an_error(env_factory):
    env = env_factory()
    other = st.Settlement(env.tmp / "s.sqlite", env.authority, env.provider, synchronous="OFF")
    results = []

    def second_opener_wins_the_race(point):
        if point == "dispatched:reserve" and not results:
            results.append(other.open_fill(env.fill(), ACCOUNTS, PARTIES))
    env.settlement._point = second_opener_wins_the_race
    assert env.open() is st.OpenResult.ALREADY and results == [st.OpenResult.OPENED]
    assert env.ops("reserve") == ["x1/f1/reserve/once"], "one reservation, because both used the same key"
    assert env.settlement.state("f1") is st.State.MATCHED
