"""Regenerate the Windows installer's `$brainLadder` from `model_plan`.

`packaging/windows/install.ps1` cannot import Python — the venv does not
exist yet at the point in the install where it asks "how should Friday
think" — so it carries its own copy of the brain ladder as a PowerShell
literal. A copy that is retyped by hand is not a copy, it is a second
default that drifts: the installer's own five-rung Qwen ladder was hand-
maintained and grew stale of `model_plan._BRAINS` (see `headroom.md` §2.9,
`docs/design/headroom.md`). This script is the fix — it makes the copy a
BUILD ARTIFACT instead of a hand edit.

Usage, from the repo root::

    python scripts/gen_installer_ladder.py            # rewrite in place
    python scripts/gen_installer_ladder.py --check     # exit 1 on drift, write nothing

`--check` is what a release script or CI should run before cutting a build;
`tests/unit/test_installer_ladder_matches_plan.py` is the same check as a
test, so drift is caught long before a release.

WHAT IS GENERATED, WHAT IS NOT:

- `Id`, `Needs` (the model's footprint, `vram_gib`) and `Gb` (the download,
  `gib`) are FACTS from `model_plan.BRAIN_MODELS` — generated, never typed.
- `Says` is installer copy — a sentence a person reads while deciding what to
  download. A script should not invent that sentence, so it is looked up in
  `BLURBS` below, keyed by model id. A `BRAIN_MODELS` id with no entry in
  `BLURBS` is a decision nobody has made yet, so this script refuses to guess
  and fails loudly instead (the same shape as HR1: no basis, no verdict).
- Only tool-capable rows are offered, matching `model_plan._pickable()` —
  the planner will never SELECT a model that cannot call tools
  (`gemma3:4b` is the standing example), so the installer must not offer one
  as if it were a real rung either.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL_PS1 = REPO / "packaging" / "windows" / "install.ps1"

sys.path.insert(0, str(REPO / "src"))

#: Installer copy for each brain-ladder rung, by model id. Not derived from
#: model_plan's own `note` field — that prose is written for a person reading
#: the module source, not for someone answering a one-line installer prompt.
#: A new BRAIN_MODELS id needs a line added here BY A PERSON before this
#: script will offer it; that is the point, not an oversight.
BLURBS = {
    # 2026-09-05: Qwen removed from model_plan.BRAIN_MODELS entirely (Gemma 4
    # only, per the 2026-09-03 product decision — see model_plan.py's own
    # comment above _BRAINS). Blurbs below map each Gemma 4 rung to the same
    # ROLE its Qwen predecessor played in the old 4-rung ladder (smallest,
    # small-everyday, tuned-against, largest) rather than inventing new
    # copy from nothing — the claims those sentences make are about the
    # RUNG's place in the ladder, which hasn't changed, not about Qwen
    # specifically.
    "gemma4:e2b": "a small model - good for quick questions, weaker at long multi-step jobs",
    "gemma4:e4b": "a solid everyday model",
    "gemma4:12b": "the model Friday is tuned and measured against",
    "gemma4:26b": "the largest Friday offers - closest to a cloud model for tools and multi-step work",
}

_LADDER_RE = re.compile(
    r"(# BEGIN GENERATED: brainLadder\r?\n)"
    r".*?"
    r"(\r?\n# END GENERATED: brainLadder)",
    re.DOTALL,
)
_COMFORTABLE_RE = re.compile(
    r"(# BEGIN GENERATED: localIsComfortable\r?\n\$localIsComfortable = "
    r"\(\$null -ne \$brainPick -and \$brainPick\.Id -ne ')"
    r"[^']*"
    r"('\)\r?\n# END GENERATED: localIsComfortable)"
)


def _model_plan():
    from agent_friday.services import model_plan
    return model_plan


def _pickable_ladder(model_plan):
    return [m for m in model_plan.BRAIN_MODELS if m["tools"]]


def render_ladder_block(models) -> str:
    """The PowerShell `$brainLadder = @( ... )` literal, one rung per line."""
    rows = []
    id_w = max(len(f"'{m['id']}';") for m in models)
    for m in models:
        blurb = BLURBS.get(m["id"])
        if blurb is None:
            raise SystemExit(
                f"gen_installer_ladder: no installer blurb for {m['id']!r} — "
                "add one to BLURBS in scripts/gen_installer_ladder.py before "
                "regenerating (a script must not invent user-facing copy)"
            )
        id_field = f"'{m['id']}';".ljust(id_w)
        rows.append(
            f"    @{{ Id = {id_field} Needs = {m['vram_gib']:>5.2f}; "
            f"Gb = {m['gib']:>5.2f}; Says = '{blurb}' }},"
        )
    if rows:
        rows[-1] = rows[-1].rstrip(",")
    body = "\n".join(rows)
    return f"$brainLadder = @(\n{body}\n)"


def generate() -> str:
    """The updated install.ps1 text, or raise if the markers are missing."""
    text = INSTALL_PS1.read_text(encoding="utf-8")
    model_plan = _model_plan()
    models = _pickable_ladder(model_plan)
    if not models:
        raise SystemExit("gen_installer_ladder: model_plan.BRAIN_MODELS has "
                          "no tool-capable rows — refusing to write an empty ladder")

    ladder_block = render_ladder_block(models)
    if not _LADDER_RE.search(text):
        raise SystemExit("gen_installer_ladder: could not find the "
                          "'# BEGIN/END GENERATED: brainLadder' markers in "
                          f"{INSTALL_PS1}")
    text = _LADDER_RE.sub(
        lambda m: m.group(1) + ladder_block + m.group(2), text, count=1)

    floor_id = model_plan.FLOOR_MODEL
    if not _COMFORTABLE_RE.search(text):
        raise SystemExit("gen_installer_ladder: could not find the "
                          "'# BEGIN/END GENERATED: localIsComfortable' "
                          f"markers in {INSTALL_PS1}")
    text = _COMFORTABLE_RE.sub(
        lambda m: m.group(1) + floor_id + m.group(2), text, count=1)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                     help="exit 1 on drift instead of writing")
    args = ap.parse_args(argv)

    updated = generate()
    current = INSTALL_PS1.read_text(encoding="utf-8")
    if updated == current:
        print(f"[gen_installer_ladder] {INSTALL_PS1} already matches model_plan.BRAIN_MODELS")
        return 0
    if args.check:
        print(f"[gen_installer_ladder] {INSTALL_PS1} is stale against "
              "model_plan.BRAIN_MODELS — run `python scripts/gen_installer_ladder.py`")
        return 1
    INSTALL_PS1.write_text(updated, encoding="utf-8")
    print(f"[gen_installer_ladder] wrote {INSTALL_PS1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
