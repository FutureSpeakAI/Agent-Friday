"""Regression tests for an AUTONOMY_SPEC A3 finding against services/goals.py
(V6 invariant 5: "no outward/irreversible goal action without a human
gate").

Two related defects, two mitigations, both covered here:

  1. The Q3/dissent gate classified only the milestone's cosmetic name and
     the goal's cosmetic description — NEVER milestone['success_criteria']
     (the field that actually becomes the executor's instruction via
     _build_milestone_prompt). A milestone titled "Weekly maintenance" under
     a goal described as "Routine housekeeping" auto-approved even when its
     success_criteria said "Delete all files in the old backups folder and
     email a confirmation to admin@example.com". Fixed by folding
     success_criteria into the classified/dissent-checked action_description
     (see _run_milestone_locked).

  2. Even when Q3 auto-approves (correctly, per its own "internal drafting"
     design, OR incorrectly, via a classifier blind spot), the dispatched
     background task got the SAME unrestricted ring-2 tool surface as any
     other authenticated background task — any registered network tool
     (email, posting, purchases, run_command, ...) could be invoked with zero
     re-classification and zero human approval. Fixed by scoping every
     goal-milestone dispatch to the "goal-milestone" safe allow-list (see
     services.subagents) UNLESS this exact dispatch was classified hard by
     Q3 AND explicitly human-approved (milestone["_hard_gate_approved"]).

The second mitigation is defense in depth: it holds regardless of how
accurately the text classifier scores the milestone's name/description, so
it also covers Q3-heuristic blind spots (e.g. A2's dissent_gate leading-
drafting-verb prefix, "Draft and immediately send the report...", which by
its own documented design always classifies as soft) without touching or
regressing A2's already-shipped, already-tested classifier.
"""
from __future__ import annotations

import pytest

from agent_friday.services import goals


