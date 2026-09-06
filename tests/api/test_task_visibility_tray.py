"""Task visibility, phase 4 (docs/design/active/task-visibility.md §4.4,
TV7, TV8, TV10): the user's live view. The backend halves are proven on the
real loops and routes; the UI halves are pinned structurally in BOTH HTML
files, the way the Approvals card is, so the surface cannot silently
disappear from one of them.
"""
from __future__ import annotations

import json
import pathlib
import re
import time
import types

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj

ROOT = pathlib.Path(__file__).resolve().parents[2]
HTML_FILES = ("index.html", "ui_parts/app.html")


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(ag, "_execute_tool", lambda name, inp, **kw: f"ok:{name}")
    monkeypatch.setattr(ag, "_get_vault_control", lambda: None)
    monkeypatch.setattr(ag, "_seal_or_block", lambda payload, provider: payload)
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "meter", lambda *a, **k: 0.001)
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    yield
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


def _seed(tid, status="running", prompt="do the thing", **extra):
    now = time.time()
    rec = {"task_id": tid, "name": "Tray task", "description": "", "prompt": prompt,
           "status": "queued", "created": now - 20, "started": None, "ended": None,
           "log": [], "result": "", "chain": None, "chain_step": 0, "model": None}
    rec.update(extra)
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = rec
    tj.append(tid, "created", name="Tray task", prompt=prompt)
    if status != "queued":
        ag._task_set(tid, status="running", started=now - 19)
    if status not in ("running", "queued"):
        ag._task_set(tid, status=status, ended=now - 1, result="r")
    return tid


# ── stop-after-step on the real loops ────────────────────────────────────────

def _fake_send(n_rounds, seen):
    def send(convo, tools):
        seen.append(1)
        i = len(seen)
        msg = {"role": "assistant", "content": f"round {i}"}
        if i <= n_rounds:
            msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                                  "function": {"name": "search_web", "arguments": json.dumps({"q": i})}}]
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    return send


def test_stop_after_step_finishes_the_current_step_and_never_starts_the_next(client):
    tid = _seed("stop-0001")
    seen = []
    tools = [{"type": "function", "function": {"name": "search_web", "parameters": {"type": "object"}}}]
    # Request the stop before the loop starts: round 1 runs (the "current"
    # step), the loop stops at the checkpoint before round 2.
    assert client.post(f"/api/tasks/{tid}/stop-after-step").get_json()["stop_requested"] is True
    tj.push_task(tid)
    try:
        text, trace = ag._oai_agentic_loop([{"role": "user", "content": "go"}], tools,
                                           _fake_send(5, seen), provider="local", model="m",
                                           session_ctx={"task_id": tid})
    finally:
        tj.pop_task()
    assert len(seen) == 1 and len(trace) == 1, "exactly one round ran"
    assert "Stopped after step 1" in text
    events = tj.read(tid)
    # one model-call checkpoint (the task-log line for the tool is a checkpoint
    # too, with no phase) and one model_call: the second round never started
    assert sum(1 for e in events if e["kind"] == "checkpoint" and e.get("phase") == "model_call") == 1
    assert sum(1 for e in events if e["kind"] == "model_call") == 1
    halt = [e for e in tj.read(tid) if e["kind"] == "halt"][-1]
    assert halt["cause"] == "cancelled" and "step 1" in halt["detail"]
    steer = [e for e in tj.read(tid) if e["kind"] == "steer"]
    assert steer and steer[0]["message"] == "stop after this step" and steer[0]["source"] == "user"


def test_stop_after_step_on_the_anthropic_loop_too(client, monkeypatch):
    tid = _seed("stop-0002")
    calls = {"n": 0}

    class _Msgs:
        def create(self, **kw):
            calls["n"] += 1
            blk = types.SimpleNamespace(type="tool_use", id=f"t{calls['n']}", name="search_web", input={"q": 1})
            txt = types.SimpleNamespace(type="text", text=f"step {calls['n']}")
            return types.SimpleNamespace(content=[txt, blk], stop_reason="tool_use",
                                         usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))
    monkeypatch.setattr(ag, "get_anthropic_client", lambda: types.SimpleNamespace(messages=_Msgs()))
    client.post(f"/api/tasks/{tid}/stop-after-step")
    tj.push_task(tid)
    try:
        text, trace = ag._call_claude_agent([{"role": "user", "content": "go"}], session_ctx={"task_id": tid})
    finally:
        tj.pop_task()
    assert calls["n"] == 1 and "Stopped after step 1" in text


