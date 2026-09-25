"""The setup wizard must never hard-code the bundled/floor model — H3.

Python call sites use `model_plan.FLOOR_MODEL` rather than a hand-typed
``'gemma3:4b'`` — the one row in `model_plan._BRAINS` that cannot call tools —
and the setup wizard's own HTML/JS must do the same (`headroom.md` §2.9,
defect H3-in-UI). `WizardGemmaPull` reads the floor model from `hw.floor_model`, served by
`/api/health/full` from `model_plan.FLOOR_MODEL` (`routes/platform.py`),
instead of a literal — this test holds that fix from drifting back, and it is
built on the same phantom-literal shape as
`test_higgsfield_catalog.py::test_no_invented_model_ids_in_source`: a literal
reintroduced as a LIVE value fails, the same literal named in prose that
explains why it must not come back does not.

The pull is shown on the first-run setup chat's basics card. Two files carry
it: `index.html` (served) and `ui_parts/app.html` (its hand-kept JSX
mirror). Both are checked.
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

_WIZARD_START = re.compile(r"function\s+exactTag\s*\(")
_PULL = "function WizardGemmaPull"


def _wizard_block(text: str) -> str:
    """The bundled-model pull the setup chat's basics card shows: `exactTag`
    through the end of `WizardGemmaPull`, stopping at the next top-level
    function."""
    m = _WIZARD_START.search(text)
    assert m, "could not find `function exactTag(` — has the model pull moved?"
    pull = text.index(_PULL, m.start())
    end = text.index("\nfunction ", pull + len(_PULL))
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
