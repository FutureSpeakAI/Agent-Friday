"""Claude Code PreToolUse guard: the hard rules of AGENTS.md, enforced in code.

Registered as a PreToolUse hook for ``Bash``, ``PowerShell`` and the file
editing tools. Reads the hook payload on stdin and either lets the call
through (exit 0) or blocks it (exit 2, the reason on stderr, which the model
sees). Deterministic rules live here, not in a prompt; the model only ever
handles the judgement left over.

Rules, each testable in both directions (``tests/unit/test_friday_guard_hook.py``):

1. Every pytest call says how many xdist workers it uses: ``-n 0``, ``-n 1``
   or ``-n 2`` (or ``-p no:xdist``). Anything else is refused in every
   checkout, because ``pytest.ini`` says ``-n auto`` and a worktree on an
   older base has no resource guard to cap it: a run of 34 named files once
   fanned out to five workers and took the machine to its memory ceiling. A
   broad run (directories, or nothing, so ``testpaths``) in a Friday checkout
   is refused outright and pointed at ``scripts/run_suite_guarded.py``.
2. ``wsl`` and ``docker`` commands are refused while free memory is under the
   floor; either one boots a multi-gigabyte VM. ``wsl --shutdown``, listing
   and status queries never boot it and stay allowed.
3. The live checkout (the tree the running Friday serves) is not edited,
   switched, reset or used to launch a server. Its ``.claude/`` directory is
   exempt: locks, receipts, settings and agent worktrees live there. The
   deploy lane bypasses this rule with a token file (see ``lane_token``);
   every bypass and every refusal is appended to the audit log.
4. Force-pushes and history rewrites are refused everywhere.
5. One machine, one memory budget: three pytest runs that each obeyed rule 1
   once pushed the commit charge past 90 % together. Any pytest call needs
   ``pytest_single_file_floor_gb`` of free memory, and while free memory is
   under ``pytest_concurrency_floor_gb`` a new run is refused if another
   pytest process is already running anywhere on the machine, unless it is a
   single named file at ``-n 0``.

Configuration is per machine and never in the tree:
``~/.claude/friday-desktop.local.json`` (or ``$FRIDAY_GUARD_CONFIG``), keys
``live_checkout``, ``min_free_ram_gb``, ``min_free_disk_gb``,
``deploy_lane_token``, ``deploy_lane_ttl_hours``, ``audit_log``,
``pytest_concurrency_floor_gb``, ``pytest_single_file_floor_gb``. Without a
config file rules 1, 2, 4 and 5 still apply with the defaults below; rule 3
needs ``live_checkout`` and is otherwise inactive.

A fault inside this script must not brick every Claude Code session on the
machine (it runs for every project), so an unexpected exception lets the call
through with the traceback on stderr and in the audit log (exit 1, which
Claude Code reports but does not block on).
"""
from __future__ import annotations

import ctypes
import datetime as _dt
import json
import os
import posixpath
import re
import sys
import traceback
from pathlib import Path

# The same floors as pytest_resource_guard.py; test_friday_guard_hook.py pins them.
DEFAULTS = {
    "live_checkout": None,
    "min_free_ram_gb": 12.0,
    "min_free_disk_gb": 20.0,
    "deploy_lane_token": None,      # default: <live_checkout>/.claude/DEPLOY_LANE
    "deploy_lane_ttl_hours": 4.0,
    "audit_log": None,              # default: <live_checkout>/.claude/receipts/guard-audit.log
    "pytest_concurrency_floor_gb": 8.0,   # under this, no second pytest on the machine
    "pytest_single_file_floor_gb": 4.0,   # under this, no pytest at all
}
PYTEST_PROCESS_MARKERS = ("pytest", "run_suite_guarded")
CONFIG_PATH = Path.home() / ".claude" / "friday-desktop.local.json"
FRIDAY_MARKER = "pytest_resource_guard.py"
FRIDAY_PACKAGE = ("src", "agent_friday")   # present on every base, unlike the marker
GUARD_SCRIPT = "scripts/run_suite_guarded.py"
MAX_EXPLICIT_WORKERS = 2

