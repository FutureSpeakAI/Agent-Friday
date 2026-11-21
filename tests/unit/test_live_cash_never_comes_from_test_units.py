"""NS-40.4-18: test balances, however large, and a charter that says it has a cash claim cannot create live-cash rights.
TEST CREDITS ONLY.

Live cash needs a named provider, accountable signatories and its own gate, none of which exist in this release. What can
be tested today is the other half of the row: nothing a test unit, a balance, a charter flag or a settings key can say turns
live cash on or lets a non-test provider in. What each check really bites on:

  1. a journal at the largest supply under a charter whose cash_claim is True: the capability question takes only the settings
     dict, so the balance is not an input to it; this is a documented-by-test fact, not a check that can fail on balances;
  2. every combination of federation settings (including fed_live_cash and a bare live_cash key) leaves the capability off:
     the real check, but with no negative control of its own (a mutant that removes the never-list is caught by 3 and 2 together);
  3. a non-test provider is refused after that balance exists: shown able to fail (the never-list emptied lets it in);
  4. a charter with cash_claim=True digests differently from False, so a test issuer cannot be swapped for a "cash" one.

Also frozen here: a bare `live_cash` key reads as a plain flag in held_features.enabled, and no capability follows from it.

The limit, stated plainly: there is no NFT or similar token type in the tree to test; the row's NFT half has nothing to
run against until one exists. And the positive half of the row (a named provider, signatories and the gate) is not buildable now.
"""
import itertools

import pytest

from agent_friday.services import held_features
from agent_friday.services import issuer_authority as auth
from agent_friday.services import issuer_registry as reg
from agent_friday.services.exchange import provider

KEY = auth.Ed25519Key(bytes(range(32)))


def charter(cash_claim, supply=reg.MAX_UNITS):
    return reg.IssuerCharter(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=supply,
                             treasury_account="treasury", cash_claim=cash_claim, genesis_authority=KEY.public_key,
                             created="2026-11-16T00:00:00Z")


def desc(test_only=True, pid="synthetic"):
    return provider.ProviderDescriptor(pid, test_only, ("USD",), 1, "provider", True)


def every_setting():
    keys = ["federation", "fed_offline_rehearsal", "fed_invited_jobs", "fed_unattended_worker",
            "fed_private_publish", "fed_public_discovery", "fed_live_cash", "live_cash", "public_discovery"]
    for values in itertools.product((True, False), repeat=len(keys)):
        yield {"held_features": dict(zip(keys, values))}


def test_the_largest_possible_balance_under_a_cash_claiming_charter_turns_nothing_on(tmp_path):
    with auth.Authority(tmp_path / "j.sqlite", charter(cash_claim=True), KEY) as a:
        assert a.balances()["treasury"] == reg.MAX_UNITS
        a.execute({"issuer": "test.one", "effect": "e1", "action": "act-1", "postings": {"treasury": -5, "alice": 5}})
        for settings in every_setting():
            assert held_features.capability_enabled(held_features.LIVE_CASH, settings) is False
            assert held_features.enabled(held_features.LIVE_CASH, settings) is (
                settings["held_features"]["live_cash"] is True), "the bare key is read as a plain flag, nothing else"
        # the flag is the only place the word appears: no capability, no provider, no route follows from it


def test_no_combination_of_federation_settings_turns_live_cash_or_public_discovery_on():
    seen = 0
    for settings in every_setting():
        seen += 1
        assert held_features.capability_enabled(held_features.LIVE_CASH, settings) is False
        assert held_features.capability_enabled(held_features.PUBLIC_DISCOVERY, settings) is False
    assert seen == 2 ** 9


def test_a_non_test_provider_is_refused_after_the_balance_exists(tmp_path):
    registry = {}
    with auth.Authority(tmp_path / "j.sqlite", charter(cash_claim=True), KEY):
        with pytest.raises(provider.LiveProviderRefused):
            provider.register_provider(desc(test_only=False, pid="real"), registry)
    assert registry == {}
    provider.register_provider(desc(), registry)
    assert list(registry) == ["synthetic"]


def test_the_check_can_fail_with_the_never_list_emptied_a_live_provider_would_get_in(monkeypatch):
    # negative control: if LIVE_CASH were ever taken off the never-list, the refusal above would stop; so the refusal
    # is the never-list and not something else that happens to be true today
    monkeypatch.setattr(held_features, "NEVER_IN_THIS_RELEASE", frozenset())
    registry = {}
    provider.register_provider(desc(test_only=False, pid="real"), registry)
    assert "real" in registry
    monkeypatch.undo()
    assert held_features.LIVE_CASH in held_features.NEVER_IN_THIS_RELEASE


def test_a_charter_that_claims_cash_is_a_different_issuer_than_the_same_id_that_does_not():
    assert reg.charter_digest(charter(cash_claim=True)) != reg.charter_digest(charter(cash_claim=False))
