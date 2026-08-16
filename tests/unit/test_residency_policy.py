"""ResidencyPolicy — golden plans for six fixtures, plus the invariants.

The golden plans are the review surface: a policy change that moves a plan must
move a committed file in the same commit, because a change in placement
behaviour is otherwise invisible in a diff.

The property tests are the safety net the goldens cannot be: they assert the
budgets hold for machines nobody wrote a fixture for.
"""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

from agent_friday.services import residency_policy as rp
from tests import residency_fixtures as fx

GOLDEN = pathlib.Path(__file__).resolve().parents[1] / "golden" / "residency"


def _plan(key):
    profile = fx.ALL_PROFILES[key]
    return rp.plan(profile, fx.catalog(profile))


# ── Golden plans ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", sorted(fx.ALL_PROFILES))
def test_plan_matches_its_committed_golden(key):
    expected = json.loads((GOLDEN / ("%s.json" % key)).read_text())
    actual = json.loads(json.dumps(_plan(key), sort_keys=True))
    assert actual == expected, (
        "placement changed for %s. If intended, regenerate with "
        "tests/golden/residency/_generate.py and commit the diff." % key)


# ── P1 — the plan the mission specifies, checked against the rules ───────────

def test_p1_pins_the_12b_on_the_gpu_with_an_explicit_context():
    s = _plan("P1")["seats"]["interactive_brain"]
    assert s["model_id"] == "gemma4:12b"
    assert s["device"] == "gpu:0"
    assert s["status"] == "pinned"
    # 131072, and the arithmetic behind it is the point. The window has to
    # hold the system prompt (~11681 tokens) AND the tool registry (~8534)
    # before the conversation starts. This seat used to be 32768, sized from
    # the tool registry alone, which left ~12.5k of room — 38% of its own
    # window. See services/context_budget.py.
    assert s["num_ctx"] == 131072       # R7: never a backend default
    assert s["context"]["room_tokens"] > 100_000
    assert s["context"]["basis"] == "measured"


def test_p1_pins_the_e2b_beside_it():
    s = _plan("P1")["seats"]["sidekick"]
    assert (s["model_id"], s["device"], s["status"]) == \
        ("gemma4:e2b", "gpu:0", "pinned")


def test_p1_pinned_pair_fits_the_budget_with_room_to_spare():
    p = _plan("P1")
    avail = p["budgets"]["gpus"][0]["available_mib"]
    # The 12b at 131072 (7814) beside the e2b at 32768 (1811). Quadrupling the
    # brain's window over the old 32768 placement cost 96 MiB.
    assert p["pinned_vram_mib"]["gpu:0"] == 7814 + 1811 == 9625
    assert p["pinned_vram_mib"]["gpu:0"] <= avail == 9997


def test_p1_embedder_is_demoted_to_cpu_and_says_why():
    """Departs from the specified plan, and the departure is forced.

    A GPU-resident embedder needs 2029 MiB against the 233 MiB left after the
    pinned pair. It costs more VRAM than the sidekick for a 639 MB artifact.
    """
    s = _plan("P1")["seats"]["embedder"]
    assert s["device"] == "cpu"
    assert s["demoted_from"] == "gpu"
    assert s["demotion_rule"] == "R3"
    assert "2029" in s["demotion_reason"]


def test_p1_heavy_hitter_is_leased_with_expert_offload():
    s = _plan("P1")["seats"]["heavy_hitter"]
    assert s["model_id"] == "gemma4:26b"
    assert s["status"] == "leased"
    assert s["offload"]["expert_offload"] is True
    assert s["is_moe"] is True


def test_p1_image_takes_an_exclusive_lease_over_everything():
    s = _plan("P1")["seats"]["image"]
    assert s["exclusive"] is True
    # R5 minus R10: exclusive of everything except the seat that keeps
    # Friday answering while the picture renders.
    assert s["displaces"] == "all seats except sidekick"


def test_p1_voice_stays_on_cpu():
    seats = _plan("P1")["seats"]
    assert seats["stt"]["device"] == "cpu"
    assert seats["tts"]["device"] == "cpu"


# ── P2 — the honest local refusal ────────────────────────────────────────────

