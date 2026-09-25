"""Run Python away from the host shell: Friday's code sandbox.

`run_command` runs PowerShell as the owner, with the owner's rights. This
runs Python code Friday wrote or was handed in a separate process that is
held in, and says exactly how far.

Host backend (always available on Windows, no admin, nothing installed):

  * LOW INTEGRITY. The process runs with a copy of the owner's token that
    has every privilege dropped and its integrity level set to Low. Windows
    then refuses it write access to anything labelled Medium -- which is
    the owner's files, ~/.friday, and the registry under HKCU. Its working
    folder is a fresh temporary folder labelled Low, the only place it can
    write. The level is read back from the started process before it is
    allowed to run; if it is not Low, the process is killed unrun.
  * A JOB OBJECT: one process only (it cannot start another program, so no
    PowerShell from inside), a memory ceiling, a CPU-time ceiling, no
    clipboard, no desktop switching, and everything dies when the job is
    closed. A wall-clock timeout ends the job.
  * NO INHERITED SECRETS. The environment is built from nothing: no API
    keys, no tokens, HOME/USERPROFILE/APPDATA point into the sandbox folder
    (so `~/.friday` there is a folder in the sandbox, not Friday's), and
    only the three pipe handles are inherited.
  * OUTPUT CAPS. Output past the cap ends the run.
  * NETWORK: proxy variables point at a dead local port and Python's socket
    module refuses to open sockets. THIS IS NOT AN OS BOUNDARY: code that
    calls Winsock through ctypes gets past it.

What it does NOT stop, stated plainly: a Low process can still READ what the
owner's account can read -- documents, ~/.friday, and anything protected
only by the owner's DPAPI key. Together with the Python-only network block,
that means code in the host sandbox could read a file and send it somewhere.
So a host-sandbox run is classified OUTWARD: it waits for a yes in chat or a
card, like `run_command`. It is still far less than `run_command` once
approved -- it cannot change the machine or start programs.

Windows Sandbox backend (only where the Windows feature is already on; this
module never turns it on): a disposable virtual machine started from a .wsb
file with networking disabled, no clipboard, no GPU, a read-only mapped
folder holding the code and the Python runtime, and one writable output
folder. Windows enforces that boundary, so a run there is INTERNAL.

On other platforms the host backend is a plain child process with resource
limits and the same environment scrub; it claims no OS boundary.
"""
from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

INTERNAL, OUTWARD, FORBIDDEN = "internal", "outward", "forbidden"

MAX_CODE_CHARS = 100_000
DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 120
DEFAULT_MEMORY_MB = 1024           # numpy with OpenBLAS commits ~500 MB
MIN_MEMORY_MB, MAX_MEMORY_MB = 64, 2048
DEFAULT_OUTPUT_CAP = 32_000          # bytes per stream
#: Nothing listens here; a proxy-honouring client fails fast.
DEAD_PROXY = "http://127.0.0.1:9"

HOST_BOUNDARY_NOTE = (
    "Host sandbox: low integrity (cannot write your files or Friday's data), "
    "one process (cannot start programs), memory and time limits, no "
    "inherited secrets. Network is blocked inside Python only, not by "
    "Windows, and it can read files your account can read.")

_BOOT = r'''
import os, sys, json
def _no_network(*a, **k):
    raise OSError("network access is disabled in Friday's sandbox")
import socket as _s, _socket as _ss
class _Blocked(_s.socket):
    def __init__(self, *a, **k):
        _no_network()
for _m in (_s, _ss):
    for _n in ("socket", "SocketType"):
        if hasattr(_m, _n):
            setattr(_m, _n, _Blocked)
for _n in ("create_connection", "create_server", "getaddrinfo", "gethostbyname",
           "gethostbyname_ex", "gethostbyaddr", "socketpair", "fromfd"):
    for _m in (_s, _ss):
        if hasattr(_m, _n):
            setattr(_m, _n, _no_network)
del _s, _ss, _m, _n
try:
    with open("_friday_sandbox.json", encoding="utf-8") as _f:
        _cfg = json.load(_f)
except Exception:
    _cfg = {}
sys.path[:] = [os.getcwd()] + [p for p in sys.path[1:] if p] + list(_cfg.get("paths") or [])
sys.argv = ["main.py"]
del _cfg
import runpy
runpy.run_path("main.py", run_name="__main__")
'''


