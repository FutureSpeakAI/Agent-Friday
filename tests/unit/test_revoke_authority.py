"""Revoke all action authority (north star §18.12, NS-18.12-1).

Red on the old code by assertion: there was no way to take back all action
authority at once; pending cards, grants, jobs and channels had to be found
and withdrawn one by one, and nothing held outward actions afterwards.
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import action_ledger as L


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    try:
        from agent_friday.services import authority
        authority._SEEN.clear()
    except ImportError:
        pass
    return h


def _outward():
    return action_gate.authorize("create_calendar_event", {"title": "x"}, {"is_background_task": True})


def test_revoking_all_authority_takes_everything_back_and_holds(home, monkeypatch):
    from agent_friday.services import approvals, authority, scheduler
    card = approvals.create_approval(kind="external_message", title="Send", subject_type="email",
                                     subject_id="s1", action_description="x", payload={}, force_gate=True)
    paused = []
    monkeypatch.setattr(scheduler, "list_schedules", lambda: [{"id": "job-1", "enabled": True},
                                                              {"id": "job-2", "enabled": False}])
    monkeypatch.setattr(scheduler, "update_schedule", lambda sid, patch: paused.append((sid, patch)) or {})
    from agent_friday.governance import send_grants
    revoked_sends = []
    monkeypatch.setattr(send_grants, "list_grants", lambda *a, **k: [{"grant_id": "sg-1"}])
    monkeypatch.setattr(send_grants, "revoke", lambda gid, by="owner": revoked_sends.append(gid) or True)
    assert _outward().action != "deny" or "revoked" not in _outward().reason
    out = authority.revoke_all("owner:test")
    assert out["approvals_expired"] >= 1
    assert approvals.get_approval(card["approval_id"] if isinstance(card, dict) and "approval_id" in card
                                  else card.get("approval", {}).get("approval_id"))["status"] == "expired"
    assert paused == [("job-1", {"enabled": False})] and out["jobs_paused"] == ["job-1"]
    assert revoked_sends == ["sg-1"]
    v = _outward()
    assert v.action == "deny" and "revoked all action authority" in v.reason
    read = action_gate.authorize("gmail_search", {"query": "x"}, {"is_background_task": True})
    if action_gate.classify("gmail_search", {"query": "x"})[0] == action_gate.OBSERVE:
        assert read.action == "deny" and "revoked" in read.reason
    kinds = [e["rec"]["event"] for e in L.entries(["authority"], newest_first=False)]
    assert kinds[:2] == ["revoked", "revoked_detail"]


def test_restoring_lifts_the_hold_only(home, monkeypatch):
    from agent_friday.services import authority
    authority.revoke_all("owner:test")
    assert authority.revoked()
    assert authority.restore("owner:test")["restored"] is True
    assert authority.revoked() is None
    assert "revoked all action authority" not in (_outward().reason or "")


def test_deleting_the_marker_does_not_restore_authority(home):
    from agent_friday.services import authority
    authority.revoke_all("owner:test")
    authority._marker().unlink()
    authority._SEEN.clear()
    assert authority.revoked() is not None
    assert _outward().action == "deny"


def test_a_marker_planted_without_a_receipt_holds_too(home):
    from agent_friday.services import authority
    authority._marker().parent.mkdir(parents=True, exist_ok=True)
    authority._marker().write_text(json.dumps({"at": "x", "by": "?"}), encoding="utf-8")
    assert authority.revoked() is not None


def test_nothing_changes_when_the_receipt_cannot_be_written(home, monkeypatch):
    from agent_friday.services import authority
    monkeypatch.setattr(L, "append", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(authority.AuthorityError):
        authority.revoke_all("owner:test")
    assert not authority._marker().exists()


def test_the_doctor_says_when_authority_is_revoked(home):
    from agent_friday.services import authority, doctor
    assert doctor._c_authority() == ("healthy", "in_force")
    authority.revoke_all("owner:test")
    assert doctor._c_authority() == ("blocked_by_policy", "authority_revoked")


def test_the_hold_reads_only_new_entries_each_time(home, monkeypatch):
    from agent_friday.services import authority
    for i in range(50):
        L.append("activity", {"kind": "x", "i": i})
    authority.revoked()
    seen = []
    real = L.entry_ok
    monkeypatch.setattr(L, "entry_ok", lambda e, k: seen.append(1) or real(e, k))
    L.append("activity", {"kind": "x"})
    authority.revoked()
    assert len(seen) <= 2, len(seen)
