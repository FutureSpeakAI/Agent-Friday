"""Text the voice model reads about the user never assumes their gender.

Friday ships to every user, so a note or tool contract that calls the user
"he" is wrong for most of them. The model is told "the user" or "they".
"""
import re

from agent_friday.services import voice_conversation_state as vcs
from agent_friday.services import voice_engine as ve

GENDERED = re.compile(r"\b(he|him|his)\b", re.I)


def test_the_conversation_note_names_no_gender():
    for depth in ("deep", "normal", "brief"):
        state = vcs.new_state()
        state.update({"depth": depth, "weights": {"budget": 2.0}, "open": ["the trip"]})
        note = vcs.render(state)
        assert not GENDERED.search(note), note


def test_the_voice_tool_contract_names_no_gender():
    for name, description, params, _required in ve._VOICE_LIVE_TOOLS:
        texts = [description] + [p[1] for p in (params or {}).values()]
        for text in texts:
            hit = GENDERED.search(text)
            assert not hit, f"{name}: {hit.group(0)!r} in {text!r}"
