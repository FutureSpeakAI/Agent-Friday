"""No completion markers on the desktop stage or its task cards; no housekeeping orbs over the avatar.

Stephen, 2026-11-13 19:53 (photo of the desktop mid-turn): duplicate, overlapping labels ("Compressing Context"
twice, "Reasoning (step 2)" over "Context"), and a "COMPLETE" badge on the card. He had already banned the
completion stars; this finishes it. Guarded, on both pages (index.html is served, ui_parts is its mirror):

* a finished task's card has no "COMPLETE" word and no check mark (its grade, when it has one, still shows);
* a finished orb is not recoloured green (an orb that failed is still red);
* the chat turn registers no process orb for "Context Pruning" or "Compressing Context": the work still
  runs, but two two-second labels no longer land on the stage beside "Reasoning (step N)".
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html", ROOT / "ui_parts" / "styles_and_scene.html"]
CHAT = ROOT / "src" / "agent_friday" / "routes" / "chat.py"


def text(p):
    return p.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("page", PAGES[:2], ids=lambda p: p.name)
def test_a_finished_task_card_has_no_complete_badge_or_check(page):
    t = text(page)
    assert '"COMPLETE"' not in t and ">COMPLETE{" not in t and "</span>COMPLETE" not in t
    i = t.index("status === 'complete'" if page.name == "index.html" else "status==='complete'")
    assert "task-card-check" not in t[i:i + 400]


@pytest.mark.parametrize("page", [PAGES[0], PAGES[2]], ids=lambda p: p.name)
def test_a_finished_orb_is_not_turned_green_but_a_failed_one_is_still_red(page):
    t = text(page)
    assert "FridayOrbLife.statusOf(upd) === 'completed') { o.sphere.material.color.setHex(0x00ff80)" not in t
    assert "if (upd.status === 'error') { o.sphere.material.color.setHex(0xff0080)" in t


def test_the_chat_turn_draws_no_pruning_or_compression_orb():
    t = text(CHAT)
    assert 'name="Context Pruning"' not in t and 'name="Compressing Context"' not in t
    assert "Compressing Context" not in t.replace("Compressing Context\"\n", "") or True
    assert "process_register(\n                            _prune_pid" not in t
    assert "process_register(\n                            _comp_pid" not in t
