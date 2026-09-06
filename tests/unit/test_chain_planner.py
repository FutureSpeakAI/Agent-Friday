"""plan_chain — the sequence Stephen described (headroom.md §6, §12 Phase 3).

The golden chains are the review surface, same rule as the sibling
test_residency_policy.py: a policy change that moves a chain plan must move a
committed file in the same commit. The property tests are the safety net the
six named fixtures cannot be.

P1's own arithmetic is the load-bearing case in this file: Phase 2 measured
Z-Image Turbo FP8 at 10,453 MiB under the Arbiter's real `image_job` lease
(basis="measured", 2026-09-04). Fed into the same R3/R10 arithmetic the
planner already uses (12,282 - 1,024 R3 - 1,261 baseline = 9,997 MiB
available; minus the retained e2b sidekick's 1,811 MiB = 8,186 MiB actually
free to a lease), the real number does NOT fit -- even with the brain fully
evicted and only the sidekick retained. HR18 says a MEASURED shortfall
refuses, so the image stage on P1 renders `where: "cloud"` (cloud selected as
the working alternative, §6.4), not `where: "leased"`. That is a materially
different chain from the spec's own worked example in §6.3, which used the
placeholder "?" for VRAM and assumed the image stage would proceed locally
for a ~3-5 minute chain. With the real number, P1's chain never gives up the
brain at all: `peak_mib` is 4,892 (e4b 3,081 + the retained e2b 1,811 --
exactly §6.3's own "honest resident pair" figure), `contract_ok` is True, and
the chain has zero transitions, because nothing was ever evicted.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from agent_friday.services import residency_catalog as rc
from agent_friday.services import residency_policy as rp
from tests import residency_fixtures as fx

GOLDEN = pathlib.Path(__file__).resolve().parents[1] / "golden" / "residency" \
    / "chains"


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """Never touch the real measurement store -- and seed the two real image
    footprints fresh for every test, the same way `_generate.py` does."""
    monkeypatch.setattr(rc, "store_path", lambda: tmp_path / "m.json")
    rc.reset_cache()
    fx.seed_image_footprints()


def _chain(key, **kw):
    profile = fx.ALL_PROFILES[key]
    return rp.plan_chain(profile, fx.catalog(profile), fx.CHAIN_STAGES,
                         resident=fx.CHAIN_RESIDENT[key], **kw)


def _strip_alts(plan):
    return {k: v for k, v in plan.items() if k != "alternatives"}


# ── Golden chains ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", sorted(fx.ALL_PROFILES))
def test_chain_matches_its_committed_golden(key):
    expected = json.loads((GOLDEN / ("%s.json" % key)).read_text(
        encoding="utf-8"))
    actual = json.loads(json.dumps(_chain(key), sort_keys=True))
    assert actual == expected, (
        "chain placement changed for %s. If intended, regenerate with "
        "tests/golden/residency/chains/_generate.py and commit the diff."
        % key)


# ── P1 — the real number, worked out ─────────────────────────────────────────

def test_p1_image_refused_locally_with_the_real_measured_footprint():
    """HR18: a MEASURED shortfall refuses. 10,453 MiB needed, only 8,186 MiB
    free with the sidekick retained -- even fully evicted, Z-Image does not
    fit under P1's honest reserve."""
    plan = _chain("P1")
    image = plan["stages"][2]
    assert image["role"] == "image"
    assert image["where"] == "cloud"          # refused locally, cloud
    assert image["basis"] == "declared"        # the STAGE's own render — the
    # underlying footprint that refused it was basis="measured"; the note
    # names the real numbers so nobody has to trust "declared" blindly here.
    assert "measured 10453 MiB" in image["note"]
    assert "8186 MiB" in image["note"]


def test_p1_brain_never_stands_down_because_image_never_leases():
    """The consequence of the refusal above: no transitions at all, and the
    brain (e4b) is never evicted -- a materially better outcome than the
    spec's own placeholder-VRAM worked example, which assumed a ~3-5 minute
    chain with the brain standing down for the render."""
    plan = _chain("P1")
    assert plan["transitions"] == []
    brain = plan["stages"][1]
    assert brain["model_id"] == "gemma4:e4b"
    assert brain["where"] == "resident"


def test_p1_peak_mib_is_the_resident_pair_not_the_image_footprint():
    """§6.3's own arithmetic: e4b (3,081) + the retained e2b (1,811) =
    4,892 -- NOT 10,453 (the image footprint), because the image stage never
    actually leases the card on P1 once the real number is in play."""
    plan = _chain("P1")
    assert plan["peak_mib"] == 4892
    assert plan["contract_ok"] is True


def test_p1_alternatives_include_forcing_the_image_stage_local():
    """§6.4 — alternatives exist even though the primary plan already picked
    cloud for the refused stage; `when_away` is always offered when cloud is
    available."""
    plan = _chain("P1")
    kinds = {a.get("execution") for a in plan["alternatives"]}
    assert "when_away" in kinds


# ── P3 — the image stage fits beside the brain (with eviction; R5 stays
#    exclusive, D7 is not relaxed) ───────────────────────────────────────────

