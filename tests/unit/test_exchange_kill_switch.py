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
    other.settlement.advance("f1")
    other.settlement.dispatch("f1")                              # the capture is asked for; no payout is ever sent (S-18: none stands)
    with sqlite3.connect(other.tmp / "s.sqlite") as db:
        db.execute("UPDATE legs SET status='sent' WHERE leg='capture'")
    other.provider.chargeback("x1/f1/capture/once")
    other.settlement.pump([r for r in other.provider.deliver() if b'"status":"reversed"' in r])
    gate.allowed = False
    assert other.settlement.refund("f1", press=base.PRESS) is State.REFUNDED


def test_a_refund_while_halted_with_the_sellers_payout_standing_holds_the_fill_and_recovers_the_p(env_factory):
    """Design 11 (S-18) under the kill switch: no cash moves, the P goes to the recovered account, the fill stays held."""
    gate = Gate(True)
    env = env_factory(gate)
    env.open()
    env.drive()                                                  # payout confirmed: it stands
    env.provider.chargeback("x1/f1/capture/once")
    env.settlement.pump(env.provider.deliver())
    gate.allowed = False
    assert env.settlement.refund("f1", press=base.PRESS) is State.HELD
    assert env.balances()["recovered:x1:f1"] == base.UNITS and env.ops("refund") == []


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
    assert env_factory(lambda: "yes").open() is OpenResult.HALTED, "only exactly True opens a fill"


def test_the_default_gate_changes_nothing_for_callers_that_pass_none(env_factory):
    env = env_factory()
    assert env.open() is OpenResult.OPENED and env.drive() is State.PAYOUT_CONFIRMED


def test_the_kill_switch_gate_follows_the_owners_setting_and_fails_closed(monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import held_features
    gate = held_features.new_effects_gate()
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


def test_a_reserve_leg_left_pending_by_a_crash_is_not_sent_by_a_tick_while_halted(env_factory):
    # opening is a new effect: a RESERVE enqueued before the switch went on must not reach the provider afterwards
    gate = Gate(True)
    env = env_factory(gate)

    def crash_before_the_reserve_is_sent(point):
        if point == "before-dispatch:reserve":
            raise base.Crash(point)
    env.settlement._point = crash_before_the_reserve_is_sent
    with pytest.raises(base.Crash):
        env.open()
    env.settlement._point = lambda name: None
    gate.allowed = False
    for _ in range(3):
        env.settlement.tick("f1")
    assert env.ops("reserve") == [], "no provider call for a new reservation while halted"
    assert env.settlement.state("f1") in st.OPENING
    gate.allowed = True
    env.settlement.tick("f1")
    assert env.ops("reserve") != [], "and it goes out once the switch is off again"


def test_settings_that_the_real_loader_could_not_read_count_as_halted(monkeypatch, tmp_path):
    # core._load_settings does not raise on an unreadable file: it returns the last good settings or the defaults,
    # marked degraded, and the defaults carry no fed_kill_switch key
    import agent_friday.core as core
    from agent_friday.services import held_features
    broken = tmp_path / "settings.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(core, "SETTINGS_FILE", broken)
    monkeypatch.setattr(core, "_SETTINGS_READ_RETRY_S", 0)
    monkeypatch.setitem(core._SETTINGS_LAST_GOOD, "value", None)
    assert core._is_degraded_settings(core._load_settings()), "the real loader marks an unreadable file degraded"
    assert held_features.new_effects_gate()() is False


def test_settlement_is_handed_its_gate_and_does_not_reach_for_the_app_core_or_settings():
    # core pulls flask, subprocess and asyncio into the exchange (scripts/check_exchange_imports.py bans those; it
    # sees only direct imports, so this holds the indirect one). provider.py names held_features for its never-list
    # (older, #905); settlement does not.
    from pathlib import Path
    root = Path(st.__file__).parent
    core = [p.name for p in root.glob("*.py") if "agent_friday.core" in p.read_text(encoding="utf-8-sig")]
    assert core == [], core
    assert "held_features" not in Path(st.__file__).read_text(encoding="utf-8-sig")