# ── Classification ─────────────────────────────────────────────────────────

def windows_sandbox_exe() -> Optional[Path]:
    """WindowsSandbox.exe if the Windows feature is already installed."""
    if sys.platform != "win32":
        return None
    p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"
    return p if p.exists() else None


def classify(args: Optional[dict]) -> tuple:
    """(internal | outward | forbidden, why) for one run_sandboxed call.

    Internal only where the boundary it claims is one Windows enforces
    (Windows Sandbox: networking off). The host backend cannot prove the
    network is closed or that nothing is read, so it waits for a decision.
    """
    backend = str((args or {}).get("backend") or "host").strip().lower()
    if backend == "windows_sandbox":
        if windows_sandbox_exe():
            return INTERNAL, ("it runs in a disposable Windows Sandbox with "
                              "networking off and only its own folders mapped")
        return FORBIDDEN, ("Windows Sandbox is not installed on this machine; "
                           "Friday does not turn Windows features on")
    if backend != "host":
        return FORBIDDEN, f"unknown sandbox backend {backend!r}"
    return OUTWARD, ("the host sandbox cannot write your files or start "
                     "programs, but its network block is inside Python, not "
                     "Windows, and it can read your files")


# ── Shared pieces ──────────────────────────────────────────────────────────

class _Capture(threading.Thread):
    """Read one stream into memory up to a cap; flag an overflow."""

    def __init__(self, fobj, cap: int, overflow: threading.Event):
        super().__init__(daemon=True, name="sandbox-capture")
        self.f, self.cap, self.overflow = fobj, cap, overflow
        self.buf = bytearray()

    def run(self):
        try:
            while True:
                chunk = self.f.read(8192)
                if not chunk:
                    break
                room = self.cap - len(self.buf)
                if room > 0:
                    self.buf += chunk[:room]
                if len(chunk) > room:
                    self.overflow.set()
                    break
        except Exception:
            pass
        finally:
            try:
                self.f.close()
            except Exception:
                pass

    def text(self) -> str:
        return bytes(self.buf).decode("utf-8", errors="replace")


def _clean_env(workdir: Path) -> dict:
    """An environment built from nothing: no secrets can be inherited."""
    w = str(workdir)
    env = {"HOME": w, "USERPROFILE": w, "APPDATA": w, "LOCALAPPDATA": w,
           "TEMP": w, "TMP": w, "TMPDIR": w,
           "HTTP_PROXY": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY, "ALL_PROXY": DEAD_PROXY,
           "http_proxy": DEAD_PROXY, "https_proxy": DEAD_PROXY, "all_proxy": DEAD_PROXY,
           "NO_PROXY": "", "no_proxy": "", "PYTHONIOENCODING": "utf-8",
           "FRIDAY_SANDBOX": "1"}
    py_dir = str(Path(_python_exe()).parent)
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        env.update({"SystemRoot": root, "windir": root,
                    "PATH": os.pathsep.join([py_dir, os.path.join(root, "System32")])})
    else:
        env.update({"PATH": os.pathsep.join([py_dir, "/usr/bin", "/bin"]), "LANG": "C.UTF-8"})
    return env


def _python_exe() -> str:
    """The real interpreter. A venv's python.exe on Windows is a launcher that
    starts the base interpreter as a second process, which a one-process job
    would refuse."""
    base = getattr(sys, "_base_executable", None)
    return base if base and os.path.exists(base) else sys.executable


def _package_paths() -> list:
    """Friday's installed packages, readable (never writable) from inside,
    so analysis code can import numpy and the like. Friday's own code is not
    among them: it is installed as an editable path file, which is not read."""
    out = []
    try:
        for p in site.getsitepackages():
            if p.lower().rstrip("\\/").endswith("site-packages") and os.path.isdir(p):
                out.append(p)
    except Exception:
        pass
    return out


