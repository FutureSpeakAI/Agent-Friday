"""The floci launch contract (salon spec §4.6.1; docs/design/research/2026-09-30-s5-floci-spike.md).

floci's defaults bind the EC2 metadata server (9169) and the Lambda runtime API
(12000) on every interface, and they ignore the HTTP host setting. The salon's
box must hold exactly one socket, 127.0.0.1:4566. These tests pin the rule in
code: the environment Friday would start floci with, and the audit that refuses
any other listener.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import floci_box


def test_env_turns_off_the_services_that_bind_every_interface(tmp_path: Path):
    env = floci_box.launch_env(tmp_path)
    assert env["FLOCI_SERVICES_EC2_ENABLED"] == "false"
    assert env["FLOCI_SERVICES_LAMBDA_ENABLED"] == "false"


def test_env_binds_loopback_in_memory_in_its_own_folder(tmp_path: Path):
    env = floci_box.launch_env(tmp_path)
    assert env["QUARKUS_HTTP_HOST"] == "127.0.0.1"
    assert env["QUARKUS_HTTP_PORT"] == "4566"
    assert env["FLOCI_STORAGE_MODE"] == "memory"
    # memory mode still writes a few files; they land in the box's own folder, never the codebase
    assert Path(env[floci_box.DATA_PATH_VAR]) == tmp_path / "data"


def test_env_carries_nothing_from_the_owner_but_the_path(tmp_path: Path):
    base = {"PATH": "x", "SystemRoot": "C:\\W", "ANTHROPIC_API_KEY": "sk-ant-secret", "OPENROUTER_API_KEY": "k", "FRIDAY_TOKEN": "t"}  # pragma: allowlist secret
    env = floci_box.launch_env(tmp_path, base)
    assert env["PATH"] == "x" and env["SystemRoot"] == "C:\\W"
    for k in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "FRIDAY_TOKEN"):
        assert k not in env


def test_command_runs_the_jar_with_a_bounded_heap():
    cmd = floci_box.launch_command("C:/jdk/bin/java.exe", "C:/floci/quarkus-run.jar", heap_mb=1024)
    assert cmd[0] == "C:/jdk/bin/java.exe"
    assert "-Xmx1024m" in cmd
    assert cmd[-2:] == ["-jar", "C:/floci/quarkus-run.jar"]


NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:445            0.0.0.0:0              LISTENING       4
  TCP    127.0.0.1:4566         0.0.0.0:0              LISTENING       58748
  TCP    127.0.0.1:4566         127.0.0.1:56218        ESTABLISHED     58748
  TCP    0.0.0.0:9169           0.0.0.0:0              LISTENING       58748
  TCP    [::]:9169              [::]:0                 LISTENING       58748
  TCP    [::]:12000             [::]:0                 LISTENING       58748
  UDP    0.0.0.0:5353           *:*                                    1234
"""


def test_listener_audit_names_every_bind_beyond_loopback_for_that_process():
    rows = floci_box.parse_netstat(NETSTAT)
    assert floci_box.offending_listeners(rows, 58748) == ["0.0.0.0:9169", "[::]:9169", "[::]:12000"]
    assert floci_box.offending_listeners(rows, 4) == ["0.0.0.0:445"]


def test_listener_audit_passes_a_box_that_holds_only_loopback():
    text = "  TCP    127.0.0.1:4566   0.0.0.0:0   LISTENING   24300\n  TCP    [::1]:4566   [::]:0   LISTENING   24300\n"
    assert floci_box.offending_listeners(floci_box.parse_netstat(text), 24300) == []


def test_the_audit_is_the_gate_a_start_must_pass():
    with pytest.raises(RuntimeError, match=r"0\.0\.0\.0:9169"):
        floci_box.require_loopback_only(floci_box.parse_netstat(NETSTAT), 58748)
    floci_box.require_loopback_only(floci_box.parse_netstat("  TCP  127.0.0.1:4566  0.0.0.0:0  LISTENING  7\n"), 7)
