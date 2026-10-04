"""Voice keeps a running picture of the conversation and sizes answers to it.

Replayed transcripts: a detail-seeking exchange moves the state to depth and
keeps it there; a quick back-and-forth never does; a brevity cue sticks until
he asks for more; priorities shift as the topic moves and a topic he keeps
returning to rises; a question no reply covered stays open. The model is
shown the state each turn it changes, and the prompt forbids the reflexive
"one, two or three things" list.
"""
import inspect

import agent_friday.routes.voice as rv
from agent_friday.services import voice_conversation_state as vcs
from agent_friday.services import voice_persona as vp


def _replay(turns):
    s = vcs.new_state()
    seen = []
    for user, agent in turns:
        vcs.update(s, user, agent)
        seen.append(s["depth"])
    return s, seen


DETAIL = [
    ("What's going on with the voter database story?",
     "The court let the administration use an overhauled federal database."),
    ("Why does that matter for the election?",
     "Because flagged voters can be challenged before they cast a ballot."),
    ("How would that actually work at the county level?",
     "County registrars receive the flags and decide whether to investigate."),
    ("Tell me more about how the database decides who to flag.",
     "It matches records across agencies, and mismatches trigger a flag."),
]

QUICK = [
    ("What time is it?", "It's ten past four."),
    ("Okay thanks.", "Anytime."),
    ("Is it raining?", "No, it's dry this afternoon."),
    ("Cool.", "Mm-hm."),
]


def test_a_detail_seeking_exchange_asks_for_depth_and_keeps_it():
    s, seen = _replay(DETAIL)
    assert seen[1:] == ["deep", "deep", "deep"], seen
    note = vcs.render(s)
    assert "connected paragraphs" in note and "no list" in note
    assert {"voter", "database"} & set(vcs.priorities(s))


def test_a_quick_back_and_forth_stays_brief():
    s, seen = _replay(QUICK)
    assert "deep" not in seen, seen
    assert "a sentence or two" in vcs.render(s) or "a few spoken sentences" in vcs.render(s)


def test_a_brevity_cue_sticks_until_he_asks_for_more():
    s, seen = _replay(DETAIL[:2] + [
        ("Keep it short, what's the bottom line?", "Some voters may be challenged."),
        ("And the database again?", "It flags mismatched records."),
        ("Okay, tell me more about that.", "It compares records across agencies."),
    ])
    assert seen[2] == "brief"
    assert seen[3] == "brief", "a brevity cue holds until he asks for more"
    assert seen[4] == "deep"


def test_priorities_shift_as_the_conversation_moves():
    s = vcs.new_state()
    for u in ("Tell me about the marathon training plan.",
              "How long should the marathon long runs be?",
              "What pace for marathon tempo runs?"):
        vcs.update(s, u, "")
    assert vcs.priorities(s)[0] == "marathon"
    for u in ("Different topic: the garden beds need compost.",
              "Which compost works for tomatoes in garden beds?",
              "When do I add compost to the garden?"):
        vcs.update(s, u, "")
    assert vcs.priorities(s)[0] in ("compost", "garden"), vcs.priorities(s)
    assert "marathon" not in vcs.priorities(s)[:1]


def test_a_topic_he_keeps_returning_to_asks_for_depth():
    s = vcs.new_state()
    for u in ("The budget thing again.", "Okay, cool.", "Back to the budget.",
              "Hmm.", "Let's do the budget."):
        vcs.update(s, u, "Sure.")
    assert s["depth"] == "deep"


def test_an_unanswered_question_stays_open_until_covered():
    s = vcs.new_state()
    vcs.update(s, "When is the dentist appointment?", "Let me check that in a moment.")
    assert s["open"] and "dentist" in s["open"][0]
    vcs.update(s, "Okay.", "Your dentist appointment is Thursday at nine.")
    assert s["open"] == []


def test_the_model_can_refine_the_picture():
    s = vcs.merge_model_note(vcs.new_state(), {"depth": "deep",
                                               "priorities": ["mortgage refinance"],
                                               "open_threads": ["compare the two lenders"]})
    assert s["depth"] == "deep" and "mortgage" in vcs.priorities(s)
    assert s["open"] == ["compare the two lenders"]


def test_the_prompt_forbids_the_reflexive_list_and_the_bridge_shows_the_state():
    rule = vp.VOICE_LENGTH_RULE
    assert "Do NOT default to 'one, two or three things'" in rule
    assert "connected paragraphs" in rule and "note_conversation_state" in vp.VOICE_STATE_NOTE_RULE
    for cue in ("tell me more", "keep it short", "news", "explanation", "story"):
        assert cue in rule
    src = inspect.getsource(rv).replace("\r\n", "\n")
    assert "_vcs.update(" in src and "_vcs.render(_voice_session[\"conv_state\"])" in src
