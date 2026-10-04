"""A Laya 2 tier-1 verdict can only escalate a governance verdict.

The approval gate's severity lattice runs internal < outward < hard. Tier 1
may add an opinion to it (a confident state-changing reading is worth
"outward") and may never argue it down: for every verdict the heads can
produce, `combine_gate(keyword, tier1) >= keyword`, and when tier 1 has
nothing to say the keyword verdict comes back untouched. `union_backend`
keeps the same OR for Laya 1.

Mutation-checked: a `combine_gate` that returns tier 1's own label fails
`test_every_tier1_verdict_only_escalates` on keyword "hard" with a
state-changing verdict (outward < hard).
"""
from __future__ import annotations

import inspect
import itertools

from agent_friday.services import laya2_seeds, laya_backend, reflex_turn

ORDER = reflex_turn.GATE_ORDER


def _rank(label):
    return ORDER.index(label)


def _every_tier1_verdict():
    """Every combination the heads can produce, plus the degraded one."""
    for shape, mutation, oos, degraded in itertools.product(
            laya2_seeds.SHAPES, laya2_seeds.MUTATIONS, (False, True), (False, True)):
        for conf in (0.0, 0.5, 0.99):
            yield {"shape": shape, "mutation": mutation, "oos": oos,
                   "degraded": degraded, "shape_conf": conf, "margin": conf / 2,
                   "route": "reflex" if conf > 0.9 and not oos else "brain"}
    yield {}
    yield None


def test_the_lattice_is_the_gate_s_own():
    assert ORDER == ("internal", "outward", "hard")


def test_every_tier1_verdict_only_escalates():
    checked = 0
    for keyword in ORDER:
        for tier1 in _every_tier1_verdict():
            out = reflex_turn.combine_gate(keyword, tier1)
            assert out in ORDER, (keyword, tier1, out)
            assert _rank(out) >= _rank(keyword), (
                "tier 1 weakened the gate: %s -> %s on %r" % (keyword, out, tier1))
            checked += 1
    assert checked > 200


def test_a_verdict_with_nothing_to_say_leaves_the_keyword_alone():
    for keyword in ORDER:
        for tier1 in _every_tier1_verdict():
            if reflex_turn.tier1_gate_opinion(tier1) is None:
                assert reflex_turn.combine_gate(keyword, tier1) == keyword, (keyword, tier1)


def test_a_degraded_verdict_says_nothing_whatever_it_claims():
    loud = {"shape": "reflex_open", "mutation": "state_changing", "oos": False,
            "degraded": True, "shape_conf": 0.99, "margin": 0.5}
    assert reflex_turn.tier1_gate_opinion(loud) is None
    for keyword in ORDER:
        assert reflex_turn.combine_gate(keyword, loud) == keyword


def test_a_confident_state_change_lifts_internal_to_outward_and_no_further():
    v = {"shape": "reflex_organise", "mutation": "state_changing", "oos": False,
         "degraded": False, "shape_conf": 0.9, "margin": 0.3}
    assert reflex_turn.combine_gate("internal", v) == "outward"
    assert reflex_turn.combine_gate("outward", v) == "outward"
    assert reflex_turn.combine_gate("hard", v) == "hard"


def test_a_label_the_lattice_does_not_know_passes_through_unchanged():
    v = {"mutation": "state_changing", "degraded": False}
    assert reflex_turn.combine_gate("destructive", v) == "destructive"
    assert reflex_turn.combine_gate("", v) == ""


def test_union_backend_is_still_an_or():
    src = inspect.getsource(laya_backend.union_backend)
    assert '"hard" if "hard" in (kw_answer, severity) else "soft"' in src
    assert "escalating only" in src
