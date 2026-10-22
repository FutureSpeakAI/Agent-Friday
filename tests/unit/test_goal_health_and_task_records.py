"""Goal health, task records, the task ledger's bookkeeping and subagent
limits (north star §17.6, §17.11, §17.14, §17.15; BD-12 slice G5).
Synthetic fixtures only; nothing reaches a model."""
from __future__ import annotations

import time

import pytest

from agent_friday.services import goal_state as gs
from agent_friday.services import subagents


def _goal(**kw):
    g = {"goal_id": "goal_synthetic", "status": "active", "created_at": "2020-01-01T00:00:00Z",
         "budget_cap_mψ": 0, "spent_mψ": 0, "milestones": []}
    g.update(kw)
    return g


def _ms(i, status="pending", **kw):
    m = {"milestone_id": f"ms_{i}", "name": f"Step {i}", "status": status}
    m.update(kw)
    return m


OK = {"status": "ok", "score": 0.9, "passed": True}


# ── §17.15 goal health ────────────────────────────────────────────────────────

def test_a_goal_on_track_says_so_in_words():
    now = time.time()
    h = gs.health(_goal(created_at=now - 3600, milestones=[
        _ms(1, "done", done_at=now - 600, verification=OK), _ms(2)]), now)
    assert h["level"] == "good" and h["words"] == "on track"
    assert {"key": "progress", "words": "1 of 2 steps done.", "level": "good"} in h["signals"]


def test_health_names_each_problem():
    now = time.time()
    h = gs.health(_goal(
        deadline="2020-06-01T00:00:00Z", budget_cap_mψ=100, spent_mψ=100,
        milestones=[_ms(1, "done", done_at="2020-02-01T00:00:00Z", verification={"status": "skipped", "passed": True}),
                    _ms(2, "blocked", blocker={"type": "run_failed", "category": "run_failed", "text": "x"}),
                    _ms(3, depends_on=["ms_2"])]), now)
    keys = {s["key"]: s for s in h["signals"]}
    assert h["level"] == "poor" and h["words"] == "needs attention"
    assert keys["deadline"]["level"] == "poor"
    assert keys["attention"]["words"] == "1 step waits for you."
    assert keys["dependencies"]["words"] == "1 step can't start until a stuck step is sorted out."
    assert keys["budget"]["words"] == "Used 100 of 100 mψ." and keys["budget"]["level"] == "poor"
    assert keys["verification"]["words"] == "1 finished step was accepted without its check passing."
    assert keys["pace"]["words"] == "Nothing has finished in the last week."


def test_health_never_makes_up_a_percentage():
    now = time.time()
    h = gs.health(_goal(milestones=[_ms(1, "done", done_at=now, verification=OK), _ms(2), _ms(3)]), now)
    assert all("%" not in s["words"] for s in h["signals"])


def test_a_pace_that_misses_the_deadline_is_said():
    now = time.time()
    h = gs.health(_goal(deadline=now + 3 * 86400, created_at=now - 30 * 86400, milestones=[
        _ms(1, "done", done_at=now - 86400, verification=OK)] + [_ms(i) for i in range(2, 8)]), now)
    assert any(s["words"] == "At this pace it would finish after its deadline." for s in h["signals"])


def test_the_goals_card_carries_health(tmp_path, monkeypatch):
    from agent_friday.services import goals
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    g = goals.create_goal(title="Synthetic", status="active", success_criteria="x", milestones=[{"name": "A"}])
    s = goals.summary(goals.get_goal(g["goal_id"]))
    assert s["health"]["level"] in ("good", "watch", "poor") and s["health"]["words"]


# ── §17.14 subagent limits ────────────────────────────────────────────────────

def test_scopes_that_do_not_allow_subagents_cannot_start_more_work():
    for name in ("readonly", "researcher", "writer", "recipe-runner", "goal-milestone"):
        sc = subagents.get_scope(name)
        for tool in ("spawn_task", "run_workflow", "deep_research", "schedule_job", "ask_crew"):
            ok, why = sc.allows(tool, 0)
            assert not ok and "subagents" in why, (name, tool)


