"""The Library is voice-callable: the three read tools are shared with voice, a
spoken result says the document and page rather than a token, and changing the
Library stays a card decided on screen."""
from __future__ import annotations

import pytest

from tests.library_fixtures import (install_fake_encoder, isolate_library, make_pdf, release_library,
                                    write_docs)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    yield
    release_library(fg, lstore)


def test_the_library_tools_are_shared_with_voice_and_resolve_from_the_registry():
    from agent_friday.services import voice_engine as ve
    for name in ("search_library", "library_show", "library_status"):
        assert name in ve._VOICE_SHARED_TOOLS
    names = {s[0] for s in ve._voice_shared_tool_specs()}
    assert {"search_library", "library_show", "library_status", "file_access"} <= names


def test_nothing_that_changes_the_library_is_a_voice_tool_of_its_own():
    from agent_friday.services import voice_engine as ve
    assert not [n for n in ve._VOICE_SHARED_TOOLS if n.startswith(("library_add", "library_remove", "library_forget"))]
    ids = {t[0] for t in ve._VOICE_LIVE_TOOLS}
    assert not [n for n in ids if n.startswith(("library_add", "library_remove", "library_forget"))]


# -- test_library_voice_answer_says_page_not_token ---------------------------------

def test_a_spoken_search_is_told_to_say_the_page_and_never_a_label(tmp_path, monkeypatch):
    from agent_friday.services import agent
    from agent_friday.services.library import grants, indexer, tools
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    write_docs(root, {"lease.pdf": make_pdf([["# Lease", "Either party may end the lease with ninety days notice."]])})
    grants.add_scope("owner", str(root))
    indexer.sweep_scope(store_for("owner"), root, allowed=lambda p: grants.allowed("owner", p))
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: "local")}))
    monkeypatch.setattr(agent, "_CURRENT_SURFACE", type("V", (), {"get": staticmethod(lambda: "voice-local")}))
    spoken = tools.search_library({"question": "how much notice to end the lease"})
    assert "You are speaking" in spoken and "never a label" in spoken
    monkeypatch.setattr(agent, "_CURRENT_SURFACE", type("V", (), {"get": staticmethod(lambda: "chat")}))
    typed = tools.search_library({"question": "how much notice to end the lease"})
    assert "You are speaking" not in typed


def test_a_cloud_voice_session_gets_no_library_text_by_default(tmp_path, monkeypatch):
    from agent_friday.services import agent
    from agent_friday.services.library import grants, indexer, tools
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    write_docs(root, {"lease.txt": "Either party may end the lease with ninety days notice."})
    grants.add_scope("owner", str(root))
    indexer.sweep_scope(store_for("owner"), root, allowed=lambda p: grants.allowed("owner", p))
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: None)}))   # a live cloud session sets none
    monkeypatch.setattr(agent, "_CURRENT_SURFACE", type("V", (), {"get": staticmethod(lambda: "voice-live")}))
    out = tools.search_library({"question": "how much notice to end the lease"})
    assert "ninety" not in out and "stay on this PC" in out


def test_spoken_text_has_no_token():
    from agent_friday.services.library import cite
    assert "lib:" not in cite.speakable("Page fourteen of the deposition says so [lib:3#44].")