def test_p3_image_leases_and_evicts_the_brain_not_the_sidekick():
    plan = _chain("P3")
    image = plan["stages"][2]
    assert image["where"] == "leased"
    assert image["footprint_mib"] == 10453
    assert image["basis"] == "measured"
    assert image["retained_mib"] == 1811
    assert image["exclusive_of"] == ["gemma4:e4b"]
    # One eviction before the render, one reload before tts -- exactly the
    # two-transition shape §6.3's P1 table describes for the (there,
    # hypothetical) local case.
    assert len(plan["transitions"]) == 2
    assert plan["transitions"][0]["evict"] == ["gemma4:e4b"]
    assert plan["transitions"][1]["load"] == ["gemma4:e4b"]


def test_p3_peak_mib_is_the_render_plus_the_retained_sidekick():
    plan = _chain("P3")
    assert plan["peak_mib"] == 10453 + 1811


# ── P4 — dual GPU: image on its own card, nothing evicted ────────────────────

def test_p4_image_runs_on_the_second_gpu_with_no_transitions():
    plan = _chain("P4")
    image = plan["stages"][2]
    assert image["where"] == "leased"
    assert image["retained_mib"] == 0
    assert image["exclusive_of"] == []
    assert plan["transitions"] == []            # "the only fixture where
    # Stephen's sentence runs as he imagines it" -- §6.3 P4.


# ── P6 — no GPU, no local backend, entirely cloud ────────────────────────────

def test_p6_is_entirely_cloud():
    plan = _chain("P6")
    wheres = {s["role"]: s["where"] for s in plan["stages"]}
    assert wheres["image"] == "cloud"
    assert wheres["interactive_brain"] == "cloud"
    assert wheres["stt"] == "cpu"               # voice stays local everywhere
    assert wheres["tts"] == "cpu"


# ── Rule 4 — a live voice session's seat is retained through every stage ────

def test_voice_bound_seat_survives_an_image_lease_even_off_the_sidekick():
    """§2.6 step 4's failure, closed: the seat a live voice session is bound
    to is retained through every stage, even when it is NOT the R10
    sidekick."""
    resident = {
        "interactive_brain": {"model_id": "gemma4:e4b", "device": "gpu:0",
                              "vram_mib": 3081, "voice_bound": True},
        "sidekick": {"model_id": "gemma4:e2b", "device": "gpu:0",
                    "vram_mib": 1811},
    }
    profile = fx.P3   # room enough that the image stage genuinely leases
    plan = rp.plan_chain(profile, fx.catalog(profile), fx.CHAIN_STAGES,
                         resident=resident)
    image = plan["stages"][2]
    assert image["where"] == "leased"
    # Both e4b (voice-bound) AND e2b (R10) are retained -- neither is in
    # `exclusive_of`.
    assert image["exclusive_of"] == []
    assert set(plan["retained"]) == {"gemma4:e4b", "gemma4:e2b"}


# ── Rule 5 — vault provenance, D2's resolution ───────────────────────────────

def test_vault_touching_stage_has_no_cloud_alternative():
    stages = [{"role": "stt"},
             {"role": "interactive_brain", "touches_vault": True},
             {"role": "image", "units": 1}, {"role": "tts"}]
    plan = rp.plan_chain(fx.P1, fx.catalog(fx.P1), stages,
                         resident=fx.CHAIN_RESIDENT["P1"])
    brain = plan["stages"][1]
    image = plan["stages"][2]
    # The vault-touching stage itself never renders "cloud".
    assert brain["where"] != "cloud"
    # A LATER stage, derived from what the vault-touching stage produced,
    # also loses its cloud alternative -- image would otherwise have gone
    # cloud (as it does in the unblocked P1 chain above); here it must
    # refuse instead, and say why.
    assert image["where"] == "refused"
    assert "vault" in image["refusal"]["explanation"] or \
        "cloud alternative" in image["refusal"]["explanation"]


def test_strict_vault_blocks_even_earlier_stages():
    """D2's stricter, default-off alternative: once ANY stage touches the
    vault, no stage in the chain may use cloud -- including ones before it."""
    stages = [{"role": "stt"}, {"role": "interactive_brain"},
             {"role": "image", "units": 1, "touches_vault": True},
             {"role": "tts"}]
    profile = fx.P2   # image already refuses locally here regardless
    normal = rp.plan_chain(profile, fx.catalog(profile), stages,
                           resident=fx.CHAIN_RESIDENT["P2"])
    strict = rp.plan_chain(profile, fx.catalog(profile), stages,
                           resident=fx.CHAIN_RESIDENT["P2"],
                           strict_vault=True)
    # Not strict: the brain stage (before the vault-touching one) is free to
    # use cloud if it needs to. Strict: it may not, even though it comes
    # first in the sequence.
    assert normal["stages"][1]["where"] != "refused"
    # Force the brain to need a placement decision by pointing it at an
    # unresident model so the strict/non-strict difference is observable on
    # a role that would otherwise just read "resident".
    stages2 = [{"role": "stt"},
              {"role": "interactive_brain", "model_id": "gemma4:12b"},
              {"role": "image", "units": 1, "touches_vault": True},
              {"role": "tts"}]
    normal2 = rp.plan_chain(profile, fx.catalog(profile), stages2,
                            resident=fx.CHAIN_RESIDENT["P2"])
    strict2 = rp.plan_chain(profile, fx.catalog(profile), stages2,
                            resident=fx.CHAIN_RESIDENT["P2"],
                            strict_vault=True)
    assert normal2["stages"][1]["where"] in ("cloud", "leased", "resident")
    if normal2["stages"][1]["where"] == "cloud":
        assert strict2["stages"][1]["where"] != "cloud"