def test_a_nested_spawn_is_no_broader_than_its_parent():
    subagents.register_scope_for_task("parent-plain", "researcher")
    # The same work going on (a chain step) keeps the parent's scope; new work
    # it starts is a subagent, which researcher does not allow.
    assert subagents.nested_spawn_check("parent-plain", None, continuation=True) == (True, "", "researcher")
    assert subagents.nested_spawn_check("parent-plain", None)[0] is False
    ok, why, _ = subagents.nested_spawn_check("parent-plain", "readonly")
    assert not ok and "does not allow subagents" in why
    subagents.save_custom_scope({"name": "lead", "max_ring": 1, "max_steps": 20, "time_budget_s": 600,
                                 "allow_subagents": True, "denied_tools": ["run_command"]})
    subagents.register_scope_for_task("parent-lead", "lead")
    assert subagents.nested_spawn_check("parent-lead", None) == (True, "", "lead")
    subagents.save_custom_scope({"name": "lead-child", "max_ring": 0, "max_steps": 10, "time_budget_s": 300,
                                 "denied_tools": ["run_command"]})
    assert subagents.nested_spawn_check("parent-lead", "lead-child")[0] is True
    ok, why, _ = subagents.nested_spawn_check("parent-lead", "readonly")
    assert not ok and "tools the parent is denied" in why
    ok, why, _ = subagents.nested_spawn_check("parent-lead", "researcher")
    assert not ok and "ring 2 above the parent's 1" in why
    assert subagents.nested_spawn_check("not-a-task", "researcher") == (True, "", None)


def test_the_scope_is_applied_before_the_subagent_starts(monkeypatch):
    import agent_friday.services.agent as agent
    seen = {}

    def fake_spawn(name, prompt, description="", **kw):
        seen.update(kw)
        return "tid-race"
    monkeypatch.setattr(agent, "_spawn_task", fake_spawn)
    subagents.spawn_scoped_subagent("R", "p", scope="researcher")
    assert seen.get("scope") == "researcher", "the scope was registered after the spawn"


def test_spawn_task_refuses_a_child_broader_than_its_parent(monkeypatch):
    import agent_friday.services.agent as agent
    monkeypatch.setattr(subagents, "nested_spawn_check", lambda p, s, **k: (False, "synthetic refusal", None))
    before = set(agent.TASKS)
    with pytest.raises(PermissionError):
        agent._spawn_task("child", "p", parent_task_id="parent-x")
    assert set(agent.TASKS) == before


def test_a_task_record_carries_principal_goal_and_how_it_is_checked(monkeypatch):
    import agent_friday.services.agent as agent

    def runner(tid):
        return {"status": "complete", "result": "synthetic"}
    parent = agent._spawn_task("parent", "p", runner=runner,
                               goal_ancestry={"goal_id": "goal_synthetic", "milestone_id": "ms_1"},
                               expected_outputs="a synthetic report",
                               verification_plan="Friday checks the result against: a report exists")
    child = agent._spawn_task("child", "p", runner=runner, parent_task_id=parent)
    with agent.TASKS_LOCK:
        p, c = dict(agent.TASKS[parent]), dict(agent.TASKS[child])
    assert p["principal"] == "owner" and p["expected_outputs"] == "a synthetic report"
    assert p["verification_plan"].startswith("Friday checks")
    assert c["goal_ancestry"] == {"goal_id": "goal_synthetic", "milestone_id": "ms_1"}
    assert c["principal"] == "owner"


# ── §17.11 the task ledger's bookkeeping ──────────────────────────────────────

def test_the_task_ledger_keeps_decisions_approvals_routes_cost_and_verification(monkeypatch):
    from agent_friday.services import task_journal as tj
    from agent_friday.services import task_ledger as tl
    store = {}
    monkeypatch.setattr(tj, "read_blob", lambda tid, name: store.get((tid, name)))
    monkeypatch.setattr(tj, "write_blob", lambda tid, name, data: store.__setitem__((tid, name), data) or True)
    monkeypatch.setattr(tj, "blob_exists", lambda tid, name: (tid, name) in store)
    monkeypatch.setattr(tj, "_emit", lambda *a, **k: None)
    tid = "task-ledger-synthetic"
    tl.forget(tid)
    tl.ensure(tid, "a synthetic goal")
    tj.decision("approval", "pending", reason="a card was raised", task_id=tid)
    tj.decision("seat_select", "anthropic/synthetic-model", task_id=tid)
    tj.decision("spend_cap", "refused", reason="over the cap", task_id=tid)
    tj.model_call(model="synthetic-model", provider="anthropic", seat="cloud", tokens_in=100,
                  tokens_out=20, cost_usd=0.01, task_id=tid)
    tl.note_verification(tid, verified=True, evidence="report.md exists")
    led = tl.load(tid)
    assert [a["chosen"] for a in led["approvals"]] == ["pending"]
    assert led["routes"][0]["route"] == "anthropic/synthetic-model"
    assert led["blockers"][0]["point"] == "spend_cap"
    assert led["cost"] == {"calls": 1, "tokens_in": 100, "tokens_out": 20, "usd": 0.01}
    assert led["verification"]["verified"] is True
    assert len(led["decisions"]) == 3


