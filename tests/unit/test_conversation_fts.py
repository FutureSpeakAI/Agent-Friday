"""Full-text search over every conversation (services/conversation_fts).

Red on 3e9f92546: there was no index, no text-chat search tool, no per-message
delete, and voice search was a substring scan over every transcript.
"""
from __future__ import annotations

import pytest

from agent_friday.services import conversation_fts as fts
from agent_friday.services import conversation_recall as rc
from agent_friday.services import conversations as cv


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    yield


def _say(cid, role, text, **meta):
    return cv.append(cid, {"role": role, "text": text,
                           "meta": {"kind": "turn", "via": meta.pop("via", "chat"), **meta}})


def _wait_built():
    fts.ensure_built()          # a search waits for the one-time build


def test_a_turn_is_searchable_by_word_by_stem_and_by_part_of_a_word():
    c = cv.create("Trip")["id"]
    _say(c, "user", "We walked along the Thamesside path to Greenwich.")
    _wait_built()
    for q in ("walked", "walking", "Greenwich", "Thames"):
        hits = fts.search(q)
        assert hits and "Greenwich" in hits[0]["text"], q
        assert hits[0]["conversation_id"] == c and hits[0]["date"]


def test_a_pasted_key_never_reaches_the_index(tmp_path):
    c = cv.create("Setup")["id"]
    key = "sk-" + "ant-api03-" + "Q7v2Lm9Xc4Rt8Wp1Zy6Bn3Kd5Hf0Gs2Ja7Ue4Io9"  # pragma: allowlist secret
    _say(c, "user", "here is my key " + key + " for the setup")
    _wait_built()
    raw = b"".join(p.read_bytes() for p in (tmp_path / "conversations").glob("index.sqlite*"))
    assert key.encode() not in raw, "the pasted key was written into the search index"
    assert fts.search("Q7v2Lm9Xc4Rt8Wp1") == []
    assert fts.search("setup"), "the rest of the message is still searchable"


def test_nothing_is_indexed_when_the_redactor_is_unavailable(monkeypatch):
    c = cv.create("x")["id"]
    fts.ensure_built()                       # empty index, built before the append

    def gone():
        raise ImportError("the redactor could not be loaded")
    real = fts._redactor
    monkeypatch.setattr(fts, "_redactor", gone)
    _say(c, "user", "ordinary words about gardening")
    monkeypatch.setattr(fts, "_redactor", real)
    assert fts.search("gardening") == [], "the index took text it could not clean"


def test_off_the_record_turns_are_not_indexed():
    c = cv.create("x")["id"]
    _say(c, "user", "secret plans about the surprise party", off_record=True)
    _wait_built()
    assert fts.search("surprise party") == []


def test_the_index_builds_itself_from_existing_transcripts():
    c = cv.create("Old")["id"]
    p = cv._dir(c) / "messages.jsonl"
    p.write_text('{"id": "m1", "role": "user", "text": "the lighthouse keeper story", "ts": 1760000000, '
                 '"meta": {"kind": "turn"}}\n', encoding="utf-8")
    _wait_built()
    assert [h["message_id"] for h in fts.search("lighthouse")] == ["m1"]


def test_deleting_a_message_removes_it_everywhere_at_once(monkeypatch):
    from agent_friday.services import model_router
    from agent_friday import core
    deleted = []

    class Mem:
        def delete(self, ids=(), exact_text=None):
            deleted.append((tuple(ids), exact_text))
            return len(ids)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: Mem())
    c = cv.create("x")["id"]
    m = _say(c, "user", "my locker code is in the blue notebook")
    _say(c, "friday", "Got it, the blue notebook.")
    monkeypatch.setattr(core, "CHAT_HISTORY", [{"id": m["id"], "text": m["text"]}])
    monkeypatch.setattr(core, "_save_chat_history", lambda h: None)
    _wait_built()
    assert fts.search("locker")
    assert fts.delete_message(c, m["id"])["ok"]
    assert all(x.get("id") != m["id"] for x in cv.messages(c)), "still in the transcript"
    assert fts.search("locker") == [], "still in the search index"
    assert deleted and deleted[0][0] == (m["id"],), "vector memory was not told"
    assert core.CHAT_HISTORY == [], "still in the chat mirror"
    assert fts.search("notebook"), "the other message was kept"


def test_a_cleared_conversation_leaves_the_index_at_once():
    c = cv.create("x")["id"]
    _say(c, "user", "the quarterly tax estimate")
    _wait_built()
    assert fts.search("quarterly")
    cv.clear(c, include_pinned=True)
    assert fts.search("quarterly") == []


def test_voice_search_reads_the_index():
    c = cv.create("Call")["id"]
    _say(c, "user", "book the ferry to the island", via="voice", provider="google-gemini",
         sent_to=["google-gemini"])
    _wait_built()
    hits = rc.search("ferry island")
    assert hits and hits[0]["via"] == "voice" and hits[0]["sent_to"] == ["google-gemini"]


def test_the_text_tool_gives_a_cloud_model_only_what_it_already_saw(monkeypatch):
    from agent_friday.services import conversation_search_tool as cst
    c = cv.create("x")["id"]
    _say(c, "user", "the orchard budget is tight this year", sent_to=["anthropic"])
    _say(c, "user", "the orchard deed is in the safe", sent_to=[])
    _wait_built()
    monkeypatch.setattr(cst, "_loop_is_local", lambda: False)
    monkeypatch.setattr(cst, "_provider", lambda: "anthropic")
    out = cst._tool_search_past_conversations({"query": "orchard"})
    assert "budget" in out and "deed" not in out and "1 more match" in out
    monkeypatch.setattr(cst, "_loop_is_local", lambda: True)
    out = cst._tool_search_past_conversations({"query": "orchard"})
    assert "budget" in out and "deed" in out