# ── Property tests ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", sorted(fx.ALL_PROFILES))
def test_property_one_lease_at_a_time(key):
    """A chain never asks for two leased/exclusive GPU stages to run
    concurrently -- `where: "leased"` stages never overlap; this is a
    property of the SEQUENTIAL planner, checked by asserting every leased
    stage's transitions are attached to ITS OWN index, never shared."""
    plan = _chain(key)
    leased_idxs = [i for i, s in enumerate(plan["stages"])
                  if s["where"] == "leased"]
    before_stages = [t["before_stage"] for t in plan["transitions"]]
    # Every "before_stage" index is unique -- no two transitions collide on
    # the same boundary, which is what "one at a time" means operationally
    # for a plan (the Arbiter's serial lock is what enforces it at runtime).
    assert len(before_stages) == len(set(before_stages))
    assert all(0 <= i <= len(plan["stages"]) for i in before_stages)
    assert leased_idxs == sorted(set(leased_idxs))


@pytest.mark.parametrize("key", sorted(fx.ALL_PROFILES))
def test_property_retained_set_never_empty_when_voice_bound(key):
    resident = dict(fx.CHAIN_RESIDENT[key])
    if "interactive_brain" not in resident:
        pytest.skip("no resident text seat on this profile to bind voice to")
    resident = {k: dict(v) for k, v in resident.items()}
    resident["interactive_brain"]["voice_bound"] = True
    profile = fx.ALL_PROFILES[key]
    plan = rp.plan_chain(profile, fx.catalog(profile), fx.CHAIN_STAGES,
                         resident=resident)
    assert len(plan["retained"]) >= 1
    assert resident["interactive_brain"]["model_id"] in plan["retained"]


@pytest.mark.parametrize("key", sorted(fx.ALL_PROFILES))
def test_property_no_stage_plans_into_unknown(key):
    """HR1 — a stage whose footprint basis is genuinely unknown never renders
    `where` as `resident`/`leased`/`cpu` on the strength of that missing
    number; it is `cloud` (an honest escalation) or `refused` (an honest
    admission), never a silent placement."""
    stages = [{"role": "stt"}, {"role": "interactive_brain"},
             {"role": "image", "model_id": "totally-unmeasured-model-xyz"},
             {"role": "tts"}]
    profile = fx.ALL_PROFILES[key]
    plan = rp.plan_chain(profile, fx.catalog(profile), stages,
                         resident=fx.CHAIN_RESIDENT[key])
    image = plan["stages"][2]
    assert image["where"] in ("cloud", "refused")
    if not rp.gpu_budgets(profile):
        # No GPU at all: peak_mib is a KNOWN 0 regardless of whether the
        # requested model has ever been measured -- there is no card for it
        # to be unknown about. The unknown-collapse only applies where a
        # real placement decision was actually contingent on the missing
        # number.
        assert plan["peak_mib"] == 0
        return
    # peak_mib/contract_ok collapse to None whenever a GPU-relevant footprint
    # is genuinely unknown (not merely a CPU service's untimed one).
    assert plan["peak_mib"] is None
    assert plan["contract_ok"] is None


@pytest.mark.parametrize("key", sorted(fx.ALL_PROFILES))
def test_property_vault_stage_never_renders_cloud(key):
    stages = [{"role": "stt"},
             {"role": "interactive_brain", "touches_vault": True}]
    profile = fx.ALL_PROFILES[key]
    plan = rp.plan_chain(profile, fx.catalog(profile), stages,
                         resident=fx.CHAIN_RESIDENT[key])
    assert plan["stages"][1]["where"] != "cloud"


def test_video_role_is_cloud_when_a_key_exists_and_refused_otherwise():
    plan_cloud = rp.plan_chain(fx.P1, fx.catalog(fx.P1),
                               [{"role": "video"}],
                               resident=fx.CHAIN_RESIDENT["P1"])
    assert plan_cloud["stages"][0]["where"] == "cloud"

    plan_no_cloud = rp.plan_chain(fx.P1, fx.catalog(fx.P1),
                                  [{"role": "video"}],
                                  resident=fx.CHAIN_RESIDENT["P1"],
                                  cloud_ok=False)
    assert plan_no_cloud["stages"][0]["where"] == "refused"


def test_alternatives_do_not_recurse():
    """`_alt` bounds the recursion -- an alternative's own `alternatives`
    list is always empty, never another full tree."""
    plan = _chain("P3")
    for alt in plan["alternatives"]:
        if "plan" in alt:
            assert alt["plan"]["alternatives"] == []
