"""The action record keeps two years live; older entries are archived (A22).

Amendment A22 (owner ruling 2026-10-22): entries older than the retention
period (Settings, two years by default) leave the live record for an encrypted
archive carried by every Friday Bundle and backup. Nothing is deleted, and the
chain verifies across the archive boundary: a missing, edited or substituted
archive, an edited list, or a live record cut at the boundary is a break.

Red on the old code by assertion and on missing names: there was no retention
setting, no archive, and old entries stayed in the live files forever.
"""
from __future__ import annotations

import json
import shutil
import sys
import time

import pytest

from agent_friday.governance import proof_of_integrity as poi
from agent_friday.services import action_ledger as L

DAY = 86400.0
MARK = "a-plain-word-only-the-old-entries-hold"


@pytest.fixture(autouse=True)
def key_in_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", tmp_path / "gk")
    monkeypatch.setitem(sys.modules, "keyring", None)
    L.reset_caches()


def _seal():
    """Close the live file into a sealed segment, as a new month would."""
    with L._TLOCK:
        L._check_dir()
        with L._lock():
            L._seal_live_locked(L.ledger_dir(), L.live_path())
    L.reset_caches()


def _old_and_new(n_old=4, n_new=2):
    for i in range(n_old):
        L.append("decision", {"tool": "probe_old", "i": i, "note": MARK}, durable=True)
    _seal()
    for i in range(n_new):
        L.append("activity", {"kind": "tool_call", "tool": "probe_new", "i": i})


def _later(days=800):
    return time.time() + days * DAY


def test_the_period_is_a_setting_two_years_by_default():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS[L.ARCHIVE_SETTING] == 730
    assert L.archive_after_days() == 730
    assert L.normalise_archive_days(0) == 0                  # off
    assert L.normalise_archive_days(5) == L.MIN_ARCHIVE_AFTER_DAYS
    assert L.normalise_archive_days("nonsense") == 730
    assert L.normalise_archive_days(True) == 730


def test_old_entries_move_to_the_encrypted_archive_and_are_never_deleted():
    _old_and_new()
    out = L.archive_old(now=_later(), days=730)
    assert out["archived"] == 1 and out["records"] == 4, out
    # the plain segment is gone from the live files; an encrypted copy replaced it
    assert not list((L.ledger_dir() / L.SEGMENTS_DIR).glob("actions-*.jsonl"))
    enc = list(L.archive_dir().glob("actions-*.jsonl.enc"))
    assert len(enc) == 1 and MARK.encode() not in enc[0].read_bytes()
    # still whole, still counted, and the archive step is itself a receipt
    rep = L.verify()
    assert rep["ok"] and rep["intact"], rep["problems"]
    assert rep["archived_records"] == 4 and rep["archive_files"] == 1
    live = L.entries(newest_first=False)
    assert all(e["kind"] != "decision" for e in live)
    assert any(e["kind"] == "archived" for e in live)
    # nothing deleted: the archive reads back in full, in order
    everything = L.entries(newest_first=False, archive=True)
    seqs = [e["seq"] for e in everything]
    assert seqs == list(range(1, len(seqs) + 1))
    assert [e["rec"]["i"] for e in everything if e["kind"] == "decision"] == [0, 1, 2, 3]
    # a strict reader (revocations) sees the archive without asking
    assert len(L.entries(["decision"], strict=True)) == 4


def test_entries_inside_the_period_stay_live():
    _old_and_new()
    assert L.archive_old(now=_later(100), days=730)["archived"] == 0
    assert L.archive_old(now=_later(800), days=0)["archived"] == 0      # off
    assert list((L.ledger_dir() / L.SEGMENTS_DIR).glob("actions-*.jsonl"))
    assert not L.archive_dir().exists() or not list(L.archive_dir().glob("*.enc"))


