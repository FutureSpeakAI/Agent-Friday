"""Task journal, phase 1 (docs/design/active/task-visibility.md TV1, TV2,
TV8, TV12, TV13): durable task state written as it changes, rebuilt at boot
with interrupted work marked rather than erased, encrypted at rest, never
auto-deleted at a threshold nobody set.

Every test here drives the real path — agent._spawn_task / _task_worker /
_task_set / _task_log and the real journal module — with only the model call
stubbed. A static check that a field is written would prove nothing
(TV14); these assert on what a fresh process can read back from disk.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    pushed = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw) or kw)
    # Keep the worker hermetic: no model, no evaluator, no completion notice.
    monkeypatch.setattr(ag, "_generate_agent", lambda messages, system=None, **kw: ("all done", []))
    monkeypatch.setattr(ag, "_get_friday_system_prompt", lambda *a, **k: "test system prompt")
    monkeypatch.setattr(ag, "_predict_route_provider", lambda **kw: "cloud")
    monkeypatch.setattr(ag, "_gated_vault_control", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_evaluate_output", lambda *a, **k: "GRADE: UNAVAILABLE")
    monkeypatch.setattr(ag, "_report_task_completion", lambda *a, **k: None)
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS); ag.TASKS.clear()
        ag.TASK_THREADS.clear()
    yield pushed
    # No worker may outlive its test. A worker keeps writing after the task's
    # status turns terminal (wrap-up log lines, evaluator verdict, completion
    # report); on a slow Windows CI runner one such tail write landed inside
    # the NEXT test's patched `open` and produced a second, correct,
    # "unrecorded" announcement for a different task (run 34060387058 at
    # 9d329fe). Join everything this test spawned and fail loudly if a
    # worker is still alive, so the leak is a hard failure everywhere rather
    # than a race that only a cold runner loses.
    with ag.TASKS_LOCK:
        threads = list(ag.TASK_THREADS.values())
    for th in threads:
        th.join(timeout=20)
    still = [th.name for th in threads if th.is_alive()]
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)
        ag.TASK_THREADS.clear()
    assert not still, f"worker thread(s) outlived the test: {still}"


def _join_worker(tid, timeout=20):
    with ag.TASKS_LOCK:
        th = ag.TASK_THREADS.get(tid)
    if th is not None:
        th.join(timeout=timeout)
        assert not th.is_alive(), f"worker for {tid} did not finish"


def _run_to_completion(name="Journal test", prompt="say hello"):
    """Spawn a real task and wait for the WORKER THREAD to exit, not merely
    for the status to turn terminal — the worker writes after that."""
    tid = ag._spawn_task(name, prompt)
    _join_worker(tid)
    snap = ag._task_snapshot(tid) or {}
    assert tj.is_terminal(snap.get("status")), f"task did not finish: {snap}"
    return tid, snap


# ── TV1/TV2: the record exists on disk from the first instant ────────────────

def test_spawn_writes_created_state_and_index_before_the_worker_runs(monkeypatch):
    # Freeze the worker so we can look at disk while the task is still queued.
    started = []
    monkeypatch.setattr(ag, "_task_worker", lambda *a, **k: started.append(1) or time.sleep(0.3))
    tid = ag._spawn_task("Early record", "prompt text")
    events = tj.read(tid)
    assert [e["kind"] for e in events] == ["created"]
    assert events[0]["name"] == "Early record" and events[0]["prompt"] == "prompt text"
    st = tj.read_state(tid)
    assert st and st["status"] == "queued" and st["task_id"] == tid
    assert tj.index_read()[tid]["status"] == "queued"


def test_a_full_run_leaves_a_complete_journal_and_state():
    tid, snap = _run_to_completion()
    kinds = [e["kind"] for e in tj.read(tid)]
    assert kinds[0] == "created" and "started" in kinds and "ended" in kinds
    assert kinds.index("started") < kinds.index("ended")
    # The worker logs a couple of wrap-up lines and records the evaluator's
    # verdict after it sets the terminal status; those stay in the record.
    assert set(kinds[kinds.index("ended") + 1:]) <= {"checkpoint", "decision"}, kinds
    assert kinds.count("checkpoint") >= 3, kinds          # spawn/description/finalize log lines
    ended = [e for e in tj.read(tid) if e["kind"] == "ended"][0]
    assert ended["status"] == snap["status"] and "all done" in (ended["result"] or "")
    st = tj.read_state(tid)
    assert st["status"] == snap["status"] and st["result"] == snap["result"]
    assert tj.index_read()[tid]["status"] == snap["status"]
    seqs = [e["seq"] for e in tj.read(tid)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "sequence must be monotonic and unique"


def test_state_is_rewritten_on_every_change_not_at_completion(monkeypatch):
    seen = []
    real = tj.write_state
    monkeypatch.setattr(tj, "write_state", lambda tid, st: seen.append(st.get("status")) or real(tid, st))
    _run_to_completion()
    assert "queued" in seen and "running" in seen and seen[-1] in tj.TERMINAL
    assert len(seen) >= 4


# ── TV8: a restart marks interrupted work instead of erasing it ──────────────

def test_restart_rebuilds_the_cache_and_marks_running_work_interrupted(_iso):
    done_tid, _ = _run_to_completion("Finished before restart")
    # A task the previous process left running: state says running, no ended.
    dead = "dead-task-0001"
    tj.append(dead, "created", name="Ad campaign", prompt="run the campaign")
    tj.append(dead, "started")
    tj.append(dead, "checkpoint", summary="Step 4 of 9: drafting copy")
    tj.write_state(dead, {"task_id": dead, "name": "Ad campaign", "status": "running",
                          "created": time.time() - 60, "started": time.time() - 50,
                          "log": ["Step 4 of 9"], "result": ""})
    tj.index_put(dead, "Ad campaign", "running", time.time() - 60)

    with ag.TASKS_LOCK:
        ag.TASKS.clear()                     # the restart
    summary = ag._restore_tasks_from_journal()

    assert summary["loaded"] == 2
    assert [t["task_id"] for t in summary["interrupted"]] == [dead]
    snap = ag._task_snapshot(dead)
    assert snap["status"] == "interrupted" and "[Interrupted]" in snap["result"]
    halt = [e for e in tj.read(dead) if e["kind"] == "halt"][0]
    assert halt["cause"] == "interrupted" and halt["last_checkpoint"] == "Step 4 of 9: drafting copy"
    assert "nothing resumes automatically" in halt["resume_hint"]
    assert tj.is_terminal(ag._task_snapshot(done_tid)["status"]), "finished work survives too"
    titles = [p["title"] for p in _iso]
    assert any("interrupted by a restart" in t for t in titles), titles
    # Idempotent: a second boot finds nothing new to interrupt.
    assert ag._restore_tasks_from_journal(announce=False)["interrupted"] == []


def test_a_killed_process_leaves_a_record_a_new_process_reads(tmp_path):
    """The real thing: a separate interpreter spawns a task whose model call
    blocks forever, then dies mid-task. This process then restores from that
    home and must see the task as interrupted at its last checkpoint."""
    # The child shares this run's Friday home so it derives the SAME vault key
    # (same passphrase, same salt) that this process will read the journal
    # back with — exactly the situation of a real restart. Only the journal
    # location is redirected.
    from agent_friday import core as _core
    home = tmp_path / "home"
    home.mkdir()
    script = textwrap.dedent(f"""
        import os, sys, time, threading
        os.environ["FRIDAY_HOME"] = r"{_core.FRIDAY_DIR}"
        os.environ["FRIDAY_TESTING"] = "1"
        sys.path.insert(0, r"{Path(__file__).resolve().parents[2] / 'src'}")
        from agent_friday.services import agent as ag, task_journal as tj
        tj.BASE_DIR_OVERRIDE = r"{home / 'tasks'}"
        def hang(messages, system=None, **kw):
            ag._task_log(kw.get("_tid") or list(ag.TASKS)[0], "Step 2 of 5: reading the brief")
            time.sleep(3600)
        ag._generate_agent = hang
        ag._get_friday_system_prompt = lambda *a, **k: "test system prompt"
        ag._predict_route_provider = lambda **kw: "cloud"
        ag._gated_vault_control = lambda *a, **k: None
        tid = ag._spawn_task("Long job", "do the long thing")
        for _ in range(1200):
            time.sleep(0.05)
            if any("Step 2 of 5" in l for l in (ag._task_snapshot(tid) or {{}}).get("log", [])):
                break
        print(tid, flush=True)
        os._exit(9)          # no cleanup, no finally blocks: a real death
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    tid = (proc.stdout.strip().splitlines() or [""])[-1]
    assert tid, f"child never printed a task id\nstdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
    assert proc.returncode == 9

    tj.BASE_DIR_OVERRIDE = home / "tasks"
    tj.reset_for_tests()
    assert tj.read_state(tid)["status"] == "running", "the dying process left it running on disk"
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
    summary = ag._restore_tasks_from_journal(announce=False)
    assert [t["task_id"] for t in summary["interrupted"]] == [tid]
    events = tj.read(tid)
    assert any(e["kind"] == "checkpoint" and "Step 2 of 5" in e.get("summary", "") for e in events)
    assert events[-1]["kind"] == "halt" and events[-1]["cause"] == "interrupted"
    assert events[-1]["last_checkpoint"] == "Step 2 of 5: reading the brief"
    assert ag._task_snapshot(tid)["status"] == "interrupted"