def test_p2_demotes_the_brain_because_the_12b_does_not_fit():
    """R6: dense models must fit or be demoted."""
    assert _plan("P2")["seats"]["interactive_brain"]["model_id"] == "gemma4:e4b"


def test_p2_refuses_the_heavy_seat_with_arithmetic():
    p = _plan("P2")
    assert p["seats"]["heavy_hitter"] is None
    r = [x for x in p["refusals"] if x["role"] == "heavy_hitter"][0]
    assert r["rule_id"] == "R2"
    assert "12288" in r["explanation"]       # the ceiling it broke
    # 12938, not the 11127 it was before R10: holding the sidekick resident
    # takes 1811 MiB off the lease budget, so more of the model lands in host
    # RAM and the refusal it breaks is by a wider margin.
    assert "12938" in r["explanation"]       # the host RAM it needed


# ── P3 — the budget, not taste, moves the embedder onto the GPU ──────────────

def test_p3_keeps_the_embedder_resident_on_the_gpu():
    s = _plan("P3")["seats"]["embedder"]
    assert s["device"] == "gpu:0"
    assert "demoted_from" not in s


def test_p3_heavy_fits_whole_so_it_never_offloads():
    """23140 MiB of budget holds the 17391 MiB model outright."""
    s = _plan("P3")["seats"]["heavy_hitter"]
    assert s["vram_mib"] == 17391
    assert s["offload"] == {}
    assert s["status"] == "leased"           # displaces the pinned seats


# ── P4 — where three rules become visible at once ────────────────────────────

def test_p4_places_the_heavy_model_whole_on_one_gpu():
    """R4: aggregate VRAM is not a resource; no model spans two cards."""
    s = _plan("P4")["seats"]["heavy_hitter"]
    assert s["device"] == "gpu:0"
    assert s["vram_mib"] == 17391
    assert "+" not in s["device"]


def test_p4_puts_the_brain_on_the_other_card():
    assert _plan("P4")["seats"]["interactive_brain"]["device"] == "gpu:1"


def test_p4_gpu0_stays_within_budget():
    p = _plan("P4")
    g0 = [g for g in p["budgets"]["gpus"] if g["index"] == 0][0]
    # 1811 = the e2b at the 32768 tool-seat context (was 1763 at 8192).
    assert p["pinned_vram_mib"]["gpu:0"] == 17391 + 1811 + 2029 == 21231
    assert p["pinned_vram_mib"]["gpu:0"] <= g0["available_mib"]


def test_p4_image_lease_leaves_the_other_gpu_serving():
    """R5: with a second GPU, image generation is not a full-system stall."""
    p = _plan("P4")
    img = p["seats"]["image"]
    assert img["device"] == "gpu:1"
    assert img["displaces"] == "gpu:1 only"
    assert p["seats"]["heavy_hitter"]["device"] == "gpu:0"


# ── P5 — CPU-only ────────────────────────────────────────────────────────────

def test_p5_collapses_sidekick_into_the_brain():
    """One seat rather than two copies of the cheapest tier."""
    seats = _plan("P5")["seats"]
    assert seats["sidekick"]["collapsed_into"] == "interactive_brain"
    assert seats["sidekick"]["model_id"] == seats["interactive_brain"]["model_id"]


def test_p5_heavy_is_leased_but_never_pinned():
    """Permitted transiently between the 65% target and the 75% hard ceiling."""
    s = _plan("P5")["seats"]["heavy_hitter"]
    assert s["status"] == "leased"
    assert s["over_target"] is True


def test_p5_refuses_image_because_there_is_no_gpu_to_lease():
    p = _plan("P5")
    assert p["seats"]["image"] is None
    assert [r for r in p["refusals"] if r["role"] == "image"][0]["rule_id"] == "R5"


# ── P6 — a class we can detect and cannot serve ──────────────────────────────

def test_p6_refuses_every_seat_rather_than_implying_a_backend():
    p = _plan("P6")
    assert all(v is None for v in p["seats"].values())
    assert {r["role"] for r in p["refusals"]} == set(rp.ROLES)
    assert all("unified-memory backend not implemented" in r["explanation"]
               for r in p["refusals"])


