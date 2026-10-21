"""One tamper-evident store for what Friday did.

North star §18.9 (receipts hash-chained; the owner can see whether one was
edited, removed or reordered), §36.9-2 and §37.2-5 (one receipt system, not
one per feature), burn-down BD-11b ("editing one receipt on disk makes the next
outward action hold with a visible reason").

Red on the old code by assertion: each writer kept its own file
(decision-bom.jsonl, actions/receipts.jsonl, activity_ledger.jsonl,
governance/dissent_events.jsonl, governance/send_grants_log.jsonl,
governance/web_sites_history.jsonl, media diet and inbox-cleanup receipts),
and editing, deleting or reordering a decision line held nothing.

These tests use only functions that exist before and after the change, and
find the store by looking at which files changed, so they judge behaviour,
not the module layout.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from agent_friday.governance import action_gate
from agent_friday.governance import proof_of_integrity as poi


@pytest.fixture
def scratch_home(tmp_path, monkeypatch):
    """A home of its own, a key in a file there, no keychain."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(home))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", home / "vault" / ".governance-key")
    monkeypatch.setitem(sys.modules, "keyring", None)
    try:
        from agent_friday.services import action_ledger
        action_ledger.reset_caches()
    except ImportError:
        pass
    return home


def _roots(home: Path) -> list[Path]:
    roots = [home]
    from agent_friday.core import FRIDAY_DIR
    roots.append(Path(FRIDAY_DIR))
    extra = os.environ.get("FRIDAY_LEDGER_DIR")
    if extra:
        roots.append(Path(extra))
    return roots


