"""Unit tests for services/user_model.py — local user modeling."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import user_model as um  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    yield


def test_observe_message_sets_formality_casual():
    for _ in range(6):
        um.observe_message("lol yeah that's awesome, thanks!", role="user")
    f = um.get_trait("comm.formality")
    assert f is not None
    assert f < 0.5


def test_observe_message_sets_formality_formal():
    for _ in range(6):
        um.observe_message(
            "Could you please review this? I would like to proceed. Regards.",
            role="user")
    f = um.get_trait("comm.formality")
    assert f is not None
    assert f > 0.5


def test_expertise_domain_grows_with_vocab():
    for _ in range(6):
        um.observe_message(
            "refactor the async function, fix the stack trace, then commit and deploy",
            role="user", workspace="code")
    exp = um.get_trait("expertise.code")
    assert exp is not None
    assert exp > 0.5


def test_novice_ask_lowers_expertise():
    for _ in range(6):
        um.observe_message("what is a function? can you explain in plain english?",
                           role="user")
    exp = um.get_trait("expertise.code")
    # novice phrasing pulls the code-expertise estimate down toward 0.2
    assert exp is None or exp < 0.5


def test_assistant_messages_are_ignored():
    r = um.observe_message("Here is your answer.", role="friday")
    assert r.get("skipped") is True


def test_note_fact_and_render():
    um.note_fact("preference", "I prefer dark mode", confidence=0.8)
    um.note_fact("bio", "My role is founder", confidence=0.9)
    prompt = um.render_user_model_prompt()
    assert "dark mode" in prompt
    assert "founder" in prompt


def test_note_fact_dedups():
    um.note_fact("preference", "I prefer dark mode")
    um.note_fact("preference", "I prefer dark mode")
    facts = um.profile()["facts"]
    assert sum(1 for f in facts if f["text"] == "I prefer dark mode") == 1


def test_observe_event_counters_and_profile():
    um.observe_event("workspace", "code")
    um.observe_event("workspace", "code")
    um.observe_event("workspace", "news")
    um.observe_event("tool", "search_web")
    prof = um.profile()
    assert "code" in prof["top_workspaces"]
    assert prof["top_workspaces"][0] == "code"


def test_forget_all():
    um.note_fact("preference", "keep this short")
    um.set_trait("comm.formality", 0.9)
    um.forget()
    assert um.profile()["facts"] == []
    assert um.get_trait("comm.formality") is None


def test_forget_by_category():
    um.note_fact("preference", "p1")
    um.note_fact("bio", "b1")
    um.forget(category="preference")
    cats = {f["category"] for f in um.profile()["facts"]}
    assert "preference" not in cats
    assert "bio" in cats


def test_render_empty_when_nothing_learned():
    assert um.render_user_model_prompt() == ""