SHELL_TOOLS = {"Bash", "PowerShell"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# pytest options that take a separate value, so the value is not a target.
PYTEST_VALUE_OPTS = {
    "-n", "-k", "-m", "-p", "-o", "-c", "-W", "-r", "--tb", "--dist", "--rootdir",
    "--timeout", "--junitxml", "--junit-xml", "--maxfail", "--durations",
    "--confcutdir", "--basetemp", "--deselect", "--ignore", "--ignore-glob",
    "--override-ini", "--log-level", "--capture", "--color", "--import-mode",
    "--numprocesses", "--maxprocesses", "--log-file", "--result-log", "--cov",
    "--cov-report", "--html", "--css", "-x",  # -x takes no value but is harmless here
}
PYTEST_NO_RUN_FLAGS = {"--collect-only", "--co", "--version", "--help", "-h",
                       "--fixtures", "--markers", "--fixtures-per-test"}

WSL_NAMES = {"wsl", "wsl.exe", "wslg", "wslg.exe"}
DOCKER_NAMES = {"docker", "docker.exe", "docker-compose", "docker-compose.exe",
                "docker desktop", "docker desktop.exe", "com.docker.cli", "com.docker.cli.exe"}
WSL_SAFE = {"--shutdown", "--list", "-l", "--status", "--version", "-v", "--help",
            "--terminate", "-t", "--unregister"}

GIT_WORKTREE_MUTATORS = {
    "checkout", "switch", "reset", "merge", "rebase", "pull", "clean", "restore",
    "cherry-pick", "revert", "am", "apply", "mv", "rm", "commit", "add",
    "update-ref", "symbolic-ref", "stash",
}
GIT_STASH_READONLY = {"list", "show"}
SHELL_WRITERS = {"sed", "perl", "tee", "rm", "mv", "cp", "touch", "truncate", "ln",
                 "patch", "rmdir", "install", "dd", "unzip", "tar", "rsync"}
POWERSHELL_WRITERS = {"set-content", "add-content", "out-file", "remove-item", "move-item",
                      "copy-item", "new-item", "rename-item", "clear-content", "ri", "rm",
                      "del", "erase", "mv", "cp", "sc", "ac", "ni", "rni", "rmdir", "rd"}
SERVER_SCRIPTS = {"server.py", "friday_tray.py", "launch_friday.py", "start_friday.py",
                  "launch_friday.bat", "start_friday.bat", "agentfriday.exe", "agentfriday"}
SERVER_MODULES = {"agent_friday.server", "agent_friday.friday_tray", "agent_friday"}
SERVER_PROGRAMS = {"waitress-serve", "gunicorn"}
WRAPPERS = {"sudo", "nohup", "time", "exec", "env", "start", "start-process", "call", "&", "."}
NESTED_SHELLS = {"bash", "sh", "zsh", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
                 "cmd", "cmd.exe"}


# ── configuration and probes ─────────────────────────────────────────────────

def load_config(path: Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    p = Path(os.environ.get("FRIDAY_GUARD_CONFIG") or path or CONFIG_PATH)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if cfg["live_checkout"]:
        live = norm_path(str(cfg["live_checkout"]))
        cfg["live_checkout"] = live
        cfg["deploy_lane_token"] = cfg["deploy_lane_token"] or live + "/.claude/DEPLOY_LANE"
        cfg["audit_log"] = cfg["audit_log"] or live + "/.claude/receipts/guard-audit.log"
    return cfg


def free_ram_gb() -> float | None:
    """Free physical memory in GB, or None when it cannot be read."""
    if sys.platform == "win32":
        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = _MS()
        ms.dwLength = ctypes.sizeof(_MS)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return ms.ullAvailPhys / 1024 ** 3
        except Exception:
            return None
        return None
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024 ** 3
    except (ValueError, OSError, AttributeError):
        return None


def running_pytest_processes() -> list[tuple[int, str]] | None:
    """(pid, command line) of every pytest or guarded-runner process on the
    machine, this process excluded; None when the listing failed."""
    import subprocess
    me = os.getpid()
    try:
        if sys.platform == "win32":
            script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
                      "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }")
            p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, timeout=15, errors="replace")
        else:
            p = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=15,
                               errors="replace")
        if p.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    out = []
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _, cmd = line.partition("|") if sys.platform == "win32" else line.partition(" ")
        pid_s = pid_s.strip()
        if not pid_s.isdigit() or int(pid_s) == me:
            continue
        if is_pytest_process(cmd):
            out.append((int(pid_s), cmd.strip()[:160]))
    return out


