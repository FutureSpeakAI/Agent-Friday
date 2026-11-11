"""Both copies of the UI keep the knowledge reader honest about a page it
could not load: only a 404 from Friday is reported as "moved or deleted".
A server that cannot be reached, a 5xx or an unreadable answer is reported
as Friday being unreachable, with a Try again button, and neither Edit nor
Delete is offered for a page that was never read.

The behaviour itself is exercised in the browser by
tests/app/specs/knowledge_reader_unreachable.spec.ts; this guards the two
files against drifting apart or quietly losing the distinction.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

UNREACHABLE = ("Friday could not be reached, so this page could not be loaded. "
               "It has not been moved or deleted.")
MISSING = "This page could not be read. It may have been moved or deleted."


@pytest.mark.parametrize("ui", ["index.html", "ui_parts/app.html"])
def test_the_reader_distinguishes_unreachable_from_missing(ui):
    text = (REPO / ui).read_text(encoding="utf-8")
    flat = re.sub(r"\s+", "", text)
    assert MISSING in text, f"{ui}: the missing-page notice is gone"
    assert UNREACHABLE in text, f"{ui}: no notice for a server that could not be reached"
    # A failed read becomes "missing" only after a 404; anything else is unreachable.
    assert "status===404?take(null):unreachable()" in flat, (
        f"{ui}: the path route's failure is not sorted by status")
    assert "status2===404?take(null):unreachable()" in flat, (
        f"{ui}: the two-level route's failure is not sorted by status")
    assert ".catch(()=>take(null))" not in flat, (
        f"{ui}: a network failure still reads as a missing page")
    # Nothing destructive, and no editor, for a page that was never read.
    assert "disabled:!doc||doc.missing||doc.unreachable" in flat, (
        f"{ui}: Delete is offered for a page Friday could not be reached for")
    assert "doc.locked||doc.missing||doc.unreachable" in flat, (
        f"{ui}: Edit is offered for a page Friday could not be reached for")
    # Try again re-runs the read.
    assert "onClick:()=>setRetry(n=>n+1)},'Tryagain')" in flat, f"{ui}: no Try again button"
    assert "[path,openOpts,retry]" in flat, f"{ui}: Try again does not re-run the page read"
