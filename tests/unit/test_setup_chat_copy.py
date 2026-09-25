"""The setup chat's words hold the promises the flow makes."""
from __future__ import annotations

from agent_friday.services import setup_chat_copy as copy
from agent_friday.services import setup_research


def test_eight_to_twelve_questions_ending_on_the_classic_one():
    assert 8 <= len(copy.QUESTIONS) <= 12
    qid, text, chips, _ph = copy.QUESTIONS[-1]
    assert qid == "mother"
    assert text == "What was the relationship with your mother like?"
    assert copy.MOTHER_QUESTION == text


def test_the_questions_cover_what_a_style_needs():
    ids = [q[0] for q in copy.QUESTIONS]
    for needed in ("address", "humor", "directness", "challenge", "detail",
                   "rhythm", "values", "want", "peeves"):
        assert needed in ids
    assert len(ids) == len(set(ids))


def test_the_mother_question_is_introduced_lightly_and_as_optional():
    lead = copy.MOTHER_LEAD_IN.lower()
    assert "freud" in lead and "optional" in lead and "rather not say" in lead


def test_skipping_is_offered_in_words_the_chat_understands():
    for w in ("skip", "rather not say"):
        assert w in copy.SKIP_WORDS


def test_the_research_promise_names_every_category_the_code_refuses():
    text = copy.RESEARCH_ASK.lower()
    for word in ("health", "sexuality", "finances", "family", "home address"):
        assert word in text, word
    for cat in setup_research.SENSITIVE_CATEGORIES:
        assert cat.replace("_", " ") in text or cat == "finances" and "finances" in text


def test_the_research_promise_says_it_is_seed_only_and_reviewed():
    text = copy.RESEARCH_ASK.lower()
    assert "only the name, handles, websites and employer" in text
    assert "untrusted" in text and "approved it line by line" in text


def test_the_connect_copy_never_asks_for_a_password_in_chat():
    assert "never ask for a password here" in copy.CONNECT


def test_every_stage_belongs_to_one_progress_group():
    grouped = [s for _g, _l, stages in copy.STAGE_GROUPS for s in stages]
    assert sorted(grouped) == sorted(copy.STAGES)