def test_the_chain_continues_across_the_boundary_after_more_writes_and_a_restart():
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    _seal()                                    # everything sealed, then archived too
    L.archive_old(now=_later(), days=730)
    L.reset_caches()
    e = L.append("activity", {"kind": "tool_call", "tool": "after"})
    everything = L.entries(newest_first=False, archive=True)
    assert e["seq"] == len(everything) and e["prev"] == everything[-2]["hash"]
    assert L.verify()["ok"]


def _broken_after(change):
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    assert L.verify()["ok"]
    change()
    L.reset_caches()
    rep = L.verify()
    assert not rep["ok"], "the change was not seen"
    assert L.hold_reason(), "outward actions are not held"
    return rep


def _enc():
    return next(L.archive_dir().glob("actions-*.jsonl.enc"))


def _manifest():
    return L.archive_dir() / L.ARCHIVE_MANIFEST


def test_a_missing_archive_file_is_a_break():
    _broken_after(lambda: _enc().unlink())


def test_a_changed_archive_file_is_a_break():
    def change():
        p = _enc()
        b = bytearray(p.read_bytes())
        b[-5] ^= 1
        p.write_bytes(bytes(b))
    _broken_after(change)


def test_an_edited_archive_list_is_a_break():
    def change():
        m = json.loads(_manifest().read_text(encoding="utf-8"))
        m["ranges"][0]["last_seq"] += 1
        _manifest().write_text(json.dumps(m), encoding="utf-8")
    _broken_after(change)


def test_a_deleted_archive_list_is_a_break():
    _broken_after(lambda: _manifest().unlink())


def test_a_live_record_cut_at_the_boundary_is_a_break():
    def change():
        lines = L.live_path().read_text(encoding="utf-8").splitlines()
        L.live_path().write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
    _broken_after(change)


def test_an_archive_substituted_from_another_ledger_is_a_break(tmp_path, monkeypatch):
    first = L.ledger_dir()
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    # a second ledger under the same key, archived the same way
    monkeypatch.setenv("FRIDAY_LEDGER_DIR", str(tmp_path / "other-ledger"))
    L.reset_caches()
    for i in range(4):
        L.append("decision", {"tool": "someone_else", "i": i}, durable=True)
    _seal()
    L.append("activity", {"kind": "x"})
    L.archive_old(now=_later(), days=730)
    other = L.archive_dir()
    monkeypatch.setenv("FRIDAY_LEDGER_DIR", str(first))
    L.reset_caches()
    assert L.verify()["ok"]
    # its archive file alone, under the first ledger's list
    mine = first / L.ARCHIVE_DIR
    theirs = next(other.glob("*.enc"))
    shutil.copy2(theirs, mine / theirs.name)
    L.reset_caches()
    assert not L.verify()["ok"]
    # its file and its signed list together: the live chain does not follow it
    shutil.copy2(other / L.ARCHIVE_MANIFEST, mine / L.ARCHIVE_MANIFEST)
    L.reset_caches()
    rep = L.verify()
    assert not rep["ok"]
    assert any("follow" in p["reason"] for p in rep["problems"]), rep["problems"]


def test_a_ledger_with_an_open_break_is_not_archived():
    _old_and_new()
    seg = next((L.ledger_dir() / L.SEGMENTS_DIR).glob("actions-*.jsonl"))
    seg.write_text(seg.read_text(encoding="utf-8").replace("probe_old", "probe_OLD", 1), encoding="utf-8")
    L.reset_caches()
    out = L.archive_old(now=_later(), days=730)
    assert out["archived"] == 0 and "break" in out["why"]
    assert seg.exists()


def test_a_new_month_seals_the_live_file(monkeypatch):
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() - 40 * DAY)
    L.append("activity", {"kind": "tool_call", "tool": "last_month"})
    monkeypatch.setattr(time, "time", real)
    L.reset_caches()
    L.append("activity", {"kind": "tool_call", "tool": "this_month"})
    segs = list((L.ledger_dir() / L.SEGMENTS_DIR).glob("actions-*.jsonl"))
    assert len(segs) == 1 and "last_month" in segs[0].read_text(encoding="utf-8")
    assert "last_month" not in L.live_path().read_text(encoding="utf-8")
    assert L.verify()["ok"]


