"""The narrated step list the owner can stop (spec section 3.7): one list per workflow run, one state per step,
each step told to a live call as it begins (in counts, never a name), and stopped by the owner three ways.

The states are the chain's own task records; the list is pushed to the page as `steps` then `step_update`; Stop
asks the one stop that exists (the step that is running finishes, the next never starts).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_friday.services import agent, desktop_bus, step_lists
from agent_friday.services import task_journal as tj
from agent_friday.services import voice_live_channel as vlc
from tests.unit.test_workflow_cancel_route import FakeSpawn, _finish

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "WORKFLOWS_DIR", tmp_path / "workflows")
    spawn = FakeSpawn()
    monkeypatch.setattr(agent, "_spawn_task", spawn)
    with agent.TASKS_LOCK:
        agent.TASKS.clear()
    agent._CHAIN_STOP.clear()
    agent._CHAIN_STOPPED.clear()
    tj._STOP_REQUESTED.clear()
    step_lists.reset()
    for name in ("write_state", "read_state", "steer"):
        monkeypatch.setattr(tj, name, lambda *a, **k: None)
    pushed = []
    monkeypatch.setattr(desktop_bus, "push", lambda actions: pushed.append(actions[0]) or True)
    agent.save_workflow_chain({"name": "Morning brief", "steps": [
        {"name": "Gather the Harbor Legal files", "prompt": "a"}, {"name": "Write", "prompt": "b"}, {"name": "Send", "prompt": "c"}]})
    yield spawn, pushed
    with agent.TASKS_LOCK:
        agent.TASKS.clear()
    agent._CHAIN_STOP.clear()
    agent._CHAIN_STOPPED.clear()
    tj._STOP_REQUESTED.clear()
    step_lists.reset()


def test_a_run_shows_its_steps_then_one_state_change_at_a_time(world):
    spawn, pushed = world
    tid = agent.run_workflow_chain("Morning brief", conversation_id="c1")
    first = pushed[0]
    assert first["type"] == "steps" and first["title"] == "Morning brief" and first["kind"] == "workflow"
    assert [(s["n"], s["state"]) for s in first["steps"]] == [(1, "doing"), (2, "waiting"), (3, "waiting")]
    _finish(tid)
    agent._advance_task_chain(tid, "done")
    updates = [(p["n"], p["state"]) for p in pushed[1:] if p["type"] == "step_update"]
    assert updates == [(1, "done"), (2, "doing")], updates
    assert all(p["id"] == first["id"] for p in pushed)


def test_each_step_is_told_to_a_live_call_in_counts_never_by_name(world):
    spawn, pushed = world
    from agent_friday.services import conversations
    cid = conversations.create(title="Live call")["id"]
    heard = []
    vlc.register(cid, lambda text, kind: heard.append((kind, text)))
    try:
        tid = agent.run_workflow_chain("Morning brief", conversation_id=cid)
        _finish(tid)
        agent._advance_task_chain(tid, "done")
    finally:
        vlc.unregister(cid) if hasattr(vlc, "unregister") else None
    assert heard == [("progress", "Step 1 of 3 has started."), ("progress", "Step 2 of 3 has started.")], heard
    assert not [t for _k, t in heard if "Harbor" in t or "Gather" in t]


def test_stop_between_steps_prevents_the_next_and_the_list_says_stopped(world):
    spawn, pushed = world
    tid = agent.run_workflow_chain("Morning brief")
    res = step_lists.stop()
    assert res["ok"] and res["text"].startswith("Stopped by you.")
    _finish(tid)
    agent._advance_task_chain(tid, "done")
    assert [c[2] for c in spawn.calls] == [0]
    final = step_lists.active()
    assert [s["state"] for s in final["steps"]] == ["done", "skipped", "skipped"]
    assert any(p["type"] == "step_update" and p["state"] == "skipped" for p in pushed)


def test_stop_during_a_step_calls_stop_after_step_and_marks_it_stopped(world):
    spawn, pushed = world
    tid = agent.run_workflow_chain("Morning brief")
    step_lists.stop()
    assert tj.stop_requested(tid)
    _finish(tid, status="cancelled")
    agent._chain_sync(tid)
    states = [s["state"] for s in step_lists.active()["steps"]]
    assert states == ["stopped", "skipped", "skipped"]


def test_stop_with_nothing_running_is_an_honest_no(world):
    assert step_lists.stop() == {"ok": False, "text": "Nothing is running that I can stop."}
    spawn, pushed = world
    tid = agent.run_workflow_chain("Morning brief")
    _finish(tid)
    for i in (1, 2):
        agent._advance_task_chain("t%d" % i, "x")
        _finish("t%d" % (i + 1))
    agent._advance_task_chain("t3", "x")
    again = step_lists.stop()
    assert again["ok"] is False and "already finished" in again["text"]


def test_a_step_waiting_at_an_approval_card_is_shown_as_needing_you():
    assert step_lists._state("awaiting_approval") == "held"
    assert step_lists._state("running") == "doing" and step_lists._state("cancelled") == "stopped"
    assert step_lists._state("completed_unverified") == "waiting" or step_lists._state("complete") == "done"


def test_the_panel_stops_three_ways_and_never_steals_esc_from_a_field_or_a_dialog():
    js = (ROOT / "static" / "step_panel.js").read_text(encoding="utf-8")
    assert "/api/steps/' + encodeURIComponent(cur.id) + '/stop'" in js, "the Stop button posts the stop"
    assert "e.key !== 'Escape' || e.defaultPrevented" in js
    assert "tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable" in js
    assert "[role=dialog],[aria-modal=true]" in js
    assert "WORD = { waiting: 'waiting', doing: 'doing', done: 'done', held: 'needs you'" in js, "each state is also said in words"


def test_the_panel_is_mounted_in_the_shell_and_the_commands_reach_it():
    for page in ("index.html", "ui_parts/app.html"):
        text = (ROOT / page).read_text(encoding="utf-8")
        assert "FridayStepPanel" in text, page
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "a.type === 'steps' || a.type === 'step_update'" in index and "new CustomEvent('friday:' + a.type" in index
    assert index.index("friday_stage.js") < index.index("step_panel.js")
    app = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    # The card's Stop run reaches the one stop: /api/workflows/action -> workflow_operations 'stop' ->
    # agent.stop_workflow_chain (the same call the step panel's route makes).
    assert "\"data-testid\": \"wf-stop\"" in index and 'data-testid="wf-stop"' in app
    assert "onAction('stop', w)" in index and "onAction('stop', w)" in app
    ops = (ROOT / "src" / "agent_friday" / "services" / "workflow_operations.py").read_text(encoding="utf-8")
    assert "agent.stop_workflow_chain(" in ops