def _prepare(code: str, workdir: Path) -> None:
    (workdir / "main.py").write_text(code, encoding="utf-8")
    (workdir / "_friday_boot.py").write_text(_BOOT, encoding="utf-8")
    (workdir / "_friday_sandbox.json").write_text(
        json.dumps({"paths": _package_paths()}), encoding="utf-8")


_OWN_FILES = {"main.py", "_friday_boot.py", "_friday_sandbox.json"}


def _produced(workdir: Path) -> list:
    out = []
    try:
        for p in sorted(workdir.rglob("*")):
            if p.is_file() and p.name not in _OWN_FILES:
                out.append({"name": p.relative_to(workdir).as_posix(),
                            "bytes": p.stat().st_size})
    except Exception:
        pass
    return out[:50]


def _argv(boot: str) -> list:
    return [_python_exe(), "-I", "-B", "-u", "-X", "utf8", boot]


# ── Windows host backend ────────────────────────────────────────────────────

def _win_api():
    import ctypes
    from ctypes import wintypes as W

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    a = ctypes.WinDLL("advapi32", use_last_error=True)

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", W.DWORD), ("lpSecurityDescriptor", W.LPVOID),
                    ("bInheritHandle", W.BOOL)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR),
                    ("lpTitle", W.LPWSTR), ("dwX", W.DWORD), ("dwY", W.DWORD),
                    ("dwXSize", W.DWORD), ("dwYSize", W.DWORD),
                    ("dwXCountChars", W.DWORD), ("dwYCountChars", W.DWORD),
                    ("dwFillAttribute", W.DWORD), ("dwFlags", W.DWORD),
                    ("wShowWindow", W.WORD), ("cbReserved2", W.WORD),
                    ("lpReserved2", W.LPVOID), ("hStdInput", W.HANDLE),
                    ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", W.LPVOID)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE),
                    ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", W.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", W.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", W.DWORD),
                    ("SchedulingClass", W.DWORD)]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", W.LPVOID), ("Attributes", W.DWORD)]

    class TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", SID_AND_ATTRIBUTES)]

    H, P = W.HANDLE, ctypes.POINTER
    sig = {
        (k, "GetCurrentProcess"): ([], H),
        (k, "CreatePipe"): ([P(H), P(H), P(SECURITY_ATTRIBUTES), W.DWORD], W.BOOL),
        (k, "SetHandleInformation"): ([H, W.DWORD, W.DWORD], W.BOOL),
        (k, "CloseHandle"): ([H], W.BOOL),
        (k, "CreateJobObjectW"): ([W.LPVOID, W.LPCWSTR], H),
        (k, "SetInformationJobObject"): ([H, ctypes.c_int, W.LPVOID, W.DWORD], W.BOOL),
        (k, "QueryInformationJobObject"): ([H, ctypes.c_int, W.LPVOID, W.DWORD,
                                            P(W.DWORD)], W.BOOL),
        (k, "AssignProcessToJobObject"): ([H, H], W.BOOL),
        (k, "TerminateJobObject"): ([H, W.UINT], W.BOOL),
        (k, "TerminateProcess"): ([H, W.UINT], W.BOOL),
        (k, "InitializeProcThreadAttributeList"): ([W.LPVOID, W.DWORD, W.DWORD,
                                                    P(ctypes.c_size_t)], W.BOOL),
        (k, "UpdateProcThreadAttribute"): ([W.LPVOID, W.DWORD, ctypes.c_size_t, W.LPVOID,
                                            ctypes.c_size_t, W.LPVOID, W.LPVOID], W.BOOL),
        (k, "DeleteProcThreadAttributeList"): ([W.LPVOID], None),
        (k, "ResumeThread"): ([H], W.DWORD),
        (k, "WaitForSingleObject"): ([H, W.DWORD], W.DWORD),
        (k, "GetExitCodeProcess"): ([H, P(W.DWORD)], W.BOOL),
        (k, "LocalFree"): ([W.LPVOID], W.LPVOID),
        (a, "OpenProcessToken"): ([H, W.DWORD, P(H)], W.BOOL),
        (a, "CreateRestrictedToken"): ([H, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.LPVOID,
                                        W.DWORD, W.LPVOID, P(H)], W.BOOL),
        (a, "ConvertStringSidToSidW"): ([W.LPCWSTR, P(W.LPVOID)], W.BOOL),
        (a, "GetLengthSid"): ([W.LPVOID], W.DWORD),
        (a, "SetTokenInformation"): ([H, ctypes.c_int, W.LPVOID, W.DWORD], W.BOOL),
        (a, "GetTokenInformation"): ([H, ctypes.c_int, W.LPVOID, W.DWORD, P(W.DWORD)], W.BOOL),
        (a, "GetSidSubAuthorityCount"): ([W.LPVOID], P(ctypes.c_ubyte)),
        (a, "GetSidSubAuthority"): ([W.LPVOID, W.DWORD], P(W.DWORD)),
        (a, "CreateProcessAsUserW"): ([H, W.LPCWSTR, W.LPWSTR, W.LPVOID, W.LPVOID, W.BOOL,
                                       W.DWORD, W.LPVOID, W.LPCWSTR, W.LPVOID,
                                       P(PROCESS_INFORMATION)], W.BOOL),
        (a, "ConvertStringSecurityDescriptorToSecurityDescriptorW"): (
            [W.LPCWSTR, W.DWORD, P(W.LPVOID), P(W.ULONG)], W.BOOL),
        (a, "GetSecurityDescriptorSacl"): ([W.LPVOID, P(W.BOOL), P(W.LPVOID), P(W.BOOL)],
                                           W.BOOL),
        (a, "SetNamedSecurityInfoW"): ([W.LPWSTR, ctypes.c_int, W.DWORD, W.LPVOID, W.LPVOID,
                                        W.LPVOID, W.LPVOID], W.DWORD),
    }
    for (dll, name), (argtypes, restype) in sig.items():
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = argtypes, restype

    class API:
        pass
    api = API()
    api.ctypes, api.W, api.k, api.a = ctypes, W, k, a
    api.SECURITY_ATTRIBUTES, api.STARTUPINFOEXW = SECURITY_ATTRIBUTES, STARTUPINFOEXW
    api.STARTUPINFOW, api.PROCESS_INFORMATION = STARTUPINFOW, PROCESS_INFORMATION
    api.EXTENDED_LIMIT, api.TOKEN_MANDATORY_LABEL = EXTENDED_LIMIT, TOKEN_MANDATORY_LABEL
    api.SID_AND_ATTRIBUTES = SID_AND_ATTRIBUTES
    return api