SHELL_WRAPPERS = {"bash", "sh", "zsh", "cmd", "powershell", "pwsh", "conhost", "wsl"}


def is_pytest_process(cmdline: str) -> bool:
    """A process that IS a test run: python running pytest or the guarded
    runner. A shell whose command text merely contains the word (the bash or
    cmd wrapper that launched the run, or a hook reading the process list) is
    not one; its pytest child is counted on its own."""
    low = cmdline.lower()
    if not any(m in low for m in PYTEST_PROCESS_MARKERS) or "friday_guard.py" in low:
        return False
    toks = tokens(cmdline)
    return bool(toks) and base_name(toks[0]) not in SHELL_WRAPPERS


def lane_token(cfg: dict, now: float | None = None) -> str | None:
    """The first line of a live deploy-lane token, or None when there is none.

    The token is a file the deploy lane writes when it starts a deploy and
    removes when it finishes. It expires after ``deploy_lane_ttl_hours`` so a
    forgotten file cannot leave the live checkout open for days.
    """
    path = cfg.get("deploy_lane_token")
    if not path:
        return None
    try:
        p = Path(path)
        st = p.stat()
        text = p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    now = now if now is not None else _dt.datetime.now().timestamp()
    if now - st.st_mtime > float(cfg.get("deploy_lane_ttl_hours") or 0) * 3600:
        return None
    return text.splitlines()[0][:120]


def audit(cfg: dict, line: str) -> None:
    path = cfg.get("audit_log")
    if not path:
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(_dt.datetime.now().isoformat(timespec="seconds") + " " + line + "\n")
    except OSError:
        pass


# ── paths ────────────────────────────────────────────────────────────────────

def norm_path(p: str, base: str | None = None) -> str:
    """One spelling for a path: forward slashes, drive letter lower-cased,
    ``/c/x`` (Git Bash) and ``~`` expanded, relative paths resolved against
    ``base``. Case-folded on Windows."""
    s = p.strip().strip("'\"")
    if s == "~" or s.startswith("~/") or s.startswith("~\\"):
        s = str(Path.home()) + s[1:]
    s = s.replace("\\", "/")
    m = re.match(r"^/([a-zA-Z])(/|$)", s)
    if m:
        s = m.group(1) + ":" + s[2:]
    if not (re.match(r"^[a-zA-Z]:", s) or s.startswith("/")):
        if base:
            s = base.rstrip("/") + "/" + s
    s = posixpath.normpath(s)
    if re.match(r"^[a-zA-Z]:$", s):
        s += "/"
    if sys.platform == "win32":
        s = s.lower()
    return s


UNKNOWN_DIR = "/__unknown__"   # a directory named by a shell variable: not resolvable here


def is_variable(tok: str) -> bool:
    """A path that starts with a shell variable cannot be resolved by this
    script, so it is never taken to be inside the live checkout."""
    t = tok.strip().strip("'\"")
    return t.startswith(("$", "%", "${"))


def under(path: str, root: str) -> bool:
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def in_live(path: str, cfg: dict) -> bool:
    """Inside the live checkout and outside its exempt ``.claude/`` directory."""
    live = cfg.get("live_checkout")
    if not live:
        return False
    return under(path, live) and not under(path, live + "/.claude")


def find_friday_root(start: str) -> str | None:
    p = Path(start)
    for d in [p, *p.parents]:
        if (d / FRIDAY_MARKER).is_file() or d.joinpath(*FRIDAY_PACKAGE).is_dir():
            return norm_path(str(d))
    return None


# ── command parsing ──────────────────────────────────────────────────────────

def strip_heredocs(text: str) -> str:
    """Remove heredoc bodies so a commit message cannot look like a command."""
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        out.append(line)
        i += 1
        if m:
            term = m.group(1)
            while i < len(lines) and lines[i].strip() != term:
                i += 1
            i += 1
    return "\n".join(out)


