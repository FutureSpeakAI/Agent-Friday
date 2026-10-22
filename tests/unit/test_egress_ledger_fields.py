"""The egress ledger's call rows, the payload-size check, and the full text of
what left kept with its receipt (north star §29.6, §26.7 as amended by A20;
BD-15 slice P2).

By default each send keeps the full content the gate released, encrypted at
rest (under the vault when a passphrase is set), and a keyed fingerprint. The
owner can switch to fingerprints only. Synthetic payloads only; nothing
reaches a network.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import egress_gate as eg

KEY = b"synthetic-governance-key-for-tests-0000000000000000"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import judgment_gate as jg
    from agent_friday.services import credential_store as cs
    log = tmp_path / "vault" / "egress-log.jsonl"
    log.parent.mkdir(parents=True)
    monkeypatch.setattr(eg, "_DEFAULT_LOG", log)
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    # Encryption stand-in: reversible, never the plain bytes.
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data[::-1], "keystore"))
    monkeypatch.setattr(cs, "unprotect", lambda blob: blob[len(b"SEALED:"):][::-1])
    monkeypatch.setattr(cs, "looks_protected", lambda blob: "keystore" if blob.startswith(b"SEALED:") else None)
    # No vault passphrase unless a test sets one (never derive a real key).
    from agent_friday.services import sent_text_store as sts
    monkeypatch.setattr(sts, "_vault_key", lambda derive=True: None)
    monkeypatch.setattr(sts, "_passphrase_set", lambda: False)
    monkeypatch.setattr(sts, "_running_total", None)
    # The gate runs exactly as under recorded cloud consent (A14): every field
    # is still recorded and the never-send floor still applies, without a
    # classifier model in the test.
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    monkeypatch.setattr(jg, "never_send_hits", lambda text: [])
    monkeypatch.setattr(eg, "_never_send_covered_by_override", lambda text: False)
    return {"log": log, "home": tmp_path}


def _rows(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def _calls(log):
    return [r for r in _rows(log) if r.get("kind") == "call"]


PAYLOAD = {"messages": [{"role": "user", "content": "What is the boiling point of water at sea level?"}]}


def test_every_sealed_send_leaves_one_call_row_with_the_29_6_fields(ledger):
    sealed = eg.seal_outbound(dict(PAYLOAD), "anthropic", model="synthetic-model-1")
    calls = _calls(ledger["log"])
    assert len(calls) == 1
    c = calls[0]
    for key in ("destination", "purpose", "manifest_id", "withheld", "grants", "payload_fingerprint",
                "ts", "model", "result", "bytes"):
        assert key in c, key
    assert c["model"] == "synthetic-model-1"
    assert c["payload_fingerprint"] == eg._fingerprint(sealed)[0], "the fingerprint is of what was released"
    assert c["fingerprint_alg"] == "hmac-sha256"
    assert c["result"] in ("sent", "sent_with_parts_withheld")
    fields = [r for r in _rows(ledger["log"]) if r.get("kind") != "call"]
    assert fields and all(r.get("call_id") == c["call_id"] for r in fields)


def test_the_fingerprint_is_keyed_so_a_guess_cannot_be_confirmed(ledger):
    import hashlib
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    plain = hashlib.sha256(eg._canonical(eg.seal_outbound(dict(PAYLOAD), "anthropic"))
                           .encode("utf-8")).hexdigest()
    assert c["payload_fingerprint"] != plain


def _kept_file(ledger, c):
    return ledger["home"] / "vault" / "sent-text" / (c["payload_fingerprint"] + ".bin")


def test_by_default_the_full_text_is_kept_encrypted_with_its_receipt(ledger):
    """A20: the owner's default is the full text, not a fingerprint."""
    from agent_friday.services import sent_text_store as sts
    assert sts.mode() == sts.FULL
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    assert c["text_kept"] is True
    assert "boiling point" not in json.dumps(c), "the ledger row holds the fingerprint, not the words"
    stored = _kept_file(ledger, c).read_bytes()
    assert b"boiling point" not in stored, "kept content is never readable on disk"
    assert "boiling point" in sts.read(c["payload_fingerprint"])["text"]