class SandboxError(RuntimeError):
    """The sandbox could not be set up; nothing was run."""


def _err(api, what):
    return SandboxError(f"{what} failed (Windows error {api.ctypes.get_last_error()})")


_LOW_RID = 0x1000


def _label_low(api, path: Path) -> None:
    """Give the folder (and what is created in it) a Low integrity label,
    so the Low process can write there and nowhere else."""
    c, W = api.ctypes, api.W
    psd = W.LPVOID()
    if not api.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "S:(ML;OICI;NW;;;LW)", 1, c.byref(psd), None):
        raise _err(api, "building the Low label")
    try:
        present, defaulted, sacl = W.BOOL(), W.BOOL(), W.LPVOID()
        if not api.a.GetSecurityDescriptorSacl(psd, c.byref(present), c.byref(sacl),
                                               c.byref(defaulted)):
            raise _err(api, "reading the Low label")
        rc = api.a.SetNamedSecurityInfoW(str(path), 1, 0x10, None, None, None, sacl)
        if rc != 0:
            raise SandboxError(f"labelling the sandbox folder Low failed (Windows error {rc})")
    finally:
        api.k.LocalFree(psd)


def _low_token(api):
    c, W = api.ctypes, api.W
    own = W.HANDLE()
    # DUPLICATE | ASSIGN_PRIMARY | QUERY | ADJUST_DEFAULT
    if not api.a.OpenProcessToken(api.k.GetCurrentProcess(), 0x0002 | 0x0001 | 0x0008 | 0x0080,
                                  c.byref(own)):
        raise _err(api, "opening Friday's token")
    try:
        tok = W.HANDLE()
        if not api.a.CreateRestrictedToken(own, 0x1, 0, None, 0, None, 0, None, c.byref(tok)):
            raise _err(api, "dropping privileges")                   # DISABLE_MAX_PRIVILEGE
    finally:
        api.k.CloseHandle(own)
    sid = W.LPVOID()
    if not api.a.ConvertStringSidToSidW("S-1-16-4096", c.byref(sid)):
        api.k.CloseHandle(tok)
        raise _err(api, "building the Low integrity SID")
    try:
        tml = api.TOKEN_MANDATORY_LABEL()
        tml.Label.Sid, tml.Label.Attributes = sid, 0x20            # SE_GROUP_INTEGRITY
        if not api.a.SetTokenInformation(tok, 25, c.byref(tml),
                                         c.sizeof(tml) + api.a.GetLengthSid(sid)):
            api.k.CloseHandle(tok)
            raise _err(api, "lowering the integrity level")
    finally:
        api.k.LocalFree(sid)
    return tok