# ── at rest: encrypted under the vault key ───────────────────────────────────

def test_journal_is_unreadable_on_disk_when_a_vault_key_exists(monkeypatch):
    from agent_friday.services import credential_store as cs
    from agent_friday.privacy import vault_crypto as vc
    key = vc.derive_key("test-passphrase-for-journal", b"0123456789abcdef")
    monkeypatch.setattr(cs, "_vault_key", lambda: key)
    secret = "the client's home address is 12 Rosewood Lane"
    tid, _ = _run_to_completion("Sensitive job", secret)
    d = tj.task_dir(tid)
    raw = (d / "journal.jsonl").read_bytes() + (d / "state.json").read_bytes()
    assert secret.encode() not in raw and b"Rosewood" not in raw
    assert (tj._index_path()).read_bytes().find(b"Sensitive job") == -1
    # ...and fully legible through the module.
    assert any(e.get("prompt") == secret for e in tj.read(tid) if e["kind"] == "created")
    assert tj.read_state(tid)["prompt"] == secret
    assert tj.index_read()[tid]["name"] == "Sensitive job"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is the no-passphrase fallback only on Windows")
def test_without_a_passphrase_dpapi_still_protects_the_journal(monkeypatch):
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "_vault_key", lambda: None)
    tid, _ = _run_to_completion("DPAPI job", "a private prompt about taxes")
    raw = (tj.task_dir(tid) / "journal.jsonl").read_bytes()
    assert b"private prompt about taxes" not in raw
    assert tj.read_state(tid)["prompt"] == "a private prompt about taxes"


