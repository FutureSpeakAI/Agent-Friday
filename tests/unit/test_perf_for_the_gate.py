"""Performance for the release gate (north star §34.15-1, §34.15-2).

Shared CI runners differ a lot in speed, so each test has two kinds of bound:
a wide wall-clock ceiling (catches a hang or a step change), and, where data
size is the question, a scaling rule: ten times the data must cost less than
twenty times the time (catches work that grows with the square of the store,
or a full re-read on every call). Synthetic data only; nothing reaches a model
or the network. Timings are printed so a CI log shows them."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"


def _timed(fn, *a, **k):
    t0 = time.perf_counter()
    out = fn(*a, **k)
    return time.perf_counter() - t0, out


def _scales(small_s: float, large_s: float, what: str, slack: float = 0.05) -> None:
    """Ten times the data costs under twenty times the time (plus a small
    absolute slack so a 1 ms baseline does not make noise a failure)."""
    print(f"[perf] {what}: small {small_s * 1000:.1f} ms, large {large_s * 1000:.1f} ms")
    assert large_s < 20 * small_s + slack, f"{what} grows faster than its data: {small_s:.4f}s -> {large_s:.4f}s"


# ── §34.15-1 startup ─────────────────────────────────────────────────────────

def test_a_cold_start_imports_the_server_in_time(tmp_path):
    """A fresh interpreter imports the whole server (every blueprint) with no
    model and no network, inside a wide ceiling."""
    env = dict(os.environ, FRIDAY_TESTING="1", FRIDAY_HOME=str(tmp_path / "home"),
               PYTHONPATH=str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    code = ("import time; t = time.perf_counter(); import agent_friday.server as s; "
            "print(round(time.perf_counter() - t, 3), len(s.app.blueprints))")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    seconds, blueprints = proc.stdout.strip().splitlines()[-1].split()
    print(f"[perf] cold import {seconds} s, {blueprints} blueprints")
    assert float(seconds) < 120 and int(blueprints) > 10


def test_restoring_tasks_at_start_grows_with_the_tasks_not_faster(tmp_path, monkeypatch):
    from agent_friday.services import agent as ag
    from agent_friday.services import task_journal as tj

    def restore(n, where):
        monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", where)
        tj.reset_for_tests()
        now = time.time()
        for i in range(n):
            tid = f"perf-{n}-{i}"
            tj.index_put(tid, f"Task {i}", "completed", now - 100, now - 50)
            tj.write_state(tid, {"task_id": tid, "name": f"Task {i}", "status": "completed",
                                 "created": now - 100, "ended": now - 50})
        with ag.TASKS_LOCK:
            ag.TASKS.clear()
        took, summary = _timed(ag._restore_tasks_from_journal, announce=False, limit=n)
        with ag.TASKS_LOCK:
            ag.TASKS.clear()
        return took, summary

    small, _ = restore(40, tmp_path / "a")
    large, summary = restore(400, tmp_path / "b")
    assert "error" not in summary and summary["loaded"] == 400 and summary.get("interrupted") == []
    _scales(small, large, "restore of journaled tasks", slack=0.5)
    assert large < 60


# ── §34.15-1 retrieval ───────────────────────────────────────────────────────

def test_conversation_search_stays_fast_as_history_grows(tmp_path, monkeypatch):
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversations as cv

    def build(n_convs, where):
        monkeypatch.setattr(cv, "_root", lambda: where)
        monkeypatch.setattr(fts, "_BUILT", {})
        monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
        for c in range(n_convs):
            cid = cv.create(f"Synthetic {c}")["id"]
            for m in range(30):
                cv.append(cid, {"role": "user", "text": f"note {c}-{m} about the quarterly plan and the harbour walk",
                                "meta": {"kind": "turn", "via": "chat"}})
        fts.ensure_built()
        cv.append(cid, {"role": "user", "text": "the zanzibar itinerary is final", "meta": {"kind": "turn", "via": "chat"}})
        took, hits = _timed(fts.search, "zanzibar itinerary")
        return took, hits

    small, hits = build(3, tmp_path / "a")
    assert hits and "zanzibar" in hits[0]["text"]
    large, hits = build(30, tmp_path / "b")
    assert hits and "zanzibar" in hits[0]["text"]
    _scales(small, large, "conversation search", slack=0.2)


# ── §34.15-2 large stores: the egress log on the per-message path ────────────

def _egress_line(i, reason="sent", provider="anthropic"):
    return json.dumps({"ts": 1_700_000_000 + i, "kind": "call", "provider": provider, "reason": reason,
                       "channel": "chat"}) + "\n"


class _CountingFile:
    """A file that counts the bytes read through it (text or binary)."""

    def __init__(self, f, counter):
        self._f, self._n = f, counter

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._f.close()
        return False

    def __iter__(self):
        for line in self._f:
            self._n[0] += len(line.encode("utf-8") if isinstance(line, str) else line)
            yield line

    def read(self, *a):
        data = self._f.read(*a)
        self._n[0] += len(data.encode("utf-8") if isinstance(data, str) else data)
        return data

    def __getattr__(self, name):
        return getattr(self._f, name)


def test_a_large_egress_log_does_not_slow_each_new_message(tmp_path, monkeypatch):
    """Every stored message asks which cloud models saw it (conversation_provenance
    reads the Gemini Live windows from the egress log). The log only grows; a
    new line must cost the new line, not the whole log again - counted in
    bytes read, so the answer does not depend on the runner's speed."""
    import builtins
    from agent_friday.services import conversation_provenance as cp
    log = tmp_path / "egress-log.jsonl"
    with open(log, "w", encoding="utf-8") as f:
        for i in range(120_000):
            f.write(_egress_line(i))
    first, _ = _timed(cp.gemini_windows, log)
    with open(log, "a", encoding="utf-8") as f:
        f.write(_egress_line(200_001, "live voice session opened", "gemini"))
    read = [0]
    real_open = builtins.open
    monkeypatch.setattr(cp, "open", lambda *a, **k: _CountingFile(real_open(*a, **k), read), raising=False)
    again, windows = _timed(cp.gemini_windows, log)
    size = log.stat().st_size
    print(f"[perf] egress log windows: first {first * 1000:.1f} ms; after one more line "
          f"{again * 1000:.1f} ms, {read[0]} of {size} bytes read")
    assert windows and windows[0][0] == 1_700_000_000 + 200_001
    assert read[0] < size // 100, f"one more line re-read the egress log ({read[0]} of {size} bytes)"