def _integrity_rid(api, hprocess) -> int:
    c, W = api.ctypes, api.W
    tok = W.HANDLE()
    if not api.a.OpenProcessToken(hprocess, 0x0008, c.byref(tok)):
        raise _err(api, "reading the sandbox's token")
    try:
        buf = c.create_string_buffer(256)
        n = W.DWORD()
        if not api.a.GetTokenInformation(tok, 25, buf, 256, c.byref(n)):
            raise _err(api, "reading the sandbox's integrity level")
        tml = api.TOKEN_MANDATORY_LABEL.from_buffer(buf)
        count = api.a.GetSidSubAuthorityCount(tml.Label.Sid)[0]
        return int(api.a.GetSidSubAuthority(tml.Label.Sid, count - 1)[0])
    finally:
        api.k.CloseHandle(tok)


def _job(api, timeout_s: int, memory_mb: int):
    c = api.ctypes
    job = api.k.CreateJobObjectW(None, None)
    if not job:
        raise _err(api, "creating the job object")
    lim = api.EXTENDED_LIMIT()
    b = lim.BasicLimitInformation
    # PROCESS_TIME | ACTIVE_PROCESS | PROCESS_MEMORY | DIE_ON_UNHANDLED_EXCEPTION
    # | KILL_ON_JOB_CLOSE
    b.LimitFlags = 0x2 | 0x8 | 0x100 | 0x400 | 0x2000
    # A little over the wall-clock limit, so a busy loop is reported as the
    # timeout it is rather than as a quota kill.
    b.PerProcessUserTimeLimit = (int(timeout_s) + 2) * 10_000_000  # 100 ns units
    b.ActiveProcessLimit = 1
    lim.ProcessMemoryLimit = int(memory_mb) * 1024 * 1024
    if not api.k.SetInformationJobObject(job, 9, c.byref(lim), c.sizeof(lim)):
        api.k.CloseHandle(job)
        raise _err(api, "setting the job limits")
    # No clipboard, no desktop switching, no global atoms, no system settings,
    # no USER handles from outside the job, no logoff.
    ui = api.W.DWORD(0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x40 | 0x80)
    if not api.k.SetInformationJobObject(job, 4, c.byref(ui), c.sizeof(ui)):
        api.k.CloseHandle(job)
        raise _err(api, "setting the job's UI limits")
    return job


def _pipe(api, child_reads: bool):
    """(parent_end, child_end); only the child's end is inheritable."""
    c, W = api.ctypes, api.W
    sa = api.SECURITY_ATTRIBUTES(c.sizeof(api.SECURITY_ATTRIBUTES), None, True)
    r, w = W.HANDLE(), W.HANDLE()
    if not api.k.CreatePipe(c.byref(r), c.byref(w), c.byref(sa), 0):
        raise _err(api, "creating a pipe")
    parent, child = (w, r) if child_reads else (r, w)
    api.k.SetHandleInformation(parent, 0x1, 0)                     # not inheritable
    return parent, child


