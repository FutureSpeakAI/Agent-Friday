"""B3 tests — orb↔task↔trace correlation ids + tier-redacted thread view.

Tests the seams directly (the conftest stubs the LLM entry points, so the
full _call_claude_agent loop is exercised elsewhere):

  * _register_agent_orb carries task_id + model into the process record
  * _orb_tool_trace writes the compact log line + a timed, tier-redacted step
  * _tier_safe_summary withholds TIER>1 args/results
  * _tool_call_status classifies deny/error/ok sentinels
  * GET /api/tasks/<orb-pid> surfaces model + log + steps for the thread panel
"""

import re

import pytest

import agent_friday.core as core
from agent_friday.services.agent import (
    _register_agent_orb,
    _orb_tool_trace,
    _tier_safe_summary,
    _tool_call_status,
)


@pytest.fixture(autouse=True)
def _clean_processes():
    registered = set(core.PROCESSES)
    yield
    for pid in [p for p in core.PROCESSES if p not in registered]:
        core.process_remove(pid)


# ── _register_agent_orb ───────────────────────────────────────────────────────

def test_register_agent_orb_carries_task_id_and_model():
    orb_id = _register_agent_orb("Researching…", "monitoring", "🛰",
                                 "claude-sonnet-5",
                                 {"task_id": "t-123", "authenticated": True})
    assert orb_id and orb_id.startswith("agent-")
    proc = core.PROCESSES[orb_id]
    assert proc["task_id"] == "t-123"
    assert proc["model"] == "claude-sonnet-5"
    assert proc["label"] == "Researching…"


def test_register_agent_orb_without_session_ctx():
    orb_id = _register_agent_orb(None, "default", "🧠", None, None)
    assert orb_id
    proc = core.PROCESSES[orb_id]
    assert proc["task_id"] is None
    assert proc["model"]  # falls back to ANTHROPIC_MODEL_DEFAULT


# ── _tool_call_status ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("result,expected", [
    ("all good, found 3 results", "ok"),
    ("[VAULT-ZT DENY] financial", "deny"),
    ("[VAULT ACCESS DENIED] nope", "deny"),
    ("[CONFIRMATION REQUIRED] risky", "deny"),
    ("[GOVERNANCE DENY] ring 3", "deny"),
    ("[SANDBOX DENY] path", "deny"),
    ("Tool error (search_web): boom", "error"),
    ("Unknown tool: frobnicate", "error"),
    ("", "ok"),
    (None, "ok"),
])
def test_tool_call_status(result, expected):
    assert _tool_call_status(result) == expected


# ── _tier_safe_summary ────────────────────────────────────────────────────────

def test_tier_safe_summary_public_args_truncated():
    out = _tier_safe_summary({"query": "weather in toronto"}, limit=120)
    assert "weather in toronto" in out
    assert len(out) <= 120


def test_tier_safe_summary_withholds_sensitive():
    out = _tier_safe_summary(
        {"note": "my bank account number and routing number for the tax return"},
        kind="args")
    assert re.fullmatch(r"\[tier-[23] args withheld\]", out), out


def test_tier_safe_summary_withholds_sensitive_result():
    out = _tier_safe_summary(
        "patient medical diagnosis: prescription list follows", kind="result")
    assert re.fullmatch(r"\[tier-[23] result withheld\]", out), out


def test_tier_safe_summary_empty():
    assert _tier_safe_summary(None) == ""
    assert _tier_safe_summary("") == ""


# ── _orb_tool_trace ───────────────────────────────────────────────────────────

def test_orb_tool_trace_logs_line_and_timed_step():
    orb_id = _register_agent_orb("x", "monitoring", "🛰", "claude-sonnet-5",
                                 {"task_id": "t-9"})
    _orb_tool_trace(orb_id, "search_web", {"query": "hello"}, "found it", 234)
    proc = core.PROCESSES[orb_id]
    assert len(proc["log"]) == 1
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\] tool search_web → ok \(234ms\)", proc["log"][0])
    step = proc["steps"][-1]
    assert step["type"] == "tool"
    assert step["name"] == "search_web"
    assert step["status"] == "ok"
    assert step["duration_ms"] == 234
    assert "hello" in step["args"]


def test_orb_tool_trace_deny_line_and_redacted_args():
    orb_id = _register_agent_orb("x", "monitoring", "🛰", "m", None)
    _orb_tool_trace(orb_id, "vault_read",
                    {"path": "bank account number and routing number"},
                    "[VAULT-ZT DENY] financial", 5)
    proc = core.PROCESSES[orb_id]
    assert "tool vault_read → deny (5ms)" in proc["log"][0]
    step = proc["steps"][-1]
    assert step["status"] == "deny"
    assert "withheld" in step["args"]
    assert "bank account" not in step["args"]


def test_orb_tool_trace_none_orb_is_noop():
    _orb_tool_trace(None, "search_web", {}, "ok", 1)  # must not raise


# ── /api/tasks/<orb-pid> thread-view enrichment ───────────────────────────────

def test_get_task_orb_pid_includes_model_log_steps(client):
    pid = "agent-testorb1"
    core.process_register(pid, name="Friday", label="Reasoning…",
                          category="monitoring", icon="🛰",
                          model="claude-sonnet-5", task_id=None)
    core.process_log(pid, "[12:00:00] tool search_web → ok (150ms)")
    core.process_update(pid, step={"type": "tool", "name": "search_web",
                                   "status": "ok", "duration_ms": 150,
                                   "args": "query hello", "ts": 1.0})
    resp = client.get(f"/api/tasks/{pid}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["model"] == "claude-sonnet-5"
    assert any("search_web" in line for line in data["log"])
    assert data["steps"][0]["duration_ms"] == 150
    assert data["process"] is True


def test_get_task_orb_pid_follows_task_link_with_model(client):
    """Orb linked to a real TASKS entry → linked task returned, enriched with
    the orb's model + orb_id so the thread panel knows who served the loop."""
    from agent_friday.services.agent import TASKS, TASKS_LOCK
    tid = "t-link-test"
    with TASKS_LOCK:
        TASKS[tid] = {"task_id": tid, "name": "bg", "status": "running",
                      "created": 1.0, "started": 1.0, "ended": None,
                      "log": ["Spawning agent: bg"], "result": ""}
    try:
        pid = "agent-testorb2"
        core.process_register(pid, name="Friday", label="x",
                              category="monitoring", icon="🛰",
                              model="gemma4:latest", task_id=tid)
        data = client.get(f"/api/tasks/{pid}").get_json()
        assert data["task_id"] == tid
        assert data["model"] == "gemma4:latest"
        assert data["orb_id"] == pid
        assert data["log"] == ["Spawning agent: bg"]
    finally:
        with TASKS_LOCK:
            TASKS.pop(tid, None)