def test_with_a_vault_passphrase_the_text_is_sealed_under_the_vault(ledger, monkeypatch):
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import sent_text_store as sts
    key = bytes(range(32))
    monkeypatch.setattr(sts, "_vault_key", lambda derive=True: key)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    stored = _kept_file(ledger, c).read_bytes()
    assert stored.startswith(vc.MAGIC), "sealed with the vault key, not the key store"
    assert "boiling point" in json.loads(vc.decrypt(stored, key))["text"]
    assert sts.status()["sealed_with"] == "vault"
    assert "vault passphrase" in sts.status()["words"]


def test_without_a_vault_passphrase_the_map_says_it_is_the_key_store(ledger):
    from agent_friday.services import sent_text_store as sts
    st = sts.status()
    assert st["mode"] == "full" and st["sealed_with"] == "keystore"
    assert "key store" in st["words"] and "Vault" in st["words"]


def test_switching_to_fingerprints_deletes_what_was_kept_and_keeps_no_more(ledger):
    from agent_friday.services import sent_text_store as sts
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    first = _calls(ledger["log"])[0]
    out = sts.set_mode(sts.FINGERPRINT)
    assert out["deleted"] == 1 and out["mode"] == "fingerprint"
    assert sts.read(first["payload_fingerprint"]) is None
    eg.seal_outbound({"messages": [{"role": "user", "content": "A different synthetic question."}]}, "anthropic")
    second = _calls(ledger["log"])[-1]
    assert second["text_kept"] is False and second["payload_fingerprint"]
    assert not list((ledger["home"] / "vault" / "sent-text").glob("*.bin"))
    assert sts.set_mode(sts.FULL)["mode"] == "full"


def test_delete_kept_removes_the_text_but_leaves_the_mode(ledger):
    from agent_friday.services import sent_text_store as sts
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    out = sts.delete_kept()
    assert out["deleted"] == 1 and out["mode"] == "full"


def test_an_edited_or_unsigned_choice_reads_as_full(ledger):
    """Only the owner's signed choice can make Friday keep less."""
    from agent_friday.services import sent_text_store as sts
    sts.set_mode(sts.FINGERPRINT)
    p = ledger["home"] / "vault" / "sent-text-choice.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["at"] = 1.0
    p.write_text(json.dumps(data), encoding="utf-8")
    assert sts.mode() == sts.FULL
    p.write_text(json.dumps({"mode": "fingerprint"}), encoding="utf-8")
    assert sts.mode() == sts.FULL


def test_the_choice_is_not_a_settings_key(ledger, monkeypatch):
    """A write to settings.json (the generic settings route, a program, a tool)
    can't change the mode."""
    import agent_friday.core as core
    from agent_friday.services import sent_text_store as sts
    monkeypatch.setattr(core, "_load_settings", lambda: {"privacy_sent_text_mode": "fingerprint"})
    assert sts.mode() == sts.FULL
    assert not any("sent_text" in k for k in core.DEFAULT_SETTINGS)


def test_the_kept_text_is_included_in_export_all_my_data(ledger):
    from pathlib import PurePath
    from agent_friday.services import data_export
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    assert data_export.skip_reason(PurePath("vault", "sent-text", c["payload_fingerprint"] + ".bin")) is None


def test_nothing_is_kept_if_encryption_is_not_available(ledger, monkeypatch):
    from agent_friday.services import sent_text_store as sts
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "protect", lambda data: (data, "plaintext"))
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    row = _calls(ledger["log"])[0]
    assert row["text_kept"] is False
    assert not list((ledger["home"] / "vault" / "sent-text").glob("*.bin")) \
        if (ledger["home"] / "vault" / "sent-text").exists() else True
    # The map reads state only (it never probes by encrypting), so it reports
    # the key store it expects; the receipt itself says nothing was kept.
    assert sts.read(row["payload_fingerprint"]) is None


