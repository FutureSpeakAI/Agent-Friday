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


def test_an_approved_card_outside_a_tool_call_is_held_too(home):
    """Review round 1 (B1): authorize_external skipped the hold."""
    from agent_friday.services import authority
    authority.revoke_all("owner:test")
    v = action_gate.authorize_external("mail: archive 3 messages", {"ids": [1, 2, 3]},
                                       requested_by="mail proposals")
    assert v.action == "deny" and "revoked all action authority" in v.reason


def test_websites_are_narrowed_to_reading(home, monkeypatch):
    """Review round 1 (S1)."""
    from agent_friday.governance import web_sites
    from agent_friday.services import authority
    narrowed = []
    monkeypatch.setattr(web_sites, "all_sites", lambda: {"shop.example": {"level": "may_act"},
                                                         "news.example": {"level": "may_read"}})
    monkeypatch.setattr(web_sites, "narrow", lambda site, made_by: narrowed.append((site, made_by)) or {})
    out = authority.revoke_all("owner:cli")
    assert narrowed == [("shop.example", "owner:ui")] and out["sites_narrowed"] == ["shop.example"]
    assert "errors" not in out, out.get("errors")


def test_a_failed_marker_write_still_revokes_everything(home, monkeypatch):
    """Review round 1 (S2)."""
    from agent_friday.services import authority, scheduler
    paused = []
    monkeypatch.setattr(scheduler, "list_schedules", lambda: [{"id": "job-1", "enabled": True}])
    monkeypatch.setattr(scheduler, "update_schedule", lambda sid, patch: paused.append(sid) or {})
    real = authority._marker
    monkeypatch.setattr(authority, "_marker", lambda: real().parent / "nope" / "\0bad")
    out = authority.revoke_all("owner:test")
    assert paused == ["job-1"] and any("marker" in e for e in out.get("errors", []))
    monkeypatch.setattr(authority, "_marker", real)
    assert authority.revoked()


def test_the_hold_fails_closed_when_the_record_cannot_be_read(home, monkeypatch):
    """Review round 1 (S3)."""
    from agent_friday.services import authority
    monkeypatch.setattr(authority, "_newest_receipt", lambda: (_ for _ in ()).throw(OSError("x")))
    assert authority.revoked() is not None and _outward().action == "deny"


def test_an_unsigned_line_cannot_hide_a_fresh_revocation(home):
    """Review round 1 (S4): a planted huge seq moved the cache past the receipt."""
    from agent_friday.services import authority
    L.append("activity", {"kind": "x"})
    lines = L.live_path().read_text(encoding="utf-8").splitlines()
    lines.insert(0, json.dumps({"seq": 10 ** 12, "kind": "activity", "rec": {}}))
    L.live_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    authority.revoked()                                     # the cache reads the planted line
    L.reset_caches()
    L.live_path().write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")   # the chain kept whole
    L.reset_caches()
    authority._SEEN.clear()
    authority.revoked()
    authority.revoke_all("owner:test")
    authority._marker().unlink()
    authority._SEEN.update(rec=None)                       # as another process would see it
    assert authority.revoked() is not None


def test_a_revocation_moved_into_the_archive_still_holds(home):
    """Review round 1 (S5)."""
    from agent_friday.services import authority
    authority.revoke_all("owner:test")
    with L._TLOCK:
        L._check_dir()
        with L._lock():
            L._seal_live_locked(L.ledger_dir(), L.live_path())
    L.reset_caches()
    L.append("activity", {"kind": "x"})
    assert L.archive_old(now=time.time() + 800 * 86400, days=730)["archived"] == 1
    authority._marker().unlink()
    authority._SEEN.clear()                                # a fresh process
    assert authority.revoked() is not None


def test_a_channel_cannot_be_started_while_authority_is_revoked(home):
    """Review round 1 (S6)."""
    from agent_friday.services import authority
    from agent_friday.services.channels import manager
    authority.revoke_all("owner:test")
    r = manager.start_channel("telegram")
    assert r["ok"] is False and "revoked" in r["error"]