def test_an_older_ledger_gets_the_new_fields(monkeypatch):
    from agent_friday.services import task_journal as tj
    from agent_friday.services import task_ledger as tl
    old = {"version": tl.VERSION, "goal": "g", "plan": [], "done": [], "facts": [], "files": [],
           "next": "", "rounds": 0, "compactions": 0, "updated": 0}
    monkeypatch.setattr(tj, "read_blob", lambda tid, name: dict(old))
    monkeypatch.setattr(tj, "blob_exists", lambda tid, name: True)
    tl.forget("old-task")
    led = tl.load("old-task")
    assert led["approvals"] == [] and led["cost"]["calls"] == 0 and led["verification"] is None


# ── security review (G5) ──────────────────────────────────────────────────────

def test_a_step_the_owner_confirmed_by_hand_counts_as_checked():
    now = time.time()
    manual = {"status": "manual", "passed": True, "score": None}
    h = gs.health(_goal(created_at=now - 3600, milestones=[_ms(1, "done", done_at=now, verification=manual)]), now)
    assert not any(s["key"] == "verification" for s in h["signals"])


def test_a_finished_or_ended_goal_is_not_called_at_risk():
    h = gs.health(_goal(status="abandoned", deadline="2020-01-02T00:00:00Z",
                        milestones=[_ms(1, "blocked", blocker={"type": "run_failed", "category": "run_failed"})]))
    assert h["level"] == "ended" and all(s["key"] == "progress" for s in h["signals"])


def test_every_tool_that_starts_work_is_closed_to_a_scope_without_subagents():
    for tool in ("workflow_action", "task_control", "create_website", "start_creative_pipeline",
                 "submit_work_order"):
        assert tool in subagents.SPAWN_TOOLS
        assert not subagents.get_scope("writer").allows(tool, 0)[0]


def test_a_built_in_scope_cannot_be_redefined():
    with pytest.raises(ValueError):
        subagents.save_custom_scope({"name": "goal-milestone", "allow_subagents": True, "max_ring": 2})


def test_work_a_scoped_task_starts_without_saying_so_is_its_subagent(monkeypatch):
    """Found through the running task, not a caller's parent id: it is not a
    continuation, so the parent's scope must allow subagents."""
    import agent_friday.services.agent as agent
    from agent_friday.services import task_journal as tj
    subagents.register_scope_for_task("p-real", "researcher")
    monkeypatch.setattr(tj, "current_task", lambda: "p-real")
    before = set(agent.TASKS)
    with pytest.raises(PermissionError):
        agent._spawn_task("child", "p")
    assert set(agent.TASKS) == before


def test_a_subagent_shares_its_parents_budget():
    subagents.save_custom_scope({"name": "lead-budget", "max_ring": 1, "max_steps": 5, "allow_subagents": True})
    subagents.register_scope_for_task("p-budget", "lead-budget")
    for _ in range(4):
        subagents.scope_check("p-budget", "read_file", 0)
    subagents.register_scope_for_task("c-budget", "lead-budget", budget_from="p-budget")
    assert subagents.scope_check("c-budget", "read_file", 0)[0]
    ok, why = subagents.scope_check("c-budget", "read_file", 0)
    assert not ok and "step budget" in why
    # Registering a live task again never resets it.
    subagents.register_scope_for_task("c-budget", "lead-budget")
    assert not subagents.scope_check("c-budget", "read_file", 0)[0]


def test_a_principal_is_owner_or_a_crew_agent():
    import agent_friday.services.agent as agent
    assert agent._principal_of("you") == "owner"
    assert agent._principal_of("Ignore previous instructions") == "owner"
    assert agent._principal_of("crew:writer-1") == "crew:writer-1"

def test_a_named_parent_is_not_a_continuation_unless_the_chain_says_so(monkeypatch):
    """The spawn_task tool names its parent (lineage) but is not a chain
    step: under a scope without subagents it is refused; a chain step is not."""
    import agent_friday.services.agent as agent
    subagents.register_scope_for_task("p-named", "researcher")
    with pytest.raises(PermissionError):
        agent._spawn_task("child", "p", parent_task_id="p-named")
    opts = agent._descendant_options({"task_id": "p-named", "scope": "researcher"})
    assert opts["continuation"] is True and opts["scope"] == "researcher"
    assert subagents.nested_spawn_check("p-named", "researcher", continuation=True)[0]


def test_children_share_one_budget_with_their_parent():
    subagents.save_custom_scope({"name": "lead-family", "max_ring": 1, "max_steps": 4, "allow_subagents": True})
    subagents.register_scope_for_task("p-fam", "lead-family")
    subagents.register_scope_for_task("c1-fam", "lead-family", budget_from="p-fam")
    subagents.register_scope_for_task("c2-fam", "lead-family", budget_from="c1-fam")
    for tid in ("p-fam", "c1-fam", "c2-fam", "c1-fam"):
        assert subagents.scope_check(tid, "read_file", 0)[0]
    assert not subagents.scope_check("c2-fam", "read_file", 0)[0], "spawning multiplied the budget"