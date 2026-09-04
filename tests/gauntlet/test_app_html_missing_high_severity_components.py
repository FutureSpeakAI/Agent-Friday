"""Gauntlet finding F13 (queued for Stephen, not fixed -- see
docs/audits/gauntlet-2026-09-03/progress.md).

ui_parts/app.html is a hand-maintained mirror of index.html that nothing
builds from. KNOWN_ISSUES.md claimed the gap was a fixed "17 components";
re-measured 2026-09-04 it was actually 11 at that entry's own commit and is
14 today -- the gap widens over time, it isn't a stable number (the doc
text has been corrected to say so instead of citing a specific count).

Of the 14 currently missing, five (originally four; sharpened 2026-09-04
by a second sweep -- see findings.jsonl F13's follow-up note) are
high-severity because they are either constantly-visible core chrome or
have a documented history of costing real money or blocking onboarding
when they've gone missing before:

  - ConsentFlow             -- the entire first-run onboarding wizard,
                               including vault passphrase collection
  - SettingsTabCosts        -- the cost/budget panel (previously deleted
                               for two months; real spend ran to
                               $1,189.76/mo against a $50 budget before it
                               was restored)
  - ConversationBar         -- persistent chat-window chrome (conversation
                               title, per-chat model pin)
  - QuickSwitch             -- the main one-click model-switch affordance
  - SettingsTabIntelligence -- the permanently-visible Settings -> Intelligence
                               tab (per-capability model-seat picker,
                               VRAM/RAM display, refusal thresholds) that
                               writes the current capability_routing shape.
                               app.html still ships the OLD tab it replaced
                               (id 'models', writing the deprecated flat
                               orchestrator_model/creative_model/
                               subagent_model keys F7 already flags as
                               latent risk) -- so regenerating from this
                               mirror wouldn't just drop a button, it would
                               silently regress the routing settings UI to
                               the flat-key scheme. Its 8 supporting
                               sub-components (Bar, ConversationSeatPicker,
                               ConversationRow, LoadFailure, ModelPicker,
                               ModelRow, RefusalGroup, RoleRow, StateChip)
                               exist solely to support it and are missing
                               alongside it, but are not pinned separately
                               here -- they have no independent severity of
                               their own outside this one tab.

This probe pins their absence from ui_parts/app.html. It is deliberately
RED: porting components into a hand-maintained mirror unattended,
overnight, is not a call to make unilaterally -- it needs Stephen to
decide whether to port them, delete the mirror file entirely (if nothing
depends on it), or accept the drift and make sure no build path can ever
regenerate index.html from it (KNOWN_ISSUES.md's §2 history records that
exact incident happening once already). This probe should stay red until
one of those happens; a change that just deletes app.html or suppresses
this test without addressing the underlying question is not a fix.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_HTML = _REPO_ROOT / "ui_parts" / "app.html"

_HIGH_SEVERITY_MISSING = (
    "ConsentFlow",
    "SettingsTabCosts",
    "ConversationBar",
    "QuickSwitch",
    "SettingsTabIntelligence",
)


def test_high_severity_components_are_present_in_the_app_html_mirror():
    text = _APP_HTML.read_text(encoding="utf-8")
    missing = [name for name in _HIGH_SEVERITY_MISSING
               if f"function {name}(" not in text]
    assert not missing, (
        "ui_parts/app.html is missing these high-severity components that "
        "index.html has: " + ", ".join(missing) + " -- a build that ever "
        "regenerated index.html from this mirror would silently lose them "
        "(see findings.jsonl F13). This probe stays red until Stephen "
        "decides whether to port them, retire the mirror, or lock down the "
        "regenerate-from-mirror build path so this can never bite again."
    )