def test_a_move_cut_short_is_finished_not_duplicated():
    _old_and_new()
    seg = next((L.ledger_dir() / L.SEGMENTS_DIR).glob("actions-*.jsonl"))
    kept = seg.read_bytes()
    L.archive_old(now=_later(), days=730)
    seg.write_bytes(kept)              # as if the process stopped before removing it
    L.reset_caches()
    L.archive_old(now=_later(), days=730)
    assert not seg.exists()
    assert L.verify()["ok"]
    assert len(list(L.archive_dir().glob("*.enc"))) == 1


def test_a_friday_bundle_carries_the_archive_and_it_verifies_after_restore(tmp_path, monkeypatch):
    from tests.unit.test_friday_bundle import PASS, _make, _point_at
    from agent_friday.services import friday_bundle as fb
    a, b = tmp_path / "a" / ".friday", tmp_path / "b" / ".friday"
    a.mkdir(parents=True)
    _point_at(a, monkeypatch)
    monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", "vault passphrase for the test")  # pragma: allowlist secret
    _old_and_new()
    assert L.archive_old(now=_later(), days=730)["archived"] == 1
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    _point_at(b, monkeypatch)
    r = fb.restore(out, PASS, home=b)
    assert r["checked_in_place"]["ledger"]["ok"] is True, r["checked_in_place"]
    assert list((b / "ledger" / L.ARCHIVE_DIR).glob("*.enc"))
    rep = L.verify()
    assert rep["ok"] and rep["archived_records"] == 4
    assert [e["rec"]["i"] for e in L.entries(["decision"], newest_first=False, archive=True)] == [0, 1, 2, 3]

def test_a_deleted_archive_holds_at_once_and_strict_readers_fail_closed():
    """Review round 1: the hold check reused a cached result while the archive
    was gone, and a strict reader (revocations) found nothing instead of failing."""
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    assert L.hold_reason() is None
    assert len(L.entries(["decision"], strict=True)) == 4
    shutil.rmtree(L.archive_dir())
    L.append("activity", {"kind": "tool_call", "tool": "after"})
    assert L.hold_reason(), "no hold while the archive is gone"
    with pytest.raises(L.LedgerError):
        L.entries(["decision"], strict=True)


def test_a_file_put_back_under_an_archived_name_is_never_read_and_stays_a_break():
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    name = next(L.archive_dir().glob("*.enc")).name[:-len(".enc")]
    seg = L.ledger_dir() / L.SEGMENTS_DIR / name
    seg.write_text("junk\n", encoding="utf-8")
    L.reset_caches()
    rep = L.verify()
    assert not rep["ok"]
    L.acknowledge("owner:test", reviewed=[p for p in rep["problems"] if not p["acknowledged"]])
    forged = {"v": 1, "seq": 1, "kind": "send_grant", "rec": {"event": "granted", "grant_id": "EVIL"}}
    seg.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    L.reset_caches()
    assert not L.verify()["ok"], "a new content under the archived name passed"
    assert not [e for e in L.entries(["send_grant"]) if e["rec"].get("grant_id") == "EVIL"]


def test_the_plain_segment_is_never_removed_without_a_good_archived_copy():
    _old_and_new()
    seg = next((L.ledger_dir() / L.SEGMENTS_DIR).glob("actions-*.jsonl"))
    kept = seg.read_bytes()
    L.archive_old(now=_later(), days=730)
    seg.write_bytes(kept)
    next(L.archive_dir().glob("*.enc")).unlink()
    L.reset_caches()
    rep = L.verify()
    assert not rep["ok"]
    L.acknowledge("owner:test", reviewed=[p for p in rep["problems"] if not p["acknowledged"]])
    L.archive_old(now=_later(), days=730)
    assert seg.exists() and seg.read_bytes() == kept, "the only copy was deleted"


