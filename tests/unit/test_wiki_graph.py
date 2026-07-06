"""Phase 1 — Tier A structural graph: wikilinks, mentions, communities, layout."""

from pathlib import Path

import pytest

from agent_friday.services.knowledge_graph import wiki_graph, layout, graph_analysis
from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore


@pytest.fixture
def vault(tmp_path):
    """A tiny wiki: two linked clusters + one page with no links at all."""
    w = tmp_path / "wiki"
    (w / "research").mkdir(parents=True)
    (w / "people").mkdir(parents=True)
    (w / "misc").mkdir(parents=True)

    (w / "research" / "transformers.md").write_text(
        "# Transformers\n\nAttention-based architecture. See [[Attention]] "
        "and [neural nets](neural-nets.md).\n", encoding="utf-8")
    (w / "research" / "attention.md").write_text(
        "# Attention\n\nCore mechanism inside Transformers.\n",
        encoding="utf-8")
    (w / "research" / "neural-nets.md").write_text(
        "# Neural Nets\n\nFoundations.\n", encoding="utf-8")
    (w / "people" / "ada.md").write_text(
        "# Ada Lovelace\n\nWrote about early computing.\n", encoding="utf-8")
    (w / "misc" / "loner.md").write_text(
        "# Zzyzx\n\nNothing links here and this links nowhere.\n",
        encoding="utf-8")
    # Generated index files must be excluded from the graph.
    (w / "research" / "_index.md").write_text("# Index\n", encoding="utf-8")
    return w


class TestIndex:
    def test_pages_and_explicit_links(self, vault):
        idx = wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False,
                                          mention_edges=False)
        assert set(idx) == {"research/transformers", "research/attention",
                            "research/neural-nets", "people/ada", "misc/loner"}
        out = dict(idx["research/transformers"]["out_links"])
        assert out.get("research/attention") == "wikilink"
        assert out.get("research/neural-nets") == "mdlink"
        assert "research/transformers" in idx["research/attention"]["in_links"]

    def test_mention_edges_densify_linkless_vaults(self, vault):
        idx = wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False,
                                          mention_edges=True)
        # attention.md says "inside Transformers" with no [[link]] → mention edge.
        kinds = dict(idx["research/attention"]["out_links"])
        assert kinds.get("research/transformers") == "mention"

    def test_title_and_summary_from_headings(self, vault):
        idx = wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False)
        assert idx["people/ada"]["title"] == "Ada Lovelace"
        assert "computing" in idx["people/ada"]["summary"]

    def test_colliding_stems_both_survive(self, vault):
        # Same filename in two sections must yield two distinct nodes
        # (regression: professional/agent-friday.md vs ai-personality/…).
        (vault / "people" / "attention.md").write_text(
            "# Attention (person view)\n\nA different page, links "
            "[[Attention]].\n", encoding="utf-8")
        idx = wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False,
                                          mention_edges=False)
        assert "people/attention" in idx and "research/attention" in idx
        # Bare [[Attention]] from people/ resolves within its own section
        # first — but never to itself, so it falls to the other candidate.
        out = dict(idx["people/attention"]["out_links"])
        assert out.get("research/attention") == "wikilink"

    def test_missing_wiki_dir_is_empty(self, tmp_path):
        idx = wiki_graph.build_wiki_index(wiki_dir=tmp_path / "ghost",
                                          include_soul=False)
        assert idx == {}


class TestRecords:
    def test_contract_shape_and_provenance(self, vault):
        idx = wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False)
        recs = wiki_graph.index_to_records(idx)
        ent = {e["id"]: e for e in recs["entities"]}
        assert "page:research/transformers" in ent
        e = ent["page:research/transformers"]
        assert e["provenance"]["wiki_pages"] == ["research/transformers.md"]
        assert e["provenance"]["sensitivity"] == 1
        assert e["degree"] > 0

        rel_pairs = {(r["source"], r["target"]) for r in recs["relationships"]}
        assert ("page:research/transformers", "page:research/attention") in rel_pairs
        # Every entity belongs to some community; loner joins its section pool.
        assert all(e2.get("community") is not None for e2 in recs["entities"])
        comms = recs["communities"]
        assert sum(c["size"] for c in comms) == len(recs["entities"])

    def test_explicit_link_outranks_mention_dedup(self, vault):
        idx = wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False)
        recs = wiki_graph.index_to_records(idx)
        rels = [r for r in recs["relationships"]
                if r["source"] == "page:research/transformers"
                and r["target"] == "page:research/attention"]
        assert len(rels) == 1
        assert rels[0]["description"] == "wikilink"
        assert rels[0]["weight"] == 1.0

    def test_encrypted_section_marks_tier3(self, vault, monkeypatch):
        import agent_friday.services.wiki_engine as we
        monkeypatch.setattr(we, "_wiki_encrypted_sections",
                            lambda: {"people"})
        idx = wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False)
        assert idx["people/ada"]["sensitivity"] == 3
        assert idx["research/transformers"]["sensitivity"] == 1
        recs = wiki_graph.index_to_records(idx)
        ada = next(e for e in recs["entities"] if e["id"] == "page:people/ada")
        assert ada["provenance"]["sensitivity"] == 3