def test_a_key_store_that_refuses_keeps_nothing(ledger, monkeypatch):
    from agent_friday.services import credential_store as cs

    def refuse(data):
        raise RuntimeError("synthetic: no way to encrypt")
    monkeypatch.setattr(cs, "protect", refuse)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    assert _calls(ledger["log"])[0]["text_kept"] is False


def test_off_the_record_no_fingerprint_and_nothing_kept(ledger, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda *a, **k: True)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    assert c["text_kept"] is False and c["purpose"] == "off the record"
    assert "payload_fingerprint" not in c
    assert not (ledger["home"] / "vault" / "sent-text").exists()


def test_a_payload_over_the_size_limit_is_refused_and_recorded(ledger, monkeypatch):
    monkeypatch.setattr(eg, "MAX_PAYLOAD_BYTES", 200)
    big = {"messages": [{"role": "user", "content": "water " * 100}]}
    with pytest.raises(RuntimeError):
        eg.seal_outbound(big, "anthropic")
    c = _calls(ledger["log"])[-1]
    assert c["result"] == "blocked" and "size" in c.get("note", "")
    assert any(r.get("action") == "block" and "limit" in r.get("reason", "") for r in _rows(ledger["log"]))
    with pytest.raises(RuntimeError):
        eg.gate_text("water " * 100, "brave", field="query")


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
    assert c["payload_fingerprint"] == eg._fingerprint(out)[0]


def test_a_field_gated_on_its_own_is_recorded_as_a_send(ledger):
    """Tools that gate one field directly (a search query, a voice line, a
    calendar write) leave a call row too, not only field rows."""
    eg._gate_text("boiling point of water", "brave", "query")
    assert [c["via"] for c in _calls(ledger["log"])] == ["field:query"]


def test_a_tool_result_in_the_data_envelope_is_one_recorded_send(ledger):
    """A tool result reaches a model wrapped as data (services/untrusted_input). Gated
    on its own it is still a send with its own call row: the envelope's contents,
    judged and recorded, then wrapped again."""
    from agent_friday.services import untrusted_input as ui
    wrapped = ui.wrap(ui.wrap("boiling point of water"))
    out = eg._gate_text(wrapped, "brave", "tool_result")
    assert [c["via"] for c in _calls(ledger["log"])] == ["field:tool_result"]
    inner, depth = ui.unwrap_all(out)
    assert depth == 1 and inner == "boiling point of water"
    assert _calls(ledger["log"])[-1]["payload_fingerprint"] == eg._fingerprint(inner)[0]


def test_a_worker_payload_is_one_send_not_two(ledger, monkeypatch):
    monkeypatch.setattr(eg, "gate_operational", lambda: True)
    eg.gate_worker_payload({"model": "remote-model", "prompt": "boiling point of water",
                            "messages": PAYLOAD["messages"]},
                           base_url="https://synthetic.example", provider="synthetic-remote")
    assert len(_calls(ledger["log"])) == 1


def test_a_self_test_probe_is_never_kept(ledger, tmp_path):
    eg.seal_outbound(dict(PAYLOAD), "anthropic", log_path=tmp_path / "probe.jsonl")
    kept = ledger["home"] / "vault" / "sent-text"
    assert not (kept.exists() and list(kept.glob("*.bin")))


def test_kept_content_has_a_total_cap_oldest_first(ledger, monkeypatch):
    from agent_friday.services import sent_text_store as sts
    monkeypatch.setattr(sts, "MAX_TOTAL_BYTES", 1)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    sts.prune()
    assert not list((ledger["home"] / "vault" / "sent-text").glob("*.bin"))


