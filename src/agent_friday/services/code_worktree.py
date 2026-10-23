"""A Claude Code task on a git repository works in its own worktree (NS-21.15-3).

A session Friday starts on a folder inside a git repository never edits the
owner's checkout. At launch Friday makes a git worktree on a new branch
``friday/<task>`` in ``<home>/Friday worktrees/<repo>-<task>`` and starts the
session there; the owner's working tree, index and current branch are left as
they were. Bringing the branch back (merge, cherry-pick, discard) is the
owner's own action.

The session gate (services/claude_code_tasks.gate) adds these refusals for such
a task, on top of Friday's own:

* a write aimed at the original checkout (reads are fine), or at any ``.git``
  file or folder (the worktree's ``.git`` file says which repository its commits
  go to; the shared folder holds hooks and config);
* a command naming the checkout, a ``.git`` folder, the repository's hooks, or a
  ``GIT_*`` variable;
* a git command that changes what the repository shares with the owner's
  checkout: pushing, moving, making or deleting branches and tags, the stash,
  worktrees, the repository's config, ``-c`` settings and aliases, switching the
  worktree to another branch; also inside ``bash -c`` / ``cmd /c`` / PowerShell
  wrappers and ``$( )``.
* a tripwire: the repository's hooks and config are fingerprinted at launch; if
  they change during the session, every later call is refused and the owner is
  told.

The worktree is the boundary; the checks on command text are a second line and
cannot see every spelling. The card says so.

A repository where the worktree cannot be made does not launch. A folder that is
not in a repository runs in place as before. A folder in the home folder's own
repository (a dotfiles repository) runs in place, and the git checks above
still apply to it. The card says which.
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BRANCH_PREFIX = "friday/"
FOLDER_NAME = "Friday worktrees"


class WorktreeError(RuntimeError):
    """The worktree could not be made, so the session does not start."""


def _home() -> Path:
    import agent_friday.core as core
    return Path(os.path.normpath(os.path.abspath(str(core.HOME))))


def _fold(s: str) -> str:
    s = s.replace("\\", "/")
    if s.startswith(("//?/", "//./")):
        s = s[4:]
    return s.lower() if os.name == "nt" else s


def _norm(p) -> str:
    """One spelling for comparing paths: absolute, normalised, forward slashes,
    lower case where the file system ignores case."""
    return _fold(os.path.normpath(os.path.abspath(os.path.expanduser(str(p)))))


def _real(p) -> str:
    """The path with links, junctions and short (8.3) names resolved."""
    try:
        s = os.path.realpath(os.path.expanduser(str(p)))
    except (OSError, ValueError):
        s = os.path.abspath(os.path.expanduser(str(p)))
    return _fold(os.path.normpath(_long_name(s)))


def _long_name(s: str) -> str:
    if sys.platform != "win32":
        return s
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(str(s), buf, 32768)
        return buf.value if 0 < n < 32768 else s
    except Exception:
        return s


def _short_name(s: str) -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(s), buf, 32768)
        return buf.value if 0 < n < 32768 else ""
    except Exception:
        return ""


def _under(c: str, p: str) -> bool:
    return c == p or c.startswith(p.rstrip("/") + "/")


def _inside(child, parent) -> bool:
    """True when ``child`` is ``parent`` or below it in either spelling (as written,
    or with links and short names resolved)."""
    return _under(_norm(child), _norm(parent)) or _under(_real(child), _real(parent))


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
    if root is None or _inside(_home(), root):
        return {}          # no repository, or the home folder's own (not a project checkout)
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
        return ("This folder is not in a project's git repository, so the session edits it "
                "directly.")
    return ("Friday makes a separate working copy (a git worktree) on a new branch %s in "
            "%s and the session works only there. Your checkout in %s is not changed; "
            "merging the branch back is yours to do. Friday also refuses commands it can "
            "see are aimed at your checkout or at what it shares (branches, tags, hooks, "
            "config), but it cannot read every way a command can be written: the separate "
            "copy is the real protection." % (info["branch"], info["worktree"], info["repo"]))


def _git(args: List[str], cwd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        import agent_friday.core as core
        flags = getattr(core, "_POPEN_FLAGS", 0)
    except Exception:
        flags = 0
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, creationflags=flags)


def _common_dir(repo: str) -> Optional[Path]:
    """The repository's shared .git folder (hooks, config, refs)."""
    dot = Path(repo) / ".git"
    if dot.is_dir():
        return dot
    try:
        text = dot.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if text.startswith("gitdir:"):
        g = Path(text[7:].strip())
        if not g.is_absolute():
            g = Path(repo) / g
        common = g / "commondir"
        if common.is_file():
            c = Path(common.read_text(encoding="utf-8", errors="replace").strip())
            return c if c.is_absolute() else (g / c)
        return g
    return None


