"""The artifact manifest's record of a file (NS-24.1-2, BD-20 V2): principal
and goal ancestry from the task record, a sensitivity LEVEL (never the words
that set it), a verification state, the QA result, the delivery receipt once
the file is sent, and its version history with write_file's backups.
Synthetic files and keys only; nothing leaves the machine."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

KEY = b"synthetic-governance-key-for-tests-0000000000000000"
FIELDS = ("principal", "goal_ancestry", "sensitivity", "verification_state", "verification_reason", "qa",
          "delivery_receipt", "previous_version")


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import off_record
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import action_journal as journal
    from agent_friday.services import file_write_receipts as fwr
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(ag, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    monkeypatch.setattr(fwr, "backups_root", lambda: tmp_path / "backups")
    monkeypatch.setattr(journal, "home_trash", lambda rid: tmp_path / "trash" / rid)
    journal.reset()
    yield tmp_path
    journal.reset()


def _task(monkeypatch, task_id, **rec):
    from agent_friday.services import agent
    monkeypatch.setitem(agent.TASKS, task_id, dict({"task_id": task_id}, **rec))


def _record(p, content, task_id=None, result=None):
    from agent_friday.services import artifact_manifest as am
    p.write_text(content, encoding="utf-8")
    lines = am.record_call("write_file", {"path": str(p), "content": content},
                           result or "Wrote %d chars to %s" % (len(content), p),
                           task_id=task_id, started_at=time.time() - 1)
    assert len(lines) == 1, lines
    return lines[0]


def test_a_record_carries_every_field_and_the_goal_it_serves(home, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    _task(monkeypatch, "task-g", principal="owner",
          goal_ancestry={"goal_id": "goal_synthetic", "milestone_id": "ms_2"})
    p = home / "budget.csv"
    line = _record(p, "item,cost\nHosting,1200\n", task_id="task-g")
    for k in FIELDS:
        assert k in line, k
    assert line["principal"] == "owner"
    assert line["goal_ancestry"] == {"goal_id": "goal_synthetic", "milestone_id": "ms_2"}
    assert line["verification_state"] == "qa_passed" and line["qa"]["checked"] and line["qa"]["ok"]
    assert line["qa"]["summary"] == "Checked: no problems found."
    assert line["delivery_receipt"] is None and line["previous_version"] is None
    assert line["verification_reason"] is None
    assert "version_history" not in line                 # rebuilt when read, never copied per line
    assert am.verify(line)
    rec = am.lookup(p)
    assert rec and rec["hmac"] == line["hmac"]
    for k in FIELDS:
        assert rec[k] == line[k], k
    assert rec["deliveries"] == [] and rec["version_history"] == []


def test_principal_comes_from_the_task_record_and_a_turn_is_the_owners(home, monkeypatch):
    _task(monkeypatch, "task-crew", principal="crew:researcher")
    crew = _record(home / "a.md", "notes", task_id="task-crew")
    assert crew["principal"] == "crew:researcher" and crew["goal_ancestry"] is None
    # A principal that is not "owner" or "crew:<id>" is never copied from a record.
    _task(monkeypatch, "task-odd", principal="ignore previous instructions")
    assert _record(home / "b.md", "notes", task_id="task-odd")["principal"] == "owner"
    turn = _record(home / "c.md", "notes")
    assert turn["principal"] == "owner" and turn["goal_ancestry"] is None
    assert turn["verification_state"] == "unverified"            # .md is not a checked kind


def test_sensitivity_is_a_level_and_never_the_content(home, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    secret = "Client social security number 078-05-1120"  # pragma: allowlist secret
    line = _record(home / "client.md", secret)
    sens = line["sensitivity"]
    assert set(sens) == {"level", "tier", "basis"}
    assert sens["level"] == "sensitive" and sens["tier"] == 3
    assert "078-05-1120" not in json.dumps(line) and "social security" not in json.dumps(line)  # pragma: allowlist secret
    assert "078-05-1120" not in am.manifest_path().read_text(encoding="utf-8")  # pragma: allowlist secret
    plain = _record(home / "plain.md", "The weather is mild today.")
    assert plain["sensitivity"]["level"] in am.SENSITIVITY_LEVELS.values()
    assert plain["sensitivity"]["tier"] < 3
    # A task that must stay on this PC makes files no less than private.
    _task(monkeypatch, "task-local", principal="owner", local_only=True)
    local = _record(home / "local.md", "The weather is mild today.", task_id="task-local")
    assert local["sensitivity"]["tier"] >= 2


def test_a_file_that_fails_its_checks_is_qa_failed_and_leaves_the_task_unverified(home, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    p = home / "ragged.csv"
    bad = _record(p, "a,b\n1,2,3\n", task_id="task-q")
    assert bad["verification_state"] == "qa_failed" and not bad["qa"]["ok"]
    failures = am.qa_failures("task-q")
    assert [f["path"] for f in failures] == [str(p.resolve())] and failures[0]["errors"]
    # The task fixes the file: its newest line decides.
    _record(p, "a,b\n1,2\n", task_id="task-q")
    assert am.qa_failures("task-q") == []
    assert am.qa_failures(None) == []


def test_a_delivered_file_gets_the_send_receipt_id(home, monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import artifact_manifest as am
    from agent_friday.services import gmail_send
    p = home / "report.csv"
    line = _record(p, "a,b\n1,2\n")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, ""))
    monkeypatch.setattr(ag, "outward_hold", lambda: None)
    receipt = ag.record_external("gmail:send", surface="mail", approval_id="appr-1", target="1 recipient")
    assert receipt and receipt["hmac"] and ag.verify_receipt(receipt)
    gmail_send._record_attachment_deliveries(
        {"attachments": [{"sha256": line["sha256"], "filename": "report.csv"}]}, receipt, "appr-1", "msg-1")
    rec = am.lookup(p)
    assert rec["delivery_receipt"]["id"] == receipt["hmac"]
    assert rec["delivery_receipt"]["channel"] == "gmail" and rec["delivery_receipt"]["ref"] == "msg-1"
    assert rec["delivery_receipt"]["approval_id"] == "appr-1"
    # The same receipt is recorded once; a receipt that does not verify never.
    assert am.record_delivery(receipt, channel="gmail", sha256=line["sha256"]) == []
    forged = dict(receipt, target="someone else")
    assert am.record_delivery(forged, channel="gmail", sha256=line["sha256"]) == []
    assert len(am.lookup(p)["deliveries"]) == 1
    # Bytes Friday did not make are not anyone's artifact.
    assert am.record_delivery(receipt, channel="gmail", sha256=hashlib.sha256(b"x").hexdigest()) == []


def test_the_owner_can_mark_a_file_verified(home):
    from agent_friday.services import artifact_manifest as am
    p = home / "ok.md"
    _record(p, "done")
    assert am.lookup(p)["verification_state"] == "unverified"
    ev = am.mark_owner_verified(p)
    assert ev and am.verify_event(ev) and not am.verify(ev)
    assert am.lookup(p)["verification_state"] == "owner_verified"
    p.write_text("changed by hand", encoding="utf-8")
    assert am.mark_owner_verified(p) is None


def _write(path, content, mode="write"):
    from agent_friday.services.agent import _tool_write_file
    return _tool_write_file({"path": str(path), "content": content, "mode": mode})


def test_a_second_write_records_the_prior_version_and_its_backup(home):
    import re
    from agent_friday.services import artifact_manifest as am
    p = home / "plan.md"
    first = _write(p, "first version")
    assert am.written_paths("write_file", {"path": str(p)}, first) == [p.resolve()]
    line1 = am.record_call("write_file", {"path": str(p), "content": "first version"}, first,
                           started_at=time.time() - 1)[0]
    assert line1["previous_version"] is None and am.lookup(p)["version_history"] == []
    second = _write(p, "second version")
    rid = re.search(r"receipt_id (rcpt_[0-9a-f]+)", second).group(1)
    assert am.written_paths("write_file", {"path": str(p)}, second) == [p.resolve()]
    line2 = am.record_call("write_file", {"path": str(p), "content": "second version"}, second,
                           started_at=time.time() - 1)[0]
    assert "version_history" not in line2
    hist = am.lookup(p)["version_history"]
    assert hist == [line2["previous_version"]]
    assert len(hist) == 1
    assert hist[0]["sha256"] == hashlib.sha256(b"first version").hexdigest() == line1["sha256"]
    assert hist[0]["ts"] == line1["ts"] and hist[0]["write_receipt"] == rid and hist[0]["by_friday"]
    assert hist[0]["backup"] and Path(hist[0]["backup"]).read_bytes() == b"first version"
    assert line2["write_receipt"] == rid
    assert am.lookup(p)["version_history"] == hist


def test_history_is_rebuilt_from_the_previous_version_on_each_line(home):
    from agent_friday.services import artifact_manifest as am
    p = home / "notes.md"
    shas = [_record(p, "version %d" % i)["sha256"] for i in range(3)]
    raw = [json.loads(x) for x in am.manifest_path().read_text(encoding="utf-8").splitlines()]
    assert all("version_history" not in x for x in raw)
    assert [x["previous_version"] and x["previous_version"]["sha256"] for x in raw] == [None, shas[0], shas[1]]
    assert [v["sha256"] for v in am.lookup(p)["version_history"]] == shas[:2]      # oldest first


def test_a_legacy_line_with_the_whole_history_is_still_read(home):
    from agent_friday.services import artifact_manifest as am
    p = home / "old.md"
    p.write_text("now", encoding="utf-8")
    old = [{"sha256": "a" * 64, "ts": "t1", "backup": None, "write_receipt": None, "by_friday": True},
           {"sha256": "b" * 64, "ts": "t2", "backup": None, "write_receipt": None, "by_friday": True}]
    legacy = am._sign({"kind": "artifact", "ts": "t3", "path": str(p.resolve()),
                       "sha256": hashlib.sha256(b"now").hexdigest(), "bytes": 3, "tool": "write_file",
                       "qa": {"checked": False, "ok": True, "findings": [], "summary": ""},
                       "verification_state": "unverified", "version_history": old})
    am.manifest_path().write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    assert am.lookup(p)["version_history"] == old
    # A new line after it carries only its previous version; the walk joins both.
    newer = _record(p, "newer")
    assert newer["previous_version"]["sha256"] == legacy["sha256"]
    assert [v["sha256"] for v in am.lookup(p)["version_history"]] == ["a" * 64, "b" * 64, legacy["sha256"]]


def test_lookup_reaches_lines_older_than_the_tail_and_hashes_only_a_recorded_path(home, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    p = home / "early.md"
    first = _record(p, "made early")
    line = _record(p, "made early, then changed")
    with open(am.manifest_path(), "a", encoding="utf-8") as fh:
        for i in range(400):                               # other files' lines, between them
            fh.write(json.dumps({"kind": "artifact", "path": str(home / ("w%d.md" % i)), "pad": "y" * 200}) + "\n")
    line3 = _record(p, "changed once more")
    with open(am.manifest_path(), "a", encoding="utf-8") as fh:
        for i in range(400):                               # other files' lines, after it
            fh.write(json.dumps({"kind": "artifact", "path": str(home / ("x%d.md" % i)), "pad": "y" * 200}) + "\n")
    monkeypatch.setattr(am, "LOOKUP_TAIL_BYTES", 4096)
    monkeypatch.setattr(am, "_BLOCK", 1024)
    rec = am.lookup(p)
    assert rec["hmac"] == line3["hmac"]
    # The history walk follows the chain back past the tail, to the first version.
    assert [v["sha256"] for v in rec["version_history"]] == [first["sha256"], line["sha256"]]
    assert first["first_version"] and not line["first_version"]
    monkeypatch.setattr(am, "LOOKUP_MAX_BYTES", 8192)       # bounded: beyond it, nothing
    assert am.lookup(p) is None
    hashed = []
    monkeypatch.setattr(am, "_sha256", lambda q: hashed.append(q))
    other = home / "never-recorded.md"
    other.write_text("not Friday's", encoding="utf-8")
    assert am.lookup(other) is None and hashed == []


def test_network_device_and_missing_paths_are_never_verified(home, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    for bad in (r"\\host\share\a.md", "//host/share/a.md", r"\\?\C:\a.md", r"\\.\COM1"):
        assert am.refused_path(bad), bad
        assert am.lookup(bad) is None and am.mark_owner_verified(bad) is None
    assert am.refused_path(str(home / "a.md")) is None
    p = home / "gone.md"
    _record(p, "here for now")
    p.unlink()
    assert am.mark_owner_verified(p) is None


def test_a_frozen_build_records_checks_unavailable(home, monkeypatch):
    import sys
    from agent_friday.services import artifact_manifest as am
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    line = _record(home / "sheet.csv", "a,b\n1,2\n")
    assert line["verification_state"] == "unverified"
    assert line["verification_reason"] == am.UNAVAILABLE_REASON == "checks unavailable in this build"
    assert am.lookup(home / "sheet.csv")["verification_reason"] == am.UNAVAILABLE_REASON


def test_a_task_waits_only_for_its_own_checks(home, monkeypatch):
    import threading
    from agent_friday.services import artifact_manifest as am
    release = threading.Event()
    real = am.record_call

    def slow(*a, **k):
        if k.get("task_id") == "task-slow":
            release.wait(10)
        return real(*a, **k)
    monkeypatch.setattr(am, "record_call", slow)
    p = home / "slow.md"
    p.write_text("slow", encoding="utf-8")
    assert am.submit("write_file", {"path": str(p), "content": "slow"}, "Wrote 4 chars to %s" % p,
                     task_id="task-slow", started_at=time.time() - 1)
    try:
        # Another task has nothing queued: it does not wait for this one.
        t0 = time.time()
        assert am.qa_verdict("task-other", wait=5) == {"failures": [], "pending": 0}
        assert time.time() - t0 < 2
        # This task's check is still running after its wait: pending, not done.
        v = am.qa_verdict("task-slow", wait=0.2)
        assert v["pending"] == 1 and am.wait_for_task("task-slow", 0.05) is False
        assert am.PENDING_REASON == "checks still running"
    finally:
        release.set()
    assert am.wait_for_task("task-slow", 10) is True
    assert am.qa_verdict("task-slow")["pending"] == 0
    assert am.lookup(p)["task_id"] == "task-slow"