def test_the_privacy_map_counts_sends_and_states_which_mode_is_on(ledger):
    from agent_friday.services import privacy_map as pm
    from agent_friday.services import sent_text_store as sts
    eg.seal_outbound(dict(PAYLOAD), "anthropic", model="synthetic-model-1")
    view = pm.view()
    d = {x["to"]: x for x in view["left"]["destinations"]}["anthropic"]
    assert d["calls"] == 1 and d["texts_kept"] == 1 and d["models"] == ["synthetic-model-1"]
    assert view["sent_text"]["mode"] == "full" and "full text" in view["sent_text"]["words"]
    store = {s["key"]: s for s in view["stores"]}["sent_text"]
    assert "until you delete" in store["retention_words"]
    sts.set_mode(sts.FINGERPRINT)
    view = pm.view()
    assert view["sent_text"]["mode"] == "fingerprint"
    assert view["sent_text"]["words"] == "Only fingerprints are kept."
    row = [r for r in pm.left_rows("anthropic")["rows"] if r.get("kind") == "call"][0]
    assert row["payload_fingerprint"] and row["purpose"] and "manifest_id" in row


# ── second review (A20) ───────────────────────────────────────────────────────

def test_after_a_delete_the_map_no_longer_says_the_text_is_kept(ledger):
    from agent_friday.services import privacy_map as pm
    from agent_friday.services import sent_text_store as sts
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    assert {x["to"]: x for x in pm.view()["left"]["destinations"]}["anthropic"]["texts_kept"] == 1
    sts.delete_kept()
    assert {x["to"]: x for x in pm.view()["left"]["destinations"]}["anthropic"]["texts_kept"] == 0
    row = [r for r in pm.left_rows("anthropic")["rows"] if r.get("kind") == "call"][0]
    assert row["text_kept"] is True and row["still_kept"] is False


def test_a_planted_unsealed_file_is_never_shown_as_what_was_sent(ledger):
    from agent_friday.services import sent_text_store as sts
    fp = "d" * 64
    d = ledger["home"] / "vault" / "sent-text"
    d.mkdir(parents=True, exist_ok=True)
    (d / (fp + ".bin")).write_bytes(json.dumps({"text": "forged words"}).encode("utf-8"))
    assert sts.read(fp) is None


def test_fridays_tools_cannot_touch_the_kept_text_or_the_choice(ledger):
    import agent_friday.core as core
    from pathlib import Path
    from agent_friday.services import credential_paths as cp
    f = Path(core.FRIDAY_DIR)
    assert cp.check(f / "vault" / "sent-text" / ("e" * 64 + ".bin"))
    assert cp.check(f / "vault" / "sent-text-choice.json")


def test_reading_the_map_never_derives_the_vault_key(ledger, monkeypatch):
    from agent_friday.services import privacy_map as pm
    from agent_friday.services import sent_text_store as sts
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    derived = []
    monkeypatch.setattr(sts, "_vault_key", lambda derive=True: derived.append(derive))
    pm.view()
    sts.status()
    sts.read(c["payload_fingerprint"])
    assert True not in derived, "a read path derived the vault key"


def test_a_passphrase_whose_key_fails_keeps_nothing_and_says_so(ledger, monkeypatch):
    import sys
    import types
    from agent_friday.services import sent_text_store as sts
    monkeypatch.setattr(sts, "_passphrase_set", lambda: True)
    monkeypatch.setitem(sys.modules, "agent_friday.services.agent",
                        types.SimpleNamespace(_VAULT_KEY_READY=True, _VAULT_KEY=None))
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    assert _calls(ledger["log"])[0]["text_kept"] is False, "fell back to a weaker seal than the owner set up"
    st = sts.status()
    assert st["sealed_with"] is None and "couldn't be opened" in st["words"]


def test_when_off_the_record_cant_be_told_no_fingerprint_is_written(ledger, monkeypatch):
    from agent_friday.services import off_record

    def boom(*a, **k):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(off_record, "active", boom)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    c = _calls(ledger["log"])[0]
    assert "payload_fingerprint" not in c and c["text_kept"] is False


def test_the_total_cap_holds_without_waiting_for_a_full_scan(ledger, monkeypatch):
    from agent_friday.services import sent_text_store as sts
    monkeypatch.setattr(sts, "MAX_TOTAL_BYTES", 1)
    eg.seal_outbound(dict(PAYLOAD), "anthropic")
    assert not list((ledger["home"] / "vault" / "sent-text").glob("*.bin"))
