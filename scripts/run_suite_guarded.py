"""The one door for a full test run on a machine that also serves the live Friday.

    python scripts/run_suite_guarded.py [--tree DIR] [--workers N] [--wait]
                                        [--session NAME] [pytest args...]

What it does, in order, and refuses to do otherwise:

1. Floors. A run starts only with at least ``MIN_FREE_RAM_GB`` of free memory
   and ``MIN_FREE_DISK_GB`` of free disk (from ``pytest_resource_guard.py``;
   a private config may raise them). Below a floor it exits 4 and says which,
   or with ``--wait`` re-checks every minute.
2. The lock. One suite at a time, machine-wide: ``SUITE_LOCK`` is claimed by
   holder id with an exclusive create. A lock whose holder process is gone is
   stale and taken over; a live holder means exit 5 (or waiting). The lock is
   released only if it still carries this run's holder id.
3. Workers. xdist runs with at most ``max_workers_with_seat`` workers while
   the local model seat answers on ``seat_port``, and at most
   ``pytest_resource_guard.MAX_WORKERS`` otherwise.
4. The run. pytest runs as a child process with its output streamed to a log.
   If free memory or disk falls under the abort floors mid-run the process
   tree is killed and the run is marked aborted.
5. The receipt. ``<receipts_dir>/<full tree sha>/suite.json`` is written from
   the child's real exit code: ``ok`` is ``exit_code == 0`` and nothing else.
   This process exits with that same code, so a wrapper cannot mask it.

Machine-specific values come from ``~/.claude/friday-desktop.local.json``
(or ``$FRIDAY_GUARD_CONFIG``): ``live_checkout``, ``suite_lock``,
``receipts_dir``, ``seat_port``, ``max_workers_with_seat``,
``max_workers_without_seat``, ``min_free_ram_gb``, ``min_free_disk_gb``,
``abort_free_ram_gb``, ``abort_free_disk_gb``. Defaults keep the script
usable from any clone: the lock and receipts land under ``<tree>/.claude/``.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as _dt
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import pytest_resource_guard as guard  # noqa: E402  (the floors live there and nowhere else)

DEFAULTS = {
    "live_checkout": None,
    "suite_lock": None,               # default: <live_checkout or tree>/.claude/SUITE_LOCK
    "receipts_dir": None,             # default: <live_checkout or tree>/.claude/receipts
    "seat_port": 8090,
    "max_workers_with_seat": 2,
    "max_workers_without_seat": guard.MAX_WORKERS,
    "min_free_ram_gb": guard.MIN_FREE_RAM_GB,
    "min_free_disk_gb": guard.MIN_FREE_DISK_GB,
    "abort_free_ram_gb": 3.0,
    "abort_free_disk_gb": 8.0,
}
CONFIG_PATH = Path.home() / ".claude" / "friday-desktop.local.json"
DEFAULT_ARGS = ["tests/unit", "tests/api", "-q"]
EXIT_REFUSED_FLOOR = 4
EXIT_REFUSED_LOCK = 5
STILL_ACTIVE = 259


def load_config(path: Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    p = Path(os.environ.get("FRIDAY_GUARD_CONFIG") or path or CONFIG_PATH)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def resolve_paths(cfg: dict, tree: Path) -> dict:
    home = Path(cfg["live_checkout"]) if cfg.get("live_checkout") else tree
    cfg = dict(cfg)
    cfg["suite_lock"] = Path(cfg["suite_lock"] or home / ".claude" / "SUITE_LOCK")
    cfg["receipts_dir"] = Path(cfg["receipts_dir"] or home / ".claude" / "receipts")
    return cfg


# ── probes ───────────────────────────────────────────────────────────────────

def seat_up(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def floor_refusal(cfg: dict, tree: Path) -> str | None:
    ram, disk = guard.free_ram_gb(), guard.free_disk_gb(tree)
    short = []
    if ram is None or ram < float(cfg["min_free_ram_gb"]):
        short.append(f"free memory is {ram if ram is None else round(ram, 1)} GB, floor {float(cfg['min_free_ram_gb']):.0f} GB")
    if disk is None or disk < float(cfg["min_free_disk_gb"]):
        short.append(f"free disk is {disk if disk is None else round(disk, 1)} GB, floor {float(cfg['min_free_disk_gb']):.0f} GB")
    return "; ".join(short) or None


def worker_count(requested: int | None, seat: bool, cfg: dict) -> int:
    cap = int(cfg["max_workers_with_seat"] if seat else cfg["max_workers_without_seat"])
    if requested is None:
        return cap
    return max(0, min(int(requested), cap))


# ── the lock ─────────────────────────────────────────────────────────────────

def parse_lock(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def lock_state(path: Path) -> tuple[str, dict]:
    """('free' | 'stale' | 'held', fields). A lock without a pid is treated as
    held: another runner's format is not evidence that it stopped."""
    try:
        fields = parse_lock(path.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return "free", {}
    except OSError:
        return "held", {}
    pid = fields.get("pid", "")
    if pid.isdigit():
        return ("held" if pid_alive(int(pid)) else "stale"), fields
    return "held", fields


def claim_lock(path: Path, holder: str, session: str, tree: Path, cmd: list[str]) -> bool:
    body = (f"holder: {holder}\nsession: {session}\npid: {os.getpid()}\n"
            f"claimed: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"suite: {' '.join(cmd)} in {tree}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    return True


def release_lock(path: Path, holder: str) -> bool:
    """Delete the lock only if it is still ours. Never ``rm -f`` someone else's."""
    try:
        if parse_lock(path.read_text(encoding="utf-8", errors="replace")).get("holder") == holder:
            path.unlink()
            return True
    except OSError:
        pass
    return False


# ── the run ──────────────────────────────────────────────────────────────────

def git_sha(tree: Path) -> str:
    p = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True)
    sha = (p.stdout or "").strip()
    return sha if len(sha) == 40 else "no-git"


def kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def run_pytest(cmd: list[str], tree: Path, log: Path, cfg: dict, poll_s: float = 15.0) -> tuple[int, str | None, str]:
    """Run pytest, stream output to ``log`` and stdout, abort under the abort
    floors. Returns (exit_code, abort_reason, tail)."""
    log.parent.mkdir(parents=True, exist_ok=True)
    tail: list[str] = []
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(cmd, cwd=str(tree), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env)

        def pump():
            for line in proc.stdout:
                fh.write(line); fh.flush()
                try:
                    sys.stdout.write(line); sys.stdout.flush()
                except (UnicodeError, OSError):
                    # A restricted console encoding or closed output pipe must
                    # not stop draining the child or its UTF-8 log.
                    pass
                tail.append(line)
                if len(tail) > 60:
                    del tail[0]

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        abort = None
        while proc.poll() is None:
            t.join(poll_s)
            if proc.poll() is not None:
                break
            ram, disk = guard.free_ram_gb(), guard.free_disk_gb(tree)
            if (ram is not None and ram < float(cfg["abort_free_ram_gb"])) or \
               (disk is not None and disk < float(cfg["abort_free_disk_gb"])):
                abort = f"resource floor hit mid-run: ram {ram} GB, disk {disk} GB"
                kill_tree(proc)
                break
        t.join(5)
        rc = proc.wait()
        if abort:
            fh.write(f"ABORTED: {abort}\n")
        fh.write(f"EXIT={rc}\n")
    return rc, abort, "".join(tail)[-3000:]


def write_receipt(cfg: dict, sha: str, rec: dict) -> Path:
    d = Path(cfg["receipts_dir"]) / sha
    d.mkdir(parents=True, exist_ok=True)
    rec.update({"check": "suite", "tree": sha, "written_at": time.time(),
                "written_at_iso": _dt.datetime.now().isoformat(timespec="seconds"),
                "writer": "scripts/run_suite_guarded.py"})
    p = d / "suite.json"
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", default=None, help="checkout to test (default: the repo this script is in)")
    ap.add_argument("--workers", type=int, default=None, help="xdist workers wanted; capped by the seat rule")
    ap.add_argument("--wait", action="store_true", help="wait for room and for the lock instead of refusing")
    ap.add_argument("--session", default=os.environ.get("FRIDAY_SUITE_SESSION", "unnamed"))
    ap.add_argument("--config", default=None, help="private config path (default ~/.claude/friday-desktop.local.json)")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)

    tree = Path(a.tree).resolve() if a.tree else ROOT
    cfg = resolve_paths(load_config(Path(a.config) if a.config else None), tree)
    args = [x for x in a.pytest_args if x != "--"] or list(DEFAULT_ARGS)
    holder = f"{a.session}-{os.getpid()}-{int(time.time())}"
    lock = Path(cfg["suite_lock"])

    while True:
        why = floor_refusal(cfg, tree)
        if why:
            print(f"[suite-guard] not starting: {why}", file=sys.stderr)
            if not a.wait:
                return EXIT_REFUSED_FLOOR
            time.sleep(60); continue
        seat = seat_up(cfg["seat_port"])
        n = worker_count(a.workers, seat, cfg)
        cmd = [sys.executable, "-m", "pytest", *args, "-p", "no:cacheprovider"]
        # Explicit zero also overrides inherited -n auto or caller options.
        cmd += ["-n", str(n)]
        state, fields = lock_state(lock)
        if state == "stale":
            print(f"[suite-guard] taking over a stale lock (holder {fields.get('holder')} pid {fields.get('pid')} is gone)", file=sys.stderr)
            try:
                lock.unlink()
            except OSError:
                pass
        if claim_lock(lock, holder, a.session, tree, cmd):
            break
        state, fields = lock_state(lock)
        print(f"[suite-guard] SUITE_LOCK is held by {fields.get('holder', '?')} ({fields.get('session', '?')}); one suite at a time", file=sys.stderr)
        if not a.wait:
            return EXIT_REFUSED_LOCK
        time.sleep(60)

    sha = git_sha(tree)
    ram0, disk0 = guard.free_ram_gb(), guard.free_disk_gb(tree)
    log = Path(cfg["receipts_dir"]) / sha / "suite.log"
    print(f"[suite-guard] {holder} claimed {lock}; tree {sha[:10]} ram={ram0} disk={disk0} seat={'up' if seat else 'down'} -> {' '.join(cmd)}")
    t0 = time.time()
    rc, abort, tail = None, None, ""
    try:
        rc, abort, tail = run_pytest(cmd, tree, log, cfg)
    finally:
        released = release_lock(lock, holder)
        exit_code = rc if rc is not None else -1
        receipt = write_receipt(cfg, sha, {
            "ok": exit_code == 0 and not abort, "exit_code": exit_code, "cmd": cmd,
            "seconds": round(time.time() - t0, 1), "tail": tail, "aborted": abort,
            "workers": n, "seat_up": seat, "free_ram_gb_start": ram0, "free_disk_gb_start": disk0,
            "lock": str(lock), "holder": holder, "lock_released": released, "log": str(log),
            "tree_path": str(tree),
        })
        print(f"[suite-guard] receipt {'OK' if exit_code == 0 and not abort else 'NOT OK'} exit={exit_code} -> {receipt}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
