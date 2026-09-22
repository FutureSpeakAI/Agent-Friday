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
The maintainer looking at the published repo, after a same-session documentation
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

import datetime as _dt
import re
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


# ══════════════════════════════════════════════════════════════════════════
#  SECTION 2 — Google / Gemini models with a published shutdown DATE
# ══════════════════════════════════════════════════════════════════════════
# Why this section is dated and the brain-ladder one is not
# ---------------------------------------------------------
# A retired brain-ladder model is a decision: it stopped being true the day
# someone decided it, and a banned-token list captures that exactly. Google's
# retirements are a CALENDAR. `gemini-omni-flash-preview` worked perfectly on
# 2026-09-29 and returned 404 on 2026-09-30, and nothing in the repo changed
# in between. A token blocklist cannot express that, because the same string
# is correct before the date and broken after it.
#
# This section was added 2026-09-22 after `gemini-omni-flash-preview`
# (shutdown 2026-09-30) and `gemini-2.5-flash-image` (shutdown 2026-10-02)
# were BOTH found still wired into dispatch with eight and ten days left —
# not caught by any test, any check, or any of the several passes that had
# touched those same files in the weeks before. They were found by a human
# reading Google's deprecations page. That is not a repeatable process, which
# is the whole argument for this being code.
#
# What it does: fails while there is still time to act, not after. An id in
# GOOGLE_MODEL_SHUTDOWNS becomes an error once its shutdown date is within
# WARN_WINDOW_DAYS, so the build breaks with a month of runway instead of on
# the morning the model goes dark.
#
# Honest limits, same as above: this table is hand-maintained and does not
# fetch ai.google.dev. It cannot know about a retirement nobody has entered
# here. What it CAN do — and what the omni/nano-banana incident needed — is
# make sure a retirement someone already wrote down cannot sit in the repo
# unnoticed until the date passes. Adding the next one is a one-line edit;
# the check then does the remembering.

#: Days of runway. A shutdown further out than this is recorded but not yet
#: an error, so the table can be filled in as soon as Google announces
#: something without immediately breaking the build.
WARN_WINDOW_DAYS = 30

#: wire id -> (shutdown date ISO, replacement id, note)
#: Dates from ai.google.dev/gemini-api/deprecations. The ids themselves were
#: confirmed to exist (or not) against the live API via models.get on
#: 2026-09-22 — a docs page saying a model is gone is not the same as the
#: model being gone, and vice versa.
GOOGLE_MODEL_SHUTDOWNS = {
    "gemini-omni-flash-preview": (
        "2026-09-30", "gemini-omni-1.1-flash",
        "any-to-any video; reached via the gemini-omni-flash friendly alias"),
    "gemini-2.5-flash-image": (
        "2026-10-02", "gemini-3.1-flash-image",
        "the original Nano Banana; reached via the bare nano-banana alias"),
    "gemini-3.1-flash-lite": (
        "2027-05-07", "gemini-3.5-flash-lite", "cheap high-volume text tier"),
    "lyria-3-pro-preview": (
        "2027-05-07", "lyria-3.5", "full-song music generation"),
}

#: NOT in the table above, and deliberately so — written here instead of
#: being encoded as if it were checked.
#:
#: `gemini-2.5-pro` and `gemini-2.5-flash` are asserted to sunset 2026-10-16
#: by a comment in provider_registry.py itself. That date was NOT re-verified
#: against ai.google.dev in the 2026-09-22 pass that built this section, and
#: `gemini-2.5-flash` in particular is load-bearing: it carries ROLE_VOICE and
#: is a pickable voice model, so acting on an unconfirmed date would remove a
#: working model from a user's picker. Confirm the date, then move both ids
#: into GOOGLE_MODEL_SHUTDOWNS — at which point this check will fail until
#: the catalogue is updated, which is the intended outcome.
GOOGLE_UNVERIFIED_SHUTDOWNS = {
    "gemini-2.5-pro": "2026-10-16?",
    "gemini-2.5-flash": "2026-10-16?",
}

#: Files that actually DISPATCH to a Gemini wire id — the ones where a dead
#: id is a runtime 404 rather than stale prose. Kept deliberately to the
#: dispatch and pricing path.
GOOGLE_CHECKED_FILES = [
    "src/agent_friday/services/creative_engine.py",
    "src/agent_friday/services/music_engine.py",
    "src/agent_friday/services/voice_engine.py",
    "src/agent_friday/services/provider_registry.py",
    "src/agent_friday/services/cost_meter.py",
    "src/agent_friday/core/__init__.py",
    "src/agent_friday/routes/voice.py",
    "ui_parts/app.html",
]

