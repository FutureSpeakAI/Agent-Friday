"""Supersession strips authority without destroying the record.

The constraint is not incidental. A stored turn is a record of something the
user or Friday actually said; deleting it destroys history and hides that a
correction ever happened. On 2026-09-09 the entry that needed neutralising was
Stephen repeating, in good faith, what a broken settings page had told him --
which is worth keeping precisely because it explains how the error travelled.
"""

import pytest

pytest.importorskip("chromadb")

from agent_friday.conversation_memory import ConversationMemory


@pytest.fixture
def mem(tmp_path):
    m = ConversationMemory(persist_dir=str(tmp_path / "mem"))
    if not m.available():
        pytest.skip("conversation memory backend unavailable")
    return m


def _seed(m):
    poisoned = m.index(
        "the settings menu shows that both google accounts are connected.",
        "user", session_id="2026-08-14", timestamp="2026-08-14T09:10:01")
    truthful = m.index(
        "Both accounts need re-authorization; tokens expired on 2026-09-01.",
        "friday", session_id="2026-09-09", timestamp="2026-09-09T20:56:03")
    return poisoned, truthful


class TestSupersedeNeverDeletes:

    def test_document_count_is_unchanged(self, mem):
        poisoned, _ = _seed(mem)
        before = mem._collection.count()
        mem.supersede([poisoned], reason="display was wrong")
        assert mem._collection.count() == before

    def test_history_still_returns_it_flagged_with_a_reason(self, mem):
        poisoned, _ = _seed(mem)
        mem.supersede([poisoned], reason="the settings page was wrong",
                      superseded_by="services.google_accounts.accounts_summary")
        turns = mem.get_session("2026-08-14", limit=50)
        hit = [t for t in turns if "settings menu shows" in (t.get("text") or "")]
        assert hit, "the record must survive in history"
        assert hit[0]["superseded"] is True
        assert "settings page was wrong" in hit[0]["superseded_reason"]

    def test_it_is_reversible(self, mem):
        poisoned, _ = _seed(mem)
        mem.supersede([poisoned], reason="x")
        assert not any(h["text"].startswith("the settings menu")
                       for h in mem.search("are my google accounts connected", n=8))
        mem.unsupersede([poisoned])
        assert any(h["text"].startswith("the settings menu")
                   for h in mem.search("are my google accounts connected", n=8))

    def test_a_reason_is_required(self, mem):
        poisoned, _ = _seed(mem)
        with pytest.raises(ValueError):
            mem.supersede([poisoned], reason="")


class TestSupersededEntriesLeaveTheRetrievalPath:

    def test_search_excludes_them_by_default(self, mem):
        poisoned, truthful = _seed(mem)
        mem.supersede([poisoned], reason="display was wrong")
        texts = [h["text"] for h in mem.search("google accounts connected", n=8)]
        assert not any("settings menu shows" in t for t in texts)

    def test_search_can_still_be_asked_for_them(self, mem):
        poisoned, _ = _seed(mem)
        mem.supersede([poisoned], reason="display was wrong")
        hits = mem.search("google accounts connected", n=8, include_superseded=True)
        found = [h for h in hits if "settings menu shows" in h["text"]]
        assert found and found[0]["superseded"] is True

    def test_unsuperseded_neighbours_are_untouched(self, mem):
        poisoned, truthful = _seed(mem)
        mem.supersede([poisoned], reason="display was wrong")
        texts = [h["text"] for h in mem.search("google accounts", n=8)]
        assert any("need re-authorization" in t for t in texts)

    def test_entries_written_before_the_flag_existed_still_retrieve(self, mem):
        """Pre-existing rows have no supersession key at all.

        Filtering in Chroma rather than Python would have made every memory
        written before this feature vanish -- a far worse bug than the one
        being fixed.
        """
        _seed(mem)
        assert mem.search("google accounts connected", n=5)
