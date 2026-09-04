"""Gauntlet finding: services/agent.py's chain_run_status() conflates "a
fresh run restarting at step 0" with "a same-step retry" because its reset
comparison uses <= instead of <.

_retry_chain_step() spawns a retry task at the SAME chain_step as the
failed attempt. chain_run_status()'s "only the latest run" walk resets its
accumulator whenever the current row's chain_step is <= the previous row's
-- which is also true for a same-step retry (1 <= 1), not just a genuine
restart-from-zero (0 <= 2). So after any mid-chain retry, every earlier,
already-completed step gets dropped from the reconstructed "latest run",
and is reported back as 'pending' even though it finished cleanly. Because
one step then shows pending, chain_run_status() reports the whole run's
state as 'idle' instead of 'completed', even though every step succeeded.

This is exactly the recovery path chains are DESIGNED to use (a step fails
once, retries, succeeds) -- so the Workflows panel's Status/history feature
was broken for the normal case, not an edge case.
"""
from __future__ import annotations

from agent_friday.services import agent as ag


def _row(task_id, chain_step, status, created):
    return {"task_id": task_id, "chain": "test-chain", "chain_step": chain_step,
            "status": status, "created": created, "started": created,
            "ended": created + 1, "result": "ok", "log": []}


class TestChainRunStatusSurvivesARetry:
    def test_a_completed_step_is_not_hidden_by_a_later_same_step_retry(self, monkeypatch):
        # 3-step chain: step 0 completes; step 1 fails then retries (SAME
        # chain_step) and succeeds; step 2 completes.
        rows = {
            "t0": _row("t0", 0, "completed", 1.0),
            "t1_fail": _row("t1_fail", 1, "failed", 2.0),
            "t1_retry": _row("t1_retry", 1, "completed", 3.0),
            "t2": _row("t2", 2, "completed", 4.0),
        }
        monkeypatch.setattr(ag, "TASKS", rows)
        monkeypatch.setattr(ag, "load_workflow_chain", lambda name: {
            "name": name, "slug": "test-chain",
            "steps": [{"name": "step0"}, {"name": "step1"}, {"name": "step2"}],
        })

        result = ag.chain_run_status("test-chain")

        step0 = next(s for s in result["steps"] if s["index"] == 0)
        assert step0["status"] == "completed", (
            "step 0 already completed, but chain_run_status() reported it "
            "as pending -- a same-chain_step retry of step 1 incorrectly "
            "reset the 'latest run' accumulator and hid step 0's real "
            "outcome (the <= vs < bug in the reset comparison)"
        )
        step1 = next(s for s in result["steps"] if s["index"] == 1)
        assert step1["status"] == "completed" and step1["task_id"] == "t1_retry", (
            "step 1 was retried and the retry succeeded, but the status "
            "lookup returned the ORIGINAL failed attempt (t1_fail) instead "
            "of the retry's real outcome (t1_retry) -- picking the first "
            "match for a step index returns the earliest attempt, not the "
            "most recent one"
        )
        assert result["state"] == "completed", (
            "every step in this chain actually succeeded, but the reset "
            "bug left step 0 looking pending, so the overall state was "
            "reported as 'idle' instead of 'completed'"
        )

    def test_a_genuine_restart_from_step_zero_still_resets(self, monkeypatch):
        """No-op-shaped sanity check: the fix must not break the case the
        reset logic exists FOR -- a completely new run of the same chain
        starting over at step 0 after an earlier run reached step 2."""
        rows = {
            "old_t0": _row("old_t0", 0, "completed", 1.0),
            "old_t1": _row("old_t1", 1, "completed", 2.0),
            "old_t2": _row("old_t2", 2, "completed", 3.0),
            "new_t0": _row("new_t0", 0, "running", 4.0),
        }
        monkeypatch.setattr(ag, "TASKS", rows)
        monkeypatch.setattr(ag, "load_workflow_chain", lambda name: {
            "name": name, "slug": "test-chain",
            "steps": [{"name": "step0"}, {"name": "step1"}, {"name": "step2"}],
        })

        result = ag.chain_run_status("test-chain")

        step0 = next(s for s in result["steps"] if s["index"] == 0)
        assert step0["task_id"] == "new_t0", (
            "a brand-new run restarting at step 0 must still discard the "
            "previous run's rows, not merge them"
        )
        assert result["state"] == "running"