class TestLayoutDeterminism:
    def test_layout_is_deterministic_and_complete(self, vault):
        idx = wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False)
        r1 = wiki_graph.index_to_records(idx)
        r2 = wiki_graph.index_to_records(idx)
        m1 = layout.compute_layout(r1["entities"], r1["relationships"],
                                   r1["communities"], seed=7)
        m2 = layout.compute_layout(r2["entities"], r2["relationships"],
                                   r2["communities"], seed=7)
        p1 = [(e["x"], e["y"], e["z"]) for e in r1["entities"]]
        p2 = [(e["x"], e["y"], e["z"]) for e in r2["entities"]]
        assert p1 == p2
        assert m1["nodes"] == len(r1["entities"])
        assert all(abs(x) + abs(y) + abs(z) > 0 for x, y, z in p1)

    def test_different_seed_different_jitter(self, vault):
        idx = wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False)
        r1 = wiki_graph.index_to_records(idx)
        r2 = wiki_graph.index_to_records(idx)
        layout.compute_layout(r1["entities"], r1["relationships"],
                              r1["communities"], seed=1)
        layout.compute_layout(r2["entities"], r2["relationships"],
                              r2["communities"], seed=2)
        p1 = [(e["x"], e["y"], e["z"]) for e in r1["entities"]]
        p2 = [(e["x"], e["y"], e["z"]) for e in r2["entities"]]
        assert p1 != p2

    def test_empty_graph_layout(self):
        meta = layout.compute_layout([], [], [], seed=1)
        assert meta["nodes"] == 0


class TestGraphAnalysis:
    def test_communities_and_hubs(self):
        outgoing = {
            "a": ["b", "c", "d"], "b": ["c"], "c": [], "d": [],
            "x": ["y"], "y": ["x"],
            "lone": [],
        }
        comms = graph_analysis.detect_communities_greedy(outgoing)
        flat = {n for c in comms for n in c}
        assert flat == set(outgoing)
        gods = graph_analysis.god_nodes(outgoing, top_n=1)
        assert gods[0]["page"] == "a"       # degree 3 hub
        assert gods[0]["degree"] == 3
        assert "lone" in graph_analysis.isolated(outgoing)
        surprising = graph_analysis.surprising_connections(outgoing, comms)
        assert isinstance(surprising, list)

    def test_determinism(self):
        outgoing = {f"n{i}": [f"n{(i + 1) % 30}"] for i in range(30)}
        c1 = graph_analysis.detect_communities_greedy(outgoing)
        c2 = graph_analysis.detect_communities_greedy(dict(reversed(
            list(outgoing.items()))))
        assert [sorted(c) for c in c1] == [sorted(c) for c in c2]


class TestRebuild:
    def test_rebuild_tier_a_persists_artifacts(self, vault, tmp_path):
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        info = wiki_graph.rebuild_tier_a(store=store, wiki_dir=vault)
        assert info["entities"] >= 5
        assert (store.base / "entities.json").exists()
        assert (store.base / ".manifest.json").exists()
        ents = store.load("entities")
        assert all("x" in e and "y" in e and "z" in e for e in ents)
        # Rebuild is idempotent — same entity ids, no dupes.
        info2 = wiki_graph.rebuild_tier_a(store=store, wiki_dir=vault)
        assert info2["entities"] == info["entities"]
        ids = [e["id"] for e in store.load("entities")]
        assert len(ids) == len(set(ids))