# ── Properties: hold for machines nobody wrote a fixture for ─────────────────

ALL = sorted(fx.ALL_PROFILES)


@pytest.mark.parametrize("key", ALL)
def test_property_no_gpu_budget_is_ever_exceeded(key):
    p = _plan(key)
    avail = {"gpu:%d" % g["index"]: g["available_mib"]
             for g in p["budgets"]["gpus"]}
    for dev, used in p["pinned_vram_mib"].items():
        assert used <= avail[dev], "%s overcommitted on %s" % (key, dev)


@pytest.mark.parametrize("key", ALL)
def test_property_no_model_is_split_across_gpus(key):
    """R4. A device may be 'gpu:N' or 'gpu:N+cpu' — never two GPU indices."""
    for seat in _plan(key)["seats"].values():
        if not seat:
            continue
        dev = str(seat.get("device") or "")
        assert dev.count("gpu:") <= 1, "%s split a model: %s" % (key, dev)


@pytest.mark.parametrize("key", ALL)
def test_property_image_is_exclusive_on_single_gpu_hosts(key):
    p = _plan(key)
    img = p["seats"]["image"]
    if img is None:
        continue_ = [r for r in p["refusals"] if r["role"] == "image"]
        assert continue_, "an unfilled image seat must carry a refusal"
        return
    assert img["exclusive"] is True
    if len(p["budgets"]["gpus"]) == 1:
        assert img["displaces"].startswith("all seats")


@pytest.mark.parametrize("key", ALL)
def test_property_every_generation_seat_has_an_explicit_num_ctx(key):
    """R7: no model ever runs on a backend default, in either direction."""
    for role in ("interactive_brain", "heavy_hitter", "sidekick", "embedder"):
        seat = _plan(key)["seats"].get(role)
        if seat:
            assert isinstance(seat["num_ctx"], int) and seat["num_ctx"] > 0


@pytest.mark.parametrize("key", ALL)
def test_property_every_empty_seat_carries_a_refusal(key):
    p = _plan(key)
    refused = {r["role"] for r in p["refusals"]}
    for role, seat in p["seats"].items():
        if seat is None:
            assert role in refused, \
                "%s left %s empty with no reason" % (key, role)


@pytest.mark.parametrize("key", ALL)
def test_property_ram_ceiling_holds_for_offloaded_seats(key):
    p = _plan(key)
    ram = p["budgets"]["ram"]
    for seat in p["seats"].values():
        host = (seat or {}).get("offload", {}).get("host_mib")
        if host:
            assert ram["os_reserve_mib"] + host <= ram["hard_ceiling_mib"]


@pytest.mark.parametrize("key", ALL)
def test_property_plan_is_deterministic(key):
    profile = fx.ALL_PROFILES[key]
    entries = fx.catalog(profile)
    first = json.dumps(rp.plan(profile, entries), sort_keys=True)
    for _ in range(20):
        assert json.dumps(rp.plan(profile, entries),
                          sort_keys=True) == first


@pytest.mark.parametrize("key", ALL)
def test_property_candidate_order_does_not_change_the_plan(key):
    """A catalog is a set; iteration order must not leak into placement."""
    profile = fx.ALL_PROFILES[key]
    entries = fx.catalog(profile)
    base = json.dumps(rp.plan(profile, entries), sort_keys=True)
    shuffled = list(reversed(copy.deepcopy(entries)))
    assert json.dumps(rp.plan(profile, shuffled), sort_keys=True) == base


@pytest.mark.parametrize("key", ALL)
def test_property_the_policy_never_mutates_its_inputs(key):
    """Purity: callers cache profiles and catalogs and reuse them."""
    profile = fx.ALL_PROFILES[key]
    entries = fx.catalog(profile)
    before = json.dumps([profile, entries], sort_keys=True)
    rp.plan(profile, entries)
    assert json.dumps([profile, entries], sort_keys=True) == before


# ── Headroom refusals ────────────────────────────────────────────────────────

def test_ram_refusal_states_its_arithmetic():
    out = rp.check_ram_headroom(fx.P1, add_mib=20000, current_host_mib=2000)
    assert out["ok"] is False
    assert out["rule_id"] == "R2"
    assert "24465" in out["explanation"]