def test_a_crash_between_the_copy_and_the_list_is_finished_next_time(monkeypatch):
    _old_and_new()
    real = L._write_manifest
    monkeypatch.setattr(L, "_write_manifest", lambda *a, **k: (_ for _ in ()).throw(OSError("power")))
    with pytest.raises(OSError):
        L.archive_old(now=_later(), days=730)
    monkeypatch.setattr(L, "_write_manifest", real)
    L.reset_caches()
    assert L.verify()["ok"], "an honest crash left a break"
    assert not list(L.archive_dir().glob("*.tmp"))
    assert L.archive_old(now=_later(), days=730)["archived"] == 1
    assert L.verify()["ok"]
    assert not list((L.ledger_dir() / L.SEGMENTS_DIR).glob("actions-*.jsonl"))


def test_new_segments_never_take_an_archived_name():
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    archived = {p.name[:-len(".enc")] for p in L.archive_dir().glob("*.enc")}
    next(L.archive_dir().glob("*.enc")).unlink()
    _seal()
    names = {p.name for p in (L.ledger_dir() / L.SEGMENTS_DIR).glob("actions-*.jsonl")}
    assert names and not names & archived


def test_an_old_receipt_file_planted_after_archiving_is_quarantined_not_read():
    """Review round 2: archiving the segment that held migration_complete
    re-opened the import, so a planted old receipt became signed history."""
    from agent_friday.paths import friday_home
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    assert L.verify()["ok"]
    planted = friday_home() / "actions" / "receipts.jsonl"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(json.dumps({"receipt_id": "rcpt_evil", "status": "complete",
                                   "undo": {"moves": [{"src": "a", "dst": "b"}]}}) + "\n",
                       encoding="utf-8")
    L.reset_caches()
    L.append("activity", {"kind": "tool_call", "tool": "next_start"})
    assert not [e for e in L.entries(["action"], archive=True) if e["rec"].get("receipt_id") == "rcpt_evil"]
    assert any(e["kind"] == "legacy_reappeared" for e in L.entries())
    assert not L.verify()["ok"] and L.hold_reason()


def test_a_crash_on_the_first_archive_does_not_stop_strict_readers(monkeypatch):
    _old_and_new()
    real = L._write_manifest
    monkeypatch.setattr(L, "_write_manifest", lambda *a, **k: (_ for _ in ()).throw(OSError("power")))
    with pytest.raises(OSError):
        L.archive_old(now=_later(), days=730)
    monkeypatch.setattr(L, "_write_manifest", real)
    L.reset_caches()
    assert len(L.entries(["decision"], strict=True)) == 4


def test_strict_reads_of_a_large_archive_open_each_file_once(monkeypatch):
    for month in range(30):
        L.append("send_grant", {"event": "revoked", "grant_id": "g%d" % month}, durable=True)
        _seal()
    L.append("activity", {"kind": "x"})
    assert L.archive_old(now=_later(), days=730)["archived"] == 30
    calls = []
    real = L._open_blob
    monkeypatch.setattr(L, "_open_blob", lambda *a: (calls.append(a[1]), real(*a))[1])
    for _ in range(3):
        L.append("activity", {"kind": "x"})
        assert len(L.entries(["send_grant"], strict=True)) == 30
    assert len(calls) == 30, len(calls)


@pytest.mark.parametrize("how", ["archive_deleted", "list_garbled"])
def test_old_receipt_files_stay_shut_when_the_archive_is_removed_or_garbled(how):
    """Review round 3: deleting the archive, or garbling its list, made the
    switch from the old files look unfinished, and a planted file was read."""
    from agent_friday.paths import friday_home
    L.migrate_legacy()                          # as every start does
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    if how == "archive_deleted":
        shutil.rmtree(L.archive_dir())
    else:
        _manifest().write_text("{not json", encoding="utf-8")
    planted = friday_home() / "actions" / "receipts.jsonl"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(json.dumps({"receipt_id": "rcpt_evil", "status": "complete"}) + "\n",
                       encoding="utf-8")
    L.reset_caches()
    L.migrate_legacy()
    L.append("activity", {"kind": "tool_call", "tool": "next_start"})
    assert not [e for e in L.entries(["action"]) if e["rec"].get("receipt_id") == "rcpt_evil"]
    assert any(e["kind"] == "legacy_reappeared" for e in L.entries())