def fingerprint(repo: str) -> str:
    """A digest of the repository's hooks and config: what runs as the owner the
    next time they use git, and how git behaves for them."""
    h = hashlib.sha256()
    common = _common_dir(repo)
    if common is None:
        return ""
    for rel in ("config",):
        f = common / rel
        try:
            h.update(rel.encode() + b"\0" + f.read_bytes() + b"\0")
        except OSError:
            h.update(rel.encode() + b"\0-\0")
    hooks = common / "hooks"
    try:
        for f in sorted(hooks.rglob("*")):
            if f.is_file():
                h.update(str(f.relative_to(hooks)).encode() + b"\0" + f.read_bytes() + b"\0")
    except OSError:
        h.update(b"hooks-unreadable")
    return h.hexdigest()


def _aliases(repo: str) -> Dict[str, str]:
    try:
        cp = _git(["config", "--get-regexp", r"^alias\."], cwd=repo, timeout=20)
    except Exception:
        return {}
    out = {}
    for line in (cp.stdout or "").splitlines():
        k, _, v = line.partition(" ")
        if k.lower().startswith("alias."):
            out[k[6:].lower()] = v
    return out


def _registered(repo: str, wt: Path, branch: str) -> bool:
    """True when ``wt`` is this repository's worktree for ``branch`` (a retried task)."""
    try:
        cp = _git(["worktree", "list", "--porcelain"], cwd=repo, timeout=20)
    except Exception:
        return False
    cur = None
    for line in (cp.stdout or "").splitlines():
        if line.startswith("worktree "):
            cur = line[9:].strip()
        elif line.startswith("branch ") and cur and _inside(cur, wt) and _inside(wt, cur):
            return line[7:].strip() == "refs/heads/" + branch
    return False


def prepare(task_id: str, cwd, entry: Optional[dict] = None) -> str:
    """Make the task's worktree when its folder is in a repository and return the
    folder the session starts in. Raises WorktreeError (nothing starts)."""
    info = plan(task_id, cwd)
    if not info:
        if entry is not None:
            entry["in_place"] = True
            root = repo_root(cwd)
            if root is not None:
                entry["in_place_repo"] = str(root)
                entry["git_aliases"] = _aliases(str(root))
        return str(cwd)
    wt = Path(info["worktree"])
    try:
        if wt.exists():
            if not _registered(info["repo"], wt, info["branch"]):
                raise WorktreeError("the folder %s already exists and is not this task's "
                                    "worktree; the session was not started" % wt)
            # This task's own worktree from an earlier start: carry on in it.
        else:
            wt.parent.mkdir(parents=True, exist_ok=True)
            cp = _git(["worktree", "add", "-b", info["branch"], str(wt), "HEAD"], cwd=info["repo"])
            if cp.returncode != 0:
                msg = (cp.stderr or cp.stdout or "").strip().splitlines()
                raise WorktreeError("git could not make the worktree (%s); the session was not started"
                                    % (msg[-1][:200] if msg else "exit %d" % cp.returncode))
    except FileNotFoundError:
        raise WorktreeError("git is not installed, so Friday cannot keep this session "
                            "off your checkout; it was not started")
    except (OSError, subprocess.SubprocessError) as e:
        raise WorktreeError("the worktree could not be made (%s); the session was not started"
                            % type(e).__name__)
    session_cwd = wt / info["sub"] if info["sub"] else wt
    if not session_cwd.is_dir():
        session_cwd = wt
    if entry is not None:
        entry.update({"repo": info["repo"], "worktree": str(wt), "branch": info["branch"],
                      "work_cwd": str(session_cwd), "in_place": False,
                      "repo_fingerprint": fingerprint(info["repo"]),
                      "git_aliases": _aliases(info["repo"])})
    return str(session_cwd)


