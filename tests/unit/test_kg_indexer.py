"""Phase 2a — Tier B indexer: parsing, merging, routing, incrementality.

All LLM traffic is a recorded stub — nothing leaves the process.
"""

import json
from pathlib import Path

import pytest

from agent_friday.services.knowledge_graph import indexer
from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore


CANNED_EXTRACTION = (
    '("entity"<|>GRAPHRAG<|>concept<|>GraphRAG builds knowledge graphs from text)\n##\n'
    '("entity"<|>FRIDAY<|>tool<|>Friday is the assistant that hosts the graph)\n##\n'
    '("relationship"<|>GRAPHRAG<|>FRIDAY<|>Friday uses GraphRAG for its second brain<|>8)\n'
    '<|COMPLETE|>')

CANNED_REPORT = json.dumps({
    "title": "Knowledge tooling",
    "summary": "A cluster about Friday's knowledge machinery.",
    "rating": 7.5,
    "rating_explanation": "central to the product",
    "findings": [{"summary": "GraphRAG is core",
                  "explanation": "it powers retrieval"}],
})


class RecordingLLM:
    """Stub for the indexer's injectable llm(messages, system, sens, mode)."""

    def __init__(self):
        self.calls = []

    def __call__(self, messages, system, sensitivity, mode, orb_label=None):
        self.calls.append({"sensitivity": sensitivity, "mode": mode,
                           "text": messages[0]["content"],
                           "system": system})
        if "-Goal-" in messages[0]["content"]:
            return CANNED_EXTRACTION
        if "community" in (messages[0]["content"] or "").lower():
            return CANNED_REPORT
        return "merged description"


@pytest.fixture
def wiki_home(tmp_path, monkeypatch):
    """Point the indexer at a tiny, exclusive wiki.

    Uses the import-time WIKI_DIR constant (tests/test_egress_adversarial.py
    leaks a Path.home() redirect session-wide) and wipes it first — these
    tests assert exact entity counts, so leftover pages from earlier tests
    would skew them.
    """
    import shutil
    from agent_friday.core import WIKI_DIR
    wiki = WIKI_DIR
    if wiki.exists():
        shutil.rmtree(wiki)
    (wiki / "research").mkdir(parents=True, exist_ok=True)
    (wiki / "research" / "notes.md").write_text(
        "# Notes\n\nGraphRAG and Friday work together.\n", encoding="utf-8")
    # keep the corpus to wiki-only for determinism
    import agent_friday.services.knowledge_graph as kg
    monkeypatch.setattr(kg, "_load_settings", lambda: {
        "knowledge_graph": {"index_sources": {
            "wiki": True, "soul": False, "cognitive": False,
            "conversations": False}}})
    return wiki


class TestParsing:
    def test_parse_extraction_tuples(self):
        ents, rels = indexer.parse_extraction(CANNED_EXTRACTION)
        assert {e["title"] for e in ents} == {"GRAPHRAG", "FRIDAY"}
        assert rels[0]["source"] == "GRAPHRAG"
        assert rels[0]["weight"] == 0.8

    def test_parse_garbage_is_empty(self):
        assert indexer.parse_extraction("no tuples here") == ([], [])
        assert indexer.parse_extraction("") == ([], [])

    def test_parse_report(self):
        rep = indexer._parse_report("```json\n" + CANNED_REPORT + "\n```")
        assert rep["title"] == "Knowledge tooling"
        assert "GraphRAG is core" in rep["full_content"]


class TestModelResolution:
    """2026-09-03: indexing_mode is a strict PER-USER choice, not a per-tier
    override. The old `gated_cloud` mode pinned TIER_2/3 chunks local no
    matter what the user picked; "cloud" now routes every sensitivity to the
    gated cloud default uniformly, and "local" pins every sensitivity to
    whatever installed model can actually run extraction -- what content is
    safe to send is the egress gate's decision, not this function's."""

    def test_local_mode_pins_the_available_model_at_every_sensitivity(
            self, monkeypatch):
        monkeypatch.setattr(indexer, "_available_local_model",
                            lambda: "gemma4:e2b")
        for sens in (1, 2, 3):
            model, pinned = indexer._resolve_model(sens, "local")
            assert pinned is True and model == "gemma4:e2b"

    def test_local_mode_raises_when_nothing_installed_can_run_it(
            self, monkeypatch):
        monkeypatch.setattr(indexer, "_available_local_model", lambda: None)
        with pytest.raises(indexer.LocalIndexingUnavailable):
            indexer._resolve_model(2, "local")

    def test_cloud_mode_routes_every_sensitivity_the_same_way(
            self, monkeypatch):
        """The regression this replaces: TIER_2/3 no longer forces local
        just because it's sensitive -- that second-guessing is gone, the
        gate decides what's safe to send, not this function."""
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: True)
        for sens in (1, 2, 3):
            model, pinned = indexer._resolve_model(sens, "cloud")
            assert model is None and pinned is False

    def test_dead_gate_blocks_cloud(self, monkeypatch):
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: False)
        with pytest.raises(indexer.CloudIndexingDisabled):
            indexer._resolve_model(1, "cloud")


