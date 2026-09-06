"""Gauntlet finding F13 — resolved under the maintainer's 2026-09-04 delegation
(docs/history/audits/gauntlet-2026-09-03/progress.md; findings.jsonl F13).

ui_parts/app.html is a hand-maintained mirror of index.html that nothing
builds from automatically. It is missing several components index.html has
(ConsentFlow, SettingsTabCosts, ConversationBar, QuickSwitch,
SettingsTabIntelligence among them) and that gap is expected to keep
widening — the mirror is kept only for history, per its own header note and
src/agent_friday/ui/build_ui.py's docstring.

The finding as originally filed worried that "a build that ever regenerated
index.html from this mirror would silently lose them." That risk is real
IF it's true — but re-reading src/agent_friday/ui/build_ui.py shows a
REGRESSION GUARD already landed on 2026-08-24, a full nine days before this
gauntlet audit opened: the assembler diffs top-level component names in the
existing index.html against what it's about to write, and refuses to write
(nonzero exit, existing file untouched) if the write would drop any of them,
unless a human explicitly passes --force. That guard already covers all 18+
components missing from the mirror, not just the five pinned here.

So of the three resolutions the original probe named — port the components,
retire the mirror, or lock down the build path so a regeneration can never
silently destroy shipped code — the third was already done, before this
finding was even filed. What was never done is proving it: the guard had no
test of its own. This probe now does that: it runs the real build script
against the real repo (safe, because the guard's whole job is to refuse to
write) and asserts it actually refuses, names the at-risk components, and
leaves index.html byte-for-byte untouched. That is the concrete, checked
form of "lock down the build path" — the mirror staying stale is accepted
and by design, not a defect, as long as this stays green.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_HTML = _REPO_ROOT / "ui_parts" / "app.html"
_INDEX_HTML = _REPO_ROOT / "index.html"
_BUILD_UI = _REPO_ROOT / "src" / "agent_friday" / "ui" / "build_ui.py"

_HIGH_SEVERITY_MISSING = (
    "ConsentFlow",
    "SettingsTabCosts",
    "ConversationBar",
    "QuickSwitch",
    "SettingsTabIntelligence",
)


def test_mirror_is_still_missing_components_this_probe_expects_that():
    text = _APP_HTML.read_text(encoding="utf-8")
    missing = [name for name in _HIGH_SEVERITY_MISSING
               if f"function {name}(" not in text]
    assert missing, (
        "ui_parts/app.html now has all the previously-missing high-severity "
        "components -- if someone ported them, great, but then this test "
        "(and F13's premise) is obsolete and should be deleted, not left "
        "green by accident."
    )


def test_build_ui_refuses_to_silently_drop_shipped_components():
    before = _INDEX_HTML.read_bytes()
    proc = subprocess.run(
        [sys.executable, str(_BUILD_UI)],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    after = _INDEX_HTML.read_bytes()

    assert after == before, (
        "build_ui.py modified index.html when run without --force -- the "
        "2026-08-24 regression guard is no longer protecting the file the "
        "server actually serves. This is the real risk F13 was about; it "
        "must stay closed."
    )
    assert proc.returncode != 0, (
        "build_ui.py exited 0 without --force even though ui_parts/app.html "
        "is missing shipped components -- it should refuse and say so."
    )
    assert "REFUSING" in proc.stdout, (
        "build_ui.py did not print its refusal message -- a silent nonzero "
        "exit is almost as bad as a silent overwrite for a script a human "
        "runs by hand and glances at."
    )
    for name in _HIGH_SEVERITY_MISSING:
        assert name in proc.stdout, (
            f"build_ui.py's refusal message doesn't name {name} as one of "
            "the components its write would have dropped -- the guard "
            "should be diagnosing the actual gap, not just refusing blindly."
        )