def split_segments(text: str) -> list[str]:
    """Split a shell line on ``&&``, ``||``, ``;``, ``|`` and newlines, outside quotes."""
    segs, buf, quote, i = [], [], None, 0
    while i < len(text):
        c = text[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
        elif c == "\n" or c == ";":
            segs.append("".join(buf)); buf = []
        elif text.startswith("&&", i) or text.startswith("||", i):
            segs.append("".join(buf)); buf = []; i += 1
        elif c == "|":
            segs.append("".join(buf)); buf = []
        elif c == "&" and not text.startswith("&>", i) and not (i > 0 and text[i - 1] == ">"):
            segs.append("".join(buf)); buf = []
        else:
            buf.append(c)
        i += 1
    segs.append("".join(buf))
    return [s.strip() for s in segs if s.strip()]


def tokens(segment: str) -> list[str]:
    """Whitespace tokens honouring single and double quotes; no backslash
    escapes, so Windows paths survive."""
    out, buf, quote = [], [], None
    for c in segment:
        if quote:
            if c == quote:
                quote = None
            else:
                buf.append(c)
        elif c in "'\"":
            quote = c
        elif c.isspace():
            if buf:
                out.append("".join(buf)); buf = []
        else:
            buf.append(c)
    if buf:
        out.append("".join(buf))
    return out


def base_name(tok: str) -> str:
    b = tok.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return b[:-4] if b.endswith(".exe") else b


def command_words(toks: list[str]) -> list[str]:
    """Drop env assignments, wrappers and PowerShell call operators so the
    first element is the program being run."""
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t) or base_name(t) in WRAPPERS:
            i += 1
            continue
        if t.lower().startswith("-") and i > 0 and base_name(toks[i - 1]) in {"env", "start-process"}:
            i += 1
            continue
        break
    return toks[i:]


def nested_scripts(words: list[str]) -> list[str]:
    """Command strings handed to an inner shell (``bash -c``, ``powershell -Command``,
    ``cmd /c``), so a rule cannot be dodged by quoting."""
    if not words or base_name(words[0]) not in NESTED_SHELLS:
        return []
    found = []
    for i, w in enumerate(words[1:], 1):
        lw = w.lower()
        if lw in {"-c", "-command", "-comm", "/c", "/k", "-encodedcommand", "-e"} and i + 1 < len(words):
            found.append(" ".join(words[i + 1:]))
            break
    return found


class Segment:
    def __init__(self, text: str, cwd: str):
        self.text = text
        self.cwd = cwd
        self.toks = tokens(text)
        self.words = command_words(self.toks)
        self.prog = base_name(self.words[0]) if self.words else ""

    def cd_target(self) -> str | None:
        if self.prog in {"cd", "pushd", "set-location", "sl", "push-location", "chdir"}:
            args = [w for w in self.words[1:] if not w.startswith("-")]
            if not args:
                return norm_path("~")
            return UNKNOWN_DIR if is_variable(args[0]) else norm_path(args[0], self.cwd)
        return None


def walk(command: str, cwd: str) -> list[Segment]:
    """Segments in order, each knowing the directory it runs in."""
    out, cur = [], norm_path(cwd)
    for text in split_segments(strip_heredocs(command)):
        seg = Segment(text, cur)
        out.append(seg)
        for inner in nested_scripts(seg.words):
            out.extend(walk(inner, cur))
        target = seg.cd_target()
        if target:
            cur = target
    return out


# ── rule 1: broad pytest ─────────────────────────────────────────────────────

def pytest_args(words: list[str]) -> list[str] | None:
    """The arguments handed to pytest, or None when the segment is not a pytest run."""
    if not words:
        return None
    if base_name(words[0]) in {"pytest", "py.test"}:
        return words[1:]
    if base_name(words[0]).startswith("python") or base_name(words[0]) == "py":
        for i, w in enumerate(words[1:], 1):
            if w == "-m" and i + 1 < len(words) and words[i + 1] == "pytest":
                return words[i + 2:]
            if not w.startswith("-"):
                break
    return None


