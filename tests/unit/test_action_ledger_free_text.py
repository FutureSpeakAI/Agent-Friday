"""The ledger writes free text as a keyed hash and keeps the text in a local sidecar. TEST CREDITS ONLY.

What each check bites on (the rules are in services/ledger_text.py and action_ledger.append / entries):
  1. the bytes on disk (the live file) hold no owner text, and what is there is the hidden form;
  2. this PC's readers get the text back: entries() resolves it, `where` predicates see it;
  3. resolve_text=False gives the lines exactly as written, and entry_ok / the chain hold for both forms;
  4. the same text under the same key hides to the same value (so equal text can be matched without reading it), and a
     different key (another device) gives a different value;
  5. a ledger restored without its sidecar shows the hash and never raises;
  6. text under keys that are not free text (ids, tool names, statuses) is kept as written.
The check is shown able to fail: with ledger_text.hide replaced by the identity (the old writer), the canary is on disk.
"""
import json

from agent_friday.services import action_ledger as L
from agent_friday.services import ledger_text

CANARY = "CANARY-31ab D:/work/Taxes 2026.xlsx"


def _live_bytes():
    return (L.ledger_dir() / L.LIVE_NAME).read_bytes()


def _write():
    return L.append("action", {"tool": "write_file", "status": "done", "summary": "Created " + CANARY,
                               "items": [{"path": CANARY, "kind": "file"}]})


def test_the_bytes_on_disk_hold_no_owner_text_only_the_hidden_form():
    _write()
    raw = _live_bytes()
    assert b"CANARY-31ab" not in raw and b"Taxes" not in raw
    assert b"ft1:" in raw and b'"tool":"write_file"' in raw and b'"status":"done"' in raw


def test_this_pcs_readers_get_the_text_back_and_where_sees_it():
    _write()
    rows = L.entries(["action"], where=lambda e: CANARY in json.dumps(e["rec"]))
    assert len(rows) == 1 and rows[0]["rec"]["summary"] == "Created " + CANARY
    assert rows[0]["rec"]["items"][0]["path"] == CANARY
    assert [r["summary"] for r in L.records("action")] == ["Created " + CANARY]


def test_unresolved_entries_are_the_lines_as_written_and_both_forms_pass_entry_ok_and_the_chain():
    _write()
    key = L._key()
    shown = L.entries(["action"])[0]
    raw = L.entries(["action"], resolve_text=False)[0]
    assert "CANARY-31ab" in json.dumps(shown["rec"]) and "CANARY-31ab" not in json.dumps(raw["rec"])
    assert L.entry_ok(shown, key) and L.entry_ok(raw, key)
    assert shown["hash"] == raw["hash"] and L.verify(force=True)["ok"] is True


def test_the_same_text_hides_to_the_same_value_and_another_key_gives_another():
    key = L._key()
    assert L._text_digest(key, CANARY) == L._text_digest(key, CANARY)
    assert L._text_digest(key, CANARY) != L._text_digest(b"another device's key" * 2, CANARY)
    assert L._text_digest(key, CANARY) != L._text_digest(key, CANARY + " ")


def test_a_ledger_without_its_sidecar_shows_the_hash_and_never_raises():
    _write()
    (L.ledger_dir() / L.TEXTS_NAME).unlink()
    L._TEXTS.clear()
    rec = L.entries(["action"])[0]["rec"]
    assert ledger_text.is_hidden(rec["summary"]) and ledger_text.is_hidden(rec["items"][0]["path"])


def test_ids_tools_and_statuses_are_kept_as_written():
    L.append("decision", {"action_id": "act-xyz", "tool": "send_email", "status": "allowed", "label": "work"})
    rec = L.entries(["decision"])[0]["rec"]
    assert rec == {"action_id": "act-xyz", "tool": "send_email", "status": "allowed", "label": "work"}


def test_the_check_can_fail_with_the_old_writer_the_canary_is_on_disk(monkeypatch):
    monkeypatch.setattr(ledger_text, "hide", lambda rec, digest: (rec, {}))
    _write()
    assert b"CANARY-31ab" in _live_bytes(), "hide() is what keeps the text off the disk"


def test_a_line_that_carries_a_raw_rec_key_is_a_tampered_line_not_a_shown_entry():
    # critic A on 92579f1ae: a stored "_raw_rec" must never stand in for the rec that was hashed
    _write()
    live = L.ledger_dir() / L.LIVE_NAME
    line = json.loads(live.read_text(encoding="utf-8").splitlines()[-1])
    line["_raw_rec"] = dict(line["rec"])
    line["rec"] = {"tool": "forged", "status": "done"}
    live.write_bytes(json.dumps(line, separators=(",", ":")).encode("utf-8") + b"\n")
    assert L.entry_ok(line, L._key()) is False
    assert L.verify(force=True)["ok"] is False
    assert all(e["rec"].get("tool") != "forged" for e in L.entries(["action"], verified=True))


