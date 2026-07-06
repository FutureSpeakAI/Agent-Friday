"""Phase 2 hard gate — adversarial egress test for the knowledge indexer.

Seeds the corpus with TIER_3 secrets (SSN, bank account, health data) in an
encrypted wiki section, then runs Tier B indexing in the *most permissive*
mode (gated_cloud) and asserts, at the indexer's single LLM choke point:

  1. No chunk carrying a sensitive span is ever routed to a cloud model.
  2. local_only mode (the shipped default) never requests a cloud model at
     all — for any tier.
  3. A failed egress-gate self-test disables cloud indexing outright.
  4. The derived artifacts holding TIER_3-derived records are encrypted at
     rest (or withheld when no vault key exists) — never plaintext.

This extends tests/security/test_egress_gate_adversarial.py: that suite
proves seal_outbound() withholds sensitive spans from cloud payloads; this
suite proves the indexer never even hands TIER_3 content to the cloud path.
"""

import json
from pathlib import Path

import pytest

from agent_friday.services.knowledge_graph import indexer
from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore

SSN = "123-45-6789"  # pragma: allowlist secret — fake canary the suite plants to prove it never leaves
BANK = "bank account number 987654321"
HEALTH = "diagnosis: type 2 diabetes, A1C 8.4, metformin 500mg"

CANNED = ('("entity"<|>PROBE<|>concept<|>benign)\n<|COMPLETE|>')


@pytest.fixture
def seeded_home(monkeypatch):
    wiki = Path.home() / ".friday" / "wiki"
    (wiki / "health").mkdir(parents=True, exist_ok=True)
    (wiki / "research").mkdir(parents=True, exist_ok=True)
    (wiki / "health" / "records.md").write_text(
        f"# Records\n\nMy SSN is {SSN}. {BANK}. {HEALTH}.\n", encoding="utf-8")
    (wiki / "research" / "public.md").write_text(
        "# Public\n\nCompletely public notes about graph theory.\n",
        encoding="utf-8")

    import agent_friday.services.knowledge_graph as kg
    import agent_friday.services.wiki_engine as we
    # health is an encrypted section → TIER_3 provenance
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: {"health"})
    monkeypatch.setattr(kg, "_load_settings", lambda: {
        "knowledge_graph": {"index_sources": {
            "wiki": True, "soul": False, "cognitive": False,
            "conversations": False}}})
    return wiki


class SpyLLM:
    def __init__(self):
        self.calls = []

    def __call__(self, messages, system, sensitivity, mode, orb_label=None):
        # mirror the indexer's routing decision at the choke point
        model, pinned = indexer._resolve_model(sensitivity, mode)
        self.calls.append({
            "sensitivity": sensitivity, "mode": mode,
            "cloud_eligible": model is None,
            "payload": (messages[0]["content"] or "") + " " + (system or ""),
        })
        return CANNED


def _sensitive(payload: str) -> bool:
    return SSN in payload or "987654321" in payload or "A1C" in payload


class TestAdversarialEgress:
    def test_gated_cloud_never_sends_tier3_to_cloud(self, seeded_home,
                                                    tmp_path, monkeypatch):
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: True)
        # force the most permissive mode
        from agent_friday.services.knowledge_graph import __init__ as _kg  # noqa
        import agent_friday.services.knowledge_graph as kg
        base = kg.kg_settings()
        monkeypatch.setattr(kg, "kg_settings", lambda: {
            **base, "indexing_mode": "gated_cloud"})
        monkeypatch.setattr(indexer, "kg_settings", kg.kg_settings)

        spy = SpyLLM()
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        indexer.reindex_tier_b(store=store, mode="full", llm=spy)

        assert spy.calls, "indexer made no LLM calls — nothing was tested"
        cloud_payloads = [c["payload"] for c in spy.calls
                          if c["cloud_eligible"]]
        local_payloads = [c["payload"] for c in spy.calls
                          if not c["cloud_eligible"]]
        # ZERO sensitive spans in anything cloud-eligible.
        assert not any(_sensitive(p) for p in cloud_payloads), \
            "TIER_3 content reached a cloud-eligible call"
        # The sensitive content WAS indexed — locally.
        assert any(_sensitive(p) for p in local_payloads), \
            "sensitive chunk was never indexed at all (should go local)"
        # And every call carrying it was classified TIER_3.
        for c in spy.calls:
            if _sensitive(c["payload"]):
                assert c["sensitivity"] == 3

    def test_local_only_default_never_requests_cloud(self, seeded_home,
                                                     tmp_path):
        spy = SpyLLM()
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        indexer.reindex_tier_b(store=store, mode="full", llm=spy)
        assert spy.calls
        assert all(not c["cloud_eligible"] for c in spy.calls), \
            "local_only mode produced a cloud-eligible call"

    def test_dead_gate_degrades_to_local(self, seeded_home, tmp_path,
                                         monkeypatch):
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: False)
        import agent_friday.services.knowledge_graph as kg
        base = kg.kg_settings()
        monkeypatch.setattr(kg, "kg_settings", lambda: {
            **base, "indexing_mode": "gated_cloud"})
        monkeypatch.setattr(indexer, "kg_settings", kg.kg_settings)

        spy = SpyLLM()
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        info = indexer.reindex_tier_b(store=store, mode="full", llm=spy)
        # pass completed, but strictly locally
        assert info["mode"] == "local_only"
        assert all(not c["cloud_eligible"] for c in spy.calls)

    def test_tier3_derived_artifacts_never_plaintext(self, seeded_home,
                                                     tmp_path, monkeypatch):
        # With a vault key: sensitive records land in the encrypted sibling.
        from agent_friday.services.knowledge_graph import store as store_mod
        monkeypatch.setattr(store_mod, "_vault_key", lambda: b"k" * 32)

        class LeakyLLM(SpyLLM):
            def __call__(self, messages, system, sensitivity, mode,
                         orb_label=None):
                super().__call__(messages, system, sensitivity, mode)
                if _sensitive(messages[0]["content"] or ""):
                    return ('("entity"<|>MY HEALTH RECORD<|>concept<|>'
                            f'Contains SSN {SSN} and {HEALTH})\n<|COMPLETE|>')
                return CANNED

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        indexer.reindex_tier_b(store=store, mode="full", llm=LeakyLLM())

        plain = (store.base / "entities.json").read_text(encoding="utf-8")
        assert SSN not in plain and "A1C" not in plain, \
            "TIER_3-derived entity written to plaintext artifact"
        merged = store.load("entities")
        assert any(SSN in (e.get("description") or "") for e in merged), \
            "encrypted sibling did not round-trip the sensitive entity"