def test_an_empty_anchored_ledger_never_reads_a_planted_old_file():
    from agent_friday.paths import friday_home
    L.migrate_legacy()
    L.append("decision", {"tool": "x"}, durable=True)
    shutil.rmtree(L.ledger_dir())
    planted = friday_home() / "actions" / "receipts.jsonl"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(json.dumps({"receipt_id": "rcpt_evil", "status": "complete"}) + "\n",
                       encoding="utf-8")
    L.reset_caches()
    L.migrate_legacy()
    assert not [e for e in L.entries(["action"]) if e["rec"].get("receipt_id") == "rcpt_evil"]
    assert not L.verify()["ok"]


def test_strict_readers_refuse_an_archive_list_put_back_from_an_earlier_copy():
    for i in range(3):
        L.append("send_grant", {"event": "revoked", "grant_id": "g%d" % i}, durable=True)
        _seal()
    L.append("activity", {"kind": "x"})
    L.archive_old(now=_later(), days=730)
    m = json.loads(_manifest().read_text(encoding="utf-8"))
    assert len(m["ranges"]) == 3
    # an earlier, genuinely signed list with one range
    body = {"v": 1, "ranges": m["ranges"][:1], "kid": m["kid"]}
    body["sig"] = L._manifest_sig(L._key(), {k: v for k, v in body.items()})
    _manifest().write_text(json.dumps(body), encoding="utf-8")
    L.reset_caches()
    with pytest.raises(L.LedgerError):
        L.entries(["send_grant"], strict=True)
    assert L.hold_reason()


def test_an_acknowledged_flag_does_not_stop_archiving_for_good():
    L.append("decision", {"tool": "x"}, durable=True)
    with open(L.live_path(), "ab") as f:
        f.write(b'{"half a line')               # a power cut mid-write
    L.reset_caches()
    L.append("decision", {"tool": "y"}, durable=True)   # sets the torn line aside, flags it
    rep = L.verify()
    assert not rep["ok"]
    L.acknowledge("owner:test", reviewed=[p for p in rep["problems"] if not p["acknowledged"]])
    _seal()
    L.append("activity", {"kind": "x"})
    assert L.archive_old(now=_later(), days=730)["archived"] == 1
    assert L.verify()["ok"]


def _plant_old_receipt():
    from agent_friday.paths import friday_home
    planted = friday_home() / "actions" / "receipts.jsonl"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(json.dumps({"receipt_id": "rcpt_evil", "status": "complete"}) + "\n",
                       encoding="utf-8")


def _evil_read():
    L.reset_caches()
    try:
        L.migrate_legacy()
    except Exception:
        pass
    return [e for e in L.entries(["action"]) if (e.get("rec") or {}).get("receipt_id") == "rcpt_evil"]


def test_a_forged_first_line_does_not_reopen_the_switch_after_the_archive_is_removed():
    """Review round 4 (A): delete the archive and plant a seq-1 line."""
    L.migrate_legacy()
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    shutil.rmtree(L.archive_dir())
    seg = L.ledger_dir() / L.SEGMENTS_DIR / "actions-000000.jsonl"
    seg.write_text(json.dumps({"seq": 1, "prev": "0" * 64, "kind": "x", "rec": {}}) + "\n", encoding="utf-8")
    _plant_old_receipt()
    assert not _evil_read()