def test_ram_headroom_allows_a_load_that_fits():
    assert rp.check_ram_headroom(fx.P1, add_mib=4000)["ok"] is True


def test_disk_refusal_names_the_pagefile_reason():
    """R8: on Windows a large resident model consumes disk via the pagefile."""
    out = rp.check_disk_headroom(fx.P1, artifact_mib=17 * 1024)
    assert out["ok"] is False
    assert out["rule_id"] == "R8"
    assert "pagefile" in out["explanation"]


def test_disk_headroom_allows_a_small_artifact():
    assert rp.check_disk_headroom(fx.P1, artifact_mib=1024)["ok"] is True


def test_disk_floor_scales_with_a_very_large_artifact():
    """floor = max(10 GB, artifact): a 30 GB model needs 30 GB left over."""
    out = rp.check_disk_headroom(
        dict(fx.P1, disk={"free_mib": 45 * 1024}), artifact_mib=30 * 1024)
    assert out["floor_mib"] == 30 * 1024
    assert out["ok"] is False


# ── Rules are inspectable data ───────────────────────────────────────────────

def test_every_rule_has_a_stable_id_and_text():
    assert [r["id"] for r in rp.RULES] == \
        ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"]
    assert all(r["text"] for r in rp.RULES)


def test_every_refusal_cites_a_real_rule():
    for key in ALL:
        for r in _plan(key)["refusals"]:
            assert r["rule_id"] in rp.RULE_BY_ID


# ── Context sized from the WHOLE prompt, not the tool list ───────────────────
#
# The defect these pin: TOOL_SEAT_NUM_CTX was derived from the 52-tool registry
# alone (~8534 tokens x4 -> 32768) and ignored the system prompt, which at
# ~11681 tokens is the larger of the two. Every tool seat therefore ran with
# 38% of its window free and nothing reported it.

def test_no_seat_is_sized_below_the_prompt_it_must_hold():
    """A window smaller than its own fixed overhead cannot work at all."""
    from agent_friday.services.context_budget import MEASURED_OVERHEAD_TOKENS
    for key in fx.ALL_PROFILES:
        for role, seat in _plan(key)["seats"].items():
            if not seat or role in rp.NO_PROMPT_ROLES or not seat.get("num_ctx"):
                continue
            assert seat["num_ctx"] >= MEASURED_OVERHEAD_TOKENS, (
                "%s/%s sized at %d, below the %d tokens of system prompt and "
                "tool schemas it must carry"
                % (key, role, seat["num_ctx"], MEASURED_OVERHEAD_TOKENS))


def test_every_tool_seat_has_real_conversation_room():
    from agent_friday.services.context_budget import MEASURED_OVERHEAD_TOKENS
    for key in fx.ALL_PROFILES:
        for role, seat in _plan(key)["seats"].items():
            if not seat or role in rp.NO_PROMPT_ROLES or not seat.get("num_ctx"):
                continue
            room = seat["num_ctx"] - MEASURED_OVERHEAD_TOKENS
            assert room >= rp.MIN_CONVERSATION_ROOM, (
                "%s/%s has %d tokens of room, below the %d floor"
                % (key, role, room, rp.MIN_CONVERSATION_ROOM))


def test_the_embedder_is_exempt_from_the_overhead_arithmetic():
    """It carries neither tools nor a system prompt, so it stays small."""
    s = _plan("P1")["seats"]["embedder"]
    assert s["num_ctx"] == 2048


def test_kv_slope_comes_from_the_models_own_rows():
    e = [x for x in fx.catalog(fx.ALL_PROFILES["P1"])
         if x["model_id"] == "gemma4:12b"][0]
    slope = rp.kv_slope_mib_per_token(e)
    # 7750 -> 7814 MiB across 65536 -> 131072, measured 2026-08-15.
    assert slope is not None and 0.0009 < slope < 0.0011


