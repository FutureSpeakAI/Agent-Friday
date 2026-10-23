"""The artifact manifest (north star §34.12-2): each file a creating tool
makes is linked to its task, turn and the receipt that allowed the call,
with its hash and structural check, in a signed line; a file is never
silently replaced by save_output. Synthetic files and keys only."""
from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile

import pytest

KEY = b"synthetic-governance-key-for-tests-0000000000000000"


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import off_record
    from agent_friday.governance import action_gate as ag
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(ag, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    return tmp_path


def _allow(tool, args, task_id="task-1", decision="allow"):
    from agent_friday.governance import action_gate as ag
    ag._receipt({"tool": tool, "class": "internal", "decision": decision, "reason": "", "task_id": task_id,
                 "args_hash": hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest(),
                 "turn_id": None})


def test_a_written_file_is_linked_to_its_task_and_the_receipt_that_allowed_it(home):
    from agent_friday.services import artifact_manifest as am
    p = home / "notes.md"
    args = {"path": str(p)}
    _allow("write_file", {"path": "elsewhere"})          # an older, different call
    _allow("write_file", args)
    p.write_text("hello", encoding="utf-8")
    lines = am.record_call("write_file", args, "Wrote 5 chars to %s" % p, task_id="task-1",
                           started_at=time.time() - 1)
    assert len(lines) == 1
    line = lines[0]
    assert line["path"] == str(p.resolve()) and line["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert line["tool"] == "write_file" and line["task_id"] == "task-1"
    assert am.verify(line)
    receipt = am.receipt_of(line)
    assert receipt and receipt["args_hash"] == hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    found = am.lookup(p)
    assert found and found["hmac"] == line["hmac"] and found["verified"]


def test_a_changed_line_does_not_verify_and_a_changed_file_is_not_found(home):
    from agent_friday.services import artifact_manifest as am
    p = home / "a.md"
    p.write_text("one", encoding="utf-8")
    line = am.record_call("write_file", {"path": str(p)}, "Wrote 3 chars", started_at=time.time() - 1)[0]
    forged = dict(line, path=str(home / "other.md"))
    assert not am.verify(forged)
    p.write_text("two, edited by hand", encoding="utf-8")
    assert am.lookup(p) is None          # the bytes no longer match what Friday made


def test_only_a_call_that_went_through_and_touched_the_file_counts(home, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    from agent_friday.services import off_record
    p = home / "b.md"
    p.write_text("x", encoding="utf-8")
    old = time.time() - 3600
    os.utime(p, (old, old))
    assert am.record_call("write_file", {"path": str(p)}, "Wrote 1 chars", started_at=time.time()) == []
    assert am.record_call("write_file", {"path": str(p)}, "Write error: denied", started_at=old - 5) == []
    assert am.record_call("write_file", {"path": str(p)}, "Wrote 1 chars", started_at=old - 5, ok=False) == []
    assert am.record_call("read_file", {"path": str(p)}, "x", started_at=old - 5) == []
    monkeypatch.setattr(off_record, "active", lambda *a, **k: True)
    assert am.record_call("write_file", {"path": str(p)}, "Wrote 1 chars", started_at=old - 5) == []
    assert not am.manifest_path().exists()


def test_an_office_document_is_resolved_checked_and_recorded(home, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    from agent_friday.services import office_engine as oe
    docs = home / "documents"
    monkeypatch.setattr(oe, "DOCUMENTS_DIR", docs)
    docs.mkdir()
    with zipfile.ZipFile(docs / "deck.pptx", "w") as z:
        z.writestr("ppt/presentation.xml", "<p:presentation xmlns:p='http://schemas.openxmlformats.org/"
                                           "presentationml/2006/main'/>")
    _allow("office", {"command": "create deck.pptx"})
    lines = am.record_call("office", {"command": "create deck.pptx"}, "done.", task_id="task-1",
                           started_at=time.time() - 1)
    assert [ln["path"] for ln in lines] == [str((docs / "deck.pptx").resolve())]
    assert lines[0]["receipt"] and lines[0]["qa"]["checked"] and not lines[0]["qa"]["ok"]   # no slides
    assert am.record_call("office", {"command": "view deck.pptx text"}, "...", started_at=0) == []


def test_creations_and_filled_forms_are_found_in_the_tool_result(home):
    from agent_friday.services import artifact_manifest as am
    img, form = home / "a.png", home / "form-filled.pdf"
    gen = json.dumps({"status": "ok", "files": [{"filename": "a.png", "path": str(img)}]})
    filled = json.dumps({"note": "Saved", "output": str(form)})
    assert am.written_paths("generate_image", {}, gen) == [img]
    assert am.written_paths("fill_pdf_form", {}, filled) == [form]
    assert am.written_paths("save_output", {}, "saved: %s (1.0 KB)" % img) == [img]
    assert am.written_paths("generate_image", {}, "[CONTENT SAFETY] blocked") == []


def test_save_output_never_replaces_a_file_already_there(home, monkeypatch):
    from agent_friday.services import creative_store, media_tools
    keep = home / "clip.mp4"
    keep.write_bytes(b"the owner's own clip")
    fresh = home / "job-123.mp4"

    def _download(url, job=None, dest_dir=None):
        fresh.write_bytes(b"new download")
        return {"ok": True, "path": str(fresh)}
    monkeypatch.setattr(creative_store, "download_output", _download)
    out = media_tools._tool_save_output({"url": "https://example.invalid/x.mp4", "filename": "clip.mp4"})
    assert keep.read_bytes() == b"the owner's own clip"
    assert "clip-2.mp4" in out and (home / "clip-2.mp4").read_bytes() == b"new download"


def test_the_agent_records_every_creating_call_it_runs(home, monkeypatch):
    from agent_friday.services import agent
    from agent_friday.services import artifact_manifest as am
    seen = []
    monkeypatch.setattr(am, "submit", lambda *a, **k: seen.append((a, k)))
    agent._orb_tool_trace(None, "write_file", {"path": "x"}, "Wrote 1 chars to x", 12)
    agent._orb_tool_trace(None, "write_file", {"path": "x"}, "[GOVERNANCE HOLD] not now", 12)
    agent._orb_tool_trace(None, "read_file", {"path": "x"}, "contents", 12)
    assert [k["ok"] for _, k in seen] == [True, False]


def test_a_receipt_for_another_call_is_never_linked(home):
    from agent_friday.services import artifact_manifest as am
    p = home / "c.md"
    _allow("write_file", {"path": str(home / "something-else.md")})       # same tool, other arguments
    _allow("write_file", {"path": str(p)}, decision="deny")                # this call, but refused
    p.write_text("x", encoding="utf-8")
    line = am.record_call("write_file", {"path": str(p)}, "Wrote 1 chars", task_id="task-1",
                          started_at=time.time() - 1)[0]
    assert line["receipt"] is None and line["args_hash"]
    # With no task and no turn there is nothing to tie a receipt to.
    _allow("write_file", {"path": str(p)}, task_id=None)
    line2 = am.record_call("write_file", {"path": str(p)}, "Wrote 1 chars", started_at=time.time() - 1)[0]
    assert line2["receipt"] is None


def test_a_call_put_to_the_owner_links_to_that_decision(home):
    from agent_friday.services import artifact_manifest as am
    p = home / "d.md"
    _allow("write_file", {"path": str(p)}, decision="confirm")
    p.write_text("x", encoding="utf-8")
    line = am.record_call("write_file", {"path": str(p)}, "Wrote 1 chars", task_id="task-1",
                          started_at=time.time() - 1)[0]
    assert line["receipt"]["decision"] == "confirm"


def test_a_manifest_line_and_a_receipt_never_verify_as_each_other(home):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import artifact_manifest as am
    p = home / "e.md"
    _allow("write_file", {"path": str(p)})
    p.write_text("x", encoding="utf-8")
    line = am.record_call("write_file", {"path": str(p)}, "Wrote 1 chars", task_id="task-1",
                          started_at=time.time() - 1)[0]
    receipt = am.receipt_of(line)
    assert am.verify(line) and ag.verify_receipt(receipt)
    assert not ag.verify_receipt(line) and not am.verify(receipt)


def test_the_agent_hands_calls_to_a_background_recorder(home):
    from agent_friday.services import artifact_manifest as am
    p = home / "f.md"
    p.write_text("x", encoding="utf-8")
    assert am.submit("write_file", {"path": str(p)}, "Wrote 1 chars", started_at=time.time() - 1)
    assert not am.submit("read_file", {"path": str(p)}, "x")
    assert am.drain()
    assert am.lookup(p)["tool"] == "write_file"


def test_a_queued_call_is_recorded_where_it_was_made(home, monkeypatch, tmp_path_factory):
    """The home and keys are read when the call is handed over, so a recorder
    that runs later never writes into some other home."""
    import agent_friday.paths as paths
    from agent_friday.services import artifact_manifest as am
    p = home / "g.md"
    p.write_text("x", encoding="utf-8")
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    with am._LOCK:                              # hold the worker's write back until the home changes
        assert am.submit("write_file", {"path": str(p)}, "Wrote 1 chars", started_at=time.time() - 1)
        monkeypatch.setattr(paths, "friday_home", lambda: elsewhere)
    assert am.drain()
    assert (home / "artifact-manifest.jsonl").exists()
    assert not (elsewhere / "artifact-manifest.jsonl").exists()


def test_office_check_reports_the_structural_findings(home, monkeypatch):
    from agent_friday.services import office_engine as oe
    docs = home / "documents"
    monkeypatch.setattr(oe, "DOCUMENTS_DIR", docs)
    docs.mkdir()
    (docs / "deck.pptx").write_bytes(b"not a zip")
    monkeypatch.setattr(oe, "run", lambda argv, **k: (0, '{"success": true}' if argv[0] == "validate"
                                                      else ("{}" if "issues" in argv else "Some words"), ""))
    out = oe.deliver_check("deck.pptx", want_image=False)
    assert not out["ok"] and any(f.startswith("validity:") for f in out["findings"])
