"""NS-36.3-12 (BD-11a, slice A7): the last outward paths that left no decision.

A6 closed the paths an allowing `authorize_external` let through. The card
decision hooks of Media's publish and tidy still carried out the approved work
with no decision written, so nothing could close. Here:

  * each hook writes exactly one decision (record_external, the same action
    name as its non-card path, the approved card's id) and exactly one
    terminal outcome from its own result: executed on success, failed on a
    failure and on an exception;
  * a decision that cannot be written (action_gate.Held, or any other error)
    stops the work: nothing runs, no outcome is written, the card is marked
    used with the reason and the owner is told, as publish_web's hook does;
  * the outward audit over a run of them finds nothing open or unconfirmed;
  * the podcast format tool, when the setting hold itself fails, answers an
    error and writes nothing (like the host-name and length tools).
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(approvals, "claim_for_execution", lambda aid: True)
    monkeypatch.setattr(approvals, "mark_used", lambda *a, **k: None)
    monkeypatch.setattr(G, "verify_claws", lambda *a, **k: (True, "intact"))
    monkeypatch.setattr(G, "outward_hold", lambda: None)
    yield


def _decisions(tool):
    return [e["rec"] for e in L.entries(["decision"], newest_first=False)
            if e["rec"].get("tool") == tool and e["rec"].get("class") == G.OUTWARD]


def _outcomes(tool):
    return [e["rec"] for e in L.entries(["outcome"], newest_first=False)
            if e["rec"].get("tool") == tool]


def _one_decision_one_outcome(tool, approval_id, word):
    ds = _decisions(tool)
    assert len(ds) == 1, ds
    assert ds[0].get("approval") == approval_id
    outs = _outcomes(tool)
    assert len(outs) == 1, outs
    assert outs[0]["outcome"] == word
    assert outs[0]["action_id"] == ds[0]["action_id"]
    assert outs[0].get("terminal") is True


# ── Media publish, from its card ────────────────────────────────────────────

def _publish_hook(mp, how, aid="ap-pub"):
    from agent_friday.services import media_index as mi
    ran = []

    def complete(cid, approval_id=None):
        ran.append((cid, approval_id))
        if how == "raise":
            raise RuntimeError("the index is locked")
        return {"status": "ok" if how == "ok" else "error", "message": "m"}
    mp.setattr(mi, "complete_publish", complete)
    mi._on_decision({"status": "approved", "approval_id": aid,
                     "payload": {"handler": mi.HANDLER, "card": "card-1"}})
    assert ran == [("card-1", aid)]
    return mi.PUBLISH_ACTION


@pytest.mark.parametrize("how, word", [("ok", G.EXECUTED), ("error", G.FAILED), ("raise", G.FAILED)])
def test_the_publish_hook_writes_one_decision_and_one_terminal_outcome(monkeypatch, how, word):
    tool = _publish_hook(monkeypatch, how)
    _one_decision_one_outcome(tool, "ap-pub", word)


def test_the_publish_hook_ignores_other_cards(monkeypatch):
    from agent_friday.services import media_index as mi
    monkeypatch.setattr(mi, "complete_publish", lambda *a, **k: pytest.fail("ran"))
    mi._on_decision({"status": "approved", "approval_id": "x", "payload": {"handler": "other"}})
    mi._on_decision({"status": "denied", "approval_id": "y", "payload": {"handler": mi.HANDLER, "card": "c"}})
    assert _decisions(mi.PUBLISH_ACTION) == [] and _outcomes(mi.PUBLISH_ACTION) == []


# ── Media tidy, from its card ───────────────────────────────────────────────

def _tidy_hook(mp, how, aid="ap-tidy"):
    from agent_friday.services import media_tidy as mt
    ran = []

    def apply(pid, approval_id=None):
        ran.append((pid, approval_id))
        if how == "raise":
            raise RuntimeError("the trash is locked")
        if how == "ok":
            return {"status": "ok", "moved": [{"id": "c1"}], "failed": []}
        return {"status": "ok", "moved": [], "failed": [{"id": "c1", "message": "locked"}]}
    mp.setattr(mt, "apply", apply)
    mt._on_decision({"status": "approved", "approval_id": aid,
                     "payload": {"handler": mt.HANDLER, "proposal": "tidy_1"}})
    assert ran == [("tidy_1", aid)]
    return mt.TIDY_ACTION


@pytest.mark.parametrize("how, word", [("ok", G.EXECUTED), ("nothing", G.FAILED), ("raise", G.FAILED)])
def test_the_tidy_hook_writes_one_decision_and_one_terminal_outcome(monkeypatch, how, word):
    tool = _tidy_hook(monkeypatch, how)
    _one_decision_one_outcome(tool, "ap-tidy", word)


# ── a decision that cannot be written stops the work (fails closed) ─────────

def _run_hooks_failing_to_record(mp, exc):
    """Both hooks with record_external raising `exc`: what ran, what was marked
    used, what the owner was told."""
    from agent_friday.services import media_index as mi, media_tidy as mt

    def failing(*a, **k):
        raise exc
    mp.setattr(G, "record_external", failing)
    used, told = [], []
    mp.setattr(approvals, "mark_used", lambda aid, *a, **k: used.append((aid, a, k)))
    mp.setattr(mi, "_notify", lambda text, ref: told.append((ref, text)))
    mp.setattr(mi, "complete_publish", lambda *a, **k: pytest.fail("published with no decision"))
    mp.setattr(mt, "apply", lambda *a, **k: pytest.fail("tidied with no decision"))
    mi._on_decision({"status": "approved", "approval_id": "ap-pub",
                     "payload": {"handler": mi.HANDLER, "card": "card-1", "title": "Walk"}})
    mt._on_decision({"status": "approved", "approval_id": "ap-tidy",
                     "payload": {"handler": mt.HANDLER, "proposal": "tidy_1"}})
    assert _outcomes(mi.PUBLISH_ACTION) == [] and _outcomes(mt.TIDY_ACTION) == []
    assert _decisions(mi.PUBLISH_ACTION) == [] and _decisions(mt.TIDY_ACTION) == []
    # the card is spent like publish_web's on a hold: marked used, not ok, with the reason
    assert [u[0] for u in used] == ["ap-pub", "ap-tidy"]
    for _aid, args, _kw in used:
        assert args[0] == "media" and args[1]["ok"] is False and args[1]["error"]
    assert [t[0] for t in told] == ["card-1", "tidy_1"]
    assert "Nothing was published" in told[0][1] and "Nothing was moved" in told[1][1]
    return used, told


def test_a_hold_when_recording_stops_the_approved_work(monkeypatch):
    used, told = _run_hooks_failing_to_record(
        monkeypatch, G.Held("the signed receipt could not be written"))
    for _aid, args, _kw in used:
        assert args[1]["error"] == "held: the signed receipt could not be written"
    assert all("the signed receipt could not be written" in t[1] for t in told)


def test_any_other_recording_error_also_stops_the_approved_work(monkeypatch):
    used, told = _run_hooks_failing_to_record(monkeypatch, RuntimeError("ledger gone"))
    for _aid, args, _kw in used:
        assert args[1]["error"].startswith("the decision could not be recorded (error ")
    assert all("the decision could not be recorded" in t[1] for t in told)


def test_a_closing_that_fails_never_raises_into_the_hook(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ledger gone")
    monkeypatch.setattr(G, "close_external", boom)
    _publish_hook(monkeypatch, "ok")
    _tidy_hook(monkeypatch, "ok")


# ── the audit over a run of them ────────────────────────────────────────────

def test_the_audit_over_the_hooks_finds_nothing_open_or_unconfirmed(monkeypatch):
    from agent_friday.services import outward_audit
    n = 0
    for how in ("ok", "error", "raise"):
        with monkeypatch.context() as mp:
            _publish_hook(mp, how, aid="ap-pub-" + how)
            n += 1
    for how in ("ok", "nothing", "raise"):
        with monkeypatch.context() as mp:
            _tidy_hook(mp, how, aid="ap-tidy-" + how)
            n += 1
    outward_audit.reset_cache()
    r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])
    c = r["counts"]
    assert c["open"] == 0 and c["unconfirmed"] == 0 and c["duplicate"] == 0, r
    assert c["closed"] >= n


# ── podcast format: a hold that fails writes nothing ────────────────────────

def test_the_podcast_format_tool_answers_an_error_when_the_hold_fails(monkeypatch):
    from agent_friday.services import podcast_tools as pt
    from agent_friday.services import setting_proposals as sp
    monkeypatch.setattr(pt.pe, "check_format", lambda routine, fmt: None)
    monkeypatch.setattr(pt.pe, "format_for", lambda routine: "solo")
    monkeypatch.setattr(pt.pe, "set_format", lambda *a, **k: pytest.fail("written without its check"))

    def broken(*a, **k):
        raise RuntimeError("the proposals file is locked")
    monkeypatch.setattr(sp, "hold", broken)
    reply = json.loads(pt._tool_podcast_format({"routine": "front_page", "format": "duo"}))
    assert reply["status"] == "error"
    assert reply["message"]
