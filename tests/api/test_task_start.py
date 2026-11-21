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
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    yield
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

@pytest.mark.parametrize("origin", ["pc\n", "device:dev_phone_1\n", "pc ", "PC"])
def test_an_origin_is_matched_whole_not_up_to_a_trailing_newline(client, spawned, origin):
    r = _start(client, origin=origin)
    assert r.status_code == 400 and spawned == []


def test_the_journal_says_where_a_job_came_from_not_that_the_device_is_paired(client, spawned):
    r = _start(client, origin="device:dev_unknown_9")
    entries = [e for e in tj.read(r.get_json()["task_id"]) if e["kind"] == "decision" and e["point"] == "origin"]
    assert "a paired device" not in entries[0]["reason"] and "device:dev_unknown_9" in entries[0]["reason"]


def test_a_journal_that_cannot_be_written_does_not_make_a_running_job_look_failed(client, spawned, monkeypatch):
    real = tj.append

    def append(tid, kind, **kw):
        if kind == "decision":
            raise OSError("disk full")
        return real(tid, kind, **kw)
    monkeypatch.setattr(tj, "append", append)
    r = _start(client)
    assert r.status_code == 201 and r.get_json()["task_id"] == "job-0001"


def test_a_spawn_that_raises_is_a_refusal_in_words_not_a_server_error(client, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("not allowed to start that")
    monkeypatch.setattr(ag, "_spawn_task", boom)
    r = _start(client)
    assert r.status_code == 409 and r.get_json()["error"] and "PermissionError" not in r.get_json()["error"]


@pytest.mark.parametrize("waiting", ["waiting_for_approval", "queued-for-seat", "waiting", "paused"])
def test_a_job_waiting_on_a_card_or_a_seat_still_holds_its_place_under_the_cap(client, spawned, waiting):
    """A job that is queued, waiting for a seat, waiting or paused has not ended: it is still the device's job.
    Three of them must not let a fourth start, and they must not be forgotten when they resume."""
    ids = [_start(client).get_json()["task_id"] for _ in range(task_start.DEVICE_JOB_CAP)]
    with ag.TASKS_LOCK:
        for tid in ids:
            ag.TASKS[tid]["status"] = waiting
    assert _start(client).status_code == 409
    with ag.TASKS_LOCK:
        ag.TASKS[ids[0]]["status"] = "running"                 # it resumes: still counted
    assert _start(client).status_code == 409
    with ag.TASKS_LOCK:
        ag.TASKS[ids[0]]["status"] = "complete"                # only an ending frees the place
    assert _start(client).status_code == 201


def test_a_job_whose_worker_is_finished_and_waits_only_for_the_owners_card_frees_its_place(client, spawned):
    """task_journal.TERMINAL counts waiting_approval as an ending (the worker is done; what is left is the owner's
    decision on a card), so it does not hold a device's place."""
    ids = [_start(client).get_json()["task_id"] for _ in range(task_start.DEVICE_JOB_CAP)]
    assert _start(client).status_code == 409
    with ag.TASKS_LOCK:
        ag.TASKS[ids[0]]["status"] = "waiting_approval"
    assert _start(client).status_code == 201


def _finished(tid="old-1"):
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = {"task_id": tid, "name": "an earlier job", "prompt": "tidy the downloads folder",
                         "status": "complete", "created": 1.0}
    return tid


def _rerun(client, tid, **body):
    return client.post("/api/tasks/%s/rerun" % tid, json=body)


def test_a_rerun_asked_for_by_a_device_counts_against_that_devices_cap(client, spawned):
    """The cap must not be a way around by re-running one finished job again and again."""
    old = _finished()
    for _ in range(task_start.DEVICE_JOB_CAP):
        assert _rerun(client, old, origin=PHONE).status_code == 200
    again = _rerun(client, old, origin=PHONE)
    assert again.status_code == 409 and "already running" in again.get_json()["error"]
    assert _start(client).status_code == 409                       # the same cap, from the other door
    assert len(spawned) == task_start.DEVICE_JOB_CAP


def test_a_rerun_from_the_pc_itself_is_not_capped(client, spawned):
    old = _finished()
    for _ in range(task_start.DEVICE_JOB_CAP + 2):
        assert _rerun(client, old).status_code == 200
    assert _rerun(client, old, origin="pc").status_code == 200


@pytest.mark.parametrize("origin", ["pc\n", 7, ["device:x"], "phone"])
def test_a_rerun_with_an_origin_that_is_not_one_is_refused_before_anything_starts(client, spawned, origin):
    old = _finished()
    assert _rerun(client, old, origin=origin).status_code == 400 and spawned == []


def test_a_cap_check_that_raises_does_not_leave_the_job_lock_held(monkeypatch):
    """__exit__ does not run when __enter__ raises, so the lock must be given back on any failure, or every later
    start and re-run waits for it forever."""
    def boom():
        raise RuntimeError("a bad record in the registry")
    monkeypatch.setattr(task_start, "_running_from_devices", boom)
    with pytest.raises(RuntimeError):
        with task_start.DeviceSlot(PHONE):
            pass
    assert task_start._LOCK.acquire(blocking=False), "the job lock was left held"
    task_start._LOCK.release()


@pytest.mark.parametrize("body", [[1], "text", 7, None])
def test_a_rerun_with_a_body_that_is_not_an_object_is_still_a_plain_rerun(client, spawned, body):
    """The route ignored its body before; a body that is not an object must not turn into a 500."""
    old = _finished()
    r = client.post("/api/tasks/%s/rerun" % old, json=body)
    assert r.status_code == 200 and len(spawned) == 1


def test_every_job_carries_its_origin_on_the_record_and_it_outlives_the_run(client, spawned):
    mine = _start(client, origin="pc").get_json()["task_id"]
    phone = _start(client).get_json()["task_id"]
    with ag.TASKS_LOCK:
        ag.TASKS[phone]["status"] = "complete"
    assert ag.TASKS[mine]["origin"] == "pc" and ag.TASKS[phone]["origin"] == PHONE
    rows = {t["task_id"]: t for t in client.get("/api/tasks").get_json()["tasks"]}
    assert rows[phone]["origin"] == PHONE and rows[mine]["origin"] == "pc"
    assert client.get("/api/tasks/%s" % phone).get_json()["origin"] == PHONE


def test_the_cap_is_read_from_the_records_so_a_finished_job_frees_its_place(client, spawned):
    ids = [_start(client).get_json()["task_id"] for _ in range(task_start.DEVICE_JOB_CAP)]
    assert _start(client).status_code == 409
    with ag.TASKS_LOCK:
        ag.TASKS[ids[0]]["status"] = "complete"
    assert _start(client).status_code == 201