def test_other_text_carrying_keys_are_hidden_target_detail_and_trust_items():
    L.append("decision", {"action_id": "act-1", "tool": "send_email", "target": "to " + CANARY, "decision": "allow"})
    L.append("outcome", {"action_id": "act-1", "tool": "send_email", "detail": "failed " + CANARY})
    L.append("goal", {"goal_id": "g1", "item": CANARY, "why": "because " + CANARY,
                                           "items": {"host": CANARY}})
    assert b"CANARY-31ab" not in _live_bytes() and b"Taxes" not in _live_bytes()
    assert b'"decision":"allow"' in _live_bytes()
    shown = L.entries(["goal"])[0]["rec"]
    assert shown["item"] == CANARY and shown["items"] == {"host": CANARY}, "this PC's readers see the text"


def test_a_sidecar_that_cannot_be_written_does_not_lose_the_receipt(monkeypatch):
    def refuse(d, texts):
        raise OSError("texts.jsonl is locked")
    monkeypatch.setattr(L, "_remember_texts", refuse)
    entry = _write()
    assert entry is not None and L.verify(force=True)["ok"] is True
    assert b"CANARY-31ab" not in _live_bytes(), "no text on disk either: it shows as the hash"


def test_losing_the_sidecar_never_brings_released_trust_back():
    # critic on 4b079dd8a: trust_anchor._read matches the released item by its text; a record a governance decision is
    # read from is kept as written, so deleting texts.jsonl cannot change what is trusted
    from agent_friday.governance import trust_anchor
    host = "https://mcp.example.com/mcp"
    L.append(trust_anchor.KIND, {"list": "hosts", "items": {host: True, "https://other.example.net/mcp": True},
                                 "made_by": "carried_over"})
    L.append(trust_anchor.RELEASE, {"list": "hosts", "item": host, "why": "owner removed it"})
    assert b"ft1:" not in _live_bytes().split(b"trust_carry_over_released")[-1], "kept as written"
    sidecar = L.ledger_dir() / L.TEXTS_NAME
    if sidecar.exists():
        sidecar.unlink()
    L._TEXTS.clear()
    carried = trust_anchor._read("hosts")
    assert host not in carried["items"] and "https://other.example.net/mcp" in carried["items"]


def test_losing_the_sidecar_never_resets_the_owners_never_to_ask_again():
    # critic on 301e393a9: doctor_weekly reads the owner's answer from the ledger by value (weekly / not_now / never);
    # the answer is one of a fixed set, so it is structure and stays as written
    from agent_friday.services import doctor_weekly
    L.append("doctor_weekly", {"event": "answer", "answer": "never", "local_only_too": False}, durable=True)
    assert b"ft1:" not in _live_bytes()
    sidecar = L.ledger_dir() / L.TEXTS_NAME
    if sidecar.exists():
        sidecar.unlink()
    L._TEXTS.clear()
    assert doctor_weekly._answer_on_record()["answer"] == "never"


def test_append_hands_back_the_entry_as_the_caller_wrote_it():
    # CI on 2ac67421c: action_gate signs its decision (hmac over the text) and returns what append() gave back;
    # a hidden rec made verify_receipt fail for the receipt the caller held (test_artifact_manifest_fields)
    e = _write()
    assert e["rec"]["summary"] == "Created " + CANARY and e["rec"]["items"][0]["path"] == CANARY
    assert L.entry_ok(e, L._key()), "the returned entry still hashes as the line that was written"
    assert b"CANARY-31ab" not in _live_bytes()


def test_grant_recipients_are_hidden_and_a_lost_sidecar_never_unrevokes_a_grant():
    # critic's residual on 2ac67421c: send_grants writes recipients (contact addresses) through keyword arguments, which the
    # literal-dict key guard cannot see; the revocation reader matches grant_id and event only
    from agent_friday.governance import send_grants
    addr = "canary-9f2a@example.test"
    send_grants._log("used", grant_id="g-1", by="friday", action="send", recipients=[addr])
    send_grants._log("revoked", grant_id="g-1", by="owner")
    assert b"canary-9f2a" not in _live_bytes()
    sidecar = L.ledger_dir() / L.TEXTS_NAME
    if sidecar.exists():
        sidecar.unlink()
    L._TEXTS.clear()
    assert "g-1" in send_grants._revoked_in_log()


def test_a_refused_skill_grant_does_not_put_its_addresses_on_the_ledger():
    # critic on cdf91da9c: send_grants' "refused" row writes seen= and declared= (the same addresses) under other keys
    from agent_friday.governance import send_grants
    addr = "canary-4c7d@example.test"
    send_grants._log("refused", by="owner", skill="weekly-report", seen=[addr], declared=[addr, "other@example.test"])
    assert b"canary-4c7d" not in _live_bytes() and b"other@example" not in _live_bytes()
