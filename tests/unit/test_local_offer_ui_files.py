"""The offer a local failure produces is rendered by BOTH UI files, and the voice
route is wired to speak it.

`index.html` is served; `ui_parts/app.html` is its hand-kept mirror
(AGENTS.md > UI). The chat response carries `local_fallback` (the offer); the page
must put its choices on the message, send nothing until one is clicked, and mark
nothing as chosen. The cloud choice re-sends the owner's own words with
`route_mode=cloud`, and `confirm_local_only` rides with it because the click is the
confirm local-only asks for.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = ("index.html", "ui_parts/app.html")


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", FILES)
class TestTheOfferIsRendered:
    def test_the_message_keeps_the_offer_and_the_words_to_resend(self, rel):
        src = _read(rel).replace(" ", "")
        assert "local_fallback:d.local_fallback||null" in src
        assert "source_text:m" in src

    def test_each_choice_is_a_button_from_the_server_and_none_is_preselected(self, rel):
        src = _read(rel)
        assert "m.local_fallback.choices" in src
        i = src.index("m.local_fallback.choices")
        window = src[i - 400:i + 700]
        assert "autoFocus" not in window and "defaultChecked" not in window
        assert "(recommended)" in src              # labelled, never preselected

    def test_the_cloud_choice_resends_with_route_mode_and_the_confirm(self, rel):
        src = _read(rel).replace(" ", "")
        assert "routeMode:'cloud'" in src and "confirmLocalOnly:true" in src
        assert "confirm_local_only=true" in src

    def test_the_card_is_single_use(self, rel):
        src = _read(rel).replace(" ", "")
        assert "local_fallback:null" in src

    def test_keep_it_local_only_dismisses(self, rel):
        assert "local_only_confirm" in _read(rel)


def test_the_two_files_agree_on_the_choice_handler():
    names = [rel for rel in FILES if "const chooseLocalFallback" in _read(rel)]
    assert names == list(FILES)


def test_the_voice_route_speaks_the_offer_and_takes_a_spoken_reply():
    src = _read("src/agent_friday/routes/voice.py")
    assert "from agent_friday.services import local_offer as _lo" in src
    assert "_lo.take_reply(" in src and "_lo.remember(" in src
    assert "spoken_offers" in src
    assert "LocalUnavailable" in src
    # The spoken cloud choice runs one turn, off the pinned seat, under the intent.
    assert 'intent_scope("user_explicit", source="voice_offer"' in src
    assert '"pin_to_seat": not cloud_once' in src
