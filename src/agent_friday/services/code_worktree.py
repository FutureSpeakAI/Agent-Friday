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

import bisect
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


#: Config keys that run a program, redirect where git talks to, or pull in other
#: config: a change to one of these acts as the owner later. Ordinary keys that git
#: itself rewrites in normal work (branch.*, remote.*.fetch, submodule.*) are left out.
_RISKY_EXACT = {"core.hookspath", "core.fsmonitor", "core.sshcommand", "core.editor", "core.pager",
                "core.askpass", "core.gitproxy", "core.worktree", "core.bare", "core.sparsecheckout",
                "gpg.program", "sequence.editor", "diff.external", "merge.tool", "diff.tool",
                "extensions.worktreeconfig"}
_RISKY_PREFIX = ("alias.", "filter.", "include.", "includeif.", "credential.", "url.", "gpg.",
                 "mergetool.", "difftool.", "pager.", "protocol.")
_RISKY_SUFFIX = (".sshcommand", ".command", ".cmd", ".program", ".helper", ".proxy", ".pushurl",
                 ".receivepack", ".uploadpack", ".textconv", ".driver", ".clean", ".smudge", ".process")


def _risky_config(path: Path) -> List[str]:
    """The risky entries of a git config file, in order, as 'section.key=value'."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ["<unreadable>"]
    out, section = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = re.match(r'^\[\s*([^\s\]"]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]\s*(.*)$', line)
        if m:
            section = m.group(1).lower() + ("." + m.group(2) if m.group(2) is not None else "")
            line = m.group(3).strip()
            if not line:
                continue
        k, _, v = line.partition("=")
        name = (section + "." + k.strip().lower()) if section else k.strip().lower()
        low = name.lower()
        last = "." + low.rsplit(".", 1)[-1]
        if low in _RISKY_EXACT or low.startswith(_RISKY_PREFIX) or last in _RISKY_SUFFIX:
            out.append("%s=%s" % (name, v.strip()))
    return out


def fingerprint(repo: str) -> str:
    """A digest of what in the repository acts as the owner later: its hooks and
    the config entries that run programs or redirect git."""
    h = hashlib.sha256()
    common = _common_dir(repo)
    if common is None:
        return ""
    for item in _risky_config(common / "config"):
        h.update(item.encode("utf-8", "replace") + b"\0")
    hooks = common / "hooks"
    try:
        for f in sorted(hooks.rglob("*")):
            if f.is_file():
                h.update(str(f.relative_to(hooks)).encode() + b"\0" + f.read_bytes() + b"\0")
    except OSError:
        h.update(b"hooks-unreadable")
    return h.hexdigest()


def _worktree_gitdir(wt: str) -> Optional[Path]:
    try:
        text = (Path(wt) / ".git").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    g = Path(text[7:].strip())
    return g if g.is_absolute() else (Path(wt) / g)


def worktree_state(wt: str) -> str:
    """The worktree's .git file (which repository its commits go to) as it is now."""
    try:
        return (Path(wt) / ".git").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "<missing>"


