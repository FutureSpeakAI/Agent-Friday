"""Subagent/process transparency — clicking a task must show a real process.

Pins the 2026-07-08 fixes: a local-inference orb's detail endpoint must carry
model + intent log + result, the raw process status must map to the vocabulary
the detail panel renders RESULT under ('completed' -> 'complete'), and
monitoring processes must stay explorable well past the 30s ephemeral purge.
"""
import time

import agent_friday.core as core


def _register_inference_orb(pid):
    core.process_register(pid, name="Local Inference", label="Deep dive…",
                          category="monitoring", icon="🧠", steps=[],
                          model="gemma4:latest")
    core.process_log(pid, "model: gemma4:latest (local inference — on-device)")
    core.process_log(pid, "intent: summarize the Techmeme story")


def test_detail_carries_model_intent_and_result(client):
    pid = "local-testorb1"
    _register_inference_orb(pid)
    try:
        core.process_update(pid, status="completed", progress=1.0,
                            result="The article argues three things…")
        r = client.get(f"/api/tasks/{pid}")
        assert r.status_code == 200
        t = r.get_json()
        # Status must be mapped to the panel's RESULT-rendering vocabulary.
        assert t["status"] == "complete"
        assert t["model"] == "gemma4:latest"
        assert t["result"].startswith("The article argues")
        joined = "\n".join(t["log"])
        assert "gemma4" in joined and "intent:" in joined
    finally:
        core.process_remove(pid)


def test_result_is_bounded(client):
    pid = "local-testorb2"
    _register_inference_orb(pid)
    try:
        core.process_update(pid, result="x" * 99999)
        with core.PROCESSES_LOCK:
            assert len(core.PROCESSES[pid]["result"]) <= 4000
    finally:
        core.process_remove(pid)


def test_monitoring_processes_outlive_ephemeral_purge(client):
    pid_mon, pid_eph = "local-testorb3", "eph-testorb4"
    _register_inference_orb(pid_mon)
    core.process_register(pid_eph, name="Ephemeral", category="scheduler")
    try:
        old = time.time() - 120  # 2 minutes ago: past 30s, inside 15 min
        with core.PROCESSES_LOCK:
            for p in (pid_mon, pid_eph):
                core.PROCESSES[p]["status"] = "completed"
                core.PROCESSES[p]["ended"] = old
        client.get("/api/processes")  # triggers the purge pass
        with core.PROCESSES_LOCK:
            assert pid_mon in core.PROCESSES, (
                "monitoring orb purged too early — the detail view must stay "
                "explorable after completion")
            assert pid_eph not in core.PROCESSES, "ephemeral orb should purge at 30s"
    finally:
        core.process_remove(pid_mon)
        core.process_remove(pid_eph)
