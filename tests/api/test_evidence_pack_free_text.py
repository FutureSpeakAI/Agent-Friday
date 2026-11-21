"""#868 critic HIGH, decision (c): the pack never holds an owner's text, because the ledger never holds it.

The earlier pack refused only a few key names (CONTENT_KEYS). Real kinds the export reads carry owner text under other
keys: the action journal's summary and items (file paths), a goal receipt's what_ran / critique / artifacts, the media
diet's removed titles. The CANARY below goes in through the REAL writers; the pack must hold none of it, must still verify
its chain, and an entry written before the change (raw text on disk) must be refused in words, not exported."""
import json
import time

from agent_friday.services import action_journal
from agent_friday.services import action_ledger as L
from agent_friday.services import ledger_text
from tests.api.test_evidence_pack_route import _export, _files, _seed

CANARY = "CANARY-7c0e D:/work/private plans.txt"


def _write_text_through_the_real_writers():
    action_journal.record("write_file", "create", "files", items=[{"path": CANARY, "kind": "file"}],
                          summary="Created " + CANARY, status="done")
    L.append("goal", {"goal_id": "g1", "what_ran": CANARY, "verified": {"critique": CANARY, "ok": True},
                      "artifacts": [CANARY], "note": CANARY})
    L.append("media_diet", {"removed": [CANARY], "kept": 2, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})


def test_text_written_through_the_real_writers_never_reaches_the_pack(client):
    _seed()
    _write_text_through_the_real_writers()
    r = _export(client)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    files = _files(r)
    assert any(b"write_file" in data for data in files.values()), "the entries are in the pack, or this proves nothing"
    leaked = sorted(name for name, data in files.items() if b"CANARY-7c0e" in data or b"private plans" in data)
    assert leaked == [], f"owner text reached the pack: {leaked}"


def test_the_pack_still_verifies_its_chain_with_the_text_hidden(client):
    _seed()
    _write_text_through_the_real_writers()
    files = _files(_export(client))
    manifest = json.loads(files["manifest.json"])
    assert manifest["ledger_verified"] is True
    from agent_friday.services import evidence_pack_checker as checker
    problems, _ = checker.check_files(files, manifest["public_key"])
    assert problems == []


def test_the_text_is_still_there_for_this_pcs_own_readers(client):
    _write_text_through_the_real_writers()
    rows = [e for e in L.entries(["action"]) if e["rec"].get("tool") == "write_file"]
    assert rows and rows[0]["rec"]["summary"] == "Created " + CANARY and rows[0]["rec"]["items"][0]["path"] == CANARY


def test_an_entry_written_before_hiding_existed_is_refused_in_words_not_exported(client, monkeypatch):
    _seed()
    # the old writer: text on disk as it was
    with monkeypatch.context() as old_writer:  # a scoped patch: undo() would also undo the client fixture
        old_writer.setattr(ledger_text, "hide", lambda rec, digest: (rec, {}))
        action_journal.record("write_file", "create", "files", items=[{"path": CANARY}], summary="Created " + CANARY,
                              status="done")
    r = _export(client)
    assert r.status_code == 409 and r.get_json()["code"] == "pack_refused"
    assert "written before free text was kept as a hash" in r.get_json()["error"]
    assert "CANARY-7c0e" not in r.get_data(as_text=True)


def test_the_owners_own_ledger_export_holds_the_lines_as_written_not_the_text(client):
    from agent_friday import core
    _write_text_through_the_real_writers()
    r = client.get("/api/ledger/export", headers={"X-Friday-Token": core._current_api_token()})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "CANARY-7c0e" not in body and "ft1:" in body, "whole lines for checking elsewhere: hashes, never the text"