class TestAvailableLocalModel:
    """`_available_local_model()` -- the 2026-09-03 fix for the default that
    was broken for everyone: `_local_model()` names a PREFERENCE with no
    check it is installed. This is what actually asks Ollama."""

    def _fake_manager(self, names):
        class _Mgr:
            def list_models(self):
                return [{"name": n} for n in names]
        return _Mgr()

    def test_prefers_the_configured_model_when_installed(self, monkeypatch):
        from agent_friday.services.model_plan import FLOOR_MODEL
        monkeypatch.setattr(indexer, "_local_model", lambda: FLOOR_MODEL)
        monkeypatch.setattr(
            "agent_friday.routing.ollama_manager.get_manager",
            lambda: self._fake_manager([FLOOR_MODEL, "gemma3:4b"]))
        assert indexer._available_local_model() == FLOOR_MODEL

    def test_falls_back_to_smallest_installed_tool_capable_model(
            self, monkeypatch):
        from agent_friday.services.model_plan import BRAIN_MODELS
        capable = [m["id"] for m in BRAIN_MODELS if m["tools"]]
        assert len(capable) >= 2, "need at least two rungs for this test"
        smallest, other = capable[0], capable[-1]
        # user's preference is a model that isn't installed
        monkeypatch.setattr(indexer, "_local_model", lambda: other + "-nope")
        monkeypatch.setattr(
            "agent_friday.routing.ollama_manager.get_manager",
            lambda: self._fake_manager([smallest, "gemma3:4b"]))
        assert indexer._available_local_model() == smallest

    def test_none_when_nothing_installed_can_call_tools(self, monkeypatch):
        # gemma3:4b is on the ladder but cannot call tools (defect H3)
        monkeypatch.setattr(
            "agent_friday.routing.ollama_manager.get_manager",
            lambda: self._fake_manager(["gemma3:4b"]))
        assert indexer._available_local_model() is None

    def test_none_when_ollama_is_unreachable(self, monkeypatch):
        class _BrokenMgr:
            def list_models(self):
                raise RuntimeError("connection refused")
        monkeypatch.setattr(
            "agent_friday.routing.ollama_manager.get_manager",
            lambda: _BrokenMgr())
        assert indexer._available_local_model() is None


