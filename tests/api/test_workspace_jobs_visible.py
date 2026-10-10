"""A job a workspace launches is visible on the desktop, like a chat-launched one.

Standing rule (docs/design/active/task-visibility.md): whatever launches model
work, the person sees it run, with the model that is doing it, its steps, its
reasoning trace, and its place in the Local AI queue. The reported case: News >
Briefings > "generate new briefing" ran for a minute while the workspace's own
spinner turned and the desktop showed no task, so there was nothing to open to
see the reasoning or which model was working.

These tests read what the desktop reads: ``/api/tasks`` (the task card) and
``/api/processes`` (the orb), WHILE the model call is in flight.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj

LOCAL_MODEL = "bonsai2:27b"


class _LocalRouter:
    """The router says: this call goes to the local seat."""

    def route(self, messages, task_context=None):
        return {"provider": "local", "model": LOCAL_MODEL, "reason": "News runs on the local seat"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    from agent_friday.services import activity_ledger as al
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity.jsonl")
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    yield
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


def _briefing_route_env(monkeypatch, tmp_path, during_call):
    """The briefing route with its feeds stubbed, the router sending the call
    to a local seat, and the local primitive replaced by a double that registers
    the orb the real one registers and then lets the test look at the desktop."""
    import agent_friday.core as core
    import agent_friday.routes.news as rn
    from agent_friday.services import model_router as mr
    from agent_friday.services import news_engine as ne

    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(rn, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ne, "_queue_podcast", lambda *a, **k: None)
    monkeypatch.setattr(rn, "_gather_live_briefing_context", lambda: "")
    monkeypatch.setattr(rn, "_get_friday_system_prompt", lambda **k: "")
    monkeypatch.setattr("agent_friday.routing.model_router.get_router",
                        lambda cfg=None: _LocalRouter())

    def local_primitive(messages, system=None, model=None, max_tokens=None,
                        temperature=None, orb_label=None, **kw):
        # What the real `_call_ollama` does before it sends a request.
        core.process_register("local-test", name="Local Inference",
                              label=orb_label or "Local inference...", category="monitoring",
                              icon="x", steps=[], model=model,
                              task_id=(kw.get("session_ctx") or {}).get("task_id"))
        during_call()
        core.process_update("local-test", status="completed", progress=1.0)
        return "# Briefing\n## Calendar\nNothing today.\n", []

    monkeypatch.setattr(mr, "_call_ollama", local_primitive)
    return core


def test_generating_a_briefing_is_a_visible_task_while_it_runs(client, tmp_path, monkeypatch):
    seen = {}

    def during_call():
        seen["tasks"] = client.get("/api/tasks").get_json()["tasks"]
        seen["procs"] = client.get("/api/processes").get_json()["processes"]
        mine = [t for t in seen["tasks"] if t.get("workspace") == "news"]
        if mine:
            seen["detail"] = client.get("/api/tasks/" + mine[0]["task_id"]).get_json()

    _briefing_route_env(monkeypatch, tmp_path, during_call)
    r = client.post("/api/briefing/generate", json={})
    assert r.status_code == 200, r.get_json()

    # 1. The task card exists WHILE the model call is in flight.
    cards = [t for t in seen["tasks"] if t.get("workspace") == "news"]
    assert len(cards) == 1, [t.get("name") for t in seen["tasks"]]
    card = cards[0]
    assert card["name"] == "Daily briefing"
    assert card["status"] == "running"
    assert not card.get("process"), "a TASKS entry, not a bare orb row"

    # 2. It names the model doing the work, and whether it is the local seat.
    assert card["model"] == LOCAL_MODEL
    assert card["seat"] == "local/" + LOCAL_MODEL and card["seat_is_local"] is True

    # 3. It has steps and a log, and a reasoning trace to open.
    detail = seen["detail"]
    assert [s["name"] for s in detail["steps"] if s.get("type") == "phase"][:2] == [
        "Gathering today's calendar, mail and news", "Writing the briefing"]
    assert any(s.get("type") == "model" and s.get("name") == LOCAL_MODEL for s in detail["steps"])
    assert any("Asking " + LOCAL_MODEL in line for line in detail["log"]), detail["log"]
    assert card["trace_id"], "the card's reasoning button needs a trace"
    from agent_friday.services import reasoning_trace as rt
    assert rt.live_trace(card["trace_id"]) is not None or rt.get_tree(card["trace_id"]) is not None

    # 4. The desktop orb is the same entry: one orb for the job, none for the
    #    model primitive's own "Local Inference" orb.
    orbs = [p for p in seen["procs"] if p["id"] == card["task_id"]]
    assert len(orbs) == 1 and orbs[0]["task_id"] == card["task_id"]
    assert orbs[0]["model"] == LOCAL_MODEL and orbs[0]["status"] == "running"
    assert not [p for p in seen["procs"] if p["id"] == "local-test"], \
        "the job owns the orb; the primitive must not open a second one"

    # 5. When it ends the same entry says so, with the result.
    done = client.get("/api/tasks/" + card["task_id"]).get_json()
    assert done["status"] == "complete" and "Nothing today" in done["result"]
    orb = [p for p in client.get("/api/processes").get_json()["processes"]
           if p["id"] == card["task_id"]][0]
    assert orb["status"] == "completed"


def test_a_failed_briefing_ends_its_task_as_failed(client, tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("the seat fell over")

    env = _briefing_route_env(monkeypatch, tmp_path, boom)
    r = client.post("/api/briefing/generate", json={})
    assert r.status_code >= 400
    tasks = [t for t in client.get("/api/tasks").get_json()["tasks"] if t.get("workspace") == "news"]
    assert len(tasks) == 1 and tasks[0]["status"] == "failed", tasks
    del env


def test_a_waiting_job_reports_its_place_in_the_local_ai_queue(client, tmp_path, monkeypatch):
    seen = {}

    def during_call():
        card = [t for t in client.get("/api/tasks").get_json()["tasks"]
                if t.get("workspace") == "news"][0]
        # The Local AI queue says this job is second in line.
        monkeypatch.setattr(ag, "local_queue_snapshot", lambda: {
            "running": [{"id": "someone-else", "state": "running"}],
            "queued": [{"id": "ahead", "why": "waits for your chat to finish"},
                       {"id": card["task_id"], "why": "waits for your chat to finish"}],
            "count": 2})
        seen["card"] = client.get("/api/tasks/" + card["task_id"]).get_json()
        seen["row"] = [t for t in client.get("/api/tasks").get_json()["tasks"]
                       if t["task_id"] == card["task_id"]][0]

    _briefing_route_env(monkeypatch, tmp_path, during_call)
    assert client.post("/api/briefing/generate", json={}).status_code == 200
    for view in (seen["card"], seen["row"]):
        assert view["status"] == "queued"
        assert view["queue_position"] == 2 and view["queue_total"] == 2
        assert view["wait_reason"] == "waits for your chat to finish"


def test_a_job_queues_under_its_own_task_id(monkeypatch):
    """The Local AI queue row and the task card are one entry: the job asks the
    supervisor to admit its first local model call under its own task id."""
    from agent_friday.services import background_gate as bg
    from agent_friday.services import visible_jobs as vj

    captured = {}

    class Supervisor:
        def admit_inline(self, spec, **kw):
            captured.update(spec)
            return spec["id"]

        def wait_while_blocked(self, job_id):
            captured["waited"] = job_id

    monkeypatch.setattr(bg, "_SUPERVISOR_PROVIDER", lambda: Supervisor())
    with vj.visible_job("Daily briefing", kind="briefing", workspace="news") as job:
        bg.before_local_model_call(LOCAL_MODEL)
    assert captured["id"] == job.id and captured["waited"] == job.id
    assert captured["kind"] == "briefing" and captured["label"] == "Daily briefing"
    assert captured["foreground"] is True


def test_stopping_the_card_stops_the_job_at_its_next_model_call(client, tmp_path, monkeypatch):
    from agent_friday.services import visible_jobs as vj
    from agent_friday.services.background_gate import JobCancelled

    with pytest.raises(JobCancelled):
        with vj.visible_job("Some workspace job", kind="x", workspace="w", queued=False) as job:
            client.post("/api/tasks/%s/stop-after-step" % job.id)
            vj.note_leg("local", LOCAL_MODEL)   # the next model call
    rec = client.get("/api/tasks").get_json()["tasks"]
    assert [t["status"] for t in rec if t["task_id"] == job.id] == ["cancelled"]


def test_a_job_that_never_reaches_a_model_leaves_no_card(client):
    from agent_friday.services import visible_jobs as vj

    with vj.visible_job("Cached read", kind="x", workspace="w", eager=False, queued=False):
        pass
    assert not [t for t in client.get("/api/tasks").get_json()["tasks"]
                if t.get("name") == "Cached read"]


def test_nested_jobs_are_steps_of_one_card(client):
    from agent_friday.services import visible_jobs as vj

    with vj.visible_job("Outer", kind="x", workspace="w", queued=False):
        with vj.visible_job("Inner", kind="y", workspace="w", queued=False):
            vj.note_route("cloud", "claude-sonnet-5")
    cards = [t for t in client.get("/api/tasks").get_json()["tasks"] if t.get("workspace") == "w"]
    assert [c["name"] for c in cards] == ["Outer"] and cards[0]["model"] == "claude-sonnet-5"
    assert cards[0]["seat_is_local"] is False


def test_inside_a_task_worker_the_task_is_the_job(client):
    """A job wrapped inside a spawned task's worker adds no second card: the
    model it uses is reported into the task."""
    import time
    from agent_friday.services import visible_jobs as vj

    now = time.time()
    with ag.TASKS_LOCK:
        ag.TASKS["spawned-1"] = {"task_id": "spawned-1", "name": "Wiki notes", "description": "",
                                 "prompt": "p", "status": "running", "created": now, "started": now,
                                 "ended": None, "log": [], "result": "", "model": None}
    tj.push_task("spawned-1")
    try:
        with vj.visible_job("Knowledge graph learning", kind="kg_learn", workspace="library", queued=False):
            vj.note_route("local", LOCAL_MODEL)
    finally:
        tj.pop_task()
    cards = [t for t in client.get("/api/tasks").get_json()["tasks"] if not t.get("process")]
    assert [c["task_id"] for c in cards] == ["spawned-1"]
    assert cards[0]["model"] == LOCAL_MODEL and cards[0]["status"] == "running"


def test_a_task_someone_else_created_can_be_adopted(client):
    import time
    from agent_friday.services import visible_jobs as vj

    now = time.time()
    with ag.TASKS_LOCK:
        ag.TASKS["draft-1"] = {"task_id": "draft-1", "name": "Draft", "description": "", "prompt": "p",
                               "status": "running", "created": now, "started": now, "ended": None,
                               "log": [], "result": "", "model": None}
    with vj.visible_task("draft-1"):
        vj.note_route("cloud", "claude-sonnet-5")
        orbs = [p for p in client.get("/api/processes").get_json()["processes"] if p["id"] == "draft-1"]
        assert len(orbs) == 1 and orbs[0]["model"] == "claude-sonnet-5"
    row = client.get("/api/tasks/draft-1").get_json()
    assert row["status"] == "running", "the creator owns the record's status"
    assert row["model"] == "claude-sonnet-5" and row["seat_is_local"] is False


def test_a_foreground_job_never_waits_for_the_turn_that_may_be_waiting_on_it(monkeypatch):
    """A chat tool that runs a workspace job inline would otherwise wait for
    the very turn that is waiting on it."""
    from agent_friday.services import background_gate as bg

    monkeypatch.setattr(bg, "interactive_kinds", lambda: ["chat"])
    record = {"seat_is_local": True, "deferrable": False}
    assert bg.block_reason(record) == bg.REASON_INTERACTIVE
    assert bg.block_reason(dict(record, foreground=True)) == ""
    assert bg.block_reason(dict(record, foreground=True, deferrable=True)) == ""


def _admit_spec(monkeypatch, **job_kw):
    """The spec a visible job hands the Local AI queue at its first local call."""
    from agent_friday.services import background_gate as bg
    from agent_friday.services import visible_jobs as vj

    captured = {}

    class Supervisor:
        def admit_inline(self, spec, **kw):
            captured.update(spec)
            return spec["id"]

        def wait_while_blocked(self, job_id):
            pass

    monkeypatch.setattr(bg, "_SUPERVISOR_PROVIDER", lambda: Supervisor())
    with vj.visible_job("J", kind="k", workspace="w", **job_kw):
        bg.before_local_model_call(LOCAL_MODEL)
    return captured


def test_a_deferrable_job_keeps_the_idle_and_interactive_rules(monkeypatch):
    """Background work (not a person's click) is not foreground: block_reason
    still holds it for idle and for an interactive turn, as in 1.0.2."""
    from agent_friday.services import background_gate as bg

    spec = _admit_spec(monkeypatch, deferrable=True)
    assert spec["foreground"] is False and spec["deferrable"] is True
    spec["seat_is_local"] = True
    monkeypatch.setattr(bg, "interactive_kinds", lambda: ["chat"])
    assert bg.block_reason(spec) == bg.REASON_INTERACTIVE
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [])
    monkeypatch.setattr(bg, "idle_state", lambda: {"idle": False})
    assert bg.block_reason(spec) == bg.REASON_IDLE


def test_a_clicked_job_is_foreground_and_does_not_deadlock_in_a_turn(monkeypatch):
    from agent_friday.services import background_gate as bg

    spec = _admit_spec(monkeypatch)
    assert spec["foreground"] is True
    spec["seat_is_local"] = True
    monkeypatch.setattr(bg, "interactive_kinds", lambda: ["chat"])
    assert bg.block_reason(spec) == ""


def test_a_scheduled_job_keeps_its_own_queue_record(monkeypatch):
    """Inside the scheduler's background_job the visible job adds no second
    admission and never marks the scheduler's record foreground."""
    from agent_friday.services import background_gate as bg
    from agent_friday.services import visible_jobs as vj

    with bg.background_job(kind="scheduled", key="s", label="L", deferrable=True) as q:
        with vj.visible_job("J", kind="scheduled", workspace="w", eager=False, queued=False) as j:
            q["task_id"] = j.id
        assert "foreground" not in q and q["deferrable"] is True
