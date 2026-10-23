"""A Claude Code task on a git repository works in its own worktree (NS-21.15-3).

A session Friday starts on a folder inside a git repository never edits the
owner's checkout. At launch Friday makes a git worktree on a new branch
``friday/<task>`` in ``<home>/Friday worktrees/<repo>-<task>`` and starts the
session there; the owner's working tree, index and current branch are left as
they were. Bringing the branch back (merge, cherry-pick, discard) is the
owner's own action.

The session gate (services/claude_code_tasks.gate) adds two refusals for such a
task, on top of Friday's own:

* a file call or command aimed at the original checkout;
* a git command that changes what the repository shares with the owner's
  checkout: pushing, moving or deleting branches and tags, the stash, worktrees,
  the repository's config, switching the worktree to another branch, or pointing
  git at another folder.

The worktree is the boundary; the gate's checks on command text are a second
line and cannot see every spelling (a relative path that climbs out, say).

A repository where the worktree cannot be made does not launch. A folder that is
not in a repository, or whose repository is the home folder itself (a dotfiles
repository), runs in place as before, and the card says so.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

BRANCH_PREFIX = "friday/"
FOLDER_NAME = "Friday worktrees"


class WorktreeError(RuntimeError):
    """The worktree could not be made, so the session does not start."""


def _home() -> Path:
    import agent_friday.core as core
    return Path(os.path.normpath(os.path.abspath(str(core.HOME))))


def _norm(p) -> str:
    """One spelling for comparing paths: absolute, normalised, forward slashes,
    lower case where the file system ignores case."""
    s = os.path.normpath(os.path.abspath(os.path.expanduser(str(p))))
    s = s.replace("\\", "/")
    return s.lower() if os.name == "nt" else s


def _inside(child, parent) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip("/") + "/")


def _safe_name(s: str, limit: int = 40) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "")).strip("-.")[:limit]
    return out or "task"


def repo_root(folder) -> Optional[Path]:
    """The top of the git repository holding ``folder`` (a ``.git`` folder or file
    in it or a parent), or None."""
    try:
        p = Path(os.path.normpath(os.path.abspath(str(folder))))
    except Exception:
        return None
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def worktrees_root() -> Path:
    return _home() / FOLDER_NAME


def plan(task_id: str, cwd) -> Dict[str, str]:
    """Where a session for this task will work. {} when it runs in place."""
    root = repo_root(cwd)
    if root is None:
        return {}
    home = _home()
    if _inside(home, root):
        return {}          # the home folder's own repository: not a project checkout
    name = "%s-%s" % (_safe_name(root.name, 40), _safe_name(task_id, 24))
    wt = worktrees_root() / name
    try:
        sub = os.path.relpath(os.path.normpath(os.path.abspath(str(cwd))), str(root))
    except ValueError:
        sub = "."
    return {"repo": str(root), "worktree": str(wt), "branch": BRANCH_PREFIX + _safe_name(task_id, 40),
            "sub": "" if sub in (".", "") else sub}


def card_text(info: Dict[str, str]) -> str:
    if not info:
        return ("This folder is not in a git repository of its own, so the session "
                "edits it directly.")
    return ("Friday makes a separate working copy (a git worktree) on a new branch %s in "
            "%s and the session works only there. Your checkout in %s is not changed; "
            "merging the branch back is yours to do." % (info["branch"], info["worktree"], info["repo"]))


def _git(args: List[str], cwd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        import agent_friday.core as core
        flags = getattr(core, "_POPEN_FLAGS", 0)
    except Exception:
        flags = 0
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, creationflags=flags)


def prepare(task_id: str, cwd, entry: Optional[dict] = None) -> str:
    """Make the task's worktree when its folder is in a repository and return the
    folder the session starts in. Raises WorktreeError (nothing starts)."""
    info = plan(task_id, cwd)
    if not info:
        if entry is not None:
            entry["in_place"] = True
        return str(cwd)
    wt = Path(info["worktree"])
    if wt.exists():
        raise WorktreeError("the worktree folder %s already exists; Friday does not reuse it" % wt)
    try:
        wt.parent.mkdir(parents=True, exist_ok=True)
        cp = _git(["worktree", "add", "-b", info["branch"], str(wt), "HEAD"], cwd=info["repo"])
    except FileNotFoundError:
        raise WorktreeError("git is not installed, so Friday cannot keep this session "
                            "off your checkout; it was not started")
    except (OSError, subprocess.SubprocessError) as e:
        raise WorktreeError("the worktree could not be made (%s); the session was not started"
                            % type(e).__name__)
    if cp.returncode != 0:
        msg = (cp.stderr or cp.stdout or "").strip().splitlines()
        raise WorktreeError("git could not make the worktree (%s); the session was not started"
                            % (msg[-1][:200] if msg else "exit %d" % cp.returncode))
    session_cwd = wt / info["sub"] if info["sub"] else wt
    if not session_cwd.is_dir():
        session_cwd = wt
    if entry is not None:
        entry.update({"repo": info["repo"], "worktree": str(wt), "branch": info["branch"],
                      "work_cwd": str(session_cwd), "in_place": False})
    return str(session_cwd)


# ── the session gate's refusals ──────────────────────────────────────────────

_PATH_KEYS = ("file_path", "path", "notebook_path")
_SEGMENT = re.compile(r"&&|\|\||[;&|\n]")

#: git subcommands that change what the repository shares with the owner's
#: checkout (refs, stash, worktrees, config) or move the worktree off its branch.
_ALWAYS_REFUSED = {"push", "update-ref", "symbolic-ref", "stash", "worktree", "switch",
                   "filter-branch", "filter-repo", "replace", "notes", "remote", "submodule",
                   "reflog", "gc", "prune", "pack-refs", "lfs", "subtree"}
_BRANCH_WRITE_FLAGS = {"-d", "-D", "-f", "-m", "-M", "-c", "-C", "--delete", "--force", "--move",
                       "--copy", "--set-upstream-to", "-u", "--unset-upstream", "--edit-description",
                       "--track", "-t"}
_GIT_REDIRECTS = ("--git-dir", "--work-tree", "git_dir=", "git_work_tree=", "git_common_dir=",
                  "git_index_file=", "git_object_directory=")


def _git_subcommand(tokens: List[str]) -> Tuple[Optional[str], List[str], List[str]]:
    """(subcommand, its args, the global options before it) for one command's tokens."""
    i, opts = 0, []
    while i < len(tokens):
        t = tokens[i]
        if t in ("-c", "-C", "--namespace", "--exec-path") and i + 1 < len(tokens):
            opts += [t, tokens[i + 1]]
            i += 2
            continue
        if t.startswith("-"):
            opts.append(t)
            i += 1
            continue
        return t.lower(), tokens[i + 1:], opts
    return None, [], opts