# ── the session gate's refusals ──────────────────────────────────────────────

_PATH_KEYS = ("file_path", "path", "notebook_path")
_READ_TOOLS = {"Read", "Grep", "Glob", "LS"}
_SHELLS = {"bash", "sh", "zsh", "dash", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh",
           "pwsh.exe", "bash.exe", "sh.exe", "wsl", "wsl.exe"}
_SEGMENT = re.compile(r"&&|\|\||[;&|\n]|\$\(|[(){}`]")
_DOT_GIT = re.compile(r"(^|[/\\\s\"'=:])\.git($|[/\\\s\"';:)])", re.I)
_GIT_ENV = re.compile(r"\bGIT_[A-Z_]+\s*=|\$env:GIT_|%GIT_|\bexport\s+GIT_|\bset\s+GIT_", re.I)
_SHARED_HINTS = ("--git-common-dir", "--git-path", "--git-dir", "--work-tree", "hookspath")

#: git subcommands that change what the repository shares with the owner's
#: checkout (refs, stash, worktrees, config, remotes) or run arbitrary programs.
_ALWAYS_REFUSED = {"push", "update-ref", "symbolic-ref", "stash", "worktree", "switch",
                   "filter-branch", "filter-repo", "replace", "gc", "prune", "pack-refs",
                   "send-pack", "http-push", "receive-pack", "subtree", "lfs", "bisect", "init",
                   "clone", "maintenance", "fast-import", "update-index"}
_BRANCH_WRITE_FLAGS = {"-d", "-D", "-f", "-m", "-M", "-c", "-C", "--delete", "--force", "--move",
                       "--copy", "--set-upstream-to", "-u", "--unset-upstream", "--edit-description",
                       "--track", "-t"}
_BRANCH_READ_FLAGS = {"-l", "--list", "--contains", "--no-contains", "--merged", "--no-merged",
                      "--points-at", "--sort", "--format", "-v", "-vv", "-a", "-r", "--all", "--remotes",
                      "--show-current"}
_TAG_READ_FLAGS = {"-l", "--list", "-n", "--sort", "--contains", "--no-contains", "--points-at",
                   "--merged", "--no-merged", "--format", "-v", "--verify"}


def _git_subcommand(tokens: List[str]) -> Tuple[Optional[str], List[str], List[str]]:
    """(subcommand, its args, the global options before it) for one command's tokens."""
    i, opts = 0, []
    while i < len(tokens):
        t = tokens[i]
        if t in ("-c", "-C", "--namespace", "--exec-path", "--config-env") and i + 1 < len(tokens):
            opts += [t, tokens[i + 1]]
            i += 2
            continue
        if t.startswith("-"):
            opts.append(t)
            i += 1
            continue
        return t.lower(), tokens[i + 1:], opts
    return None, [], opts


def _flag(a: str) -> str:
    return a.split("=", 1)[0]