def test_the_windows_stay_right_when_read_a_piece_at_a_time(tmp_path):
    from agent_friday.services import conversation_provenance as cp
    log = tmp_path / "egress-log.jsonl"
    log.write_text(_egress_line(1, "live voice session opened", "gemini"), encoding="utf-8")
    assert cp.gemini_windows(log)[0][0] == 1_700_000_001                       # open: capped window
    with open(log, "a", encoding="utf-8") as f:
        f.write(_egress_line(5, "live voice session closed", "gemini")[:-10])  # half a line
    assert cp.gemini_windows(log)[0][1] != 1_700_000_005                       # not read yet
    with open(log, "a", encoding="utf-8") as f:
        f.write(_egress_line(5, "live voice session closed", "gemini")[-10:])
    assert cp.gemini_windows(log) == [(1_700_000_001, 1_700_000_005)]
    log.write_text(_egress_line(9, "live voice session opened", "gemini"), encoding="utf-8")   # replaced
    assert cp.gemini_windows(log)[0][0] == 1_700_000_009
    # Rewritten in place and longer than what was read: everything is read again.
    with open(log, "w", encoding="utf-8") as f:
        f.write(_egress_line(20, "live voice session opened", "gemini"))
        f.write(_egress_line(30, "live voice session closed", "gemini"))
        for i in range(50):
            f.write(_egress_line(100 + i))
    assert cp.gemini_windows(log) == [(1_700_000_020, 1_700_000_030)]


# ── §34.15-2 large stores: approvals ─────────────────────────────────────────

def test_the_approvals_store_answers_in_time_at_a_realistic_size(tmp_path, monkeypatch):
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    ids = []
    t_create, _ = _timed(lambda: [ids.append(approvals.create_approval(
        kind="tool_confirm", subject_type="tool", subject_id=f"perf-{i}", title=f"Synthetic {i}")["approval_id"])
        for i in range(300)])
    t_get, recs = _timed(lambda: [approvals.get_approval(a) for a in ids[::3]])
    print(f"[perf] approvals: 300 created in {t_create:.2f} s, 100 reads in {t_get:.2f} s")
    assert all(recs) and t_create < 60 and t_get < 20


# ── §34.15-2 receipt verification ────────────────────────────────────────────

def test_ten_thousand_receipts_verify_in_time(tmp_path, monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.privacy import vault_crypto as vc
    key = b"synthetic-governance-key-for-tests-0000000000000000"
    monkeypatch.setattr(ag, "friday_home", lambda: str(tmp_path))
    monkeypatch.setattr(ag, "_governance_key", lambda: key)
    for i in range(10_000):
        ag._receipt({"tool": "read_file", "class": "internal", "decision": "allow", "task_id": f"t{i % 50}"})
    took, report = _timed(vc.verify_bom_file, ag.receipts_path(), key)
    print(f"[perf] 10,000 receipts verified in {took:.2f} s")
    assert report["total"] == 10_000 and report["valid"] == 10_000
    assert took < 30


# ── §34.15-2 trigger storms ──────────────────────────────────────────────────

def test_a_storm_of_one_notice_is_one_card_and_stays_in_time():
    import agent_friday.notifications_engine as ne
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    # A registered needs-you emitter (E27), so the notice is a real tray card.
    took, _ = _timed(lambda: [ne.push(title="Synthetic task not recorded", body="synthetic", source="task-journal",
                                      kind="task_unrecorded", dedupe_key="task_unrecorded:perf")
                              for _ in range(500)])
    cards = [n for n in ne.list_notifications(limit=1000) if n.get("kind") == "task_unrecorded"]
    print(f"[perf] 500 identical notices in {took:.2f} s -> {len(cards)} card(s)")
    assert len(cards) == 1 and took < 60


def test_a_storm_of_different_notices_keeps_the_tray_bounded():
    import agent_friday.notifications_engine as ne
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    took, _ = _timed(lambda: [ne.push(title=f"Synthetic task {i} not recorded", source="task-journal",
                                      kind="task_unrecorded", dedupe_key=f"task_unrecorded:perf-{i}")
                              for i in range(400)])
    n = len(ne.list_notifications(include_dismissed=True, limit=10_000))
    print(f"[perf] 400 distinct notices in {took:.2f} s -> {n} kept")
    assert n <= 200 and took < 120
