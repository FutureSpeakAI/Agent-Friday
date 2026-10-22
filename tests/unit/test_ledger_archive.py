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
