"""Egress tests for connector tool results (NS-34.6-10, BD-15; F4).

What a connector read returns (mail, calendar, tasks, Drive and Docs, contacts, an add-on's
tool) reaches the next model call as a tool result. On the way to a CLOUD model it passes the
egress gate: a private detail inside it is held, the rest still arrives, the egress log records
the hold under the tool's own name, and a result withheld whole is replaced by the tool-result
marker (never the marker that tells the model the user's own message was sensitive). A local
seat gets it as it is.

Both wire shapes: an OpenAI-compatible `role: "tool"` message answering a `tool_calls` id, and an
Anthropic `tool_result` block answering a `tool_use` id. The classifier is pinned here (private
markers are SENSITIVE, the rest PUBLIC), so the test proves the gate's handling for every
connector without loading a model. Synthetic content only.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import egress_gate as eg

KEY = b"synthetic-governance-key-for-tests-0000000000000000"

#: Private details a connector result can carry (all synthetic).
MEDICAL = "diagnosed with a synthetic condition for testing"
IBAN = "XX00 SYNTHETIC IBAN 0000 1111"
ACCOUNT_CODE = "SYNTHETIC-ACCOUNT-CODE-FOR-TESTS"
PRIVATE = (MEDICAL, IBAN, ACCOUNT_CODE)
#: Ordinary details that should still arrive.
PLAIN = ("Quarterly planning", "Team lunch", "Grocery run")

CONNECTORS = {
    "search_email": {"messages": [{"subject": "Quarterly planning", "snippet": "Lab results: " + MEDICAL}]},
    "check_email": {"unread": [{"subject": "Team lunch", "preview": "Wire to " + IBAN}]},
    "query_calendar": {"events": [{"title": "Team lunch", "description": "Bring card " + IBAN}]},
    "find_calendar_events": {"events": [{"title": "Quarterly planning", "notes": MEDICAL}]},
    "list_tasks": {"tasks": [{"title": "Grocery run", "notes": "token " + ACCOUNT_CODE}]},
    "search_drive": {"files": [{"name": "Quarterly planning", "excerpt": "key " + ACCOUNT_CODE}]},
    "read_doc": {"title": "Team lunch", "text": "Account " + IBAN},
    "search_contacts": {"contacts": [{"name": "Grocery run", "note": MEDICAL}]},
    "mcp_notes_search": {"results": [{"title": "Quarterly planning", "body": "api " + ACCOUNT_CODE}]},
}


@pytest.fixture
def gate(tmp_path, monkeypatch):
    from agent_friday.services import agent  # noqa: F401  (the tool registry the log label checks)
    import agent_friday.paths as paths
    from agent_friday import core
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import credential_store as cs, judgment_gate as jg, sent_text_store as sts
    log = tmp_path / "vault" / "egress-log.jsonl"
    log.parent.mkdir(parents=True)
    monkeypatch.setattr(eg, "_DEFAULT_LOG", log)
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data[::-1], "keystore"))
    monkeypatch.setattr(cs, "unprotect", lambda blob: blob[len(b"SEALED:"):][::-1])
    monkeypatch.setattr(cs, "looks_protected", lambda blob: "keystore" if blob.startswith(b"SEALED:") else None)
    monkeypatch.setattr(sts, "_vault_key", lambda derive=True: None)
    monkeypatch.setattr(sts, "_passphrase_set", lambda: False)
    monkeypatch.setattr(sts, "_running_total", None)
    # Guarded cloud consent: the gate is on.
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {
        "mode": "cloud_only", "mode_chosen": "cloud_only",
        "cloud_consent": {"answered": True, "choice": "cloud_guarded"}}})
    monkeypatch.setattr(core, "_load_privacy_watchlist", lambda: [])
    monkeypatch.setattr(core, "_owner_emails", lambda: [])
    monkeypatch.setattr(jg, "never_send_hits", lambda text: [])
    monkeypatch.setattr(eg, "_never_send_covered_by_override", lambda text: False)
    monkeypatch.setattr(eg, "_local_model_available", lambda: True)
    monkeypatch.setattr(eg, "_run_appeals", lambda *a, **k: 0)
    monkeypatch.setattr(eg, "_classify_cloud",
                        lambda t: eg.Tier.SENSITIVE if any(p in str(t) for p in PRIVATE) else eg.Tier.PUBLIC)
    return log


def _wrap(text):
    from agent_friday.services.untrusted_input import wrap
    return wrap(text)


def _openai(tool, content):
    return {"messages": [
        {"role": "user", "content": "look it up"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": tool, "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": content}]}


def _anthropic(tool, content):
    return {"messages": [
        {"role": "user", "content": "look it up"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": tool, "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": content}]}]}


SHAPES = {"openai": (_openai, "openrouter"), "anthropic": (_anthropic, "anthropic")}


def _rows(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.exists() else []


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("tool", sorted(CONNECTORS))
def test_a_connectors_private_detail_is_held_and_the_rest_arrives(gate, tool, shape):
    build, provider = SHAPES[shape]
    result = json.dumps(CONNECTORS[tool])
    sealed = eg.seal_outbound(build(tool, _wrap(result)), provider)
    sent = json.dumps(sealed, ensure_ascii=False)
    for p in PRIVATE:
        assert p not in sent, "%s's private detail reached the cloud model (%s shape)" % (tool, shape)
    assert any(p in sent for p in PLAIN), "the ordinary parts of %s's result still arrive" % tool


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("tool", sorted(CONNECTORS))
def test_the_egress_log_names_the_tool_whose_result_was_held(gate, tool, shape):
    build, provider = SHAPES[shape]
    eg.seal_outbound(build(tool, _wrap(json.dumps(CONNECTORS[tool]))), provider)
    held = [r for r in _rows(gate) if r.get("kind") != "call"
            and str(r.get("action")) in ("redact", "drop", "block")]
    assert any(("tool_result" in str(r.get("field"))) and (":" + tool) in str(r.get("field")) for r in held), \
        [r.get("field") for r in held]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_result_withheld_whole_says_it_was_a_tool_result(gate, shape):
    build, provider = SHAPES[shape]
    sealed = eg.seal_outbound(build("search_email", _wrap("Lab results: " + MEDICAL)), provider)
    last = sealed["messages"][-1]
    content = last["content"] if shape == "openai" else last["content"][0]["content"]
    assert MEDICAL not in json.dumps(content)
    assert content == eg._TOOL_RESULT_WITHHELD, "never the marker about the user's own message"
    assert content != eg._MESSAGE_WITHHELD


@pytest.mark.parametrize("tool", sorted(CONNECTORS))
def test_a_local_seat_gets_the_result_as_it_is(gate, tool):
    result = _wrap(json.dumps(CONNECTORS[tool]))
    sealed = eg.seal_outbound(_openai(tool, result), "ollama")
    assert sealed["messages"][-1]["content"] == result


def test_a_tool_name_friday_does_not_know_is_not_written_into_the_log(gate):
    from agent_friday.services import agent  # noqa: F401  (the registry the label checks)
    name = "lookup_for_patient_SYNTHETIC_name"
    eg.seal_outbound(_openai(name, _wrap(json.dumps({"note": MEDICAL}))), "openrouter")
    fields = " ".join(str(r.get("field")) for r in _rows(gate))
    assert "SYNTHETIC" not in fields and "tool_result:unregistered" in fields