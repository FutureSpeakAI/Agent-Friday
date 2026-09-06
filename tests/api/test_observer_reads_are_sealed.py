"""The observer credential sees sealed text on EVERY route it may read.

docs/reference/task-observation.md promises every free-text field served
to a non-user principal passes through the gate, with a ledger row and a
`gate` decision on the task when something was redacted. The 2026-09-06
audit found /api/tasks and /api/tasks/<id> on the allowlist serving
prompt, result and log raw with no ledger row, and /api/processes and the
orchestrator routes serving orb logs and worker output the same way.
Red on 9d329fe.
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import observer_access as obs
from agent_friday.services import task_journal as tj

SECRET = "the client's SSN is 123-45-6789"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    monkeypatch.setattr(obs, "_hash_path", lambda: tmp_path / "obs.sha256")
    from agent_friday.services import activity_ledger as al
    monkeypatch.setattr(al, "LEDGER_PATH", tmp_path / "ledger.jsonl", raising=False)
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    # a deterministic gate: anything carrying the SSN is redacted
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "_gate_text", lambda text, provider, field, log_path=None:
                        "[REDACTED]" if "123-45-6789" in str(text) else text)  # pragma: allowlist secret
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    yield
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


def _seed(tid="obs-0001"):
    now = time.time()
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = {"task_id": tid, "name": "Task about " + SECRET, "description": "",
                         "prompt": SECRET, "status": "complete", "created": now - 30,
                         "started": now - 29, "ended": now - 1,
                         "log": ["Spawning agent", "note: " + SECRET], "result": "done: " + SECRET,
                         "chain": None, "chain_step": 0, "model": None}
    tj.append(tid, "created", name="Task", prompt=SECRET)
    tj.append(tid, "checkpoint", summary="note: " + SECRET)
    return tid


def _mint(client):
    return {obs.HEADER: client.post("/api/tasks/observer-token").get_json()["token"]}


def test_observer_list_and_detail_are_sealed_and_ledgered(client):
    tid = _seed()
    hdr = _mint(client)
    listing = client.get("/api/tasks", headers=hdr)
    assert listing.status_code == 200
    body = listing.get_data(as_text=True)
    assert "123-45-6789" not in body, "list served the prompt/result/log raw to the observer"  # pragma: allowlist secret
    assert listing.get_json().get("sealed_for") == "observer"
    detail = client.get(f"/api/tasks/{tid}", headers=hdr)
    assert detail.status_code == 200
    assert "123-45-6789" not in detail.get_data(as_text=True)  # pragma: allowlist secret
    from agent_friday.services import activity_ledger as al
    rows = [r for r in al.read(limit=50) if r.get("kind") == "journal_read"]
    assert {r.get("route") for r in rows} >= {"list", "detail"}, rows
    # the user still sees the whole thing
    assert "123-45-6789" in client.get(f"/api/tasks/{tid}").get_data(as_text=True)  # pragma: allowlist secret


def test_a_redacting_read_is_journaled_as_a_gate_decision(client):
    tid = _seed()
    hdr = _mint(client)
    r = client.get(f"/api/tasks/{tid}/digest", headers=hdr)
    assert r.status_code == 200 and r.get_json()["redacted_fields"] + r.get_json()["withheld_fields"] > 0
    gates = [e for e in tj.read(tid) if e["kind"] == "decision" and e.get("point") == "gate"]
    assert gates, "the redaction was not journaled on the task"
    assert gates[-1]["chosen"] in ("redacted", "withheld")
    assert "observer" in json.dumps(gates[-1])


def test_unsealed_routes_are_off_the_observer_allowlist(client):
    hdr = _mint(client)
    for path in ("/api/processes", "/api/orchestrator/workers", "/api/orchestrator/results/w1",
                 "/api/orchestrator/status"):
        assert client.get(path, headers=hdr).status_code == 403, path
    assert not any(p.startswith(("/api/processes", "/api/orchestrator")) for p in obs.READ_ONLY_PREFIXES)