def test_a_forged_line_in_an_emptied_anchored_ledger_does_not_reopen_the_switch():
    """Review round 4 (A2)."""
    L.migrate_legacy()
    L.append("decision", {"tool": "x"}, durable=True)
    shutil.rmtree(L.ledger_dir())
    L.ledger_dir().mkdir(parents=True)
    L.live_path().write_text(json.dumps({"seq": 1, "prev": "0" * 64, "kind": "x", "rec": {}}) + "\n",
                             encoding="utf-8")
    _plant_old_receipt()
    assert not _evil_read()


def test_cutting_the_record_back_before_the_switch_ended_does_not_reopen_it():
    """Review round 4 (B): no archive needed; cut the live file back to the
    rows imported before migration_complete."""
    from agent_friday.paths import friday_home
    old = friday_home() / "actions" / "receipts.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("".join(json.dumps({"receipt_id": "r%d" % i, "status": "complete"}) + "\n"
                           for i in range(2)), encoding="utf-8")
    L.migrate_legacy()
    L.append("decision", {"tool": "x"}, durable=True)
    lines = L.live_path().read_text(encoding="utf-8").splitlines()
    assert any(json.loads(ln)["kind"] == "migration_complete" for ln in lines)
    keep = [ln for ln in lines if json.loads(ln)["kind"] == "action"]
    assert len(keep) == 2
    L.live_path().write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    _plant_old_receipt()
    assert not _evil_read()


def test_a_genuine_import_that_stopped_halfway_still_continues():
    from agent_friday.paths import friday_home
    rows = [{"receipt_id": "r%d" % i, "status": "complete"} for i in range(5)]
    old = friday_home() / "actions" / "receipts.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    real = L._set_aside
    def stop(*a, **k):
        raise OSError("power cut before the file was set aside")
    L._set_aside = stop
    try:
        with pytest.raises(OSError):
            L.migrate_legacy()
    finally:
        L._set_aside = real
    L.reset_caches()
    L.migrate_legacy()
    got = sorted((e["rec"] or {}).get("receipt_id") for e in L.entries(["action"]))
    assert got == ["r0", "r1", "r2", "r3", "r4"]
    assert not any(e["kind"] == "legacy_reappeared" for e in L.entries())


def test_strict_readers_refuse_a_rolled_back_list_with_no_live_record_left():
    """Review round 4 (C)."""
    for i in range(3):
        L.append("send_grant", {"event": "revoked", "grant_id": "g%d" % i}, durable=True)
        _seal()
    L.append("activity", {"kind": "x"}, durable=True)
    L.archive_old(now=_later(), days=730)
    m = json.loads(_manifest().read_text(encoding="utf-8"))
    body = {"v": 1, "ranges": m["ranges"][:1], "kid": m["kid"]}
    body["sig"] = L._manifest_sig(L._key(), dict(body))
    _manifest().write_text(json.dumps(body), encoding="utf-8")
    for r in m["ranges"][1:]:
        (L.archive_dir() / (r["name"] + ".enc")).unlink()
    for p in list((L.ledger_dir() / L.SEGMENTS_DIR).glob("*.jsonl")) + [L.live_path(), L.head_path()]:
        if p.exists():
            p.unlink()
    L.reset_caches()
    with pytest.raises(L.LedgerError):
        L.entries(["send_grant"], strict=True)


@pytest.mark.parametrize("junk", [b"\n", b"[]\n", b"garbage"])
def test_bytes_that_are_not_an_entry_do_not_reopen_the_switch(junk):
    """Review round 5: an anchored ledger emptied down to a line that is not an entry."""
    L.migrate_legacy()
    L.append("decision", {"tool": "x"}, durable=True)
    shutil.rmtree(L.ledger_dir())
    L.ledger_dir().mkdir(parents=True)
    L.live_path().write_bytes(junk)
    _plant_old_receipt()
    assert not _evil_read()


def test_ordinary_rechecks_do_not_postpone_the_full_reread(monkeypatch):
    """Review round 5: every recompute restarted the full-check clock, so an
    edit that kept a sealed file's size, time and tail could hide for good."""
    _old_and_new(n_old=3, n_new=1)
    L.verify(force=True)
    full_at = L._STATE["full_at"]
    L.append("activity", {"kind": "x"})
    L.verify(force=False)
    assert L._STATE["full_at"] == full_at


