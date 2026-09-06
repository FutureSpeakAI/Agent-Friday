"""Task visibility, phase 3 (docs/design/active/task-visibility.md §4.5,
TV6, TV11): the query surface over the journal, the READ-ONLY observer
credential, sealing for non-user principals, and gap-honest cursors.

Three things are proven rather than asserted:
  1. The observer credential can read, and CANNOT steer, cancel, delete,
     dismiss, spawn, change settings or mint — on every route those actions
     are reachable through, from loopback and from a remote address alike.
  2. The default digest carries no reasoning prose — asserted on bytes —
     and reasoning reaches an observer only on explicit request, through
     the gate, with a ledger row.
  3. A cursor that cannot be joined to the record is reported as a gap in
     JSON and in the SSE tail, never silently skipped.
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj
from agent_friday.services import observer_access as obs

NON_LOOPBACK = "203.0.113.7"
SECRET_THOUGHT = "private thought: the client is Mrs Okafor at 14 Linden Row"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    monkeypatch.setattr(obs, "_hash_path", lambda: tmp_path / "security" / "observer_token.sha256")
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    from agent_friday.services import activity_ledger as al
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity.jsonl")
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS); ag.TASKS.clear()
    yield
    with ag.TASKS_LOCK:
        ag.TASKS.clear(); ag.TASKS.update(saved)


def _task(tid="vis-0001", running=False, with_reasoning=True):
    now = time.time()
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = {"task_id": tid, "name": "Visible task", "description": "", "prompt": "do it",
                         "status": "queued", "created": now - 30, "started": None, "ended": None,
                         "log": [], "result": "", "chain": None, "chain_step": 0, "model": None}
    tj.append(tid, "created", name="Visible task", prompt="do it")
    ag._task_set(tid, status="running", started=now - 29)
    tj.push_task(tid)
    try:
        tj.checkpoint(1, "model_call", "Reasoning (step 1) on claude-sonnet-5")
        tj.model_call(model="claude-sonnet-5", provider="anthropic", seat="cloud",
                      tokens_in=100, tokens_out=20, cost_usd=0.01, duration_ms=800, iteration=1)
        if with_reasoning:
            tj.reasoning(text=SECRET_THOUGHT, thinking="hidden chain", iteration=1, model="claude-sonnet-5")
        tj.tool_call(name="search_web", args={"query": "linden row"}, result="ok", duration_ms=50)
        tj.decision("seat_select", "cloud/claude-sonnet-5", reason="cloud_only mode",
                    alternatives=["local", "openai"])
    finally:
        tj.pop_task()
    if not running:
        ag._task_set(tid, status="complete", result="the answer", ended=now - 1)
    return tid


def _mint(client):
    r = client.post("/api/tasks/observer-token")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["token"]


def _obs(token):
    return {obs.HEADER: token}


# ═══ 1. Read-only: proven by attempting the writes ═══════════════════════════

def test_observer_can_read_every_read_route(client):
    tid = _task()
    tok = _mint(client)
    for path in (f"/api/tasks", f"/api/tasks/{tid}", f"/api/tasks/{tid}/journal",
                 f"/api/tasks/{tid}/digest", f"/api/tasks/{tid}/events",
                 "/api/activity", "/api/tasks/retention"):
        r = client.get(path, headers=_obs(tok))
        assert r.status_code == 200, (path, r.status_code, r.get_data(as_text=True)[:200])
    # Routes that do not seal their free text are refused, not served raw
    # (2026-09-06 audit: orb logs and worker outputs were on the allowlist).
    for path in ("/api/processes", "/api/orchestrator/status", "/api/orchestrator/workers",
                 "/api/orchestrator/results/w1"):
        r = client.get(path, headers=_obs(tok))
        assert r.status_code == 403, (path, r.status_code)
    # and from a remote address, with no FRIDAY_REMOTE_KEY configured at all
    r = client.get(f"/api/tasks/{tid}/digest", headers=_obs(tok),
                   environ_overrides={"REMOTE_ADDR": NON_LOOPBACK})
    assert r.status_code == 200


MUTATIONS = [
    ("POST", "/api/agent/steer", {"task_id": "vis-0001", "message": "stop everything"}),
    ("DELETE", "/api/tasks/vis-0001", None),
    ("POST", "/api/processes/vis-0001/cancel", None),
    ("POST", "/api/processes/vis-0001/dismiss", None),
    ("POST", "/api/processes/dismiss-failed", None),
    ("POST", "/api/orbs/clear", None),
    ("POST", "/api/tasks/retention", {"retention_days": 1}),
    ("POST", "/api/tasks/observer-token", None),
    ("DELETE", "/api/tasks/observer-token", None),
    ("POST", "/api/settings", {"settings": {"task_journal": {"capture_reasoning": False}}}),
    ("POST", "/api/orchestrator/cancel/worker-1", None),
    ("POST", "/api/orchestrator/spawn", {"prompt": "run something"}),
    ("POST", "/api/orchestrator/delegate", {"prompt": "run something"}),
    ("POST", "/api/orchestrator/cleanup", None),
]


@pytest.mark.parametrize("method,path,body", MUTATIONS)
@pytest.mark.parametrize("addr", ["127.0.0.1", NON_LOOPBACK])
def test_observer_credential_cannot_steer_cancel_delete_or_configure(client, method, path, body, addr):
    tid = _task(running=True)
    tok = _mint(client)
    before = ag._task_snapshot(tid)["status"]
    r = client.open(path, method=method, json=body, headers=_obs(tok),
                    environ_overrides={"REMOTE_ADDR": addr})
    assert r.status_code == 403, (method, path, addr, r.status_code, r.get_data(as_text=True)[:200])
    assert "read-only" in r.get_json().get("error", "")
    # Nothing moved: the task is still running, the token still valid, retention untouched.
    assert ag._task_snapshot(tid)["status"] == before
    assert obs.verify(tok)


def test_presenting_the_header_demotes_even_a_loopback_user(client):
    """A confused local agent that sends the observer header cannot borrow
    loopback trust for a write; the header decides the principal first."""
    tid = _task(running=True)
    tok = _mint(client)
    r = client.post("/api/agent/steer", json={"task_id": tid, "message": "x"}, headers=_obs(tok))
    assert r.status_code == 403
    # the same write WITHOUT the header, from loopback, is the user's and works
    r = client.post("/api/agent/steer", json={"task_id": tid, "message": "x"})
    assert r.status_code == 200


def test_invalid_or_revoked_observer_token_is_refused_outright(client):
    tid = _task()
    r = client.get(f"/api/tasks/{tid}/digest", headers=_obs("fobs_not-a-real-token"))
    assert r.status_code == 401
    tok = _mint(client)
    assert client.get(f"/api/tasks/{tid}/digest", headers=_obs(tok)).status_code == 200
    assert client.delete("/api/tasks/observer-token").get_json()["revoked"] is True
    assert client.get(f"/api/tasks/{tid}/digest", headers=_obs(tok)).status_code == 401
    # minting again invalidates the previous token (one credential at a time)
    tok1 = _mint(client); tok2 = _mint(client)
    assert not obs.verify(tok1) and obs.verify(tok2)


def test_token_is_never_stored_only_its_hash(client, tmp_path):
    tok = _mint(client)
    stored = (tmp_path / "security" / "observer_token.sha256").read_text()
    assert tok not in stored and len(stored.strip()) == 64


# ═══ 2. The digest does not leak reasoning; the gate stands between ═════════

def test_default_digest_has_no_reasoning_bytes_for_anyone(client):
    tid = _task()
    for headers in ({}, _obs(_mint(client))):
        r = client.get(f"/api/tasks/{tid}/digest", headers=headers)
        body = r.get_data()
        assert r.status_code == 200
        assert b"private thought" not in body and b"Okafor" not in body and b"hidden chain" not in body, headers
        d = r.get_json()
        assert "reasoning" not in d
        assert d["status"] == "complete" and d["cost_usd"] == 0.01 and d["model"] == "claude-sonnet-5"
        assert d["decisions"][0]["point"] == "seat_select" and d["now"].startswith("Reasoning (step 1)")


def test_user_gets_reasoning_intact_on_explicit_request(client):
    tid = _task()
    r = client.get(f"/api/tasks/{tid}/digest?reasoning=1")
    d = r.get_json()
    assert d["reasoning"][0]["text"] == SECRET_THOUGHT and d["reasoning"][0]["thinking"] == "hidden chain"
    assert "sealed_for" not in d


def test_observer_gets_reasoning_only_through_the_gate_with_a_ledger_row(client, monkeypatch, tmp_path):
    import agent_friday.services.egress_gate as eg
    seen = []
    def fake_gate(text, provider, field, log_path=None):
        seen.append((provider, field))
        if "Okafor" in text:
            return text.replace("Mrs Okafor at 14 Linden Row", "[PII:addr]")
        return text
    monkeypatch.setattr(eg, "_gate_text", fake_gate)
    tid = _task()
    tok = _mint(client)
    r = client.get(f"/api/tasks/{tid}/digest?reasoning=1", headers=_obs(tok))
    assert r.status_code == 200
    body = r.get_data()
    assert b"Okafor" not in body and b"[PII:addr]" in body
    d = r.get_json()
    assert d["sealed_for"] == "observer" and d["redacted_fields"] >= 1
    assert all(p == "observer" for p, _ in seen) and any(f == "task_journal.text" for _, f in seen)
    rows = [json.loads(l) for l in (tmp_path / "activity.jsonl").read_text().splitlines()]
    jr = [x for x in rows if x["kind"] == "journal_read"]
    assert jr and jr[-1]["principal"] == "observer" and jr[-1]["reasoning"] is True and jr[-1]["redacted"] >= 1
    # the user's own read of the same thing writes no egress row
    n = len(jr)
    client.get(f"/api/tasks/{tid}/digest?reasoning=1")
    rows = [json.loads(l) for l in (tmp_path / "activity.jsonl").read_text().splitlines()]
    assert len([x for x in rows if x["kind"] == "journal_read"]) == n


def test_never_send_material_is_withheld_from_an_observer_and_gate_failure_fails_closed(client, monkeypatch):
    import agent_friday.services.egress_gate as eg
    tid = _task()
    tok = _mint(client)
    def blocking_gate(text, provider, field, log_path=None):
        if "Okafor" in text:
            raise eg.NeverSendBlocked("never-send watchlist")
        return text
    monkeypatch.setattr(eg, "_gate_text", blocking_gate)
    d = client.get(f"/api/tasks/{tid}/digest?reasoning=1", headers=_obs(tok)).get_json()
    assert d["reasoning"][0]["text"] == tj.WITHHELD and d["withheld_fields"] >= 1
    # gate unreachable → every text field withheld, nothing leaks
    monkeypatch.setattr(eg, "_gate_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate down")))
    r = client.get(f"/api/tasks/{tid}/journal", headers=_obs(tok))
    assert b"Okafor" not in r.get_data() and b"linden row" not in r.get_data()
    assert r.get_json()["withheld_fields"] >= 1


def test_observer_sees_the_journal_sealed_but_the_user_sees_it_whole(client, monkeypatch):
    import agent_friday.services.egress_gate as eg
    monkeypatch.setattr(eg, "_gate_text", lambda text, provider, field, log_path=None: text.replace("linden", "[q]"))
    tid = _task()
    tok = _mint(client)
    user = client.get(f"/api/tasks/{tid}/journal").get_json()
    observer = client.get(f"/api/tasks/{tid}/journal", headers=_obs(tok)).get_json()
    u_tool = next(e for e in user["events"] if e["kind"] == "tool_call")
    o_tool = next(e for e in observer["events"] if e["kind"] == "tool_call")
    assert "linden" in u_tool["args"] and "[q]" in o_tool["args"]
    assert observer["sealed_for"] == "observer" and "sealed_for" not in user


# ═══ 3. Cursors and tails are gap-honest ═════════════════════════════════════

def _journal_with_hole(tid="gap-0001"):
    tj.write_state(tid, {"task_id": tid, "name": "gappy", "status": "running", "created": time.time()})
    tj.append(tid, "created", name="gappy")           # seq 1
    tj.append(tid, "checkpoint", summary="one")       # seq 2
    tj.append(tid, "checkpoint", summary="two")       # seq 3
    # Simulate a lost write: drop seq 3 from the file (a failed append that
    # consumed a sequence number), then continue.
    p = tj.task_dir(tid) / "journal.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    kept = [l for l in lines if tj._decode_line(l)["seq"] != 3]
    p.write_text("\n".join(kept) + "\n", encoding="utf-8")
    tj.append(tid, "checkpoint", summary="four")      # seq 4
    return tid


def test_journal_and_events_report_a_hole_instead_of_hiding_it(client):
    tid = _journal_with_hole()
    for route in ("journal", "events"):
        d = client.get(f"/api/tasks/{tid}/{route}?since=0").get_json()
        assert [e["seq"] for e in d["events"]] == [1, 2, 4]
        assert d["gaps"] == [{"missing_from": 3, "missing_to": 3}]
        assert d["complete"] is False and d["cursor_ahead"] is False
    # resuming from a cursor just before the hole still reports it
    d = client.get(f"/api/tasks/{tid}/events?since=2").get_json()
    assert d["gaps"] == [{"missing_from": 3, "missing_to": 3}] and [e["seq"] for e in d["events"]] == [4]
    # a clean resume is marked complete
    d = client.get(f"/api/tasks/{tid}/events?since=4").get_json()
    assert d["events"] == [] and d["complete"] is True and d["gaps"] == []


def test_cursor_beyond_the_record_is_flagged_not_treated_as_caught_up(client):
    tid = _task()
    last = client.get(f"/api/tasks/{tid}/events").get_json()["journal_last_seq"]
    d = client.get(f"/api/tasks/{tid}/events?since={last + 50}").get_json()
    assert d["events"] == [] and d["cursor_ahead"] is True and d["complete"] is False


def test_sse_tail_announces_gaps_carries_ids_and_ends_on_terminal(client):
    tid = _journal_with_hole()
    tj.write_state(tid, dict(tj.read_state(tid), status="complete"))
    r = client.get(f"/api/tasks/{tid}/events?stream=1&since=0&max_wait=8")
    assert r.status_code == 200 and r.mimetype == "text/event-stream"
    text = r.get_data(as_text=True)
    frames = [f for f in text.split("\n\n") if f.strip()]
    assert frames[0].startswith("event: gap") and '"missing_from": 3' in frames[0]
    ids = [int(l.split("id: ")[1]) for f in frames for l in f.splitlines() if l.startswith("id: ")]
    assert ids == [1, 2, 4]
    assert any(f.startswith("event: end") for f in frames)
    # a reconnect from the last id it saw resumes cleanly
    r2 = client.get(f"/api/tasks/{tid}/events?stream=1&max_wait=3", headers={"Last-Event-ID": "4"})
    frames2 = [f for f in r2.get_data(as_text=True).split("\n\n") if f.strip()]
    assert not any(f.startswith("event: gap") for f in frames2)
    assert not any(l.startswith("id: ") for f in frames2 for l in f.splitlines())


def test_sse_tail_from_a_cursor_beyond_the_record_says_so_first(client):
    tid = _task()
    r = client.get(f"/api/tasks/{tid}/events?stream=1&since=999&max_wait=3")
    frames = [f for f in r.get_data(as_text=True).split("\n\n") if f.strip()]
    assert frames and frames[0].startswith("event: gap") and "beyond the record" in frames[0]
