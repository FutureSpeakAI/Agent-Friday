"""Windows' fully isolated mode (AppContainer) for work that needs none of the owner's files.

A process in an AppContainer with no capabilities has NO network (Windows refuses
every outbound connection, loopback included) and can open only what is granted to its
container, plus what Windows grants every app container (its own system folders:
System32, Program Files, read-only). No administrator is needed. It is the one true
network boundary a per-user app can have, at the price of the owner's files, so it is
used only where the work needs none:

  * the code sandbox, when its Python is Friday's own install: that folder is made
    readable to app containers once (read only, a marker remembers it); each run gets
    its OWN container identity, granted only its own scratch folder, and the identity
    is deleted afterwards;
  * an MCP stdio server whose config says "isolation": "appcontainer": its own
    identity (per server name), granted only its own work folder, which Friday made.

`start()` returns a Popen-like object (stdin/stdout/stderr pipes, pid, poll, wait,
terminate, kill, close). The process starts suspended, goes into a kill-on-close job,
and only then runs. Windows only; elsewhere `available()` is False.
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterable, List, Optional

ALL_APP_PACKAGES = "S-1-15-2-1"
INTERNET_CLIENT = "S-1-15-3-1"       # the internetClient capability
_MARK = ".friday-isolated-read"


class IsolationError(RuntimeError):
    """Isolation could not be set up; nothing ran."""


def available() -> bool:
    return sys.platform == "win32"


def _api():
    import ctypes
    from ctypes import wintypes as W
    from agent_friday.services import code_sandbox as _sbx
    api = _sbx._win_api()
    ue = ctypes.WinDLL("userenv")
    ue.CreateAppContainerProfile.argtypes = [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.LPVOID, W.DWORD,
                                             ctypes.POINTER(W.LPVOID)]
    ue.CreateAppContainerProfile.restype = ctypes.c_long
    ue.DeriveAppContainerSidFromAppContainerName.argtypes = [W.LPCWSTR, ctypes.POINTER(W.LPVOID)]
    ue.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    ue.DeleteAppContainerProfile.argtypes = [W.LPCWSTR]
    ue.DeleteAppContainerProfile.restype = ctypes.c_long
    api.ue = ue
    a = api.a
    a.ConvertSidToStringSidW.argtypes = [W.LPVOID, ctypes.POINTER(W.LPWSTR)]
    a.ConvertSidToStringSidW.restype = W.BOOL
    a.FreeSid.argtypes = [W.LPVOID]
    a.FreeSid.restype = W.LPVOID
    k = api.k
    k.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, W.LPVOID, W.LPVOID, W.BOOL, W.DWORD,
                                 W.LPVOID, W.LPCWSTR, W.LPVOID, ctypes.POINTER(api.PROCESS_INFORMATION)]
    k.CreateProcessW.restype = W.BOOL

    class SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [("AppContainerSid", W.LPVOID), ("Capabilities", W.LPVOID),
                    ("CapabilityCount", W.DWORD), ("Reserved", W.DWORD)]
    api.SECURITY_CAPABILITIES = SECURITY_CAPABILITIES
    return api


def _profile(api, name: str):
    """(sid pointer, sid text) for container profile `name`, made if new."""
    import ctypes
    from ctypes import wintypes as W
    sid = W.LPVOID()
    hr = api.ue.CreateAppContainerProfile(name, name, name, None, 0, ctypes.byref(sid))
    if hr != 0:
        sid = W.LPVOID()
        if api.ue.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(sid)) != 0:
            raise IsolationError("the isolated-work profile could not be made (0x%08x)" % (hr & 0xFFFFFFFF))
    s = W.LPWSTR()
    if not api.a.ConvertSidToStringSidW(sid, ctypes.byref(s)):
        api.a.FreeSid(sid)
        raise IsolationError("the isolated-work profile has no readable id")
    text = s.value
    api.k.LocalFree(s)
    return sid, text


def readable_by_containers(path, sid_text: Optional[str] = None) -> Optional[bool]:
    """Can an isolated app run/read `path`? True when its DACL allows ALL APPLICATION
    PACKAGES (or `sid_text`), False when it does not, None when it cannot be read.
    (Some installers, Node.js's among them, set a DACL without app containers; only an
    administrator can change one under Program Files.)"""
    if not available():
        return None
    import ctypes
    from ctypes import wintypes as W
    try:
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        dacl, sd = W.LPVOID(), W.LPVOID()
        adv.GetNamedSecurityInfoW.argtypes = [W.LPCWSTR, ctypes.c_int, W.DWORD, W.LPVOID, W.LPVOID,
                                              ctypes.POINTER(W.LPVOID), W.LPVOID, ctypes.POINTER(W.LPVOID)]
        adv.GetNamedSecurityInfoW.restype = W.DWORD
        if adv.GetNamedSecurityInfoW(str(path), 1, 4, None, None, ctypes.byref(dacl), None,
                                     ctypes.byref(sd)) != 0:
            return None
        try:
            if not dacl.value:
                return True            # no DACL: everyone
            class ACL_SIZE(ctypes.Structure):
                _fields_ = [("AceCount", W.DWORD), ("AclBytesInUse", W.DWORD), ("AclBytesFree", W.DWORD)]
            info = ACL_SIZE()
            adv.GetAclInformation.argtypes = [W.LPVOID, W.LPVOID, W.DWORD, ctypes.c_int]
            if not adv.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
                return None
            adv.GetAce.argtypes = [W.LPVOID, W.DWORD, ctypes.POINTER(W.LPVOID)]
            adv.ConvertSidToStringSidW.argtypes = [W.LPVOID, ctypes.POINTER(W.LPWSTR)]
            want = {ALL_APP_PACKAGES} | ({sid_text} if sid_text else set())
            for i in range(info.AceCount):
                ace = W.LPVOID()
                if not adv.GetAce(dacl, i, ctypes.byref(ace)):
                    continue
                ace_type = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))[0]
                ace_flags = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))[1]
                if ace_type != 0 or ace_flags & 0x08:      # an allow entry that applies here
                    continue
                mask = ctypes.cast(ace.value + 4, ctypes.POINTER(W.DWORD))[0]
                s = W.LPWSTR()
                if adv.ConvertSidToStringSidW(ctypes.c_void_p(ace.value + 8), ctypes.byref(s)):
                    text = s.value
                    k32.LocalFree(s)
                    # FILE_EXECUTE, or GENERIC_EXECUTE / GENERIC_ALL
                    if text in want and mask & (0x20 | 0x20000000 | 0x10000000):
                        return True
            return False
        finally:
            k32.LocalFree(sd)
    except Exception:
        return None


def _icacls(args: List[str], what: str) -> None:
    r = subprocess.run(["icacls", *args, "/Q"], capture_output=True, text=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise IsolationError(what)


def grant(path, sid_text: str, write: bool = False) -> None:
    """Give `sid_text` read (or modify) access to `path` and what is under it.

    One inheritable entry on the folder itself (no /T): Windows carries it down to
    what inherits, and it does not follow junctions or links to somewhere else."""
    rights = "(OI)(CI)M" if write else "(OI)(CI)RX"
    _icacls([str(path), "/grant", "*%s:%s" % (sid_text, rights)],
            "access for isolated work could not be granted on %s" % Path(path).name)



def revoke(path, sid_text: str) -> bool:
    """Take back every entry `sid_text` has on `path` (grants and denials). False when
    the folder is not there (moved or deleted): its record is kept for next time."""
    if not Path(path).exists():
        return False
    _icacls([str(path), "/remove", "*%s" % sid_text],
            "access for isolated work could not be taken back on %s" % Path(path).name)
    return True


def _pkey(p) -> str:
    s = os.path.normpath(os.path.abspath(str(p))).replace("\\", "/")
    return s.lower() if os.name == "nt" else s


def _path_within(p, root) -> bool:
    a, b = _pkey(p), _pkey(root)
    return a == b or a.startswith(b.rstrip("/") + "/")


#: One writer at a time for the record of what was given (servers start in parallel).
import threading as _threading  # noqa: E402
_GIVEN_LOCK = _threading.RLock()


# ── a connector server's identity and what it was given ──────────────────────

def _state_dir() -> Path:
    import agent_friday.core as core
    d = Path(core.FRIDAY_DIR) / "mcp-isolation"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _salt() -> str:
    """Unique to this install: a server of the same name elsewhere, or after the
    install is replaced, is a different identity and inherits nothing."""
    f = _state_dir() / "salt"
    try:
        s = f.read_text(encoding="ascii").strip()
        if len(s) >= 32:
            return s
    except OSError:
        pass
    s = uuid.uuid4().hex + uuid.uuid4().hex
    f.write_text(s, encoding="ascii")
    return s


def _given_file() -> Path:
    return _state_dir() / "given.json"


def _given() -> dict:
    import json
    try:
        data = json.loads(_given_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_given(data: dict) -> None:
    import json
    f = _given_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def _sid_text(name: str) -> str:
    api = _api()
    sid, text = _profile(api, name)
    api.a.FreeSid(sid)
    return text


def set_server_access(profile: str, read: Iterable = (), write: Iterable = ()) -> None:
    """Make what `profile` may reach exactly this: take back anything given before
    that is not in the new set (or is now read-only), then grant. Recorded in
    Friday's state so a later change or removal can take it back.

    Only allow entries: Windows does not apply a deny entry to an isolated app's
    identity (checked on Windows 11), so nothing here relies on one."""
    with _GIVEN_LOCK:
        _set_server_access(profile, read, write)


def _set_server_access(profile, read, write) -> None:
    sid_text = _sid_text(profile)
    want = ([{"path": str(p), "kind": "read"} for p in read]
            + [{"path": str(p), "kind": "write"} for p in write])
    data = _given()
    before = data.get(profile, {}).get("entries", [])
    keys = {(e["path"].lower() if os.name == "nt" else e["path"], e["kind"]) for e in want}
    # Record first (so a crash part-way leaves a list that covers what was given).
    data[profile] = {"sid": sid_text, "entries": before + [e for e in want if e not in before]}
    _save_given(data)
    missing = []
    for e in before:
        k = (e["path"].lower() if os.name == "nt" else e["path"], e["kind"])
        if k not in keys and not revoke(e["path"], sid_text):
            missing.append(e)          # moved or deleted: kept, taken back if it returns
    for e in want:
        if e["kind"] == "read":
            grant(e["path"], sid_text, write=False)
        elif e["kind"] == "write":
            grant(e["path"], sid_text, write=True)
    data = _given()
    data[profile] = {"sid": sid_text, "entries": want + missing}
    _save_given(data)


def forget_servers(keep_profiles: Iterable[str]) -> List[str]:
    """Take back everything given to server identities not in `keep_profiles` and
    delete those identities. Returns the profiles forgotten."""
    with _GIVEN_LOCK:
        return _forget_servers(set(keep_profiles))


def _forget_servers(keep: set) -> List[str]:
    data = _given()
    gone = []
    for profile in [p for p in data if p not in keep]:
        sid_text = data[profile].get("sid") or ""
        ok = True
        left = []
        for e in data[profile].get("entries", []):
            try:
                if sid_text and not revoke(e["path"], sid_text):
                    left.append(e)        # moved or deleted: kept for next time
            except Exception:
                ok = False
                left.append(e)
        if left:
            data[profile]["entries"] = left
            ok = False
        if ok:
            try:
                _api().ue.DeleteAppContainerProfile(profile)
            except Exception:
                pass
            data.pop(profile, None)
            gone.append(profile)
    _save_given(data)
    return gone


def ensure_readable(folder) -> None:
    """Make a Friday-owned folder (its Python) readable to app containers, once."""
    folder = Path(folder)
    if not friday_owned(folder):
        raise IsolationError("only Friday's own folders are opened to isolated work")
    if (folder / _MARK).exists():
        return
    grant(folder, ALL_APP_PACKAGES, write=False)
    try:
        (folder / _MARK).write_text("read for app containers\n", encoding="ascii")
    except OSError:
        pass


class _Proc:
    """Just enough of subprocess.Popen for pipe-speaking callers."""

    def __init__(self, api, hprocess, pid, stdin, stdout, stderr, job, sid, profile, temporary):
        self._api, self._h, self.pid = api, hprocess, pid
        self.stdin, self.stdout, self.stderr = stdin, stdout, stderr
        self._job, self._sid, self._profile, self._temporary = job, sid, profile, temporary
        self.returncode = None
        self._closed = False

    def poll(self):
        if self.returncode is None and self._api.k.WaitForSingleObject(self._h, 0) == 0:
            from ctypes import byref, wintypes as W
            code = W.DWORD()
            self._api.k.GetExitCodeProcess(self._h, byref(code))
            self.returncode = int(code.value)
        return self.returncode

    def wait(self, timeout=None):
        ms = 0xFFFFFFFF if timeout is None else int(timeout * 1000)
        if self._api.k.WaitForSingleObject(self._h, ms) != 0:
            raise subprocess.TimeoutExpired("isolated process", timeout)
        return self.poll()

    def terminate(self):
        if not self._api.k.TerminateJobObject(self._job, 1):
            self._api.k.TerminateProcess(self._h, 1)

    kill = terminate

    def close(self):
        if self._closed:
            return
        self._closed = True
        for h in (self._h, self._job):
            try:
                self._api.k.CloseHandle(h)
            except Exception:
                pass
        try:
            self._api.a.FreeSid(self._sid)
        except Exception:
            pass
        if self._temporary:
            try:
                self._api.ue.DeleteAppContainerProfile(self._profile)
            except Exception:
                pass


def start(argv: List[str], *, cwd, env: dict, read: Iterable = (), write: Iterable = (),
          profile: Optional[str] = None, text: bool = True, memory_mb: int = 2048,
          max_processes: int = 8, timeout_s: int = 0, contain: bool = False,
          network: bool = False):
    """Start `argv` in an AppContainer with stdio pipes. `profile` names a lasting
    identity (an MCP server); None makes a one-run identity deleted on close(). `read`
    and `write` are folders granted to THIS identity only. Raises IsolationError when
    isolation cannot be set up; nothing runs then."""
    if not available():
        raise IsolationError("isolated work is available on Windows only")
    import ctypes
    import msvcrt
    from ctypes import wintypes as W
    from agent_friday.services import code_sandbox as _sbx
    api = _api()
    temporary = profile is None
    name = profile or ("AgentFriday.Run." + uuid.uuid4().hex[:16])
    sid, sid_text = _profile(api, name)
    c = ctypes
    job = None
    try:
        for d in read:
            grant(d, sid_text, write=False)
        for d in write:
            grant(d, sid_text, write=True)
        job = _sbx._job(api, timeout_s or 3600, memory_mb, max_processes, contain=contain)
        if not contain:
            lim = api.EXTENDED_LIMIT()
            if not api.k.QueryInformationJobObject(job, 9, c.byref(lim), c.sizeof(lim), None):
                raise IsolationError("the job could not be read")
            lim.BasicLimitInformation.LimitFlags |= 0x2000      # KILL_ON_JOB_CLOSE
            if not api.k.SetInformationJobObject(job, 9, c.byref(lim), c.sizeof(lim)):
                raise IsolationError("the job could not be set to end with Friday")
    except Exception:
        if job:
            api.k.CloseHandle(job)
        api.a.FreeSid(sid)
        if temporary:
            api.ue.DeleteAppContainerProfile(name)
        raise
    in_parent, in_child = _sbx._pipe(api, child_reads=True)
    out_parent, out_child = _sbx._pipe(api, child_reads=False)
    err_parent, err_child = _sbx._pipe(api, child_reads=False)
    child_ends = [in_child, out_child, err_child]
    pi = api.PROCESS_INFORMATION()
    # `network`: the internetClient capability (outbound internet; this PC's own
    # loopback stays closed to app containers). Without it there is no network at all.
    cap_sid = W.LPVOID()
    cap_arr = None
    if network:
        api.a.ConvertStringSidToSidW.argtypes = [W.LPCWSTR, c.POINTER(W.LPVOID)]
        api.a.ConvertStringSidToSidW.restype = W.BOOL
        if not api.a.ConvertStringSidToSidW(INTERNET_CLIENT, c.byref(cap_sid)):
            for h in child_ends + [in_parent, out_parent, err_parent, job]:
                api.k.CloseHandle(h)
            api.a.FreeSid(sid)
            if temporary:
                api.ue.DeleteAppContainerProfile(name)
            raise IsolationError("the internet capability could not be prepared")

        class SID_AND_ATTRIBUTES(c.Structure):
            _fields_ = [("Sid", W.LPVOID), ("Attributes", W.DWORD)]
        cap_arr = (SID_AND_ATTRIBUTES * 1)(SID_AND_ATTRIBUTES(cap_sid, 0x4))   # SE_GROUP_ENABLED
        caps = api.SECURITY_CAPABILITIES(sid, c.cast(cap_arr, W.LPVOID), 1, 0)
    else:
        caps = api.SECURITY_CAPABILITIES(sid, None, 0, 0)
    size = c.c_size_t(0)
    api.k.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
    attr = c.create_string_buffer(size.value)

    def _fail():
        for h in child_ends + [in_parent, out_parent, err_parent, job]:
            api.k.CloseHandle(h)
        api.a.FreeSid(sid)
        if temporary:
            api.ue.DeleteAppContainerProfile(name)
    try:
        if not api.k.InitializeProcThreadAttributeList(attr, 2, 0, c.byref(size)):
            raise IsolationError("preparing the start attributes failed")
        handles = (W.HANDLE * 3)(*child_ends)
        if not api.k.UpdateProcThreadAttribute(attr, 0, 0x20002, handles, c.sizeof(handles), None, None):
            raise IsolationError("restricting inherited handles failed")
        # PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES
        if not api.k.UpdateProcThreadAttribute(attr, 0, 0x20009, c.byref(caps), c.sizeof(caps), None, None):
            raise IsolationError("setting the isolation attribute failed")
        si = api.STARTUPINFOEXW()
        si.StartupInfo.cb = c.sizeof(api.STARTUPINFOEXW)
        si.StartupInfo.dwFlags = 0x100
        si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = child_ends
        si.lpAttributeList = c.cast(attr, W.LPVOID)
        # Windows starts an isolated process by reading the profile folders from the
        # environment it is given (it points LOCALAPPDATA and TEMP at the identity's own
        # folders); without them CreateProcess fails with ERROR_ENVVAR_NOT_FOUND (203).
        # These are locations, not secrets.
        full = {}
        for k, v in env.items():
            if k.upper() not in {x.upper() for x in full}:
                full[k] = v
        have = {k.upper() for k in full}
        for k in ("SystemRoot", "SystemDrive", "windir", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                  "HOMEDRIVE", "HOMEPATH", "ProgramData", "PUBLIC", "ALLUSERSPROFILE"):
            if k.upper() not in have and os.environ.get(k):
                full[k] = os.environ[k]
        block = "".join(f"{k}={v}\0" for k, v in sorted(full.items(), key=lambda kv: kv[0].upper())) + "\0"
        env_buf = (c.c_wchar * len(block))(*block)
        cmd = c.create_unicode_buffer(subprocess.list2cmdline(argv))
        flags = 0x4 | 0x400 | 0x80000 | 0x08000000      # SUSPENDED|UNICODE_ENV|EXTENDED|NO_WINDOW
        if not api.k.CreateProcessW(argv[0], cmd, None, None, True, flags, env_buf, str(cwd),
                                    c.byref(si), c.byref(pi)):
            raise IsolationError("the isolated process could not start (error %d)" % c.get_last_error())
    except Exception:
        _fail()
        raise
    finally:
        api.k.DeleteProcThreadAttributeList(attr)
        if cap_sid:
            api.k.LocalFree(cap_sid)
    for h in child_ends:
        api.k.CloseHandle(h)
    if not api.k.AssignProcessToJobObject(job, pi.hProcess):
        api.k.TerminateProcess(pi.hProcess, 1)
        for h in (pi.hProcess, pi.hThread, in_parent, out_parent, err_parent, job):
            api.k.CloseHandle(h)
        api.a.FreeSid(sid)
        if temporary:
            api.ue.DeleteAppContainerProfile(name)
        raise IsolationError("the isolated process could not be held in its job")
    if api.k.ResumeThread(pi.hThread) == 0xFFFFFFFF:
        api.k.TerminateJobObject(job, 1)
    api.k.CloseHandle(pi.hThread)

    def _f(h, mode):
        fd = msvcrt.open_osfhandle(h.value, os.O_WRONLY if "w" in mode else os.O_RDONLY)
        raw = os.fdopen(fd, mode + "b", buffering=0)
        if not text:
            return raw
        return io.TextIOWrapper(io.BufferedWriter(raw) if "w" in mode else io.BufferedReader(raw),
                                encoding="utf-8", errors="replace", line_buffering=("w" in mode))
    return _Proc(api, pi.hProcess, int(pi.dwProcessId), _f(in_parent, "w"), _f(out_parent, "r"),
                 _f(err_parent, "r"), job, sid, name, temporary)


def run(argv: List[str], *, cwd, env: dict, read: Iterable = (), write: Iterable = (),
        timeout: float = 60.0, stdin_text: str = "", cap: int = 200_000,
        memory_mb: int = 1024, max_processes: int = 1) -> dict:
    """Run to completion under a one-run identity, fully contained (one process by
    default, memory and CPU ceilings, no clipboard). Output past `cap` ends the run.
    {exit_code, stdout, stderr, timed_out, output_capped}."""
    import threading
    p = start(argv, cwd=cwd, env=env, read=read, write=write, text=False,
              memory_mb=memory_mb, max_processes=max_processes, timeout_s=int(timeout) + 2,
              contain=True)
    over = threading.Event()
    bufs = {"out": bytearray(), "err": bytearray()}

    def pump(stream, key):
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            room = cap - len(bufs[key])
            bufs[key] += chunk[:max(0, room)]
            if len(chunk) > room:
                over.set()
                return
    try:
        if stdin_text:
            p.stdin.write(stdin_text.encode("utf-8"))
        p.stdin.close()
        t1 = threading.Thread(target=pump, args=(p.stdout, "out"), daemon=True)
        t2 = threading.Thread(target=pump, args=(p.stderr, "err"), daemon=True)
        t1.start()
        t2.start()
        timed_out = False
        import time as _t
        deadline = _t.monotonic() + timeout
        while p.poll() is None:
            if over.is_set():
                p.kill()
                break
            if _t.monotonic() > deadline:
                timed_out = True
                p.kill()
                break
            _t.sleep(0.05)
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            pass
        t1.join(5)
        t2.join(5)
        return {"exit_code": p.returncode,
                "stdout": bytes(bufs["out"]).decode("utf-8", "replace"),
                "stderr": bytes(bufs["err"]).decode("utf-8", "replace"),
                "timed_out": timed_out, "output_capped": over.is_set()}
    finally:
        p.close()


def server_profile(server_name: str) -> str:
    return "AgentFriday.MCP." + hashlib.sha256((_salt() + "\0" + str(server_name)).encode("utf-8")).hexdigest()[:16]


def friday_owned(path) -> bool:
    """Is `path` inside Friday's own install (the only place opened to isolated work)?"""
    try:
        la = os.environ.get("LOCALAPPDATA")
        if not la:
            return False
        base = (Path(la) / "AgentFriday").resolve()
        p = Path(path).resolve()
        return p == base or base in p.parents
    except Exception:
        return False
