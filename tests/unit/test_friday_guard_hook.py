"""The Claude Code PreToolUse guard: every hard rule blocks the bad call and
lets the good one through.

The hook is a script, so it is loaded from its path and driven through
``decide()`` with an injected memory reading and clock; one subprocess test
proves the exit-code contract Claude Code relies on (2 blocks, 0 allows).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import pytest_resource_guard as resource_guard

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "hooks" / "friday_guard.py"

spec = importlib.util.spec_from_file_location("friday_guard", HOOK)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def bash(command, cwd):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


def pwsh(command, cwd):
    return {"tool_name": "PowerShell", "tool_input": {"command": command}, "cwd": str(cwd)}


def edit(path):
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}, "cwd": str(ROOT)}


@pytest.fixture
def live(tmp_path):
    """A pretend live checkout, a worktree beside it, and a config naming them."""
    live = tmp_path / "live"
    (live / ".claude" / "worktrees" / "agent-x").mkdir(parents=True)
    (live / "src").mkdir()
    (live / "AGENTS.md").write_text("x", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    cfg = g.load_config(tmp_path / "missing.json")
    cfg.update({"live_checkout": g.norm_path(str(live)),
                "deploy_lane_token": str(live / ".claude" / "DEPLOY_LANE"),
                "audit_log": str(tmp_path / "audit.log")})
    return {"live": live, "wt": wt, "cfg": cfg, "audit": tmp_path / "audit.log"}


@pytest.fixture
def friday_tree(tmp_path):
    """A directory that counts as a Friday checkout, and one that does not."""
    tree = tmp_path / "friday"
    (tree / "tests" / "unit").mkdir(parents=True)
    (tree / resource_guard.__name__).with_suffix(".py").write_text("", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    return tree, other


def base_cfg():
    return g.load_config(Path("/definitely/missing.json"))


def decide(payload, cfg, **kw):
    """The hook's decision with the machine probes pinned: plenty of memory and
    no other pytest process, unless a test says otherwise."""
    kw.setdefault("ram", 20.0)
    kw.setdefault("running", [])
    return g.decide(payload, cfg, **kw)


# ── the floors match the pytest plugin ───────────────────────────────────────

def test_default_floors_match_pytest_resource_guard():
    assert g.DEFAULTS["min_free_ram_gb"] == resource_guard.MIN_FREE_RAM_GB
    assert g.DEFAULTS["min_free_disk_gb"] == resource_guard.MIN_FREE_DISK_GB


# ── rule 1: broad pytest ─────────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "pytest",
    "pytest tests/unit tests/api -q",
    "pytest -q -n 4 tests",
    "./venv/Scripts/python.exe -m pytest -n 4",
    "python -m pytest tests/unit -x -q --tb=short",
    "FRIDAY_TESTING=1 pytest tests/api",
    "pytest -k 'egress' tests/unit",
    "cd {tree} && pytest -q",
    "bash -c 'pytest tests/unit tests/api'",
])
def test_broad_pytest_is_blocked_in_a_friday_tree(friday_tree, command):
    tree, _ = friday_tree
    ok, why = decide(bash(command.format(tree=tree), tree), base_cfg())
    assert not ok
    assert "run_suite_guarded.py" in why


@pytest.mark.parametrize("command", [
    "pytest tests/unit/test_x.py -n 0",
    "pytest -q tests/unit/test_x.py::test_a -n 2",
    "python -m pytest tests/unit/test_a.py tests/unit/test_b.py -q -n 1",
    "pytest -n2 -k egress tests/unit/test_egress_gate.py",
    "pytest --numprocesses=2 tests/unit/test_x.py",
    "pytest --numprocesses 0 tests/unit/test_x.py",
    "pytest -p no:xdist tests/unit/test_x.py",
    "pytest -n auto tests/unit/test_x.py -n 2",
    "FRIDAY_TESTING=1 ./venv/Scripts/python.exe -m pytest tests/unit/test_x.py -q -p no:cacheprovider -n 0 2>&1 | tail -1",
    "pytest tests/unit/test_x.py -n 0 > /tmp/out.txt 2>&1",
    "python scripts/run_suite_guarded.py tests/unit tests/api",
    "pytest --collect-only -q",
    "pytest --version",
    "git log --oneline -3",
    "echo pytest is not run here",
])
def test_targeted_pytest_and_the_guard_script_are_allowed(friday_tree, command):
    tree, _ = friday_tree
    ok, why = decide(bash(command, tree), base_cfg())
    assert ok, why


@pytest.mark.parametrize("command", [
    "pytest tests/unit/test_x.py",
    "pytest tests/unit/test_x.py tests/unit/test_y.py -q",
    "pytest -n auto tests/unit/test_x.py",
    "pytest -n 4 tests/unit/test_x.py",
    "pytest -n3 tests/unit/test_x.py",
    "pytest --numprocesses=5 tests/unit/test_x.py",
    "pytest -n logical tests/unit/test_x.py",
    "pytest -n 2 tests/unit/test_x.py -n auto",
    "python -m pytest tests/unit/test_x.py::test_a",
    "cd {tree} && FRIDAY_TESTING=1 ./venv/Scripts/python.exe -m pytest tests/unit/test_a.py tests/unit/test_b.py",
])
def test_a_pytest_call_without_an_explicit_small_worker_count_is_blocked(friday_tree, command):
    tree, _ = friday_tree
    ok, why = decide(bash(command.format(tree=tree), tree), base_cfg())
    assert not ok, command
    assert "-n 2" in why


def test_the_worker_rule_applies_outside_friday_trees_too(friday_tree):
    _, other = friday_tree
    ok, why = decide(bash("pytest tests/test_x.py", other), base_cfg())
    assert not ok and "-n 2" in why
    ok, _ = decide(bash("pytest tests/test_x.py -n 1", other), base_cfg())
    assert ok


def test_a_broad_run_outside_a_friday_tree_only_needs_a_worker_count(friday_tree):
    _, other = friday_tree
    ok, why = decide(bash("pytest", other), base_cfg())
    assert not ok and "-n 2" in why and "Full-suite pytest" not in why
    ok, _ = decide(bash("pytest -n 0", other), base_cfg())
    assert ok


def test_an_older_base_without_the_resource_guard_is_still_a_friday_tree(tmp_path):
    old = tmp_path / "old-base"
    (old / "src" / "agent_friday").mkdir(parents=True)
    (old / "tests" / "unit").mkdir(parents=True)
    ok, why = decide(bash("pytest tests/unit -q", old), base_cfg())
    assert not ok and "run_suite_guarded.py" in why
    files = "tests/unit/test_avatar_voice.py tests/unit/test_calendar_write_accounts.py"
    ok, why = decide(bash(f"pytest {files}", old), base_cfg())
    assert not ok and "-n 2" in why
    ok, why = decide(bash(f"pytest {files} -n 2", old), base_cfg())
    assert ok, why


def test_cd_into_a_friday_tree_counts(friday_tree):
    tree, other = friday_tree
    ok, why = decide(bash(f"cd {tree} && pytest tests", other), base_cfg())
    assert not ok and "run_suite_guarded.py" in why


# ── rule 5: one machine, one memory budget ───────────────────────────────────

OTHER = [(4242, "python -m pytest tests/unit/test_avatar_voice.py -n 2")]


@pytest.mark.parametrize("command", [
    "pytest tests/unit/test_a.py tests/unit/test_b.py -n 2",
    "pytest tests/unit/test_a.py -n 2",
    "pytest tests/unit/test_a.py tests/unit/test_b.py -n 0",
    "python -m pytest tests/unit/test_a.py::test_x -n 1",
])
def test_under_8gb_a_second_run_is_refused_while_another_pytest_runs(friday_tree, command):
    tree, _ = friday_tree
    ok, why = decide(bash(command, tree), base_cfg(), ram=6.5, running=OTHER)
    assert not ok, command
    assert "already run" in why and "pid 4242" in why and "8 GB" in why


@pytest.mark.parametrize("command", [
    "pytest tests/unit/test_a.py -n 0",
    "python -m pytest tests/unit/test_a.py::test_x -n 0",
    "pytest -p no:xdist tests/unit/test_a.py",
])
def test_a_single_file_at_n0_may_proceed_under_8gb_with_others_running(friday_tree, command):
    tree, _ = friday_tree
    ok, why = decide(bash(command, tree), base_cfg(), ram=6.5, running=OTHER)
    assert ok, why


def test_under_8gb_with_no_other_pytest_a_run_proceeds(friday_tree):
    tree, _ = friday_tree
    ok, why = decide(bash("pytest tests/unit/test_a.py tests/unit/test_b.py -n 2", tree), base_cfg(),
                     ram=6.5, running=[])
    assert ok, why


def test_at_or_above_8gb_others_do_not_matter(friday_tree):
    tree, _ = friday_tree
    ok, why = decide(bash("pytest tests/unit/test_a.py tests/unit/test_b.py -n 2", tree), base_cfg(),
                     ram=8.0, running=OTHER)
    assert ok, why


@pytest.mark.parametrize("command", ["pytest tests/unit/test_a.py -n 0", "pytest tests/unit/test_a.py -n 2"])
def test_under_4gb_nothing_runs(friday_tree, command):
    tree, _ = friday_tree
    ok, why = decide(bash(command, tree), base_cfg(), ram=3.9, running=[])
    assert not ok and "4 GB" in why


def test_at_4gb_a_single_file_at_n0_runs(friday_tree):
    tree, _ = friday_tree
    ok, why = decide(bash("pytest tests/unit/test_a.py -n 0", tree), base_cfg(), ram=4.0, running=OTHER)
    assert ok, why


def test_unreadable_memory_or_process_list_refuses_a_multi_file_run(friday_tree):
    tree, _ = friday_tree
    ok, why = decide(bash("pytest tests/unit/test_a.py tests/unit/test_b.py -n 2", tree), base_cfg(),
                     ram=None, running=[])
    assert not ok and "could not be read" in why
    ok, why = decide(bash("pytest tests/unit/test_a.py tests/unit/test_b.py -n 2", tree), base_cfg(),
                     ram=6.5, running=None)
    assert not ok and "process list" in why
    ok, why = decide(bash("pytest tests/unit/test_a.py -n 0", tree), base_cfg(), ram=6.5, running=None)
    assert ok, why


# Two concurrent runs, machine-wide, whatever the memory: eight concurrent
# single-file runs once took the commit charge to 96 %.

TWO_RUNS = [(4242, "python -m pytest tests/unit/test_a.py -n 0"),
            (4243, "python -m pytest tests/unit/test_a.py -n 0"),   # its venv child
            (5151, "python -m pytest tests/api/test_b.py -n 0")]


@pytest.mark.parametrize("ram", [6.5, 50.0])
def test_a_third_run_waits_while_two_already_run(friday_tree, ram):
    tree, _ = friday_tree
    ok, why = decide(bash("pytest tests/unit/test_c.py -n 0", tree), base_cfg(),
                     ram=ram, running=TWO_RUNS)
    assert not ok
    assert "2 pytest runs" in why and "wait" in why.lower()


def test_a_launcher_and_its_child_are_one_run(friday_tree):
    tree, _ = friday_tree
    ok, why = decide(bash("pytest tests/unit/test_c.py -n 0", tree), base_cfg(),
                     ram=50.0, running=TWO_RUNS[:2])
    assert ok, why


def test_the_cap_comes_from_config(friday_tree):
    tree, _ = friday_tree
    cfg = base_cfg()
    assert cfg["pytest_max_concurrent_runs"] == 2
    cfg["pytest_max_concurrent_runs"] = 3
    ok, why = decide(bash("pytest tests/unit/test_c.py -n 0", tree), cfg,
                     ram=50.0, running=TWO_RUNS)
    assert ok, why


def test_the_hook_waits_for_a_slot_before_refusing(monkeypatch):
    """Probed for real, a full machine is re-read until a slot frees or the
    wait (inside the hook's own timeout) runs out."""
    reads = iter([TWO_RUNS, TWO_RUNS, TWO_RUNS[:2]])
    monkeypatch.setattr(g, "running_pytest_processes", lambda: next(reads))
    clock = [0.0]
    monkeypatch.setattr(g.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(g.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    assert g.wait_for_pytest_slot(base_cfg()) == TWO_RUNS[:2]
    reads = iter([TWO_RUNS] * 50)
    monkeypatch.setattr(g, "running_pytest_processes", lambda: next(reads))
    assert g.wait_for_pytest_slot(base_cfg()) == TWO_RUNS


def test_the_floors_come_from_config(friday_tree):
    tree, _ = friday_tree
    cfg = base_cfg()
    cfg["pytest_concurrency_floor_gb"] = 100
    ok, _ = decide(bash("pytest tests/unit/test_a.py tests/unit/test_b.py -n 2", tree), cfg, ram=50, running=OTHER)
    assert not ok


def test_the_process_probe_sees_another_pytest_process_and_not_the_caller():
    marker = "import time; time.sleep(30)  # pytest-probe-target"
    child = subprocess.Popen([sys.executable, "-c", marker])  # python, not a shell wrapper
    try:
        time.sleep(1.0)
        found = g.running_pytest_processes()
        assert found is not None
        pids = {pid for pid, _ in found}
        assert child.pid in pids, found
        assert os.getpid() not in pids, "the calling process is never counted against itself"
        assert all("friday_guard.py" not in cmd for _, cmd in found)
    finally:
        child.kill()
        child.wait(timeout=10)


@pytest.mark.parametrize("cmdline,is_run", [
    ("C:/x/venv/Scripts/python.exe -m pytest tests/unit/test_a.py -n 0", True),
    ("pytest tests/unit/test_a.py", True),
    ("python scripts/run_suite_guarded.py tests/unit tests/api", True),
    ('"C:/Program Files/Git/bin/bash.exe" -c "cd /c/x && python -m pytest tests/unit/test_a.py -n 0"', False),
    ('cmd.exe /c ""C:/x/python.exe" -m pytest test_a.py"', False),
    ("powershell.exe -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'pytest' }\"", False),
    ("python C:/x/.claude/hooks/friday_guard.py", False),
    ("python -c \"import time; time.sleep(30)\"", False),
])
def test_only_real_test_runs_count_as_pytest_processes(cmdline, is_run):
    assert g.is_pytest_process(cmdline) is is_run


def test_the_guarded_runner_counts_as_a_pytest_process(friday_tree):
    tree, _ = friday_tree
    runner = [(99, "python scripts/run_suite_guarded.py tests/unit tests/api")]
    ok, why = decide(bash("pytest tests/unit/test_a.py tests/unit/test_b.py -n 2", tree), base_cfg(),
                     ram=6.5, running=runner)
    assert not ok and "pid 99" in why


# ── rule 2: wsl / docker under the memory floor ──────────────────────────────

@pytest.mark.parametrize("command", [
    "wsl", "wsl -d Ubuntu -- ls", "wsl.exe --exec bash", "docker ps", "docker run -it x",
    "docker compose up -d", "docker-compose up", "docker desktop start",
    "cd /tmp && docker build .", "powershell -Command \"docker ps\"",
    "Start-Process 'C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe'",
])
def test_vm_commands_are_blocked_under_the_floor(command):
    ok, why = decide(bash(command, "/tmp"), base_cfg(), ram=6.7)
    assert not ok
    assert "6.7 GB" in why and "12 GB" in why


@pytest.mark.parametrize("command", ["wsl -d Ubuntu", "docker ps", "docker compose up"])
def test_vm_commands_are_allowed_with_room(command):
    ok, _ = decide(bash(command, "/tmp"), base_cfg(), ram=20.0)
    assert ok


@pytest.mark.parametrize("command", ["wsl --shutdown", "wsl --list", "wsl -l -v", "wsl --status",
                                     "wsl --terminate Ubuntu", "echo docker", "cat docker-notes.md"])
def test_non_booting_wsl_queries_and_mentions_are_allowed_under_the_floor(command):
    ok, why = decide(bash(command, "/tmp"), base_cfg(), ram=6.7)
    assert ok, why


def test_unreadable_memory_blocks_a_vm_start():
    ok, why = decide(bash("wsl", "/tmp"), base_cfg(), ram=None)
    assert not ok and "could not be read" in why


def test_the_floor_comes_from_config():
    cfg = base_cfg()
    cfg["min_free_ram_gb"] = 4.0
    ok, _ = decide(bash("docker ps", "/tmp"), cfg, ram=6.7)
    assert ok


# ── rule 3: the live checkout ────────────────────────────────────────────────

def test_editing_the_live_checkout_is_blocked_and_audited(live):
    ok, why = decide(edit(live["live"] / "src" / "server.py"), live["cfg"])
    assert not ok and "live checkout" in why
    assert "BLOCK" in live["audit"].read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", [".claude/SUITE_LOCK", ".claude/DEPLOY_LANE",
                                 ".claude/worktrees/agent-x/src/server.py", ".claude/settings.local.json"])
def test_the_live_claude_directory_and_its_worktrees_are_exempt(live, rel):
    ok, why = decide(edit(live["live"] / rel), live["cfg"])
    assert ok, why


def test_editing_a_worktree_elsewhere_is_allowed(live):
    ok, _ = decide(edit(live["wt"] / "src" / "server.py"), live["cfg"])
    assert ok


def test_edit_paths_in_any_spelling_are_recognised(live):
    p = str(live["live"] / "AGENTS.md")
    for spelling in (p, p.replace("\\", "/"), p.upper() if sys.platform == "win32" else p):
        ok, _ = decide(edit(spelling), live["cfg"])
        assert not ok, spelling


def test_write_and_notebook_tools_are_covered(live):
    for tool, key in (("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
        payload = {"tool_name": tool, "tool_input": {key: str(live["live"] / "x.py")}, "cwd": "/tmp"}
        ok, _ = decide(payload, live["cfg"])
        assert not ok, tool


@pytest.mark.parametrize("command", [
    "git checkout main", "git switch -c x", "git reset --hard HEAD~1", "git merge --ff-only x",
    "git pull", "git stash", "git stash pop", "git clean -fd", "git restore .", "git commit -m x",
    "git add -A", "sed -i 's/a/b/' AGENTS.md", "echo hi > notes.txt", "cat x >> AGENTS.md",
    "rm AGENTS.md", "touch new.txt", "cp /tmp/x src/server.py", "mv a b", "tee AGENTS.md",
    "python server.py", "./venv/Scripts/python.exe -m agent_friday.server",
    "python -m agent_friday.friday_tray", "python -c \"open('x.txt','w').write('1')\"",
    "perl -i -pe 's/a/b/' README.md",
])
def test_mutations_with_the_live_checkout_as_cwd_are_blocked(live, command):
    ok, why = decide(bash(command, live["live"]), live["cfg"])
    assert not ok, command
    assert "live checkout" in why


@pytest.mark.parametrize("command", [
    "git status", "git log --oneline -5", "git diff", "git worktree add ../x -b x", "git fetch",
    "git stash list", "git branch --show-current", "cat AGENTS.md", "grep -rn foo src",
    "git log > /tmp/log.txt", "pytest tests/unit/test_x.py -n 0", "ls -la", "cp AGENTS.md /tmp/copy.md",
    "sed 's/a/b/' AGENTS.md", "python -c \"print(open('AGENTS.md').read())\"",
    "echo 'deploy-2b' > .claude/DEPLOY_LANE", "git worktree remove /tmp/x",
    "echo \"main -> branch is a clean fast-forward\"", "cat > \"$SCRATCH/notes.md\" <<'EOF'\nbody\nEOF",
    "ls .claude/DEPLOY_LANE 2>&1 | sed 's/^/x: /'", "git log -1 2>/dev/null",
    "S=/tmp/x; mkdir -p \"$S\" && cd \"$S\" && git init -q . && git commit -q --allow-empty -m probe",
    "rm \"$SCRATCH/old.log\"", "git -C \"$WT\" checkout -b x", "cp AGENTS.md \"$OUT/copy.md\"",
    "cd $HOME/ftv/std && git checkout -b x",
])
def test_reads_and_worktree_creation_in_the_live_checkout_are_allowed(live, command):
    ok, why = decide(bash(command, live["live"]), live["cfg"])
    assert ok, (command, why)


def test_the_live_checkout_is_reachable_by_cd_and_by_git_dash_c(live):
    L = str(live["live"])
    for command in (f"cd {L} && git checkout main", f"git -C {L} merge --ff-only x",
                    f"git -C \"{L}\" reset --hard", f"cd {L}; python server.py",
                    f"echo x > {L}/notes.txt", f"sed -i s/a/b/ {L}/AGENTS.md"):
        ok, _ = decide(bash(command, live["wt"]), live["cfg"])
        assert not ok, command


def test_a_test_file_named_like_a_server_is_not_a_server_launch(live):
    """The salon session's case: the live venv is shared by every worktree, and
    a test file's name is an argument, not what the command runs."""
    venv_py = str(live["live"] / "venv" / "Scripts" / "python.exe")
    ok, why = decide(bash(f"{venv_py} -m pytest tests/unit/test_published_server.py -n 0", live["wt"]), live["cfg"])
    assert ok, why
    ok, why = decide(bash(f"FRIDAY_TESTING=1 {venv_py} -m pytest tests/unit/test_published_server.py tests/unit/test_server_routes.py -n 2", live["wt"]), live["cfg"])
    assert ok, why
    ok, why = decide(bash(f"{venv_py} -c \"print('server.py')\"", live["wt"]), live["cfg"])
    assert ok, why
    ok, why = decide(bash("cat docs/server.py.md && grep friday_tray README.md", live["wt"]), live["cfg"])
    assert ok, why


def test_a_server_launch_with_the_live_interpreter_is_refused_from_a_worktree(live):
    venv_py = str(live["live"] / "venv" / "Scripts" / "python.exe")
    for command in (f"{venv_py} server.py", f"{venv_py} -m agent_friday.server", f"{venv_py} -m agent_friday.friday_tray",
                    f"nohup {venv_py} server.py --port 3000", f"{venv_py} {live['live']}/server.py"):
        ok, why = decide(bash(command, live["wt"]), live["cfg"])
        assert not ok, command
        assert "server launch" in why


def test_a_server_launch_from_the_live_tree_is_refused_with_any_interpreter(live):
    for command in ("python server.py", "py -3 -m agent_friday.server", "flask run", "waitress-serve app:app",
                    "Start-Process python -ArgumentList server.py"):
        ok, why = decide(bash(command, live["live"]), live["cfg"])
        assert not ok, command


def test_the_same_mutations_in_a_worktree_are_allowed(live):
    for command in ("git checkout -b x", "git reset --hard", "git merge main", "python server.py",
                    "sed -i s/a/b/ AGENTS.md", "echo x > notes.txt", "git commit -m x"):
        ok, why = decide(bash(command, live["wt"]), live["cfg"])
        assert ok, (command, why)


def test_powershell_mutations_are_covered(live):
    L = str(live["live"])
    for command in (f"Set-Location '{L}'; git checkout main",
                    f"Set-Content -Path '{L}\\x.txt' -Value 1",
                    f"Remove-Item {L}\\AGENTS.md",
                    f"cd {L}; Start-Process python -ArgumentList server.py"):
        ok, _ = decide(pwsh(command, live["wt"]), live["cfg"])
        assert not ok, command
    ok, why = decide(pwsh(f"Set-Location '{L}'; git status", live["wt"]), live["cfg"])
    assert ok, why


def test_the_deploy_lane_token_is_the_one_bypass_and_it_is_audited(live):
    token = Path(live["cfg"]["deploy_lane_token"])
    token.write_text("deploy-2b by the program lead\n", encoding="utf-8")
    ok, _ = decide(bash("git merge --ff-only x", live["live"]), live["cfg"])
    assert ok
    ok, _ = decide(edit(live["live"] / "AGENTS.md"), live["cfg"])
    assert ok
    log = live["audit"].read_text(encoding="utf-8")
    assert log.count("BYPASS") == 2 and "deploy-2b" in log


def test_an_expired_or_empty_token_does_not_bypass(live):
    token = Path(live["cfg"]["deploy_lane_token"])
    token.write_text("deploy\n", encoding="utf-8")
    old = time.time() - 5 * 3600
    os.utime(token, (old, old))
    ok, _ = decide(bash("git checkout main", live["live"]), live["cfg"])
    assert not ok
    token.write_text("", encoding="utf-8")
    ok, _ = decide(bash("git checkout main", live["live"]), live["cfg"])
    assert not ok


def test_without_a_live_checkout_configured_rule_3_is_inactive(live):
    cfg = dict(live["cfg"], live_checkout=None)
    ok, _ = decide(bash("git checkout main", live["live"]), cfg)
    assert ok


# ── rule 4: history rewrites ─────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "git push --force origin main", "git push -f", "git push --force-with-lease origin x",
    "git push -fu origin x", "git push origin +main", "git push origin :old-branch",
    "git push --delete origin x", "git push --mirror backup", "git commit --amend --no-edit",
    "git filter-branch --all", "git filter-repo --path x", "git replace a b",
    "git reflog expire --expire=now --all", "cd /tmp && git push -f",
    "powershell -Command \"git push --force origin main\"", "bash -c 'git push -f'",
])
def test_history_rewrites_are_blocked_everywhere(command):
    ok, why = decide(bash(command, "/tmp"), base_cfg())
    assert not ok, command
    assert "blocked" in why


@pytest.mark.parametrize("command", [
    "git push origin feat/x", "git push -u origin feat/x", "git push",
    "git commit -m 'force-push is blocked by the guard'", "git rebase main",
    "git log --format=%H", "git reflog", "git commit -m 'x' --no-verify",
    "git push --dry-run origin x",
])
def test_ordinary_pushes_and_commits_are_allowed(command):
    ok, why = decide(bash(command, "/tmp"), base_cfg())
    assert ok, (command, why)


def test_a_heredoc_body_cannot_trip_the_rules():
    command = "git commit -F - <<'EOF'\nfix: git push --force is blocked\n\nwsl and docker too\nEOF\n"
    ok, why = decide(bash(command, "/tmp"), base_cfg(), ram=1.0)
    assert ok, why


# ── the exit-code contract ───────────────────────────────────────────────────

def _run_hook(payload, config_path):
    env = dict(os.environ, FRIDAY_GUARD_CONFIG=str(config_path))
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True,
                          text=True, env=env, timeout=60)


