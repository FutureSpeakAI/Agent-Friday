"""Every supported cloud provider's own wire shape is sealed by the egress gate (NS-34.6-14).

tests/unit/test_egress_failures_and_providers.py proves the OpenAI-compatible providers are sealed
before their request is posted. Anthropic and Google do not use that wire shape: Anthropic sends
`system` plus `messages` with typed content blocks (text, tool_use, tool_result); Google sends
`systemInstruction` and `contents` made of `parts`. Here each of those shapes carries a private
detail in every place a prompt can sit, and none of it may come out of `seal_outbound` for the
cloud provider, while a local seat gets the payload as it is. Synthetic content only; nothing is
posted.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import egress_gate as eg

SSN = "123-45-6789"  # pragma: allowlist secret
PRIVATE = "my social security number is " + SSN + ", please keep it"


def _anthropic():
    return {
        "model": "claude-sonnet-5-5", "max_tokens": 64,
        "system": "You are Friday. " + PRIVATE,
        "messages": [
            {"role": "user", "content": PRIVATE},
            {"role": "assistant", "content": [
                {"type": "text", "text": PRIVATE},
                {"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {"path": "a.txt"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": PRIVATE},
                {"type": "text", "text": PRIVATE}]},
        ],
    }


def _google():
    return {
        "systemInstruction": {"parts": [{"text": "You are Friday. " + PRIVATE}]},
        "contents": [
            {"role": "user", "parts": [{"text": PRIVATE}]},
            {"role": "model", "parts": [{"text": PRIVATE}]},
            {"role": "user", "parts": [{"functionResponse": {
                "name": "read_file", "response": {"content": PRIVATE}}}]},
        ],
    }


SHAPES = [("anthropic", "claude-sonnet-5-5", _anthropic),
          ("google-gemini", "gemini-2.5-flash", _google)]


@pytest.mark.parametrize("provider,model,build", SHAPES, ids=[s[0] for s in SHAPES])
def test_a_private_detail_in_any_part_of_a_native_payload_does_not_leave(provider, model, build, tmp_path):
    assert eg._is_cloud(provider, model), "the test must be about a cloud destination"
    payload = build()
    assert SSN in json.dumps(payload), "the payload should carry the detail before it is sealed"
    sealed = eg.seal_outbound(payload, provider, log_path=tmp_path / "egress.jsonl", model=model)
    assert SSN not in json.dumps(sealed)


@pytest.mark.parametrize("provider,model,build", SHAPES, ids=[s[0] for s in SHAPES])
def test_the_hold_is_recorded_in_the_egress_ledger(provider, model, build, tmp_path):
    log = tmp_path / "egress.jsonl"
    eg.seal_outbound(build(), provider, log_path=log, model=model)
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows, "a sealed cloud send leaves a ledger row"
    assert SSN not in json.dumps(rows)


@pytest.mark.parametrize("provider,model,build", SHAPES, ids=[s[0] for s in SHAPES])
def test_the_shape_is_kept_so_the_provider_can_still_read_it(provider, model, build, tmp_path):
    sealed = eg.seal_outbound(build(), provider, log_path=tmp_path / "egress.jsonl", model=model)
    assert set(sealed) == set(build()), "sealing keeps every top-level field"
    key = "messages" if "messages" in sealed else "contents"
    assert len(sealed[key]) == len(build()[key])


def test_a_local_seat_gets_the_payload_as_it_is(tmp_path):
    payload = _anthropic()
    assert eg.seal_outbound(payload, "ollama", log_path=tmp_path / "egress.jsonl",
                            model="llama3.2") == payload
