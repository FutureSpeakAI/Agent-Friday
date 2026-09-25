"""A fact learned from the public web says where it came from.

source_kind="research" is the provenance for findings the user reviewed and
accepted in the setup chat. It records the URLs the fact came from, and a
research fact with no source is refused rather than stored unattributed.
"""
from __future__ import annotations


def test_ingest_fact_records_research_sources_and_refuses_none(tmp_path, monkeypatch):
    from agent_friday.services.knowledge_graph import integration
    saved = {}

    class Store:
        def load(self, kind):
            return saved.get(kind, [])

        def save(self, kind, rows):
            saved[kind] = rows
    monkeypatch.setattr("agent_friday.services.knowledge_graph.store.KnowledgeGraphStore",
                        Store)
    monkeypatch.setattr(integration, "kg_settings", lambda: {"enabled": True})
    assert integration.ingest_fact("Sam is a principal engineer at Acme.",
                                   source_kind="research", source_key="k") is None
    eid = integration.ingest_fact(
        "Sam is a principal engineer at Acme.", source_kind="research",
        source_key="setup-research:j:c",
        sources=[{"url": "https://example.org/a", "fetched_at": 1}])
    node = saved["entities"][0]
    assert node["id"] == eid
    assert node["provenance"]["learned"] == "research"
    assert node["provenance"]["research"] == ["setup-research:j:c"]
    assert node["provenance"]["sources"] == [{"url": "https://example.org/a", "fetched_at": 1}]