def test_the_hook_exits_2_with_the_reason_on_stderr_and_0_when_allowed(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}", encoding="utf-8")
    blocked = _run_hook(bash("git push --force origin main", "/tmp"), cfg_path)
    assert blocked.returncode == 2
    assert "force" in blocked.stderr
    allowed = _run_hook(bash("git status", "/tmp"), cfg_path)
    assert allowed.returncode == 0 and allowed.stderr == ""


def test_a_malformed_payload_lets_the_call_through_loudly(tmp_path):
    env = dict(os.environ, FRIDAY_GUARD_CONFIG=str(tmp_path / "none.json"))
    p = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True,
                       env=env, timeout=60)
    assert p.returncode == 1 and "internal error" in p.stderr


def test_the_private_config_is_read_from_the_env_path(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    live = tmp_path / "live"
    live.mkdir()
    cfg_path.write_text(json.dumps({"live_checkout": str(live), "audit_log": str(tmp_path / "a.log")}), encoding="utf-8")
    p = _run_hook(bash("git checkout main", live), cfg_path)
    assert p.returncode == 2 and "live checkout" in p.stderr
    assert "BLOCK" in (tmp_path / "a.log").read_text(encoding="utf-8")


# ── while the lane's suite holds SUITE_LOCK ─────────────────────────────────

@pytest.fixture
def locked(live):
    lock = live["live"] / ".claude" / "SUITE_LOCK"
    lock.write_text("holder: program-lead-lane-123\nsession: program-lead\n", encoding="utf-8")
    cfg = dict(live["cfg"])
    cfg["suite_lock"] = str(lock)
    return {**live, "lock": lock, "cfg": cfg}


@pytest.mark.parametrize("command", [
    "python -m pytest tests/unit/test_a.py tests/unit/test_b.py -n 0 -q",
    "python -m pytest tests/unit -n 0 -q",
    "python -m pytest tests/unit/test_a.py -n 1 -q",
])
def test_a_suite_lock_refuses_anything_but_one_file_at_n0(locked, command):
    ok, why = decide(bash(command, locked["wt"]), locked["cfg"])
    assert not ok
    assert "SUITE_LOCK" in why and "program-lead-lane-123" in why
    assert "wait" in why.lower() and "orchestrator" in why.lower()


def test_a_suite_lock_still_allows_one_named_file_at_n0(locked):
    ok, why = decide(bash("python -m pytest tests/unit/test_a.py -n 0 -q", locked["wt"]), locked["cfg"])
    assert ok, why


def test_without_the_lock_a_multi_file_run_is_judged_as_before(live):
    cfg = dict(live["cfg"])
    cfg["suite_lock"] = str(live["live"] / ".claude" / "SUITE_LOCK")   # absent
    ok, why = decide(bash("python -m pytest tests/unit/test_a.py tests/unit/test_b.py -n 0 -q", live["wt"]), cfg)
    assert ok, why


def test_the_lock_path_defaults_to_the_live_checkout(tmp_path):
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps({"live_checkout": str(tmp_path / "live")}), encoding="utf-8")
    cfg = g.load_config(cfg_path)
    assert cfg["suite_lock"].replace("\\", "/").endswith("live/.claude/SUITE_LOCK")


@pytest.mark.parametrize("wrapper", [
    '"C:/Program Files/Git/usr/bin/timeout.exe" 1200 ../venv/Scripts/python.exe -m pytest tests/api/test_b.py -n 0',
    "timeout 300 python -m pytest tests/api/test_b.py -n 0",
    "env FRIDAY_TESTING=1 python -m pytest tests/api/test_b.py -n 0",
])
def test_a_command_wrapper_around_pytest_is_not_a_run_of_its_own(wrapper):
    assert not g.is_pytest_process(wrapper)
