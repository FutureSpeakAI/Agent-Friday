"""People trust stays home: a person's record never reaches a cloud model.

Red on main 7c49be86: `query_trust_graph` returned the whole record (scores,
evidence, saved intelligence) into whichever tool loop ran it, and the
context builder put the TRUST DATA / TRUST NETWORK sections and the
smart-context TRUST GRAPH block into a cloud prompt whenever vault gating
was off. Now the full record is assembled only for a loop that is KNOWN
local; the cloud and an unknown provider get role, confirmed relationship
and contact channel.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import agent as ag

PERSON = {
    "name": "Pat Example", "aliases": ["Pat"], "entity_type": "human",
    "role": "accountant", "company": "Example Ltd", "emails": ["pat@example.test"],
    "relationship": {"type": "colleague", "confirmed_by_owner": True},
    "scores": {"reliability": 0.91, "emotional_safety": 0.4, "alignment": 0.6, "competence": 0.8},
    "evidence": [{"type": "kept_commitment", "notes": "returned the contract on Tuesday"}],
    "intelligence": [{"content": "SAVED-INTEL: prefers morning calls", "source": "data_flow"}],
}
SECRETS = ("0.91", "kept_commitment", "returned the contract", "SAVED-INTEL", "emotional_safety")
TOOLS = [{"type": "function", "function": {
    "name": "query_trust_graph",
    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}}}}]


@pytest.fixture
def graph(monkeypatch):
    from agent_friday.services import misc_engine as me
    monkeypatch.setattr(me, "_load_trust_graph", lambda *a, **k: {"people": {"pat_example": PERSON}})
    return PERSON


def _loop_tool_result(provider, monkeypatch):
    """Run one query_trust_graph call through the shared loop on `provider`
    and return the text the model got back."""
    results = []
    real = ag._execute_tool

    def through_hooks_free(name, args, **kw):
        # The gates are not under test; the handler's own answer is.
        handler = ag.CLAUDE_TOOL_HANDLERS[name]
        tok = ag._CURRENT_PROVIDER.set((kw.get("session_ctx") or {}).get("provider") or ag._LOOP_PROVIDER.get())
        try:
            out = handler(args)
        finally:
            ag._CURRENT_PROVIDER.reset(tok)
        results.append(out)
        return out
    monkeypatch.setattr(ag, "_execute_tool", through_hooks_free)
    rounds = []

    def send(convo, tools, **kw):
        rounds.append(1)
        if len(rounds) == 1:
            return {"choices": [{"message": {"role": "assistant", "content": "",
                                             "tool_calls": [{"id": "c1", "type": "function",
                                                             "function": {"name": "query_trust_graph",
                                                                          "arguments": json.dumps({"name": "Pat"})}}]},
                                 "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    ag._oai_agentic_loop([{"role": "user", "content": "what do we know about Pat?"}], TOOLS, send,
                         provider=provider, model="fixture")
    assert results, "the tool never ran"
    return results[-1]


def test_a_cloud_loop_gets_role_relationship_and_channel_only(graph, monkeypatch):
    out = _loop_tool_result("anthropic", monkeypatch)
    for s in SECRETS:
        assert s not in out, "a cloud loop saw %r" % s
    data = json.loads(out)
    assert data["name"] == "Pat Example" and data["role"] == "accountant"
    assert data["relationship"] == "colleague" and data["emails"] == ["pat@example.test"]
    assert "stays on the owner's machine" in data["note"]


def test_a_local_loop_gets_the_full_record(graph, monkeypatch):
    out = _loop_tool_result("local", monkeypatch)
    for s in SECRETS:
        assert s in out


def test_an_unknown_provider_is_treated_as_not_local(graph, monkeypatch):
    """A cloud voice session sets no provider; unknown must mean 'not local'."""
    tok = ag._LOOP_PROVIDER.set(None)
    try:
        out = ag._tool_query_trust_graph({"name": "Pat"})
    finally:
        ag._LOOP_PROVIDER.reset(tok)
    for s in SECRETS:
        assert s not in out


def test_the_anthropic_loop_names_itself_cloud(monkeypatch):
    seen = []

    def run(*a, **k):
        seen.append(ag._LOOP_PROVIDER.get())
        return "ok", []
    monkeypatch.setattr(ag, "_call_claude_agent_run", run)
    ag._call_claude_agent([{"role": "user", "content": "x"}])
    assert seen == ["anthropic"]
    assert ag._LOOP_PROVIDER.get() is None, "the loop's provider leaked past its run"


def test_an_unconfirmed_relationship_is_a_judgement_and_stays_home():
    from agent_friday.trust import people as tp
    rec = dict(PERSON, relationship={"type": "rival", "confirmed_by_owner": False})
    view = tp.view_for_loop(rec, local=False)
    assert "relationship" not in view


def test_a_cloud_prompt_build_carries_no_dimension_or_evidence(monkeypatch, tmp_path):
    """Vault gating off, cloud provider: the plain join used to include the
    TRUST DATA section and the smart-context TRUST GRAPH block verbatim."""
    from agent_friday.services import model_router as mr
    tfile = tmp_path / "trust_graph.json"
    tfile.write_text(json.dumps({"people": {"pat_example": PERSON}}), encoding="utf-8")
    monkeypatch.setattr(mr, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(mr, "_load_vault_summary",
                        lambda: {"trust_people": {"Pat Example": {"overall": 0.91, "relationship": "colleague"}}},
                        raising=False)
    real_needs = mr._detect_context_needs

    def needs(message, *a, **k):
        n = real_needs(message, *a, **k)
        n.add("trust")
        return n
    monkeypatch.setattr(mr, "_detect_context_needs", needs)
    cloud, _ = mr._build_context_prompt("what did Pat Example say about the contract?",
                                        workspace="chat", provider="anthropic", vault_control=None)
    for s in SECRETS:
        assert s not in cloud, "a cloud prompt carried %r" % s
    assert "TRUST DATA" not in cloud and "TRUST NETWORK" not in cloud and "TRUST GRAPH:" not in cloud
    local, _ = mr._build_context_prompt("what did Pat Example say about the contract?",
                                        workspace="chat", provider="local", vault_control=None)
    assert "Pat Example" in local and ("0.91" in local or "TRUST" in local)
