"""A Claude Code task on a git repository never edits the owner's checkout (NS-21.15-3).

The session starts in its own git worktree on a new branch friday/<task>; the owner's
working tree, index and current branch stay as they were. The session gate refuses
file calls and commands aimed at the checkout and git commands that change what the
repository shares with it. A repository where the worktree cannot be made does not
launch; a folder outside a repository runs in place, and the card says which.
"""
from __future__ import annotations

import shutil
import subprocess
import types
from pathlib import Path

import pytest

try:
    from agent_friday.services import code_worktree as cwt
except ImportError:          # the old tree: the tests below fail by assertion
    cwt = None

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args],
                          cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A scratch Friday home, a scratch user home and a repository in it."""
    fh = tmp_path / "friday"
    fh.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(fh))
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(ap, "APPROVALS_FILE", fh / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", fh)
    import agent_friday.core as core
    monkeypatch.setattr(core, "VIBE_TERMINALS", {})
    from agent_friday.services import claude_code_tasks as cct
    from agent_friday.services import code_engine as ce
    monkeypatch.setattr(cct, "VIBE_TERMINALS", core.VIBE_TERMINALS)
    monkeypatch.setattr(ce, "VIBE_TERMINALS", core.VIBE_TERMINALS)
    monkeypatch.setattr(core, "_persist_vibe_terminals", lambda: None)
    monkeypatch.setattr(core, "_safe_under_home", lambda p: p)
    monkeypatch.setattr(ce, "_safe_under_home", lambda p: p)
    home = tmp_path / "home"
    repo = home / "Projects" / "proj"
    (repo / "src").mkdir(parents=True)
    monkeypatch.setattr(core, "HOME", home)
    _git(repo, "init", "-q")
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    popen = []

    def fake_popen(*a, **k):
        popen.append((a, k))
        return types.SimpleNamespace(pid=4242)
    monkeypatch.setattr(ce, "subprocess", types.SimpleNamespace(Popen=fake_popen, CREATE_NEW_CONSOLE=0))
    return types.SimpleNamespace(home=home, repo=repo, friday=fh, popen=popen, cct=cct, ce=ce, core=core)


def _launch(env, tid, cwd):
    env.core.VIBE_TERMINALS[tid] = {"id": tid, "task": "fix it", "status": "awaiting_approval",
                                    "cwd": str(cwd), "token": "tok-" + tid}
    env.cct._mint(tid, "fix it")
    env.ce._run_claude_terminal(tid, "fix it", str(cwd))
    return env.core.VIBE_TERMINALS[tid]


def _branch(cwd):
    return _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")


def test_a_repository_task_starts_in_its_own_worktree_on_its_own_branch(env):
    head_before = _git(env.repo, "rev-parse", "HEAD")
    branch_before = _branch(env.repo)
    entry = _launch(env, "t1", env.repo / "src")
    assert entry["status"] == "running", entry.get("error")
    assert len(env.popen) == 1
    started_in = Path(env.popen[0][1]["cwd"])
    assert not str(started_in).startswith(str(env.repo)), "the session started in the owner's checkout"
    wt = Path(entry["worktree"])
    assert started_in == wt / "src"
    assert str(started_in) in env.popen[0][0][0][2]           # the console cds there too
    assert _branch(wt) == "friday/t1" and entry["branch"] == "friday/t1"
    # The owner's checkout is as it was: same branch, same commit, nothing changed.
    assert _branch(env.repo) == branch_before
    assert _git(env.repo, "rev-parse", "HEAD") == head_before
    assert _git(env.repo, "status", "--porcelain") == ""


def test_work_in_the_worktree_leaves_the_owners_branch_alone(env):
    entry = _launch(env, "t2", env.repo)
    wt = Path(entry["worktree"])
    main_before = _git(env.repo, "rev-parse", "HEAD")
    (wt / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(wt, "commit", "-q", "-am", "session change")
    assert _git(env.repo, "rev-parse", "HEAD") == main_before
    assert (env.repo / "src" / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert _git(env.repo, "rev-parse", "friday/t2") != main_before    # the work is on its branch


def test_the_card_says_where_the_session_works(env):
    from agent_friday.services import approvals as ap
    rec = env.cct.request("t3", "refactor", str(env.repo))
    desc = next(r for r in ap.list_approvals() if r["approval_id"] == rec["approval_id"])["description"]
    assert "worktree" in desc and "friday/t3" in desc and "not changed" in desc
    outside = env.home / "notes"
    outside.mkdir()
    rec2 = env.cct.request("t4", "tidy", str(outside))
    desc2 = next(r for r in ap.list_approvals() if r["approval_id"] == rec2["approval_id"])["description"]
    assert "edits it directly" in desc2


def test_a_folder_outside_a_repository_runs_in_place(env):
    notes = env.home / "notes"
    notes.mkdir()
    entry = _launch(env, "t5", notes)
    assert entry["status"] == "running"
    assert Path(env.popen[0][1]["cwd"]) == notes
    assert not entry.get("worktree")


def test_no_worktree_means_no_launch(env, monkeypatch):
    assert cwt is not None, "no services/code_worktree on this tree"

    def no_git(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(cwt, "_git", no_git)
    entry = _launch(env, "t6", env.repo)
    assert env.popen == [], "the session started on the owner's checkout"
    assert entry["status"] == "error" and "git" in entry["error"]


def test_the_home_folders_own_repository_is_not_a_project_checkout(env):
    assert cwt is not None, "no services/code_worktree on this tree"
    _git(env.home, "init", "-q")
    notes = env.home / "notes"
    notes.mkdir()
    assert cwt.plan("t7", notes) == {}


def _worktree_session(env, tid="t8"):
    entry = _launch(env, tid, env.repo)
    entry["status"] = "running"
    return entry


@pytest.mark.parametrize("tool,inp", [
    ("Write", {"file_path": "{repo}/src/a.py", "content": "x"}),
    ("Edit", {"file_path": "{repo}/src/a.py", "old_string": "1", "new_string": "2"}),
    ("Bash", {"command": 'cd "{repo}" && git commit -am x'}),
    ("Bash", {"command": "git -C {repo} commit -am x"}),
    ("Bash", {"command": "git --git-dir={repo}/.git log"}),
    ("Bash", {"command": "git push origin HEAD"}),
    ("Bash", {"command": "git branch -D main"}),
    ("Bash", {"command": "git branch -f main HEAD"}),
    ("Bash", {"command": "git checkout main"}),
    ("Bash", {"command": "git switch -c other"}),
    ("Bash", {"command": "git stash"}),
    ("Bash", {"command": "git config user.email x@y"}),
    ("Bash", {"command": "git tag v9"}),
    ("Bash", {"command": "git update-ref refs/heads/main HEAD"}),
    ("Bash", {"command": "git worktree remove ."}),
    ("Bash", {"command": "echo ok && git push"}),
])
def test_the_gate_refuses_the_checkout_and_shared_git(env, tool, inp):
    entry = _worktree_session(env)
    repo = str(env.repo).replace("\\", "/")
    inp = {k: (v.replace("{repo}", repo) if isinstance(v, str) else v) for k, v in inp.items()}
    out = env.cct.gate("t8", entry["token"], tool, inp)
    assert out["decision"] == "deny", (tool, inp, out)


@pytest.mark.parametrize("tool,inp", [
    ("Write", {"file_path": "{wt}/src/a.py", "content": "x"}),
    ("Edit", {"file_path": "src/a.py", "old_string": "1", "new_string": "2"}),
    ("Bash", {"command": "git status"}),
    ("Bash", {"command": "git commit -am 'session change'"}),
    ("Bash", {"command": "git checkout -- src/a.py"}),
    ("Bash", {"command": "git log --oneline -5"}),
    ("Bash", {"command": "git branch"}),
    ("Bash", {"command": "git config --get user.name"}),
    ("Bash", {"command": "python -m pytest -q"}),
])
def test_the_gate_lets_the_session_work_in_its_worktree(env, tool, inp):
    entry = _worktree_session(env, "t9")
    wt = str(entry["worktree"]).replace("\\", "/")
    inp = {k: (v.replace("{wt}", wt) if isinstance(v, str) else v) for k, v in inp.items()}
    out = env.cct.gate("t9", entry["token"], tool, inp)
    assert out["decision"] == "allow", (tool, inp, out)


def test_an_in_place_task_is_not_held_to_a_worktree(env):
    notes = env.home / "notes"
    notes.mkdir()
    entry = _launch(env, "t10", notes)
    entry["status"] = "running"
    assert env.cct.gate("t10", entry["token"], "Bash", {"command": "git status"})["decision"] == "allow"
