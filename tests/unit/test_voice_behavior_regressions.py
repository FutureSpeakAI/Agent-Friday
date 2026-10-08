"""Spoken budgets and phrase boundaries preserve requested detail."""


def test_explicit_spoken_budget_is_not_silently_reduced():
    from agent_friday.routes.voice import _voice_reply_cap
    assert _voice_reply_cap({"voice_max_tokens": 5000}) == 5000


def test_default_streaming_preserves_a_natural_sentence():
    from agent_friday.services.voice_session import ClauseChunker
    sentence = "The reason this matters becomes clear when we compare the two approaches, because each solves a different problem."
    chunker = ClauseChunker()
    assert chunker.feed(sentence + " Next") == [sentence]
    assert chunker.flush() == "Next"
