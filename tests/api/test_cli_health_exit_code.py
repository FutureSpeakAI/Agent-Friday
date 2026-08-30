"""`friday health --exit-code` — the exit-code contract greenboot's
`30-health.sh` depends on: 0 iff boot_critical_ok, non-zero otherwise.

Exercises `cli.cmd_health(exit_code=True)` and `cli._exit_code()` directly
(no subprocess) for speed, monkeypatching
`agent_friday.services.health_check.boot_critical_report` so each scenario is
deterministic rather than depending on this machine's real subsystem state.
The literal end-to-end subprocess run (`friday health --exit-code`, real exit
code observed by the shell) is demonstrated separately for the PR's
acceptance criteria — see the PR description for that captured output.
"""
from __future__ import annotations

from agent_friday import cli


def _report(boot_critical_ok, boot_status="ok"):
    return {
        "health_schema_version": 1,
        "boot_critical_ok": boot_critical_ok,
        "boot_status": boot_status,
        "subsystems": {
            "config": {"ok": True, "detail": "ok", "critical": True},
            "credential_store": {"ok": boot_critical_ok, "detail": "x", "critical": True},
            "memory_db": {"ok": True, "detail": "ok", "critical": True},
            "http_serving": {"ok": True, "detail": "ok", "critical": True},
        },
        "deployment": "unknown",
    }


def test_exit_code_flag_returns_true_and_exits_0_when_healthy(monkeypatch, capsys):
    from agent_friday.services import health_check
    monkeypatch.setattr(health_check, "boot_critical_report",
                        lambda **k: _report(True, "ok"))

    rv = cli.cmd_health(exit_code=True)

    assert rv is True
    assert cli._exit_code(rv) == 0


def test_exit_code_flag_returns_false_and_exits_nonzero_when_unhealthy(monkeypatch, capsys):
    from agent_friday.services import health_check
    monkeypatch.setattr(health_check, "boot_critical_report",
                        lambda **k: _report(False, "failed"))

    rv = cli.cmd_health(exit_code=True)

    assert rv is False
    assert cli._exit_code(rv) != 0


def test_plain_health_command_still_exits_0_regardless_of_boot_status(monkeypatch, capsys):
    """Backward compatibility: `friday health` with NO flag must keep its
    pre-existing behaviour (always exits 0) even when the boot-critical
    contract it now additionally prints is unhealthy — only `--exit-code`
    opts in to strict exit semantics.
    """
    from agent_friday.services import health_check
    monkeypatch.setattr(health_check, "boot_critical_report",
                        lambda **k: _report(False, "failed"))
    # Avoid the heavier diagnostic panels (providers/hardware/etc.) touching
    # anything real in this fast unit test — they are already independently
    # exercised elsewhere and are unrelated to the exit-code contract.
    monkeypatch.setattr(cli, "_ollama_probe", lambda *a, **k: (False, []))

    rv = cli.cmd_health(exit_code=False)

    assert rv is None
    assert cli._exit_code(rv) == 0


def test_health_subparser_accepts_exit_code_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["health", "--exit-code"])
    assert args.command == "health"
    assert args.exit_code is True


def test_health_subparser_defaults_exit_code_to_false():
    parser = cli.build_parser()
    args = parser.parse_args(["health"])
    assert args.exit_code is False
