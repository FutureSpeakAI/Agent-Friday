"""The egress ledger's call rows, the payload-size check, and keeping the
exact text only if the owner turns it on (north star §29.6, §26.7;
BD-15 slice P2).

By default a send leaves a fingerprint (SHA-256 of exactly what left), never
its words. Synthetic payloads only; nothing reaches a network.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from agent_friday.services import egress_gate as eg


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    import agent_friday.core as core
    log = tmp_path / "vault" / "egress-log.jsonl"
    log.parent.mkdir(parents=True)
    monkeypatch.setattr(eg, "_DEFAULT_LOG", log)
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    settings = {"privacy_keep_sent_text": False, "privacy_keep_sent_text_days": 30}
    monkeypatch.setattr(core, "_load_settings", lambda: dict(settings))
    monkeypatch.setattr(core, "_save_settings", lambda delta, **k: settings.update(delta))
    # The gate runs exactly as under recorded cloud consent (A14): every field
    # is still recorded and the never-send floor still applies, without a
    # classifier model in the test.
    from agent_friday.services import judgment_gate as jg
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    monkeypatch.setattr(jg, "never_send_hits", lambda text: [])
    monkeypatch.setattr(eg, "_never_send_covered_by_override", lambda text: False)
    return {"log": log, "settings": settings, "home": tmp_path}


def _rows(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def _calls(log):
    return [r for r in _rows(log) if r.get("kind") == "call"]


PAYLOAD = {"messages": [{"role": "user", "content": "What is the boiling point of water at sea level?"}]}


def _canonical_sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
                          .encode("utf-8")).hexdigest()


def test_every_sealed_send_leaves_one_call_row_with_the_29_6_fields(ledger):
    sealed = eg.seal_outbound(dict(PAYLOAD), "anthropic", model="synthetic-model-1")
    calls = _calls(ledger["log"])
    assert len(calls) == 1
    c = calls[0]
    for key in ("destination", "purpose", "manifest_id", "withheld", "grants", "payload_sha256",
                "ts", "model", "result", "bytes"):
        assert key in c, key
    assert c["model"] == "synthetic-model-1"
    assert c["payload_sha256"] == _canonical_sha(sealed), "the fingerprint is of exactly what left"
    assert c["result"] in ("sent", "sent_with_parts_withheld")
    fields = [r for r in _rows(ledger["log"]) if r.get("kind") != "call"]
    assert fields and all(r.get("call_id") == c["call_id"] for r in fields)


def test_by_default_the_words_are_not_kept(ledger):
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    assert c["text_kept"] is False
    assert "boiling point" not in ledger["log"].read_text(encoding="utf-8").split('"kind": "call"')[-1]
    assert not (ledger["home"] / "vault" / "sent-text").exists()


def test_the_exact_text_is_kept_encrypted_only_when_the_owner_turns_it_on(ledger, monkeypatch):
    from agent_friday.services import sent_text_store as sts
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data[::-1], "keystore"))
    monkeypatch.setattr(cs, "unprotect", lambda blob: blob[len(b"SEALED:"):][::-1])
    sts.set_keep(True)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    assert c["text_kept"] is True
    stored = (ledger["home"] / "vault" / "sent-text" / (c["payload_sha256"] + ".bin")).read_bytes()
    assert b"boiling point" not in stored, "kept text is never readable on disk"
    assert "boiling point" in sts.read(c["payload_sha256"])["text"]
    # Turning it off deletes what was kept.
    out = sts.set_keep(False)
    assert out["deleted"] == 1 and sts.read(c["payload_sha256"]) is None


def test_nothing_is_kept_if_encryption_is_not_available(ledger, monkeypatch):
    from agent_friday.services import sent_text_store as sts
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "protect", lambda data: (data, "plaintext"))
    sts.set_keep(True)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    assert _calls(ledger["log"])[0]["text_kept"] is False


def test_nothing_is_kept_off_the_record(ledger, monkeypatch):
    from agent_friday.services import sent_text_store as sts
    from agent_friday.services import credential_store as cs
    from agent_friday.services import off_record
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data, "keystore"))
    monkeypatch.setattr(off_record, "active", lambda *a, **k: True)
    sts.set_keep(True)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    assert c["text_kept"] is False and c["purpose"] == "off the record"


def test_a_payload_over_the_size_limit_is_refused_and_recorded(ledger, monkeypatch):
    monkeypatch.setattr(eg, "MAX_PAYLOAD_BYTES", 200)
    big = {"messages": [{"role": "user", "content": "water " * 100}]}
    with pytest.raises(RuntimeError):
        eg.seal_outbound(big, "anthropic")
    c = _calls(ledger["log"])[-1]
    assert c["result"] == "blocked" and "size" in c.get("note", "")
    assert any(r.get("action") == "block" and "limit" in r.get("reason", "") for r in _rows(ledger["log"]))


def test_a_withheld_field_is_counted_on_its_send(ledger, monkeypatch):
    real = eg._log

    def log_then_withhold(provider, field, tier, action, reason, log_path=None):
        return real(provider, field, tier, "redact" if field == "system" else action, reason, log_path)
    monkeypatch.setattr(eg, "_log", log_then_withhold)
    eg.seal_outbound({"system": "You are a synthetic assistant.", **PAYLOAD}, "anthropic")
    c = _calls(ledger["log"])[-1]
    assert c["withheld"] >= 1 and c["result"] == "sent_with_parts_withheld"


def test_a_single_gated_text_is_its_own_send(ledger):
    out = eg.gate_text("boiling point of water", "brave", field="query")
    c = _calls(ledger["log"])[-1]
    assert c["via"] == "gate_text:query"
    assert c["payload_sha256"] == hashlib.sha256(out.encode("utf-8")).hexdigest()


def test_a_self_test_probe_is_never_kept(ledger, monkeypatch, tmp_path):
    from agent_friday.services import sent_text_store as sts
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data, "keystore"))
    sts.set_keep(True)
    eg.seal_outbound(dict(PAYLOAD), "anthropic", log_path=tmp_path / "probe.jsonl")
    assert not list((ledger["home"] / "vault" / "sent-text").glob("*.bin")) \
        if (ledger["home"] / "vault" / "sent-text").exists() else True


def test_the_privacy_map_counts_sends_and_says_fingerprints_only(ledger):
    from agent_friday.services import privacy_map as pm
    eg.seal_outbound(dict(PAYLOAD), "anthropic", model="synthetic-model-1")
    view = pm.view()
    d = {x["to"]: x for x in view["left"]["destinations"]}["anthropic"]
    assert d["calls"] == 1 and d["texts_kept"] == 0 and d["models"] == ["synthetic-model-1"]
    assert view["sent_text"]["keep"] is False
    assert "fingerprint" in view["sent_text"]["explanation"]
    row = [r for r in pm.left_rows("anthropic")["rows"] if r.get("kind") == "call"][0]
    assert row["payload_sha256"] and row["purpose"] and "manifest_id" in row
