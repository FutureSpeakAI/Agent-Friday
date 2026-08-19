"""The seven working roles, and the arithmetic that lets them share models.

Stephen, 2026-08-18: seven roles -- memory manager, function manager,
embeddings manager, orchestrator, sidekick, researcher, heavy hitter -- with
one model allowed to hold several, and a warning BEFORE a selection overflows
the card rather than a refusal after it.

The bug these tests exist to prevent: counting a model once per ROLE instead of
once per MODEL. Three roles on one 12B is one process holding one copy of one
set of weights; charging it three times refuses a lineup that fits easily.
"""
from __future__ import annotations

import pytest

from agent_friday.services import residency_policy as rp


# ── fixtures: entries shaped like the real catalog ───────────────────────────

def _entry(model_id, vram_at_ctx, *, is_moe=False, embedding=False):
    """A catalog entry with MEASURED rows, which is what sizing reads."""
    return {
        "model_id": model_id,
        "backend": "llama-server",
        "params_total_b": 12.0,
        "context_window": 262144,
        "can_generate": not embedding,
        "is_embedding": embedding,
        "is_moe": is_moe,
        "measured": [{"num_ctx": c, "vram_mib": v, "total_mib": v}
                     for c, v in sorted(vram_at_ctx.items())],
    }


@pytest.fixture
def entries():
    return [
        _entry("gemma4:12b", {4096: 7689, 32768: 8180, 131072: 9652}),
        _entry("gemma4:e2b", {4096: 1629, 32768: 1811}),
        _entry("gemma4:26b", {4096: 9802, 32768: 9802}, is_moe=True),
        _entry("gemma4:e4b", {4096: 3081, 32768: 3300}),
        _entry("qwen3.5:9b", {4096: 5235, 32768: 5500}),
        _entry("qwen3-embed:0.6b-q8", {2048: 640}, embedding=True),
        _entry("functiongemma:270m", {4096: 300, 32768: 380}),
    ]


@pytest.fixture
def profile():
    return {
        "os_family": "windows",
        "gpus": [{"index": 0, "vram_total_mib": 12282,
                  "vram_baseline_mib": 542,
                  "vram_display_reserve_mib": 2800,
                  "compute_class": "consumer-fp8", "name": "RTX 4070"}],
        "ram": {"total_mib": 32620},
        "os_reserve_mib": 6144,
    }


# ── the make-or-break: one model, many roles, counted ONCE ───────────────────

def test_one_model_in_three_roles_is_charged_once(entries):
    """The subtle bug that would quietly ruin this."""
    cost = rp.assignment_cost(
        {"orchestrator": "gemma4:12b",
         "researcher": "gemma4:12b",
         "function_manager": "gemma4:12b"}, entries)

    assert cost["distinct_models"] == 1
    assert cost["roles_assigned"] == 3
    row = cost["models"][0]
    assert row["roles"] == ["function_manager", "orchestrator", "researcher"]

    # One copy of the weights, not three.
    one_copy = row["vram_mib"]
    assert cost["peak_vram_mib"] == one_copy
    assert cost["peak_vram_mib"] < 3 * one_copy


def test_the_same_three_roles_on_three_models_costs_three_times(entries):
    """The control arm: distinct models really do add up."""
    shared = rp.assignment_cost({"orchestrator": "gemma4:12b",
                                 "researcher": "gemma4:12b",
                                 "function_manager": "gemma4:12b"}, entries)
    split = rp.assignment_cost({"orchestrator": "gemma4:12b",
                                "researcher": "gemma4:26b",
                                "function_manager": "gemma4:e2b"}, entries)
    assert split["distinct_models"] == 3
    assert split["peak_vram_mib"] > shared["peak_vram_mib"]


def test_a_shared_model_gets_one_context_the_largest_any_role_needs(entries):
    """Two roles share one process, so they cannot have two context sizes."""
    cost = rp.assignment_cost({"embeddings_manager": "gemma4:12b",   # wants 2048
                               "orchestrator": "gemma4:12b"}, entries)
    assert cost["distinct_models"] == 1
    assert cost["models"][0]["num_ctx"] == rp.DEFAULT_NUM_CTX["orchestrator"]


def test_a_shared_model_takes_the_warmest_residency(entries):
    """Resident beats leased: a lease cannot evict what the conversation uses."""
    cost = rp.assignment_cost({"orchestrator": "gemma4:12b",     # resident
                               "researcher": "gemma4:12b"}, entries)  # leased
    assert cost["models"][0]["residency"] == rp.RESIDENT
    assert cost["resident_vram_mib"] > 0
    assert cost["peak_lease_vram_mib"] == 0