def _one_git(sub: str, args: List[str]) -> Optional[str]:
    shared = ("'git %s' changes what your repository shares with your own checkout; "
              "this session works only on its branch, so it is not run" % sub)
    if sub in _ALWAYS_REFUSED:
        return shared
    if sub == "branch":
        flags = {_flag(a) for a in args if a.startswith("-")}
        names = [a for a in args if not a.startswith("-")]
        if flags & _BRANCH_WRITE_FLAGS or any(f.startswith(("--set-upstream", "--delete", "--move",
                                                            "--copy", "--force")) for f in flags):
            return "changing branches other than its own is yours to do; 'git branch' with that option is not run"
        if names and not (flags & _BRANCH_READ_FLAGS):
            return "this session works on its own branch; making other branches is yours to do"
    if sub == "tag" and args:
        flags = {_flag(a) for a in args if a.startswith("-")}
        names = [a for a in args if not a.startswith("-")]
        if flags - _TAG_READ_FLAGS or (names and not flags & _TAG_READ_FLAGS):
            return "tags are shared with your checkout; 'git tag' that changes them is not run"
    if sub == "config" and not any(a in ("--get", "--get-all", "--get-regexp", "--list", "-l")
                                   for a in args):
        return "the repository's config is shared with your checkout; changing it is not run"
    if sub == "checkout" and not (args[:1] == ["--"] or args == ["."]):
        return ("this session stays on its own branch; 'git checkout' is run only for files, "
                "written 'git checkout -- <files>' (or use 'git restore')")
    if sub in ("fetch", "pull") and any(":" in a and not a.startswith("-") and "://" not in a
                                        for a in args):
        return "a fetch or pull that writes a named branch is not run; plain 'git %s' is fine" % sub
    if sub == "rebase" and any(_flag(a) in ("--update-refs", "--exec", "-x") for a in args):
        return "'git rebase' with that option reaches beyond this session's branch; it is not run"
    if sub == "remote" and args and args[0] not in ("-v", "--verbose", "show", "get-url"):
        return shared
    if sub == "reflog" and args and args[0] not in ("show",) and not args[0].startswith("-"):
        return shared
    if sub == "notes" and args and args[0] not in ("show", "list"):
        return shared
    if sub == "submodule" and args and args[0] not in ("update", "init", "status", "summary", "sync"):
        return shared
    return None


def _git_refusal(command: str, aliases: Optional[Dict[str, str]] = None, depth: int = 0) -> Optional[str]:
    if depth > 3:
        return "this command nests too deeply for Friday to read, so it is not run"
    low = command.lower()
    if _GIT_ENV.search(command):
        return "this session's git stays in its own worktree; setting GIT_* variables is not run"
    if any(h in low.replace("\\", "/") for h in _SHARED_HINTS):
        return ("this touches the repository's shared folder (hooks, config) or points git "
                "elsewhere; it is not run")
    if re.search(r"(powershell|pwsh)\S*\s.*(-encodedcommand|-enc\s|-ec\s|-e\s+[a-z0-9+/=]{16,})", low):
        return "an encoded command cannot be read, so it is not run"
    for seg in _SEGMENT.split(command):
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            tokens = seg.split()
        while tokens and ("=" in tokens[0] and not tokens[0].startswith("-")):
            tokens = tokens[1:]            # VAR=value prefixes
        while tokens and tokens[0].lower() in ("env", "command", "exec", "nohup", "time", "sudo", "&", "."):
            tokens = tokens[1:]
        if not tokens:
            continue
        exe = os.path.basename(tokens[0].replace("\\", "/")).lower().strip("'\"")
        if exe in _SHELLS:
            # bash -c "...", cmd /c ..., powershell -Command ...: read what it runs.
            rest = tokens[1:]
            for i, t in enumerate(rest):
                if t.lower() in ("-c", "/c", "/k", "-command", "-commandwithargs", "-lc", "--command"):
                    why = _git_refusal(" ".join(rest[i + 1:]), aliases, depth + 1)
                    if why:
                        return why
                    break
            continue
        if exe in ("xargs", "find", "parallel", "watch") and re.search(r"\bgit(\.exe|\.cmd|\.bat)?\b", seg, re.I):
            return "git run through %s cannot be checked, so it is not run" % exe
        if exe not in ("git", "git.exe", "git.cmd", "git.bat"):
            continue
        sub, args, opts = _git_subcommand(tokens[1:])
        if "-C" in opts:
            return "this session's git stays in its own worktree; 'git -C' is not run"
        if any(o in ("-c", "--config-env") for o in opts):
            return "'git -c' changes git's settings or aliases for this command; it is not run"
        if sub is None:
            continue
        if aliases and sub in aliases:
            expansion = aliases[sub]
            if expansion.startswith("!"):
                return "'git %s' is an alias that runs a program; it is not run" % sub
            why = _git_refusal("git " + expansion + " " + " ".join(shlex.quote(a) for a in args),
                               None, depth + 1)
            if why:
                return why
            continue
        why = _one_git(sub, args)
        if why:
            return why
    return None


