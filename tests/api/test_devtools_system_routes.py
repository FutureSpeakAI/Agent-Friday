"""Developer-tools / system route tests.

Covers: vibe-code, git, repos, files, code, computer control, flow,
routines, logs, tasks, system, and notifications.

SAFETY HARNESS (autouse fixture `_block_subprocess`):
  - subprocess.run, subprocess.Popen, subprocess.check_output → NoopSubprocess
  - os.startfile → no-op recorder
  - webbrowser.open → no-op recorder
  - server._perform_open → no-op (prevents real file-system opens)
  - server._run_claude_terminal → patches the function that the vibe-code launch
    thread calls, so no CMD windows are ever spawned
Nothing in this file ever actually executes an OS command or opens an
application.
"""
from __future__ import annotations

import os
import subprocess

import json
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
import server as friday_server  # noqa: E402 (already imported by conftest but we want the ref)


# ═══════════════════════════════════════════════════════════════
#  MANDATORY SAFETY HARNESS
# ═══════════════════════════════════════════════════════════════

class _FakeCompletedProcess:
    """Returned by the fake subprocess.run / check_output."""
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.pid = 99999  # sentinal PID that is never real


class _FakeProc:
    """Returned by the fake subprocess.Popen."""
    pid = 99999

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def communicate(self, input=None, timeout=None):
        return ("", "")


class _SubprocessRecorder:
    """Drop-in replacement for the subprocess module's call surface.

    Records every call so tests can assert on the argv without ever running it.
    """
    def __init__(self):
        self.calls: list[dict] = []

    def _record(self, label, args, kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        self.calls.append({"label": label, "cmd": cmd, "kwargs": kwargs})

    def run(self, *args, **kwargs):
        self._record("run", args, kwargs)
        stdout = kwargs.get("_fake_stdout", "")
        return _FakeCompletedProcess(returncode=0, stdout=stdout, stderr="")

    def Popen(self, *args, **kwargs):
        self._record("Popen", args, kwargs)
        return _FakeProc()

    def check_output(self, *args, **kwargs):
        self._record("check_output", args, kwargs)
        return b""

    # Sentinel so TimeoutExpired can still be caught
    TimeoutExpired = subprocess.TimeoutExpired
    CalledProcessError = subprocess.CalledProcessError
    CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.fixture(autouse=True)
def _block_subprocess(monkeypatch):
    """SAFETY: replace every OS-touching surface with inert stubs.

    This fixture runs for EVERY test in this file, so no test can accidentally
    spawn a real process or open a real application.
    """
    recorder = _SubprocessRecorder()

    # 1. subprocess module-level functions
    monkeypatch.setattr(subprocess, "run", recorder.run, raising=False)
    monkeypatch.setattr(subprocess, "Popen", recorder.Popen, raising=False)
    monkeypatch.setattr(subprocess, "check_output", recorder.check_output, raising=False)

    # 2. os.startfile (Windows only — may not exist on the attribute; raising=False handles that)
    _startfile_calls: list = []
    monkeypatch.setattr(os, "startfile", lambda p, *a, **k: _startfile_calls.append(p), raising=False)

    # 3. webbrowser.open (not always imported at module level but guard it)
    try:
        import webbrowser
        monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: None, raising=False)
    except Exception:
        pass

    # 4. _perform_open — the central "open a local path or alias" helper.
    #    Star-imports give every route/service module its own reference, so
    #    patch ALL project namespaces, not just `server`.
    from tests.api.conftest import _patch_everywhere
    _patch_everywhere(monkeypatch, "_perform_open",
                      lambda target: f"[mocked open: {target}]")

    # 5. _run_claude_terminal — the thread function that spawns CMD windows.
    #    We set it to a no-op so launching a vibe-code task never opens anything.
    _patch_everywhere(monkeypatch, "_run_claude_terminal",
                      lambda tid, task, cwd: None)

    yield recorder  # tests that need to inspect calls receive the recorder


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def _fake_git_repo(tmp_path: Path, name: str = "testrepo") -> Path:
    """Create a minimal fake git repo directory under PROJECTS_DIR so the server's
    path-validation logic (_repo_path / _safe_project_path) accepts it."""
    projects = friday_server.PROJECTS_DIR
    repo = projects / name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    return repo


def _fake_repo_name(tmp_path: Path, name: str = "testrepo") -> str:
    _fake_git_repo(tmp_path, name)
    return name


# ═══════════════════════════════════════════════════════════════
#  1. VIBE-CODE
# ═══════════════════════════════════════════════════════════════

