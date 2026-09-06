"""Gauntlet finding F1: the nightly knowledge-graph reindex job was registered
inside services/notifications.py's `_register_default_daily_jobs()`, a
function orphaned by the scheduler migration described in server.py's own
comment ("replaces _register_default_daily_jobs + _daily_scheduler_loop").
server.py never imports or calls that function, so the job that is supposed
to run nightly at 03:30 and feed real conversation turns into the Tier B
knowledge graph never gets scheduled at all -- Settings still shows the
toggle, DEFAULT_SETTINGS still says `nightly_reindex: True`, and nothing
runs.

This probe must be RED before the fix (the ref is absent from BUILTIN_TASKS,
because _register_default_builtin_tasks -- the function server.py's
start_scheduler() actually calls -- never registers it), and GREEN after
the fix wires it in the same way every sibling daily job (news_morning,
front_page_evening, weekly_digest, ...) already is.
"""
from __future__ import annotations


class TestKnowledgeGraphNightlyReindexScheduled:
    def test_knowledge_graph_reindex_registers_as_a_builtin(self):
        from agent_friday.services import scheduler as sched
        sched._register_default_builtin_tasks()
        assert "knowledge_graph_reindex" in sched.BUILTIN_TASKS, (
            "the nightly KG reindex job is not registered in BUILTIN_TASKS, "
            "so start_scheduler() never schedules it -- a conversation turn "
            "never automatically reaches the knowledge graph"
        )

    def test_knowledge_graph_reindex_calls_the_real_job(self):
        from agent_friday.services import scheduler as sched
        from agent_friday.services.notifications import _run_knowledge_reindex_job
        sched._register_default_builtin_tasks()
        assert sched.BUILTIN_TASKS["knowledge_graph_reindex"]["fn"] is \
            _run_knowledge_reindex_job

    def test_knowledge_graph_reindex_runs_after_memory_dreaming(self):
        """The original (orphaned) registration was deliberately placed at
        03:30, after memory-dreaming's 03:00 default, so freshly consolidated
        facts make it into the same night's graph pass. Preserve that
        ordering rather than just any schedule."""
        from agent_friday.services import scheduler as sched
        sched._register_default_builtin_tasks()
        kg_spec = sched.BUILTIN_TASKS["knowledge_graph_reindex"]["default_spec"]
        dreaming_spec = sched.BUILTIN_TASKS["memory_dreaming"]["default_spec"]
        kg_minutes = kg_spec["hour"] * 60 + kg_spec["minute"]
        dreaming_minutes = dreaming_spec["hour"] * 60 + dreaming_spec["minute"]
        assert kg_minutes > dreaming_minutes
