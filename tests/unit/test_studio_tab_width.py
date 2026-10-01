"""Studio's Podcasts view fills whatever frame it is given.

The tab shell (`.ws-tab-body`) is full-bleed: the workspace body is a flex
column with no content cap, and the unified shell keeps it that way. A
workspace that wants a readable measure puts it on its text, never on its
root, or the whole view hugs the left edge of a wide tab and looks trapped.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START = "// ═══ PODCASTS — player, Studio view, News chip"
END = "const NEWS_PODCAST_ROUTINE = {"
FILES = ("index.html", "ui_parts/app.html")


def _podcasts_block(rel: str) -> str:
    s = (ROOT / rel).read_text(encoding="utf-8")
    assert s.count(START) == 1, rel
    return s[s.index(START):s.index(END)]


def _podcasts_view_root(block: str) -> str:
    """The style object of PodcastsView's outermost element."""
    m = re.search(
        r"function PodcastsView\(\)(?P<body>.*?)\n\}\n", block, re.S
    )
    assert m, "PodcastsView is defined in the podcast block"
    root = re.search(
        r"return h\('div', \{ style: \{(?P<style>[^}]*)\} \}", m.group("body")
    )
    assert root, "PodcastsView returns a styled div"
    return root.group("style")


def test_the_podcasts_view_root_has_no_width_cap():
    for rel in FILES:
        style = _podcasts_view_root(_podcasts_block(rel))
        assert "maxWidth" not in style, f"{rel}: PodcastsView root is capped"
        assert "width" not in style.lower(), f"{rel}: PodcastsView root fixes its width"


def test_the_intro_keeps_a_readable_measure_on_the_text_itself():
    for rel in FILES:
        block = _podcasts_block(rel)
        assert re.search(r"marginTop: 4, maxWidth: '\d+ch'", block), rel