def is_broad_pytest(args: list[str]) -> bool:
    if any(a in PYTEST_NO_RUN_FLAGS for a in args):
        return False
    targets, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a.startswith("-"):
            if "=" not in a and a in PYTEST_VALUE_OPTS and a != "-x":
                skip = True
            continue
        if re.fullmatch(r"\d?>>?|&>|<", a):
            skip = True  # a redirection operator: its target is a file, not a test
            continue
        if ">" in a or "<" in a:
            continue  # a compact redirection such as 2>&1 or >out.txt
        targets.append(a)
    if not targets:
        return True
    return any("::" not in t and not t.lower().endswith(".py") for t in targets)


def xdist_workers(args: list[str]):
    """What the call says about workers: an int, "auto"/"logical", "off" for
    ``-p no:xdist``, or None when it says nothing (so pytest.ini decides).
    The last statement wins, as it does for pytest."""
    value = None
    for i, a in enumerate(args):
        if a in {"-n", "--numprocesses"} and i + 1 < len(args):
            value = args[i + 1]
        elif a.startswith("--numprocesses="):
            value = a.split("=", 1)[1]
        elif a.startswith("-n") and len(a) > 2 and a[2] in "=0123456789al":
            value = a[2:].lstrip("=")
        elif (a == "-p" and i + 1 < len(args) and args[i + 1] == "no:xdist") or a == "-pno:xdist":
            value = "off"
    if value is None or value == "off":
        return value
    return int(value) if value.isdigit() else value.lower()


def check_pytest(segs: list[Segment]) -> str | None:
    for seg in segs:
        args = pytest_args(seg.words)
        if args is None or any(a in PYTEST_NO_RUN_FLAGS for a in args):
            continue
        if is_broad_pytest(args) and find_friday_root(seg.cwd) is not None:
            return ("Full-suite pytest is blocked here. Run it through the guard script:\n"
                    f"    python {GUARD_SCRIPT} [pytest args]\n"
                    "It checks the free-memory and free-disk floors, takes SUITE_LOCK, caps xdist "
                    "while the local model seat is up, and writes a receipt with pytest's real exit "
                    "code. Named test files run directly with an explicit worker count: "
                    f"pytest tests/unit/test_x.py -n {MAX_EXPLICIT_WORKERS}")
        n = xdist_workers(args)
        if n == "off" or (isinstance(n, int) and 0 <= n <= MAX_EXPLICIT_WORKERS):
            continue
        said = "it says nothing about workers" if n is None else f"it says -n {n}"
        return (f"pytest is blocked: {said}. Every pytest call carries -n 0, -n 1 or "
                f"-n {MAX_EXPLICIT_WORKERS} (or -p no:xdist), because pytest.ini defaults to -n auto "
                "and a checkout on an older base has no guard to cap it. Full runs go through "
                f"python {GUARD_SCRIPT}.")
    return None


# ── rule 5: one machine, one memory budget ───────────────────────────────────

def pytest_targets(args: list[str]) -> list[str]:
    """The test files and node ids a call names (options and their values,
    redirections excluded)."""
    targets, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a.startswith("-"):
            if "=" not in a and a in PYTEST_VALUE_OPTS and a != "-x":
                skip = True
            continue
        if re.fullmatch(r"\d?>>?|&>|<", a):
            skip = True
            continue
        if ">" in a or "<" in a:
            continue
        targets.append(a)
    return targets


def is_single_file_serial(args: list[str]) -> bool:
    targets = pytest_targets(args)
    if len(targets) != 1:
        return False
    t = targets[0]
    if "::" not in t and not t.lower().endswith(".py"):
        return False
    return xdist_workers(args) in (0, "off")


