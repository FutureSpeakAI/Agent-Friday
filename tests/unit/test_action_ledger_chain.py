"""The action ledger's own guarantees (services/action_ledger.py).

The behaviour Friday shows for these (outward actions held) is in
test_one_action_ledger.py; these pin the chain itself: what verify() reports,
what an acknowledgement covers, an unfinished write, records cut off the end,
segments, and a resumed migration.
"""
from __future__ import annotations

import json
import sys

import pytest

from agent_friday.governance import proof_of_integrity as poi
from agent_friday.services import action_ledger as L


@pytest.fixture(autouse=True)
def key_in_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", tmp_path / "gk")
    monkeypatch.setitem(sys.modules, "keyring", None)
    L.reset_caches()


def _lines():
    return L.live_path().read_text(encoding="utf-8").splitlines()


def _write(lines):
    L.live_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    L.reset_caches()


def test_a_fresh_chain_verifies_and_links_every_line():
    for i in range(5):
        L.append("probe", {"i": i})
    rep = L.verify()
    assert rep["intact"] and rep["ok"] and rep["records"] == 5 and rep["last_seq"] == 5
    es = [json.loads(x) for x in _lines()]
    assert es[0]["prev"] == L.GENESIS
    assert all(es[i]["prev"] == es[i - 1]["hash"] for i in range(1, 5))
    assert all(e["principal"] == "owner" for e in es)


def test_verify_names_the_line_never_its_content():
    for i in range(3):
        L.append("probe", {"secret": f"PRIVATE-{i}"})
    ls = _lines()
    e = json.loads(ls[1])
    e["rec"]["secret"] = "PRIVATE-X"
    ls[1] = json.dumps(e)
    _write(ls)
    rep = L.verify()
    assert not rep["ok"]
    assert [p["seq"] for p in rep["problems"]] == [2]
    assert "PRIVATE" not in json.dumps(rep)


def test_a_signature_from_another_key_is_reported():
    L.append("probe", {"i": 1})
    ls = _lines()
    e = json.loads(ls[0])
    e["sig"] = "0" * 64
    ls[0] = json.dumps(e)
    _write(ls)
    rep = L.verify()
    assert any("signature" in p["reason"] for p in rep["problems"])


def test_an_acknowledgement_covers_exactly_the_lines_it_names():
    for i in range(4):
        L.append("probe", {"i": i})
    ls = _lines()
    e = json.loads(ls[1]); e["rec"]["i"] = 99; ls[1] = json.dumps(e)
    _write(ls)
    assert L.hold_reason()
    out = L.acknowledge("owner:test")
    assert out["acknowledged"] >= 1 and L.hold_reason() is None
    # the same line changed again is a new break
    ls = _lines()
    e = json.loads(ls[1]); e["rec"]["i"] = 100; ls[1] = json.dumps(e)
    _write(ls)
    assert L.hold_reason()


def test_an_unfinished_write_is_set_aside_and_recorded():
    L.append("probe", {"i": 1})
    with open(L.live_path(), "ab") as f:
        f.write(b'{"v":1,"seq":2,"kind":"pro')          # a crash mid-write
    L.reset_caches()
    L.append("probe", {"i": 2})
    kinds = [json.loads(x)["kind"] for x in _lines()]
    assert kinds == ["probe", "torn_tail", "probe"]
    kept = list((L.ledger_dir() / "torn").iterdir())
    assert kept and kept[0].read_bytes().startswith(b'{"v":1,"seq":2')
    rep = L.verify()
    assert rep["intact"] is False and any("unfinished" in p["reason"] for p in rep["problems"])


def test_records_cut_off_the_end_stay_on_the_record_after_the_next_write():
    for i in range(4):
        L.append("probe", {"i": i})
    _write(_lines()[:2])
    assert L.hold_reason() and "missing" in L.hold_reason()
    L.append("probe", {"i": "after"})               # the head moves on ...
    rep = L.verify()
    assert any("missing" in p["reason"] for p in rep["problems"])   # ... the break does not
    assert L.hold_reason()


def test_segments_continue_one_chain(monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX", 400)
    for i in range(12):
        L.append("probe", {"i": i, "pad": "x" * 40})
    assert len(L.segments()) > 2
    rep = L.verify()
    assert rep["ok"] and rep["records"] == 12
    assert [e["rec"]["i"] for e in L.entries(["probe"], newest_first=False)] == list(range(12))


def test_a_ledger_that_cannot_be_checked_holds(monkeypatch):
    L.append("probe", {"i": 1})
    def boom(*a, **k):
        raise OSError("unreadable")
    monkeypatch.setattr(L, "_verify_files", boom)
    L.reset_caches()
    assert "could not be checked" in (L.hold_reason() or "")


def test_a_half_done_migration_continues_without_duplicates(tmp_path, monkeypatch):
    from agent_friday.services import activity_ledger as al
    legacy = tmp_path / "activity_ledger.jsonl"
    rows = [{"kind": "tool_call", "ts": float(i), "tool": f"old_{i}", "ok": True} for i in range(6)]
    legacy.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(al, "LEDGER_FILE", legacy)
    digest = L._file_sha256(legacy)
    # three lines already brought in before Friday stopped (planted with the
    # automatic first-use migration held off)
    L._check_dir()
    L._STATE["migrated"] = True
    for n, r in enumerate(rows[:3], 1):
        L.append("activity", dict(r, _migrated={"from": "activity_ledger.jsonl", "line": n,
                                                "file_sha256": digest, "legacy_verified": None}))
    L.reset_caches()
    L.migrate_legacy()
    tools = [e["rec"]["tool"] for e in L.entries(["activity"], newest_first=False)]
    assert tools == [f"old_{i}" for i in range(6)]
    assert not legacy.exists()
    mig = [e["rec"] for e in L.entries(["migration"])]
    assert mig and mig[0]["lines"] == 6 and mig[0]["file_sha256"] == digest
    L.reset_caches()
    L.migrate_legacy()                                # nothing left to do
    assert len(L.entries(["activity"])) == 6
