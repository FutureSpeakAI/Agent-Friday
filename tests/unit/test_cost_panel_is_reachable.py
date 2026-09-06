"""The Cost & Usage panel must be reachable, and must read what it fetches.

This is the generalisation of what a spend audit found on 2026-08-30.

The panel was complete and working when it landed in 91411e9: a collapsible
Settings section with range pills, a total, a daily sparkline, per-provider /
per-workspace / per-model breakdowns and budget controls. Commit 0cd13fc
("replace 1,270-line sidebar with a minimal quick-access strip; add a full
tabbed Settings workspace") deleted the rendering JSX and kept everything
behind it. What survived was:

  * four useState declarations -- costSummary, costSeries, costSched,
    costBudget -- written by their setters and read by NOTHING;
  * a refreshCosts() that fetches four endpoints;
  * a useEffect gated on `openSections.costs`, a key that stayed in the init
    object while the only control that could flip it, toggleSection('costs'),
    went with the JSX. The effect could therefore never fire;
  * saveBudget(), defined and called by nothing.

The backend never stopped working. /api/costs/summary returned 200 the whole
time. So for two months the meter recorded every call correctly and delivered
it to a panel that had been deleted, and the only spend a user could actually
see was one "$3.50 today" line on the Anthropic row of the Providers tab.

The cost of that silence, measured on Stephen's own install the day it was
found: $1,189.76 for the month against a $50 monthly budget whose alert was
switched off -- switched off because the only UI that could ever have armed
it was the one that had been deleted.

Two of these tests would have failed the day 0cd13fc landed.

SCOPE: structure only. Whether the numbers are RIGHT is tests/unit/
test_cost_meter.py's job; whether they can be SEEN is this file's.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX = _ROOT / "index.html"


def _src() -> str:
    return _INDEX.read_text(encoding="utf-8", errors="replace")


def _reachable_tab_ids(src: str) -> set:
    """Tab ids that have BOTH a TABS entry and a render branch.

    Either half alone is a ghost: an entry with no branch highlights a tab
    over a blank pane, and a branch with no entry is what SettingsTabProviders
    had -- reachable only by an event nothing visible dispatched.
    """
    ids = set(re.findall(r"id:\s*'([a-z_]+)',\s*\n\s*label:\s*'([^']+)'", src))
    return {tab_id for tab_id, _label in ids
            if re.search(r"tab === '%s' && " % re.escape(tab_id), src)}


def test_the_tab_parse_still_works():
    """Guard the guard: a regex that stopped matching would pass everything."""
    reachable = _reachable_tab_ids(_src())
    assert len(reachable) >= 8, "parsed only %d tabs: %s" % (
        len(reachable), sorted(reachable))
    assert "providers" in reachable


def test_cost_and_usage_is_a_reachable_tab():
    """The deletion regression, stated directly."""
    reachable = _reachable_tab_ids(_src())
    assert "costs" in reachable, (
        "Cost & Usage is unreachable -- it has lost its TABS entry or its "
        "render branch. A person cannot see what they are spending. "
        "reachable tabs: %s" % sorted(reachable)
    )


def test_the_panel_reads_every_value_it_fetches():
    """The orphan guard.

    A setter with no corresponding reader is a fetch whose result goes
    nowhere -- precisely the state 0cd13fc left behind, and precisely what
    no test noticed for two months. Each name is declared once by its
    useState; any additional occurrence is a read.
    """
    src = _src()
    orphaned = []
    for name in ("costSummary", "costSeries", "costSched", "costBudget"):
        reads = len(re.findall(r"\b%s\b" % name, src))
        if reads < 2:
            orphaned.append("%s (declared, never read)" % name)
    assert not orphaned, (
        "these are fetched and then discarded; the panel that rendered them "
        "has been deleted again:\n  %s" % "\n  ".join(orphaned)
    )


def test_the_budget_controls_are_wired_to_something():
    """saveBudget existed and was called by nothing for two months."""
    src = _src()
    calls = len(re.findall(r"\bsaveBudget\b", src))
    assert calls >= 2, (
        "saveBudget is defined and never called -- the budget alerts cannot "
        "be armed from the UI, which is how a $50 monthly limit went 24x "
        "over in silence"
    )


def test_the_budget_post_carries_the_api_token():
    """POST /api/costs/budget is @login_required.

    The deleted code used a bare fetch(), which sends no X-Friday-Token and
    would have 401'd had anything ever called it. apiFetch adds the header.
    """
    src = _src()
    idx = src.find("/api/costs/budget'")
    assert idx != -1, "the budget endpoint is not called at all"
    # Look at the whole cost block rather than one call site.
    block_start = max(0, src.find("/api/costs/summary") - 2000)
    block = src[block_start:src.find("/api/costs/budget'") + 4000]
    assert "apiFetch('/api/costs/budget'" in block, (
        "the budget calls must go through apiFetch -- a bare fetch() omits "
        "X-Friday-Token and the authenticated POST silently fails"
    )


def test_the_panel_says_what_it_does_not_count():
    """Honesty requirement.

    cost_meter records Friday's OWN model calls. Anything Claude Code or any
    other tool spends on its own account is invisible to it. A total that
    looks complete but is not is worse than no total, so the panel has to say
    so where the number is read, not in a doc nobody opens.
    """
    src = _src()
    assert "own model calls" in src, (
        "the panel must state that it covers Friday's own model calls only"
    )


def test_the_panel_does_not_arm_budgets_on_the_users_behalf():
    """Both alerts being off is a consequence of the deletion, not a choice
    Stephen made -- but the fix is to let him arm them, not to flip them on
    for him. A UI default of `true` here would spend his attention without
    asking."""
    src = _src()
    start = src.find("const [costBudget")
    assert start != -1, "costBudget state is gone"
    decl = src[start:start + 400]
    assert "daily_enabled: false" in decl and "monthly_enabled: false" in decl, (
        "the client-side default must stay off; arming is the user's call"
    )