# ── residency classes: what actually costs VRAM all day ──────────────────────

def test_leases_are_exclusive_so_they_do_not_sum(entries):
    """Two leased models never sit on the card together."""
    cost = rp.assignment_cost({"heavy_hitter": "gemma4:26b",
                               "researcher": "gemma4:12b"}, entries)
    heavy = next(m for m in cost["models"] if m["model_id"] == "gemma4:26b")
    assert cost["peak_lease_vram_mib"] == heavy["vram_mib"]
    assert cost["resident_vram_mib"] == 0


def test_a_scheduled_role_costs_nothing_resident(entries):
    """The memory manager runs nightly; it must not be charged all day."""
    cost = rp.assignment_cost({"memory_manager": "gemma4:12b"}, entries)
    assert cost["models"][0]["residency"] == rp.ON_DEMAND
    assert cost["resident_vram_mib"] == 0


def test_seven_roles_fit_when_residency_is_respected(entries, profile):
    """The headline claim: seven roles on a 12 GB card, and it closes.

    Three models cover seven seats. The 8,458 MiB budget here is the honest
    one -- 12,282 less a 1 GB reserve less a 2,800 MiB compositor -- which is
    why the resident tier is small models and the big ones are leased.
    """
    view = rp.preview_assignment(
        {"orchestrator": "gemma4:e4b",
         "function_manager": "gemma4:e4b",
         "memory_manager": "gemma4:e4b",
         "sidekick": "gemma4:e2b",
         "embeddings_manager": "qwen3-embed:0.6b-q8",
         "researcher": "qwen3.5:9b",
         "heavy_hitter": "qwen3.5:9b"}, entries, profile)
    assert view["roles_assigned"] == 7
    assert view["distinct_models"] == 4
    assert view["fits"] is True, view["advice"]
    # Resident tier is only the two small seats; the 9B arrives on a lease.
    assert view["resident_vram_mib"] < view["vram_budget_mib"]


def test_a_resident_12b_beside_a_sidekick_does_not_fit_and_says_so(entries,
                                                                   profile):
    """Measured 2026-08-17 and encoded here: once the compositor's 2.8 GB is
    honestly reserved, a 12B cannot be RESIDENT beside the sidekick. This is
    the arithmetic that forces the orchestrator to be a small model."""
    view = rp.preview_assignment({"orchestrator": "gemma4:12b",
                                  "sidekick": "gemma4:e2b"}, entries, profile)
    assert view["fits"] is False
    assert view["overflow_mib"] > 0
    assert "gemma4:12b" in view["advice"]


def test_the_26b_no_longer_fits_the_lease_budget(entries, profile):
    """Its measured expert-offload seat is 9,802 MiB against 8,458 usable.

    It fitted when the display was assumed to want ~1 GB. It does not now, and
    the advisory must say so rather than let a lease fail at load time.
    """
    view = rp.preview_assignment({"heavy_hitter": "gemma4:26b",
                                  "sidekick": "gemma4:e2b"}, entries, profile)
    assert view["fits"] is False
    assert view["peak_state"] == "leased"
    assert view["overflow_mib"] > 0


# ── CPU placement buys headroom ──────────────────────────────────────────────

def test_a_cpu_capable_role_costs_no_vram(entries):
    cost = rp.assignment_cost({"embeddings_manager": "qwen3-embed:0.6b-q8"},
                              entries)
    row = cost["models"][0]
    assert row["device"] == "cpu"
    assert row["vram_mib"] == 0
    assert row["ram_mib"] > 0


def test_sharing_a_model_with_a_gpu_role_pulls_it_back_onto_the_gpu(entries):
    """An embedder sharing a model with the orchestrator sits where the
    orchestrator needs it -- one process cannot be on two devices."""
    cost = rp.assignment_cost({"embeddings_manager": "gemma4:12b",
                               "orchestrator": "gemma4:12b"}, entries)
    assert cost["models"][0]["device"] == "gpu"
    assert cost["models"][0]["vram_mib"] > 0


# ── warning at selection time, not after ─────────────────────────────────────

def test_an_overflowing_selection_warns_and_says_what_would_give(entries,
                                                                profile):
    view = rp.preview_assignment({"orchestrator": "gemma4:12b",
                                  "sidekick": "gemma4:26b",
                                  "function_manager": "gemma4:e2b"},
                                 entries, profile)
    assert view["fits"] is False
    assert view["overflow_mib"] > 0
    assert view["would_evict"], "must name what would have to give"
    assert "would have to give" in view["advice"]