def test_kv_slope_refuses_a_non_positive_fit_rather_than_inventing_one():
    """VRAM that measured LOWER at a larger context is allocator noise.

    Real case: the 12b measured 8001 MiB at 16384 under Ollama 0.32.9 and 7718
    at 32768 under 0.32.11. A fit through those two rows slopes downward, and
    projecting along it would predict a model shrinking as its context grows.
    """
    e = {"model_id": "noisy:1b", "measured": [
        {"num_ctx": 16384, "vram_mib": 8001, "total_mib": 8001},
        {"num_ctx": 32768, "vram_mib": 7718, "total_mib": 7718}]}
    assert rp.kv_slope_mib_per_token(e) is None
    mib, basis = rp.vram_estimate_at(e, 131072)
    assert basis == "below-range", "a lower bound must not pass as an estimate"
    assert mib == 8001


def test_a_measured_row_is_labelled_measured_and_an_extrapolation_is_not():
    e = [x for x in fx.catalog(fx.ALL_PROFILES["P1"])
         if x["model_id"] == "gemma4:12b"][0]
    assert rp.vram_estimate_at(e, 131072) == (7814, "measured")
    mib, basis = rp.vram_estimate_at(e, 262144)
    assert basis == "extrapolated" and mib > 7814


def test_context_is_refused_with_arithmetic_when_the_floor_will_not_fit():
    e = {"model_id": "huge:400b", "measured": [
        {"num_ctx": 32768, "vram_mib": 400_000, "total_mib": 400_000}]}
    cb = rp.context_for("interactive_brain", e, 10_000, 20_215)
    assert cb["num_ctx"] is None
    assert "budget is 10000 MiB" in cb["capped_by"]


def test_a_model_whose_own_window_is_below_the_floor_says_so():
    e = {"model_id": "tiny-ctx:1b", "context_window": 4096,
         "measured": [{"num_ctx": 4096, "vram_mib": 500, "total_mib": 500}]}
    cb = rp.context_for("sidekick", e, 10_000, 20_215)
    assert cb["num_ctx"] == 4096
    assert "model context window" in cb["capped_by"]


# ── R10 — the sidekick survives every lease ──────────────────────────────────
#
# Stephen, 2026-08-15: "keep e2b awake so Friday is always alive." Before this,
# a lease stood down the whole pinned set, so asking for depth made Friday mute
# for the duration and the machine looked hung rather than busy.

def test_the_lease_budget_is_the_gpu_minus_the_retained_sidekick():
    p = _plan("P1")
    avail = p["budgets"]["gpus"][0]["available_mib"]
    side = p["seats"]["sidekick"]["vram_mib"]
    assert p["seats"]["heavy_hitter"]["offload"]["lease_budget_mib"] == \
        avail - side == 9997 - 1811


def test_an_image_lease_takes_everything_except_the_sidekick():
    s = _plan("P1")["seats"]["image"]
    assert s["exclusive"] is True
    assert s["displaces"] == "all seats except sidekick"
    assert s["retained_mib"] == 1811


def test_the_offload_point_comes_from_a_sweep_under_r10_conditions():
    """Measured 2026-08-15 with the sidekick resident, which is the condition
    R10 created and therefore the one the number has to hold under.

    18 layers lands the heavy model at 7973 MiB, inside the 8186 MiB lease
    budget. It is a MEASURED point, not the extrapolated 32 the old two-point
    linear fit asked for — that guess would have wasted 5.7 GB of card to run
    slower.
    """
    off = _plan("P1")["seats"]["heavy_hitter"]["offload"]
    assert off["n_cpu_moe"] == 18
    assert off["n_cpu_moe_basis"] == "measured"


def test_a_budget_inside_the_measured_sweep_reports_measured():
    assert rp.n_cpu_moe_for_budget(8_186) == (18, "measured")
    assert rp.n_cpu_moe_for_budget(6_100) == (20, "measured")
    assert rp.n_cpu_moe_for_budget(2_500) == (28, "measured")


def test_a_budget_below_the_whole_sweep_says_it_is_extrapolating():
    """An operating point outside the measured range is a starting guess for
    the next sweep, never a result — and the plan has to say which it is."""
    layers, basis = rp.n_cpu_moe_for_budget(1_000)
    assert basis == "extrapolated"
    assert layers > 28
