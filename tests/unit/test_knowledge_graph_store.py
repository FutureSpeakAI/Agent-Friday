"""Phase 0 — KnowledgeGraphStore round-trip + encryption-at-rest."""

import json

import pytest

from agent_friday.services.knowledge_graph import kg_settings, mark_wiki_dirty, \
    consume_wiki_dirty
from agent_friday.services.knowledge_graph.store import (
    KnowledgeGraphStore, record_is_sensitive)


def _entity(i, sensitivity=1):
    return {
        "id": f"ent_{i}",
        "title": f"Entity {i}",
        "type": "concept",
        "description": "d",
        "degree": 1,
        "frequency": 1,
        "provenance": {"wiki_pages": [f"concepts/e{i}.md"],
                       "sensitivity": sensitivity},
    }


@pytest.fixture
def store(tmp_path):
    return KnowledgeGraphStore(base_dir=tmp_path / "kg")


class TestRoundTrip:
    def test_all_artifact_types_round_trip(self, store):
        for name in ("entities", "relationships", "communities",
                     "community_reports"):
            recs = [_entity(i) for i in range(3)]
            info = store.save(name, recs)
            assert info["public"] == 3
            assert store.load(name) == recs

    def test_unknown_artifact_rejected(self, store):
        with pytest.raises(ValueError):
            store.save("nope", [])
        with pytest.raises(ValueError):
            store.load("nope")

    def test_layout_round_trip(self, store):
        store.save_layout({"seed": 1337, "algorithm": "fibonacci+relax"})
        assert store.load_layout()["seed"] == 1337

    def test_clear_removes_everything(self, store):
        store.save("entities", [_entity(1)])
        store.save_layout({"seed": 1})
        store.clear()
        assert store.load("entities") == []
        assert store.load_layout() == {}

    def test_load_missing_is_empty(self, store):
        assert store.load("entities") == []


class TestSensitivity:
    def test_tier_detection_int_and_string(self):
        assert not record_is_sensitive(_entity(1, 1))
        assert record_is_sensitive(_entity(1, 2))
        assert record_is_sensitive(_entity(1, 3))
        assert record_is_sensitive(_entity(1, "TIER_3"))
        assert not record_is_sensitive(_entity(1, "TIER_1"))
        assert not record_is_sensitive({"id": "x"})  # no provenance → public

    def test_sensitive_records_encrypted_at_rest(self, store, monkeypatch):
        key = b"k" * 32
        from agent_friday.services.knowledge_graph import store as store_mod
        monkeypatch.setattr(store_mod, "_vault_key", lambda: key)

        recs = [_entity(1, 1), _entity(2, 3)]
        info = store.save("entities", recs)
        assert info == {"public": 1, "sensitive": 1, "dropped": 0}

        # Plain file holds only the public record, in plaintext.
        plain = json.loads((store.base / "entities.json").read_text())
        assert [r["id"] for r in plain] == ["ent_1"]

        # Sensitive sibling exists and is a vault blob, not JSON.
        blob = (store.base / "entities.sensitive.json").read_bytes()
        import agent_friday.privacy.vault_crypto as vc
        assert vc.is_encrypted(blob)
        assert b"ent_2" not in blob  # ciphertext, no plaintext leak

        # Transparent merged read-back.
        loaded = {r["id"] for r in store.load("entities")}
        assert loaded == {"ent_1", "ent_2"}

    def test_no_key_drops_sensitive_fail_closed(self, store, monkeypatch):
        from agent_friday.services.knowledge_graph import store as store_mod
        monkeypatch.setattr(store_mod, "_vault_key", lambda: None)

        info = store.save("entities", [_entity(1, 1), _entity(2, 3)])
        assert info["dropped"] == 1
        assert not (store.base / "entities.sensitive.json").exists()
        assert [r["id"] for r in store.load("entities")] == ["ent_1"]

    def test_wrong_key_omits_sensitive_but_keeps_public(self, store, monkeypatch):
        from agent_friday.services.knowledge_graph import store as store_mod
        monkeypatch.setattr(store_mod, "_vault_key", lambda: b"a" * 32)
        store.save("entities", [_entity(1, 1), _entity(2, 3)])
        monkeypatch.setattr(store_mod, "_vault_key", lambda: b"b" * 32)
        assert [r["id"] for r in store.load("entities")] == ["ent_1"]


class TestSettingsAndDirty:
    def test_defaults_local_only_and_index_everything(self):
        s = kg_settings()
        assert s["indexing_mode"] == "local_only"
        assert s["power_indexer"] == "native"
        assert all(s["index_sources"].values())

    def test_user_overlay_merges_nested(self, monkeypatch):
        import agent_friday.services.knowledge_graph as kg
        monkeypatch.setattr(kg, "_load_settings", lambda: {
            "knowledge_graph": {"indexing_mode": "gated_cloud",
                                "index_sources": {"conversations": False}}})
        s = kg.kg_settings()
        assert s["indexing_mode"] == "gated_cloud"
        assert s["index_sources"]["conversations"] is False
        assert s["index_sources"]["wiki"] is True  # untouched keys survive

    def test_dirty_flag_cycle(self):
        mark_wiki_dirty("test")
        assert consume_wiki_dirty() is True
        assert consume_wiki_dirty() is False
