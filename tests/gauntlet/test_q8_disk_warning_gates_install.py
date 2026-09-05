"""Q8 (gauntlet audit 2026-09-03): the installer computed a real disk-space
warning for the model it was about to download, printed it, and then
downloaded anyway.

`model_plan.plan()` already computes an accurate `disk_warning` (real free
space minus the real download size, against the `FREE_DISK_FLOOR_GIB` floor —
see model_plan.py's `plan()`/`render()`). `install.ps1`'s Step 1 only checks a
flat pip-dependency floor, never the specific model Step 2b/9 chooses, so a
machine that clears Step 1 can still be short by the time `cli.cmd_models`
reaches the actual pull. `cli.cmd_models(install=True)` printed that warning
(via `model_plan.render`) and then called `model_setup.install()`
unconditionally — the warning was decorative. A full disk mid-pull then fails
confusingly deep inside `ollama pull` instead of being caught up front.

Fix: `cmd_models` now gates on `plan["disk_warning"]` — it refuses to call
`model_setup.install()` when a warning is present, unless `force=True` (CLI:
`friday models --install --force`).

These tests call `cli.cmd_models` directly (no subprocess) and monkeypatch
`model_plan.plan` so the disk-warning branch is deterministic regardless of
this machine's real free space, following the pattern in
tests/api/test_cli_health_exit_code.py (monkeypatch the service layer,
exercise the CLI entry point, assert on the return code and on what got
called).
"""
from __future__ import annotations

from agent_friday import cli
from agent_friday.services import hardware_profile, model_plan, model_setup
from agent_friday.services import prewarm as _prewarm


def _hardware(**overrides):
    h = {"ram_gib": 32.0, "disk_free_gib": 15.0, "gpu_count": 1, "vram_gib": 12.0}
    h.update(overrides)
    return h


def _plan(disk_warning: bool, download=None, disk_after_gib=None) -> dict:
    """A plan shaped exactly like `model_plan.plan()`'s real return value —
    everything `render()` and `cmd_models` touch, nothing `plan()`'s own
    internals (this is a stand-in for the function, not a call to it).
    """
    if download is None:
        download = [{"id": "qwen3:4b", "gib": 20.2, "role": "brain",
                     "why": "answers you without touching a cloud provider",
                     "tools": True, "basis": "test"}]
    total = sum(m["gib"] for m in download)
    if disk_after_gib is None:
        disk_after_gib = round(15.0 - total, 1)
    return {
        "hardware": _hardware(),
        "ram_available_gib": 20.0,
        "vram_usable_gib": 9.5,
        "tiers": [
            {"id": "vault", "name": "Memory", "status": "ready",
             "reason": "runs on CPU", "models": []},
            {"id": "brain", "name": "Local conversational brain",
             "status": "install" if download else "ready",
             "reason": "test fixture", "models": download},
        ],
        "download": download,
        "download_gib": round(total, 2),
        "disk_after_gib": disk_after_gib,
        "disk_warning": disk_warning,
        "vault_ready": True,
    }


def _stub_success_install(monkeypatch, calls):
    def fake_install(plan, **kwargs):
        calls.append(plan)
        return {"ok": True, "results": [], "installed": len(plan.get("download") or []),
                "failed": 0, "summary": "all installed"}
    monkeypatch.setattr(model_setup, "install", fake_install)
    monkeypatch.setattr(model_setup, "vault_status",
                        lambda report, plan: (True, "Vault memory and tools are installed."))
    monkeypatch.setattr(_prewarm, "prewarm", lambda **k: {"ok": True, "summary": "warmed"})
    monkeypatch.setattr(_prewarm, "what_still_downloads_later", lambda w: [])


def _common_stubs(monkeypatch, plan_dict):
    # hardware_profile.get() itself is not under test here — model_plan.plan
    # is replaced wholesale, so its input only needs to not raise.
    monkeypatch.setattr(hardware_profile, "get", lambda *a, **k: {"stub": True})
    monkeypatch.setattr(model_plan, "plan", lambda *a, **k: plan_dict)


def test_disk_warning_blocks_install_by_default(monkeypatch, capsys):
    """A real disk_warning must stop the download before model_setup.install
    is ever called, and the process must report failure (non-zero)."""
    plan = _plan(disk_warning=True)
    _common_stubs(monkeypatch, plan)

    install_calls = []
    def fail_if_called(*a, **k):
        install_calls.append((a, k))
        raise AssertionError("model_setup.install must not run when "
                              "disk_warning is set and force is not passed")
    monkeypatch.setattr(model_setup, "install", fail_if_called)

    rv = cli.cmd_models(install=True, force=False)

    assert not install_calls, "install() was called despite an active disk_warning"
    assert rv not in (0, None), f"expected a non-zero abort code, got {rv!r}"

    out = capsys.readouterr().out
    assert "--force" in out, "abort message should tell the user how to override"


def test_disk_warning_with_force_proceeds_to_install(monkeypatch, capsys):
    """--force is the documented override: the same disk_warning must no
    longer block the call once force=True is passed."""
    plan = _plan(disk_warning=True)
    _common_stubs(monkeypatch, plan)
    calls = []
    _stub_success_install(monkeypatch, calls)

    rv = cli.cmd_models(install=True, force=True)

    assert len(calls) == 1, "install() should run exactly once when forced"
    assert calls[0] is plan
    assert rv == 0


def test_no_disk_warning_installs_normally(monkeypatch, capsys):
    """Sanity/no-op check: when the plan carries no warning, installation
    proceeds exactly as before — the gate must not fire on the happy path."""
    plan = _plan(disk_warning=False)
    _common_stubs(monkeypatch, plan)
    calls = []
    _stub_success_install(monkeypatch, calls)

    rv = cli.cmd_models(install=True, force=False)

    assert len(calls) == 1, "install() should still run when there is no warning"
    assert calls[0] is plan
    assert rv == 0


def test_models_subparser_accepts_force_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["models", "--install", "--force"])
    assert args.command == "models"
    assert args.install is True
    assert args.force is True


def test_models_subparser_defaults_force_to_false():
    parser = cli.build_parser()
    args = parser.parse_args(["models", "--install"])
    assert args.force is False
