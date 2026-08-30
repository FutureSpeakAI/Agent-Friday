"""interactive_sessions — spawn_interactive_session / send_to_session /
read_session_output.

Focus: the adversarial cases Stephen asked to see proved, not just the happy
path — a Ring-3 denial is legible (never silent, never runs the handler), a
buffer overflow truncates visibly (never silently drops data unremarked), a
session that outlives its parent process gets reaped on the next boot (never
leaks like the orphaned llama-server incident this pattern was copied from),
and the recursion guard actually refuses before touching subprocess.Popen.

No FRIDAY_TESTING stub covers this module (it isn't an LLM entry point), so
these hit the real subprocess/Popen/registry code — the root conftest
redirects HOME to an isolated temp dir, so the on-disk registry lands there.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

import agent_friday.services.agent as agent_mod
import agent_friday.services.interactive_sessions as isess

# A handful of tests below launch a REAL child process and the command they
# launch is a Windows shell (cmd.exe / powershell.exe). Friday is a Windows
# desktop app, so that is the honest subject — but on Linux the spawn fails
# with ENOENT and the test reports a KeyError on 'session_id' rather than
# anything about the behaviour it names.
#
# Scoped to the individual tests that really spawn, NOT the module: the
# guardrail and ring-gating tests also mention cmd.exe, but they are refused
# or mocked before Popen is reached, so they are genuinely portable and keep
# running on every leg.
requires_windows_shell = pytest.mark.skipif(
    sys.platform != "win32",
    reason="launches a real cmd.exe/powershell.exe child; Windows-only by construction",
)


@pytest.fixture(autouse=True)
def clean_sessions():
    """Isolate per-test: no leftover in-memory sessions, no stale registry."""
    isess._SESSIONS.clear()
    try:
        isess._registry_path().unlink(missing_ok=True)
    except Exception:
        pass
    yield
    # Best-effort cleanup of anything a test actually spawned.
    for sid, entry in list(isess._SESSIONS.items()):
        try:
            entry["proc"].kill()
        except Exception:
            pass
    isess._SESSIONS.clear()
    try:
        isess._registry_path().unlink(missing_ok=True)
    except Exception:
        pass


# ── bounded buffer ───────────────────────────────────────────────────────────
class TestBuffer:
    def test_small_writes_are_never_truncated(self):
        buf = isess._Buffer(cap=1000)
        buf.append("hello ")
        buf.append("world")
        text, dropped = buf.snapshot()
        assert text == "hello world"
        assert dropped == 0

    def test_overflow_drops_oldest_and_reports_it_visibly(self):
        buf = isess._Buffer(cap=10)
        buf.append("0123456789")   # exactly at cap
        buf.append("ABCDE")        # eviction is whole-chunk: this pushes the
                                    # first 10-byte chunk out entirely, not a
                                    # 5-byte slice of it
        text, dropped = buf.snapshot()
        assert len(text) <= 10
        assert dropped == 10
        assert text == "ABCDE"
        assert "0123456789" not in text  # the oldest bytes are actually gone

    def test_tail_trim_on_read_also_counts_as_a_visible_drop(self):
        buf = isess._Buffer(cap=1000)
        buf.append("x" * 500)
        text, dropped = buf.snapshot(tail_chars=100)
        assert len(text) == 100
        assert dropped == 400


# ── spawn() guardrails ───────────────────────────────────────────────────────
class TestSpawnGuardrails:
    def test_empty_command_is_a_clear_error(self):
        result = isess.spawn(command="")
        assert result.get("error")

    def test_blocklisted_command_is_refused_before_any_process_exists(self, monkeypatch):
        called = []
        monkeypatch.setattr(isess.subprocess, "Popen",
                            lambda *a, **kw: called.append(1) or pytest.fail("Popen must not run"))
        result = isess.spawn(command="Remove-Item C:\\ -Recurse -Force")
        assert result.get("error")
        assert "blocklist" in result["error"].lower()
        assert called == []

    def test_recursion_guard_refuses_without_touching_popen(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_SESSION_DEPTH", "1")
        monkeypatch.setattr(isess.subprocess, "Popen",
                            lambda *a, **kw: pytest.fail("Popen must not run under the recursion guard"))
        result = isess.spawn(command="cmd.exe /c echo hi")
        assert result.get("error")
        assert "recursion" in result["error"].lower()

    def test_concurrency_cap_refuses_a_fourth_session(self, monkeypatch):
        class _FakeProc:
            def poll(self):
                return None  # still "running"

        for i in range(isess.MAX_CONCURRENT_SESSIONS):
            isess._SESSIONS[f"fake-{i}"] = {"proc": _FakeProc()}
        monkeypatch.setattr(isess.subprocess, "Popen",
                            lambda *a, **kw: pytest.fail("Popen must not run over the cap"))
        result = isess.spawn(command="cmd.exe /c echo hi")
        assert result.get("error")
        assert str(isess.MAX_CONCURRENT_SESSIONS) in result["error"]

    def test_cwd_outside_home_is_refused(self, monkeypatch):
        monkeypatch.setattr(isess.subprocess, "Popen",
                            lambda *a, **kw: pytest.fail("Popen must not run for an escaping cwd"))
        result = isess.spawn(command="cmd.exe /c echo hi", cwd="C:\\Windows\\System32")
        assert result.get("error")

    @requires_windows_shell
    def test_cwd_defaults_to_home_when_omitted(self):
        result = isess.spawn(command="cmd.exe /c echo default_cwd_ok")
        assert result.get("session_id")
        # realpath + normcase, because the two sides name the same directory
        # with different spellings. On the GitHub runner the child reports the
        # 8.3 short form "C:\\Users\\RUNNER~1\\..." while isess.HOME holds the
        # long "C:\\Users\\runneradmin\\...", and they also disagree on case.
        # realpath expands the short form and normcase folds the case, so this
        # compares WHICH directory was chosen — the test's actual subject —
        # rather than how the OS happened to spell it.
        def _same_dir(p):
            return os.path.normcase(os.path.realpath(str(p).rstrip("\\/")))

        assert _same_dir(result["cwd"]) == _same_dir(isess.HOME)


# ── real subprocess: spawn/read/send actually work ──────────────────────────
@requires_windows_shell
class TestSpawnAndInteractForReal:
    def _wait_for(self, predicate, timeout=10.0, interval=0.1):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return False

    def test_spawn_captures_output_and_reports_exit(self):
        result = isess.spawn(command="cmd.exe /c echo hello_from_child")
        sid = result["session_id"]
        assert result["status"] == "running"

        assert self._wait_for(
            lambda: isess.read_output(sid)["status"] == "exited", timeout=10)
        out = isess.read_output(sid)
        assert "hello_from_child" in out["output"]
        assert out["exit_code"] == 0

    def test_send_to_session_reaches_the_child_and_it_answers(self):
        result = isess.spawn(
            command='powershell.exe -NoProfile -Command '
                   '"$x = Read-Host; Write-Output (\'got:\' + $x)"')
        sid = result["session_id"]
        assert self._wait_for(
            lambda: "running" in isess.read_output(sid)["status"], timeout=10)

        send_result = isess.send(sid, "ping-value")
        assert send_result.get("sent") is True

        assert self._wait_for(
            lambda: "got:ping-value" in isess.read_output(sid)["output"], timeout=10)


# ── send/read error paths ────────────────────────────────────────────────────
class TestSendReadErrors:
    def test_send_to_unknown_session_is_a_clear_error_not_a_crash(self):
        result = isess.send("does-not-exist", "hi")
        assert result.get("error")

    def test_read_unknown_session_is_a_clear_error_not_a_crash(self):
        result = isess.read_output("does-not-exist")
        assert result.get("error")

    @requires_windows_shell
    def test_send_after_exit_is_refused(self):
        result = isess.spawn(command="cmd.exe /c echo bye")
        sid = result["session_id"]
        deadline = time.time() + 10
        while time.time() < deadline and isess.read_output(sid)["status"] == "running":
            time.sleep(0.1)
        send_result = isess.send(sid, "too late")
        assert send_result.get("error")
        assert "exited" in send_result["error"].lower()


# ── orphan reaping (boot-time) ───────────────────────────────────────────────
class TestOrphanReap:
    def test_a_session_that_outlives_its_parent_process_gets_reaped(self, monkeypatch):
        # Simulate what a PRIOR Friday process would have left behind: a
        # persisted registry entry with no in-memory handle (this "fresh"
        # process's _SESSIONS is empty, exactly as at real boot).
        isess._write_persisted({
            "orphan-1": {"pid": 999999, "command": "cmd.exe /c pause",
                        "cwd": str(isess.HOME), "started_at": time.time(),
                        "os_start_time": "2026-08-01T00:00:00"},
        })
        killed = []
        monkeypatch.setattr(isess, "_same_process", lambda pid, ts: True)
        monkeypatch.setattr(isess, "_kill_pid", lambda pid: killed.append(pid))

        report = isess.reap_orphans()

        assert killed == [999999]
        assert report["reaped"] == ["orphan-1"]
        assert isess._read_persisted() == {}   # never left for a THIRD process to trip over

    def test_a_pid_reused_by_an_unrelated_process_is_left_alone(self, monkeypatch):
        # The orphan's PID now belongs to some other, unrelated process (PID
        # reuse) — _same_process (start-time cross-check) says no, so taskkill
        # must never be called against it.
        isess._write_persisted({
            "orphan-2": {"pid": 888888, "command": "cmd.exe /c pause",
                        "cwd": str(isess.HOME), "started_at": time.time(),
                        "os_start_time": "2026-08-01T00:00:00"},
        })
        killed = []
        monkeypatch.setattr(isess, "_same_process", lambda pid, ts: False)
        monkeypatch.setattr(isess, "_kill_pid", lambda pid: killed.append(pid))

        report = isess.reap_orphans()

        assert killed == []                     # never touch a process we don't recognize
        assert report["reaped"] == []
        assert isess._read_persisted() == {}    # the stale record is still dropped either way

    def test_register_runs_the_reap_sweep_once_at_import_time(self, monkeypatch):
        calls = []
        monkeypatch.setattr(isess, "reap_orphans", lambda: calls.append(1) or {"reaped": []})
        isess.register([], {}, {})
        assert calls == [1]


# ── Ring 3 gating: enforced at dispatch, denial is legible ──────────────────
class TestRingGating:
    def test_spawn_is_denied_without_computer_control_permission(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "_cc_check",
                            lambda: (False, "Computer control permission not granted."))
        out = agent_mod._execute_tool(
            "spawn_interactive_session", {"command": "cmd.exe /c echo hi"},
            session_ctx={"authenticated": True})
        assert out.startswith("[GOVERNANCE DENY]")
        assert "ring-3" in out.lower() or "computer control" in out.lower()

    def test_denial_never_reaches_the_real_spawn_function(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "_cc_check",
                            lambda: (False, "Computer control permission not granted."))
        called = []
        monkeypatch.setattr(isess, "spawn", lambda **kw: called.append(kw) or {"session_id": "x"})
        agent_mod._execute_tool(
            "spawn_interactive_session", {"command": "cmd.exe /c echo hi"},
            session_ctx={"authenticated": True})
        assert called == []   # a denied ring-3 call must never invoke the handler

    def test_send_and_read_are_also_ring_3(self):
        assert agent_mod.TOOL_RINGS["send_to_session"] == 3
        assert agent_mod.TOOL_RINGS["read_session_output"] == 3

    def test_spawn_is_allowed_and_reaches_the_handler_once_cc_is_granted(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "_cc_check", lambda: (True, None))
        called = []
        monkeypatch.setattr(isess, "spawn",
                            lambda **kw: called.append(kw) or {"session_id": "sid-ok", "status": "running"})
        out = agent_mod._execute_tool(
            "spawn_interactive_session", {"command": "cmd.exe /c echo hi"},
            session_ctx={"authenticated": True, "is_background_task": True})
        assert called == [{"command": "cmd.exe /c echo hi", "cwd": None}]
        assert json.loads(out)["session_id"] == "sid-ok"


# ── registration sanity ──────────────────────────────────────────────────────
class TestToolRegistration:
    def test_all_three_tools_are_registered_and_dispatchable(self):
        for name in ("spawn_interactive_session", "send_to_session", "read_session_output"):
            assert name in agent_mod.CLAUDE_TOOL_HANDLERS
            assert any(t["name"] == name for t in agent_mod.CLAUDE_TOOLS)
            assert agent_mod.TOOL_RINGS[name] == 3

    def test_spawn_requires_confirmation_every_call(self):
        assert "spawn_interactive_session" in agent_mod._ALWAYS_CONFIRM
        # send/read must NOT be per-call confirmed -- a session already
        # exists only because its spawn was confirmed; re-confirming every
        # relay would make a multi-turn session unusable.
        assert "send_to_session" not in agent_mod._ALWAYS_CONFIRM
        assert "read_session_output" not in agent_mod._ALWAYS_CONFIRM