def check_pytest_load(segs: list[Segment], cfg: dict, ram: float | None, running) -> str | None:
    """Refuse a pytest call the machine has no room for. ``running`` is the
    list from ``running_pytest_processes()`` (None when it could not be read)."""
    calls = [pytest_args(s.words) for s in segs]
    calls = [a for a in calls if a is not None and not any(x in PYTEST_NO_RUN_FLAGS for x in a)]
    if not calls:
        return None
    single_floor = float(cfg["pytest_single_file_floor_gb"])
    conc_floor = float(cfg["pytest_concurrency_floor_gb"])
    if ram is None:
        return f"pytest is blocked: free memory could not be read, and any run needs {single_floor:.0f} GB."
    if ram < single_floor:
        return (f"pytest is blocked: free memory is {ram:.1f} GB and any run needs at least "
                f"{single_floor:.0f} GB. Wait for room, or stop what is holding it.")
    if ram >= conc_floor:
        return None
    if all(is_single_file_serial(a) for a in calls):
        return None
    if running is None:
        return (f"pytest is blocked: free memory is {ram:.1f} GB, under the {conc_floor:.0f} GB "
                "floor for a second run, and the machine's process list could not be read. "
                "A single named file at -n 0 is the only run allowed without that check.")
    if running:
        others = "; ".join(f"pid {pid}: {cmd[:80]}" for pid, cmd in running[:3])
        return (f"pytest is blocked: free memory is {ram:.1f} GB, under the {conc_floor:.0f} GB "
                f"floor, and {len(running)} pytest process(es) already run on this machine "
                f"({others}). Three concurrent runs once pushed the commit charge past 90 %. "
                "Wait for them to finish, or run a single named file at -n 0.")
    return None


# ── rule 2: wsl / docker under the memory floor ──────────────────────────────

def vm_command(seg: Segment) -> str | None:
    """'wsl' or 'docker' when the segment starts one of them, else None."""
    if not seg.words:
        return None
    joined = " ".join(seg.words[:2]).lower()
    prog = seg.prog
    if prog in WSL_NAMES:
        rest = [w.lower() for w in seg.words[1:]]
        if rest and rest[0] in WSL_SAFE:
            return None
        return "wsl"
    if prog in DOCKER_NAMES or "docker desktop" in joined or "docker desktop" in seg.words[0].lower():
        return "docker"
    return None


def check_vm(segs: list[Segment], cfg: dict, ram: float | None) -> str | None:
    floor = float(cfg["min_free_ram_gb"])
    for seg in segs:
        kind = vm_command(seg)
        if kind is None:
            continue
        if ram is None:
            return f"{kind} is blocked: free memory could not be read, and the floor is {floor:.0f} GB."
        if ram < floor:
            return (f"{kind} is blocked: free memory is {ram:.1f} GB and the floor is {floor:.0f} GB. "
                    f"Either one boots a VM that takes gigabytes. Free memory first "
                    f"(wsl --shutdown is always allowed) and retry.")
    return None


# ── rule 3: the live checkout ────────────────────────────────────────────────

def path_args(seg: Segment, start: int = 1) -> list[str]:
    return [norm_path(w, seg.cwd) for w in seg.words[start:]
            if not w.startswith("-") and not is_variable(w)]


def redirect_targets(seg: Segment) -> list[str]:
    """Files a segment's redirections write to. Works on tokens, so text inside
    quotes ("main -> branch") is not a redirection; a target that is a shell
    variable cannot be resolved and is not treated as a live-checkout write."""
    out, toks = [], seg.toks
    for i, tok in enumerate(toks):
        m = re.fullmatch(r"(\d?>>?|&>)(.*)", tok)
        if not m:
            continue
        t = m.group(2) or (toks[i + 1] if i + 1 < len(toks) else "")
        if not t or t.startswith(("&", "$", "%")) or t.lower() in {"/dev/null", "nul", "$null"}:
            continue
        out.append(norm_path(t, seg.cwd))
    return out