def test_encrypt_at_rest_is_a_reversible_setting(monkeypatch):
    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 0, "capture_reasoning": True,
                                                 "encrypt_at_rest": False})
    tid, _ = _run_to_completion("Plain job", "visible prompt")
    assert b"visible prompt" in (tj.task_dir(tid) / "journal.jsonl").read_bytes()


# ── TV12: a journal that cannot write does not break the task, and says so ──

def test_write_failure_marks_the_task_unrecorded_loudly_and_work_continues(monkeypatch, _iso):
    real_open = open
    def failing_open(path, *a, **k):
        if str(path).endswith(("journal.jsonl", "state.json.tmp", "index.jsonl")):
            raise OSError("disk full")
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", failing_open)
    tid, snap = _run_to_completion("Unrecorded job")
    assert tj.is_terminal(snap["status"]) and snap["status"] != "failed", "the task itself still finished"
    assert snap.get("unrecorded") is True and "disk full" in snap.get("unrecorded_detail", "")
    unrec = [p for p in _iso if p["kind"] == "task_unrecorded"]
    assert len(unrec) == 1, "announced once, not once per write"
    assert tid[:8] in unrec[0]["body"]


# ── TV13: deletion is the user's; retention is a setting, default forever ───

def test_retention_zero_keeps_everything_and_positive_deletes_only_old_finished_tasks(monkeypatch):
    old_tid, _ = _run_to_completion("Old finished")
    new_tid, _ = _run_to_completion("New finished")
    # age the old one's index row and state
    st = tj.read_state(old_tid); st["ended"] = time.time() - 40 * 86400; tj.write_state(old_tid, st)
    tj.index_put(old_tid, "Old finished", st["status"], st["created"], st["ended"])
    running = "still-running-0001"
    tj.write_state(running, {"task_id": running, "name": "Running", "status": "running", "created": time.time() - 90 * 86400})
    tj.index_put(running, "Running", "running", time.time() - 90 * 86400)

    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 0, "capture_reasoning": True, "encrypt_at_rest": True})
    assert tj.apply_retention() == []
    assert tj.task_dir(old_tid).exists()

    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 30, "capture_reasoning": True, "encrypt_at_rest": True})
    assert tj.apply_retention() == [old_tid]
    assert not tj.task_dir(old_tid).exists()
    assert tj.task_dir(new_tid).exists(), "newer than the cutoff: kept"
    assert tj.task_dir(running).exists(), "never finished: never retention-deleted"
    assert tj.index_read()[old_tid]["status"] == "deleted"


def test_user_delete_removes_the_journal_of_a_finished_task():
    tid, _ = _run_to_completion("Delete me")
    assert tj.delete(tid) is True
    assert not tj.task_dir(tid).exists() and tj.read(tid) == []
    assert tj.index_read()[tid]["status"] == "deleted"
    assert tj.delete(tid) is False
