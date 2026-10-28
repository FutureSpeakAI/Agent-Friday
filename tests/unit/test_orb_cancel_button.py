"""A Cancel button on a task orb (Stephen's ruling 15, 2026-10-28; WCAG 2.2 2.5.7; north star NS-33.1-14).

A running task's orb could be cancelled only by throwing it away from Friday
(a drag). Opening an orb shows its process panel; that panel now has a Cancel
button for a task that is still running. It does exactly what the throw does
(FridayOrbHands.act('cancel'): the same undo window and note), and says
"Cancelling…" while the undo window is open.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_process_panel_has_a_cancel_button_that_does_what_the_throw_does(rel):
    s = (ROOT / rel).read_text(encoding="utf-8")
    panel = s[s.index("function ProcessOrbThread("):]
    panel = panel[:panel.index("\n}\n")]
    assert "running&&typeof FridayOrbHands!=='undefined'&&rtEl('button',{type:'button',className:'thread-cancel'" in panel
    assert "'aria-label':cancelling?'Cancelling this task; undo it from the note':'Cancel this task'" in panel
    assert "FridayOrbHands.act('cancel',[orb.id],'button')" in panel
    assert "onMouseDown:e=>e.stopPropagation(),onClick:cancelTask" in panel, "pressing it never starts a panel drag"
    assert "minHeight:24,minWidth:24" in panel


def test_the_throw_and_the_button_share_one_cancel():
    s = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "else if (op === 'cancel') r = cancel(o);" in s
    assert "note('undo:' + o.id, Name(o) + ' will stop.', { undo: () => undo(o), ms: UNDO_MS });" in s