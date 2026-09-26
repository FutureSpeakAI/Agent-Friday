"""How the live voice session behaves in a real room.

Three failures from one live session with two people present: Friday answered
fragments of their conversation with each other and was cut off by them about
fifteen times; she answered one question in Italian; and asked about someone
the user knows, she recited a stock "that's in my Sovereign Vault" paragraph
although the tools that could answer were available to her.

The prompt is assembled inside the ws_live handler, so its composition is
checked at source level (the same approach as test_voice_live_tuning); the
pieces it is built from are checked directly.
"""
import inspect
import pathlib

from google.genai import types

import agent_friday.routes.voice as rv
import agent_friday.services.voice_engine as ve

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = inspect.getsource(rv)


def _padding(*args):
    return rv._build_realtime_input_config(types, *args).automatic_activity_detection.prefix_padding_ms


# ── crosstalk and barge-in ──────────────────────────────────────────────

def test_a_short_sound_does_not_start_a_turn():
    """Speech must be sustained before it counts: 200 ms let single words and
    fragments of other people's talk interrupt Friday."""
    assert _padding("auto") >= 400
    assert _padding("no-barge") >= 400


def test_room_mode_asks_for_longer_speech():
    assert _padding("auto", "room") > _padding("auto", "one") >= 400


def test_room_mode_is_a_setting_with_a_default():
    import agent_friday.core as core
    assert core.DEFAULT_SETTINGS["voice_room_mode"] == "one"
    assert rv._voice_room_mode({}) == "one"
    assert rv._voice_room_mode({"voice_room_mode": "room"}) == "room"
    assert rv._voice_room_mode({"voice_room_mode": "nonsense"}) == "one"


def test_room_mode_is_in_both_copies_of_the_settings_ui():
    served = (ROOT / "index.html").read_text(encoding="utf-8")
    mirror = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    assert "voice_room_mode: o.k" in served
    assert "voice_room_mode:o.k" in mirror


def test_addressed_to_friday():
    # Named: always.
    assert rv._addressed("Friday, what's the weather?", "", None)
    # Two people talking, nothing to do with her.
    assert not rv._addressed("did you feed the dog", "Here's the news.", 45.0)
    assert not rv._addressed("fax", "", None)
    # A reply to her question, or straight after she spoke.
    assert rv._addressed("yes please", "Want the next story?", 20.0)
    assert rv._addressed("and the second one", "That's the first story.", 4.0)


def test_the_bridge_keeps_unaddressed_replies_off_the_speakers():
    assert "if _barged_turn[0] or _quiet_turn[0]:" in SRC
    assert "_addressed(_heard, _last, _since)" in SRC
    # Only as a reply starts, never partway through one.
    assert "if _room and _reply_starting:" in SRC
    assert "_voice_room_mode(live_settings)" in SRC


def test_the_prompt_tells_her_to_ignore_crosstalk():
    assert "not addressed to you" in rv.VOICE_CROSSTALK_RULE
    assert "+ VOICE_CROSSTALK_RULE" in SRC


# ── language ────────────────────────────────────────────────────────────

def test_english_unless_another_language_is_chosen():
    assert rv._live_language("") == ("en-US", "English")
    assert rv._live_language(None) == ("en-US", "English")
    assert rv._live_language("fr-FR") == ("fr-FR", "French")


def test_the_language_is_always_pinned():
    assert 'speech_kwargs["language_code"] = live_language' in SRC
    assert "if live_language:\n" not in SRC.replace("\r\n", "\n")
    assert "VOICE_ENGLISH_RULE.format(language=live_language_name)" in SRC
    rule = rv.VOICE_ENGLISH_RULE.format(language="English")
    assert "Always speak English" in rule and "misheard" in rule


# ── personal questions ──────────────────────────────────────────────────

def test_personal_questions_go_to_her_own_knowledge():
    assert "set up a fully local voice mode" not in SRC
    assert "That information is in my Sovereign Vault" not in SRC.replace('"\n            "', "")
    # The rule depends on the real vault setting (test_voice_persona_and_news).
    rule = rv.vault_rule(True, True)
    assert "search_wiki" in rule and "ask_friday" in rule and "withheld" in rule
    assert "+ vault_rule(_vault_open, _mind_ready)" in SRC
    names = [t[0] for t in ve._VOICE_LIVE_TOOLS]
    assert "search_wiki" in names and "ask_friday" in names