def test_it_advises_rather_than_refuses(entries, profile):
    """A model he selects wins. This returns advice, never an exception."""
    view = rp.preview_assignment({"orchestrator": "gemma4:26b",
                                  "sidekick": "gemma4:26b"}, entries, profile)
    assert isinstance(view, dict)
    assert "advice" in view and view["advice"]


def test_a_fitting_selection_reports_what_is_left(entries, profile):
    view = rp.preview_assignment({"sidekick": "gemma4:e2b"}, entries, profile)
    assert view["fits"] is True
    assert view["vram_remaining_mib"] > 0
    assert "Fits" in view["advice"]


def test_an_uninstalled_model_is_named_not_silently_dropped(entries, profile):
    view = rp.preview_assignment({"orchestrator": "nemotron-3-nano:4b"},
                                 entries, profile)
    assert view["fits"] is False
    assert view["not_installed"] == ["nemotron-3-nano:4b"]
    assert "not installed" in view["advice"]


def test_the_advice_mentions_a_shared_model_so_the_saving_is_visible(entries,
                                                                    profile):
    view = rp.preview_assignment({"orchestrator": "gemma4:12b",
                                  "function_manager": "gemma4:12b",
                                  "researcher": "gemma4:12b"},
                                 entries, profile)
    assert "counted once" in view["advice"]


# ── aliases must not become second seats ─────────────────────────────────────

def test_embeddings_manager_and_embedder_are_one_seat(entries):
    assert rp.resolve_role("embeddings_manager") == "embedder"
    cost = rp.assignment_cost({"embeddings_manager": "qwen3-embed:0.6b-q8",
                               "embedder": "qwen3-embed:0.6b-q8"}, entries)
    assert cost["distinct_models"] == 1
    assert cost["models"][0]["roles"] == ["embedder"]


def test_an_unknown_role_is_reported_not_charged(entries):
    cost = rp.assignment_cost({"wizard": "gemma4:12b"}, entries)
    assert cost["distinct_models"] == 0
    assert cost["unknown_roles"][0]["role"] == "wizard"


def test_all_seven_named_roles_are_known():
    for role in ("memory_manager", "function_manager", "embeddings_manager",
                 "orchestrator", "sidekick", "researcher", "heavy_hitter"):
        assert rp.resolve_role(role) in rp.ROLES, role
        assert rp.residency_of(role) in (rp.RESIDENT, rp.LEASED, rp.ON_DEMAND)


# ── an unknown size is not a free model ──────────────────────────────────────

def test_an_unmeasured_model_is_not_counted_as_free(entries, profile):
    """The most expensive wrong answer this advisory could give.

    Coercing an unknown VRAM figure to 0 makes a lineup "fit" precisely because
    nobody knows what it costs. Caught 2026-08-18 running the real catalog:
    qwen3.5:9b had no measured row and the lineup came back FITS=True with the
    9B reading 0 MiB.
    """
    entries = entries + [{"model_id": "brand-new:9b", "backend": "llama-server",
                          "can_generate": True, "is_embedding": False,
                          "is_moe": False, "measured": []}]
    view = rp.preview_assignment({"orchestrator": "brand-new:9b"},
                                 entries, profile)
    row = view["models"][0]
    assert row["sized"] is False
    assert row["vram_mib"] is None, "unknown must not be reported as 0"
    assert view["fits"] is False
    assert view["unsized"] == ["brand-new:9b"]
    assert "never been measured" in view["advice"]


def test_a_measured_model_still_reports_a_number(entries, profile):
    view = rp.preview_assignment({"sidekick": "gemma4:e2b"}, entries, profile)
    assert view["models"][0]["sized"] is True
    # 1,811 was this seat at 32,768. Since injected context entered the
    # overhead count a tool-using seat needs 65,536 to hold one turn.
    assert view["models"][0]["vram_mib"] > 1811
    assert view["fits"] is True


# ── one artifact, one id ─────────────────────────────────────────────────────
# Found 2026-08-18: the same 0.6B embedder was registered as
# `qwen3-embed:0.6b-q8` (Friday's store) and `qwen3-embedding:0.6b` (Ollama).
# installed_entries deduped on an exact id match, so models present in BOTH
# stores under the SAME name collapsed correctly and this pair did not -- and
# the picker would have offered two rows for one model.

