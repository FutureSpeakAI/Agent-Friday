"""The posting screens say what actually happened.

Read from the shipped page (index.html), its JSX twin (ui_parts/app.html)
and Media (static/media_ws.js):

* the accounts card offers "Check connection", which posts nothing, and no
  longer claims "Test post sent" when nothing was sent;
* an OAuth platform that needs the owner's developer app shows a form for
  its details instead of "The connect flow is not available yet";
* the Connect-your-services cards no longer say "Approve access in the
  browser window that opened" when no window was opened;
* Quick Post's "Open full composer" opens the new post's card in Media
  (where the composer is), not the old Content workspace that redirects to
  Media's board;
* a held post's card in Media can be released, with the reason shown and an
  explicit yes, through the release route that requires ack;
* a post armed while something stops it going out shows the notice from the
  server with the button that fixes it, and Workflows shows why the Content
  publisher was switched on.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
MEDIA = (ROOT / "static" / "media_ws.js").read_text(encoding="utf-8")


def _fn(text, name, end_marker):
    """The source of one top-level function, from `function name(` to the
    next marker."""
    start = text.index("function " + name + "(")
    end = text.index(end_marker, start + 10)
    return text[start:end]


def test_no_screen_claims_a_test_post_was_sent():
    for label, text in (("index.html", INDEX), ("app.html", APP)):
        assert "Test post sent" not in text, label
        assert "Check connection" in text, label
    accounts = _fn(INDEX, "ContentAccountsTab", "\nfunction ")
    assert '"Test post"' not in accounts and "Check connection" in accounts


def test_an_oauth_platform_asks_for_its_developer_app():
    for label, text in (("index.html", INDEX), ("app.html", APP)):
        assert "The connect flow is not available yet" not in text, label
        assert "cp-oauth-app-form" in text, label
        assert "needs === 'client'" in text or "needs==='client'" in text, label


def test_connect_cards_do_not_claim_a_window_opened():
    for label, text in (("index.html", INDEX), ("app.html", APP)):
        assert "Approve access in the browser window that opened" not in text, label


def test_open_full_composer_opens_the_post_card_in_media():
    quick = _fn(INDEX, "QuickPostModal", "\nfunction QuickPostHost(")
    composer = quick[quick.index("mode === 'composer'"):quick.index("/compose'")]
    assert "workspace: 'content'" not in composer, composer
    assert "workspace: 'media'" in composer and "card: cardRef.current" in composer
    assert "cardRef.current = d.media_card" in quick


def test_a_held_post_can_be_released_from_its_media_card():
    assert "function releaseHeld(" in MEDIA
    release = MEDIA[MEDIA.index("function releaseHeld("):MEDIA.index("function moveCard(")]
    assert "/release'" in release and "ack: true" in release
    assert "confirm(" in release, "release must ask for an explicit yes"
    assert re.search(r"c\.kind === 'post' && c\.held \? h\('button'", MEDIA), \
        "no release button on a held post's card"


def test_armed_posts_show_what_stops_them_and_workflows_says_why_it_is_on():
    assert "function fridayPublisherNotice(" in INDEX
    assert INDEX.count("fridayPublisherNotice(d)") >= 4      # compose and Quick Post, now and schedule
    assert "publisherNotice(d)" in MEDIA
    assert "wf-routine-note" in INDEX and "wf-routine-note" in APP
