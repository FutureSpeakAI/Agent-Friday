"""PW-G12: the owner starts a background task from a prompt on another device. The desktop route is the one door;
the task is an ordinary one, so it shows as an orb and gets receipts like any other.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import global_stop, task_journal as tj, task_start

PHONE = "device:dev_phone_1"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_task_worker", lambda *a, **k: None)
    monkeypatch.setattr(global_stop, "active", lambda: False)
    task_start._FROM_DEVICE.clear()
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    yield
    task_start._FROM_DEVICE.clear()
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


@pytest.fixture
def spawned(monkeypatch):
    calls = []

    def fake(name, prompt, **kw):
        calls.append({"name": name, "prompt": prompt, **kw})
        tid = "job-%04d" % len(calls)
        with ag.TASKS_LOCK:
            ag.TASKS[tid] = {"task_id": tid, "status": "running", "prompt": prompt}
        tj.append(tid, "created", name=name, prompt=prompt)
        return tid

    monkeypatch.setattr(ag, "_spawn_task", fake)
    return calls


def _start(client, **body):
    body.setdefault("prompt", "tidy the downloads folder")
    body.setdefault("origin", PHONE)
    return client.post("/api/tasks/start", json=body)


def test_a_device_job_starts_as_the_owner_with_the_default_tools_and_records_where_it_came_from(client, spawned):
    r = _start(client)
    assert r.status_code == 201 and r.get_json() == {"ok": True, "task_id": "job-0001"}
    call = spawned[0]
    assert call["prompt"] == "tidy the downloads folder" and call["principal"] == "owner"
    assert "tools" not in call and "model" not in call and "scope" not in call
    origin = [e for e in tj.read("job-0001") if e["kind"] == "decision" and e["point"] == "origin"]
    assert origin and origin[0]["chosen"] == PHONE


def test_the_job_is_a_real_task_in_the_registry(client, monkeypatch):
    r = _start(client, prompt="summarise this week")
    assert r.status_code == 201
    rec = ag.TASKS[r.get_json()["task_id"]]
    assert rec["prompt"] == "summarise this week" and rec["principal"] == "owner"
    assert rec["name"] == "summarise this week"


@pytest.mark.parametrize("prompt", ["", "   ", None, 7, ["a"], "x" * (task_start.MAX_PROMPT + 1)])
def test_a_prompt_that_is_empty_not_text_or_too_long_is_refused_before_anything_starts(client, spawned, prompt):
    r = _start(client, prompt=prompt)
    assert r.status_code == 400 and spawned == []


@pytest.mark.parametrize("origin", [None, "", "phone", "device:", "device:../x", "pc;rm", "device:" + "a" * 97])
def test_an_origin_that_is_not_pc_or_a_device_id_is_refused(client, spawned, origin):
    assert _start(client, origin=origin).status_code == 400 and spawned == []


def test_an_unknown_conversation_is_refused(client, spawned):
    r = _start(client, conversation_id="conv_nope")
    assert r.status_code == 404 and spawned == []


def test_nothing_starts_while_everything_is_stopped(client, spawned, monkeypatch):
    monkeypatch.setattr(global_stop, "active", lambda: True)
    r = _start(client)
    assert r.status_code == 409 and "stopped" in r.get_json()["error"] and spawned == []


def test_the_fourth_concurrent_device_job_is_refused_in_words_and_a_finished_one_frees_a_place(client, spawned):
    for _ in range(task_start.DEVICE_JOB_CAP):
        assert _start(client).status_code == 201
    r = _start(client)
    assert r.status_code == 409 and "already running" in r.get_json()["error"]
    assert len(spawned) == task_start.DEVICE_JOB_CAP
    with ag.TASKS_LOCK:
        ag.TASKS["job-0001"]["status"] = "complete"
    assert _start(client).status_code == 201


def test_the_pc_own_origin_is_not_counted_against_the_device_cap(client, spawned):
    for _ in range(task_start.DEVICE_JOB_CAP):
        assert _start(client).status_code == 201
    assert _start(client, origin="pc").status_code == 201


def test_a_spawn_the_pc_declines_is_passed_on_not_called_a_success(client, monkeypatch):
    monkeypatch.setattr(ag, "_spawn_task", lambda *a, **k: None)
    r = _start(client)
    assert r.status_code == 409 and not r.get_json().get("ok")


def test_the_read_only_observer_cannot_start_a_job(client, spawned, monkeypatch, tmp_path):
    from agent_friday.services import observer_access as obs
    monkeypatch.setattr(obs, "_hash_path", lambda: tj.tasks_dir() / "obs.sha256")
    tok = client.post("/api/tasks/observer-token").get_json()["token"]
    r = client.post("/api/tasks/start", json={"prompt": "x", "origin": PHONE}, headers={obs.HEADER: tok})
    assert r.status_code == 403 and spawned == []
