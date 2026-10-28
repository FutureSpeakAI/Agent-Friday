"""Memory that cannot be read is named, never mistaken for 'nothing relevant' (NS-32.5-1).

services/recall_health and the stores that report to it. Nothing here breaks a real store: each
read is made to fail in place. On a tree without the module each test fails at the fixture.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def rh():
    try:
        mod = importlib.import_module("agent_friday.services.recall_health")
    except ImportError:
        pytest.fail("a memory store that cannot be read looks empty: services/recall_health does not exist")
    mod._reset()
    yield mod
    mod._reset()


def test_nothing_failed_means_no_note(rh):
    assert rh.degraded() == [] and rh.context_note() == ""
    assert rh.status()["state"] == "ok"


def test_a_failure_is_named_and_a_later_read_clears_it(rh):
    rh.note_failure("past_conversations", OSError(5, "I/O error"), now=1000.0)
    assert rh.degraded(now=1001.0) == ["past_conversations"]
    note = rh.context_note(now=1001.0)
    assert "RECALL DEGRADED" in note and "past conversations could not be read" in note
    assert "Do not present anything as remembered" in note
    rh.note_ok("past_conversations")
    assert rh.context_note(now=1002.0) == ""


def test_a_failure_lapses_after_the_window(rh):
    rh.note_failure("memory_card", now=1000.0)
    assert rh.degraded(now=1000.0 + rh.WINDOW_S - 1) == ["memory_card"]
    assert rh.degraded(now=1000.0 + rh.WINDOW_S + 1) == []


def test_an_unknown_source_is_ignored(rh):
    rh.note_failure("somewhere_else")
    assert rh.degraded() == []


def test_the_failure_kind_comes_from_the_shared_classifier(rh):
    rh.note_failure("knowledge_graph", OSError(28, "No space left on device"))
    assert rh.status()["kinds"] == ["disk_full"]


def test_past_conversations_that_will_not_open_are_degraded_not_empty(rh, monkeypatch):
    from agent_friday.services import model_router as mr

    class Broken:
        _init_error = RuntimeError("chroma could not open its store")

        def available(self):
            return False
    monkeypatch.setattr(mr, "_get_conversation_memory", lambda: Broken())
    monkeypatch.setattr(mr, "_load_settings", lambda: {"memory_recall_enabled": True})
    mr._build_memory_context_block("what did we decide about the depot?", "s1")
    assert rh.degraded() == ["past_conversations"]


def test_recall_switched_off_or_never_installed_is_not_degraded(rh, monkeypatch):
    from agent_friday.services import model_router as mr

    class Absent:
        _init_error = None

        def available(self):
            return False
    monkeypatch.setattr(mr, "_get_conversation_memory", lambda: Absent())
    monkeypatch.setattr(mr, "_load_settings", lambda: {"memory_recall_enabled": True})
    mr._build_memory_context_block("hello", "s1")
    assert rh.degraded() == []


def test_recall_not_installed_is_not_degraded(rh, monkeypatch):
    from agent_friday.services import model_router as mr

    class NotInstalled:
        _init_error = ModuleNotFoundError("No module named 'chromadb'")

        def available(self):
            return False
    monkeypatch.setattr(mr, "_get_conversation_memory", lambda: NotInstalled())
    monkeypatch.setattr(mr, "_load_settings", lambda: {"memory_recall_enabled": True})
    mr._build_memory_context_block("hello", "s1")
    assert rh.degraded() == []


def test_a_card_that_reads_again_clears_its_failure(rh, monkeypatch, tmp_path):
    from agent_friday.services import core_memory as cm
    rh.note_failure("memory_card")
    card = tmp_path / "user.md"
    card.write_text("- the owner lives in Leeds\n", encoding="utf-8")
    monkeypatch.setattr(cm, "_path", lambda target: card)
    cm.entries("user")
    assert rh.degraded() == []


def test_user_facts_read_again_clear_their_failure(rh, monkeypatch):
    from agent_friday.services import user_model as um
    rh.note_failure("user_facts")
    monkeypatch.setattr(um, "_enabled", lambda: True)
    monkeypatch.setattr(um, "get_trait", lambda key: None)
    monkeypatch.setattr(um, "_all_traits", lambda: {})
    monkeypatch.setattr(um, "_top_counters", lambda *a, **k: [])
    monkeypatch.setattr(um, "_recent_facts", lambda n: [])
    um.render_user_model_prompt()
    assert rh.degraded() == []


def test_a_search_that_raises_is_degraded(rh, monkeypatch):
    from agent_friday.services import model_router as mr

    class Raising:
        _init_error = None

        def available(self):
            return True

        def search(self, *a, **k):
            raise OSError(5, "I/O error")
    monkeypatch.setattr(mr, "_get_conversation_memory", lambda: Raising())
    monkeypatch.setattr(mr, "_load_settings", lambda: {"memory_recall_enabled": True})
    assert mr._build_memory_context_block("the depot", "s1") == ""
    assert rh.degraded() == ["past_conversations"]


def test_an_unreadable_memory_card_is_degraded_and_a_missing_one_is_not(rh, monkeypatch, tmp_path):
    from agent_friday.services import core_memory as cm
    monkeypatch.setattr(cm, "_path", lambda target: tmp_path / "missing.md")
    assert cm.entries("user") == [] and rh.degraded() == []
    unreadable = tmp_path / "card_is_a_folder"
    unreadable.mkdir()
    monkeypatch.setattr(cm, "_path", lambda target: unreadable)
    assert cm.entries("user") == []
    assert rh.degraded() == ["memory_card"]


def test_user_facts_that_cannot_be_read_are_degraded(rh, monkeypatch):
    from agent_friday.services import user_model as um

    def boom(*a, **k):
        raise OSError(5, "I/O error")
    monkeypatch.setattr(um, "_enabled", lambda: True)
    monkeypatch.setattr(um, "get_trait", boom)
    assert um.render_user_model_prompt() == ""
    assert "user_facts" in rh.degraded()


def test_the_search_tool_says_it_could_not_search_instead_of_nothing_matches(rh, monkeypatch):
    from agent_friday.services import conversation_recall as rc
    from agent_friday.services import conversation_search_tool as tool
    import sqlite3

    def broken(*a, **k):
        raise sqlite3.DatabaseError("database disk image is malformed")
    monkeypatch.setattr(rc, "search", broken)
    out = tool._tool_search_conversations_typed({"query": "depot"})
    assert "could not be searched" in out and "Nothing in earlier conversations" not in out
    assert rh.degraded() == ["conversation_search"]
    assert rh.status()["kinds"] == ["index_corruption"]


def test_the_doctor_names_the_sources(rh):
    from agent_friday.services import doctor
    assert doctor._c_recall() == ("healthy", "ok")
    rh.note_failure("past_conversations")
    rh.note_failure("knowledge_graph")
    assert doctor._c_recall() == ("degraded", "recall_degraded:past_conversations,knowledge_graph")
