"""NS-40.1-3: the six logical federation capabilities are visible in the capability state. TEST CREDITS ONLY.

What each check really bites on:
  1. all six appear, each in the closed vocabulary, in the spec's order;
  2. nothing is ever reported as working (no job on this PC has proven any of them), every unconfigured row names the
     setting that would change it, and public discovery and live cash read as absent and held in every combination of settings (2**10 of them, so a new switch cannot open them);
  3. an off federation switch, an off own switch, the kill switch and a fully switched-on capability each read as what
     they are, and the kill switch outranks every other setting;
  4. they are NOT in PROBES (what the model reads every turn) and the model block names none of them;
  (the route half is in tests/api/test_federation_capability_state_route.py)
The check in 2 is shown able to fail: with the never-list emptied, live cash would read as switched on.
"""
import itertools

from agent_friday.services import capability_state as cs
from agent_friday.services import held_features as hf

KEYS = ["federation", "fed_kill_switch", "fed_offline_rehearsal", "fed_invited_jobs", "fed_unattended_worker",
        "fed_private_publish", "fed_public_discovery", "fed_live_cash", "live_cash", "public_discovery"]


def settings(**on):
    return {"held_features": {k: True for k in on}}


def by_key(rows):
    return {r.key: r for r in rows}


def test_all_six_appear_in_the_spec_order_in_the_closed_vocabulary():
    rows = cs.federation_states({})
    assert [r.key for r in rows] == ["federation_" + c for c in hf.CAPABILITIES]
    assert all(r.state in cs.STATES for r in rows)
    assert len(rows) == 6


def test_nothing_is_working_and_the_two_held_ones_never_open_in_any_combination_of_settings():
    for values in itertools.product((True, False), repeat=len(KEYS)):
        rows = by_key(cs.federation_states({"held_features": dict(zip(KEYS, values))}))
        assert all(r.state != cs.WORKING for r in rows.values())
        for held in ("federation_public_discovery", "federation_live_cash"):
            assert rows[held].state == cs.ABSENT and rows[held].health == "blocked_by_policy", held
        for key, row in rows.items():
            if key not in ("federation_public_discovery", "federation_live_cash"):
                assert row.state != cs.ABSENT, key       # a switch could open it, so it is not "nothing to switch on"
            if row.state == cs.UNCONFIGURED:
                assert row.needs and row.fix, f"{key}: unconfigured names the setting that would change it"
                assert "settings.json" in row.fix and row.needs[0] in row.fix, f"{key}: the fix names where the setting lives"
                assert "Settings ->" not in row.fix, f"{key}: no Settings tab has these switches while Federation is off"


def test_each_situation_reads_as_what_it_is():
    off = by_key(cs.federation_states({}))["federation_invited_jobs"]
    assert off.state == cs.UNCONFIGURED and off.needs == ("held_features.federation",)
    own_off = by_key(cs.federation_states(settings(federation=True)))["federation_invited_jobs"]
    assert own_off.state == cs.UNCONFIGURED and own_off.needs == ("held_features.fed_invited_jobs",)
    on = by_key(cs.federation_states(settings(federation=True, fed_invited_jobs=True)))["federation_invited_jobs"]
    assert on.state == cs.PRESENT_UNVERIFIED and "no job on this PC has proven it" in on.detail
    stopped = by_key(cs.federation_states(settings(federation=True, fed_invited_jobs=True, fed_kill_switch=True)))
    assert stopped["federation_invited_jobs"].state == cs.PRESENT_UNVERIFIED, "still installed and switched on"
    assert stopped["federation_invited_jobs"].health == "blocked_by_policy"
    assert "kill switch" in stopped["federation_invited_jobs"].detail
    rows = by_key(cs.federation_states(settings(federation=True, fed_kill_switch=True)))
    assert [k for k, r in rows.items() if r.state == cs.ABSENT] == ["federation_public_discovery", "federation_live_cash"],         "only the two that are not part of this release are absent; a stopped capability is not"


def test_the_check_can_fail_with_the_never_list_emptied_live_cash_would_read_as_switched_on(monkeypatch):
    monkeypatch.setattr(hf, "NEVER_IN_THIS_RELEASE", frozenset())
    row = by_key(cs.federation_states(settings(federation=True, fed_live_cash=True)))["federation_live_cash"]
    assert row.health != "blocked_by_policy" and row.state == cs.PRESENT_UNVERIFIED, "the never-list is what holds it"


def test_they_stay_out_of_what_the_model_reads():
    assert not any(p.__name__.startswith("federation") for p in cs.PROBES)
    block = cs.describe_for_model()
    assert "Federation" not in block and "federation_" not in block

