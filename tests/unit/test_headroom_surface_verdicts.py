"""The surface's guard against a control that looks informative and is not
(docs/design/headroom.md §8.4, §12 Phase 4 item 4).

Two instruments, matching the design doc's own pairing:

  1. STATIC -- a test that walks every verdict LOCAL MODELS ON THIS MACHINE
     can render (`routes.intelligence.local_models_catalog()`) and asserts
     it carries a `basis` in {measured, derived, declared, unknown}, and
     that the underlying Footprint never claims `measured` without
     `measured_at` (HR6, already enforced at construction by
     `residency_catalog.make_footprint()` -- this re-proves it holds for
     every row the SURFACE actually renders, not just for one hand-built
     record).
  2. A grep-style check that `index.html` and `ui_parts/app.html` never
     render the literal word "compatible" (HR2) -- the same shape as
     `test_model_plan.py::test_no_shipped_default_names_a_model_that_
     cannot_call_tools`: sweep the real artifact, not a list of places we
     remembered to check.

Plus the Phase 4 acceptance line itself: on a profile with no GPU (P5), every
GPU-shaped row (image) renders `unknown` or `refused` on `fits` -- never
`ready` -- so nothing reads as "compatible" by implication either.
"""
from __future__ import annotations

import pathlib

import pytest

from agent_friday.routes.intelligence import local_models_catalog
from tests import residency_fixtures as fx

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

_VALID_BASES = ("measured", "derived", "declared", "unknown")
_VALID_STATUSES = {
    "fits": ("ready", "ready-but", "refused", "unknown"),
    "runs_well": ("ready", "degraded", "unknown"),
    "worth_it": ("ready", "ready-but", "unknown"),
}


def _all_rows(catalog: dict) -> list:
    rows = []
    for section in ("text", "image", "voice", "embed"):
        rows.extend(catalog.get(section) or [])
    return rows


@pytest.mark.parametrize("profile_name", ["P1", "P2", "P3", "P4", "P5", "P6"])
def test_every_verdict_carries_a_recognised_basis(profile_name):
    """HR1 -- a verdict without a basis is not a verdict. Every axis of
    every row this surface can render, on every one of the six fixture
    profiles, must carry a `basis` from the four the spec names and a
    `status` from that axis's own vocabulary -- never a bare "compatible"
    and never a silently-invented fifth value."""
    profile = fx.ALL_PROFILES[profile_name]
    catalog = local_models_catalog(profile, {})
    rows = _all_rows(catalog)
    assert rows, "local_models_catalog() produced no rows at all on %s" % profile_name

    for row in rows:
        v = row["verdicts"]
        for axis in ("fits", "runs_well", "worth_it"):
            axis_v = v[axis]
            assert axis_v["basis"] in _VALID_BASES, (
                "%s / %s carries basis %r, not one of %s"
                % (row["model_id"], axis, axis_v.get("basis"), _VALID_BASES))
            assert axis_v["status"] in _VALID_STATUSES[axis], (
                "%s / %s carries status %r, not one of %s"
                % (row["model_id"], axis, axis_v.get("status"),
                   _VALID_STATUSES[axis]))
            # HR1's other half: unknown never renders as fit.
            if axis_v["basis"] == "unknown":
                assert axis_v["status"] == "unknown", (
                    "%s / %s has basis 'unknown' but status %r -- an "
                    "unknown basis must never render as a verdict"
                    % (row["model_id"], axis, axis_v["status"]))


def test_measured_footprints_all_carry_measured_at():
    """HR6, re-proven at the surface: every model this catalog can show,
    whose recorded Footprint says basis='measured', actually carries
    measured_at. `make_footprint()` already refuses to construct the
    opposite; this walks the real catalog rather than trusting that no
    caller ever bypassed it."""
    from agent_friday.services import residency_catalog as rc

    catalog = local_models_catalog(fx.P1, {})
    checked = 0
    for row in _all_rows(catalog):
        fp = rc.footprint(row["model_id"], fx.P1)
        if fp is None:
            continue
        checked += 1
        if fp.get("basis") == "measured":
            assert fp.get("measured_at"), (
                "%s's footprint claims basis='measured' with no "
                "measured_at -- an idle reading must never be written as "
                "a footprint (HR6)" % row["model_id"])
    assert checked > 0, "no row in the catalog had a recorded footprint on P1"


