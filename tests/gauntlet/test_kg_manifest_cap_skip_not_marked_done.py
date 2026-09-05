"""Gauntlet finding Q23: F31's own newly-shipped MAX_CLOUD_EXTRACT_CALLS cap
can silently, permanently drop chunks from multi-chunk source files.

F31's fix_note claimed a cap-skipped chunk "stays stale in the manifest --
the next delta pass picks it back up", implying nothing is ever permanently
lost, only deferred. True for single-chunk sources; false for multi-chunk
ones.

KnowledgeGraphManifest.record() (store.py) keys by source_path and
OVERWRITES the whole entry per call rather than merging. In
reindex_tier_b's extraction loop, manifest.record(chunk['source_path'], ...)
was only called for a chunk's own successful extraction -- a cap-skipped
chunk (`skipped_cloud_cap += 1; continue`) never reached it. But if ANY
OTHER chunk from the SAME source file succeeded in the same pass (a wiki
page or SOUL.md over CHUNK_SIZE=1200 chars produces multiple chunks via
_chunk_text), that chunk's own record() call marked the ENTIRE FILE "up to
date" at its current on-disk fingerprint. The next delta pass's
manifest.delta() then classifies the file "unchanged" and excludes it from
`todo` entirely -- the cap-skipped chunk(s) are never retried, never counted
as a failure, and never visible anywhere, unless the file is edited again.

The fix tracks, per pass, which source_paths had at least one cap-skipped
chunk, and scrubs the manifest entry for those paths after the loop (via
manifest.forget()) so the file's fingerprint stays stale and the whole file
-- including its already-successful chunks -- is picked up again on the
next delta pass.

Self-contained per this codebase's established gauntlet-probe pattern (see
test_kg_indexer_pin_enforcement.py / test_kg_indexer_cloud_extraction_cap.py):
the RecordingLLM stub is duplicated locally rather than imported from
tests/unit/test_kg_indexer.py.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services.knowledge_graph import indexer
from agent_friday.services.knowledge_graph.store import (
    KnowledgeGraphManifest, KnowledgeGraphStore, canonical)

CANNED_EXTRACTION = (
    '("entity"<|>GRAPHRAG<|>concept<|>GraphRAG builds knowledge graphs from '
    'text)\n##\n<|COMPLETE|>')


class RecordingLLM:
    """Stub for the indexer's injectable llm(messages, system, sens, mode)."""

    def __init__(self):
        self.calls = []

    def __call__(self, messages, system, sensitivity, mode, orb_label=None):
        self.calls.append({"sensitivity": sensitivity, "mode": mode,
                           "text": messages[0]["content"]})
        if "-Goal-" in messages[0]["content"]:
            return CANNED_EXTRACTION
        return "merged description"


@pytest.fixture
def gated_cloud_settings(monkeypatch):
    """gated_cloud + an operational egress gate, so TIER_1 chunks are
    cloud-eligible and the cap can actually engage."""
    import agent_friday.services.egress_gate as eg
    import agent_friday.services.knowledge_graph as kg
    monkeypatch.setattr(kg, "_load_settings", lambda: {
        "knowledge_graph": {
            "indexing_mode": "gated_cloud",
            "index_sources": {"wiki": False, "soul": False,
                              "cognitive": False, "conversations": False},
        }})
    monkeypatch.setattr(eg, "gate_operational", lambda: True)


