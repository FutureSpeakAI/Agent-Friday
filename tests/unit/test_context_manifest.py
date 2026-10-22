"""Every send that leaves through the gate gets a context manifest: what went
into it by name, size and class, never its content (north star §12.5, §14.3,
§26.7; BD-15 slice P3). Synthetic payloads only; nothing reaches a network.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import egress_gate as eg

KEY = b"synthetic-governance-key-for-tests-0000000000000000"
PAYLOAD = {"system": "You are a synthetic assistant.",
           "messages": [{"role": "user", "content": "What is the boiling point of water at sea level?"},
                        {"role": "assistant", "content": "Synthetic answer."}]}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import credential_store as cs
    from agent_friday.services import judgment_gate as jg
    from agent_friday.services import sent_text_store as sts
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
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    monkeypatch.setattr(jg, "never_send_hits", lambda text: [])
    monkeypatch.setattr(eg, "_never_send_covered_by_override", lambda text: False)
    return {"log": log, "manifests": log.with_name("context-manifests.jsonl"), "home": tmp_path}


def _calls(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if '"kind": "call"' in x]


def _manifests(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def test_every_send_has_a_manifest_under_its_rows_id(ledger):
    from agent_friday.services import context_manifest as cm
    eg.seal_outbound(dict(PAYLOAD), "anthropic", model="synthetic-model-1")
    row = _calls(ledger["log"])[0]
    m = cm.read(row["manifest_id"])
    assert m is not None, "the send has no manifest"
    assert m["manifest_id"] == row["manifest_id"] == "cm-" + row["call_id"]
    for key in ("sources", "principal", "sensitivity_classes", "redactions", "grants", "destination",
                "purpose", "estimated_tokens", "previewed"):
        assert key in m, key
    parts = {s["part"] for s in m["sources"]}
    assert "message[].content" in parts, parts
    assert m["estimated_tokens"] == (row["bytes"] + 3) // 4
    assert m["previewed"] is False and "preview" in m["previewed_words"]
    assert "boiling point" not in json.dumps(m), "a manifest never holds content"


def test_each_assembly_has_its_own_manifest_and_the_turn_lists_them(ledger):
    from agent_friday.services import context_manifest as cm
    from agent_friday.services import turn_plan as tp
    from agent_friday.services import turn_receipt as tr
    tp.begin_turn("turn-synthetic-1")
    try:
        eg.seal_outbound(dict(PAYLOAD), "anthropic")
        eg.seal_outbound({"messages": [{"role": "user", "content": "A second synthetic call."}]}, "anthropic")
    finally:
        tp.begin_turn(None)
    ids = [r["manifest_id"] for r in _calls(ledger["log"])]
    assert len(set(ids)) == 2, "two assemblies share one manifest"
    listed = [m["manifest_id"] for m in cm.for_turn("turn-synthetic-1")]
    assert listed == ids
    receipt = tr.build("turn-synthetic-1")
    assert [m["manifest_id"] for m in receipt.get("manifests", [])] == ids
    assert receipt["manifests"][0]["open"] == "/api/privacy/manifest/" + ids[0]


def test_a_withheld_part_is_named_in_the_manifest(ledger, monkeypatch):
    from agent_friday.services import context_manifest as cm
    real = eg._log

    def log_then_withhold(provider, field, tier, action, reason, log_path=None):
        return real(provider, field, tier, "redact" if field == "system" else action, reason, log_path)
    monkeypatch.setattr(eg, "_log", log_then_withhold)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    m = cm.read(_calls(ledger["log"])[0]["manifest_id"])
    assert m["withheld"] >= 1
    assert any(r["part"] == "system" and r["action"] == "redact" for r in m["redactions"])


def test_off_the_record_the_manifest_keeps_counts_only(ledger, monkeypatch):
    from agent_friday.services import context_manifest as cm
    from agent_friday.services import off_record
    from agent_friday.services import turn_plan as tp
    monkeypatch.setattr(off_record, "active", lambda *a, **k: True)
    tp.begin_turn("turn-synthetic-off")
    try:
        eg.seal_outbound(dict(PAYLOAD), "anthropic")
    finally:
        tp.begin_turn(None)
    m = _manifests(ledger["manifests"])[-1]
    assert m["off_record"] is True
    assert m["sources"] and all("part" not in s for s in m["sources"]), "off the record parts are not named"
    assert "boiling point" not in json.dumps(m)
    opened = cm.read(m["manifest_id"])
    assert opened is not None and "assembled_sections" not in opened


def test_a_self_test_probe_never_writes_a_manifest_anywhere(ledger, tmp_path, monkeypatch):
    import os
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    eg.seal_outbound(dict(PAYLOAD), "anthropic", log_path=tmp_path / "probe.jsonl")
    eg.seal_outbound(dict(PAYLOAD), "anthropic", log_path=Path(os.devnull))
    assert _manifests(ledger["manifests"]) == []
    assert not list(tmp_path.glob("*manifests*")), "a probe left a manifest file behind"


@pytest.mark.parametrize("field,want", [
    ("messages[3].content", "messages[].content"),
    ("messages[2].content[0].tool_result.jane.doe@example.com.size", "messages[].content[].tool_result.{…}"),
    ("tools[0].input.secret_project_name", "tools[].input.{…}"),
    ("files.Quarterly plan v2.docx", "files.{…}"),
])
def test_a_part_name_never_carries_data(field, want):
    from agent_friday.services import context_manifest as cm
    assert cm.part_name(field) == want


def test_a_send_nested_inside_another_does_not_claim_the_turns_prompt(ledger):
    from agent_friday.services import context_manifest as cm
    from agent_friday.services import turn_plan as tp
    tp.begin_turn("turn-synthetic-nested")
    try:
        outer = eg._open_call("anthropic", "synthetic-model-1", "seal")
        inner = eg._open_call("anthropic", "synthetic-judge", "seal")
        eg._close_call(inner, {"messages": [{"role": "user", "content": "judge this"}]}, result="sent")
        eg._close_call(outer, dict(PAYLOAD), result="sent")
    finally:
        tp.begin_turn(None)
    by_model = {r["model"]: r for r in _calls(ledger["log"])}
    judge = cm.read(by_model["synthetic-judge"]["manifest_id"])
    assert judge["nested"] is True and "assembled_sections" not in judge
    assert "assembled_sections" in cm.read(by_model["synthetic-model-1"]["manifest_id"])


def test_a_send_never_reads_the_retrieval_ledger(ledger, monkeypatch):
    """The manifest is built before every send: it must stay cheap."""
    from agent_friday.services import turn_plan as tp
    from agent_friday.services import turn_receipt as tr

    def slow(*a, **k):
        raise AssertionError("the send path read the retrieval ledger")
    monkeypatch.setattr(tr, "_sections", slow)
    tp.begin_turn("turn-synthetic-cheap")
    try:
        eg.seal_outbound(dict(PAYLOAD), "anthropic")
    finally:
        tp.begin_turn(None)
    assert _manifests(ledger["manifests"])


@pytest.mark.parametrize("bad", ["../../secret", "cm-", "eg-123", "cm-" + "a" * 200, 'cm-a"b'])
def test_only_a_manifest_id_opens_a_manifest(ledger, bad):
    from agent_friday.services import context_manifest as cm
    assert cm.read(bad) is None
