"""The Doctor's integrity lines include the update signature (BD-16 acceptance).

Read from services/update_check's stored state only: the Doctor never reaches the network. On a
tree without the check, each test fails at the fixture with the reason.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def check(monkeypatch):
    from agent_friday.services import doctor
    fn = getattr(doctor, "_c_update_signature", None)
    if fn is None:
        pytest.fail("the Doctor shows no update-signature check")
    from agent_friday.services import trust_keys, update_check
    state = {}
    monkeypatch.setattr(update_check, "read_state", lambda: dict(state))
    monkeypatch.setattr(trust_keys, "RELEASE_KEYS", ("RWQ-test-key",))

    def no_network(*a, **k):
        raise AssertionError("the Doctor reached the network")
    monkeypatch.setattr(update_check, "check_for_update", no_network)
    return fn, state, trust_keys, monkeypatch


def test_it_is_one_of_the_doctors_checks(check):
    from agent_friday.services import doctor
    assert "update_signature" in [c[0] for c in doctor.CHECKS]


def test_without_a_release_key_it_is_off_by_configuration_not_a_failure(check):
    fn, state, trust_keys, mp = check
    mp.setattr(trust_keys, "RELEASE_KEYS", ())
    assert fn() == ("unavailable_by_configuration", "no_release_key")


def test_a_verified_release_is_healthy(check):
    fn, state, *_ = check
    state.update(verification="verified", last_success_ts=1.0)
    assert fn() == ("healthy", "last_release_verified")


@pytest.mark.parametrize("bad", ["bad_signature", "expired", "downgrade", "mismatch", "unsigned", "unreachable"])
def test_a_refused_release_is_degraded_and_named(check, bad):
    fn, state, *_ = check
    state.update(verification=bad, last_success_ts=1.0)
    assert fn() == ("degraded", "last_release_refused:" + bad)


def test_never_checked_is_unverified(check):
    fn, state, *_ = check
    assert fn() == ("unverified", "not_checked_yet")


def test_up_to_date_is_not_a_failure(check):
    fn, state, *_ = check
    state.update(verification=None, last_success_ts=1.0, update_available=False)
    assert fn() == ("healthy", "no_newer_release")


def test_a_check_from_before_the_key_is_unverified(check):
    fn, state, *_ = check
    state.update(verification="no_trusted_key", last_success_ts=1.0, update_available=True)
    assert fn() == ("unverified", "last_release_checked_before_the_key")


def test_an_unexpected_stored_value_is_never_echoed_into_the_report(check):
    fn, state, *_ = check
    state.update(verification="C:\\Users\\someone\\secret", last_success_ts=1.0, update_available=True)
    assert fn() == ("unverified", "last_release_unknown")
