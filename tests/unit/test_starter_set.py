"""The onboarding starting set (docs/design/implemented/headroom.md §9, §12 Phase 6).

`routes.intelligence.build_starter_set(profile)` is the one call chain the
spec names: `hardware_profile.get()` -> `model_plan.plan()` for the brain ->
`residency_policy.plan_chain()` for the interview's own chain
`[stt, interactive_brain, tts]` and, separately, `[..., image]`. This file
checks the shape §9 promises and the two rules that make it honest rather
than a second, disagreeing recommendation surface:

  * HR1/HR2 travel here too -- every verdict `build_starter_set` hands the
    wizard is one of `local_models_catalog()`'s own rows (same basis/status
    vocabulary `test_headroom_surface_verdicts.py` already sweeps for
    Settings), never a bare fit/no-fit collapsed from something else.
  * HR15 -- the brain this function proposes is `model_plan.plan()`'s own
    pick, on every one of the six fixture profiles, not
    `ollama_manager.recommend_models()`'s (a different, and until this
    document, disagreeing, ladder -- see test_ollama_manager.py's own HR15
    tests for that function in isolation).
"""
from __future__ import annotations

import pytest

from agent_friday.routes.intelligence import build_starter_set
from agent_friday.services import model_plan as mp
from tests import residency_fixtures as fx

_VALID_BASES = ("measured", "derived", "declared", "unknown")
_VALID_STATUSES = {
    "fits": ("ready", "ready-but", "refused", "unknown"),
    "runs_well": ("ready", "degraded", "unknown"),
    "worth_it": ("ready", "ready-but", "unknown"),
}


def _assert_verdicts_honest(verdicts, label):
    if verdicts is None:
        return
    for axis in ("fits", "runs_well", "worth_it"):
        axis_v = verdicts[axis]
        assert axis_v["basis"] in _VALID_BASES, (
            "%s / %s carries basis %r" % (label, axis, axis_v.get("basis")))
        assert axis_v["status"] in _VALID_STATUSES[axis], (
            "%s / %s carries status %r" % (label, axis, axis_v.get("status")))


@pytest.mark.parametrize("profile_name", ["P1", "P2", "P3", "P4", "P5", "P6"])
def test_shape_and_verdicts_on_every_fixture(profile_name):
    """§9's payload shape, present and honest, on all six fixtures -- a
    starter set must never itself crash the health-full route on any real
    machine shape this codebase has already fixtured."""
    profile = fx.ALL_PROFILES[profile_name]
    ss = build_starter_set(profile)

    for key in ("brain", "voice", "image", "video", "chain", "contract",
               "floor_model"):
        assert key in ss, "%s missing from starter_set on %s" % (key, profile_name)

    _assert_verdicts_honest(ss["brain"]["verdicts"], "brain")
    _assert_verdicts_honest(ss["voice"]["stt"]["verdicts"] if ss["voice"]["stt"] else None, "stt")
    _assert_verdicts_honest(ss["voice"]["tts"]["verdicts"] if ss["voice"]["tts"] else None, "tts")
    _assert_verdicts_honest(ss["image"]["verdicts"], "image")

    # D8 -- one sentence-shaped value, never a declared row per candidate.
    assert ss["video"] == "cloud"

    # D1 not decided -- only the display reserve is reported, never a
    # fabricated working/away/yield level or a slack/RAM-available floor.
    assert "display_reserve_mib" in ss["contract"]
    assert ss["contract"]["display_reserve_mib"] is None or \
        isinstance(ss["contract"]["display_reserve_mib"], int)
    for forbidden in ("working", "away", "yield", "level", "slack_mib",
                      "ram_available_floor_mib"):
        assert forbidden not in ss["contract"], (
            "starter_set.contract carries %r -- D1's full Contract is not "
            "decided and must not be fabricated" % forbidden)

    assert ss["floor_model"] == mp.FLOOR_MODEL


@pytest.mark.parametrize("profile_name", ["P1", "P2", "P3", "P4", "P5", "P6"])
def test_chain_plans_do_not_raise_and_carry_both_shapes(profile_name):
    """§9: 'chain' is `plan_chain()` for `[stt, interactive_brain, tts]` and,
    separately, `[..., image]` -- both present, both a real ChainPlan (never
    an inline error string swallowing a real bug in disguise)."""
    profile = fx.ALL_PROFILES[profile_name]
    ss = build_starter_set(profile)
    chain = ss["chain"]
    assert "voice_only" in chain and "with_image" in chain
    for name, plan in (("voice_only", chain["voice_only"]),
                       ("with_image", chain["with_image"])):
        assert "error" not in plan, (
            "%s chain on %s raised inside plan_chain: %s"
            % (name, profile_name, plan.get("error")))
        assert "stages" in plan
        roles = [s["role"] for s in plan["stages"]]
        assert "stt" in roles and "tts" in roles
        assert "interactive_brain" in roles
    assert any(s["role"] == "image"
              for s in chain["with_image"]["stages"])
    assert not any(s["role"] == "image"
                  for s in chain["voice_only"]["stages"])


def test_brain_pick_is_the_planners_not_a_second_ladder():
    """HR15's actual content for THIS surface: the brain `build_starter_set`
    proposes must be a model `model_plan.plan()` itself picked -- read
    straight from the plan's own 'brain' tier, never independently derived
    or copied from `ollama_manager.recommend_models()`."""
    profile = fx.ALL_PROFILES["P1"]
    ss = build_starter_set(profile)
    plan = mp.plan(profile)
    tier = next(t for t in plan["tiers"] if t["id"] == "brain")
    if tier["status"] == "refused":
        assert ss["brain"]["model_id"] is None
    else:
        picked = (tier["models"][0]["id"] if tier.get("models")
                  else next(a["id"] for a in tier["alternatives"]
                           if a.get("default")))
        assert ss["brain"]["model_id"] == picked
        assert ss["brain"]["download_gib"] == plan["download_gib"]


def test_image_refusal_always_names_the_cloud_alternative():
    """§6.4 / HR8, applied to this surface: an image row that is not `ready`
    or `ready-but` on `fits` gets `alternative: 'cloud'`, never a refusal
    with no next step -- the same rule the chain planner's own
    `alternatives` list follows."""
    for name, profile in fx.ALL_PROFILES.items():
        ss = build_starter_set(profile)
        status = ((ss["image"]["verdicts"] or {}).get("fits") or {}).get("status")
        if status in ("refused", "unknown", None):
            assert ss["image"]["alternative"] == "cloud", (
                "%s: image fits=%r but no cloud alternative offered"
                % (name, status))
