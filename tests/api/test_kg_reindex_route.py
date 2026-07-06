"""Phase 2 — Tier B reindex route (sync mode for determinism)."""

import pytest


@pytest.fixture
def seeded_wiki():
    # Use the import-time constant, not Path.home(): tests/test_egress_adversarial.py
    # rebinds USERPROFILE at import and the redirect leaks session-wide, while the
    # indexer reads WIKI_DIR frozen at first import. Writing anywhere else means
    # the indexer scans a different (empty) wiki.
    from agent_friday.core import WIKI_DIR
    wiki = WIKI_DIR
    (wiki / "research").mkdir(parents=True, exist_ok=True)
    (wiki / "research" / "kb.md").write_text(
        "# KB\n\nGraphRAG and Friday together.\n", encoding="utf-8")
    from agent_friday.services.knowledge_graph import mark_wiki_dirty
    mark_wiki_dirty("test")
    return wiki


def test_reindex_tier_b_sync(client, seeded_wiki, patch_app):
    canned = ('("entity"<|>GRAPHRAG<|>concept<|>graphs from text)\n'
              '<|COMPLETE|>')
    # The api conftest already stubs _generate_text everywhere; make the stub
    # return a parseable extraction so the pass produces entities.
    patch_app("_generate_text",
              lambda *a, **k: canned)

    r = client.post("/api/knowledge-graph/reindex",
                    json={"tier": "B", "mode": "full", "sync": True})
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok"
    assert d["entities"] >= 1
    assert d["mode"] == "local_only"        # shipped default

    r = client.get("/api/knowledge-graph/reindex/status")
    assert r.status_code == 200
    assert r.get_json()["running"] is False


def test_query_route_modes(client, seeded_wiki, patch_app):
    patch_app("_generate_text", lambda *a, **k: "stub answer")
    # structural (explicit)
    r = client.post("/api/knowledge-graph/query",
                    json={"question": "list all pages about research",
                          "mode": "structural"})
    assert r.status_code == 200
    assert r.get_json()["mode"] == "structural"
    # auto-routed (no Tier B index in this test home → structural)
    r = client.post("/api/knowledge-graph/query",
                    json={"question": "tell me about graphrag"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
