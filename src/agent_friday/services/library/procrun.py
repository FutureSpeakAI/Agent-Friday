"""Run one untrusted-document task in a child process with hard limits.

A hostile PDF or archive must not be able to hang or bloat the server, so
every parse and every page render happens in a short-lived child that is
killed at a wall-clock limit and held under a memory cap (a Windows job
object, or RLIMIT_AS elsewhere). The child imports only the worker module and
the parsing libraries, never the Flask app. Results come back as one JSON
document on stdout; bytes travel base64-encoded inside it.

A build that cannot start the child (a frozen build has no `-m` entry) or cannot hold
it to the limits (no job object could be made) reads nothing: `run_task` refuses with
kind "unsupported" and the Library says so. A hostile document is never parsed inside
the server. `limits_enforced()` says whether a child can be started at all.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

WALL_SECONDS = 120
MEMORY_MB = 1536
MAX_RESULT_BYTES = 96 * 1024 * 1024

_SRC_DIR = str(Path(__file__).resolve().parents[3])


class TaskFailed(Exception):
    """The child failed, timed out or was killed. `reason` is plain text."""

    def __init__(self, reason: str, kind: str = "failed"):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind  # "timeout" | "memory" | "failed" | "too_large"


def limits_enforced() -> bool:
    return not getattr(sys, "frozen", False)


def _job_for(memory_mb: int):
    """A Windows job object with a per-process memory cap that dies with us."""
    import ctypes
    from ctypes import wintypes as W

    k = ctypes.WinDLL("kernel32", use_last_error=True)

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", W.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", W.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", W.DWORD),
                    ("SchedulingClass", W.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in
                    ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k.CreateJobObjectW.restype = W.HANDLE
    k.CreateJobObjectW.argtypes = [W.LPVOID, W.LPCWSTR]
    k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD]
    k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
    k.CloseHandle.argtypes = [W.HANDLE]
    job = k.CreateJobObjectW(None, None)
    if not job:
        return None
    lim = EXT()
    # PROCESS_MEMORY (0x100) | DIE_ON_UNHANDLED_EXCEPTION (0x400) | KILL_ON_JOB_CLOSE (0x2000)
    lim.Basic.LimitFlags = 0x100 | 0x400 | 0x2000
    lim.ProcessMemoryLimit = int(memory_mb) * 1024 * 1024
    if not k.SetInformationJobObject(job, 9, ctypes.byref(lim), ctypes.sizeof(lim)):
        k.CloseHandle(job)
        return None

    def assign(handle) -> bool:
        return bool(k.AssignProcessToJobObject(job, W.HANDLE(handle)))

    def close():
        k.CloseHandle(job)

    return assign, close


def _posix_limits(memory_mb: int, nice: int = 0):
    def apply():  # pragma: no cover - runs in the child before exec
        import os
        import resource
        if nice:
            os.nice(nice)
        cap = int(memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    return apply


def run_task(task: str, args: dict, *, wall_s: float = WALL_SECONDS,
             memory_mb: int = MEMORY_MB, low_priority: bool = False) -> dict:
    """Run `task` in a child and return its result dict, or raise TaskFailed."""
    if not limits_enforced():
        # A reader that cannot be held to a time and memory limit does not read a hostile
        # document inside the server: a crash or a runaway there would take Friday with it.
        raise TaskFailed("reading documents needs the unpacked app, where the reader runs in its own limited "
                         "process", "unsupported")
    env = {k: v for k, v in os.environ.items()
           if k.upper() in ("SYSTEMROOT", "PATH", "TEMP", "TMP", "HOME", "USERPROFILE",
                            "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL", "FRIDAY_LIBRARY_SELFTEST")}
    env["PYTHONPATH"] = _SRC_DIR
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_HUB_OFFLINE"] = "1"
    cmd = [sys.executable, "-X", "utf8", "-B", "-m", "agent_friday.services.library.worker"]
    kw: dict = {}
    job = None
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000 | (0x4000 if low_priority else 0)   # NO_WINDOW | BELOW_NORMAL
        try:
            job = _job_for(memory_mb)
        except Exception:  # noqa: BLE001 - no job object, no limits
            job = None
        if job is None:
            raise TaskFailed("the reader could not be held to a memory limit on this system, so no document "
                             "is read", "unsupported")
    else:
        kw["preexec_fn"] = _posix_limits(memory_mb, 10 if low_priority else 0)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, **kw)
    try:
        if job:
            assign, _close = job
            if not assign(int(proc._handle)):  # type: ignore[attr-defined]
                proc.kill()
                raise TaskFailed("could not start a limited reader")
        payload = json.dumps({"task": task, "args": args}).encode("utf-8")
        box: dict = {"out": b"", "over": False}

        def drain():
            """Read the child's answer in pieces, stopping it the moment it is over the cap."""
            buf = bytearray()
            while True:
                chunk = proc.stdout.read(1 << 16)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_RESULT_BYTES:
                    box["over"] = True
                    proc.kill()
                    break
            box["out"] = bytes(buf)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            proc.stdin.write(payload)
            proc.stdin.close()
        except OSError:
            pass
        reader.join(wall_s)
        if reader.is_alive():
            proc.kill()
            reader.join(5)
            raise TaskFailed("took too long to read", "timeout")
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        if proc.poll() is None:
            proc.kill()
        if job:
            job[1]()
    out = box["out"]
    if box["over"]:
        raise TaskFailed("produced too much text", "too_large")
    try:
        res = json.loads(out.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        kind = "memory" if proc.returncode not in (0, None) else "failed"
        raise TaskFailed("could not be read (the reader stopped early)", kind) from None
    if not res.get("ok"):
        raise TaskFailed(str(res.get("error") or "could not be read"), res.get("kind", "failed"))
    return res["result"]