def _emb_entries():
    e = _entry("qwen3-embedding:0.6b", {2048: 640}, embedding=True)
    return [e, _entry("gemma4:e2b", {4096: 1629, 32768: 1811})]


def test_either_embedder_id_resolves_to_one_seat_and_one_charge():
    """Assign the embedder role by each id; expect one seat, one charge."""
    from agent_friday.services.residency_catalog import canonical_model_id
    assert canonical_model_id("qwen3-embed:0.6b-q8") == "qwen3-embedding:0.6b"

    canonical = rp.assignment_cost(
        {"embeddings_manager": "qwen3-embedding:0.6b"}, _emb_entries())
    legacy = rp.assignment_cost(
        {"embeddings_manager": "qwen3-embed:0.6b-q8"}, _emb_entries())

    assert canonical["distinct_models"] == legacy["distinct_models"] == 1
    assert legacy["models"][0]["model_id"] == "qwen3-embedding:0.6b"
    assert legacy["models"][0]["sized"] is True, \
        "the legacy id must inherit the canonical entry's measurements"
    assert canonical["peak_vram_mib"] == legacy["peak_vram_mib"]


def test_both_ids_at_once_are_still_one_model(entries):
    """The picker offering both rows must not double-charge the budget."""
    cost = rp.assignment_cost({"embeddings_manager": "qwen3-embedding:0.6b",
                               "memory_manager": "qwen3-embed:0.6b-q8"},
                              _emb_entries())
    assert cost["distinct_models"] == 1
    assert cost["roles_assigned"] == 2
    assert cost["models"][0]["roles"] == ["embedder", "memory_manager"]


def test_an_unaliased_id_passes_through_unchanged():
    from agent_friday.services.residency_catalog import canonical_model_id
    assert canonical_model_id("gemma4:e2b") == "gemma4:e2b"
    assert canonical_model_id(None) is None


def test_the_duplicate_detector_flags_look_alike_artifacts():
    """The standing check for duplicates nobody has met yet."""
    from agent_friday.services import residency_catalog as rc
    ents = [
        {"model_id": "some-embedder:0.6b", "artifact_bytes": 639150592,
         "is_embedding": True},
        {"model_id": "some-embedder-gguf:q8", "artifact_bytes": 644245094,
         "is_embedding": True},
        {"model_id": "gemma4:e2b", "artifact_bytes": 7162394016,
         "is_embedding": False},
    ]
    dups = rc.duplicate_candidates(ents)
    assert len(dups) == 1
    assert sorted(dups[0]["ids"]) == ["some-embedder-gguf:q8",
                                      "some-embedder:0.6b"]


def test_the_detector_does_not_flag_genuinely_different_models():
    from agent_friday.services import residency_catalog as rc
    ents = [
        {"model_id": "gemma4:e2b", "artifact_bytes": 7162394016,
         "is_embedding": False},
        {"model_id": "gemma4:e4b", "artifact_bytes": 9608338848,
         "is_embedding": False},
        {"model_id": "qwen3.5:9b", "artifact_bytes": 6549825126,
         "is_embedding": False},
    ]
    assert rc.duplicate_candidates(ents) == []


def test_an_already_aliased_pair_is_not_reported_twice():
    """The alias table has handled it; the detector must stay quiet."""
    from agent_friday.services import residency_catalog as rc
    ents = [
        {"model_id": "qwen3-embed:0.6b-q8", "artifact_bytes": 639150592,
         "is_embedding": True},
        {"model_id": "qwen3-embedding:0.6b", "artifact_bytes": 644245094,
         "is_embedding": True},
    ]
    assert rc.duplicate_candidates(ents) == []


def test_a_finetune_is_not_mistaken_for_its_base_model():
    """Regression for a false positive found on the live inventory 2026-08-18.

    A 2% relative tolerance flagged gemma4:12b (7,381,382,048 bytes) as a
    duplicate of the HauhauCS 12B finetune (7,516,192,768). They are genuinely
    different weights, and merging them in the picker would HIDE a model --
    worse than showing two rows. The tolerance is absolute now, because
    container framing overhead is a fixed cost, not a proportion.
    """
    from agent_friday.services import residency_catalog as rc
    ents = [
        {"model_id": "gemma4:12b", "artifact_bytes": 7381382048,
         "is_embedding": False},
        {"model_id": "hf.co/HauhauCS/Gemma4-12B-Balanced:Q4_K_M",
         "artifact_bytes": 7516192768, "is_embedding": False},
    ]
    assert rc.duplicate_candidates(ents) == []


