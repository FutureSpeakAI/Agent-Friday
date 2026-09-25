"""Gauntlet finding F34: _resolve_model()'s `pinned`
return value was computed and then discarded -- indexer._llm() passed the
resolved model name to model_router._generate_text() as a hint only, and
routing/model_router.py's capability-based seat choice
(settings.capability_routing.reasoning) can select a cloud model regardless
of that hint. index.html's own KG settings copy ("Local only = nothing ever
leaves this machine") and knowledge_graph/indexer.py's own module docstring
("indexing_mode 'local_only' ... pins every call to the local model") were
both false the moment a user picked a cloud model as their reasoning seat --
an ordinary, UI-encouraged action. _llm() must call the local primitive
directly when pinned, never the general router.

Gauntlet probes live only in tests/gauntlet/, so this file is
self-contained rather than importing helpers from
tests/unit/test_kg_indexer.py.
"""
from __future__ import annotations

from agent_friday.services.knowledge_graph import indexer


class TestLlmEnforcesThePin:
    def test_pinned_chunk_calls_ollama_directly_not_the_router(self, monkeypatch):
        # indexing_mode's legacy "local_only"/"gated_cloud" pair is now
        # "local"/"cloud" (a strict per-user choice: _resolve_model no
        # longer overrides it per-chunk by sensitivity). "local" mode always
        # pins; an installed model is mocked because _resolve_model()'s
        # local branch checks for one.
        monkeypatch.setattr(indexer, "_available_local_model", lambda: "gemma4:e2b")
        calls = {"ollama": [], "generate_text": []}
        monkeypatch.setattr(
            "agent_friday.services.model_router._call_ollama",
            lambda messages, **kw: (calls["ollama"].append(kw), ("local text", []))[1])
        monkeypatch.setattr(
            "agent_friday.services.model_router._generate_text",
            lambda messages, **kw: calls["generate_text"].append(kw) or "cloud text")

        out = indexer._llm([{"role": "user", "content": "hi"}], None, 1, "local")

        assert calls["ollama"], (
            "a local_only chunk never called _call_ollama at all -- the pin "
            "from _resolve_model() is not being enforced"
        )
        assert not calls["generate_text"], (
            "a local_only (pinned) chunk reached _generate_text(), which "
            "consults routing/model_router.py's general seat choice and can "
            "select a cloud model regardless of the model= hint -- this is "
            "exactly how 'local only = nothing ever leaves this machine' "
            "became false"
        )
        assert out == "local text"

    def test_unpinned_chunk_still_uses_the_router(self, monkeypatch):
        """No-op-shaped sanity check: cloud/TIER_1 (unpinned) chunks must
        still go through _generate_text as before -- the fix must be
        specific to the pinned case, not a blanket switch to local.

        "cloud" (formerly "gated_cloud") is the only mode _resolve_model
        treats as unpinned, with the same routing/egress-gate requirement.
        """
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: True)
        calls = {"ollama": [], "generate_text": []}
        monkeypatch.setattr(
            "agent_friday.services.model_router._call_ollama",
            lambda messages, **kw: calls["ollama"].append(kw) or ("local text", []))
        monkeypatch.setattr(
            "agent_friday.services.model_router._generate_text",
            lambda messages, **kw: calls["generate_text"].append(kw) or "cloud text")

        out = indexer._llm([{"role": "user", "content": "hi"}], None, 1, "cloud")

        assert calls["generate_text"], "an unpinned chunk must still use the router"
        assert not calls["ollama"]
        assert out == "cloud text"
