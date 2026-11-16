"""The phone's jobs list over the gateway: a page of summary rows, never a job's prompt, log or result.

Stephen, 2026-11-15 (Mobile v3, order: chats, workspaces, jobs, media). The desktop's /api/tasks sends every job with
its whole record in one answer; a phone with a long history would get a body over the 4 MB cap and no list at all.
This file guards what the phone gets instead:

* the list is PAGED (a page, a total and a next_offset), optionally only jobs in one ``status``;
* each row carries the summary fields the phone draws (JOB_FIELDS) and nothing else: the prompt, the log, the result
  and the other record fields stay on the PC, and the detail routes remain the way to read one job;
* a stalled job arrives as the desktop worded it (never turned into "running"), because the row passes through;
* a list that would still be too big for a phone is refused, not sent.
"""
import json

import pytest

from agent_friday.tv_gateway import pc_read
from tests.unit.test_tv_pc_read import _clean, client, get, paired  # noqa: F401  (fixtures and helpers)


class Desk:
    def __init__(self):
        self.calls, self.tasks = [], []

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "/api/tasks":
            return 200, json.dumps({"tasks": self.tasks}).encode(), "application/json"
        return 404, b'{"error":"no"}', "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


def job(i, status="complete", **extra):
    return {"task_id": "t%d" % i, "name": "Job %d" % i, "status": status, "prompt": "SECRET PROMPT",
            "log": ["a line"] * 50, "result": "SECRET RESULT", "on_complete": "x", "tools": ["a"], **extra}


def test_the_list_is_one_page_with_a_total_and_a_next_offset(client, desk):
    desk.tasks = [job(i) for i in range(120)]
    r = get(client, paired(), "/tv/v1/jobs").get_json()
    assert [t["task_id"] for t in r["tasks"]] == ["t%d" % i for i in range(50)]
    assert r["total"] == 120 and r["offset"] == 0 and r["next_offset"] == 50
    last = get(client, paired(), "/tv/v1/jobs?offset=100").get_json()
    assert len(last["tasks"]) == 20 and last["next_offset"] is None


def test_a_huge_limit_is_capped_and_junk_is_ignored(client, desk):
    desk.tasks = [job(i) for i in range(300)]
    dev = paired()
    assert len(get(client, dev, "/tv/v1/jobs?limit=99999").get_json()["tasks"]) == 200
    assert get(client, dev, "/tv/v1/jobs?limit=x&offset=-3").get_json()["total"] == 300


def test_status_keeps_only_jobs_in_that_status(client, desk):
    desk.tasks = [job(1, "running"), job(2, "complete"), job(3, "running"), job(4, "stalled")]
    r = get(client, paired(), "/tv/v1/jobs?status=running").get_json()
    assert [t["task_id"] for t in r["tasks"]] == ["t1", "t3"] and r["total"] == 2


def test_a_row_is_a_summary_and_never_carries_the_prompt_log_or_result(client, desk):
    desk.tasks = [job(1, "running", progress=0.5, elapsed=12, stalled=False, now="reading", model="m")]
    body = get(client, paired(), "/tv/v1/jobs")
    row = body.get_json()["tasks"][0]
    assert set(row) <= set(pc_read.JOB_FIELDS) and row["progress"] == 0.5 and row["now"] == "reading"
    assert "SECRET" not in body.get_data(as_text=True) and not {"prompt", "log", "result", "tools"} & set(row)


def test_a_stalled_job_arrives_as_stalled_not_running(client, desk):
    desk.tasks = [job(1, "stalled", stalled=True)]
    row = get(client, paired(), "/tv/v1/jobs").get_json()["tasks"][0]
    assert row["status"] == "stalled" and row["stalled"] is True


def test_every_job_field_the_summary_names_survives_and_no_other_does(client, desk):
    full = {k: "v" for k in pc_read.JOB_FIELDS}
    desk.tasks = [{**full, "task_id": "t1", "prompt": "p", "extra_field": 1}]
    row = get(client, paired(), "/tv/v1/jobs").get_json()["tasks"][0]
    assert set(row) == set(pc_read.JOB_FIELDS)


def test_a_list_too_big_for_a_phone_is_refused_not_sent(client, desk, monkeypatch):
    monkeypatch.setattr(pc_read, "MAX_JSON", 1500)
    desk.tasks = [job(i, name="n" * 100) for i in range(40)]
    r = get(client, paired(), "/tv/v1/jobs")
    assert r.status_code == 413 and r.get_json()["code"] == "too_big"


def test_a_desktop_failure_is_a_plain_503_that_names_nothing(client, monkeypatch):
    def boom(path, params=None):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(pc_read, "_desk", boom)
    r = get(client, paired(), "/tv/v1/jobs")
    assert r.status_code == 503 and "secret detail" not in r.get_data(as_text=True)


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "family"), ("phone", "family"), ("phone", None)])
def test_only_an_owner_phone_or_tablet_with_the_right_is_answered(client, desk, kind, member):
    assert get(client, paired(kind, member), "/tv/v1/jobs?status=running&offset=5").status_code == 403 and desk.calls == []
