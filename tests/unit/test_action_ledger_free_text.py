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