@pytest.fixture(autouse=True)
def _iso_goals(tmp_path, monkeypatch):
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(goals.approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield
    goals.set_executor(None)


def _executor_recording_hints(text="a solid output"):
    calls = []

    def _fn(goal, milestone, *, critique_hint=""):
        calls.append(critique_hint)
        return {"ok": True, "text": f"{text} (attempt {len(calls)})", "cost_mψ": 100}
    _fn.calls = calls
    return _fn


def _always_pass(monkeypatch):
    monkeypatch.setattr(goals.qa_gates, "evaluate_text",
                       lambda content, intent, **kw: {
                           "status": "ok", "passed": True, "score": 0.9,
                           "threshold": 0.7, "critique": "", "suggestions": ""})


# ═══════════════════════════════════════════════════════════════════════════
#  Mitigation 1 — classify the REAL instruction (success_criteria), not just
#  the milestone's cosmetic name/description.
# ═══════════════════════════════════════════════════════════════════════════

class TestMilestoneGateClassifiesRealInstructionText:
    def test_irreversible_and_outward_success_criteria_forces_a_gated_approval(self, monkeypatch):
        """A milestone whose NAME/DESCRIPTION read as harmless ('Weekly
        maintenance' / 'Routine housekeeping') but whose success_criteria is
        actually irreversible+outward must NOT auto-approve."""
        _always_pass(monkeypatch)
        g = goals.create_goal(
            title="Weekly maintenance", description="Routine housekeeping",
            status="active",
            milestones=[{
                "name": "Weekly maintenance",
                "success_criteria": ("Delete all files in the old backups folder and "
                                    "email a confirmation to admin@example.com"),
            }],
        )
        mid = g["milestones"][0]["milestone_id"]
        executor = _executor_recording_hints()
        goals.set_executor(executor)

        result = goals.run_milestone(g["goal_id"], mid)

        assert result["ok"] is False
        assert result["status"] == "pending_approval"
        assert len(executor.calls) == 0  # never dispatched unsupervised

        appr = goals.approvals.get_approval(result["approval_id"])
        assert appr is not None
        assert appr["gated"] is True
        assert appr["policy_class"] != "internal"

        updated = goals.get_goal(g["goal_id"])
        assert updated["milestones"][0]["status"] == "blocked"

    def test_benign_success_criteria_still_auto_approves(self, monkeypatch):
        """Control case: a genuinely internal drafting milestone (no outward/
        irreversible/spend language anywhere, including success_criteria)
        still auto-proceeds — the enrichment must not over-gate."""
        _always_pass(monkeypatch)
        g = goals.create_goal(
            title="Weekly maintenance", description="Routine housekeeping",
            status="active",
            milestones=[{
                "name": "Weekly maintenance",
                "success_criteria": "Summarize this week's disk usage in one paragraph",
            }],
        )
        mid = g["milestones"][0]["milestone_id"]
        executor = _executor_recording_hints()
        goals.set_executor(executor)

        result = goals.run_milestone(g["goal_id"], mid)

        assert result["ok"] is True
        assert result["status"] == "done"
        assert len(executor.calls) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  Mitigation 2 — defense in depth: the REAL executor dispatch (services.
#  agent._spawn_task) is scoped to a safe tool subset unless a human
#  explicitly cleared a genuinely hard/gated approval for THIS milestone.
# ═══════════════════════════════════════════════════════════════════════════

class TestMilestoneDispatchIsScopedUnlessExplicitlyApproved:
    def _stub_agent_spawn(self, monkeypatch, *, result_text="a fine draft"):
        recorded = {}

        def _fake_spawn_task(name, prompt, description='', on_complete=None,
                             chain=None, chain_step=0, orb_icon='🛰', scope=None):
            recorded["scope"] = scope
            recorded["prompt"] = prompt
            return "fake-task-id"

        def _fake_snapshot(tid):
            return {"status": "complete", "result": result_text}

        import agent_friday.services.agent as agent_mod
        monkeypatch.setattr(agent_mod, "_spawn_task", _fake_spawn_task)
        monkeypatch.setattr(agent_mod, "_task_snapshot", _fake_snapshot)
        return recorded

    def test_auto_approved_milestone_dispatch_is_scoped_to_safe_tools(self, monkeypatch):
        """Even a genuinely soft/internal (correctly auto-approved) milestone
        must dispatch through services.agent._spawn_task UNDER the
        'goal-milestone' safety scope — it never gets the ordinary unscoped
        ring-2 tool surface just because Q3 read it as internal drafting."""
        _always_pass(monkeypatch)
        recorded = self._stub_agent_spawn(monkeypatch)
        g = goals.create_goal(title="Draft the newsletter", description="Draft it",
                              status="active",
                              milestones=[{"name": "Draft this week's issue"}])
        mid = g["milestones"][0]["milestone_id"]

        result = goals.run_milestone(g["goal_id"], mid)

        assert result["status"] == "done"
        assert recorded["scope"] == "goal-milestone"

    def test_explicitly_approved_hard_milestone_dispatch_is_unscoped(self, monkeypatch):
        """A milestone that Q3 correctly classifies hard (outward: 'send')
        and that a human explicitly approves gets the ordinary, unscoped
        dispatch — the same reach a desktop-authenticated owner could invoke
        directly."""
        _always_pass(monkeypatch)
        recorded = self._stub_agent_spawn(monkeypatch)
        g = goals.create_goal(title="Send the vendor confirmation", description="",
                              status="active",
                              milestones=[{"name": "Send the vendor confirmation email"}])
        mid = g["milestones"][0]["milestone_id"]

        first = goals.run_milestone(g["goal_id"], mid)
        assert first["status"] == "pending_approval"
        assert "scope" not in recorded  # not dispatched at all yet

        goals.approvals.decide(first["approval_id"], "approve", decided_by="owner")

        assert recorded.get("scope") is None
        assert goals.get_goal(g["goal_id"])["milestones"][0]["status"] == "done"

    def test_scope_registration_failure_fails_closed_not_open(self, monkeypatch):
        """If the safety scope can't be applied for some reason, the
        milestone must NOT silently run unscoped — services.agent._spawn_task
        itself fails closed (raises) when a requested scope can't be
        registered; the executor must surface that as a failed attempt, not
        swallow it and proceed unprotected."""
        _always_pass(monkeypatch)

        def _boom_spawn_task(name, prompt, description='', on_complete=None,
                             chain=None, chain_step=0, orb_icon='🛰', scope=None):
            if scope:
                raise RuntimeError(f"could not apply required scope {scope!r}: boom")
            return "fake-task-id"

        import agent_friday.services.agent as agent_mod
        monkeypatch.setattr(agent_mod, "_spawn_task", _boom_spawn_task)

        g = goals.create_goal(title="Draft the newsletter", description="Draft it",
                              status="active",
                              milestones=[{"name": "Draft this week's issue"}])
        mid = g["milestones"][0]["milestone_id"]

        # Real _default_executor runs (no set_executor call) — it must catch
        # the raise and report a failed attempt, never let it propagate as
        # an unhandled exception, and never proceed unscoped.
        result = goals.run_milestone(g["goal_id"], mid)
        assert result["ok"] is False