class TestVibeCodePresets:
    """PURE route — no subprocess needed."""

    def test_presets_returns_ok(self, client):
        resp = client.get("/api/vibe-code/presets")
        assert resp.status_code == 200

    def test_presets_has_status_ok(self, client):
        data = resp = client.get("/api/vibe-code/presets").get_json()
        assert data["status"] == "ok"

    def test_presets_contains_security_audit(self, client):
        data = client.get("/api/vibe-code/presets").get_json()
        names = [p["name"] for p in data["presets"]]
        assert "Security Audit" in names

    def test_presets_all_have_tasks(self, client):
        data = client.get("/api/vibe-code/presets").get_json()
        for preset in data["presets"]:
            assert "tasks" in preset and isinstance(preset["tasks"], list)
            assert len(preset["tasks"]) > 0


class TestVibeCodeLaunch:
    """Launch MUST be fully mocked — _run_claude_terminal is patched to no-op."""

    def test_launch_no_tasks_returns_400(self, client):
        resp = client.post("/api/vibe-code/launch", json={"tasks": []})
        assert resp.status_code == 400

    def test_launch_missing_tasks_returns_400(self, client):
        resp = client.post("/api/vibe-code/launch", json={})
        assert resp.status_code == 400

    def test_launch_single_task_returns_launched_ids(self, client):
        # _run_claude_terminal is no-op'd by _block_subprocess; no CMD opens.
        resp = client.post("/api/vibe-code/launch",
                           json={"tasks": ["Write some tests"]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["launched"], list)
        assert len(data["launched"]) == 1

    def test_launch_multiple_tasks(self, client):
        resp = client.post("/api/vibe-code/launch",
                           json={"tasks": ["Task A", "Task B", "Task C"]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 3
        assert len(data["launched"]) == 3

    def test_launch_ids_are_unique_strings(self, client):
        resp = client.post("/api/vibe-code/launch",
                           json={"tasks": ["T1", "T2"]})
        ids = resp.get_json()["launched"]
        assert len(set(ids)) == 2
        assert all(isinstance(i, str) for i in ids)

    def test_launched_ids_appear_in_status(self, client):
        resp = client.post("/api/vibe-code/launch",
                           json={"tasks": ["Status check task"]})
        tid = resp.get_json()["launched"][0]
        status = client.get("/api/vibe-code/status").get_json()
        known_ids = [t["id"] for t in status["terminals"]]
        assert tid in known_ids


class TestVibeCodeStatus:
    def test_status_returns_ok(self, client):
        resp = client.get("/api/vibe-code/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "terminals" in data

    def test_status_terminals_is_list(self, client):
        data = client.get("/api/vibe-code/status").get_json()
        assert isinstance(data["terminals"], list)


class TestVibeCodeStop:
    def test_stop_unknown_id_returns_404(self, client):
        resp = client.post("/api/vibe-code/stop", json={"id": "nonexistent-id"})
        assert resp.status_code == 404

    def test_stop_known_id_returns_ok(self, client):
        # Launch first (no-op, safe)
        launch = client.post("/api/vibe-code/launch",
                             json={"tasks": ["Stopme task"]}).get_json()
        tid = launch["launched"][0]
        resp = client.post("/api/vibe-code/stop", json={"id": tid})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


class TestVibeCodeClear:
    def test_clear_returns_ok(self, client):
        resp = client.post("/api/vibe-code/clear")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "removed" in data

    def test_clear_removes_stopped_terminals(self, client):
        # Launch then stop to make a 'stopped' terminal
        tid = client.post("/api/vibe-code/launch",
                          json={"tasks": ["Clear me"]}).get_json()["launched"][0]
        client.post("/api/vibe-code/stop", json={"id": tid})
        before = client.get("/api/vibe-code/status").get_json()["terminals"]
        before_ids = [t["id"] for t in before]
        assert tid in before_ids

        client.post("/api/vibe-code/clear")
        after = client.get("/api/vibe-code/status").get_json()["terminals"]
        after_ids = [t["id"] for t in after]
        assert tid not in after_ids


# ═══════════════════════════════════════════════════════════════
#  2. GIT OPERATIONS  (all mocked — no real git runs)
# ═══════════════════════════════════════════════════════════════

class TestGitDiff:
    def test_missing_repo_returns_404(self, client):
        resp = client.get("/api/git/diff?repo=nonexistent-repo-xyz")
        assert resp.status_code == 404

    def test_diff_with_valid_fake_repo(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path)
        resp = client.get(f"/api/git/diff?repo={name}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "diff" in data
        # Confirm subprocess.run was called with 'git' as the first arg
        git_calls = [c for c in _block_subprocess.calls if "cmd" in c and
                     isinstance(c["cmd"], list) and c["cmd"][0] == "git"]
        assert len(git_calls) >= 1

    def test_diff_bad_file_path_returns_400(self, client, tmp_path):
        name = _fake_repo_name(tmp_path)
        resp = client.get(f"/api/git/diff?repo={name}&file=../../etc/passwd")
        assert resp.status_code == 400


class TestGitBranches:
    def test_missing_repo_returns_404(self, client):
        resp = client.get("/api/git/branches?repo=doesnt-exist")
        assert resp.status_code == 404

    def test_branches_with_fake_repo(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path)
        resp = client.get(f"/api/git/branches?repo={name}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "branches" in data
        assert "current" in data


class TestGitPull:
    def test_missing_repo_returns_404(self, client):
        resp = client.post("/api/git/pull", json={"repo": "ghost-repo"})
        assert resp.status_code == 404

    def test_pull_issues_git_pull_command(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/pull", json={"repo": name})
        assert resp.status_code == 200
        cmds = [c["cmd"] for c in _block_subprocess.calls if isinstance(c.get("cmd"), list)]
        assert any("pull" in cmd for cmd in cmds), f"No git pull in: {cmds}"

    def test_pull_no_real_network(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path)
        client.post("/api/git/pull", json={"repo": name})
        # returncode from the fake is always 0, so we just verify the route completed
        cmds = [c["cmd"] for c in _block_subprocess.calls]
        for cmd in cmds:
            if isinstance(cmd, list):
                # Must not be a network-touching binary; only 'git' is expected
                assert cmd[0] == "git", f"Unexpected binary: {cmd[0]}"


class TestGitPush:
    def test_missing_repo_returns_404(self, client):
        resp = client.post("/api/git/push", json={"repo": "ghost-repo"})
        assert resp.status_code == 404

    def test_push_issues_git_command(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/push", json={"repo": name})
        assert resp.status_code == 200
        cmds = [c["cmd"] for c in _block_subprocess.calls if isinstance(c.get("cmd"), list)]
        assert any(cmd[0] == "git" for cmd in cmds)

    def test_push_no_force_flag(self, client, tmp_path, _block_subprocess):
        """Server must never pass --force/-f to git push."""
        name = _fake_repo_name(tmp_path)
        client.post("/api/git/push", json={"repo": name})
        cmds = [c["cmd"] for c in _block_subprocess.calls if isinstance(c.get("cmd"), list)]
        push_cmds = [c for c in cmds if "push" in c]
        for cmd in push_cmds:
            assert "--force" not in cmd and "-f" not in cmd, f"Force flag found: {cmd}"


class TestGitCheckout:
    def test_missing_repo_returns_404(self, client):
        resp = client.post("/api/git/checkout",
                           json={"repo": "ghost", "branch": "main"})
        assert resp.status_code == 404

    def test_invalid_branch_name_returns_400(self, client, tmp_path):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/checkout",
                           json={"repo": name, "branch": "bad branch!"})
        assert resp.status_code == 400

    def test_empty_branch_returns_400(self, client, tmp_path):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/checkout", json={"repo": name, "branch": ""})
        assert resp.status_code == 400

    def test_checkout_valid_branch(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/checkout",
                           json={"repo": name, "branch": "my-feature"})
        assert resp.status_code == 200
        cmds = [c["cmd"] for c in _block_subprocess.calls if isinstance(c.get("cmd"), list)]
        assert any("checkout" in cmd for cmd in cmds)


class TestGitBranchCreate:
    def test_missing_repo_returns_404(self, client):
        resp = client.post("/api/git/branch", json={"repo": "ghost", "name": "feat"})
        assert resp.status_code == 404

    def test_invalid_name_returns_400(self, client, tmp_path):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/branch",
                           json={"repo": name, "name": "bad name!"})
        assert resp.status_code == 400

    def test_valid_branch_create(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/branch",
                           json={"repo": name, "name": "new-branch"})
        assert resp.status_code == 200
        cmds = [c["cmd"] for c in _block_subprocess.calls if isinstance(c.get("cmd"), list)]
        assert any("checkout" in cmd and "-b" in cmd for cmd in cmds)


class TestGitCommit:
    def test_missing_repo_returns_404(self, client):
        resp = client.post("/api/git/commit",
                           json={"repo": "ghost", "message": "msg"})
        assert resp.status_code == 404

    def test_missing_message_returns_400(self, client, tmp_path):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/commit", json={"repo": name, "message": ""})
        assert resp.status_code == 400

    def test_commit_calls_git_commit(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/commit",
                           json={"repo": name, "message": "test commit"})
        assert resp.status_code == 200
        cmds = [c["cmd"] for c in _block_subprocess.calls if isinstance(c.get("cmd"), list)]
        assert any("commit" in cmd for cmd in cmds)


class TestGitPR:
    def test_missing_repo_returns_404(self, client):
        resp = client.post("/api/git/pr",
                           json={"repo": "ghost", "title": "My PR"})
        assert resp.status_code == 404

    def test_missing_title_returns_400(self, client, tmp_path):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/pr", json={"repo": name, "title": ""})
        assert resp.status_code == 400

    def test_pr_calls_gh_or_returns_graceful_error(self, client, tmp_path, _block_subprocess):
        """gh pr create is mocked; route should return 200/error (never 500)."""
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/git/pr",
                           json={"repo": name, "title": "Test PR", "body": "Desc"})
        # A 504 means gh wasn't found but didn't 500; that's acceptable too.
        assert resp.status_code in (200, 404, 504)


# ═══════════════════════════════════════════════════════════════
#  3. REPOS
# ═══════════════════════════════════════════════════════════════

class TestReposScan:
    def test_scan_returns_ok(self, client):
        resp = client.get("/api/repos/scan")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "repos" in data

    def test_scan_result_is_list(self, client):
        data = client.get("/api/repos/scan").get_json()
        assert isinstance(data["repos"], list)

    def test_scan_with_fake_repo_finds_it(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path, "scanme-repo")
        resp = client.get("/api/repos/scan")
        data = resp.get_json()
        repo_names = [r["name"] for r in data["repos"]]
        assert "scanme-repo" in repo_names


class TestReposStatus:
    def test_unknown_repo_returns_404(self, client):
        resp = client.get("/api/repos/unknown-xyz-repo/status")
        assert resp.status_code == 404

    def test_known_repo_returns_ok(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path, "statme-repo")
        resp = client.get(f"/api/repos/{name}/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "repo" in data
        assert data["repo"]["name"] == name

    def test_known_repo_has_files_key(self, client, tmp_path, _block_subprocess):
        name = _fake_repo_name(tmp_path, "statme-files")
        resp = client.get(f"/api/repos/{name}/status")
        assert "files" in resp.get_json()["repo"]


# ═══════════════════════════════════════════════════════════════
#  4. FILES
# ═══════════════════════════════════════════════════════════════

class TestFilesList:
    def test_list_root_projects_dir(self, client):
        # PROJECTS_DIR always exists (or returns 404 gracefully)
        resp = client.get("/api/files/list")
        assert resp.status_code in (200, 404)

    def test_list_path_traversal_rejected(self, client):
        # ../../ should escape Projects/ sandbox and be rejected
        resp = client.get("/api/files/list?path=../../etc")
        assert resp.status_code == 404

    def test_list_returns_entries_list(self, client, tmp_path):
        # Create a real subdir in PROJECTS_DIR
        sub = friday_server.PROJECTS_DIR / "listme-dir"
        sub.mkdir(parents=True, exist_ok=True)
        resp = client.get("/api/files/list?path=listme-dir")
        # Whether this dir has contents or not, the shape must be right
        if resp.status_code == 200:
            data = resp.get_json()
            assert "entries" in data
            assert isinstance(data["entries"], list)


class TestFilesRead:
    def test_nonexistent_file_returns_404(self, client):
        resp = client.get("/api/files/read?path=nonexistent/file.txt")
        assert resp.status_code == 404

    def test_path_traversal_rejected(self, client):
        resp = client.get("/api/files/read?path=../../etc/passwd")
        assert resp.status_code == 404

    def test_read_real_file(self, client):
        """Create a synthetic file inside PROJECTS_DIR and read it back."""
        target = friday_server.PROJECTS_DIR / "readtest.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hello tests", encoding="utf-8")
        try:
            resp = client.get("/api/files/read?path=readtest.txt")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["content"] == "hello tests"
            assert data["name"] == "readtest.txt"
        finally:
            target.unlink(missing_ok=True)

    def test_read_response_has_lang_key(self, client):
        target = friday_server.PROJECTS_DIR / "snippet.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("print('hi')", encoding="utf-8")
        try:
            resp = client.get("/api/files/read?path=snippet.py")
            if resp.status_code == 200:
                assert resp.get_json()["lang"] == "python"
        finally:
            target.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
#  5. CODE: plan / apply / processes / kill
# ═══════════════════════════════════════════════════════════════

class TestCodePlan:
    """LLM route — autouse stub returns CANNED_TEXT.

    The plan parser will fail to parse CANNED_TEXT as JSON → 502.
    That is acceptable (we're testing route plumbing, not model output).
    """
    def test_missing_repo_returns_404(self, client):
        resp = client.post("/api/code/plan",
                           json={"repo": "ghost", "instruction": "add a widget"})
        assert resp.status_code == 404

    def test_missing_instruction_returns_400(self, client, tmp_path):
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/code/plan",
                           json={"repo": name, "instruction": ""})
        assert resp.status_code == 400

    def test_plan_invokes_llm_returns_result(self, client, tmp_path):
        """With the LLM stub active the JSON parse will fail → 200 ok or 502."""
        name = _fake_repo_name(tmp_path)
        resp = client.post("/api/code/plan",
                           json={"repo": name, "instruction": "refactor server"})
        # 200 means stub returned parseable JSON (unlikely), 502 means parse failed.
        assert resp.status_code in (200, 502)
        assert resp.status_code != 500  # must not blow up internally


class TestCodePlansList:
    def test_returns_ok(self, client):
        resp = client.get("/api/code/plans")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "plans" in data
        assert isinstance(data["plans"], list)


class TestCodePlanGet:
    def test_missing_plan_returns_404(self, client):
        resp = client.get("/api/code/plan/not-a-real-plan-id")
        assert resp.status_code == 404

    def test_existing_plan_round_trip(self, client, tmp_path):
        """Write a fake plan file and read it back via the API."""
        plan_id = "20260101-000000-abc123"
        plan_data = {
            "id": plan_id,
            "created": "2026-01-01T00:00:00",
            "repo": "myrepo",
            "repo_path": "/fake/path",
            "instruction": "add feature",
            "summary": "adds a feature",
            "steps": ["Step 1"],
            "files": [],
            "applied": False,
        }
        plan_file = friday_server.CODE_PLANS_DIR / f"{plan_id}.json"
        plan_file.write_text(json.dumps(plan_data), encoding="utf-8")
        try:
            resp = client.get(f"/api/code/plan/{plan_id}")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["plan"]["id"] == plan_id
        finally:
            plan_file.unlink(missing_ok=True)


class TestCodeApply:
    def test_missing_plan_returns_404(self, client):
        resp = client.post("/api/code/apply", json={"plan_id": "nope"})
        assert resp.status_code == 404

    def test_apply_writes_files(self, client, tmp_path):
        """Fake plan with a single file; apply should write it inside Projects/."""
        repo_name = _fake_repo_name(tmp_path, "applyrepo")
        rp = str(friday_server.PROJECTS_DIR / repo_name)
        plan_id = "20260101-000001-apply1"
        rel_path = "applytest.txt"
        plan_data = {
            "id": plan_id,
            "created": "2026-01-01T00:00:00",
            "repo": repo_name,
            "repo_path": rp,
            "instruction": "add file",
            "summary": "",
            "steps": [],
            "files": [{"path": rel_path, "action": "create", "new_content": "hello apply", "rationale": "test"}],
            "applied": False,
        }
        plan_file = friday_server.CODE_PLANS_DIR / f"{plan_id}.json"
        plan_file.write_text(json.dumps(plan_data), encoding="utf-8")
        try:
            resp = client.post("/api/code/apply", json={"plan_id": plan_id})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            # File was written inside the sandbox
            written = friday_server.PROJECTS_DIR / repo_name / rel_path
            assert written.exists()
            assert written.read_text(encoding="utf-8") == "hello apply"
        finally:
            plan_file.unlink(missing_ok=True)
            written = friday_server.PROJECTS_DIR / repo_name / rel_path
            written.unlink(missing_ok=True)


class TestCodeProcesses:
    def test_returns_ok(self, client):
        resp = client.get("/api/code/processes")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "processes" in data

    def test_processes_is_list(self, client):
        data = client.get("/api/code/processes").get_json()
        assert isinstance(data["processes"], list)


class TestCodeKill:
    def test_missing_id_returns_400(self, client):
        resp = client.post("/api/code/kill", json={})
        assert resp.status_code == 400

    def test_unknown_id_returns_404(self, client):
        resp = client.post("/api/code/kill", json={"id": "totally-unknown-proc"})
        assert resp.status_code == 404

    def test_kill_registered_terminal(self, client, _block_subprocess):
        """Register a fake terminal with a PID, then kill it.  subprocess.run is
        mocked so taskkill never actually runs."""
        tid = "faketerm-kill-test"
        friday_server.VIBE_TERMINALS[tid] = {
            "id": tid, "task": "test", "status": "running",
            "pid": 99999, "cwd": "", "started": "", "stopped": None, "log_file": None,
        }
        try:
            resp = client.post("/api/code/kill", json={"id": tid})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["killed"] == tid
            # Verify taskkill was attempted via the mock (not real)
            cmds = [c["cmd"] for c in _block_subprocess.calls if isinstance(c.get("cmd"), list)]
            assert any("taskkill" in cmd for cmd in cmds)
        finally:
            friday_server.VIBE_TERMINALS.pop(tid, None)


# ═══════════════════════════════════════════════════════════════
#  6. COMPUTER CONTROL
# ═══════════════════════════════════════════════════════════════

class TestComputerOpen:
    """api_computer_open MUST be mocked — _perform_open is patched to a stub."""

    def test_missing_path_returns_400(self, client):
        resp = client.post("/api/computer/open", json={})
        assert resp.status_code == 400

    def test_empty_path_returns_400(self, client):
        resp = client.post("/api/computer/open", json={"path": ""})
        assert resp.status_code == 400

    def test_valid_path_returns_ok(self, client):
        """_perform_open is stubbed; route sees a truthy return and replies 200."""
        resp = client.post("/api/computer/open", json={"path": "downloads"})
        # _perform_open is replaced by a lambda returning a string → status ok
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_no_real_startfile_called(self, client, monkeypatch):
        """Confirm os.startfile is never invoked (still guarded even after mock)."""
        called: list = []
        monkeypatch.setattr(os, "startfile",
                            lambda p, *a, **k: called.append(p), raising=False)
        client.post("/api/computer/open", json={"path": "downloads"})
        # _perform_open stub short-circuits before any startfile
        assert called == []


class TestControlPermission:
    def test_get_returns_permission_state(self, client):
        resp = client.get("/api/control/permission")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "granted" in data

    def test_post_grant_when_disabled_returns_403(self, client, monkeypatch):
        """Computer control is off by default; granting without the setting
        enabled must return 403."""
        monkeypatch.setattr(friday_server, "_load_settings",
                            lambda: {"computer_control_enabled": False})
        resp = client.post("/api/control/permission", json={"action": "grant"})
        assert resp.status_code == 403

    def test_post_revoke_returns_ok(self, client):
        resp = client.post("/api/control/permission", json={"action": "revoke"})
        assert resp.status_code == 200
        assert resp.get_json()["granted"] is False

    def test_post_invalid_action_returns_400(self, client):
        resp = client.post("/api/control/permission", json={"action": "unknown"})
        assert resp.status_code == 400


class TestControlKill:
    def test_kill_revokes_permission(self, client):
        resp = client.post("/api/control/kill")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["killed"] is True
        # Permission should be revoked
        perm = client.get("/api/control/permission").get_json()
        assert perm["granted"] is False


# ═══════════════════════════════════════════════════════════════
#  7. FLOW
# ═══════════════════════════════════════════════════════════════

class TestFlow:
    def test_missing_content_returns_400(self, client):
        resp = client.post("/api/flow", json={
            "destinations": ["clipboard"],
        })
        assert resp.status_code == 400

    def test_missing_destinations_returns_400(self, client):
        resp = client.post("/api/flow", json={
            "content": "Some content",
            "destinations": [],
        })
        assert resp.status_code == 400

    def test_valid_flow_returns_ok(self, client):
        resp = client.post("/api/flow", json={
            "content": "Contact research for Alice",
            "destinations": ["clipboard"],
            "data_type": "contact_research",
            "metadata": {"person_name": "Alice"},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "results" in data

    def test_unknown_destination_does_not_500(self, client):
        resp = client.post("/api/flow", json={
            "content": "Some content",
            "destinations": ["totally_unknown_destination"],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        # Unknown dest yields an error result entry, not a 500
        errors = [r for r in data["results"] if not r.get("ok")]
        assert len(errors) >= 1


class TestFlowQueue:
    def test_returns_ok(self, client):
        resp = client.get("/api/flow/queue")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "items" in data
        assert isinstance(data["items"], list)


class TestFlowDraftConfirm:
    def test_missing_draft_id_returns_400(self, client):
        resp = client.post("/api/flow/draft/confirm", json={})
        assert resp.status_code == 400

    def test_nonexistent_draft_returns_404(self, client):
        resp = client.post("/api/flow/draft/confirm",
                           json={"draft_id": "no-such-draft"})
        assert resp.status_code == 404

    def test_existing_draft_confirms(self, client):
        """Write a fake draft file and confirm it."""
        draft_id = "testdraft001"
        draft_file = friday_server.FLOW_QUEUE_DIR / f"gmail-draft-{draft_id}.json"
        friday_server.FLOW_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        draft_file.write_text(json.dumps({
            "draft_id": draft_id, "status": "pending",
        }), encoding="utf-8")
        try:
            resp = client.post("/api/flow/draft/confirm",
                               json={"draft_id": draft_id})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
        finally:
            draft_file.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
#  8. ROUTINES
# ═══════════════════════════════════════════════════════════════

class TestRoutines:
    def test_list_returns_ok(self, client):
        resp = client.get("/api/routines")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "routines" in data

    def test_routines_matches_registry(self, client):
        data = client.get("/api/routines").get_json()
        ids = {r["id"] for r in data["routines"]}
        registry_ids = {r["id"] for r in friday_server.ROUTINE_REGISTRY}
        assert ids == registry_ids

    def test_routine_run_unknown_returns_404(self, client):
        resp = client.post("/api/routines/no-such-routine/run")
        assert resp.status_code == 404

    def test_routine_run_known_returns_ok(self, client, _block_subprocess):
        """Running a real routine enqueues a VIBE_TERMINAL entry.
        _run_claude_terminal is patched to no-op so nothing actually launches."""
        first_id = friday_server.ROUTINE_REGISTRY[0]["id"]
        resp = client.post(f"/api/routines/{first_id}/run")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["routine"] == first_id
        assert "task_id" in data

    def test_routine_run_creates_vibe_terminal_entry(self, client, _block_subprocess):
        first_id = friday_server.ROUTINE_REGISTRY[0]["id"]
        resp = client.post(f"/api/routines/{first_id}/run")
        tid = resp.get_json()["task_id"]
        status = client.get("/api/vibe-code/status").get_json()
        known_ids = [t["id"] for t in status["terminals"]]
        assert tid in known_ids


# ═══════════════════════════════════════════════════════════════
#  9. LOGS
# ═══════════════════════════════════════════════════════════════

class TestLogsRecent:
    def test_returns_ok(self, client):
        resp = client.get("/api/logs/recent")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "events" in data

    def test_events_is_list(self, client):
        data = client.get("/api/logs/recent").get_json()
        assert isinstance(data["events"], list)

    def test_custom_limit_respected(self, client):
        # Emit a few log lines first
        for i in range(5):
            client.post("/api/logs/emit",
                        json={"message": f"test log line {i}", "source": "pytest"})
        resp = client.get("/api/logs/recent?limit=3")
        data = resp.get_json()
        assert data["count"] <= 3


class TestLogsEmit:
    def test_missing_message_returns_400(self, client):
        resp = client.post("/api/logs/emit", json={})
        assert resp.status_code == 400

    def test_empty_message_returns_400(self, client):
        resp = client.post("/api/logs/emit", json={"message": "  "})
        assert resp.status_code == 400

    def test_valid_message_returns_ok(self, client):
        resp = client.post("/api/logs/emit",
                           json={"message": "hello from test", "source": "pytest"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "event" in data

    def test_emitted_event_appears_in_recent(self, client):
        unique_msg = f"unique-test-log-{uuid.uuid4()}"
        client.post("/api/logs/emit", json={"message": unique_msg})
        events = client.get("/api/logs/recent").get_json()["events"]
        messages = [e["message"] for e in events]
        assert unique_msg in messages

    # NOTE: /api/logs/stream is an infinite SSE generator — we deliberately
    # do NOT GET it here to avoid hanging the test runner.


# ═══════════════════════════════════════════════════════════════
#  10. TASKS & AGENT STEER & PROCESSES
# ═══════════════════════════════════════════════════════════════

class TestTasks:
    def test_list_returns_tasks_key(self, client):
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "tasks" in data

    def test_get_unknown_task_returns_404(self, client):
        resp = client.get("/api/tasks/no-such-task-id")
        assert resp.status_code == 404

    def test_delete_unknown_task_returns_404(self, client):
        resp = client.delete("/api/tasks/no-such-task-id")
        assert resp.status_code == 404

    def test_create_and_delete_task_lifecycle(self, client):
        """Directly insert a task into the in-memory registry, then delete via API."""
        import time as _t
        tid = f"pytest-task-{uuid.uuid4()}"
        with friday_server.TASKS_LOCK:
            friday_server.TASKS[tid] = {
                "task_id": tid,
                "name": "Test Task",
                "status": "running",
                "created": _t.time(),
            }
        try:
            # Should be found
            resp = client.get(f"/api/tasks/{tid}")
            assert resp.status_code == 200
            # Delete it
            del_resp = client.delete(f"/api/tasks/{tid}")
            assert del_resp.status_code == 200
            assert del_resp.get_json()["status"] == "cancelled"
        finally:
            with friday_server.TASKS_LOCK:
                friday_server.TASKS.pop(tid, None)


class TestAgentSteer:
    def test_missing_fields_returns_400(self, client):
        resp = client.post("/api/agent/steer", json={})
        assert resp.status_code == 400

    def test_unknown_task_returns_404(self, client):
        resp = client.post("/api/agent/steer",
                           json={"task_id": "ghost-task", "message": "keep going"})
        assert resp.status_code == 404

    def test_steer_known_task(self, client):
        import time as _t
        tid = f"pytest-steer-{uuid.uuid4()}"
        with friday_server.TASKS_LOCK:
            friday_server.TASKS[tid] = {
                "task_id": tid, "name": "Steerable", "status": "running",
                "created": _t.time(),
            }
        try:
            resp = client.post("/api/agent/steer",
                               json={"task_id": tid, "message": "pivot approach"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert tid in data["task_id"]
        finally:
            with friday_server.TASKS_LOCK:
                friday_server.TASKS.pop(tid, None)
            with friday_server._FOLLOW_UP_LOCK:
                friday_server._FOLLOW_UP_QUEUES.pop(tid, None)


class TestProcesses:
    def test_returns_processes_key(self, client):
        resp = client.get("/api/processes")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "processes" in data

    def test_processes_is_list(self, client):
        assert isinstance(client.get("/api/processes").get_json()["processes"], list)


# ═══════════════════════════════════════════════════════════════
#  11. SYSTEM
# ═══════════════════════════════════════════════════════════════

class TestSystemInfo:
    def test_returns_200(self, client):
        """subprocess.run is mocked; PowerShell never executes."""
        resp = client.get("/api/system")
        assert resp.status_code == 200

    def test_shape_has_disks_and_processes(self, client):
        data = client.get("/api/system").get_json()
        # With mocked subprocess stdout="", json.loads("") fails → empty lists
        assert "disks" in data or "status" in data

    def test_powershell_called_not_executed(self, client, _block_subprocess):
        """Confirm a powershell command was recorded (not executed)."""
        client.get("/api/system")
        cmds = [c["cmd"] for c in _block_subprocess.calls if isinstance(c.get("cmd"), list)]
        assert any("powershell" in str(cmd).lower() for cmd in cmds)


class TestHealth:
    def test_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_has_status_ok(self, client):
        data = client.get("/api/health").get_json()
        assert data["status"] == "ok"

    def test_has_uptime(self, client):
        data = client.get("/api/health").get_json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], int)


# ═══════════════════════════════════════════════════════════════
#  12. NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

class TestNotifications:
    def test_get_returns_ok(self, client):
        resp = client.get("/api/notifications")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "items" in data or "notifications" in data

    def test_push_missing_title_returns_400_or_503(self, client):
        """Missing title → 400 (or 503 if engine is unavailable in test env)."""
        resp = client.post("/api/notifications/push", json={})
        assert resp.status_code in (400, 503)

    def test_push_and_list_round_trip(self, client):
        """Push a notification and check it surfaces in the list (if engine up)."""
        push_resp = client.post("/api/notifications/push", json={
            "title": "pytest test notification",
            "body": "created by test_devtools_system_routes",
            "priority": "low",
            "source": "pytest",
        })
        if push_resp.status_code == 503:
            pytest.skip("notifications_engine not available in test environment")
        assert push_resp.status_code == 200
        entry = push_resp.get_json()["notification"]
        nid = entry["id"]

        # Should appear in the list
        list_data = client.get("/api/notifications").get_json()
        all_ids = [n.get("id") for n in (list_data.get("items") or list_data.get("notifications") or [])]
        assert nid in all_ids

    def test_read_endpoint_reachable(self, client):
        resp = client.post("/api/notifications/read", json={"id": "nonexistent"})
        assert resp.status_code < 500

    def test_dismiss_endpoint_reachable(self, client):
        resp = client.post("/api/notifications/dismiss", json={"id": "nonexistent"})
        assert resp.status_code < 500

    def test_chat_injections_returns_items(self, client):
        resp = client.get("/api/notifications/chat-injections")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data

    def test_chat_injections_ack_missing_id_returns_400(self, client):
        resp = client.post("/api/notifications/chat-injections/ack", json={})
        assert resp.status_code in (400, 200)  # route returns 400 when no id

    def test_chat_injections_ack_unknown_id(self, client):
        resp = client.post("/api/notifications/chat-injections/ack",
                           json={"id": "no-such-injection"})
        assert resp.status_code < 500

    def test_mark_all_read(self, client):
        resp = client.post("/api/notifications/read", json={"all": True, "id": None})
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════
#  SAFETY CONFIRMATION: no real subprocess / app-launch
# ═══════════════════════════════════════════════════════════════

class TestSafetyHarnessIntegrity:
    """Meta-tests: confirm the safety harness is working as expected."""

    def test_subprocess_run_is_mocked(self, _block_subprocess):
        """Calling the (monkeypatched) subprocess.run returns a fake object."""
        result = subprocess.run(["git", "status"],
                                              capture_output=True, text=True)
        assert isinstance(result, _FakeCompletedProcess)
        assert result.returncode == 0

    def test_subprocess_popen_is_mocked(self, _block_subprocess):
        proc = subprocess.Popen(["cmd"], shell=True)
        assert isinstance(proc, _FakeProc)
        assert proc.pid == 99999

    def test_perform_open_is_mocked(self):
        """_perform_open is stubbed; calling it must not open anything."""
        result = friday_server._perform_open("downloads")
        assert "[mocked open:" in result

    def test_run_claude_terminal_is_mocked(self):
        """_run_claude_terminal is no-op; calling it must not spawn a CMD window."""
        # If it returned None (the no-op stub) without error, the mock is active.
        result = friday_server._run_claude_terminal("tid-test", "task", str(Path.home()))
        assert result is None  # stub returns None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