def _git_refusal(command: str) -> Optional[str]:
    low = command.lower()
    if any(r in low for r in _GIT_REDIRECTS):
        return "this session's git stays in its own worktree; pointing git at another folder is not run"
    for seg in _SEGMENT.split(command):
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            tokens = seg.split()
        while tokens and ("=" in tokens[0] and not tokens[0].startswith("-")):
            tokens = tokens[1:]            # VAR=value prefixes
        if not tokens:
            continue
        exe = os.path.basename(tokens[0].replace("\\", "/")).lower()
        if exe not in ("git", "git.exe"):
            continue
        sub, args, opts = _git_subcommand(tokens[1:])
        if "-C" in opts:
            return "this session's git stays in its own worktree; 'git -C' is not run"
        if sub is None:
            continue
        if sub in _ALWAYS_REFUSED:
            return ("'git %s' changes what your repository shares with your own checkout; "
                    "this session works only on its branch, so it is not run" % sub)
        if sub == "branch" and any(a in _BRANCH_WRITE_FLAGS or a.startswith(("--set-upstream", "--delete",
                                                                             "--move", "--copy", "--force"))
                                   for a in args):
            return "changing branches other than its own is yours to do; 'git branch' with that option is not run"
        if sub == "branch" and [a for a in args if not a.startswith("-")] \
                and not any(a in ("-l", "--list", "--contains", "--merged", "--no-merged", "--points-at")
                            for a in args):
            return "this session works on its own branch; making other branches is yours to do"
        if sub == "tag" and args and not any(a in ("-l", "--list") for a in args):
            return "tags are shared with your checkout; 'git tag' that changes them is not run"
        if sub == "config" and not any(a in ("--get", "--get-all", "--get-regexp", "--list", "-l")
                                       for a in args):
            return "the repository's config is shared with your checkout; changing it is not run"
        if sub == "checkout" and not ("--" in args or args == ["."]):
            return ("this session stays on its own branch; 'git checkout' is run only for files "
                    "('git checkout -- <files>')")
        if sub == "fetch" and any(":" in a for a in args):
            return "a fetch that writes a named branch is not run; plain 'git fetch' is fine"
    return None


def refusal(entry: dict, tool_name: str, tool_input: dict) -> Optional[str]:
    """Why a call from a worktree session is refused, or None. Only for a task that
    has a worktree; an in-place task is unaffected."""
    repo, wt = (entry or {}).get("repo"), (entry or {}).get("worktree")
    if not repo or not wt:
        return None
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    for key in _PATH_KEYS:
        val = str(tool_input.get(key) or "")
        if not val:
            continue
        base = (entry or {}).get("work_cwd") or wt
        target = val if os.path.isabs(os.path.expanduser(val)) else os.path.join(base, val)
        if _inside(target, repo) and not _inside(target, wt):
            return ("this session works in its own worktree (%s); your checkout in %s is "
                    "changed only by you" % (wt, repo))
    if tool_name in ("Bash", "PowerShell"):
        cmd = str(tool_input.get("command") or "")
        low = cmd.replace("\\", "/")
        low_cmp = low.lower() if os.name == "nt" else low
        repo_s = _norm(repo)
        wt_s = _norm(wt)
        # The checkout's own path, but not the worktree's (which may share a prefix).
        scrubbed = low_cmp.replace(wt_s, "")
        if re.search(re.escape(repo_s.rstrip("/")) + r"(?=$|[/\s\"'`;&|)])", scrubbed):
            return ("this session works in its own worktree (%s); a command aimed at your "
                    "checkout in %s is not run" % (wt, repo))
        why = _git_refusal(cmd)
        if why:
            return why
    return None


def describe(entry: dict) -> Dict[str, object]:
    """What the task's status shows."""
    e = entry or {}
    if e.get("worktree"):
        return {"worktree": e.get("worktree"), "branch": e.get("branch"), "repo": e.get("repo"),
                "in_place": False}
    return {"in_place": bool(e.get("in_place"))}


__all__ = ["BRANCH_PREFIX", "WorktreeError", "plan", "prepare", "refusal", "card_text",
           "describe", "repo_root", "worktrees_root"]
