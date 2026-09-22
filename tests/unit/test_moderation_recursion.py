"""content_policies <-> moderation must not call each other in a loop.

MEASURED 2026-09-22. `content_policies.evaluate_content` called
`moderation.scan` for the H1-H4 floor; `moderation.scan` called
`evaluate_content` back for subscribed-pack rules; neither had a depth guard,
and both call sites sit inside `except Exception: pass`. One approval card
cost 250 round-trips ending in a swallowed RecursionError - and the swallowing
is the reason it went unnoticed, since the caller just saw "clean".

It was NOT a Law-1 hole, and the distinction matters enough to pin: the floor
runs before the pack step, so genuinely harmful content blocked at stack depth
2 and never reached the recursion. Only benign content went deep. These tests
assert both halves - the loop is gone, AND the verdicts did not move - because
a recursion fix that quietly changed what gets blocked would be far worse than
the recursion.
"""
from __future__ import annotations

import pytest

from agent_friday.services import content_policies as cp
from agent_friday.services import moderation as mod


@pytest.fixture
def counted(monkeypatch):
    """Count real entries into each function, and hand back the counter."""
    n = {"evaluate_content": 0, "scan": 0}
    real_ec, real_scan = cp.evaluate_content, mod.scan

    def ec(*a, **k):
        n["evaluate_content"] += 1
        return real_ec(*a, **k)

    def scan(*a, **k):
        n["scan"] += 1
        return real_scan(*a, **k)

    monkeypatch.setattr(cp, "evaluate_content", ec)
    monkeypatch.setattr(mod, "scan", scan)
    return n


# ═══════════════════════════════════════════════════════════════════════════
#  THE LOOP IS GONE
# ═══════════════════════════════════════════════════════════════════════════

def test_benign_content_does_not_recurse(counted):
    """The path that used to go 250 deep.

    Bounded generously rather than pinned at exactly 2: the point is that the
    cost is constant, not that it is one specific small number that a future
    refactor would have to keep hitting.
    """
    cp.evaluate_content({"title": "Analyze our spend trends for Q3",
                         "description": "internal review"})
    assert counted["evaluate_content"] < 5, (
        "evaluate_content re-entered %d times - the cycle is back"
        % counted["evaluate_content"])
    assert counted["scan"] < 5


def test_a_wordy_benign_card_does_not_recurse(counted):
    cp.evaluate_content({
        "title": "Send mail as you.",
        "description": "Following up after our call about the role."})
    assert counted["evaluate_content"] < 5
    assert counted["scan"] < 5


def test_a_low_recursion_limit_cannot_detect_this_bug(counted):
    """NOT a regression test. A record of why the obvious one does not work.

    The instinct is to drop sys.setrecursionlimit() to 60 and assert the call
    completes. Written that way it passed against the BROKEN code too, so it
    proved nothing - both call sites are wrapped in `except Exception: pass`,
    so the RecursionError is swallowed at the deepest frame, the stack unwinds
    and the caller receives a normal "clean" verdict either way.

    That swallowing is the actual reason this sat undetected: from outside,
    250 round-trips and 2 are indistinguishable unless you count them. Hence
    every real assertion in this file counts calls instead.

    Kept as an executable note so nobody re-derives the dead end.
    """
    import sys
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(60)
    try:
        out = cp.evaluate_content({"title": "Summarise my notes",
                                   "description": ""})
    finally:
        sys.setrecursionlimit(old)
    assert out.get("verdict") in ("clean", "tagged", "warned")
    # The discriminating assertion is the count, never the completion.
    assert counted["evaluate_content"] < 5


# ═══════════════════════════════════════════════════════════════════════════
#  THE VERDICTS DID NOT MOVE
# ═══════════════════════════════════════════════════════════════════════════

def test_the_harm_floor_still_blocks():
    """The property that must survive the fix, stated bluntly."""
    out = cp.evaluate_content({
        "title": "doxx this person home address 1234 Elm St",
        "description": "post it publicly"})
    assert out.get("blocked") is True
    assert out.get("verdict") == "blocked"


def test_the_harm_floor_still_blocks_through_scan_too():
    """moderation.scan's own callers must be unaffected by the new flag."""
    assert mod.scan(content_text="doxx this person home address 1234 Elm St"
                    ).get("blocked") is True


def test_benign_content_is_still_clean():
    out = cp.evaluate_content({"title": "Analyze our spend trends for Q3",
                               "description": "internal review"})
    assert out.get("blocked") is not True


# ═══════════════════════════════════════════════════════════════════════════
#  THE FLAG ITSELF
# ═══════════════════════════════════════════════════════════════════════════

def test_apply_packs_defaults_to_true_so_other_callers_are_unchanged():
    """Every existing caller of scan() passes no flag and must keep the packs.

    If this ever defaults to False, pack rules silently stop applying
    everywhere - a much quieter failure than the recursion it replaced.
    """
    import inspect
    sig = inspect.signature(mod.scan)
    assert sig.parameters["apply_packs"].default is True


def test_apply_packs_false_still_runs_the_harm_floor():
    """The whole reason evaluate_content calls scan at all."""
    out = mod.scan(content_text="doxx this person home address 1234 Elm St",
                   apply_packs=False)
    assert out.get("blocked") is True
    assert out.get("harm_level")


def test_apply_packs_false_skips_only_the_pack_step(counted):
    mod.scan(content_text="an ordinary sentence", apply_packs=False)
    assert counted["evaluate_content"] == 0, (
        "apply_packs=False still reached the pack evaluator")