def _head_ok(wt: str, branch: str) -> bool:
    """The worktree is still on its own branch (or mid-rebase of it, which detaches
    HEAD for a while)."""
    g = _worktree_gitdir(wt)
    if g is None:
        return False
    try:
        head = (g / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return False
    if head == "ref: refs/heads/" + branch:
        return True
    if re.fullmatch(r"[0-9a-f]{40,64}", head) and any((g / d).exists() for d in ("rebase-merge", "rebase-apply")):
        return True
    return False


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
            have = _git(["rev-parse", "--verify", "--quiet", "refs/heads/" + info["branch"]],
                        cwd=info["repo"], timeout=20).returncode == 0
            if have:
                # This task's branch from an earlier start whose folder is gone: check it
                # out again (-f: its old, now missing, registration is replaced).
                cp = _git(["worktree", "add", "-f", str(wt), info["branch"]], cwd=info["repo"])
            else:
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
                      "worktree_link": worktree_state(str(wt)),
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
    if sub == "stash" and args[:1] in (["list"], ["show"]):
        return None
    if sub == "worktree" and args[:1] == ["list"]:
        return None
    if sub in _ALWAYS_REFUSED:
        return shared
    if sub == "branch":
        flags = {_flag(a) for a in args if a.startswith("-")}
        names = [a for a in args if not a.startswith("-")]
        if flags & _BRANCH_WRITE_FLAGS or any(f.startswith(("--set-upstream", "--delete", "--move",
                                                            "--copy", "--force")) for f in flags):
            return "changing branches other than its own is yours to do; 'git branch' with that option is not run"
        # Names are patterns only with a listing option; otherwise a name makes a branch.
        if names and not (flags & {"-l", "--list", "--contains", "--no-contains", "--merged",
                                   "--no-merged", "--points-at"}):
            return "this session works on its own branch; making other branches is yours to do"
    if sub == "tag" and args:
        flags = {_flag(a) for a in args if a.startswith("-")}
        names = [a for a in args if not a.startswith("-")]
        if flags - _TAG_READ_FLAGS or (names and not flags & _TAG_READ_FLAGS):
            return "tags are shared with your checkout; 'git tag' that changes them is not run"
    if sub == "config" and not any(a in ("--get", "--get-all", "--get-regexp", "--list", "-l")
                                   for a in args):
        return "the repository's config is shared with your checkout; changing it is not run"
    if sub == "checkout":
        # Files only: paths after '--' (from any commit), '.', or --ours/--theirs with paths.
        # Anything that could move HEAD (a branch, -b, --detach, --orphan, a bare '--') is not run.
        switching = {"-b", "-B", "--orphan", "--detach", "--ignore-other-worktrees", "-t", "--track",
                     "-f", "--force", "-m", "--merge", "--guess"}
        flags = {_flag(a) for a in args if a.startswith("-") and a != "--"}
        after = args[args.index("--") + 1:] if "--" in args else []
        files_only = (bool(after) or args == ["."]
                      or (flags & {"--ours", "--theirs"} and [a for a in args if not a.startswith("-")]))
        if flags & switching or not files_only:
            return ("this session stays on its own branch; 'git checkout' is run only for files, "
                    "written 'git checkout -- <files>' (or use 'git restore')")
    if sub == "fetch" and any(_flag(a) == "--refmap" for a in args):
        return "a fetch with its own refmap can write named branches; it is not run"
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


_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1")


def _without_heredoc_bodies(command: str) -> str:
    """`command` with each heredoc's body (the lines up to its closing word)
    replaced by '<<heredoc', read line by line."""
    out, lines, i = [], command.split("\n"), 0
    where: Dict[str, List[int]] = {}               # each line's text -> where it occurs, in order
    for n, text in enumerate(lines):
        where.setdefault(text.strip(), []).append(n)
    while i < len(lines):
        line = lines[i]
        m = _HEREDOC.search(line)
        if not m:
            out.append(line)
            i += 1
            continue
        at = where.get(m.group(2), [])
        k = bisect.bisect_right(at, i)
        end = at[k] if k < len(at) else None
        if end is None:
            out.append(line)
            i += 1
            continue
        out.append(line[:m.start()] + "<<heredoc" + line[m.end():])
        i = end + 1
    return "\n".join(out)


def _git_refusal(command: str, aliases: Optional[Dict[str, str]] = None, depth: int = 0) -> Optional[str]:
    if depth > 3:
        return "this command nests too deeply for Friday to read, so it is not run"
    low = command.lower()
    if _GIT_ENV.search(command):
        return "this session's git stays in its own worktree; setting GIT_* variables is not run"
    if any(h in low.replace("\\", "/") for h in _SHARED_HINTS):
        return ("this touches the repository's shared folder (hooks, config) or points git "
                "elsewhere; it is not run")
    # word by word: a pattern for "the shell's name, then anything" retries from
    # every position of a long run of non-space text
    words = low.split()
    shell_at = next((i for i, w in enumerate(words) if "powershell" in w or "pwsh" in w), None)
    if shell_at is not None and re.search(r"-encodedcommand|-enc\s|-ec\s|-e\s[a-z0-9+/=]{16,}",
                                          " ".join(words[shell_at + 1:]) + " "):
        return "an encoded command cannot be read, so it is not run"
    # A heredoc's body is text, not commands (a commit message may mention 'git push').
    body_free = _without_heredoc_bodies(command)
    for seg in _SEGMENT.split(body_free):
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
        if exe in ("eval", "iex", "invoke-expression", "source") and re.search(r"\bgit\b", seg, re.I):
            return "git run through %s cannot be checked, so it is not run" % exe
        if exe in _SHELLS:
            # bash -c "...", bash -ec "...", cmd /c ..., powershell [-Command] ...: read what it runs.
            rest = tokens[1:]
            ps = exe.startswith(("powershell", "pwsh"))
            payload = None
            for i, t in enumerate(rest):
                tl = t.lower()
                if (tl in ("/c", "/k", "-command", "-commandwithargs", "--command", "-c")
                        or (not ps and re.fullmatch(r"-[a-z]*c[a-z]*", tl))):
                    payload = rest[i + 1:]
                    break
                if ps and tl in ("-file", "-f"):
                    payload = None
                    break
                if ps and not t.startswith("-"):
                    payload = rest[i:]          # PowerShell runs a bare argument as the command
                    break
            if payload:
                why = _git_refusal(" ".join(payload), aliases, depth + 1)
                if why:
                    return why
            continue
        if exe in ("xargs", "find", "parallel", "watch") and re.search(r"\bgit(\.exe|\.cmd|\.bat)?\b", seg, re.I):
            return "git run through %s cannot be checked, so it is not run" % exe
        if exe not in ("git", "git.exe", "git.cmd", "git.bat"):
            continue
        sub, args, opts = _git_subcommand(tokens[1:])
        if "-C" in opts:
            return "this session's git stays in its own worktree; 'git -C' is not run"
        if "--config-env" in opts:
            return "'git --config-env' changes git's settings for this command; it is not run"
        for i, o in enumerate(opts):
            if o == "-c":
                key = (opts[i + 1] if i + 1 < len(opts) else "").split("=", 1)[0].lower()
                if not key.startswith(("color.", "advice.", "column.", "i18n.")):
                    return ("'git -c %s' changes git's settings or aliases for this command; "
                            "it is not run" % key)
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


_WRITERS = {"cp", "mv", "rm", "rmdir", "del", "erase", "rd", "tee", "ln", "mklink", "copy", "move",
            "xcopy", "robocopy", "set-content", "add-content", "out-file", "new-item", "remove-item",
            "copy-item", "move-item", "rename-item", "ni", "ri", "sc", "ac", "chmod", "chown", "touch",
            "sed", "perl", "truncate", "install", "rsync", "dd", "mkdir", "md", "unlink", "attrib",
            "icacls", "takeown"}
_CHDIR = {"cd", "pushd", "set-location", "sl", "chdir", "push-location"}
_REDIRECT = re.compile(r"(?:\d?>{1,2}|>\|)\s*([\"']?)([^\s\"';&|<>]+)\1")


def _writes_dot_git(cmd: str) -> bool:
    """A command that writes into, or moves into, a .git file or folder. Mentions in
    text (a commit message, grep --exclude-dir=.git, find -name .git) are fine."""
    for m in _REDIRECT.finditer(cmd):
        if _DOT_GIT.search("/" + m.group(2).replace("\\", "/") + " "):
            return True
    for seg in _SEGMENT.split(cmd):
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            tokens = seg.split()
        while tokens and ("=" in tokens[0] and not tokens[0].startswith("-")):
            tokens = tokens[1:]
        if not tokens:
            continue
        exe = os.path.basename(tokens[0].replace("\\", "/")).lower()
        exe = exe[:-4] if exe.endswith(".exe") else exe
        if exe in _WRITERS or exe in _CHDIR:
            for t in tokens[1:]:
                if not t.startswith("-") and _DOT_GIT.search("/" + t.replace("\\", "/") + " "):
                    return True
    return False


def _home_spellings(text: str) -> str:
    """Command text with the usual ways of writing the home folder spelled out."""
    home = str(_home())
    for token in ("$env:USERPROFILE", "%USERPROFILE%", "${USERPROFILE}", "$USERPROFILE",
                  "%HOMEDRIVE%%HOMEPATH%", "$env:HOMEDRIVE$env:HOMEPATH", "$HOMEDRIVE$HOMEPATH",
                  "${HOME}", "$HOME", "$env:HOME"):
        text = re.sub(re.escape(token), lambda _m: home, text, flags=re.I)
    text = re.sub(r"(^|[\s\"'=])~(?=[/\\]|$|\s)", lambda m: m.group(1) + home, text)
    return text


def _posix_forms(p: str) -> set:
    """'c:/users/x' also as Git Bash, WSL and Cygwin write it."""
    m = re.match(r"^([a-z]):/(.*)$", p, re.I)
    if not m:
        return set()
    d, rest = m.group(1).lower(), m.group(2)
    return {"/%s/%s" % (d, rest), "/mnt/%s/%s" % (d, rest), "/cygdrive/%s/%s" % (d, rest)}


def _names_checkout(cmd: str, repo: str, wt: str) -> bool:
    text = _fold(_home_spellings(cmd))
    for spelling in sorted({_norm(wt), _real(wt)} | _posix_forms(_norm(wt)) | _posix_forms(_real(wt)),
                           key=len, reverse=True):
        text = text.replace(spelling, "")
    forms = {_norm(repo), _real(repo)}
    short = _short_name(repo)
    if short:
        forms.add(_fold(short))
    for f in list(forms):
        forms |= _posix_forms(f)
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
        e["tripwire"] = "your repository's hooks or git settings changed"
    link = e.get("worktree_link")
    if link and worktree_state(wt) != link:
        e["tripwire"] = "the session's worktree no longer points at its own place in your repository"
    if e.get("branch") and not _head_ok(wt, e["branch"]):
        e["tripwire"] = "the session's worktree left its own branch %s" % e["branch"]
    if e.get("tripwire"):
        return ("%s while this session was running; Friday has stopped running its actions "
                "until you look (%s)" % (e["tripwire"], repo))
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
        # Either spelling decides: as written, and with links, junctions and short names
        # resolved (a link inside the worktree that leads into the checkout is the checkout).
        as_written = _under(_norm(target), _norm(repo)) and not _under(_norm(target), _norm(wt))
        resolved = _under(_real(target), _real(repo)) and not _under(_real(target), _real(wt))
        if writes and (as_written or resolved):
            return ("this session works in its own worktree (%s); your checkout in %s is "
                    "changed only by you" % (wt, repo))
        if writes and _DOT_GIT.search("/" + _real(target)):
            return ("the worktree's .git file and the repository's .git folder are not the "
                    "session's to change")
    if tool_name in ("Bash", "PowerShell"):
        cmd = str(tool_input.get("command") or "")
        if _writes_dot_git(cmd):
            return "a command writing into a .git folder or file is not run for this session"
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