def _home_spellings(text: str) -> str:
    """Command text with the usual ways of writing the home folder spelled out."""
    home = str(_home())
    for token in ("$env:USERPROFILE", "%USERPROFILE%", "${HOME}", "$HOME", "$env:HOME"):
        text = re.sub(re.escape(token), lambda _m: home, text, flags=re.I)
    text = re.sub(r"(^|[\s\"'=])~(?=[/\\]|$|\s)", lambda m: m.group(1) + home, text)
    return text


def _names_checkout(cmd: str, repo: str, wt: str) -> bool:
    text = _fold(_home_spellings(cmd))
    for spelling in {_norm(wt), _real(wt)}:
        text = text.replace(spelling, "")
    forms = {_norm(repo), _real(repo)}
    short = _short_name(repo)
    if short:
        forms.add(_fold(short))
    for f in forms:
        if f and re.search(re.escape(f.rstrip("/")) + r"(?=$|[/\s\"'`;&|)])", text):
            return True
    # A relative climb that ends in the checkout's folder name.
    name = _fold(os.path.basename(os.path.normpath(repo)))
    if name and ".." in text and re.search(r"\.\./(?:[^\s\"']*/)?" + re.escape(name) + r"(?=$|[/\s\"'`;&|)])", text):
        return True
    return False


def refusal(entry: dict, tool_name: str, tool_input: dict) -> Optional[str]:
    """Why a call from a session on a repository is refused, or None."""
    e = entry or {}
    repo, wt = e.get("repo"), e.get("worktree")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    aliases = e.get("git_aliases") or {}
    if not (repo and wt):
        if e.get("in_place_repo") and tool_name in ("Bash", "PowerShell"):
            return _git_refusal(str(tool_input.get("command") or ""), aliases)
        return None
    fp = e.get("repo_fingerprint")
    if fp and fingerprint(repo) != fp:
        e["tripwire"] = True
    if e.get("tripwire"):
        return ("your repository's hooks or config changed while this session was running; "
                "Friday has stopped running its actions until you look (%s)" % repo)
    writes = tool_name not in _READ_TOOLS
    for key in _PATH_KEYS:
        val = str(tool_input.get(key) or "")
        if not val:
            continue
        if writes and _DOT_GIT.search("/" + val.replace("\\", "/")):
            return ("the worktree's .git file and the repository's .git folder are not the "
                    "session's to change")
        base = e.get("work_cwd") or wt
        target = val if os.path.isabs(os.path.expanduser(val)) else os.path.join(base, val)
        if writes and _inside(target, repo) and not _inside(target, wt):
            return ("this session works in its own worktree (%s); your checkout in %s is "
                    "changed only by you" % (wt, repo))
    if tool_name in ("Bash", "PowerShell"):
        cmd = str(tool_input.get("command") or "")
        if _DOT_GIT.search(" " + cmd + " "):
            return "a command reaching into a .git folder or file is not run for this session"
        if _names_checkout(cmd, repo, wt):
            return ("this session works in its own worktree (%s); a command aimed at your "
                    "checkout in %s is not run" % (wt, repo))
        why = _git_refusal(cmd, aliases)
        if why:
            return why
    return None


def describe(entry: dict) -> Dict[str, object]:
    """What the task's status shows."""
    e = entry or {}
    if e.get("worktree"):
        return {"worktree": e.get("worktree"), "branch": e.get("branch"), "repo": e.get("repo"),
                "in_place": False, "tripwire": bool(e.get("tripwire"))}
    return {"in_place": bool(e.get("in_place"))}


__all__ = ["BRANCH_PREFIX", "WorktreeError", "plan", "prepare", "refusal", "card_text",
           "describe", "repo_root", "worktrees_root", "fingerprint"]
