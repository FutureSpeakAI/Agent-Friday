"""Discuss and the media diet on the page: a Discuss button on every story
card opens the panel; the panel asks the local route and labels her read with
her name; the Media diet shows rules, proposals and receipts. Both pages."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ["index.html", "ui_parts/app.html"]


@pytest.mark.parametrize("page", PAGES)
def test_every_story_card_can_be_discussed(page):
    s = (ROOT / page).read_text(encoding="utf-8")
    actions = s[s.index("cardActions"):]
    actions = actions[:actions.index("banSource")]
    assert "setDiscussFor" in actions and "setPanel('discuss')" in actions
    assert "DiscussPanel" in s and "panel === 'discuss'" in s.replace("panel==='discuss'", "panel === 'discuss'")


@pytest.mark.parametrize("page", PAGES)
def test_the_panel_asks_the_local_route_and_labels_her_read(page):
    s = (ROOT / page).read_text(encoding="utf-8")
    panel = s[s.index("function DiscussPanel"):s.index("const NEWS_PODCAST_ROUTINE = {")]
    assert "/api/news/discuss" in panel and "fridayName() + '\u2019s read'" in panel
    for mode in ("compare", "primary", "background", "claims", "local", "follow", "make"):
        assert "'%s'" % mode in panel, mode


@pytest.mark.parametrize("page", PAGES)
def test_the_media_diet_shows_rules_proposals_and_receipts(page):
    s = (ROOT / page).read_text(encoding="utf-8")
    comp = s[s.index("function MediaDietRules"):s.index("const DISCUSS_MODES")]
    assert "/api/news/media-diet" in comp and "/decide" in comp and "'approve'" in comp and "'deny'" in comp
    assert "MediaDietRules" in s[s.index("YOUR MEDIA DIET"):]