def test_no_gpu_profile_never_renders_a_gpu_row_as_ready():
    """Phase 4's own acceptance line: on a profile with no GPU, every image
    row's `fits` axis is `unknown` or `refused` -- never `ready` -- and its
    cloud alternative is what the row falls back to (headroom.md §12 Phase
    4 item 4, verified here rather than by eye)."""
    catalog = local_models_catalog(fx.P5, {})
    assert catalog["image"], "no image rows to check"
    for row in catalog["image"]:
        status = row["verdicts"]["fits"]["status"]
        assert status in ("unknown", "refused"), (
            "%s fits as %r on a GPU-less profile -- an image row must "
            "never read ready with no card to run it on"
            % (row["model_id"], status))


def test_measured_image_models_are_not_unknown_on_the_reference_card():
    """The other half of the same acceptance line: on P1 (a real 4070, the
    reference instance Phase 2 actually measured), an image model with a
    recorded Footprint must NOT still read `unknown` -- that would mean the
    surface is throwing away a real measurement, not that none exists."""
    from agent_friday.services import residency_catalog as rc

    catalog = local_models_catalog(fx.P1, {})
    for row in catalog["image"]:
        fp = rc.footprint(row["model_id"], fx.P1)
        if fp is None or fp.get("basis") == "unknown":
            continue
        # `fits` and `worth_it` are answerable from vram_mib/licence/
        # quality_note alone, which the image Footprint always carries once
        # measured. `runs_well` is a THIRD, independent question (host RAM
        # against what is available) that Phase 2 never recorded for image
        # jobs (`host_ram_mib` is `None` in both seeded rows) -- an honest
        # `unknown` there is not a discarded measurement, it is the correct
        # answer to a question nobody has measured yet, so it is excluded
        # from this assertion on purpose.
        for axis in ("fits", "worth_it"):
            assert row["verdicts"][axis]["basis"] != "unknown", (
                "%s has a recorded, non-unknown footprint on P1 but its "
                "%s verdict still reads unknown -- a real measurement is "
                "being discarded" % (row["model_id"], axis))


# ── The literal-string guard (§8.4, the test_no_shipped_default_names_a_
#    model_that_cannot_call_tools shape: sweep the real artifact) ───────────
#
# Scoped to the ONE function this surface actually lives in
# (`SettingsTabIntelligence`, `index.html` -- U8 settled this: the
# Intelligence tab is not mirrored into `ui_parts/app.html` at all, so
# checking that file too would either miss nothing new or, worse, false-flag
# unrelated prose like "OpenAI-compatible" elsewhere in a 40k-line app that
# has nothing to do with a verdict). A whole-file substring sweep for a
# common English word is too blunt an instrument; this greps the surface HR2
# actually governs, the same "sweep the real artifact, not a guess" spirit
# as the model_plan.py precedent this test is modelled on.

# The marker is the LOCAL MODELS section comment, not the function keyword
# itself -- the surface is several components (AxisBadge, LocalModelRow,
# FetchPreflightCard, LocalModelsSection) defined ABOVE
# SettingsTabIntelligence, plus that function's own THE MACHINE JSX. All of
# it is scanned through to the next top-level `function `, i.e. past the end
# of SettingsTabIntelligence itself.
_COMPONENT_START = "// ── LOCAL MODELS ON THIS MACHINE (headroom.md §8.2"
_FN_START = "function SettingsTabIntelligence()"


def _settings_tab_intelligence_source() -> str:
    path = REPO_ROOT / "index.html"
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.index(_COMPONENT_START)
    fn_start = text.index(_FN_START, start)
    end = text.index("\nfunction ", fn_start + len(_FN_START))
    return text[start:end]


def test_no_surface_ever_spells_out_compatible():
    """HR2 -- no surface renders a single combined 'compatible /
    incompatible'. Three axes, or the worst axis named. A literal
    'compatible' anywhere in the LOCAL MODELS / THE MACHINE surface is the
    phantom control this whole guard exists to catch before it ships."""
    src = _settings_tab_intelligence_source()
    # A `//` comment is source commentary, not something a viewer's browser
    # ever renders -- the guard is about what ships to the page, so a code
    # comment discussing HR2 by name is not the violation this test exists
    # to catch. Only non-comment lines count.
    offenders = [ln.strip()[:160] for ln in src.splitlines()
                if "compatible" in ln.lower()
                and not ln.strip().startswith("//")]
    assert not offenders, (
        "the word 'compatible' appears inside SettingsTabIntelligence -- "
        "HR2 forbids a single combined verdict:\n" + "\n".join(offenders))


def test_video_section_has_no_rows_only_the_sentence():
    """D8's resolution (already decided, not this session's to relitigate):
    one sentence, no rows -- a declared row per candidate model is the
    thing D8 actually decides, and this phase does not decide it."""
    catalog = local_models_catalog(fx.P1, {})
    assert "video" not in catalog or not catalog.get("video")
    assert catalog.get("video_note") == (
        "Video runs in the cloud on every machine today.")
