"""Bring your AI history in (services/ai_history_import): ChatGPT and Claude exports, staged.

The exports here are synthetic, in the shapes the two services ship (LESSONS L11). Nothing
reaches the network or a model. On a tree without the module, each test fails at its fixture.
"""
from __future__ import annotations

import importlib
import io
import json
import zipfile

import pytest


def _module():
    try:
        return importlib.import_module("agent_friday.services.ai_history_import")
    except ImportError:
        pytest.fail("there is no AI-history import: services/ai_history_import does not exist")


def _chatgpt_export():
    return [{
        "title": "Council budget notes", "create_time": 1767225600.0, "update_time": 1767229200.0,
        "conversation_id": "c-1", "current_node": "n3",
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["n1"]},
            "n1": {"id": "n1", "parent": "root", "children": ["n2", "nx"], "message": {
                "author": {"role": "user"}, "create_time": 1767225600.0,
                "content": {"content_type": "text", "parts": ["What changed in the 2026 budget?"]}}},
            "nx": {"id": "nx", "parent": "n1", "children": [], "message": {
                "author": {"role": "assistant"}, "create_time": 1767225601.0,
                "content": {"content_type": "text", "parts": ["(an answer that was regenerated away)"]}}},
            "n2": {"id": "n2", "parent": "n1", "children": ["n3"], "message": {
                "author": {"role": "assistant"}, "create_time": 1767225610.0,
                "content": {"content_type": "text", "parts": ["The roads line rose by 4%."]}}},
            "n3": {"id": "n3", "parent": "n2", "children": [], "message": {
                "author": {"role": "system"}, "create_time": 1767225620.0,
                "content": {"content_type": "text", "parts": ["system note"]}}},
        }}, {
        "title": "Empty", "conversation_id": "c-2", "mapping": {}}]


def _claude_export():
    return [{"uuid": "k-1", "name": "Interview prep", "created_at": "2026-02-01T10:00:00Z",
             "updated_at": "2026-02-01T11:00:00Z", "chat_messages": [
                 {"uuid": "a", "sender": "human", "text": "Questions for the mayor?",
                  "created_at": "2026-02-01T10:00:00Z"},
                 {"uuid": "b", "sender": "assistant", "text": "",
                  "content": [{"type": "text", "text": "Ask about the depot."}],
                  "created_at": "2026-02-01T10:00:05Z"}]}]


def _bytes(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


@pytest.fixture
def ai(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    mod = _module()
    from agent_friday.services import conversations as cv
    monkeypatch.setattr(cv, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(cv, "_fts", lambda *a, **k: None)
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    from agent_friday.services import forget_person as fp
    monkeypatch.setattr(fp, "forgotten_names", lambda: set())
    monkeypatch.setattr(fp, "forgotten_emails", lambda: set())
    mod.cv = cv
    mod.home = tmp_path
    return mod


def _imported(ai):
    return [c for c in ai.cv.list_all() if c.get("imported")]


def test_a_chatgpt_export_previews_without_writing(ai):
    out = ai.preview(_bytes(_chatgpt_export()))
    assert out["ok"] and out["source"] == "chatgpt" and out["conversations"] == 1
    assert out["messages"] == 2                      # the shown branch only, no system turn
    assert out["first"] == "2026-01-01"
    assert not (ai.home / "wiki" / "_staging").exists()
    assert _imported(ai) == []


def test_staging_creates_no_conversation_until_accepted(ai):
    out = ai.stage(_bytes(_chatgpt_export()))
    assert out["ok"] and out["staged"] == 1 and out["waiting"] == 1
    assert _imported(ai) == [], "an imported conversation became durable before the owner answered"
    from agent_friday.services import import_staging as st
    item = st.list_items(source="chatgpt", status="open")[0]
    assert item["label"].startswith("Council budget notes · ChatGPT · 2026-01-01 · 2 messages")
    assert st.review(item["id"], "accept")["ok"]
    convs = _imported(ai)
    assert len(convs) == 1
    conv = convs[0]
    assert conv["title"] == "ChatGPT: Council budget notes"
    assert conv["imported"]["source"] == "chatgpt" and conv["imported"]["conversation_id"] == "c-1"
    msgs = ai.cv.messages(conv["id"])
    assert [(m["role"], m["text"]) for m in msgs] == [
        ("user", "What changed in the 2026 budget?"), ("assistant", "The roads line rose by 4%.")]
    assert all(m["imported_from"] == "chatgpt" for m in msgs)
    assert msgs[0]["ts"] == 1767225600.0


def test_a_claude_export_is_read_too(ai):
    out = ai.stage(_bytes(_claude_export()))
    assert out["ok"] and out["source"] == "claude" and out["staged"] == 1
    from agent_friday.services import import_staging as st
    st.review_class("claude", "accept")
    conv = _imported(ai)[0]
    assert conv["title"] == "Claude: Interview prep"
    assert [m["text"] for m in ai.cv.messages(conv["id"])] == ["Questions for the mayor?", "Ask about the depot."]


def test_importing_the_same_export_again_proposes_nothing_new(ai):
    ai.stage(_bytes(_chatgpt_export()))
    from agent_friday.services import import_staging as st
    st.review_class("chatgpt", "accept")
    again = ai.stage(_bytes(_chatgpt_export()))
    assert again["staged"] == 0 and again["already_decided"] == 1
    assert len(_imported(ai)) == 1


def test_the_export_zip_is_read(ai):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("chat.html", "<html></html>")
        z.writestr("conversations.json", json.dumps(_claude_export()))
    assert ai.preview(buf.getvalue())["conversations"] == 1


def test_a_zip_that_expands_too_far_is_refused(ai):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("conversations.json", "[" + " " * (5 * 1024 * 1024) + "]")
    out = ai.preview(buf.getvalue())
    assert out["ok"] is False and "too large" in out["error"]


@pytest.mark.parametrize("data", [b"", b"not json", _bytes({"a": 1}), _bytes([{"x": 1}]), b"PK\x03\x04junk"])
def test_a_file_that_is_not_an_export_is_refused_with_words(ai, data):
    out = ai.stage(data)
    assert out["ok"] is False and out["error"]
    assert _imported(ai) == []


def test_a_very_long_message_is_cut_and_says_so(ai):
    exp = _claude_export()
    exp[0]["chat_messages"][0]["text"] = "x" * (ai.MAX_CHARS + 50)
    convs = ai.conversations(exp, "claude")
    assert convs[0]["cut"] is True
    assert convs[0]["messages"][0]["text"].endswith("characters]")


def test_off_the_record_nothing_is_saved_and_the_item_keeps_waiting(ai, monkeypatch):
    ai.stage(_bytes(_claude_export()))
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    from agent_friday.services import import_staging as st
    item = st.list_items(source="claude", status="open")[0]
    assert st.review(item["id"], "accept")["ok"] is False
    assert _imported(ai) == []
    assert st.list_items(source="claude", status="open")[0]["id"] == item["id"]


def test_a_pasted_key_in_an_imported_message_is_withheld(ai):
    exp = _claude_export()
    exp[0]["chat_messages"][0]["text"] = "my key is sk-ant-api03-" + "A" * 40  # pragma: allowlist secret
    ai.stage(_bytes(exp))
    from agent_friday.services import import_staging as st
    st.review_class("claude", "accept")
    text = ai.cv.messages(_imported(ai)[0]["id"])[0]["text"]
    assert "AAAAAAAAAAAAAAAAAAAA" not in text
