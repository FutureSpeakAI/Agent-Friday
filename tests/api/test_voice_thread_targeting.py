"""Voice turns belong to the conversation the user is looking at.

Speaking is a way of typing into the open thread. `_persist_voice_turn` used to
call `conversations.resolve(None)` unconditionally, so EVERY voice turn filed
into Main no matter which conversation was on screen -- the last live mechanism
of the thread-collision bug the maintainer reported (two conversations merging when he
hit the mic). Main stays the fallback for callers that genuinely have no open
thread, but it is no longer the destination for everyone.
"""
import json
import pytest


@pytest.fixture
def convs(tmp_path, monkeypatch):
    import agent_friday.core as core
    import agent_friday.services.conversations as c
    import agent_friday.services.voice_engine as ve
    from agent_friday.services import user_model
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path, raising=False)
    monkeypatch.setattr(c, "FRIDAY_DIR", tmp_path, raising=False)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    monkeypatch.setattr(ve, "_index_chat_turn", lambda *a, **k: None)
    monkeypatch.setattr(user_model, "observe_message", lambda *a, **k: None)
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


@pytest.mark.parametrize("provider", ["local", "google-gemini"])
def test_deleted_owner_does_not_redirect_a_late_voice_turn(convs, monkeypatch, provider):
    """Local and cloud callbacks retain the original id, even after deletion."""
    import agent_friday.services.voice_engine as ve
    from agent_friday.services import conversation_provenance, user_model
    import shutil
    effects = []
    monkeypatch.setattr(ve, "_load_settings", lambda: effects.append("settings") or {})
    monkeypatch.setattr(ve, "_log_context", lambda *a, **k: effects.append("log"))
    monkeypatch.setattr(ve, "_save_chat_history", lambda *a, **k: effects.append("mirror"))
    monkeypatch.setattr(ve, "_index_chat_turn", lambda *a, **k: effects.append("index"))
    monkeypatch.setattr(user_model, "observe_message", lambda *a, **k: effects.append("adapt"))
    monkeypatch.setattr(conversation_provenance, "turn_meta", lambda *a, **k: effects.append("provenance") or {})
    monkeypatch.setattr(ve, "CHAT_HISTORY", [], raising=False)

    convs.ensure_main()
    old = convs.create(title="Old call owner")["id"]
    with convs._LOCK:
        shutil.rmtree(convs._dir(old))
    ve._persist_voice_turn("old private words", "old private answer", conversation_id=old, provider=provider)
    assert not _texts(convs, convs.MAIN_ID)
    assert convs.load(old) is None
    assert not ve.CHAT_HISTORY and not effects


def test_owner_validation_holds_the_conversation_write_lock(convs, monkeypatch):
    """A delete cannot slip between the existence check and either turn row."""
    import threading
    import agent_friday.services.voice_engine as ve
    owner = convs.create(title="Call owner")["id"]
    entered, release = threading.Event(), threading.Event()
    failures = []
    validations = []
    original_load = convs.load
    def pause_during_validation(cid):
        result = original_load(cid)
        if cid == owner:
            validations.append(cid)
        if cid == owner and len(validations) == 2:
            entered.set()
            assert release.wait(2), "fixture must release owner validation"
        return result
    monkeypatch.setattr(convs, "load", pause_during_validation)
    monkeypatch.setattr(ve, "_load_settings", lambda: {})
    monkeypatch.setattr(ve, "_log_context", lambda *a, **k: None)
    monkeypatch.setattr(ve, "_save_chat_history", lambda *a, **k: None)
    monkeypatch.setattr(ve, "CHAT_HISTORY", [])
    def persist():
        try:
            ve._persist_voice_turn("Question", "Answer", owner, provider="local")
        except Exception as exc:
            failures.append(exc)
    worker = threading.Thread(target=persist)
    worker.start()
    try:
        assert entered.wait(2)
        lock_was_free = convs._LOCK.acquire(blocking=False)
        if lock_was_free:
            convs._LOCK.release()
        assert not lock_was_free
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and not failures
    assert _texts(convs, owner) == ["Question", "Answer"]


def test_owner_deleted_while_settings_resolve_has_no_transcript_side_effects(convs, monkeypatch):
    import shutil
    import agent_friday.services.voice_engine as ve
    from agent_friday.services import conversation_provenance, user_model
    owner = convs.create(title="Call owner")["id"]
    effects = []
    def resolve_settings():
        with convs._LOCK:
            shutil.rmtree(convs._dir(owner))
        return {}
    monkeypatch.setattr(ve, "_load_settings", resolve_settings)
    monkeypatch.setattr(ve, "_log_context", lambda *a, **k: effects.append("log"))
    monkeypatch.setattr(ve, "_save_chat_history", lambda *a, **k: effects.append("mirror"))
    monkeypatch.setattr(ve, "_index_chat_turn", lambda *a, **k: effects.append("index"))
    monkeypatch.setattr(user_model, "observe_message", lambda *a, **k: effects.append("adapt"))
    monkeypatch.setattr(conversation_provenance, "turn_meta", lambda *a, **k: effects.append("provenance") or {})
    monkeypatch.setattr(ve, "CHAT_HISTORY", [])
    assert ve._persist_voice_turn("Old words", "Old answer", owner, provider="local") is False
    assert not effects and not ve.CHAT_HISTORY and convs.load(owner) is None


def test_off_record_snapshot_keeps_voice_turn_in_memory_only(convs, monkeypatch, tmp_path):
    import agent_friday.core as core
    import agent_friday.services.voice_engine as ve
    from agent_friday.services import off_record
    owner = convs.create(title="Call owner")["id"]
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    settings = {"off_record": True, "off_record_stops_storage": True}
    monkeypatch.setattr(ve, "_load_settings", lambda: settings)
    monkeypatch.setattr(core, "CHAT_HISTORY_FILE", tmp_path / "chat_history.json")
    monkeypatch.setattr(ve, "_save_chat_history", core._save_chat_history)
    monkeypatch.setattr(ve, "CHAT_HISTORY", [])
    # The canonical append must use the trusted snapshot; a second settings
    # resolution under its store lock is both unnecessary and potentially slow.
    monkeypatch.setattr(off_record, "_settings", lambda: pytest.fail("unexpected settings resolution"))
    try:
        assert ve._persist_voice_turn("Unsaved words", "Unsaved answer", owner, provider="local") is True
        after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        assert before == after
        assert [m["text"] for m in off_record.recalled(owner)] == ["Unsaved words", "Unsaved answer"]
        assert all(row.get("off_record") for row in ve.CHAT_HISTORY)
    finally:
        with off_record._LOCK:
            off_record._MEMORY.pop(owner, None)


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