def _run_windows_host(code, workdir: Path, timeout_s, memory_mb, cap) -> dict:
    import msvcrt
    api = _win_api()
    c, W = api.ctypes, api.W
    _label_low(api, workdir)
    _prepare(code, workdir)
    closers = []
    try:
        tok = _low_token(api)
        closers.append(tok)
        job = _job(api, timeout_s, memory_mb)
        closers.append(job)
        in_parent, in_child = _pipe(api, child_reads=True)
        out_parent, out_child = _pipe(api, child_reads=False)
        err_parent, err_child = _pipe(api, child_reads=False)
        api.k.CloseHandle(in_parent)                   # stdin: empty, at EOF
        child_ends = [in_child, out_child, err_child]
        closers.extend(child_ends)

        si = api.STARTUPINFOEXW()
        si.StartupInfo.cb = c.sizeof(api.STARTUPINFOEXW)
        si.StartupInfo.dwFlags = 0x100                                # USESTDHANDLES
        si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = (
            in_child, out_child, err_child)
        # Inherit these three handles and nothing else Friday has open.
        size = c.c_size_t(0)
        api.k.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
        attr = c.create_string_buffer(size.value)
        if not api.k.InitializeProcThreadAttributeList(attr, 1, 0, c.byref(size)):
            raise _err(api, "preparing the handle list")
        handles = (W.HANDLE * 3)(*child_ends)
        try:
            if not api.k.UpdateProcThreadAttribute(attr, 0, 0x20002, handles,
                                                   c.sizeof(handles), None, None):
                raise _err(api, "restricting inherited handles")
            si.lpAttributeList = c.cast(attr, W.LPVOID)

            env = _clean_env(workdir)
            block = "".join(f"{k}={v}\0" for k, v in
                            sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
            env_buf = (c.c_wchar * len(block))(*block)
            cmd = c.create_unicode_buffer(subprocess.list2cmdline(_argv("_friday_boot.py")))
            pi = api.PROCESS_INFORMATION()
            # SUSPENDED | UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO | NO_WINDOW
            flags = 0x4 | 0x400 | 0x80000 | 0x08000000
            if not api.a.CreateProcessAsUserW(tok, _python_exe(), cmd, None, None, True, flags,
                                              env_buf, str(workdir), c.byref(si), c.byref(pi)):
                raise _err(api, "starting the sandboxed process")
        finally:
            api.k.DeleteProcThreadAttributeList(attr)
        for h in child_ends:
            api.k.CloseHandle(h)
            closers.remove(h)
        closers.extend([pi.hThread, pi.hProcess])

        # Nothing runs until the job holds it and its level is proven Low.
        if not api.k.AssignProcessToJobObject(job, pi.hProcess):
            api.k.TerminateProcess(pi.hProcess, 1)
            raise _err(api, "placing the process in its job")
        rid = _integrity_rid(api, pi.hProcess)
        if rid != _LOW_RID:
            api.k.TerminateProcess(pi.hProcess, 1)
            raise SandboxError(f"the process did not start at Low integrity (0x{rid:x}); "
                               f"it was killed before running")

        overflow = threading.Event()
        caps = []
        for h in (out_parent, err_parent):
            fd = msvcrt.open_osfhandle(h.value, os.O_RDONLY)
            caps.append(_Capture(os.fdopen(fd, "rb", buffering=0), cap, overflow))
        for t in caps:
            t.start()
        api.k.ResumeThread(pi.hThread)

        deadline = time.monotonic() + timeout_s
        timed_out = False
        while True:
            if api.k.WaitForSingleObject(pi.hProcess, 100) == 0:          # WAIT_OBJECT_0
                break
            if overflow.is_set():
                api.k.TerminateJobObject(job, 1)
                break
            if time.monotonic() > deadline:
                timed_out = True
                api.k.TerminateJobObject(job, 1)
                break
        api.k.WaitForSingleObject(pi.hProcess, 5000)
        code_out = W.DWORD()
        api.k.GetExitCodeProcess(pi.hProcess, c.byref(code_out))
        lim = api.EXTENDED_LIMIT()
        api.k.QueryInformationJobObject(job, 9, c.byref(lim), c.sizeof(lim), None)
        for t in caps:
            t.join(5)
        return {
            "exit_code": int(code_out.value),
            "stdout": caps[0].text(), "stderr": caps[1].text(),
            "timed_out": timed_out, "output_capped": overflow.is_set(),
            "peak_memory_mb": round(lim.PeakProcessMemoryUsed / 1048576, 1),
            "files": _produced(workdir),
            "boundary": {"backend": "host", "os_boundary": True, "integrity": "low",
                         "job": {"one_process": True, "memory_mb": memory_mb,
                                 "cpu_seconds": timeout_s, "ui_restricted": True},
                         "network": "blocked inside Python only (not by Windows)",
                         "reads": "can read what your account can read",
                         "note": HOST_BOUNDARY_NOTE},
        }
    finally:
        for h in reversed(closers):
            try:
                api.k.CloseHandle(h)
            except Exception:
                pass


# ── Other platforms ─────────────────────────────────────────────────────────

def _run_posix(code, workdir: Path, timeout_s, memory_mb, cap) -> dict:
    import resource
    import signal
    _prepare(code, workdir)

    def limits():
        mem = int(memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (int(timeout_s), int(timeout_s) + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 << 20, 64 << 20))

    proc = subprocess.Popen(_argv("_friday_boot.py"), cwd=str(workdir),
                            env=_clean_env(workdir), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            preexec_fn=limits, start_new_session=True, close_fds=True)
    overflow = threading.Event()
    caps = [_Capture(proc.stdout, cap, overflow), _Capture(proc.stderr, cap, overflow)]
    for t in caps:
        t.start()
    deadline = time.monotonic() + timeout_s
    timed_out = False
    while proc.poll() is None:
        if overflow.is_set() or time.monotonic() > deadline:
            timed_out = not overflow.is_set()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
            break
        time.sleep(0.05)
    proc.wait(5)
    for t in caps:
        t.join(5)
    return {"exit_code": proc.returncode, "stdout": caps[0].text(), "stderr": caps[1].text(),
            "timed_out": timed_out, "output_capped": overflow.is_set(),
            "files": _produced(workdir),
            "boundary": {"backend": "host", "os_boundary": False,
                         "network": "blocked inside Python only",
                         "reads": "can read and write what your account can",
                         "note": "Resource limits and a clean environment only; no OS "
                                 "isolation on this platform."}}


# ── Windows Sandbox backend ─────────────────────────────────────────────────

_WSB_ROOT = r"C:\FridaySandbox"


def build_wsb(input_dir: Path, output_dir: Path, python_dir: Path, memory_mb: int) -> str:
    """The .wsb configuration: networking off, nothing shared but the three
    folders, and only the output folder writable."""
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return f"""<Configuration>
  <Networking>Disable</Networking>
  <vGPU>Disable</vGPU>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <PrinterRedirection>Disable</PrinterRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <ProtectedClient>Enable</ProtectedClient>
  <MemoryInMB>{max(2048, int(memory_mb))}</MemoryInMB>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>{esc(input_dir)}</HostFolder>
      <SandboxFolder>{_WSB_ROOT}\\in</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>{esc(python_dir)}</HostFolder>
      <SandboxFolder>{_WSB_ROOT}\\python</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>{esc(output_dir)}</HostFolder>
      <SandboxFolder>{_WSB_ROOT}\\out</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>cmd.exe /c {_WSB_ROOT}\\in\\run.cmd</Command>
  </LogonCommand>
</Configuration>
"""


def _wsb_run_cmd(timeout_s: int) -> str:
    r = _WSB_ROOT
    return "\r\n".join([
        "@echo off",
        r"mkdir %TEMP%\work",
        rf"copy /y {r}\in\*.py %TEMP%\work >nul",
        rf"copy /y {r}\in\*.json %TEMP%\work >nul",
        r"cd /d %TEMP%\work",
        rf"{r}\python\python.exe -I -B -u -X utf8 _friday_boot.py "
        rf"> {r}\out\stdout.txt 2> {r}\out\stderr.txt",
        rf"echo %ERRORLEVEL% > {r}\out\exit.txt",
        "shutdown /s /t 0",
        ""])


def _run_windows_sandbox(code, workdir: Path, timeout_s, memory_mb, cap) -> dict:
    exe = windows_sandbox_exe()
    if not exe:
        raise SandboxError("Windows Sandbox is not installed on this machine")
    indir, outdir = workdir / "in", workdir / "out"
    indir.mkdir()
    outdir.mkdir()
    _prepare(code, indir)
    # Inside the VM the host's packages are not mapped: only the standard library.
    (indir / "_friday_sandbox.json").write_text(json.dumps({"paths": []}), encoding="utf-8")
    (indir / "run.cmd").write_text(_wsb_run_cmd(timeout_s), encoding="utf-8")
    wsb = workdir / "friday.wsb"
    wsb.write_text(build_wsb(indir, outdir, Path(_python_exe()).parent, memory_mb),
                   encoding="utf-8")
    proc = subprocess.Popen([str(exe), str(wsb)],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # The VM takes a while to boot before the code's own time starts.
    deadline = time.monotonic() + timeout_s + 120
    done = outdir / "exit.txt"
    while time.monotonic() < deadline and not done.exists():
        time.sleep(0.5)
    timed_out = not done.exists()
    if timed_out:
        _stop_windows_sandbox(proc)

    def read(name):
        p = outdir / name
        try:
            with open(p, "rb") as f:
                data = f.read(cap + 1)
            return data[:cap].decode("utf-8", errors="replace"), len(data) > cap
        except Exception:
            return "", False
    out, o1 = read("stdout.txt")
    err, o2 = read("stderr.txt")
    try:
        exit_code = int((done.read_text(encoding="utf-8", errors="replace").strip() or "-1"))
    except Exception:
        exit_code = -1
    return {"exit_code": exit_code, "stdout": out, "stderr": err, "timed_out": timed_out,
            "output_capped": o1 or o2, "files": [],
            "boundary": {"backend": "windows_sandbox", "os_boundary": True,
                         "network": "disabled by Windows Sandbox",
                         "reads": "only the mapped input folder and the Python runtime",
                         "note": "A disposable Windows Sandbox VM; it shuts itself down."}}


def _stop_windows_sandbox(proc) -> None:
    """End the VM this call started: the launcher and its children."""
    try:
        import psutil
        p = psutil.Process(proc.pid)
        for ch in p.children(recursive=True):
            ch.kill()
        p.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ── Entry point ─────────────────────────────────────────────────────────────

def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def run(code: str, *, timeout_s=DEFAULT_TIMEOUT_S, memory_mb=DEFAULT_MEMORY_MB,
        output_cap=DEFAULT_OUTPUT_CAP, backend: str = "host") -> dict:
    """Run `code` in the sandbox. Returns a dict; never raises for the
    code's own failures. `ok` is False when the sandbox itself could not be
    set up, and then nothing ran."""
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "no code given"}
    if len(code) > MAX_CODE_CHARS:
        return {"ok": False, "error": f"code is longer than {MAX_CODE_CHARS} characters"}
    timeout_s = _clamp(timeout_s, 1, MAX_TIMEOUT_S, DEFAULT_TIMEOUT_S)
    memory_mb = _clamp(memory_mb, MIN_MEMORY_MB, MAX_MEMORY_MB, DEFAULT_MEMORY_MB)
    cap = _clamp(output_cap, 1_000, 1_000_000, DEFAULT_OUTPUT_CAP)
    backend = str(backend or "host").strip().lower()
    workdir = Path(tempfile.mkdtemp(prefix="friday-sandbox-"))
    t0 = time.monotonic()
    try:
        if backend == "windows_sandbox":
            res = _run_windows_sandbox(code, workdir, timeout_s, memory_mb, cap)
        elif backend != "host":
            return {"ok": False, "error": f"unknown sandbox backend {backend!r}"}
        elif sys.platform == "win32":
            res = _run_windows_host(code, workdir, timeout_s, memory_mb, cap)
        else:
            res = _run_posix(code, workdir, timeout_s, memory_mb, cap)
        res["ok"] = True
        res["seconds"] = round(time.monotonic() - t0, 2)
        return res
    except SandboxError as e:
        return {"ok": False, "error": f"the sandbox could not be set up, so nothing ran: {e}"}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