def live_mutation(seg: Segment, cfg: dict) -> str | None:
    """Why this segment changes the live checkout, or None."""
    if not cfg.get("live_checkout") or not seg.words:
        return None
    prog, words = seg.prog, seg.words
    here = in_live(seg.cwd, cfg)

    if prog == "git":
        i, gdir = 1, seg.cwd
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-C" and i + 1 < len(words):
                gdir = UNKNOWN_DIR if is_variable(words[i + 1]) else norm_path(words[i + 1], seg.cwd)
                i += 2
                continue
            if words[i].startswith("-C") and len(words[i]) > 2:
                gdir = UNKNOWN_DIR if is_variable(words[i][2:]) else norm_path(words[i][2:], seg.cwd)
            i += 1
        sub = words[i] if i < len(words) else ""
        if sub in GIT_WORKTREE_MUTATORS and in_live(gdir, cfg):
            if sub == "stash" and i + 1 < len(words) and words[i + 1] in GIT_STASH_READONLY:
                return None
            return f"git {sub} in the live checkout"
        return None

    launch = server_launch(seg, cfg)
    if launch:
        return launch

    if prog in SHELL_WRITERS or prog in POWERSHELL_WRITERS:
        targets = path_args(seg)
        if prog in {"cp", "mv", "copy-item", "move-item", "rename-item"}:
            last = [w for w in words[1:] if not w.startswith("-")]
            targets = [] if not last or is_variable(last[-1]) else [norm_path(last[-1], seg.cwd)]
        if prog == "sed" and "-i" not in words and not any(w.startswith("-i") for w in words[1:]):
            targets = []
        if prog == "perl" and not any(w.startswith("-i") for w in words[1:]):
            targets = []
        if any(in_live(t, cfg) for t in targets):
            return f"{prog} writing into the live checkout"

    for t in redirect_targets(seg):
        if in_live(t, cfg):
            return "a redirect writing into the live checkout"

    if here and prog in {"python", "python3", "py", "python.exe"}:
        if re.search(r"open\([^)]*['\"][wa]", seg.text) or re.search(r"write_(text|bytes)\(", seg.text):
            return "a Python write from the live checkout"
    return None


def server_launch(seg: Segment, cfg: dict) -> str | None:
    """Why this segment starts a Friday server with the live checkout's
    interpreter or tree, or None. Decided by what the command runs (the
    script, module or program), never by words in its arguments: a test file
    named test_published_server.py is not a server. A second Friday server
    can reap the live one's model seat, so the launch counts as live when the
    interpreter is the live venv, the entry point lives in the live tree, or
    the working directory is the live tree."""
    words = seg.words
    if not words:
        return None
    prog = seg.prog
    here = in_live(seg.cwd, cfg)
    interp_live = in_live(norm_path(words[0], seg.cwd), cfg)

    if prog in SERVER_PROGRAMS or (prog == "flask" and "run" in words[1:]):
        return "a server launch from the live checkout" if (here or interp_live) else None

    if prog in SERVER_SCRIPTS:   # the entry point run directly: AgentFriday.exe, a .bat, a script
        return "a server launch from the live checkout" if (here or in_live(norm_path(words[0], seg.cwd), cfg)) else None

    if not (prog.startswith("python") or prog in {"py", "pythonw"}):
        return None
    i = 1
    while i < len(words):
        w = words[i]
        if w == "-m":
            module = words[i + 1] if i + 1 < len(words) else ""
            if module in SERVER_MODULES and (here or interp_live):
                return f"a Friday server launch (-m {module}) with the live interpreter or tree"
            return None
        if w == "-c":
            return None
        if w.startswith("-"):
            i += 1
            continue
        script = w.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if script in SERVER_SCRIPTS and (here or interp_live or in_live(norm_path(w, seg.cwd), cfg)):
            return f"a Friday server launch ({script}) with the live interpreter or tree"
        return None
    return None


def check_live_shell(segs: list[Segment], cfg: dict) -> str | None:
    for seg in segs:
        why = live_mutation(seg, cfg)
        if why:
            return live_message(why, cfg)
    return None


def live_message(why: str, cfg: dict) -> str:
    return (f"Blocked: {why}. The live checkout ({cfg['live_checkout']}) is what the running "
            "Friday serves; it is never edited, switched, reset or used to launch a server. "
            "Work in a worktree (git worktree add), and leave deploys to the program lead's "
            f"lane, whose token file ({cfg['deploy_lane_token']}) is the one bypass.")


def check_live_edit(tool_input: dict, cfg: dict) -> str | None:
    if not cfg.get("live_checkout"):
        return None
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if raw and in_live(norm_path(str(raw)), cfg):
        return live_message(f"editing {raw}", cfg)
    return None


# ── rule 4: history rewrites ─────────────────────────────────────────────────

