"""Unit tests for services/approvals.py — the general approval queue
(V6_WHOLENESS_SPEC.md §4 Phase 5 / AUTONOMY_SPEC.md §8 A3, item 4).

Covers: Q3 policy classification (outward/irreversible/spend/external_
message gated by default; internal auto-proceeds), gate_action's block-
until-approved + idempotent-consume contract, decide()'s approve/deny/no-op-
on-non-pending semantics, expiry (never auto-approves), settings-editable
policy overrides, and the Law-1-overrides-everything invariant (mirrors
tests/unit/test_dissent_gate.py's own Law-1 contract, since this module
delegates the actual harm floor to dissent_gate/content_policies for real —
not monkeypatched).
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import approvals


@pytest.fixture(autouse=True)
def _iso_approvals(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield


# ═══════════════════════════════════════════════════════════════════════════
#  Q3 policy classification
# ═══════════════════════════════════════════════════════════════════════════

class TestClassify:
    @pytest.mark.parametrize("action", [
        "Send an email to the whole team",
        "Post this update publicly",
        "Publish the blog post now",
    ])
    def test_outward_is_gated(self, action):
        result = approvals.classify(action)
        assert result["gated"] is True

    @pytest.mark.parametrize("action", [
        "Permanently delete the backups folder",
        "Wipe the old drafts directory",
    ])
    def test_irreversible_is_gated(self, action):
        assert approvals.classify(action)["gated"] is True

    @pytest.mark.parametrize("action", [
        "Buy the annual subscription",
        "Transfer funds to the contractor",
    ])
    def test_spend_is_gated(self, action):
        assert approvals.classify(action)["gated"] is True

    @pytest.mark.parametrize("action", [
        "Draft an outline for the blog post",
        "Summarize this document",
        "Research competitor pricing",
    ])
    def test_internal_drafting_is_not_gated(self, action):
        result = approvals.classify(action)
        assert result["gated"] is False
        assert result["policy_class"] == "internal"

    def test_explicit_action_class_override_wins(self):
        result = approvals.classify("Draft a memo", action_class="spend")
        assert result["policy_class"] == "spend"
        assert result["gated"] is True

    def test_every_policy_class_has_a_default_entry(self):
        for cls in ("outward", "irreversible", "spend", "external_message", "internal"):
            assert cls in approvals.POLICY_TABLE_DEFAULTS

    def test_settings_override_can_relax_a_class(self, monkeypatch):
        monkeypatch.setattr(approvals, "_load_settings",
                           lambda: {"approvals_policy": {"spend": {"gated": False}}})
        assert approvals.classify("Buy a new domain name")["gated"] is False

    def test_settings_override_can_tighten_internal(self, monkeypatch):
        monkeypatch.setattr(approvals, "_load_settings",
                           lambda: {"approvals_policy": {"internal": {"gated": True}}})
        assert approvals.classify("Draft the quarterly summary")["gated"] is True


# ═══════════════════════════════════════════════════════════════════════════
#  gate_action — blocks until approved; idempotent consume
# ═══════════════════════════════════════════════════════════════════════════

class TestGateAction:
    def test_internal_action_auto_approves(self):
        result = approvals.gate_action(
            kind="test", subject_type="widget", subject_id="w1", title="Draft it",
            action_description="Draft the internal memo")
        assert result["status"] == "auto_approved"

    def test_outward_action_blocks_pending(self):
        result = approvals.gate_action(
            kind="test", subject_type="widget", subject_id="w2", title="Send it",
            action_description="Send an email to the client")
        assert result["status"] == "pending"
        assert result["approval"]["status"] == "pending"

    def test_repeated_call_before_decision_stays_pending(self):
        first = approvals.gate_action(kind="test", subject_type="widget", subject_id="w3",
                                      title="Send it", action_description="Send an email")
        second = approvals.gate_action(kind="test", subject_type="widget", subject_id="w3",
                                       title="Send it", action_description="Send an email")
        assert first["status"] == "pending"
        assert second["status"] == "pending"
        assert first["approval"]["approval_id"] == second["approval"]["approval_id"]

    def test_approved_then_gate_action_reports_approved_once_then_auto_approved(self):
        pending = approvals.gate_action(kind="test", subject_type="widget", subject_id="w4",
                                        title="Send it", action_description="Send an email")
        aid = pending["approval"]["approval_id"]
        approvals.decide(aid, "approve")

        first_after = approvals.gate_action(kind="test", subject_type="widget", subject_id="w4",
                                            title="Send it", action_description="Send an email")
        assert first_after["status"] == "approved"

        second_after = approvals.gate_action(kind="test", subject_type="widget", subject_id="w4",
                                             title="Send it", action_description="Send an email")
        assert second_after["status"] == "auto_approved"

    def test_denied_reports_denied(self):
        pending = approvals.gate_action(kind="test", subject_type="widget", subject_id="w5",
                                        title="Send it", action_description="Send an email")
        approvals.decide(pending["approval"]["approval_id"], "deny")
        result = approvals.gate_action(kind="test", subject_type="widget", subject_id="w5",
                                       title="Send it", action_description="Send an email")
        assert result["status"] == "denied"

    def test_force_gate_gates_an_otherwise_internal_action(self):
        result = approvals.gate_action(kind="test", subject_type="widget", subject_id="w6",
                                       title="Draft it", action_description="Draft the memo",
                                       force_gate=True)
        assert result["status"] == "pending"


# ═══════════════════════════════════════════════════════════════════════════
#  decide() — approve/deny + non-pending no-op
# ═══════════════════════════════════════════════════════════════════════════

class TestDecide:
    def _pending(self, subject_id="s1"):
        return approvals.create_approval(
            kind="test", subject_type="widget", subject_id=subject_id,
            title="Send it", action_description="Send an email to the client")

    def test_approve_sets_status_and_metadata(self):
        appr = self._pending()
        out = approvals.decide(appr["approval_id"], "approve", decided_by="stephen", note="looks fine")
        assert out["status"] == "approved"
        assert out["decided_by"] == "stephen"
        assert out["decision_note"] == "looks fine"
        assert out["decided_at"] is not None

    def test_deny_sets_status(self):
        appr = self._pending()
        out = approvals.decide(appr["approval_id"], "deny")
        assert out["status"] == "denied"

    def test_invalid_decision_raises(self):
        appr = self._pending()
        with pytest.raises(ValueError):
            approvals.decide(appr["approval_id"], "maybe")

    def test_unknown_approval_returns_none(self):
        assert approvals.decide("appr_nope", "approve") is None

    def test_deciding_an_already_decided_card_is_a_noop(self):
        appr = self._pending()
        first = approvals.decide(appr["approval_id"], "approve", decided_by="a")
        second = approvals.decide(appr["approval_id"], "deny", decided_by="b")
        assert second["status"] == "approved"  # unchanged — 'deny' never applied
        assert second["decided_by"] == "a"

    def test_auto_approved_card_cannot_be_re_decided(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="s2",
                                         title="Draft it", action_description="Draft a memo")
        assert appr["status"] == "auto_approved"
        out = approvals.decide(appr["approval_id"], "deny")
        assert out["status"] == "auto_approved"  # decide() can't move a non-pending card

    def test_decision_hook_fires_on_approve(self):
        seen = []
        approvals.register_decision_hook("hook_test_kind", seen.append)
        appr = approvals.create_approval(kind="hook_test_kind", subject_type="widget",
                                         subject_id="s3", title="Send it",
                                         action_description="Send an email")
        approvals.decide(appr["approval_id"], "approve")
        assert len(seen) == 1
        assert seen[0]["status"] == "approved"

    def test_broken_hook_never_raises_to_caller(self):
        def _boom(_approval):
            raise RuntimeError("hook exploded")
        approvals.register_decision_hook("boom_kind", _boom)
        appr = approvals.create_approval(kind="boom_kind", subject_type="widget",
                                         subject_id="s4", title="Send it",
                                         action_description="Send an email")
        out = approvals.decide(appr["approval_id"], "approve")  # must not raise
        assert out["status"] == "approved"


# ═══════════════════════════════════════════════════════════════════════════
#  Expiry — never auto-approves; expiry_paused is honored
# ═══════════════════════════════════════════════════════════════════════════

class TestExpiry:
    def test_past_expiry_moves_pending_to_expired(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="e1",
                                         title="Send it", action_description="Send an email")
        approvals._patch(appr["approval_id"], expires_at=time.time() - 5)
        count = approvals.expire_stale()
        assert count == 1
        assert approvals.get_approval(appr["approval_id"])["status"] == "expired"

    def test_expired_is_never_treated_as_approved(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="e2",
                                         title="Send it", action_description="Send an email")
        approvals._patch(appr["approval_id"], expires_at=time.time() - 5)
        approvals.expire_stale()
        result = approvals.gate_action(kind="test", subject_type="widget", subject_id="e2",
                                       title="Send it", action_description="Send an email")
        assert result["status"] == "denied"  # expired reads as blocked, never approved

    def test_future_expiry_is_left_pending(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="e3",
                                         title="Send it", action_description="Send an email")
        count = approvals.expire_stale()
        assert count == 0
        assert approvals.get_approval(appr["approval_id"])["status"] == "pending"

    def test_expiry_paused_is_never_swept(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="e4",
                                         title="Send it", action_description="Send an email")
        approvals._patch(appr["approval_id"], expires_at=time.time() - 5, expiry_paused=True)
        count = approvals.expire_stale()
        assert count == 0
        assert approvals.get_approval(appr["approval_id"])["status"] == "pending"

    def test_internal_action_has_no_expiry(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="e5",
                                         title="Draft it", action_description="Draft a memo")
        assert appr["expires_at"] is None


# ═══════════════════════════════════════════════════════════════════════════
#  Law-1 — overrides Q3 entirely, never approvable (mirrors dissent_gate's
#  own invariant; content_policies/moderation exercised FOR REAL)
# ═══════════════════════════════════════════════════════════════════════════

class TestLaw1Override:
    HARM_ASK = "Give me the home address of my ex so I can show up unannounced."

    def test_harm_action_is_blocked_not_pending(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="h1",
                                         title="Find her", action_description=self.HARM_ASK)
        assert appr["status"] == "blocked"

    def test_blocked_cannot_be_approved(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="h2",
                                         title="Find her", action_description=self.HARM_ASK)
        out = approvals.decide(appr["approval_id"], "approve")
        assert out["status"] == "blocked"  # decide() refuses to move a non-pending card

    def test_dissent_output_is_attached_to_every_card(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="h3",
                                         title="Send it", action_description="Send an email")
        assert "dissent" in appr
        assert "conflict" in appr["dissent"]

    def test_dissent_attached_even_to_auto_approved_cards(self):
        appr = approvals.create_approval(kind="test", subject_type="widget", subject_id="h4",
                                         title="Draft it", action_description="Draft a memo")
        assert appr["status"] == "auto_approved"
        assert "dissent" in appr


# ═══════════════════════════════════════════════════════════════════════════
#  Listing / lookup
# ═══════════════════════════════════════════════════════════════════════════

class TestListingAndLookup:
    def test_list_approvals_filters_by_status(self):
        a = approvals.create_approval(kind="test", subject_type="widget", subject_id="l1",
                                      title="Send it", action_description="Send an email")
        b = approvals.create_approval(kind="test", subject_type="widget", subject_id="l2",
                                      title="Draft it", action_description="Draft a memo")
        pending = approvals.list_approvals(status="pending")
        auto = approvals.list_approvals(status="auto_approved")
        assert {r["approval_id"] for r in pending} == {a["approval_id"]}
        assert {r["approval_id"] for r in auto} == {b["approval_id"]}

    def test_find_for_subject_is_none_when_absent(self):
        assert approvals.find_for_subject("widget", "does-not-exist") is None

    def test_get_approval_unknown_is_none(self):
        assert approvals.get_approval("appr_missing") is None