def test_strict_readers_refuse_a_cut_record_even_without_an_archive():
    """Review round 5: the strict anchor check ran only with an archive."""
    L.append("send_grant", {"event": "revoked", "grant_id": "g1"}, durable=True)
    L.append("send_grant", {"event": "revoked", "grant_id": "g2"}, durable=True)
    lines = L.live_path().read_text(encoding="utf-8").splitlines()
    L.live_path().write_text("\n".join(lines[:-1]) + "\n" +
                             json.dumps({"seq": 99999999, "hash": "x"}) + "\n", encoding="utf-8")
    L.reset_caches()
    with pytest.raises(L.LedgerError):
        L.entries(["send_grant"], strict=True)


def test_a_ledger_folder_swapped_for_a_link_is_a_break_and_keeps_its_anchor(tmp_path):
    """Review round 6: a junction on ledger/ named another keychain account."""
    import os
    L.append("decision", {"tool": "x"}, durable=True)
    acct = L._anchor_account()
    d = L.ledger_dir()
    old = tmp_path / "older-copy"
    shutil.copytree(d, old)
    L.append("decision", {"tool": "y"}, durable=True)
    shutil.move(str(d), str(tmp_path / "moved-away"))
    try:
        os.symlink(old, d, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot make links")
    L.reset_caches()
    assert L._anchor_account() == acct
    rep = L.verify()
    assert not rep["ok"]
    assert any("link" in p["reason"] for p in rep["problems"])
    assert L.hold_reason()


def test_strict_readers_wait_while_the_ledger_has_an_open_break():
    L.append("send_grant", {"event": "revoked", "grant_id": "g1"}, durable=True)
    L.append("activity", {"kind": "x"})
    p = L.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace('"x"', '"X"'), encoding="utf-8")
    L.reset_caches()
    with pytest.raises(L.LedgerError):
        L.entries(["send_grant"], strict=True)


def test_an_acknowledgement_that_cannot_keep_its_anchor_says_so(monkeypatch):
    L.append("decision", {"tool": "x"}, durable=True)
    p = L.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace('"x"', '"X"'), encoding="utf-8")
    L.reset_caches()
    rep = L.verify()
    monkeypatch.setattr(L, "_anchor_set", lambda *a, **k: (_ for _ in ()).throw(OSError("keychain")))
    with pytest.raises(L.LedgerError):
        L.acknowledge("owner:test", reviewed=[q for q in rep["problems"] if not q["acknowledged"]])


def test_the_owner_can_still_list_send_approvals_while_the_ledger_shows_a_break():
    """Review round 7 (A): the Settings list answered 500 during a break."""
    from agent_friday.governance import send_grants
    L.append("activity", {"kind": "x"})
    p = L.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace('"x"', '"X"'), encoding="utf-8")
    L.reset_caches()
    rows = send_grants.list_grants()            # must not raise
    assert all(r.get("in_force") is False for r in rows)


def test_the_workspace_reads_the_archive_during_a_break():
    """Review round 7 (B): the workspace dropped the archive while a break was open."""
    _old_and_new()
    L.archive_old(now=_later(), days=730)
    L.append("activity", {"kind": "x"})
    p = L.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace('"x"', '"X"'), encoding="utf-8")
    L.reset_caches()
    rows = L.entries(["decision"], archive=True, strict=True, gate=False)
    assert len(rows) == 4


def test_a_strict_read_refuses_a_line_whose_signature_does_not_hold(monkeypatch):
    """Review round 7 (F): within the cache window a strict read checks each line."""
    L.append("send_grant", {"event": "revoked", "grant_id": "g1"}, durable=True)
    assert L.entries(["send_grant"], strict=True)
    monkeypatch.setattr(L, "verify", lambda **k: {"ok": True})   # as within the 10-minute window
    p = L.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace('"g1"', '"g9"'), encoding="utf-8")
    with pytest.raises(L.LedgerError):
        L.entries(["send_grant"], strict=True)