class TestKnowledgeGraphSettingsPersistence:
    """Regression: `knowledge_graph` was absent from `core.DEFAULT_SETTINGS`.

    `_load_settings_raw()` whitelists every top-level key it returns against
    `DEFAULT_SETTINGS` (core/__init__.py:2002) -- a key missing from that
    dict is written to settings.json successfully (the write path does not
    whitelist, see `_save_settings`) and then silently dropped on every
    subsequent read. `kg_settings()` reads its user overlay via
    `_load_settings()`, so a user who unchecked "index conversations" or
    switched indexing_mode to gated_cloud in Settings -> Knowledge saw
    "Saved", and the very next read served the untouched factory defaults --
    the same defect class as `egress_mode` and the top-level
    `vault_local_only` (docs/design/security-boundary.md #18), just on the
    other side of the settings file.
    """

    @pytest.fixture
    def restore_kg_settings(self):
        """This test writes into the REAL, session-shared settings.json
        (the whole point is to exercise the real _save_settings /
        _load_settings_raw round trip, not a mock of it) -- so it must put
        the `knowledge_graph` block back exactly as it found it, or every
        test after it in the same session inherits a customized
        indexing_mode. `test_defaults_local_only_and_index_everything` in
        test_knowledge_graph_store.py caught exactly this the first time
        this fixture didn't exist."""
        import agent_friday.core as core
        original = core._load_settings_raw().get("knowledge_graph")
        yield
        core._save_settings({"knowledge_graph": original or {}})
        core._invalidate_settings_cache()

    def test_saved_kg_customization_survives_a_reload(
            self, monkeypatch, restore_kg_settings):
        import agent_friday.core as core
        from agent_friday.services.knowledge_graph import kg_settings

        core._invalidate_settings_cache()
        custom = {"indexing_mode": "cloud",
                  "index_sources": {"wiki": True, "conversations": False,
                                     "cognitive": False, "soul": False}}
        core._save_settings({"knowledge_graph": custom})
        core._invalidate_settings_cache()

        raw = core._load_settings_raw()
        assert raw.get("knowledge_graph", {}).get("indexing_mode") == \
            "cloud", (
                "the saved block did not survive _load_settings_raw() -- "
                "'knowledge_graph' fell out of the DEFAULT_SETTINGS "
                "whitelist again")

        merged = kg_settings()
        assert merged["indexing_mode"] == "cloud"
        assert merged["index_sources"]["conversations"] is False
        # Untouched defaults still fill in what the user never set.
        assert merged["nightly_reindex"] is True

    def test_without_the_default_settings_entry_the_save_is_silently_lost(
            self, monkeypatch, restore_kg_settings):
        """Would have caught the original bug: pull `knowledge_graph` back
        out of DEFAULT_SETTINGS (reproducing the state before this fix) and
        confirm the exact failure this class describes -- a successful save
        that a reload cannot see."""
        import agent_friday.core as core
        from agent_friday.services.knowledge_graph import kg_settings

        core._invalidate_settings_cache()
        core._save_settings({"knowledge_graph": {"indexing_mode": "cloud"}})
        core._invalidate_settings_cache()

        stripped = {k: v for k, v in core.DEFAULT_SETTINGS.items()
                    if k != "knowledge_graph"}
        monkeypatch.setattr(core, "DEFAULT_SETTINGS", stripped)
        core._invalidate_settings_cache()
        try:
            raw = core._load_settings_raw()
            assert "knowledge_graph" not in raw
            assert kg_settings()["indexing_mode"] == "local"  # reverted
        finally:
            core._invalidate_settings_cache()


class TestConversationChunks:
    """Regression coverage for the conversation source.

    `_conversation_chunks` used to call `cm.recent_turns(limit=...)`, a
    method `ConversationMemory` has never had, and read a `content` field
    that its real `recent()` has never returned either. A bare
    `except Exception: return []` turned both into an empty list that reads
    exactly like "no conversations yet" -- so the conversation source has
    never indexed a single turn. These stub `ConversationMemory` the way the
    real `recent()` actually shapes its rows (`text`, not `content`; no
    `turn_id`), so a reintroduced wrong method or field name fails loudly.
    """

    def test_recent_shape_is_indexed(self, monkeypatch):
        import agent_friday.conversation_memory as cmem

        class FakeConversationMemory:
            def available(self):
                return True

            def recent(self, n=20, roles=None):
                assert n == 400  # the indexer's limit, passed through
                return [
                    {"text": "A" * 50, "role": "user",
                     "timestamp": "2026-09-01T00:00:00", "date": "2026-09-01",
                     "session_id": "s1", "topic_keywords": []},
                    # under the 40-char floor -- must be skipped as trivia
                    {"text": "short", "role": "user",
                     "timestamp": "2026-09-01T00:01:00", "date": "2026-09-01",
                     "session_id": "s1", "topic_keywords": []},
                ]

        monkeypatch.setattr(cmem, "ConversationMemory", FakeConversationMemory)
        monkeypatch.setattr(indexer, "_classify_free_text", lambda text: 2)

        chunks = list(indexer._conversation_chunks())
        assert len(chunks) == 1
        c = chunks[0]
        assert c["text"] == "A" * 50
        assert c["sensitivity"] == 2
        assert c["id"].startswith("conv:")
        assert c["source_path"].startswith("conversation:")
        assert c["provenance"]["conversations"]

    def test_unavailable_store_yields_nothing_quietly(self, monkeypatch, capsys):
        import agent_friday.conversation_memory as cmem

        class FakeConversationMemory:
            def available(self):
                return False

        monkeypatch.setattr(cmem, "ConversationMemory", FakeConversationMemory)
        assert list(indexer._conversation_chunks()) == []
        # "not ready" is not a failure -- must not print an alarm for it.
        assert capsys.readouterr().out == ""

    def test_broken_method_is_reported_not_swallowed(self, monkeypatch, capsys):
        """Would have caught the original bug: a store shaped like the real
        one (available, but no working `recent`) must not vanish without a
        trace -- it still degrades to [], but says why."""
        import agent_friday.conversation_memory as cmem

        class BrokenConversationMemory:
            def available(self):
                return True
            # no `recent` (and no `recent_turns`) -- calling either raises
            # AttributeError, reproducing the original defect's failure mode.

        monkeypatch.setattr(cmem, "ConversationMemory", BrokenConversationMemory)
        assert list(indexer._conversation_chunks()) == []
        assert "conversation source failed" in capsys.readouterr().out