class TestCappedFileStaysStaleInTheManifest:
    """The core Q23 regression: one chunk of a multi-chunk file succeeds and
    calls manifest.record(); a sibling chunk of the SAME file is cap-skipped
    in the same pass. The file must not end up "unchanged" on the next
    delta pass."""

    def test_a_capped_files_successful_sibling_does_not_mark_it_done(
            self, gated_cloud_settings, tmp_path, monkeypatch):
        # Two chunks, same source file: chunk_0 gets through (cap=1), chunk_1
        # is skipped past the cap.
        fake_chunks = [
            {"id": "big_file_chunk_0", "source_path": "wiki/big.md",
             "text": "First half about GraphRAG.", "sensitivity": 1,
             "provenance": {"sensitivity": 1}},
            {"id": "big_file_chunk_1", "source_path": "wiki/big.md",
             "text": "Second half about GraphRAG.", "sensitivity": 1,
             "provenance": {"sensitivity": 1}},
        ]
        monkeypatch.setattr(indexer, "gather_chunks", lambda: fake_chunks)
        monkeypatch.setattr(indexer, "MAX_CLOUD_EXTRACT_CALLS", 1)

        # A real file backs "wiki/big.md" so store.py's fingerprint() (sha1
        # of on-disk bytes) has something to hash -- canonical()/fingerprint()
        # work off the real filesystem, not the fake chunk dicts.
        real_file = tmp_path / "big.md"
        real_file.write_text("First half about GraphRAG. Second half too.",
                             encoding="utf-8")
        for c in fake_chunks:
            c["source_path"] = str(real_file)

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="full", llm=llm)

        assert info["skipped_cloud_cap"] == 1
        assert info["extracted"] == 1

        # The manifest must NOT consider this file ingested: if it silently
        # got recorded via chunk_0's success, the next delta pass will
        # classify it "unchanged" and chunk_1's entities are gone for good.
        manifest = KnowledgeGraphManifest(base_dir=store.base)
        key = canonical(str(real_file))
        assert key not in manifest.sources, (
            "the file was marked ingested in the manifest even though one "
            "of its chunks was cap-skipped -- the next delta pass will call "
            "it 'unchanged' and that chunk's entities are lost until the "
            "file is edited again"
        )

    def test_the_capped_file_is_todo_again_on_the_next_delta_pass(
            self, gated_cloud_settings, tmp_path, monkeypatch):
        """Behavioral proof, not just a manifest-shape check: run delta mode
        TWICE with an unraised cap on pass 2 and confirm pass 2 actually
        re-extracts the chunk the cap skipped on pass 1."""
        real_file = tmp_path / "big.md"
        real_file.write_text("First half about GraphRAG. Second half too.",
                             encoding="utf-8")
        fake_chunks = [
            {"id": "big_file_chunk_0", "source_path": str(real_file),
             "text": "First half about GraphRAG.", "sensitivity": 1,
             "provenance": {"sensitivity": 1}},
            {"id": "big_file_chunk_1", "source_path": str(real_file),
             "text": "Second half about GraphRAG.", "sensitivity": 1,
             "provenance": {"sensitivity": 1}},
        ]
        monkeypatch.setattr(indexer, "gather_chunks", lambda: fake_chunks)

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")

        # Pass 1: cap of 1 -- chunk_1 is skipped.
        monkeypatch.setattr(indexer, "MAX_CLOUD_EXTRACT_CALLS", 1)
        llm1 = RecordingLLM()
        info1 = indexer.reindex_tier_b(store=store, mode="delta", llm=llm1)
        assert info1["skipped_cloud_cap"] == 1
        assert info1["extracted"] == 1

        # Pass 2: cap lifted, nothing on disk changed. If the manifest had
        # wrongly marked "big.md" up to date after pass 1, delta() would
        # call it unchanged and `todo` would be empty here -- chunk_1 would
        # never be attempted.
        monkeypatch.setattr(indexer, "MAX_CLOUD_EXTRACT_CALLS", 200)
        llm2 = RecordingLLM()
        info2 = indexer.reindex_tier_b(store=store, mode="delta", llm=llm2)

        assert info2["skipped_cloud_cap"] == 0
        assert info2["extracted"] == 2, (
            "pass 2 should re-extract BOTH of the capped file's chunks "
            "(the whole file was left stale, not just the one chunk the "
            "cap skipped) -- got %d" % info2["extracted"]
        )


class TestUncappedMultiChunkFileIsRecordedNormally:
    """No-op-shaped sanity check: when nothing is cap-skipped, a multi-chunk
    file's manifest entry is written as before -- this fix must not make
    every multi-chunk file permanently stale."""

    def test_no_cap_skip_means_the_file_is_recorded(
            self, gated_cloud_settings, tmp_path, monkeypatch):
        real_file = tmp_path / "small.md"
        real_file.write_text("All about GraphRAG in one go.", encoding="utf-8")
        fake_chunks = [
            {"id": "small_chunk_0", "source_path": str(real_file),
             "text": "All about GraphRAG.", "sensitivity": 1,
             "provenance": {"sensitivity": 1}},
            {"id": "small_chunk_1", "source_path": str(real_file),
             "text": "More GraphRAG.", "sensitivity": 1,
             "provenance": {"sensitivity": 1}},
        ]
        monkeypatch.setattr(indexer, "gather_chunks", lambda: fake_chunks)
        monkeypatch.setattr(indexer, "MAX_CLOUD_EXTRACT_CALLS", 200)

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="full", llm=llm)

        assert info["skipped_cloud_cap"] == 0
        manifest = KnowledgeGraphManifest(base_dir=store.base)
        assert canonical(str(real_file)) in manifest.sources


class TestLocalOnlyModeIsUnaffected:
    """No-op-shaped sanity check: local_only pins every chunk (pinned=True),
    so the cap -- which only counts cloud-eligible calls -- never engages,
    and this fix's bookkeeping is a no-op there too."""

    def test_local_only_never_withholds_manifest_records(
            self, tmp_path, monkeypatch):
        import agent_friday.services.knowledge_graph as kg
        monkeypatch.setattr(kg, "_load_settings", lambda: {
            "knowledge_graph": {
                "indexing_mode": "local_only",
                "index_sources": {"wiki": False, "soul": False,
                                  "cognitive": False, "conversations": False},
            }})
        monkeypatch.setattr(indexer, "MAX_CLOUD_EXTRACT_CALLS", 0)

        real_file = tmp_path / "local.md"
        real_file.write_text("Local-only content about GraphRAG.",
                             encoding="utf-8")
        fake_chunks = [{
            "id": "local_chunk_0", "source_path": str(real_file),
            "text": "Local-only content about GraphRAG.", "sensitivity": 1,
            "provenance": {"sensitivity": 1},
        }]
        monkeypatch.setattr(indexer, "gather_chunks", lambda: fake_chunks)

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="full", llm=llm)

        assert info["skipped_cloud_cap"] == 0
        manifest = KnowledgeGraphManifest(base_dir=store.base)
        assert canonical(str(real_file)) in manifest.sources
