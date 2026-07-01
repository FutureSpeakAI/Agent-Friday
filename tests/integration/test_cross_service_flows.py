"""Integration tests that exercise real seams BETWEEN services (no Flask needed):

  • Memory dreaming → user model: a consolidated durable fact lands in the model.
  • Orchestrator → budget enforcer: exceeding the cap stops the worker.
  • Federation task credit → economy: a completed task credits Positrons.
  • Channel bridge → egress gate: an outbound reply is gated.
  • Egress gate ← sensitivity classifier: the gate consults the real classifier.

These use lightweight stubs only at true external boundaries (LLM, network,
ChromaDB); the wiring between Friday's own modules is real.
"""
from __future__ import annotations

import uuid

import pytest


# ── Memory dreaming writes durable facts into the user model ──────────────────

class TestDreamingFeedsUserModel:
    def test_high_confidence_fact_reaches_user_model(self):
        from agent_friday.services import memory_dreaming as md
        from agent_friday.services import user_model as um
        um.forget()

        class FakeMemory:
            def recent(self, n=100):
                return [{"text": "I prefer bullet-point summaries over prose.",
                         "date": "2026-06-30", "timestamp": "2026-06-30T10:00:00",
                         "role": "user", "topic_keywords": []}]

        md.dream(day="2026-06-30", memory=FakeMemory())
        facts = [f["text"] for f in um._recent_facts(50)]
        # The mined preference fact (confidence >= 0.6) was handed to note_fact.
        assert any("bullet-point" in t for t in facts)


# ── Orchestrator honors the budget enforcer's veto ────────────────────────────

class TestOrchestratorBudgetIntegration:
    def test_over_cap_task_is_stopped(self, monkeypatch):
        from agent_friday.services import orchestrator as orch
        from agent_friday.services import budget_enforcer as be
        from agent_friday.services.orchestrator import (
            Orchestrator, ResultStatus, WorkerStatus, WorkerResult)

        ws = f"integ-budget-{uuid.uuid4().hex[:6]}"
        # Cap the workspace tiny, then delegate a task that reserves more.
        be.set_policy(ws, monthly_cap_mψ=1_000)

        class FakeAdapter:
            def start(self, task): return "aid"
            def poll(self, aid): return WorkerStatus.COMPLETED
            def result(self, aid): return WorkerResult(task_id="t",
                status=ResultStatus.COMPLETED, output="x", cost_mψ=500)
            def cancel(self, aid): pass

        monkeypatch.setattr(orch, "_get_adapter", lambda t: FakeAdapter())
        monkeypatch.setattr(orch, "_log_start", lambda e: None)
        monkeypatch.setattr(orch, "_log_finish", lambda e: None)

        res = Orchestrator().delegate(
            "expensive", budget_mψ=50_000,
            context={"workspace": ws}, deadline_seconds=5)
        assert res.status == ResultStatus.BUDGET_EXCEEDED


# ── Federation task completion credits Positrons via the economy ──────────────

class TestFederationCreditsEconomy:
    def test_task_credit_increases_psi(self):
        from agent_friday.services import economy as econ
        econ._ensure_schema()
        agent = f"integ-fed-{uuid.uuid4().hex[:6]}"
        before = econ.get_wallet(agent)["psi_balance"]
        # A completed federation task awards PSI_TASK milliPositrons.
        econ.earn(agent, econ.PSI_TASK, "federation task completed")
        after = econ.get_wallet(agent)["psi_balance"]
        assert after - before == econ.PSI_TASK


# ── Channel bridge routes an outbound reply through the egress gate ───────────

class TestChannelEgressIntegration:
    def test_sensitive_reply_withheld(self):
        from agent_friday.services.channels import manager
        # gate_reply funnels the assistant text through egress_gate.seal_outbound.
        out = manager.gate_reply("My SSN is 123-45-6789.", channel="telegram")  # pragma: allowlist secret
        assert "123-45-6789" not in out  # pragma: allowlist secret

    def test_public_reply_passes(self):
        from agent_friday.services.channels import manager
        out = manager.gate_reply("The meeting is at noon on Tuesday.", channel="discord")
        assert "meeting is at noon" in out


# ── Egress gate consults the REAL sensitivity classifier ──────────────────────

class TestGateUsesRealClassifier:
    def test_gate_and_classifier_agree_on_sensitive(self):
        import os
        from pathlib import Path
        from agent_friday.services import egress_gate as eg
        from agent_friday.services.sensitivity_classifier import classify, Tier
        text = "My bank account number and routing number are on the wire form."
        # The classifier flags it...
        assert classify(text) >= Tier.PRIVATE
        # ...and the gate therefore withholds it from a cloud provider.
        sealed = eg.seal_outbound(
            {"messages": [{"role": "user", "content": text}]},
            "anthropic", log_path=Path(os.devnull))
        assert "bank account number" not in str(sealed["messages"][0]["content"]).lower()


# ── Learning loop: a successful outcome becomes a promotable skill candidate ──

class TestLearningLoopEndToEnd:
    def test_success_stream_becomes_skill(self):
        from agent_friday.services import learning_loop as ll
        tt, ap = f"integ-learn-{uuid.uuid4().hex[:6]}", "the-winning-approach"
        for i in range(5):
            ll.observe(tt, f"varied prompt number {i}", approach=ap, success=True,
                       satisfaction=0.9)
        created = ll.mine_candidates(min_success=0.7, min_samples=3, min_distinct=2)
        made = [c for c in created if c["name"] == f"{tt}:{ap}"]
        assert made, "a successful outcome stream should mint a skill candidate"
        sid = made[0]["skill_id"]
        for _ in range(4):
            ll.record_trial(sid, success=True, satisfaction=0.95)
        ll.promote(threshold=0.5, min_trials=3)
        assert sid in [s["skill_id"] for s in ll.active_skills()]