class TestCognitiveChunksFailureVisibility:
    def test_broken_source_is_reported_not_swallowed(self, monkeypatch, capsys):
        import agent_friday.cognitive_memory as cogmem

        class BrokenCognitiveMemory:
            def __init__(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(cogmem, "CognitiveMemory", BrokenCognitiveMemory)
        assert list(indexer._cognitive_chunks()) == []
        assert "cognitive memory source failed" in capsys.readouterr().out


class TestIndexPass:
    def test_full_index_produces_artifacts(self, wiki_home, tmp_path):
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="full", llm=llm)
        assert info["entities"] == 2
        assert info["relationships"] == 1
        ents = [e for e in store.load("entities") if e.get("tier") == "B"]
        assert {e["title"] for e in ents} == {"Graphrag", "Friday"}
        # provenance points back to the source, never a copy
        assert all(e["provenance"].get("wiki_pages") for e in ents)
        # layout covers Tier B nodes too
        assert all("x" in e for e in ents)

    def test_delta_reindex_skips_unchanged(self, wiki_home, tmp_path):
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        indexer.reindex_tier_b(store=store, mode="full", llm=llm)
        first_calls = len(llm.calls)
        assert first_calls > 0

        llm2 = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="delta", llm=llm2)
        assert info["extracted"] == 0
        extract_calls = [c for c in llm2.calls if "-Goal-" in c["text"]]
        assert extract_calls == []          # no re-extraction of unchanged

    def test_tier_a_rebuild_preserves_tier_b(self, wiki_home, tmp_path):
        from agent_friday.services.knowledge_graph import wiki_graph
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        indexer.reindex_tier_b(store=store, mode="full", llm=RecordingLLM())
        b_before = {e["id"] for e in store.load("entities")
                    if e.get("tier") == "B"}
        assert b_before
        wiki_graph.rebuild_tier_a(store=store)
        b_after = {e["id"] for e in store.load("entities")
                   if e.get("tier") == "B"}
        assert b_after == b_before


class TestFailFast:
    """2026-09-03, item #1: a mode that can't run must say so once, before
    any chunk is attempted -- not fail per-chunk 348 times. These exercise
    the REAL default path (`llm=None`, i.e. `call is _llm`) deliberately --
    the checks are gated on that so an injected test/caller llm is never
    blocked by a machine's real Ollama or gate state (TestIndexPass above
    relies on that gate to stay hermetic)."""

    def test_local_mode_refuses_before_touching_any_chunk(
            self, wiki_home, tmp_path, monkeypatch):
        monkeypatch.setattr(indexer, "_available_local_model", lambda: None)
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        info = indexer.reindex_tier_b(store=store, mode="full")  # llm=None
        assert info["error"] == "no_local_model"
        assert info["degraded"] is True
        assert info["extracted"] == 0 and info["extract_failures"] == 0
        assert info["chunks"] > 0  # the corpus was gathered, just not touched
        assert [e for e in store.load("entities") if e.get("tier") == "B"] == []

    def test_cloud_mode_refuses_when_gate_self_test_fails(
            self, wiki_home, tmp_path, monkeypatch):
        import agent_friday.services.knowledge_graph as kg
        monkeypatch.setattr(kg, "_load_settings", lambda: {
            "knowledge_graph": {
                "indexing_mode": "cloud",
                "index_sources": {"wiki": True, "soul": False,
                                   "cognitive": False, "conversations": False}}})
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: False)
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        info = indexer.reindex_tier_b(store=store, mode="full")  # llm=None
        assert info["error"] == "egress_gate_unavailable"
        assert info["degraded"] is True
        assert info["extracted"] == 0

    def test_injected_llm_bypasses_the_local_fail_fast(
            self, wiki_home, tmp_path, monkeypatch):
        """A caller supplying its own `llm` owns its own backend -- the
        fail-fast exists to protect the real default path, not to demand
        every test have a real Ollama running."""
        monkeypatch.setattr(indexer, "_available_local_model", lambda: None)
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        info = indexer.reindex_tier_b(store=store, mode="full", llm=RecordingLLM())
        assert info.get("error") is None
        assert info["entities"] == 2