def test_the_detail_names_what_was_taken_back(home, monkeypatch):
    """Review round 1 (L1): the owner can give back what they want."""
    from agent_friday.governance import send_grants
    from agent_friday.services import authority
    monkeypatch.setattr(send_grants, "list_grants", lambda *a, **k: [{"grant_id": "sg-9"}])
    monkeypatch.setattr(send_grants, "revoke", lambda gid, by="owner": True)
    authority.revoke_all("owner:test")
    d = authority.status()["detail"]
    assert d["send_grants_revoked"] == ["sg-9"]


def test_an_approved_card_not_yet_used_is_withdrawn(home):
    """Review round 2 (M1): it would otherwise run by itself after a restore."""
    from agent_friday.services import approvals, authority
    card = approvals.create_approval(kind="governed_action", title="Archive mail", subject_type="external_action",
                                     subject_id="mail:abc", action_description="x", payload={}, force_gate=True)
    aid = card["approval_id"] if "approval_id" in card else card.get("approval", {}).get("approval_id")
    approvals.decide(aid, "approve")
    out = authority.revoke_all("owner:test")
    assert out["approved_withdrawn"] >= 1
    assert approvals.get_approval(aid)["status"] == "expired"


def test_one_grant_that_will_not_go_does_not_leave_the_others(home, monkeypatch):
    """Review round 2 (L1)."""
    from agent_friday.governance import send_grants
    from agent_friday.services import authority
    monkeypatch.setattr(send_grants, "list_grants", lambda *a, **k: [{"grant_id": "a"}, {"grant_id": "b"}])
    gone = []

    def revoke(gid, by="owner"):
        if gid == "a":
            raise OSError("locked")
        gone.append(gid)
        return True
    monkeypatch.setattr(send_grants, "revoke", revoke)
    out = authority.revoke_all("owner:test")
    assert gone == ["b"] and out["send_grants_revoked"] == ["b"]
    assert any(e.startswith("a:") for e in out["errors"])


def test_a_connector_tool_in_capitals_is_held_too(home):
    """Review round 2 (L4)."""
    from agent_friday.services import authority
    authority.revoke_all("owner:test")
    v = action_gate._decide("MCP_bank_get_balance", action_gate.INTERNAL, "", {}, False, {}, True)
    assert v.action == "deny" and "revoked" in v.reason


def test_a_ledger_break_is_named_not_called_a_revocation(monkeypatch, tmp_path):
    """CI on #308: with a broken ledger the hold must say the ledger, never
    that the owner revoked authority they did not revoke; restore refuses."""
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    from agent_friday.governance import action_gate
    from agent_friday.services import action_ledger, authority

    def broken(*a, **k):
        raise RuntimeError("break")
    monkeypatch.setattr(authority, "_newest_receipt", broken)
    why = authority.hold_reason()
    assert why and "ledger" in why and not why.startswith("you revoked")
    assert authority.status()["unreadable"] is True
    monkeypatch.setattr(action_ledger, "hold_reason", lambda: "the action ledger has a break")
    with pytest.raises(authority.AuthorityError):
        authority.restore()
    assert action_gate.outward_hold() == "the action ledger has a break"


def test_an_unreadable_archive_never_locks_the_owner_out(monkeypatch, tmp_path):
    """Review of the CI fix (1): with no open break, the owner's restore is
    written even though an archived part stays unreadable, and clears the hold."""
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    from agent_friday.services import action_ledger, authority
    real = authority._newest_receipt
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:                 # hold_reason, then restore's own check
            raise RuntimeError("archive segment unreadable")
        return real(*a, **k)
    monkeypatch.setattr(authority, "_newest_receipt", flaky)
    monkeypatch.setattr(action_ledger, "hold_reason", lambda: None)
    assert "archive segment unreadable" in authority.hold_reason()
    assert authority.restore()["restored"] is True
    assert authority.revoked() is None


def test_an_open_break_still_refuses_restore(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    from agent_friday.services import action_ledger, authority
    monkeypatch.setattr(authority, "_newest_receipt", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(action_ledger, "hold_reason", lambda: "the action ledger has a break")
    with pytest.raises(authority.AuthorityError):
        authority.restore()
