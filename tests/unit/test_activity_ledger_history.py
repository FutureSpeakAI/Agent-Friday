"""A9: the activity record keeps its history and shows its chain
(NS-11.8-2 "events are immutable facts", NS-11.8-3 "events must not be
rewritten to make the past appear cleaner").

Activity rows are entries of the one action ledger (services/action_ledger):
sealed segments never overwritten, one HMAC chain across them, and retention
only through the owner's archive period, which leaves a signed "archived"
entry in the chain. These pin that for activity rows and the A9 additions:
activity_ledger.verify_chain(), the "activity record" line of the integrity
view (Doctor record_integrity, GET /api/ledger/integrity, static/ledger.html),
no rotation that can drop history, and the privacy map's words.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_friday.governance import proof_of_integrity as poi
from agent_friday.services import action_ledger as L
from agent_friday.services import activity_ledger as al

ROOT = Path(__file__).resolve().parents[2]
DAY = 86400.0


@pytest.fixture(autouse=True)
def _scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", tmp_path / "gk")
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity_ledger.jsonl")
    L.reset_caches()
    yield
    L.reset_caches()


def _tools(n, prefix="tool"):
    for i in range(n):
        assert al.record("tool_call", tool=f"{prefix}_{i}", ok=True, duration_ms=i)


def _entries(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _seal():
    with L._TLOCK:
        L._check_dir()
        with L._lock():
            L._seal_live_locked(L.ledger_dir(), L.live_path())
    L.reset_caches()


def _sealed_with_activity(min_lines=3):
    """A sealed segment holding at least `min_lines` activity rows."""
    for p in L.segments():
        if p.name == L.LIVE_NAME:
            continue
        es = _entries(p)
        if sum(1 for e in es if e.get("kind") == "activity") >= min_lines:
            return p, es
    raise AssertionError("no sealed segment with %d activity rows" % min_lines)


# ── history kept, one chain across segments, readers see all of it ──────────

def test_history_across_many_segments_is_read_whole_and_chained(monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX", 300)
    _tools(20)
    segs = L.segments()
    assert len(segs) > 2, "small segments should seal several files"
    # readers (activity views, stats, cards) see every segment, newest first
    assert [e["tool"] for e in al.read(limit=100)] == [f"tool_{i}" for i in range(19, -1, -1)]
    # the chain continues across files: each file's first line links to the
    # last line of the file before it
    flat = [e for p in segs for e in _entries(p)]
    assert all(flat[i]["prev"] == flat[i - 1]["hash"] for i in range(1, len(flat)))
    v = al.verify_chain()
    assert v["state"] == "intact", v
    assert v["lines"] == v["chained"] == 20
    assert v["segments"] == len(segs) and v["activity_segments"] > 2
    assert v["breaks"] == v["activity_breaks"] == v["unchained"] == v["legacy_unchained"] == 0
    assert v["first_break"] is None and v["partial"] is False


def test_the_history_dropping_rotation_is_gone():
    """No size rotation (rename to `.1`, deleting the previous `.1`) exists
    in the activity record: nothing there can drop history."""
    for name in ("_maybe_rotate_locked", "_rotated_path", "_MAX_BYTES"):
        assert not hasattr(al, name), name
    _tools(3)
    assert not Path(str(al.LEDGER_FILE) + ".1").exists()


# ── tampering is found, with its location ───────────────────────────────────

def test_editing_a_middle_activity_line_is_a_break_with_its_location(monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX", 1600)
    _tools(16)
    p, es = _sealed_with_activity(3)
    i = next(k for k in range(1, len(es) - 1) if es[k].get("kind") == "activity")
    es[i]["rec"]["tool"] = es[i]["rec"]["tool"] + "_rewritten"
    p.write_text("".join(json.dumps(e) + "\n" for e in es), encoding="utf-8")
    L.reset_caches()
    v = al.verify_chain()
    assert v["state"] == "broken" and v["breaks"] >= 1 and v["activity_breaks"] >= 1
    fb = v["first_break"]
    assert fb["segment"] == "%s/%s" % (L.SEGMENTS_DIR, p.name)
    assert fb["seq"] == es[i]["seq"] and fb["line"] == i + 1
    assert "_rewritten" not in json.dumps(v), "a location, never the content"


def test_deleting_a_middle_activity_line_is_a_break_with_its_location(monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX", 1600)
    _tools(16)
    p, es = _sealed_with_activity(3)
    i = 1
    gone, after = es[i], es[i + 1]
    kept = es[:i] + es[i + 1:]
    p.write_text("".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
    L.reset_caches()
    v = al.verify_chain()
    assert v["state"] == "broken" and v["breaks"] >= 1
    fb = v["first_break"]
    assert fb["segment"] == "%s/%s" % (L.SEGMENTS_DIR, p.name)
    assert fb["seq"] == after["seq"] == gone["seq"] + 1
    assert "order" in fb["reason"] or "follow" in fb["reason"]


# ── retention leaves an honest, chained gap ─────────────────────────────────

def test_retention_leaves_a_chained_note_and_verifies_clean():
    _tools(4, "old")
    _seal()
    _tools(2, "new")
    out = L.archive_old(now=time.time() + 800 * DAY, days=730)
    assert out["archived"] == 1, out
    notes = [e["rec"] for e in L.entries(["archived"], newest_first=False)]
    assert notes and notes[-1]["segments"] and notes[-1]["records"] >= 4 and notes[-1]["last_hash"]
    v = al.verify_chain()
    assert v["state"] == "intact" and v["breaks"] == 0, v
    assert v["archived_notes"] == 1 and v["archived_segments"] == 1 and v["archived_records"] >= 4
    assert v["lines"] == 2, "live rows only; the archived ones are counted by the archive"
    assert L.verify()["ok"]


# ── rows from before chaining are reported as such, not as tampering ────────

def test_legacy_rows_are_before_chaining_not_breaks():
    for n in range(3):
        L.append("activity", {"kind": "tool_call", "ts": float(n), "tool": f"legacy_{n}", "ok": True,
                              "_migrated": {"from": "activity_ledger.jsonl", "line": n + 1,
                                            "file_sha256": "0" * 64, "legacy_verified": None}})
    _tools(2)
    v = al.verify_chain()
    assert v["legacy_unchained"] == 3 and v["chained"] == 2 and v["lines"] == 5
    assert v["breaks"] == v["activity_breaks"] == v["unchained"] == 0
    assert v["state"] == "before_chaining"
    al.LEDGER_FILE.write_text(json.dumps({"kind": "tool_call", "ts": 1.0, "tool": "w"}) + "\n",
                              encoding="utf-8")
    v = al.verify_chain()
    assert v["legacy_file_waiting"] is True and v["breaks"] == 0


# ── many writers, one unbroken chain ────────────────────────────────────────

def test_concurrent_appends_from_threads_produce_an_unbroken_chain(monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX", 4000)

    def writer(n):
        for i in range(25):
            al.record("tool_call", tool=f"w{n}_{i}", ok=True, duration_ms=1)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(L.segments()) > 1
    v = al.verify_chain()
    assert v["state"] == "intact", v
    assert v["lines"] == v["chained"] == 200 and v["breaks"] == 0
    flat = [e for p in L.segments() for e in _entries(p)]
    assert [e["seq"] for e in flat] == list(range(1, len(flat) + 1))


# ── bounded and fail-safe ───────────────────────────────────────────────────

def test_past_the_cap_it_says_partial():
    _tools(10)
    v = al.verify_chain(max_lines=3)
    assert v["partial"] is True and v["state"] == "partial" and v["scanned"] == 3


def test_it_never_raises(monkeypatch):
    _tools(1)

    def boom():
        raise OSError("unreadable")
    monkeypatch.setattr(L, "_reader_segments", boom)
    v = al.verify_chain()
    assert v["state"] == "error" and v["error"] == "OSError"


# ── the integrity view, the Doctor, the route and the page ──────────────────

def test_the_integrity_view_and_the_doctor_show_the_activity_record():
    from agent_friday.services import doctor, ledger_integrity
    from tests.ledger_support import tamper
    _tools(3)
    v = ledger_integrity.integrity()
    a = v["activity"]
    assert a["lines"] == 3 and a["chained"] == 3 and a["state"] == "intact"
    state, code = doctor._c_record_integrity()
    assert "act:ok/3/0/0" in code and len(code) <= 160
    tamper("activity", lambda r: r.get("tool") == "tool_1", lambda r: r.update(tool="tool_x"))
    state, code = doctor._c_record_integrity()            # a warning, never a crash
    assert state == "broken" and "act:break/" in code
    assert ledger_integrity.integrity()["activity"]["activity_breaks"] >= 1


def test_the_integrity_view_survives_a_failed_activity_check(monkeypatch):
    from agent_friday.services import doctor, ledger_integrity
    _tools(1)
    monkeypatch.setattr(al, "verify_chain", lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    v = ledger_integrity.integrity()
    assert v["activity_error"] == "RuntimeError" and v["ok"] is False and "chain" in v
    state, code = doctor._c_record_integrity()
    assert state in ("unverified", "broken") and "act:err/" in code


def test_the_route_and_the_page_carry_the_activity_record_line():
    from agent_friday import core
    from agent_friday.routes import ledger as ledger_routes
    _tools(2)
    with core.app.test_request_context("/api/ledger/integrity"):
        d = ledger_routes.api_ledger_integrity.__wrapped__().get_json()
    assert d["status"] == "ok" and d["integrity"]["activity"]["lines"] == 2
    html = (ROOT / "static" / "ledger.html").read_text(encoding="utf-8")
    assert 'id="activity-record"' in html and "Activity record" in html
    assert "v.activity" in html and "first_break" in html


# ── the privacy map tells the truth about it ────────────────────────────────

def test_the_privacy_map_no_longer_says_the_activity_record_rotates(monkeypatch):
    from agent_friday.services import privacy_map as pm
    receipts = next(s for s in pm._stores() if s["key"] == "receipts")
    assert "ledger" in receipts["paths"]
    monkeypatch.setattr(L, "archive_after_days", lambda: 730)
    words = receipts["ret"]()
    assert "rotates" not in words and "730 days" in words and "never deleted" in words
    monkeypatch.setattr(L, "archive_after_days", lambda: 0)
    assert "every entry stays" in receipts["ret"]()