def test_worker_records_a_stopped_task_as_cancelled_not_complete(client, monkeypatch):
    tid = _seed("stop-0003", status="queued")
    monkeypatch.setattr(ag, "_get_friday_system_prompt", lambda *a, **k: "s")
    monkeypatch.setattr(ag, "_predict_route_provider", lambda **kw: "cloud")
    monkeypatch.setattr(ag, "_gated_vault_control", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_report_task_completion", lambda *a, **k: None)
    evaluated = []
    monkeypatch.setattr(ag, "_evaluate_output", lambda *a, **k: evaluated.append(1) or "GRADE: PASS")

    def fake_generate(messages, system=None, **kw):
        # the loop honoured the stop and returned its marker text
        tj.append(tid, "halt", cause="cancelled", detail="stopped after step 2 at the user's request")
        return "[Stopped after step 2 at the user's request.]", []
    monkeypatch.setattr(ag, "_generate_agent", fake_generate)
    tj.request_stop(tid)
    ag._task_worker(tid, "Tray task", "do the thing")
    snap = ag._task_snapshot(tid)
    assert snap["status"] == "cancelled" and "Stopped after step 2" in snap["result"]
    assert evaluated == [], "a stopped task is not graded"
    assert not tj.stop_requested(tid), "the stop was consumed"


def test_stop_after_step_refuses_a_finished_task_and_an_unknown_one(client):
    tid = _seed("stop-0004", status="complete")
    assert client.post(f"/api/tasks/{tid}/stop-after-step").status_code == 409
    assert client.post("/api/tasks/nope/stop-after-step").status_code == 404


# ── re-run: the human's choice, never automatic ─────────────────────────────

def test_rerun_spawns_a_new_task_from_the_recorded_prompt(client, monkeypatch):
    tid = _seed("rerun-0001", status="interrupted", prompt="finish the campaign")
    spawned = {}
    monkeypatch.setattr(ag, "_spawn_task", lambda name, prompt, **kw: spawned.update(name=name, prompt=prompt, kw=kw) or "new-0001")
    r = client.post(f"/api/tasks/{tid}/rerun")
    assert r.status_code == 200 and r.get_json() == {"ok": True, "task_id": "new-0001", "rerun_of": tid}
    assert spawned["prompt"] == "finish the campaign" and spawned["name"] == "Tray task"
    old = [e for e in tj.read(tid) if e["kind"] == "decision" and e["point"] == "rerun"]
    assert old and old[0]["chosen"] == "new-0001"
    new = [e for e in tj.read("new-0001") if e["kind"] == "decision" and e["point"] == "rerun_of"]
    assert new and new[0]["chosen"] == tid


def test_rerun_refuses_running_work_and_the_observer(client, monkeypatch):
    from agent_friday.services import observer_access as obs
    monkeypatch.setattr(obs, "_hash_path", lambda: tj.tasks_dir() / "obs.sha256")
    running = _seed("rerun-0002")
    assert client.post(f"/api/tasks/{running}/rerun").status_code == 409
    done = _seed("rerun-0003", status="complete")
    tok = client.post("/api/tasks/observer-token").get_json()["token"]
    r = client.post(f"/api/tasks/{done}/rerun", headers={obs.HEADER: tok})
    assert r.status_code == 403 and "read-only" in r.get_json()["error"]
    r = client.post(f"/api/tasks/{done}/stop-after-step", headers={obs.HEADER: tok})
    assert r.status_code == 403


def test_boot_restore_offers_interrupted_tasks_in_the_list(client):
    tid = "boot-0001"
    tj.append(tid, "created", name="Left running", prompt="p")
    tj.append(tid, "checkpoint", summary="Step 3 of 7")
    tj.write_state(tid, {"task_id": tid, "name": "Left running", "status": "running",
                         "created": time.time() - 100, "started": time.time() - 90, "log": [], "result": ""})
    tj.index_put(tid, "Left running", "running", time.time() - 100)
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
    ag._restore_tasks_from_journal(announce=False)
    rows = client.get("/api/tasks").get_json()
    rows = rows.get("tasks") if isinstance(rows, dict) else rows
    row = next(t for t in rows if t.get("task_id") == tid)
    assert row["status"] == "interrupted" and row["now"] == "Step 3 of 7"


# ── liveness on the list (TV7) ───────────────────────────────────────────────

def test_running_task_with_a_stale_heartbeat_is_reported_stalled(client):
    fresh = _seed("live-0001")
    stale = _seed("live-0002")
    for tid, age in ((stale, 120), (fresh, 2)):
        st = tj.read_state(tid)
        st["last_seen"] = time.time() - age
        tj.write_state(tid, st)
    tj.push_task(fresh)
    try:
        tj.checkpoint(1, "model_call", "Reasoning (step 1) on m")
        tj.model_call(model="m", provider="local", seat="local", cost_usd=0.25, iteration=1)
    finally:
        tj.pop_task()
    rows = client.get("/api/tasks").get_json()
    rows = {t["task_id"]: t for t in (rows.get("tasks") if isinstance(rows, dict) else rows) if t.get("task_id")}
    assert rows[stale]["stalled"] is True and rows[fresh]["stalled"] is False
    assert rows[fresh]["now"] == "Reasoning (step 1) on m" and rows[fresh]["cost_usd"] == 0.25
    assert rows[stale]["last_seen"] is not None


def test_steer_route_journals_the_message_with_its_source(client):
    tid = _seed("steer-0001")
    r = client.post("/api/agent/steer", json={"task_id": tid, "message": "focus on Q3"})
    assert r.status_code == 200
    ev = [e for e in tj.read(tid) if e["kind"] == "steer"]
    assert ev and ev[-1]["message"] == "focus on Q3" and ev[-1]["source"] == "user"


# ── the surface exists in BOTH HTML files (the Approvals-card shape) ─────────

@pytest.mark.parametrize("rel", HTML_FILES)
def test_tray_reads_the_journal_and_exposes_steer_stop_rerun_delete(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    assert "function TaskTimeline(" in text, f"{rel}: no timeline component"
    assert "function TaskControls(" in text, f"{rel}: no controls component"
    assert "/journal?since=" in text, f"{rel}: the drawer does not tail the journal by cursor"
    assert "record gap" in text, f"{rel}: a sequence gap is not shown"
    assert "/stop-after-step" in text and "/rerun" in text, f"{rel}: stop/re-run controls missing"
    assert "'/api/agent/steer'" in text, f"{rel}: steer control missing"
    assert "STALLED" in text and "INTERRUPTED" in text, f"{rel}: liveness states not rendered"
    assert "function TaskRecordsCard(" in text and "/api/tasks/retention" in text, f"{rel}: no retention control"
    assert re.search(r"TaskRecordsCard", text.split("function SystemWS(")[1][:20000]), f"{rel}: retention card not in the System workspace"


# ── phase 5: steer names its hand; the observer contract is documented ──────

def test_steer_source_is_recorded_as_agent_slug_or_user(client):
    from agent_friday.routes.tasks import _steer_source
    assert _steer_source(None) == "user"
    assert _steer_source("user") == "user"
    assert _steer_source("Fable") == "agent:fable"
    assert _steer_source("agent:Astra Prime!") == "agent:astra-prime"
    assert _steer_source("<script>" * 20).startswith("agent:") and len(_steer_source("x" * 99)) <= len("agent:") + 32
    tid = _seed("steer-0002")
    r = client.post("/api/agent/steer", json={"task_id": tid, "message": "narrow to Q3", "source": "Fable"})
    assert r.status_code == 200 and r.get_json()["source"] == "agent:fable"
    ev = [e for e in tj.read(tid) if e["kind"] == "steer"][-1]
    assert ev["source"] == "agent:fable" and ev["message"] == "narrow to Q3"


def test_observer_contract_is_documented_and_reachable_from_the_index():
    doc = ROOT / "docs" / "reference" / "task-observation.md"
    text = doc.read_text(encoding="utf-8")
    from agent_friday.services import observer_access as obs
    assert obs.HEADER in text, "the header name the code enforces is not in the operator doc"
    for pre in obs.READ_ONLY_PREFIXES:
        assert pre.split("/")[2] in text, f"allowlisted prefix {pre} is not documented"
    for route in ("/api/tasks/<id>/digest", "/api/tasks/<id>/events", "/api/tasks/observer-token",
                  "stop-after-step", "rerun", "reasoning=1", "gaps"):
        assert route in text, f"{route} missing from the operator doc"
    assert "reference/task-observation.md" in (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "task-observation.md" in (ROOT / "docs" / "reference" / "api.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", HTML_FILES)
def test_observer_token_can_be_minted_and_revoked_from_the_system_workspace(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    card = text.split("function TaskRecordsCard(")[1].split("\nfunction ")[0]
    assert "/api/tasks/observer-token" in card
    assert "method: 'POST'" in card.replace('method:"POST"', "method: 'POST'").replace("method:'POST'", "method: 'POST'")
    assert "'DELETE'" in card or '"DELETE"' in card
    assert "Shown once" in card, f"{rel}: the one-time nature of the token is not shown"


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/head.html"))
def test_every_rendered_task_state_has_a_visible_colour(rel):
    """Found in the browser 2026-09-06: INTERRUPTED and STOPPED rendered in
    black on the dark panel because only running/complete/failed had a
    colour rule. The label text was pinned and green; the pixels were not.
    Every state TaskCard can render must have a .task-card-status rule."""
    css = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    for state in ("running", "complete", "failed", "interrupted", "cancelled", "running.stalled"):
        assert re.search(r"\.task-card\." + re.escape(state) + r" \.task-card-status \{ color:", css), \
            f"{rel}: no colour for the {state} state"
