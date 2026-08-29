"""Voice turns belong to the conversation the user is looking at.

Speaking is a way of typing into the open thread. `_persist_voice_turn` used to
call `conversations.resolve(None)` unconditionally, so EVERY voice turn filed
into Main no matter which conversation was on screen -- the last live mechanism
of the thread-collision bug Stephen reported (two conversations merging when he
hit the mic). Main stays the fallback for callers that genuinely have no open
thread, but it is no longer the destination for everyone.
"""
import json
import pytest


@pytest.fixture
def convs(tmp_path, monkeypatch):
    import agent_friday.core as core
    import agent_friday.services.conversations as c
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path, raising=False)
    monkeypatch.setattr(c, "FRIDAY_DIR", tmp_path, raising=False)
    return c


def _texts(convs, cid):
    return [m.get("text") for m in convs.messages(cid)]


def test_a_voice_turn_lands_in_the_open_thread_not_main(convs, monkeypatch):
    import agent_friday.services.voice_engine as ve
    monkeypatch.setattr(ve, "_load_settings", lambda: {}, raising=False)
    monkeypatch.setattr(ve, "_log_context", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "_save_chat_history", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "CHAT_HISTORY", [], raising=False)

    convs.ensure_main()
    other = convs.create(title="Not Main")

    ve._persist_voice_turn("said out loud", "answered out loud",
                           conversation_id=other["id"])

    assert "said out loud" in _texts(convs, other["id"])
    # The whole point: it did NOT also land in Main.
    assert "said out loud" not in _texts(convs, convs.MAIN_ID)


def test_no_open_thread_still_falls_back_to_main(convs, monkeypatch):
    """An explicit fallback for callers with nothing -- scheduler, channels."""
    import agent_friday.services.voice_engine as ve
    monkeypatch.setattr(ve, "_load_settings", lambda: {}, raising=False)
    monkeypatch.setattr(ve, "_log_context", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "_save_chat_history", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "CHAT_HISTORY", [], raising=False)

    convs.ensure_main()
    ve._persist_voice_turn("no thread open", "still answered", conversation_id=None)
    assert "no thread open" in _texts(convs, convs.MAIN_ID)


def test_an_unknown_thread_id_does_not_strand_the_turn(convs, monkeypatch):
    """A deleted/bogus id resolves to Main rather than losing the exchange."""
    import agent_friday.services.voice_engine as ve
    monkeypatch.setattr(ve, "_load_settings", lambda: {}, raising=False)
    monkeypatch.setattr(ve, "_log_context", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "_save_chat_history", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "CHAT_HISTORY", [], raising=False)

    convs.ensure_main()
    ve._persist_voice_turn("orphan", "answered", conversation_id="conv-deleted")
    assert "orphan" in _texts(convs, convs.MAIN_ID)


def test_the_mirror_row_carries_the_open_thread(convs, monkeypatch):
    """The flat mirror is stamped too, so /api/chat/history can filter it."""
    import agent_friday.services.voice_engine as ve
    rows = []
    monkeypatch.setattr(ve, "_load_settings", lambda: {}, raising=False)
    monkeypatch.setattr(ve, "_log_context", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "_save_chat_history", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "CHAT_HISTORY", rows, raising=False)

    convs.ensure_main()
    other = convs.create(title="Not Main")
    ve._persist_voice_turn("stamped", "answered", conversation_id=other["id"])

    assert rows and all(r.get("conversation_id") == other["id"] for r in rows)
    assert all(r.get("via") == "voice" for r in rows)
