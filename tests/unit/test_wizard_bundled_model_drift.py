"""The setup wizard must never hard-code the bundled/floor model — H3.

The 2026-08-26 sweep (`7da7798`) replaced a hand-typed ``'gemma3:4b'`` — the
one row in `model_plan._BRAINS` that cannot call tools — with
`model_plan.FLOOR_MODEL` at five Python call sites, but the setup wizard's own
HTML/JS was outside that sweep's reach (`headroom.md` §2.9, defect H3-in-UI).
`WizardGemmaPull` now reads the floor model from `hw.floor_model`, served by
`/api/health/full` from `model_plan.FLOOR_MODEL` (`routes/platform.py`),
instead of a literal — this test holds that fix from drifting back, and it is
built on the same phantom-literal shape as
`test_higgsfield_catalog.py::test_no_invented_model_ids_in_source`: a literal
reintroduced as a LIVE value fails, the same literal named in prose that
explains why it must not come back does not.

Two files carry the wizard: `index.html` (served) and `ui_parts/app.html`
(its JSX source, `project_ui_build_divergence` — the two are kept in step by
hand). Both are checked.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "index.html"
APP_HTML = ROOT / "ui_parts" / "app.html"

# The one row in the brain ladder that cannot call tools (model_plan.py,
# `_BRAINS`) — the model H3 kept naming by accident.
PHANTOM = "gemma3:4b"

_WIZARD_START = re.compile(r"const\s+WIZARD_STEPS\s*=")
_WIZARD_END = "function SetupWizard"


def _wizard_block(text: str) -> str:
    """The setup-wizard section: `WIZARD_STEPS` through the end of
    `WizardGemmaPull`, stopping just before `SetupWizard` begins."""
    m = _WIZARD_START.search(text)
    assert m, "could not find `const WIZARD_STEPS =` — has the wizard moved?"
    end = text.index(_WIZARD_END, m.start())
    return text[m.start():end]


def _non_comment_lines(block: str):
    """Lines that are not entirely a `//` comment — where a phantom literal
    would actually run, as opposed to being named in an explanation of why
    it must not."""
    for line in block.splitlines():
        if line.strip().startswith("//"):
            continue
        yield line


@pytest.mark.parametrize("path", [INDEX_HTML, APP_HTML], ids=["index.html", "app.html"])
def test_wizard_never_hardcodes_the_bundled_model(path):
    text = path.read_text(encoding="utf-8")
    block = _wizard_block(text)
    for line in _non_comment_lines(block):
        assert PHANTOM not in line, (
            f"{path.name}: the wizard reintroduces the literal {PHANTOM!r} "
            "as a live value. The bundled/floor model must be read from "
            "hw.floor_model (model_plan.FLOOR_MODEL via /api/health/full), "
            "never retyped by hand — that is defect H3."
        )


@pytest.mark.parametrize("path", [INDEX_HTML, APP_HTML], ids=["index.html", "app.html"])
def test_wizard_reads_floor_model_from_the_hardware_payload(path):
    text = path.read_text(encoding="utf-8")
    block = _wizard_block(text)
    assert "hw.floor_model" in block, (
        f"{path.name}: WizardGemmaPull should source its model id from "
        "hw.floor_model, the field /api/health/full derives from "
        "model_plan.FLOOR_MODEL"
    )


@pytest.mark.parametrize("path", [INDEX_HTML, APP_HTML], ids=["index.html", "app.html"])
def test_wizard_install_check_is_an_exact_tag_match(path):
    """The family-prefix match (`startsWith('gemma3')`) reported a small
    variant of a family as installed when only a larger one was present
    (`model_setup._resolves`'s own docstring names the exact failure). The
    wizard's install-detection must not use a bare family prefix."""
    text = path.read_text(encoding="utf-8")
    block = _wizard_block(text)
    for line in _non_comment_lines(block):
        assert "startsWith('gemma3')" not in line, (
            f"{path.name}: family-prefix match reintroduced — a specific "
            "tag must match exactly (or with a quantisation suffix), per "
            "model_setup._resolves"
        )