def test_a_junction_for_the_ledger_folder_is_a_break(tmp_path):
    """Review round 7 (D): the Windows junction path, where links need no privilege."""
    import os
    import subprocess
    if os.name != "nt":
        pytest.skip("junctions are a Windows thing")
    L.append("decision", {"tool": "x"}, durable=True)
    d = L.ledger_dir()
    old = tmp_path / "older-copy"
    shutil.copytree(d, old)
    shutil.move(str(d), str(tmp_path / "moved-away"))
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(d), str(old)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("mklink /J failed: %s" % r.stderr)
    L.reset_caches()
    rep = L.verify()
    assert not rep["ok"] and any("link" in p["reason"] for p in rep["problems"])


def test_an_acknowledged_bad_line_does_not_hold_strict_readers_for_good():
    """Review round 8 (1)."""
    L.append("send_grant", {"event": "revoked", "grant_id": "g1"}, durable=True)
    L.append("send_grant", {"event": "revoked", "grant_id": "g2"}, durable=True)
    p = L.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace('"g1"', '"g9"'), encoding="utf-8")
    L.reset_caches()
    rep = L.verify()
    L.acknowledge("owner:test", reviewed=[q for q in rep["problems"] if not q["acknowledged"]])
    got = {e["rec"]["grant_id"] for e in L.entries(["send_grant"], strict=True)}
    assert {"g2", "g9"} <= got


def test_a_line_garbled_or_renamed_within_the_cache_window_is_refused(monkeypatch):
    """Review round 8 (2): the check runs before the kind filter, on every line."""
    L.append("send_grant", {"event": "revoked", "grant_id": "g1"}, durable=True)
    L.append("activity", {"kind": "x"})
    monkeypatch.setattr(L, "verify", lambda **k: {"ok": True})
    p = L.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace('"kind":"send_grant"', '"kind":"send_grent"'),
                 encoding="utf-8")
    with pytest.raises(L.LedgerError):
        L.entries(["send_grant"], strict=True)
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join(["not json"] + lines[1:]) + "\n", encoding="utf-8")
    with pytest.raises(L.LedgerError):
        L.entries(["send_grant"], strict=True)



@pytest.mark.parametrize("tail", [b" ", b"\r", b"\t "])
def test_whitespace_after_the_last_line_hides_nothing_from_strict_reads(tail):
    """Closing review (D1): whitespace with no newline is no unfinished write."""
    L.append("send_grant", {"event": "revoked", "grant_id": "g1"}, durable=True)
    L.append("send_grant", {"event": "revoked", "grant_id": "g2"}, durable=True)
    with open(L.live_path(), "ab") as f:
        f.write(tail)
    L.reset_caches()
    got = {e["rec"]["grant_id"] for e in L.entries(["send_grant"], strict=True)}
    assert got == {"g1", "g2"}
    got = {e["rec"]["grant_id"] for e in L.entries(["send_grant"], strict=True, newest_first=False)}
    assert got == {"g1", "g2"}


def test_strict_reads_of_sealed_files_are_checked_once(monkeypatch):
    """Closing review (performance): a sealed file is checked line by line once
    per signature, then its rows of those kinds come from memory."""
    for i in range(5):
        L.append("send_grant", {"event": "revoked", "grant_id": "g%d" % i}, durable=True)
    _seal()
    L.append("activity", {"kind": "x"})
    assert len(L.entries(["send_grant"], strict=True)) == 5
    calls = []
    real = L.entry_ok
    monkeypatch.setattr(L, "entry_ok", lambda e, k: calls.append(1) or real(e, k))
    for _ in range(3):
        assert len(L.entries(["send_grant"], strict=True)) == 5
    assert len(calls) <= 3 * 3, len(calls)          # the live file only (2 lines + migration)