def _snapshot(home: Path) -> dict:
    out = {}
    for root in _roots(home):
        if not root.exists():
            continue
        for p in root.rglob("*.jsonl"):
            try:
                st = p.stat()
                out[str(p)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return out


def _changed(before: dict, home: Path) -> set:
    after = _snapshot(home)
    return {p for p, v in after.items() if before.get(p) != v}


def _decision(tool: str) -> None:
    action_gate._receipt({"tool": tool, "class": "outward", "decision": "allow",
                          "surface": "test", "reason": "test receipt"})


def _outward():
    return action_gate.authorize("create_calendar_event", {"title": "x"},
                                 {"is_background_task": True})


def _store_with(home: Path, before: dict, needle: str) -> Path:
    hits = [Path(p) for p in _changed(before, home)
            if needle in Path(p).read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


def test_every_kind_of_receipt_lands_in_one_file(scratch_home):
    from agent_friday.services import action_journal, activity_ledger, dissent_gate
    from agent_friday.services import inbox_cleanup, media_diet
    from agent_friday.governance import send_grants, web_sites
    action_journal.reset()
    _decision("probe_decision")              # make sure the key exists first
    before = _snapshot(scratch_home)

    _decision("probe_decision_2")
    action_journal.record("move_file", "move", "files", items=[{"path": "a"}],
                          summary="probe_journal", status="complete")
    activity_ledger.record("tool_call", tool="probe_activity", ok=True, duration_ms=1)
    dissent_gate.record_dissent_event("probe_dissent", {"category": "probe"})
    send_grants._log("used", grant_id="probe_grant", by="friday")
    web_sites._history({"event": "set", "site": "probe.example"})
    media_diet._receipt({"event": "probe_diet"})
    inbox_cleanup._receipt("probe_inbox")

    changed = _changed(before, scratch_home)
    assert len(changed) == 1, sorted(changed)
    text = Path(next(iter(changed))).read_text(encoding="utf-8")
    for needle in ("probe_decision_2", "probe_journal", "probe_activity", "probe_dissent",
                   "probe_grant", "probe.example", "probe_diet", "probe_inbox"):
        assert needle in text, needle


def test_an_intact_ledger_does_not_hold(scratch_home):
    _decision("probe_a")
    v = _outward()
    assert v.action != "deny", v.reason


def test_editing_one_receipt_holds_the_next_outward_action(scratch_home):
    _decision("probe_first")
    before = _snapshot(scratch_home)
    _decision("probe_edit_me")
    _decision("probe_after")
    store = _store_with(scratch_home, before, "probe_edit_me")
    raw = store.read_text(encoding="utf-8")
    store.write_text(raw.replace("probe_edit_me", "probe_edit_ME"), encoding="utf-8")

    v = _outward()
    assert v.action == "deny", v
    assert "ledger" in v.reason and "changed" in v.reason, v.reason


def test_removing_a_receipt_from_the_middle_holds(scratch_home):
    _decision("probe_0")
    before = _snapshot(scratch_home)
    for i in range(1, 4):
        _decision(f"probe_mid_{i}")
    store = _store_with(scratch_home, before, "probe_mid_2")
    lines = store.read_text(encoding="utf-8").splitlines(keepends=True)
    store.write_text("".join(ln for ln in lines if "probe_mid_2" not in ln), encoding="utf-8")

    v = _outward()
    assert v.action == "deny" and "ledger" in v.reason, v


def test_reordering_receipts_holds(scratch_home):
    _decision("probe_0")
    before = _snapshot(scratch_home)
    _decision("probe_ord_1")
    _decision("probe_ord_2")
    store = _store_with(scratch_home, before, "probe_ord_1")
    lines = store.read_text(encoding="utf-8").splitlines(keepends=True)
    i1 = next(i for i, ln in enumerate(lines) if "probe_ord_1" in ln)
    i2 = next(i for i, ln in enumerate(lines) if "probe_ord_2" in ln)
    lines[i1], lines[i2] = lines[i2], lines[i1]
    store.write_text("".join(lines), encoding="utf-8")

    v = _outward()
    assert v.action == "deny" and "ledger" in v.reason, v


def test_cutting_receipts_off_the_end_holds(scratch_home):
    _decision("probe_0")
    before = _snapshot(scratch_home)
    _decision("probe_keep")
    _decision("probe_cut")
    store = _store_with(scratch_home, before, "probe_cut")
    lines = store.read_text(encoding="utf-8").splitlines(keepends=True)
    store.write_text("".join(ln for ln in lines if "probe_cut" not in ln), encoding="utf-8")

    v = _outward()
    assert v.action == "deny" and "ledger" in v.reason, v


def test_a_held_send_from_an_executor_is_refused_too(scratch_home):
    _decision("probe_0")
    before = _snapshot(scratch_home)
    _decision("probe_exec")
    store = _store_with(scratch_home, before, "probe_exec")
    store.write_text(store.read_text(encoding="utf-8").replace("probe_exec", "probe_EXEC"),
                     encoding="utf-8")
    with pytest.raises(action_gate.Held) as err:
        action_gate.record_external("send_email", surface="test", approval_id="card-1")
    assert "ledger" in str(err.value)


def test_old_receipt_files_are_brought_in_once_and_kept(scratch_home):
    """Receipts written before the change still show, and the old files are
    kept whole (moved, never edited or deleted)."""
    from agent_friday.services import action_journal, activity_ledger
    from agent_friday.services import morning_receipt
    # the key first, then write lines in the old formats where the old code kept them
    key = poi.get_governance_key()
    old = {"tool": "probe_legacy_send", "class": "outward", "decision": "allow",
           "surface": "test", "timestamp": "2026-10-01T10:00:00Z"}
    import hashlib
    import hmac
    body = json.dumps(old, sort_keys=True, default=str).encode()
    old["hmac"] = hmac.new(key, body, hashlib.sha256).hexdigest()
    bom = Path(action_gate.receipts_path())
    bom.parent.mkdir(parents=True, exist_ok=True)
    bom_bytes = (json.dumps(old) + "\n").encode()
    bom.write_bytes(bom_bytes)
    journal = Path(action_journal._path())
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({"receipt_id": "rcpt_00000000001legacy", "v": 1,
                                   "tool": "move_file", "status": "complete",
                                   "summary": "probe legacy move", "items": [],
                                   "undo": {}}) + "\n", encoding="utf-8")
    act = Path(activity_ledger.LEDGER_FILE)
    act_before = act.read_bytes() if act.exists() else b""
    act.parent.mkdir(parents=True, exist_ok=True)
    with open(act, "ab") as f:
        f.write((json.dumps({"kind": "tool_call", "ts": 1.0, "tool": "probe_legacy_tool",
                             "ok": True}) + "\n").encode())
    action_journal.reset()
    try:
        from agent_friday.services import action_ledger
        action_ledger.reset_caches()
    except ImportError:
        pass

    rep = morning_receipt.build(since=0)
    sends = [a for a in rep["actions"] if a["tool"] == "probe_legacy_send"]
    assert len(sends) == 1 and sends[0]["verified"] is True, rep["actions"]
    assert any(r.get("summary") == "probe legacy move" for r in action_journal.recent(50))
    assert any(e.get("tool") == "probe_legacy_tool" for e in activity_ledger.read(limit=1000))

    # the old decision file is gone from where it was, kept unchanged elsewhere
    assert not bom.exists(), "the old decision file is still being used"
    kept = [p for root in _roots(scratch_home) if root.exists()
            for p in root.rglob("*decision-bom.jsonl")]
    assert kept and any(p.read_bytes() == bom_bytes for p in kept), kept
    del act_before

    # running again does not bring them in twice
    try:
        from agent_friday.services import action_ledger
        action_ledger.reset_caches()
    except ImportError:
        pass
    rep2 = morning_receipt.build(since=0)
    assert len([a for a in rep2["actions"] if a["tool"] == "probe_legacy_send"]) == 1


def test_a_receipt_that_cannot_be_written_still_holds_the_action(scratch_home, monkeypatch):
    """The one store keeps the old rule: no receipt, no action (§18.7)."""
    import builtins
    real_open = builtins.open

    def guarded_open(file, mode="r", *a, **k):
        if ("a" in str(mode)) and str(file).endswith(".jsonl"):
            raise OSError("disk full")
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr(builtins, "open", guarded_open)
    with pytest.raises(action_gate.Held):
        action_gate.record_external("send_email", surface="test", approval_id="card-1")