def test_two_different_embedders_of_similar_size_are_not_merged():
    """Second false positive from the live inventory, 2026-08-18.

    embeddinggemma:300m (621,867,104) and qwen3-embedding:0.6b (639,150,592)
    are 17 MB apart and completely different models. No size threshold that
    catches a 5 MB framing difference can exclude them, so a duplicate must
    also SHARE A NAME.
    """
    from agent_friday.services import residency_catalog as rc
    ents = [
        {"model_id": "embeddinggemma:300m", "artifact_bytes": 621867104,
         "is_embedding": True},
        {"model_id": "qwen3-embedding:0.6b", "artifact_bytes": 639150592,
         "is_embedding": True},
    ]
    assert rc.duplicate_candidates(ents) == []


def test_a_real_duplicate_still_trips_both_conditions():
    from agent_friday.services import residency_catalog as rc
    ents = [
        {"model_id": "nomic-embed:v1.5", "artifact_bytes": 639150592,
         "is_embedding": True},
        {"model_id": "nomic-embed-text:latest", "artifact_bytes": 644245094,
         "is_embedding": True},
    ]
    dups = rc.duplicate_candidates(ents)
    assert len(dups) == 1
    assert "nomic" in dups[0]["shared_tokens"]


def test_registry_prefixes_and_quant_markers_are_not_evidence():
    """`hf.co/Org/` says where a model came from; Q4_K_M is not an identity."""
    from agent_friday.services.residency_catalog import _id_tokens
    toks = _id_tokens("hf.co/HauhauCS/Gemma4-12B-QAT-Uncensored:Q4_K_M")
    assert "hauhaucs" not in toks or "gemma4" in toks
    assert "q4_k_m" not in toks and "q4" not in toks
    assert _id_tokens("gemma4:e2b") & _id_tokens("gemma4:e4b") == {"gemma4"}


# ── seats are not sized where the cost is a guess ────────────────────────────
# 2026-08-18: a seat spawned at the architectural maximum of 262,144 left
# 448 MiB of 12,282 and took a monitor off the desktop. Above the largest
# measured context the VRAM figure is extrapolated, and on this family it
# extrapolates flat because sliding-window attention caps the KV cache across
# the measured range. It does not stay flat.

def test_a_seat_is_capped_at_the_largest_measured_context():
    e = _entry("well-measured:12b", {32768: 7718, 65536: 7750, 131072: 7814})
    cb = rp.context_for("interactive_brain", e, 12000, 47309)
    assert cb["num_ctx"] <= 131072, "must not exceed the measured range"


def test_when_every_sufficient_rung_is_a_guess_take_the_smallest():
    """The hole that defeated the cap on its first attempt.

    gemma4:e4b is measured only at 8,192. With the floor above that, no rung is
    both sufficient AND measured, the cap found nothing to apply, and the seat
    sailed past every rung to 262,144 -- the exact value the rule exists to
    prevent. Some extrapolation is unavoidable here; the largest possible
    extrapolation never is.
    """
    e = _entry("barely-measured:8b", {8192: 3081})
    cb = rp.context_for("interactive_brain", e, 12000, 47309)
    assert cb["num_ctx"] is not None
    assert cb["num_ctx"] < 262144, "never the architectural maximum on a guess"
    assert cb["num_ctx"] == 65536, "the smallest rung that holds one real turn"


def test_a_model_with_no_measurements_at_all_is_not_capped():
    """A fresh install must stay usable; there is nothing to be careful about."""
    e = {"model_id": "brand-new:8b", "backend": "llama-server",
         "can_generate": True, "is_embedding": False, "is_moe": False,
         "context_window": 262144, "measured": []}
    cb = rp.context_for("interactive_brain", e, 12000, 47309)
    assert cb["num_ctx"] is not None


def test_a_tool_seat_can_hold_a_real_turn():
    """32,768 could not: 47,309 tokens of overhead is 14,541 over the window."""
    from agent_friday.services import context_budget as cb
    assert rp.TOOL_SEAT_NUM_CTX >= 65536
    room = cb.working_room(rp.TOOL_SEAT_NUM_CTX)
    assert room["sufficient"] is True
    assert cb.working_room(32768)["sufficient"] is False


def test_injected_context_is_counted_in_the_overhead():
    """It was not counted at all, which is why seats came out too small."""
    from agent_friday.services import context_budget as cb
    o = cb.overhead(force=True)
    assert o["injected_tokens"] > 0
    assert o["total_tokens"] == (o["tool_tokens"] + o["system_prompt_tokens"]
                                + o["injected_tokens"])