def check_history(segs: list[Segment]) -> str | None:
    for seg in segs:
        words = seg.words
        if not words:
            continue
        if seg.prog in {"git-filter-repo", "filter-repo"}:
            return "git filter-repo rewrites history and is blocked."
        if seg.prog != "git":
            continue
        rest = [w for w in words[1:] if not (w.startswith("-") and w != "-f")]
        opts = [w for w in words[1:] if w.startswith("-")]
        sub = rest[0] if rest else ""
        if sub in {"filter-branch", "filter-repo"}:
            return f"git {sub} rewrites history and is blocked."
        if sub == "replace":
            return "git replace rewrites history and is blocked."
        if sub == "commit" and "--amend" in opts:
            return "git commit --amend rewrites history and is blocked; make a new commit."
        if sub == "reflog" and "expire" in rest:
            return "git reflog expire discards history and is blocked."
        if sub == "push":
            for o in opts:
                if o.startswith("--force") or o in {"--mirror", "--delete", "-d"}:
                    return f"git push {o} is blocked: force-pushes, mirrors and remote deletes rewrite public history."
                if re.match(r"^-[a-zA-Z]*f[a-zA-Z]*$", o):
                    return "git push -f is blocked: force-pushes rewrite public history."
            for r in rest[1:]:
                if r.startswith("+") or r.startswith(":"):
                    return f"git push {r} is blocked: a forced or deleting refspec rewrites public history."
    return None


# ── decision ─────────────────────────────────────────────────────────────────

_PROBE = object()


def decide(payload: dict, cfg: dict, ram=_PROBE, now: float | None = None, running=_PROBE) -> tuple[bool, str]:
    """(allowed, reason). ``ram`` is free memory in GB (None when unreadable; left out to
    probe); ``now`` a timestamp for token expiry; ``running`` the other pytest processes
    (None when unreadable; left out to probe)."""
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()

    if tool in EDIT_TOOLS:
        why = check_live_edit(tool_input, cfg)
        if why:
            token = lane_token(cfg, now)
            if token:
                audit(cfg, f"BYPASS lane={token!r} tool={tool} path={tool_input.get('file_path') or tool_input.get('notebook_path')}")
                return True, ""
            audit(cfg, f"BLOCK tool={tool} path={tool_input.get('file_path') or tool_input.get('notebook_path')}")
            return False, why
        return True, ""

    if tool not in SHELL_TOOLS:
        return True, ""

    command = str(tool_input.get("command") or "")
    if not command.strip():
        return True, ""
    segs = walk(command, cwd)

    why = check_history(segs)
    if why:
        audit(cfg, f"BLOCK history tool={tool} cwd={cwd} cmd={command[:600]!r}")
        return False, why

    why = check_pytest(segs)
    if why:
        audit(cfg, f"BLOCK pytest tool={tool} cwd={cwd} cmd={command[:600]!r}")
        return False, why

    if any(pytest_args(s.words) is not None for s in segs):
        ram = free_ram_gb() if ram is _PROBE else ram
        if ram is not None and ram < float(cfg["pytest_concurrency_floor_gb"]) and running is _PROBE:
            running = running_pytest_processes()
        elif running is _PROBE:
            running = []
        why = check_pytest_load(segs, cfg, ram, running)
        if why:
            audit(cfg, f"BLOCK pytest-load tool={tool} ram={ram} running={len(running or [])} cmd={command[:600]!r}")
            return False, why

    if any(vm_command(s) for s in segs):
        ram = free_ram_gb() if ram is _PROBE else ram
        why = check_vm(segs, cfg, ram)
        if why:
            audit(cfg, f"BLOCK vm tool={tool} ram={ram} cmd={command[:600]!r}")
            return False, why

    why = check_live_shell(segs, cfg)
    if why:
        token = lane_token(cfg, now)
        if token:
            audit(cfg, f"BYPASS lane={token!r} tool={tool} cwd={cwd} cmd={command[:600]!r}")
            return True, ""
        audit(cfg, f"BLOCK live tool={tool} cwd={cwd} cmd={command[:600]!r}")
        return False, why

    return True, ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        cfg = load_config()
        allowed, reason = decide(payload, cfg)
    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write("[friday_guard] internal error, call allowed:\n" + tb)
        try:
            audit(load_config(), "ERROR " + tb.replace("\n", " | ")[:1000])
        except Exception:
            pass
        return 1
    if allowed:
        return 0
    sys.stderr.write("[friday_guard] " + reason + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
