"""Upgrade and restore leftovers also show as a Home card (Stephen's ruling 17,
north star §37.3-10): Home reads the same list as the Doctor page, shows each
item in Friday's own words as plain text, only when there is something to look
at, and sends the owner to the Doctor page to mark it sorted."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "static" / "friday_experience.js"


def _block() -> str:
    src = JS.read_text(encoding="utf-8")
    assert "/api/doctor/after-upgrade" in src, "Home does not read what an upgrade or restore left open"
    i = src.index("leftovers.length > 0 && h('section'")
    return src[i:src.index("h(HomeCards,", i)]


def test_home_shows_the_card_only_when_something_is_open():
    blk = _block()
    assert "After an upgrade or a restore" in blk
    src = JS.read_text(encoding="utf-8")
    assert "const [leftovers, setLeftovers] = useState([]);" in src
    assert re.search(r"setLeftovers\(items\.filter\(", src)


def test_items_are_plain_text_and_the_way_to_sort_them_is_the_doctor_page():
    blk = _block()
    assert "it.said" in blk and "it.todo" in blk
    assert "dangerouslySetInnerHTML" not in blk and "innerHTML" not in blk
    assert "href: '/doctor#afterupgrade'" in blk


def test_the_card_comes_before_the_other_home_cards():
    _block()
    src = JS.read_text(encoding="utf-8")
    day = src[src.index("function dayView()"):src.index("function formView()")]
    assert day.index("leftovers.length > 0") < day.index("h(HomeCards,")