#: (file, model id) pairs where naming a retiring model is CORRECT and must
#: not fail the check. Each needs a reason, because an unexplained entry here
#: is indistinguishable from someone silencing the check to get green.
GOOGLE_ALLOWED = {
    # A price row must outlive its model: spend already recorded against the
    # old id still has to price, and a missing row meters $0 — which the cost
    # panel renders the same as "ran locally, free".
    ("src/agent_friday/services/cost_meter.py", "gemini-omni-flash-preview"),
    ("src/agent_friday/services/cost_meter.py", "gemini-2.5-flash-image"),
    ("src/agent_friday/services/cost_meter.py", "gemini-3.1-flash-lite"),
    # Forward-resolving alias: an old id in settings.json or a saved creation
    # is MAPPED to the replacement here, which is the opposite of dispatching
    # to it.
    ("src/agent_friday/services/creative_engine.py", "gemini-omni-flash-preview"),
    # The comment recording WHY the bare nano-banana alias was repointed off
    # gemini-2.5-flash-image. Deleting the old id from that sentence would
    # make the note unreadable, and the check's job is to stop the repo
    # DISPATCHING to a dead model, not to stop it remembering one.
    ("src/agent_friday/services/creative_engine.py", "gemini-2.5-flash-image"),
    # Same shape: the catalogue comment records what the gemini-omni-flash
    # friendly id USED to resolve to, and why the swap needed no settings
    # migration. The descriptor's "models" list names the friendly id only.
    ("src/agent_friday/services/provider_registry.py", "gemini-omni-flash-preview"),
}


def _today() -> _dt.date:
    return _dt.date.today()


def _mentions(text: str, model_id: str) -> bool:
    """Does the file name this EXACT id, not merely a longer id containing it?

    Without the boundary check, 'gemini-2.5-pro' matches inside
    'gemini-2.5-pro-preview-03-25' and 'gemini-2.5-flash-image' matches
    inside 'gemini-2.5-flash-image-preview' — different models with
    different dates. A substring check here would report retirements that
    are not the ones being looked at.
    """
    return re.search(r"(?<![\w.-])%s(?![\w.-])" % re.escape(model_id), text) is not None


def find_google_problems(today: _dt.date | None = None) -> list:
    today = today or _today()
    problems = []
    for rel in GOOGLE_CHECKED_FILES:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for model_id, (iso, replacement, note) in sorted(GOOGLE_MODEL_SHUTDOWNS.items()):
            if (rel, model_id) in GOOGLE_ALLOWED:
                continue
            if not _mentions(text, model_id):
                continue
            shutdown = _dt.date.fromisoformat(iso)
            days = (shutdown - today).days
            if days > WARN_WINDOW_DAYS:
                continue
            when = ("SHUT DOWN %s (%d days ago)" % (iso, -days) if days < 0
                    else "shuts down %s (in %d days)" % (iso, days))
            problems.append(
                "%s: names %r, which %s - %s. Replacement: %r"
                % (rel, model_id, when, note, replacement))
    return problems


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
    failed = False
    problems = find_problems()
    if problems:
        failed = True
        print("[check-stale-model-names] retired model names found in "
              "user-facing docs:")
        for p in problems:
            print("  -", p)
        print("[check-stale-model-names] if the ladder changed again and a "
              "new family needs banning, extend BANNED_BRAIN_TOKENS in "
              "this script instead of just fixing the doc.")
    else:
        print("[check-stale-model-names] OK - no user-facing doc names a "
              "retired brain-ladder model")

    google = find_google_problems()
    if google:
        failed = True
        print("[check-stale-model-names] Gemini models at or near shutdown "
              "are still wired into dispatch:")
        for p in google:
            print("  -", p)
        print("[check-stale-model-names] repoint the alias/default to the "
              "replacement. If naming the old id is deliberate (a price row "
              "that must outlive its model, or a forward-resolving alias), "
              "add the (file, id) pair to GOOGLE_ALLOWED with a reason.")
    else:
        print("[check-stale-model-names] OK - no dispatch path names a Gemini "
              "model shutting down within %d days" % WARN_WINDOW_DAYS)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
