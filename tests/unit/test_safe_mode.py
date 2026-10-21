"""Safe mode: extensions and the cloud off, so Friday can be repaired.

North star §28.5 (models optional, connectors disabled, schedules paused,
outward actions held, Doctor, backup), §28.5-2 (still reachable after starts
that keep failing), §33.5-3; burn-down BD-03.

Red on the old code by assertion: FRIDAY_SAFE_START did nothing, so every
refusal below was a normal answer (a cloud token, a web search, an allowed read
at a connected service, the owner's skills, a scheduled run, an MCP reload).
Only functions that exist before and after the change are called; the mode is
reset between tests where the new module exists.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


@pytest.fixture
def safe(monkeypatch):
    monkeypatch.setenv("FRIDAY_SAFE_START", "1")
    _reset()
    yield
    monkeypatch.delenv("FRIDAY_SAFE_START", raising=False)
    _reset()


def _reset():
    try:
        from agent_friday.services import safe_mode
        safe_mode.reset_for_tests()
    except ImportError:
        pass


def test_no_cloud_model_call_even_in_cloud_only_mode(safe, monkeypatch):
    from agent_friday.services import cloud_intent
    monkeypatch.setattr(cloud_intent, "routing_mode", lambda: "cloud_only")
    with pytest.raises(cloud_intent.NoCloudIntent):
        cloud_intent.require("anthropic", "probe", "claude-probe")


def test_a_local_model_still_answers(safe):
    from agent_friday.services import cloud_intent
    tok = cloud_intent.require("ollama", "probe", "llama-probe", base_url="http://127.0.0.1:11434")
    assert tok.intent == "local"


def test_web_search_and_fetch_are_refused_without_touching_the_network(safe, monkeypatch):
    import socket
    def no_net(*a, **k):
        raise AssertionError("NETWORK REACHED")
    monkeypatch.setattr(socket, "getaddrinfo", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    from agent_friday.services import web_fetch, web_search
    r = web_search.search("probe query")
    assert r["results"] == [] and "safe mode" in r["detail"]
    f = web_fetch.fetch("https://example.com/probe", use_cache=False)
    assert not f.ok and "safe mode" in (f.get("error") or "")


def test_outward_actions_and_connected_service_reads_are_held(safe):
    from agent_friday.governance import action_gate
    v = action_gate.authorize("create_calendar_event", {"title": "x"}, {"is_background_task": True})
    assert v.action == "deny" and "safe mode" in v.reason
    v = action_gate._decide("mcp_fakebank_get_balance", action_gate.OBSERVE, "a read",
                            {"is_background_task": True}, False)
    assert v.action == "deny" and "safe mode" in v.reason
    with pytest.raises(action_gate.Held):
        action_gate.record_external("send_email", surface="test", approval_id="card-1")


def test_the_owners_and_learned_skills_are_not_loaded(safe):
    from agent_friday import skill_registry
    dirs = [str(d) for d in skill_registry._default_dirs()]
    assert str(skill_registry.SKILLS_DIR) not in dirs
    assert str(skill_registry.BUNDLED_DIR) in dirs


def test_scheduled_jobs_are_paused(safe):
    from agent_friday.services import scheduler
    with pytest.raises(scheduler.StoodDown):
        scheduler._run_task_inner({"task": {"kind": "builtin", "ref": "probe"}, "id": "sch_probe"})


def test_mcp_servers_are_not_reloaded_and_channels_do_not_start(safe):
    from agent_friday.services import agent
    from agent_friday.services.channels import manager
    out = agent._mcp_reload()
    assert "safe mode" in str(out.get("error", "")), out
    r = manager.start_channel("telegram")
    assert r["ok"] is False and "safe mode" in r["error"]


def test_no_update_check_and_no_new_connection(safe, monkeypatch):
    import requests
    def no_net(*a, **k):
        raise AssertionError("NETWORK REACHED")
    monkeypatch.setattr(requests, "get", no_net)
    monkeypatch.setattr(requests.Session, "request", no_net)
    from agent_friday.services import connectors, update_check
    r = update_check.check_for_update()
    assert r["ok"] is False and "safe mode" in r["reason"]
    c = connectors.connect_connector("google")
    assert c["ok"] is False and "safe mode" in c["message"]


def test_hooks_the_owner_added_do_not_run_but_builtin_ones_do(safe):
    from agent_friday.services import tool_hooks as th
    ran = []
    th.register_pre_hook(lambda ctx: ran.append("user"), name="probe_user_hook", source="user",
                         priority=-1000, tools={"probe_tool"})
    th.register_pre_hook(lambda ctx: ran.append("builtin"), name="probe_builtin_hook",
                         priority=-1000, tools={"probe_tool"})
    try:
        th.run_pre_hooks(th.HookContext(tool_name="probe_tool", input={}))
    finally:
        th.unregister_hook("probe_user_hook")
        th.unregister_hook("probe_builtin_hook")
    assert "builtin" in ran and "user" not in ran


def test_self_modification_is_locked_too(safe):
    from agent_friday.services import boot_guard
    assert boot_guard.safe_mode() is True


def test_starts_that_keep_failing_open_safe_mode(monkeypatch):
    from agent_friday.services import boot_guard
    monkeypatch.delenv("FRIDAY_SAFE_START", raising=False)
    monkeypatch.setattr(boot_guard, "_read_attempt", lambda: {"consecutive_failures": 3})
    _reset()
    try:
        from agent_friday.governance import action_gate
        v = action_gate.authorize("create_calendar_event", {"title": "x"}, {"is_background_task": True})
        assert v.action == "deny" and "safe mode" in v.reason
    finally:
        _reset()


def test_a_normal_start_is_unchanged(monkeypatch):
    monkeypatch.delenv("FRIDAY_SAFE_START", raising=False)
    _reset()
    from agent_friday.governance import action_gate
    v = action_gate.authorize("create_calendar_event", {"title": "x"}, {"is_background_task": True})
    assert "safe mode" not in (v.reason or "")
    from agent_friday import skill_registry
    assert str(skill_registry.SKILLS_DIR) in [str(d) for d in skill_registry._default_dirs()]


# Every part safe mode keeps off is asked at the place it starts. A scan of the
# server's boot code, so a new background part added later without the question
# fails here.
_GUARDED_STARTS = (
    "_ph.ensure_started()", "_reconcile.run_at_boot", "_frm.run_at_boot",
    "_content_publisher.start()", "start_scheduler()", "_call_watch.start()",
    "target=_notification_trigger_loop", "target=_news_archiver_loop", "_podcast_engine.start_worker()",
    "_media_index.start_background()", "_library_runtime.start_background()",
    "target=_network_monitor_loop", "target=_predictive_prewarm_loop", "connector_health_monitor_loop, daemon",
    "sweep_loop, daemon", "_residency_boot, daemon", "_mm.start_loop()", "_la.boot(_port)",
)


def test_every_background_part_the_server_starts_asks_safe_mode_first():
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "server.py").read_text(
        encoding="utf-8")
    lines = src.splitlines()
    missing = []
    for needle in _GUARDED_STARTS:
        idx = [i for i, ln in enumerate(lines) if needle in ln and not ln.lstrip().startswith("#")
               and not ln.lstrip().startswith("def ")]
        assert idx, needle
        for i in idx:
            window = "\n".join(lines[max(0, i - 3):i + 1])
            if not re.search(r"allows\(\"[a-z_]+\"\)", window):
                missing.append(needle)
    assert not missing, "started without asking safe mode: %s" % missing
