#!/usr/bin/env python3
"""Static check: user-facing docs never name a retired brain-ladder model.

Why this exists
----------------
The 2026-09-03 decision to remove Qwen from `model_plan.BRAIN_MODELS`
entirely (Gemma 4 only, a placeholder until FutureSpeak's own model ships)
was fixed in the ladder itself, then found AGAIN independently in three
more places over the next three days: the setup wizard's hardcoded default,
`install.ps1`'s own five-rung ladder (`scripts/gen_installer_ladder.py`
exists because of that one), and finally the public README — found by
Stephen looking at the published repo, after a same-session documentation
reconciliation pass had already run and missed it. Four fixes for one
fact is the same disease `check_settings_readers.py` exists for on the
settings side: a value asserted in more than one place drifts, and nothing
short of a mechanical recheck notices when it does.

`scripts/gen_installer_ladder.py --check` and
`tests/unit/test_installer_ladder_matches_plan.py` already close this gap
for `install.ps1`'s generated block specifically, by regenerating it FROM
`model_plan.BRAIN_MODELS` and diffing. Prose cannot be regenerated the same
way — a README sentence is hand-written, not templated — so this is a
narrower, cheaper check: does the CURATED set of user-facing files below
still name any of the SPECIFIC models the ladder used to contain and no
longer does.

What it checks, and what it deliberately does not
---------------------------------------------------
A fixed, hand-maintained list of retired brain-ladder model ids
(`BANNED_BRAIN_TOKENS`), not a guessed pattern like "anything shaped like a
model id". A pattern-based check would false-positive on `qwen3-embedding:
0.6b` (the embedder) and `qwen-image-q3ks` (local image generation) — both
real, CURRENT parts of the product, never part of the brain-ladder decision
this check is about. Guessing at "looks like a model name" cannot tell
those apart from a retired brain model; a maintained list can, at the cost
of needing a human to add the next family here when the ladder next
changes — the same tradeoff the destructive-command blocklist makes for
banned shell tokens, stated the same way: cheap and exact beats clever and
wrong.

The curated file list (`CHECKED_FILES`) is deliberately short and is
user-facing, living documentation — the things a stranger reads to learn
what this product currently does. `docs/audits/*`, `CHANGELOG.md`'s older
entries, and anywhere else Qwen's removal is itself the historical subject
being documented are correctly excluded: this check would be actively
wrong applied there, flagging the record of the fix as if it were the bug.

Honest limits: a file not in `CHECKED_FILES` is not covered, and a model
family retired in the future needs a human to extend
`BANNED_BRAIN_TOKENS` — this does not diff against `model_plan.py` live,
because "which removed family is worth banning" is a judgment call, not
something inferred from the current ladder alone (the current ladder does
not know what used to be in it).

Runs in under a second, no imports of the app itself required.
Exit 0 = clean. Exit 1 = at least one retired model name found.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Former `model_plan.BRAIN_MODELS` members, removed 2026-09-03. Extend this
#: the next time a family is retired from the reasoning ladder specifically
#: — NOT for embedding, image, or video models, which are separate catalogs
#: with their own independent lifecycles.
BANNED_BRAIN_TOKENS = [
    "qwen3:4b", "qwen3:8b", "qwen3:14b", "qwen3:32b",
    "qwen3.5:9b", "qwen3.6:27b", "qwen3.6-35b", "qwen3.6:35b",
]

#: Current-state, user-facing files. Adding a file here is a promise that
#: its prose stays in sync with the ladder; historical records (audits,
#: older CHANGELOG entries) are deliberately not included.
CHECKED_FILES = [
    "README.md",
    "docs/getting-started/installation.md",
    "docs/user-guide/configuration.md",
    "docs/reference/api.md",
    "docs/getting-started/tutorial.md",
    "KNOWN_ISSUES.md",
]


def find_problems() -> list:
    problems = []
    for rel in CHECKED_FILES:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for token in BANNED_BRAIN_TOKENS:
            if token.lower() in text:
                problems.append("%s: names retired brain-ladder model %r"
                                % (rel, token))
    return problems


def main() -> int:
    problems = find_problems()
    if problems:
        print("[check-stale-model-names] retired model names found in "
              "user-facing docs:")
        for p in problems:
            print("  -", p)
        print("[check-stale-model-names] if the ladder changed again and a "
              "new family needs banning, extend BANNED_BRAIN_TOKENS in "
              "this script instead of just fixing the doc.")
        return 1
    print("[check-stale-model-names] OK - no user-facing doc names a "
         "retired brain-ladder model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
