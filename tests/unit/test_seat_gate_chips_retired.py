"""The page draws no seat-gate chips and polls no seat-gate status.

The chips lived beside each model in the top-bar "Local Models" panel. That
panel was deleted because it wrote a key conversational dispatch never reads,
and the chips (and their "check" button) went with it. The model picker that
replaced it shows no gate state: nothing gates a seat binding, so a chip there
would suggest a check that decides nothing.

Both UI files hold this: index.html is served, and ui_parts/app.html is its
hand-maintained mirror, so dead code left in the mirror returns the moment the
page is regenerated from it -- here, a 15-second poll whose result nothing
renders. The seat-gate API itself stays; only the page stops calling it.
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI_FILES = pytest.mark.parametrize(
    "path",
    [ROOT / "index.html", ROOT / "ui_parts" / "app.html"],
    ids=["index.html", "app.html"],
)


@UI_FILES
def test_page_does_not_poll_seat_gate(path):
    src = path.read_text(encoding="utf-8")
    assert src.count("/api/seat-gate/") == 0


@UI_FILES
@pytest.mark.parametrize("name", ["gateChip", "seatGate", "SeatGate"])
def test_no_seat_gate_chip_code(path, name):
    src = path.read_text(encoding="utf-8")
    assert src.count(name) == 0
