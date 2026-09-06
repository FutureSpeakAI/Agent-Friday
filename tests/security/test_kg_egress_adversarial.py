"""Phase 2 hard gate — adversarial egress test for the knowledge indexer.

Rewritten 2026-09-03, same day as the routing redesign this file used to
test the OLD shape of: `gated_cloud` pinned TIER_2/3 chunks local no matter
what the user chose, so the indexer was itself a second privacy gate on top
of the egress gate. That per-tier override is gone — "he is not asking for
a system that decides for people, he's asking for one that does what the
person picked." `indexing_mode` ("local"/"cloud") is now a strict per-user
routing choice, uniform across sensitivity; what content is actually SAFE
to put on the wire is entirely the egress gate's decision, the same as
every other cloud call in the app.

That moves the safety property this file exists to prove: it is no longer
"the indexer never requests cloud for a sensitive chunk" (false now, and
deliberately so — "cloud" mode requests cloud for everything). It is "cloud
mode may ASK for a chunk to be sent, but the shared egress gate still
strips/withholds the sensitive text before it reaches the wire" — proven
here by running the REAL pipeline (reindex_tier_b -> indexer._llm ->
model_router._generate_text -> _call_claude -> seal_outbound) down to a
fake transport, and asserting the canary secrets never appear in what the
fake transport actually received.

Seeds the corpus with TIER_3 secrets (SSN, bank account, health data) in an
encrypted wiki section, and asserts:

  1. "cloud" mode may route sensitive chunks to the cloud-eligible default,
     but the SSN/bank/health canaries never appear in the payload that
     reaches the transport — the shared gate redacted or blocked them.
  2. "local" mode (the shipped default) never requests a cloud model at
     all — for any tier.
  3. A failed egress-gate self-test disables cloud indexing outright —
     refuses up front, rather than silently falling back to local.
  4. The derived artifacts holding TIER_3-derived records are encrypted at
     rest (or withheld when no vault key exists) — never plaintext.

This extends tests/security/test_egress_gate_adversarial.py: that suite
proves seal_outbound() withholds sensitive spans from cloud payloads; this
suite proves the indexer's real, wired-together cloud path actually goes
through that same gate rather than around it.
"""

import json
from pathlib import Path

import pytest

from agent_friday.services.knowledge_graph import indexer
from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
from tests.fake_backends import FakeAnthropicClient

SSN = "123-45-6789"  # pragma: allowlist secret — fake canary the suite plants to prove it never leaves
BANK = "bank account number 987654321"
HEALTH = "diagnosis: type 2 diabetes, A1C 8.4, metformin 500mg"

CANNED = ('("entity"<|>PROBE<|>concept<|>benign)\n<|COMPLETE|>')


@pytest.fixture
def seeded_home(monkeypatch):
    # Import-time WIKI_DIR + wipe: the suite plants exact canary strings and
    # asserts on which calls carry them, so the corpus must be exclusive
    # (and Path.home() is unreliable — tests/test_egress_adversarial.py
    # leaks a USERPROFILE redirect session-wide).
    import shutil
    from agent_friday.core import WIKI_DIR
    wiki = WIKI_DIR
    if wiki.exists():
        shutil.rmtree(wiki)
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
    def test_cloud_mode_never_puts_sensitive_text_on_the_wire(
            self, seeded_home, tmp_path, monkeypatch):
        """"cloud" mode WILL ask to route sensitive chunks to the cloud
        default now (that's the whole point of the redesign) — this proves
        that request never actually reaches the transport with the secret
        still in it. Runs the real pipeline down to a fake Anthropic
        transport; nothing above the transport is mocked."""
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: True)
        import agent_friday.services.knowledge_graph as kg
        base = kg.kg_settings()
        monkeypatch.setattr(kg, "kg_settings", lambda: {
            **base, "indexing_mode": "cloud"})
        monkeypatch.setattr(indexer, "kg_settings", kg.kg_settings)

        # Force the router to the cloud provider deterministically, rather
        # than depending on whatever model_routing happens to be on the
        # machine running the suite.
        import agent_friday.routing.model_router as routing_mod

        class _ForcedCloudRouter:
            def route(self, messages, task_context=None):
                return {"provider": "cloud", "model": None,
                        "provider_name": None}
        monkeypatch.setattr(routing_mod, "get_router",
                            lambda cfg: _ForcedCloudRouter())

        import agent_friday.services.model_router as mr
        fake = FakeAnthropicClient()
        monkeypatch.setattr(mr, "get_anthropic_client", lambda: fake)

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        indexer.reindex_tier_b(store=store, mode="full")  # llm=None: real _llm

        assert fake.calls, "indexer made no LLM calls — nothing was tested"
        for call in fake.calls:
            wire_text = json.dumps(call.get("messages", []))
            assert not _sensitive(wire_text), (
                "a secret canary reached the fake transport unredacted -- "
                "cloud mode is sending sensitive content around the gate, "
                "not through it")

    def test_local_mode_default_never_requests_cloud(self, seeded_home,
                                                      tmp_path, monkeypatch):
        monkeypatch.setattr(indexer, "_available_local_model",
                            lambda: "gemma4:e2b")
        spy = SpyLLM()
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        indexer.reindex_tier_b(store=store, mode="full", llm=spy)
        assert spy.calls
        assert all(not c["cloud_eligible"] for c in spy.calls), \
            "local mode (the default) produced a cloud-eligible call"

    def test_dead_gate_in_cloud_mode_refuses_rather_than_degrading(
            self, seeded_home, tmp_path, monkeypatch):
        """2026-09-03: no more silent degrade-to-local when the gate is
        down -- that was exactly the shape of silent failure the day's
        instruction named. A dead gate in "cloud" mode must refuse the pass
        outright, before any chunk is attempted."""
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: False)
        import agent_friday.services.knowledge_graph as kg
        base = kg.kg_settings()
        monkeypatch.setattr(kg, "kg_settings", lambda: {
            **base, "indexing_mode": "cloud"})
        monkeypatch.setattr(indexer, "kg_settings", kg.kg_settings)

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        info = indexer.reindex_tier_b(store=store, mode="full")  # llm=None
        assert info["error"] == "egress_gate_unavailable"
        assert info["extracted"] == 0 and info["degraded"] is True

    def test_tier3_derived_artifacts_never_plaintext(self, seeded_home,
                                                     tmp_path, monkeypatch):
        # With a vault key: sensitive records land in the encrypted sibling.
        from agent_friday.services.knowledge_graph import store as store_mod
        monkeypatch.setattr(store_mod, "_vault_key", lambda: b"k" * 32)
        # indexing_mode defaults to "local" here (seeded_home doesn't
        # override it) -- LeakyLLM is injected, but its own SpyLLM base
        # still calls the real _resolve_model as part of its spying, which
        # needs an installed model to resolve against; mocked so this test
        # doesn't depend on whatever the machine running it has pulled.
        monkeypatch.setattr(indexer, "_available_local_model",
                            lambda: "gemma4:e2b")

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
