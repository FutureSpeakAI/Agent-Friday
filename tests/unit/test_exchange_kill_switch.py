"""The owner's kill switch stops NEW fills and lets committed settlement finish, with the history kept
(NS-40.1-18). Synthetic cash and test credits only."""
import sqlite3

import pytest

from agent_friday.services.exchange import settlement as st
from tests.unit import test_exchange_settlement as base

State, OpenResult = st.State, st.OpenResult


@pytest.fixture
def env_factory(tmp_path):
    counter = iter(range(1000))

    def make(gate=None):
        folder = tmp_path / f"e{next(counter)}"
        folder.mkdir()
        env = base.Env(folder)
        if gate is not None:
            env.settlement = st.Settlement(folder / "s.sqlite", env.authority, env.provider, synchronous="OFF",
                                           new_effects_allowed=gate)
        return env
    return make


class Gate:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def __call__(self):
        return self.allowed


def history(env):
    with sqlite3.connect(env.tmp / "s.sqlite") as db:
        return db.execute("SELECT fill_id, from_state, to_state FROM history ORDER BY n").fetchall()


def test_a_halted_node_opens_no_new_fill_and_touches_nothing(env_factory):
    env = env_factory(Gate(False))
    assert env.open() is OpenResult.HALTED
    assert env.settlement.state("f1") is None
    assert env.provider.books()["ops"] == {} and env.provider.books()["power"]["bp-1"]["reserved"] == 0
    assert env.balances() == {"treasury": base.SUPPLY - base.UNITS, base.SALE: base.UNITS}
    assert env.open_retry() is OpenResult.HALTED, "a retry is halted too"


def test_a_fill_committed_before_the_switch_finishes_while_it_is_on(env_factory):
    gate = Gate(True)
    env = env_factory(gate)
    assert env.open() is OpenResult.OPENED
    env.settlement.advance("f1")
    gate.allowed = False
    assert env.drive() is State.PAYOUT_CONFIRMED, "legally committed settlement completes"
    base.settled_ok(env)
    assert base.violations(env) == []


def test_a_committed_fill_can_still_be_cancelled_and_a_held_one_refunded_while_halted(env_factory):
    gate = Gate(True)
    env = env_factory(gate)
    env.open()
    gate.allowed = False
    assert env.settlement.cancel("f1") is st.CancelResult.CANCELLED
    assert env.settlement.state("f1") is State.RELEASED and env.balances()[base.SALE] == base.UNITS
    other = env_factory(gate)
    gate.allowed = True
    other.open()
    other.drive()
    other.provider.chargeback("x1/f1/capture/once")
    other.settlement.pump(other.provider.deliver())
    gate.allowed = False
    assert other.settlement.refund("f1", press=base.PRESS) is State.REFUNDED


def test_the_history_is_kept_and_only_grows_while_halted(env_factory):
    gate = Gate(True)
    env = env_factory(gate)
    env.open()
    before = history(env)
    gate.allowed = False
    env.drive()
    after = history(env)
    assert after[:len(before)] == before and len(after) > len(before)


def test_a_fill_that_was_still_opening_waits_while_halted_and_no_cash_is_reserved(env_factory):
    gate = Gate(True)
    env = env_factory(gate)

    def crash_after_the_p_is_held(point):
        if point == "p-held":
            raise base.Crash(point)
    env.settlement._point = crash_after_the_p_is_held
    with pytest.raises(base.Crash):
        env.open()
    env.settlement._point = lambda name: None
    gate.allowed = False
    assert env.open() is OpenResult.HALTED and env.settlement.tick("f1") is State.P_HELD
    assert env.ops("reserve") == [], "opening is a new effect: the cash is not reserved while halted"
    gate.allowed = True
    assert env.open() is OpenResult.OPENED and env.settlement.state("f1") is State.MATCHED


def test_turning_the_switch_off_again_lets_new_fills_open(env_factory):
    gate = Gate(False)
    env = env_factory(gate)
    assert env.open() is OpenResult.HALTED
    gate.allowed = True
    assert env.open() is OpenResult.OPENED and env.settlement.state("f1") is State.MATCHED


def test_a_gate_that_cannot_answer_counts_as_halted(env_factory):
    def broken():
        raise RuntimeError("settings unreadable")
    env = env_factory(broken)
    assert env.open() is OpenResult.HALTED
    assert env_factory(lambda: None).open() is OpenResult.HALTED, "no answer is not a yes"


def test_the_default_gate_changes_nothing_for_callers_that_pass_none(env_factory):
    env = env_factory()
    assert env.open() is OpenResult.OPENED and env.drive() is State.PAYOUT_CONFIRMED


def test_the_kill_switch_gate_follows_the_owners_setting_and_fails_closed(monkeypatch):
    import agent_friday.core as core
    gate = st.kill_switch_gate()
    from agent_friday.services import held_features
    key = held_features.KILL_SWITCH
    monkeypatch.setattr(core, "_load_settings", lambda: {"held_features": {key: True}})
    assert gate() is False
    monkeypatch.setattr(core, "_load_settings", lambda: {"held_features": {key: False}})
    assert gate() is True
    monkeypatch.setattr(core, "_load_settings", lambda: {"held_features": {}})
    assert gate() is True
    monkeypatch.setattr(core, "_load_settings", lambda: {"held_features": {key: "yes"}})
    assert gate() is True, "only exactly True engages it, as everywhere else"

    def unreadable():
        raise OSError("no settings")
    monkeypatch.setattr(core, "_load_settings", unreadable)
    assert gate() is False, "settings that cannot be read cannot say the switch is off"
