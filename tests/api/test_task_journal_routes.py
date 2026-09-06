"""API surface of the task journal, phase 1 (docs/design/active/
task-visibility.md TV10, TV13): the journal is readable per task, the task
list survives a restart because it is served from the rebuilt cache, DELETE
means cancel-and-keep for running work and delete-journal for finished
work, and retention is a user setting whose default keeps everything.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS); ag.TASKS.clear()
    yield
    with ag.TASKS_LOCK:
        ag.TASKS.clear(); ag.TASKS.update(saved)


def _finished_task(tid="fin-0001", name="Finished"):
    now = time.time()
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = {"task_id": tid, "name": name, "description": "", "prompt": "p",
                         "status": "queued", "created": now - 10, "started": None, "ended": None,
                         "log": [], "result": "", "chain": None, "chain_step": 0, "model": None}
    tj.append(tid, "created", name=name, prompt="p")
    ag._task_set(tid, status="running", started=now - 9)
    ag._task_log(tid, "did a thing")
    ag._task_set(tid, status="complete", result="the answer", ended=now - 1)
    return tid


def _running_task(tid="run-0001", name="Running"):
    now = time.time()
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = {"task_id": tid, "name": name, "description": "", "prompt": "p",
                         "status": "queued", "created": now - 5, "started": None, "ended": None,
                         "log": [], "result": "", "chain": None, "chain_step": 0, "model": None}
    tj.append(tid, "created", name=name, prompt="p")
    ag._task_set(tid, status="running", started=now - 4)
    return tid


def test_journal_route_returns_events_and_state_with_a_cursor(client):
    tid = _finished_task()
    r = client.get(f"/api/tasks/{tid}/journal")
    assert r.status_code == 200
    body = r.get_json()
    kinds = [e["kind"] for e in body["events"]]
    assert kinds[0] == "created" and "started" in kinds and "checkpoint" in kinds and kinds[-1] == "ended"
    assert body["state"]["status"] == "complete" and body["state"]["result"] == "the answer"
    last = body["last_seq"]
    tail = client.get(f"/api/tasks/{tid}/journal?since={last}").get_json()
    assert tail["events"] == [] and tail["last_seq"] == last
    assert client.get("/api/tasks/nope/journal").status_code == 404


def test_task_list_survives_a_restart_because_it_is_served_from_disk(client):
    tid = _finished_task()
    with ag.TASKS_LOCK:
        ag.TASKS.clear()                                       # the restart
    assert all(t.get("task_id") != tid for t in client.get("/api/tasks").get_json().get("tasks", client.get("/api/tasks").get_json() if isinstance(client.get("/api/tasks").get_json(), list) else []))
    ag._restore_tasks_from_journal(announce=False)             # boot
    body = client.get("/api/tasks").get_json()
    rows = body.get("tasks") if isinstance(body, dict) else body
    assert any(t.get("task_id") == tid and t.get("status") == "complete" for t in rows)
    assert client.get(f"/api/tasks/{tid}").get_json()["result"] == "the answer"


def test_delete_cancels_running_work_and_keeps_its_journal(client):
    tid = _running_task()
    r = client.delete(f"/api/tasks/{tid}")
    assert r.get_json() == {"status": "cancelled", "journal": "kept"}
    assert ag._task_snapshot(tid)["status"] == "cancelled"
    events = tj.read(tid)
    assert events[-1]["kind"] == "halt" and events[-1]["cause"] == "cancelled"
    assert tj.task_dir(tid).exists()


def test_delete_removes_the_journal_of_finished_work(client):
    tid = _finished_task()
    r = client.delete(f"/api/tasks/{tid}")
    assert r.get_json() == {"status": "deleted", "journal": "deleted"}
    assert not tj.task_dir(tid).exists()
    assert client.get(f"/api/tasks/{tid}/journal").status_code == 404
    assert client.delete(f"/api/tasks/{tid}").status_code == 404


def test_retention_defaults_to_forever_and_is_a_user_setting(client, monkeypatch):
    from agent_friday import core
    saved = {}
    monkeypatch.setattr(core, "_load_settings_raw", lambda: {"task_journal": dict(saved.get("task_journal", {}))})
    monkeypatch.setattr(core, "_save_settings", lambda patch: saved.update(patch))
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {"task_journal": dict(saved.get("task_journal", {}))})
    old = _finished_task("old-0001", "Old")
    st = tj.read_state(old); st["ended"] = time.time() - 400 * 86400; tj.write_state(old, st)
    tj.index_put(old, "Old", "complete", st["created"], st["ended"])

    assert client.get("/api/tasks/retention").get_json()["retention_days"] == 0
    assert tj.task_dir(old).exists(), "default: nothing is ever deleted"

    assert client.post("/api/tasks/retention", json={"retention_days": -1}).status_code == 400
    r = client.post("/api/tasks/retention", json={"retention_days": 30})
    assert r.get_json()["retention_days"] == 30 and r.get_json()["deleted"] == [old]
    assert saved["task_journal"]["retention_days"] == 30
    assert not tj.task_dir(old).exists()
