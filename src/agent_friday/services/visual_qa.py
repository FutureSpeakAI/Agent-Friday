"""Visual QA (NS-24.4-1, NS-36.5-8): does a file Friday made LOOK right?

services/artifact_qa reads a file the way a reader's program parses it
(does it open, do its formulas work). This module looks at the layout the
reader will see, and reports problems as fixed codes with page (or slide, or
sheet) numbers. A finding never carries the file's words.

  PDF      pages are rendered locally with pdfium (pypdfium2, which ships
           with pdfplumber, a core dependency) and read with pdfplumber:
             page_empty / page_nearly_empty   a blank or all-but-blank page
             text_off_page                    text outside the page (text
                                              drawn invisibly is ignored)
             text_near_edge                   text inside a printer's margin
             text_overlap                     text drawn over other text
             content_off_page                 a chart, table or picture
                                              spilling off one edge (an error
                                              only when the rendered pixels
                                              show it reaching that edge)
             font_missing                     a font neither embedded nor
                                              one of the PDF standard 14
             image_distorted                  a picture shown at another
                                              aspect ratio than its pixels
             low_contrast                     text colour against the
                                              rendered background (WCAG)
             large_gap                        a big blank band between blocks
  Word, PowerPoint, Excel
           checked from the file's own layout (always), and, when LibreOffice
           is installed here, converted to PDF in a temporary folder and
           rendered as above, plus font_substituted (a font the file asks for
           that is not in the rendered output). The converter runs with one
           shared, locked-down profile under Friday's home (no macros, no link
           updates), one conversion at a time; a macro-enabled file, or one
           linking to outside content, is never converted:
             .pptx  shape_off_slide, text_overflow (estimated), alt_text_missing,
                    image_distorted
             .docx  table_too_wide, graphic_too_wide, long_unbroken_text,
                    image_distorted
             .xlsx  cell_text_cut, cell_shows_hashes

Every result says what it did ("status"):
  checked           every check ran on every page (an Office file checked from
                    its layout alone only when no converter is installed)
  partial           a cap was hit (pages, time): the pages after it were not
                    looked at; or a converter is here but this Office file
                    was not converted or rendered (layout checked only). The
                    file is never "passed"
  could_not_render  the PDF renderer is missing here or failed: no pixel
                    check ran (what could be read without pixels is still
                    reported); never a pass
  unavailable       a frozen build, which cannot start the isolated check
  not_applicable    a kind this module does not look at

Bounded (bytes, pages, a time budget, a pixel cap per page, a converter
timeout, an isolated child with a hard time and memory limit and a minimal
environment, whose whole process tree is killed on a time-out),
deterministic (no sampling at random, pages in order) and fail-safe: `check`
never raises.
"""
from __future__ import annotations

import contextlib
import importlib.util
import itertools
import json
import math
import os
import posixpath
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

#: Files bigger than this are not looked at.
MAX_BYTES = 100 * 1024 * 1024
#: Only the first pages are looked at; a longer file is "partial".
MAX_PAGES = 30
#: The page loop stops after this long; the rest is "partial".
TIME_BUDGET_S = 12.0
#: A rendered page has at most this many pixels.
MAX_RENDER_PIXELS = 1_600_000
RENDER_SCALE = 1.25
#: Per page: characters read for edges and contrast, words for overlaps,
#: drawn objects for spills.
MAX_CHARS = 20_000
MAX_CONTRAST_SAMPLES = 160
MAX_WORDS = 2_000
MAX_OBJECTS = 4_000
#: Office files: cells, paragraphs and media read at most.
MAX_CELLS = 200_000
MAX_PARAGRAPHS = 20_000
MAX_MEDIA_HEAD = 512 * 1024
#: LibreOffice gets this long to convert one file.
CONVERT_TIMEOUT_S = 15
#: The isolated child is abandoned after this long (it and everything it
#: started are killed).
ISOLATED_TIMEOUT_S = 45
#: The isolated child (and the converter it starts) may use at most this
#: much memory: a Windows job's memory limit, a POSIX address-space limit.
CHILD_MEMORY_BYTES = 1024 * 1024 * 1024
#: Left-over friday-vqa-* temporary folders older than this are removed.
STALE_TEMP_S = 3600
#: Macro-enabled Office files are never handed to the converter.
MACRO_KINDS = frozenset((".xlsm", ".docm", ".pptm"))
#: Relationship types whose external targets the converter could fetch.
EXTERNAL_TYPES = frozenset(("image", "oleObject", "frame", "attachedTemplate", "subDocument",
                            "externalLinkPath", "audio", "video", "media"))
#: Per sheet: merged ranges read, and merged-range comparisons in all.
MAX_MERGES = 2000
MAX_MERGE_WORK = 2_000_000
#: Page numbers listed per finding, findings per result.
MAX_LISTED_PAGES = 20
MAX_FINDINGS = 30

#: Text closer than this (points) to the page edge may be cut by a printer.
EDGE_PT = 12.0
#: WCAG 2 contrast: 4.5:1 for body text, 3:1 for large text (18pt, or 14pt
#: bold). Below SEVERE_CONTRAST the text is all but unreadable: an error.
CONTRAST_NORMAL = 4.5
CONTRAST_LARGE = 3.0
SEVERE_CONTRAST = 2.0
#: A page is flagged when at least this many characters fall short.
LOW_CONTRAST_MIN_CHARS = 3
#: A picture is distorted when its shown and pixel aspect ratios differ by more.
DISTORTION = 0.05
#: A blank band taller than this share of the page, with content above and below.
GAP_SHARE = 0.25

VISUAL_KINDS = frozenset((".pdf", ".docx", ".pptx", ".xlsx", ".xlsm"))
STATUSES = ("checked", "partial", "could_not_render", "unavailable", "not_applicable")
UNITS = ("page", "slide", "sheet")

#: Every code: its usual severity and the plain words for it. A few codes
#: are raised to "error" when the case is severe (said where it happens).
CODES: Dict[str, Tuple[str, str]] = {
    "page_empty": ("error", "a page is empty"),
    "page_nearly_empty": ("warn", "a page is nearly empty"),
    "text_off_page": ("error", "text runs off the edge of the page"),
    "text_near_edge": ("warn", "text sits so close to the edge that a printer may cut it off"),
    "text_overlap": ("warn", "text is drawn over other text"),
    "content_off_page": ("warn", "a chart, table or picture spills off the page"),
    "font_missing": ("warn", "a font is not embedded, so the reader may see a different font"),
    "font_substituted": ("warn", "a font the file asks for was replaced by another when it was rendered"),
    "image_distorted": ("warn", "a picture is stretched out of shape"),
    "low_contrast": ("warn", "text is hard to read against its background"),
    "large_gap": ("warn", "a large blank gap breaks up the page"),
    "shape_off_slide": ("error", "a shape runs off the slide"),
    "text_overflow": ("warn", "text probably overflows its box"),
    "alt_text_missing": ("error", "a picture or chart has no alt text"),
    "table_too_wide": ("warn", "a table is wider than the page's text area"),
    "graphic_too_wide": ("warn", "a picture or chart is wider than the page's text area"),
    "long_unbroken_text": ("warn", "a long unbroken run of text is wider than the page"),
    "cell_text_cut": ("warn", "text in a cell is cut off by the next cell"),
    "cell_shows_hashes": ("warn", "a column is too narrow for its dates, so they show as ####"),
}
_RANK = {"error": 0, "warn": 1}

#: The PDF standard 14 families every reader has.
_STANDARD_14 = ("courier", "helvetica", "times", "symbol", "zapfdingbats")
EMU_PER_PT = 12700
TWIPS_PER_PT = 20


# ── results ─────────────────────────────────────────────────────────────────

class _Capped(Exception):
    """A native check reached one of its caps (the message says which)."""


class _Found:
    """Findings gathered per (code, unit): pages and how often."""

    def __init__(self):
        self.items: Dict[tuple, dict] = {}

    def add(self, code: str, page: Optional[int] = None, *, unit: Optional[str] = None,
            severity: Optional[str] = None) -> None:
        sev = severity or CODES[code][0]
        it = self.items.setdefault((code, unit), {"code": code, "severity": sev, "unit": unit,
                                                  "pages": set(), "count": 0})
        if _RANK[sev] < _RANK[it["severity"]]:
            it["severity"] = sev
        it["count"] += 1
        if page is not None:
            it["pages"].add(int(page))

    def codes(self) -> Set[str]:
        return {c for c, _u in self.items}

    def findings(self) -> List[dict]:
        out = []
        for it in sorted(self.items.values(), key=lambda i: (_RANK[i["severity"]], i["code"], i["unit"] or "")):
            out.append({"code": it["code"], "severity": it["severity"], "unit": it["unit"],
                        "pages": sorted(it["pages"])[:MAX_LISTED_PAGES], "count": int(it["count"])})
        return out[:MAX_FINDINGS]


def _result(kind: str, status: str, *, found: Optional[_Found] = None, method: Optional[str] = None,
            renderer: Optional[str] = None, converter: Optional[str] = None, pages_checked: int = 0,
            pages_total: Optional[int] = None, reason: Optional[str] = None) -> dict:
    findings = found.findings() if found else []
    return {"status": status, "kind": kind, "method": method, "renderer": renderer, "converter": converter,
            "pages_checked": int(pages_checked), "pages_total": pages_total, "reason": reason,
            "ok": not any(f["severity"] == "error" for f in findings), "findings": findings}


def _qa():
    """services/artifact_qa: its zip reader refuses DTDs and oversized parts.
    In the isolated child (loaded by file path, no package) it is loaded the
    same way, from beside this file."""
    mod = sys.modules.get("agent_friday.services.artifact_qa")
    if mod is not None:
        return mod
    if __package__:
        from agent_friday.services import artifact_qa
        return artifact_qa
    mod = sys.modules.get("artifact_qa")
    if mod is None:
        spec = importlib.util.spec_from_file_location("artifact_qa", str(Path(__file__).with_name("artifact_qa.py")))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["artifact_qa"] = mod
    return mod


# ── renderers and converters ────────────────────────────────────────────────

def _renderer():
    """pypdfium2, or None when it is not installed here."""
    try:
        import pypdfium2
        return pypdfium2
    except Exception:
        return None


def find_converter() -> Optional[str]:
    """LibreOffice's soffice when this machine has it, else None. Its usual
    install folders come first; PATH is looked at last, never the current
    folder."""
    candidates = []
    if os.name == "nt":
        for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    else:
        candidates += [Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
                       Path("/usr/lib/libreoffice/program/soffice"), Path("/usr/bin/soffice"),
                       Path("/usr/local/bin/soffice"), Path("/opt/libreoffice/program/soffice")]
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    if os.name == "nt":
        # Without this, Windows (and shutil.which on newer Pythons) also
        # looks in the current folder for a program of that name.
        os.environ.setdefault("NoDefaultCurrentDirectoryInExePath", "1")
    try:
        here = Path.cwd().resolve()
    except OSError:
        here = None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if not found:
            continue
        try:
            if here is not None and Path(found).resolve().parent == here:
                continue                                 # never a program in the current folder
        except OSError:
            continue
        return found
    return None


# ── child processes: a minimal environment, the whole tree killed ───────────

#: The only environment variables a child gets (so no API key is inherited).
_CHILD_ENV = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
              "USERPROFILE", "HOME", "LANG", "LC_ALL")


def _child_env() -> Dict[str, str]:
    env = {}
    for name in _CHILD_ENV:
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


_WIN = None


def _win_api():
    """kernel32 and ntdll with the job-object calls typed (Windows only)."""
    global _WIN
    if _WIN is not None:
        return _WIN
    import ctypes
    from ctypes import wintypes

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    ntdll.NtResumeProcess.restype = ctypes.c_long
    _WIN = (ctypes, k32, ntdll, Extended)
    return _WIN


_JOB_LIMIT_JOB_MEMORY = 0x0200
_JOB_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x0400
_JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_CREATE_SUSPENDED = 0x00000004


def _win_contain(proc, memory_bytes: Optional[int]):
    """Puts the suspended `proc` in a new job object (everything in it is
    killed when the job's handle is closed; a memory limit when asked), then
    lets it run. Returns the job handle, or None when no job could be made
    (it runs anyway, and a time-out falls back to taskkill /T)."""
    ctypes, k32, ntdll, Extended = _win_api()
    job = None
    try:
        job = k32.CreateJobObjectW(None, None)
        if job:
            info = Extended()
            flags = _JOB_LIMIT_KILL_ON_JOB_CLOSE | _JOB_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            if memory_bytes:
                flags |= _JOB_LIMIT_JOB_MEMORY
                info.JobMemoryLimit = int(memory_bytes)
            info.BasicLimitInformation.LimitFlags = flags
            if not (k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                                ctypes.sizeof(info))
                    and k32.AssignProcessToJobObject(job, int(proc._handle))):
                k32.CloseHandle(job)
                job = None
    except Exception:
        if job:
            try:
                k32.CloseHandle(job)
            except Exception:
                pass
        job = None
    # The process was created suspended, so nothing it starts escapes the job.
    if ntdll.NtResumeProcess(int(proc._handle)) != 0:
        _kill_tree(proc, job)
        _close_job(job)
        raise OSError("the child process could not be started")
    return job


def _close_job(job) -> None:
    """Closes the job's handle: whatever is still running in it is killed."""
    if job:
        try:
            _win_api()[1].CloseHandle(job)
        except Exception:
            pass


def _kill_tree(proc, job) -> None:
    """Kills `proc` and everything it started."""
    if os.name == "nt":
        if job:
            try:
                _win_api()[1].TerminateJobObject(job, 1)
            except Exception:
                pass
        else:
            taskkill = os.path.join(os.environ.get("SYSTEMROOT") or r"C:\Windows", "System32", "taskkill.exe")
            try:
                subprocess.run([taskkill, "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                               env=_child_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)          # its own session: the whole group
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _posix_limits(memory_bytes: Optional[int]):
    """A preexec_fn: the address-space limit for the isolated child. For the
    converter (None) an inherited limit is lifted back to the hard limit, as
    LibreOffice reserves far more address space than it uses."""
    def apply():
        try:
            import resource
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            if memory_bytes:
                want = memory_bytes if hard == resource.RLIM_INFINITY else min(memory_bytes, hard)
            else:
                want = hard
            resource.setrlimit(resource.RLIMIT_AS, (want, hard))
        except Exception:
            pass
    return apply


def _run_contained(argv: List[str], timeout: float, *, memory_bytes: Optional[int] = None) -> Tuple[int, bytes]:
    """Runs `argv` with a minimal environment and returns (exit code, stdout).
    On Windows it runs in a job object (killed with the job), on POSIX in a
    session of its own, so a time-out (subprocess.TimeoutExpired, re-raised)
    kills it and everything it started."""
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL,
          "env": _child_env()}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | _CREATE_SUSPENDED
    else:
        kw["start_new_session"] = True
        kw["preexec_fn"] = _posix_limits(memory_bytes)
    proc = subprocess.Popen(argv, **kw)
    job = None
    try:
        if os.name == "nt":
            job = _win_contain(proc, memory_bytes)
        try:
            out, _err = proc.communicate(timeout=max(0.0, float(timeout)))
        except subprocess.TimeoutExpired:
            _kill_tree(proc, job)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            raise
        return proc.returncode, out or b""
    finally:
        _close_job(job)
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass


@contextlib.contextmanager
def _workdir():
    """A temporary friday-vqa-* folder, removed afterwards even when a file
    in it is still held (whatever is left is swept on a later check)."""
    with tempfile.TemporaryDirectory(prefix="friday-vqa-", ignore_cleanup_errors=True) as td:
        yield Path(td)


_LAST_SWEEP = 0.0


def _sweep_stale_temp(force: bool = False) -> int:
    """Removes friday-vqa-* temporary folders older than STALE_TEMP_S (left by
    a check that was killed). At most once a minute per process. Returns how
    many were removed. Never raises."""
    global _LAST_SWEEP
    now = time.time()
    if not force and now - _LAST_SWEEP < 60:
        return 0
    _LAST_SWEEP = now
    removed = 0
    try:
        with os.scandir(tempfile.gettempdir()) as it:
            for entry in it:
                if removed >= 50:
                    break
                if not entry.name.startswith("friday-vqa-"):
                    continue
                try:
                    if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)() \
                            or not entry.is_dir(follow_symlinks=False):
                        continue
                    if now - entry.stat(follow_symlinks=False).st_mtime < STALE_TEMP_S:
                        continue
                except OSError:
                    continue
                shutil.rmtree(entry.path, ignore_errors=True)
                removed += 1
    except OSError:
        pass
    return removed


# ── LibreOffice: one locked-down profile, one conversion at a time ──────────

#: Written into the profile before LibreOffice first starts with it (and put
#: back whenever it is missing): no macros, no link updates, no fetching.
_LOCKDOWN = (
    ("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", "int", "3"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "boolean", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "boolean", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableActiveContent", "boolean", "true"),
    ("/org.openoffice.Office.Writer/Content/Update", "Link", "short", "0"),        # never
    ("/org.openoffice.Office.Calc/Content/Update", "Link", "short", "1"),          # never
    ("/org.openoffice.Office.Common/Misc", "UseLocking", "boolean", "false"),
    ("/org.openoffice.Office.Jobs/Jobs/org.openoffice.Office.Jobs:Job['UpdateCheck']/Arguments",
     "AutoCheckEnabled", "boolean", "false"),
)


def _lockdown_xcu() -> str:
    items = "".join('<item oor:path="%s"><prop oor:name="%s" oor:op="fuse"><value>%s</value></prop></item>\n'
                    % (path, name, value) for path, name, _t, value in _LOCKDOWN)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n' + items + '</oor:items>\n')


def _locked_down(text: str) -> bool:
    for path, name, _t, value in _LOCKDOWN:
        rx = (re.escape('oor:path="%s"' % path) + r'\s*>\s*<prop oor:name="' + re.escape(name)
              + r'"[^>]*>\s*<value>' + re.escape(value) + r'</value>')
        if not re.search(rx, text):
            return False
    return True


def _profile_dir() -> Path:
    """The one LibreOffice profile visual checks use, under Friday's home."""
    try:
        from agent_friday.paths import friday_home
        return Path(friday_home()) / "visual_qa" / "libreoffice-profile"
    except Exception:
        return Path(tempfile.gettempdir()) / "friday-libreoffice-profile"


def _prepare_profile(profile: Path) -> Path:
    """The profile's registrymodifications.xcu, locked down (written before
    the first launch, and again whenever a setting is missing)."""
    user = profile / "user"
    user.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(str(profile), 0o700)
        except OSError:
            pass
    xcu = user / "registrymodifications.xcu"
    try:
        text = xcu.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if not _locked_down(text):
        tmp = xcu.with_name(xcu.name + ".friday-tmp")
        tmp.write_text(_lockdown_xcu(), encoding="utf-8")
        os.replace(str(tmp), str(xcu))
    return xcu


_PROFILE_LOCK = threading.Lock()


def _try_file_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _file_unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _profile_lock(profile: Path, deadline: float):
    """Yields True while this is the only conversion using `profile` (a lock
    in this process and an OS lock on a file beside the profile, released if
    its holder dies), or False when the wait reached `deadline`."""
    have_thread = have_file = False
    fd = None
    try:
        while True:
            if not have_thread:
                have_thread = _PROFILE_LOCK.acquire(timeout=max(0.01, min(0.2, deadline - time.monotonic())))
            if have_thread:
                if fd is None:
                    profile.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(str(profile.parent / (profile.name + ".lock")), os.O_RDWR | os.O_CREAT, 0o600)
                have_file = _try_file_lock(fd)
                if have_file:
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        yield have_file
    finally:
        if have_file:
            _file_unlock(fd)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if have_thread:
            _PROFILE_LOCK.release()


def _soffice_argv(exe: str, profile: Path, out_dir: Path, inp: Path) -> List[str]:
    return [exe, "--headless", "--invisible", "--norestore", "--nolockcheck", "--nodefault", "--nologo",
            "--nofirststartwizard", "-env:UserInstallation=" + profile.as_uri(),
            "--convert-to", "pdf", "--outdir", str(out_dir), str(inp)]


def _convert(exe: str, src: Path, work: Path, timeout: float = CONVERT_TIMEOUT_S,
             profile: Optional[Path] = None) -> Tuple[Optional[Path], Optional[str]]:
    """`src` converted to PDF inside `work` (a temporary folder): (the PDF,
    None), or (None, why not). The converter reads a copy under a fixed name,
    with the shared locked-down profile, one conversion at a time; a
    macro-enabled file is never converted."""
    if src.suffix.lower() in MACRO_KINDS:
        return None, "macro-enabled files are never converted"
    deadline = time.monotonic() + max(0.0, float(timeout))
    inp = work / ("input" + src.suffix.lower())
    shutil.copyfile(str(src), str(inp))
    out_dir = work / "out"
    out_dir.mkdir()
    profile = Path(profile) if profile else _profile_dir()
    with _profile_lock(profile, deadline) as held:
        if not held:
            return None, "another file was still being converted"
        try:
            _prepare_profile(profile)
        except OSError:
            return None, "the converter's profile could not be prepared"
        left = deadline - time.monotonic()
        if left < 0.5:
            return None, "time ran out before the file could be converted"
        try:
            _run_contained(_soffice_argv(exe, profile, out_dir, inp), left)
        except subprocess.TimeoutExpired:
            # A killed converter can leave its profile half-written: start afresh.
            shutil.rmtree(str(profile), ignore_errors=True)
            return None, "the converter did not finish within %d seconds" % max(1, round(timeout))
        except Exception as e:
            return None, "the converter could not run (%s)" % type(e).__name__
    pdf = out_dir / "input.pdf"
    if pdf.is_file() and pdf.stat().st_size > 0:
        return pdf, None
    return None, "the converter could not convert the file"


# ── PDF ─────────────────────────────────────────────────────────────────────

def _name(v) -> str:
    """A PDF name or string as plain text."""
    n = getattr(v, "name", v)
    if isinstance(n, bytes):
        n = n.decode("latin-1", "replace")
    return str(n or "").strip("/'")


def _font_family(base: str) -> str:
    """'ABCDEF+Calibri-Bold' -> 'calibri' (for comparing names)."""
    plain = base.split("+", 1)[-1]
    plain = re.split(r"[-,]", plain, maxsplit=1)[0]
    return re.sub(r"[^a-z0-9]", "", plain.lower()).replace("mt", "").replace("ps", "")


def _page_fonts(page) -> List[Tuple[str, bool]]:
    """(base font name, embedded) for each font the page's resources name."""
    from pdfminer.pdftypes import resolve1
    out = []
    try:
        fonts = resolve1((page.page_obj.resources or {}).get("Font")) or {}
        items = list(fonts.values())[:200]
    except Exception:
        return out
    for font in items:
        try:
            fd = resolve1(font) or {}
            base = _name(resolve1(fd.get("BaseFont")))
            subtype = _name(resolve1(fd.get("Subtype")))
            if subtype == "Type3":
                continue
            desc = resolve1(fd.get("FontDescriptor")) or {}
            if subtype == "Type0":
                kids = resolve1(fd.get("DescendantFonts")) or []
                kid = resolve1(kids[0]) if kids else {}
                desc = resolve1((kid or {}).get("FontDescriptor")) or {}
            embedded = any(k in desc for k in ("FontFile", "FontFile2", "FontFile3"))
            out.append((base, embedded))
        except Exception:
            continue
    return out


def _rgb(color) -> Optional[Tuple[int, int, int]]:
    """A pdfplumber colour (gray, RGB or CMYK, 0..1) as 0..255 RGB."""
    if not isinstance(color, (tuple, list)) or not color:
        return None
    try:
        v = [float(x) for x in color]
    except (TypeError, ValueError):
        return None
    if len(v) == 1:
        g = round(255 * min(1, max(0, v[0])))
        return (g, g, g)
    if len(v) == 3:
        return tuple(round(255 * min(1, max(0, x))) for x in v)
    if len(v) == 4:
        c, m, y, k = (min(1, max(0, x)) for x in v)
        return (round(255 * (1 - c) * (1 - k)), round(255 * (1 - m) * (1 - k)), round(255 * (1 - y) * (1 - k)))
    return None


def _luminance(rgb) -> float:
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(a, b) -> float:
    """WCAG 2 contrast ratio of two RGB colours (1.0 to 21.0)."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _spill_sides(o: dict, box: Tuple[float, float, float, float]) -> str:
    """The edges ("l", "r", "t", "b") a drawn object partly on the page runs
    off, or "". One that runs off both opposite edges, or spans the page, is
    a deliberate bleed: ""."""
    bx0, btop, bx1, bbottom = box
    w, h = bx1 - bx0, bbottom - btop
    try:
        x0, x1, top, bottom = float(o["x0"]), float(o["x1"]), float(o["top"]), float(o["bottom"])
    except (KeyError, TypeError, ValueError):
        return ""
    if not (x1 > bx0 and x0 < bx1 and bottom > btop and top < bbottom):
        return ""                                        # wholly off the page: never seen
    tx, ty = max(3.0, 0.01 * w), max(3.0, 0.01 * h)
    left, right = bx0 - x0 > tx, x1 - bx1 > tx
    up, down = btop - top > ty, bottom - bbottom > ty
    if (left and right) or (up and down) or (x1 - x0) >= 0.95 * w or (bottom - top) >= 0.95 * h:
        return ""
    return ("l" if left else "") + ("r" if right else "") + ("t" if up else "") + ("b" if down else "")


def _spills(o: dict, box: Tuple[float, float, float, float]) -> bool:
    return bool(_spill_sides(o, box))


def _spill_visible(img, info: dict) -> bool:
    """Whether the rendered page shows ink where a spilling object meets the
    edge it runs off (so something visible really is cut there). An object
    the reader cannot see at that edge (white on white, clipped away) is not."""
    gray = img.convert("L")
    W, H = gray.size
    hist = gray.histogram()
    bg = max(range(256), key=lambda v: hist[v])
    bx0, btop, bx1, bbottom = info["box"]
    sx, sy = W / max(1e-6, bx1 - bx0), H / max(1e-6, bbottom - btop)
    band = 3
    for x0, top, x1, bottom, sides in info.get("spills") or ():
        px0, px1 = max(0, int((x0 - bx0) * sx)), min(W, int(math.ceil((x1 - bx0) * sx)))
        py0, py1 = max(0, int((top - btop) * sy)), min(H, int(math.ceil((bottom - btop) * sy)))
        strips = []
        if "l" in sides:
            strips.append((0, py0, min(W, band), py1))
        if "r" in sides:
            strips.append((max(0, W - band), py0, W, py1))
        if "t" in sides:
            strips.append((px0, 0, px1, min(H, band)))
        if "b" in sides:
            strips.append((px0, max(0, H - band), px1, H))
        for s in strips:
            if s[2] - s[0] < 1 or s[3] - s[1] < 1:
                continue
            lo, hi = gray.crop(s).getextrema()
            if abs(lo - bg) > 32 or abs(hi - bg) > 32:
                return True
    return False


def _invisible_text(doc, index: int) -> List[Tuple[float, float, float, float]]:
    """Boxes (x0, top, x1, bottom, measured like pdfplumber's) of the text
    the page draws invisibly (render mode 3, as in a scan's searchable text
    layer). Empty when pdfium cannot tell. Never raises."""
    out: List[Tuple[float, float, float, float]] = []
    try:
        import pypdfium2.raw as raw
        page = doc[index]
        try:
            ml, _mb, _mr, mt = page.get_mediabox()
            objs = page.get_objects(filter=(raw.FPDF_PAGEOBJ_TEXT,), max_depth=1)
            for obj in itertools.islice(objs, MAX_OBJECTS):
                if raw.FPDFTextObj_GetTextRenderMode(obj.raw) == raw.FPDF_TEXTRENDERMODE_INVISIBLE:
                    left, bottom, right, top = obj.get_bounds()
                    out.append((left - ml, mt - top, right - ml, mt - bottom))
        finally:
            try:
                page.close()
            except Exception:
                pass
    except Exception:
        return out
    return out


def _is_hidden(c: dict, hidden) -> bool:
    if not hidden:
        return False
    try:
        cx = (float(c["x0"]) + float(c["x1"])) / 2
        cy = (float(c["top"]) + float(c["bottom"])) / 2
    except (KeyError, TypeError, ValueError):
        return False
    return any(x0 - 1 <= cx <= x1 + 1 and top - 1 <= cy <= bottom + 1 for x0, top, x1, bottom in hidden)


def _distorted(shown_w: float, shown_h: float, px_w: float, px_h: float) -> bool:
    if min(shown_w, shown_h) <= 0 or min(px_w, px_h) <= 0:
        return False
    shown, pixels = shown_w / shown_h, px_w / px_h
    off = abs(shown / pixels - 1)
    turned = abs(shown * pixels - 1)                     # rotated a quarter turn
    return off > DISTORTION and turned > DISTORTION


def _structure(page, n: int, found: _Found, fonts: Dict[str, bool], hidden=()) -> dict:
    """The checks that need no pixels. Returns what the pixel pass reuses.
    Text drawn invisibly (`hidden`) is not looked at: the reader never sees it.
    A spill off the page is judged by the caller (it needs the pixels)."""
    box = tuple(float(v) for v in page.bbox)
    bx0, btop, bx1, bbottom = box
    chars = [c for c in page.chars[:MAX_CHARS] if (c.get("text") or "").strip() and not _is_hidden(c, hidden)]
    off = near = False
    for c in chars:
        try:
            x0, x1, top, bottom = float(c["x0"]), float(c["x1"]), float(c["top"]), float(c["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        if x0 < bx0 - 1 or x1 > bx1 + 1 or top < btop - 1 or bottom > bbottom + 1:
            off = True
        elif x0 < bx0 + EDGE_PT or x1 > bx1 - EDGE_PT or top < btop + EDGE_PT or bottom > bbottom - EDGE_PT:
            near = True
    if off:
        found.add("text_off_page", n, unit="page")
    elif near:
        found.add("text_near_edge", n, unit="page")
    # Text over text: words from different lines whose boxes overlap.
    try:
        words = page.extract_words()[:MAX_WORDS]
    except Exception:
        words = []
    grid: Dict[tuple, list] = {}
    overlap = False
    for i, wd in enumerate(words):
        try:
            x0, x1, top, bottom = float(wd["x0"]), float(wd["x1"]), float(wd["top"]), float(wd["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        cells = {(gx, gy) for gx in range(int(x0 // 60), int(x1 // 60) + 1)
                 for gy in range(int(top // 60), int(bottom // 60) + 1)}
        for cell in cells:
            for j in grid.get(cell, ()):
                other = words[j]
                if other.get("text") == wd.get("text") or abs(float(other["top"]) - top) < 1.0:
                    continue                             # a fake-bold copy, or the same line
                ix = min(x1, float(other["x1"])) - max(x0, float(other["x0"]))
                iy = min(bottom, float(other["bottom"])) - max(top, float(other["top"]))
                if ix > 0 and iy > 0:
                    a = (x1 - x0) * (bottom - top)
                    b = (float(other["x1"]) - float(other["x0"])) * (float(other["bottom"]) - float(other["top"]))
                    if ix * iy > 0.3 * max(1e-6, min(a, b)):
                        overlap = True
                        break
            if overlap:
                break
            grid.setdefault(cell, []).append(i)
        if overlap:
            break
    if overlap:
        found.add("text_overlap", n, unit="page")
    # Charts, tables and pictures spilling off one edge.
    objects = (list(page.rects) + list(page.lines) + list(page.curves))[:MAX_OBJECTS]
    images = list(page.images)[:MAX_OBJECTS]
    spills = []
    for o in objects + images:
        sides = _spill_sides(o, box)
        if sides:
            spills.append((float(o["x0"]), float(o["top"]), float(o["x1"]), float(o["bottom"]), sides))
            if len(spills) >= 50:
                break
    for im in images:
        try:
            sw, sh = im.get("srcsize") or (0, 0)
            w, h = float(im["x1"]) - float(im["x0"]), float(im["bottom"]) - float(im["top"])
        except (KeyError, TypeError, ValueError):
            continue
        if min(sw, sh) >= 16 and min(w, h) >= 18 and _distorted(w, h, sw, sh):
            found.add("image_distorted", n, unit="page")
            break
    missing = False
    for base, embedded in _page_fonts(page):
        fonts[base] = fonts.get(base, False) or embedded
        if not embedded and not base.split("+")[-1].lower().startswith(_STANDARD_14):
            missing = True
    if missing:
        found.add("font_missing", n, unit="page")
    return {"box": box, "chars": chars, "images": len(images), "objects": len(objects), "spills": spills}


def _render(doc, index: int):
    page = doc[index]
    try:
        w, h = page.get_size()
        if w <= 0 or h <= 0:
            return None
        scale = min(RENDER_SCALE, math.sqrt(MAX_RENDER_PIXELS / float(w * h)))
        bitmap = page.render(scale=scale)
        try:
            return bitmap.to_pil().convert("RGB")
        finally:
            try:
                bitmap.close()
            except Exception:
                pass
    finally:
        try:
            page.close()
        except Exception:
            pass


def _pixels(img, info: dict, n: int, total: int, found: _Found) -> None:
    """Empty pages, big gaps and contrast, from the rendered page."""
    from PIL import Image
    gray = img.convert("L")
    W, H = gray.size
    hist = gray.histogram()
    bg = max(range(256), key=lambda v: hist[v])
    mask = gray.point([255 if abs(v - bg) > 32 else 0 for v in range(256)])
    ink = mask.histogram()[255] / float(max(1, W * H))
    chars = info["chars"]
    if ink < 0.0002:
        found.add("page_empty", n, unit="page")
        return
    if ink < 0.003 and len(chars) <= 10 and not info["images"]:
        found.add("page_nearly_empty", n, unit="page")
    # A big blank band between blocks (not on a cover or the last page, and
    # not the space above a footer: there must be real content below it).
    if 1 < n < total:
        try:
            rows = list(mask.resize((1, H), getattr(Image, "Resampling", Image).BOX).getdata())
        except Exception:
            rows = []
        inked = [i for i, v in enumerate(rows) if v > 0.25]
        if inked:
            first, last = inked[0], inked[-1]
            best, gap_end = 0, None
            prev = first
            for i in inked[1:]:
                if i - prev > best:
                    best, gap_end = i - prev, i
                prev = i
            if gap_end is not None and best > GAP_SHARE * H:
                above = (gap_end - best) - first
                below = last - gap_end
                if above > 0.05 * H and below > 0.05 * H:
                    found.add("large_gap", n, unit="page")
    # Contrast: each sampled character's fill colour against the most common
    # colour of the rendered pixels under it (the background).
    bx0, btop, bx1, bbottom = info["box"]
    pw, ph = max(1e-6, bx1 - bx0), max(1e-6, bbottom - btop)
    sx, sy = W / pw, H / ph
    step = max(1, len(chars) // MAX_CONTRAST_SAMPLES)
    low, worst = 0, 21.0
    for c in chars[::step][:MAX_CONTRAST_SAMPLES]:
        fg = _rgb(c.get("non_stroking_color"))
        if fg is None:
            continue
        try:
            x0, x1 = (float(c["x0"]) - bx0) * sx, (float(c["x1"]) - bx0) * sx
            y0, y1 = (float(c["top"]) - btop) * sy, (float(c["bottom"]) - btop) * sy
            size = float(c.get("size") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        x0, x1 = max(0, int(x0)), min(W, int(math.ceil(x1)))
        y0, y1 = max(0, int(y0)), min(H, int(math.ceil(y1)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        colors = img.crop((x0, y0, x1, y1)).getcolors((x1 - x0) * (y1 - y0))
        if not colors:
            continue
        back = max(colors, key=lambda cc: cc[0])[1]
        ratio = contrast_ratio(fg, back)
        bold = "bold" in str(c.get("fontname") or "").lower()
        need = CONTRAST_LARGE if size >= 18 or (bold and size >= 14) else CONTRAST_NORMAL
        if ratio < need:
            low += 1
            worst = min(worst, ratio)
    if low >= LOW_CONTRAST_MIN_CHARS or (low and len(chars) < LOW_CONTRAST_MIN_CHARS):
        found.add("low_contrast", n, unit="page", severity="error" if worst < SEVERE_CONTRAST else "warn")


def _check_pdf(path: Path, deadline: float, found: _Found, fonts: Dict[str, bool], *,
               kind: str = "pdf") -> dict:
    with open(path, "rb") as fh:
        if not fh.read(1024).lstrip().startswith(b"%PDF-"):
            return _result(kind, "could_not_render", found=found, reason="the file is not a PDF")
    try:
        import pdfplumber
    except Exception:
        return _result(kind, "could_not_render", found=found, reason="no PDF reader is installed here")
    pdfium = _renderer()
    with pdfplumber.open(str(path)) as pdf:
        pages = pdf.pages
        total = len(pages)
        if not total:
            return _result(kind, "could_not_render", found=found, pages_total=0,
                           reason="the PDF has no pages to look at")
        doc, render_reason = None, None
        if pdfium is None:
            render_reason = "no PDF renderer is installed here"
        else:
            try:
                doc = pdfium.PdfDocument(str(path))
            except Exception:
                render_reason = "the PDF renderer could not open the file"
        checked = rendered = 0
        timed_out = False
        try:
            for i in range(min(total, MAX_PAGES)):
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                page = pages[i]
                hidden = _invisible_text(doc, i) if doc is not None else []
                info = _structure(page, i + 1, found, fonts, hidden)
                img = None
                if doc is not None:
                    try:
                        img = _render(doc, i)
                        if img is not None:
                            _pixels(img, info, i + 1, total, found)
                            rendered += 1
                    except Exception:
                        img = None
                    if img is None and render_reason is None:
                        render_reason = "page %d could not be rendered" % (i + 1)
                elif not info["chars"] and not info["images"] and not info["objects"]:
                    found.add("page_empty", i + 1, unit="page")      # nothing drawn at all
                if info["spills"]:
                    # Only an error when the pixels show something visible
                    # meeting the edge it runs off; unconfirmed, a warning.
                    try:
                        seen = img is not None and _spill_visible(img, info)
                    except Exception:
                        seen = False
                    found.add("content_off_page", i + 1, unit="page", severity="error" if seen else "warn")
                checked += 1
                try:
                    page.close()
                except Exception:
                    pass
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
    common = {"found": found, "method": "rendered", "renderer": "pdfium" if pdfium else None,
              "pages_checked": checked, "pages_total": total}
    if render_reason:
        return _result(kind, "could_not_render", reason=render_reason, **common)
    if checked < total:
        why = ("time ran out after %d of %d pages" % (checked, total) if timed_out
               else "only the first %d of %d pages were looked at" % (checked, total))
        return _result(kind, "partial", reason=why, **common)
    return _result(kind, "checked", **common)


# ── Office files, from their own layout ─────────────────────────────────────

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_R_EMBED = "{%s}embed" % _NS["r"]
_R_ID = "{%s}id" % _NS["r"]


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def image_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) in pixels from a PNG, GIF or JPEG header, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data[:3] == b"\xff\xd8\xff":
        i, n = 2, len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01, 0xFF) or 0xD0 <= marker <= 0xD7:
                i += 2 if marker != 0xFF else 1
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return (w, h)
            i += 2 + seg
    return None


def _rels(pkg, part: str) -> Dict[str, str]:
    """{relationship id: package path} for one part's internal targets."""
    folder, _, name = part.rpartition("/")
    root = pkg.xml("%s/_rels/%s.rels" % (folder, name))
    out = {}
    for rel in (list(root) if root is not None else []):
        if (rel.get("TargetMode") or "") == "External":
            continue
        target = (rel.get("Target") or "").replace("\\", "/")
        full = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id") or ""] = full
    return out


def _media_size(pkg, name: Optional[str]) -> Optional[Tuple[int, int]]:
    if not name or name not in pkg.names:
        return None
    try:
        with pkg.z.open(name) as fh:
            return image_size(fh.read(MAX_MEDIA_HEAD))
    except Exception:
        return None


def _picture_distorted(pkg, rels: Dict[str, str], blip_fill, cx: int, cy: int) -> bool:
    """A picture shown at cx x cy (EMU) whose (cropped) pixels have another shape."""
    if blip_fill is None:
        return False
    blip = blip_fill.find("a:blip", _NS)
    size = _media_size(pkg, rels.get(blip.get(_R_EMBED) or "")) if blip is not None else None
    if not size:
        return False
    w, h = float(size[0]), float(size[1])
    crop = blip_fill.find("a:srcRect", _NS)
    if crop is not None:
        w *= 1 - (_int(crop.get("l")) + _int(crop.get("r"))) / 100000.0
        h *= 1 - (_int(crop.get("t")) + _int(crop.get("b"))) / 100000.0
    if min(size) < 16:
        return False
    return _distorted(float(cx), float(cy), w, h)


def _decorative(cnvpr) -> bool:
    return any(el.tag.endswith("}decorative") and el.get("val") in ("1", "true") for el in cnvpr.iter())


def _alt_text(pr) -> bool:
    """A description or a title counts as alt text (as in artifact_qa)."""
    return bool((pr.get("descr") or pr.get("title") or "").strip())


def _off_slide(ox: int, oy: int, cx: int, cy: int, sw: int, sh: int) -> bool:
    """A shape running off the slide. One that runs off both opposite edges
    (a band across the slide) is a deliberate bleed."""
    tol_x, tol_y = 0.005 * sw, 0.005 * sh
    left, right = ox < -tol_x, ox + cx > sw + tol_x
    up, down = oy < -tol_y, oy + cy > sh + tol_y
    if (left and right) or (up and down):
        return False
    return left or right or up or down


def _pptx_native(path: Path, found: _Found, deadline: Optional[float] = None) -> Set[str]:
    qa = _qa()
    pkg = qa._Pkg(path)
    pres = pkg.xml("ppt/presentation.xml")
    if pres is None:
        return set()
    size = pres.find("p:sldSz", _NS)
    sw = _int(size.get("cx"), 12192000) if size is not None else 12192000
    sh = _int(size.get("cy"), 6858000) if size is not None else 6858000
    slides = sorted(pkg.matching(r"ppt/slides/slide\d+\.xml"), key=lambda n: int(re.findall(r"\d+", n)[-1]))
    fonts: Set[str] = set()
    a = "{%s}" % _NS["a"]
    for name in slides[:500]:
        if deadline is not None and time.monotonic() >= deadline:
            raise _Capped("time ran out while reading the slides")
        root = pkg.xml(name)
        if root is None:
            continue
        n = int(re.findall(r"\d+", name)[-1])
        rels = _rels(pkg, name)
        tree = root.find("p:cSld/p:spTree", _NS)
        for shape in (list(tree) if tree is not None else []):
            x = None
            for sub in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm"):
                x = shape.find(sub, _NS)
                if x is not None:
                    break
            off = x.find("a:off", _NS) if x is not None else None
            ext = x.find("a:ext", _NS) if x is not None else None
            if off is not None and ext is not None:
                ox, oy, cx, cy = (_int(off.get("x")), _int(off.get("y")), _int(ext.get("cx")), _int(ext.get("cy")))
                if _off_slide(ox, oy, cx, cy, sw, sh):
                    found.add("shape_off_slide", n, unit="slide")
                tag = shape.tag.rsplit("}", 1)[-1]
                if tag == "sp" and _text_overflows(shape, cx, cy):
                    found.add("text_overflow", n, unit="slide")
                if tag == "pic" and _picture_distorted(pkg, rels, shape.find("p:blipFill", _NS), cx, cy):
                    found.add("image_distorted", n, unit="slide")
        for pic in root.iter("{%s}pic" % _NS["p"]):
            pr = pic.find("p:nvPicPr/p:cNvPr", _NS)
            if pr is not None and not _alt_text(pr) and not _decorative(pr):
                found.add("alt_text_missing", n, unit="slide")
        for frame in root.iter("{%s}graphicFrame" % _NS["p"]):
            data = frame.find("a:graphic/a:graphicData", _NS)
            pr = frame.find("p:nvGraphicFramePr/p:cNvPr", _NS)
            if data is not None and (data.get("uri") or "").endswith("/chart") and pr is not None \
                    and not _alt_text(pr) and not _decorative(pr):
                # A chart's own title and labels say much of what it shows:
                # a missing description is worth fixing, not a failure.
                found.add("alt_text_missing", n, unit="slide", severity="warn")
        for latin in root.iter(a + "latin"):
            face = (latin.get("typeface") or "").strip()
            if face and not face.startswith("+"):
                fonts.add(face)
    if len(slides) > 500:
        raise _Capped("only the first 500 slides were looked at")
    return fonts


def _text_overflows(sp, cx: int, cy: int) -> bool:
    """An estimate: does the shape's text need more room than the box has?
    Average glyph width 0.5em, line height 1.2em, 18pt when no size is set.
    Boxes that shrink text or grow to fit it are never flagged."""
    body = sp.find("p:txBody", _NS)
    if body is None:
        return False
    pr = body.find("a:bodyPr", _NS)
    if pr is not None:
        if pr.find("a:normAutofit", _NS) is not None or pr.find("a:spAutoFit", _NS) is not None:
            return False
        if (pr.get("vert") or "horz") != "horz":
            return False
    def g(k, d):
        return _int(pr.get(k), d) if pr is not None else d
    avail_w = (cx - g("lIns", 91440) - g("rIns", 91440)) / float(EMU_PER_PT)
    avail_h = (cy - g("tIns", 45720) - g("bIns", 45720)) / float(EMU_PER_PT)
    no_wrap = pr is not None and pr.get("wrap") == "none"
    a = "{%s}" % _NS["a"]
    height, any_text = 0.0, False
    for para in body.findall("a:p", _NS)[:500]:
        size, segments = None, [0]
        for child in para:
            tag = child.tag
            if tag in (a + "r", a + "fld"):
                rpr = child.find("a:rPr", _NS)
                if size is None and rpr is not None and rpr.get("sz"):
                    size = _int(rpr.get("sz"), 1800) / 100.0
                t = child.find("a:t", _NS)
                segments[-1] += len((t.text or "") if t is not None else "")
            elif tag == a + "br":
                segments.append(0)
        if size is None:
            end = para.find("a:endParaRPr", _NS)
            size = _int(end.get("sz"), 1800) / 100.0 if end is not None and end.get("sz") else 18.0
        size = max(1.0, size)
        lines = 0
        for chars in segments:
            any_text = any_text or chars > 0
            need = chars * 0.5 * size
            if avail_w <= 1:
                if chars:
                    return True
                lines += 1
            elif no_wrap:
                if need > avail_w * 1.1:
                    return True
                lines += 1
            else:
                lines += max(1, int(math.ceil(need / avail_w)))
        height += lines * size * 1.2
    return any_text and height > avail_h * 1.15 + 2


def _section_size(sect) -> Tuple[int, int, int, bool]:
    """(page width, left margin, right margin) in twips from a w:sectPr, and
    whether they were all given (else Letter with 1-inch margins is assumed)."""
    w = "{%s}" % _NS["w"]
    pg = sect.find("w:pgSz", _NS) if sect is not None else None
    mar = sect.find("w:pgMar", _NS) if sect is not None else None
    known = (pg is not None and pg.get(w + "w") is not None and mar is not None
             and mar.get(w + "left") is not None and mar.get(w + "right") is not None)
    page_w = _int(pg.get(w + "w"), 12240) if pg is not None else 12240
    left = _int(mar.get(w + "left"), 1440) if mar is not None else 1440
    right = _int(mar.get(w + "right"), 1440) if mar is not None else 1440
    return page_w, left, right, known


def _docx_native(path: Path, found: _Found, deadline: Optional[float] = None) -> Set[str]:
    qa = _qa()
    pkg = qa._Pkg(path)
    doc = pkg.xml("word/document.xml")
    if doc is None:
        return set()
    w = "{%s}" % _NS["w"]
    body = doc.find("w:body", _NS)
    if body is None:
        return set()
    sect = body.find("w:sectPr", _NS)
    page_w, left, right, _known = _section_size(sect)
    text_w = max(1, page_w - left - right)                         # twips
    styles = pkg.xml("word/styles.xml")
    size_pt = 11.0
    if styles is not None:
        sz = styles.find("w:docDefaults/w:rPrDefault/w:rPr/w:sz", _NS)
        if sz is not None and sz.get(w + "val"):
            size_pt = max(1.0, _int(sz.get(w + "val"), 22) / 2.0)
    # Tables: the grid's width (and indent) against the text area and the
    # page of the section the table is in. A section ends at a paragraph
    # carrying w:pPr/w:sectPr; the body's own w:sectPr is the last section's.
    # When the section's size is not given, a too-wide table is only a warning.
    def judge(tables, sect_pr):
        sw, sl, sr, known = _section_size(sect_pr)
        stext = max(1, sw - sl - sr)
        for tbl in tables:
            cols = sum(_int(gc.get(w + "w")) for gc in tbl.findall("w:tblGrid/w:gridCol", _NS))
            ind = tbl.find("w:tblPr/w:tblInd", _NS)
            indent = _int(ind.get(w + "w")) if ind is not None and (ind.get(w + "type") in (None, "dxa")) else 0
            if cols and sl + indent + cols > sw:
                found.add("table_too_wide", severity="error" if known else "warn")
            elif cols and indent + cols > stext * 1.05:
                found.add("table_too_wide")
    pending, seen = [], 0
    for child in body:
        if child.tag == w + "tbl":
            if seen < 2000:
                pending.append(child)
            seen += 1
        elif child.tag == w + "p":
            brk = child.find("w:pPr/w:sectPr", _NS)
            if brk is not None:
                judge(pending, brk)
                pending = []
    judge(pending, sect)
    rels = _rels(pkg, "word/document.xml")
    text_emu = text_w * 635                                         # twips -> EMU
    for holder in list(doc.iter("{%s}inline" % _NS["wp"])) + list(doc.iter("{%s}anchor" % _NS["wp"])):
        ext = holder.find("wp:extent", _NS)
        if ext is None:
            continue
        cx, cy = _int(ext.get("cx")), _int(ext.get("cy"))
        if holder.tag.endswith("}inline") and cx > text_emu * 1.02:
            found.add("graphic_too_wide")
        pic = holder.find(".//pic:pic", _NS)
        if pic is not None and _picture_distorted(pkg, rels, pic.find("pic:blipFill", _NS), cx, cy):
            found.add("image_distorted")
    # Long unbroken runs (a URL, a hash) wider than the text area.
    width_pt = text_w / float(TWIPS_PER_PT)
    paragraphs = body.findall("w:p", _NS)
    for i, para in enumerate(paragraphs[:MAX_PARAGRAPHS]):
        if deadline is not None and i % 500 == 0 and time.monotonic() >= deadline:
            raise _Capped("time ran out while reading the paragraphs")
        text = "".join(t.text or "" for t in para.iter(w + "t"))
        if not text:
            continue
        size = size_pt
        sz = para.find(".//w:rPr/w:sz", _NS)
        if sz is not None and sz.get(w + "val"):
            size = max(1.0, _int(sz.get(w + "val"), 22) / 2.0)
        longest = max((len(tok) for tok in text.split()), default=0)
        if longest * 0.5 * size > width_pt:
            found.add("long_unbroken_text")
    fonts: Set[str] = set()
    for root in (doc, styles):
        if root is None:
            continue
        for rf in root.iter(w + "rFonts"):
            for attr in ("ascii", "hAnsi"):
                v = (rf.get(w + attr) or "").strip()
                if v:
                    fonts.add(v)
    if len(paragraphs) > MAX_PARAGRAPHS:
        raise _Capped("only the first %d paragraphs were looked at" % MAX_PARAGRAPHS)
    return fonts


#: Widths (in digit widths) of dates in the built-in formats.
_DATE_WIDTHS = {14: 10, 15: 9, 16: 6, 17: 6, 18: 8, 19: 11, 20: 5, 21: 8, 22: 15, 45: 5, 46: 8, 47: 7}
_DATE_BUILTIN = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))


def _date_width(fmt_id: int, code: Optional[str], value: str) -> float:
    """How wide (in digit widths) a date shows in its format."""
    if code is None:
        if fmt_id == 14:
            try:
                import datetime
                d = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(float(value)))
                return len(str(d.month)) + len(str(d.day)) + 4 + 2 * 0.6
            except (ValueError, OverflowError):
                return 10
        return _DATE_WIDTHS.get(fmt_id, 10)
    plain = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", code.split(";", 1)[0])
    plain = plain.replace("mmmm", "mmmmmmmmm").replace("dddd", "ddddddddd")
    return sum(0.6 if ch in "/-.:, " else 1.0 for ch in plain)


def _xlsx_native(path: Path, found: _Found, deadline: Optional[float] = None) -> Set[str]:
    qa = _qa()
    pkg = qa._Pkg(path)
    wb = pkg.xml("xl/workbook.xml")
    if wb is None:
        return set()
    s = "{%s}" % _NS["s"]
    targets = {}
    rels = pkg.xml("xl/_rels/workbook.xml.rels")
    for rel in (list(rels) if rels is not None else []):
        targets[rel.get("Id")] = (rel.get("Target") or "").lstrip("/").replace("xl/", "", 1)
    # Cell styles: number format and whether the text wraps.
    custom, xfs = {}, []
    styles = pkg.xml("xl/styles.xml")
    if styles is not None:
        for nf in styles.iter(s + "numFmt"):
            custom[_int(nf.get("numFmtId"), -1)] = nf.get("formatCode") or ""
        cell_xfs = styles.find("s:cellXfs", _NS)
        for xf in (cell_xfs.findall("s:xf", _NS) if cell_xfs is not None else []):
            al = xf.find("s:alignment", _NS)
            xfs.append((_int(xf.get("numFmtId")), al is not None and al.get("wrapText") in ("1", "true")
                        or (al is not None and al.get("shrinkToFit") in ("1", "true"))))
    shared: List[int] = []
    sroot = pkg.xml("xl/sharedStrings.xml")
    if sroot is not None:
        for si in sroot.findall("s:si", _NS):
            shared.append(sum(len(t.text or "") for t in si.iter(s + "t")))
    budget = MAX_CELLS
    work = MAX_MERGE_WORK

    def out_of_time():
        return deadline is not None and time.monotonic() >= deadline
    for index, sheet in enumerate(wb.findall(".//s:sheets/s:sheet", _NS), 1):
        if out_of_time():
            raise _Capped("time ran out while reading the sheets")
        rid = sheet.get(_R_ID)
        part = "xl/" + targets[rid] if rid in targets else None
        root = pkg.xml(part) if part else None
        if root is None:
            continue
        fmt = root.find("s:sheetFormatPr", _NS)
        default_w = 8.43
        try:
            if fmt is not None and fmt.get("defaultColWidth"):
                default_w = max(0.0, min(255.0, float(fmt.get("defaultColWidth"))))
        except ValueError:
            pass
        widths: Dict[int, float] = {}
        for col in list(root.iter(s + "col"))[:1000]:
            lo, hi = _int(col.get("min"), 0), _int(col.get("max"), 0)
            try:
                width = float(col.get("width"))
            except (TypeError, ValueError):
                continue
            if col.get("hidden") in ("1", "true"):
                continue
            for c in range(max(1, lo), min(hi, lo + 300) + 1):
                widths[c] = width
        merged = []
        for mc in itertools.islice(root.iter(s + "mergeCell"), MAX_MERGES + 1):
            if len(merged) >= MAX_MERGES:
                raise _Capped("a sheet has more than %d merged ranges" % MAX_MERGES)
            ends = [qa._split_ref(x) for x in (mc.get("ref") or "").split(":")]
            if len(ends) == 2 and all(ends):
                c1, c2 = sorted((ends[0][0], ends[1][0]))
                r1, r2 = sorted((ends[0][1], ends[1][1]))
                merged.append((c1, r1, c2, r2))
        # Rows come in order: only the merges spanning the current row are
        # compared (a row out of order compares against them all).
        merged.sort(key=lambda m: m[1])
        active: list = []
        nxt, last_r = 0, 0
        for row in root.iter(s + "row"):
            r = _int(row.get("r"), 0)
            if r >= last_r:
                while nxt < len(merged) and merged[nxt][1] <= r:
                    active.append(merged[nxt])
                    nxt += 1
                active = [m for m in active if m[3] >= r]
                here = active
                last_r = r
            else:
                here = [m for m in merged if m[1] <= r <= m[3]]
            cells = {}
            for c in row.findall("s:c", _NS):
                budget -= 1
                if budget % 1024 == 0 and out_of_time():
                    raise _Capped("time ran out while reading the sheets")
                pos = qa._split_ref(c.get("r") or "")
                v = c.find("s:v", _NS)
                t = c.get("t") or "n"
                if pos is None:
                    continue
                if t == "inlineStr":
                    length = sum(len(x.text or "") for x in c.iter(s + "t"))
                    cells[pos[0]] = ("text", length, _int(c.get("s")), "")
                elif v is None or not (v.text or "").strip():
                    continue
                elif t == "s":
                    try:
                        length = shared[int(v.text)]
                    except (ValueError, IndexError):
                        length = 0
                    cells[pos[0]] = ("text", length, _int(c.get("s")), "")
                elif t == "str":
                    cells[pos[0]] = ("text", len(v.text or ""), _int(c.get("s")), "")
                elif t == "n":
                    cells[pos[0]] = ("num", 0, _int(c.get("s")), v.text or "")
                else:
                    cells[pos[0]] = ("other", 0, _int(c.get("s")), "")
            for col, (kind, length, style, value) in cells.items():
                width = widths.get(col, default_w)
                fmt_id, wraps = xfs[style] if 0 <= style < len(xfs) else (0, False)
                work -= len(here) + 1
                if work <= 0:
                    raise _Capped("only part of the merged cells were looked at")
                if any(c1 <= col <= c2 for c1, _r1, c2, _r2 in here):
                    continue
                if kind == "text" and not wraps and length * 0.9 > width * 1.1 + 1 and (col + 1) in cells:
                    found.add("cell_text_cut", index, unit="sheet")
                elif kind == "num":
                    code = custom.get(fmt_id)
                    is_date = (fmt_id in _DATE_BUILTIN and code is None) or (
                        code is not None and qa._format_category(fmt_id, code) == "date")
                    if is_date and _date_width(fmt_id, code, value) > width + 0.01:
                        found.add("cell_shows_hashes", index, unit="sheet")
            if budget <= 0:
                raise _Capped("only the first %d cells were looked at" % MAX_CELLS)
    return set()


_NATIVE = {".docx": _docx_native, ".pptx": _pptx_native, ".xlsx": _xlsx_native, ".xlsm": _xlsx_native}


def _not_convertible(path: Path) -> Optional[str]:
    """Why `path` must not be handed to the converter, or None: a
    macro-enabled file (by its name, or a .xlsx/.docx/.pptx carrying a macro
    project), or one linking to outside content the converter could fetch
    (an external image, OLE object, frame, template or sub-document)."""
    if path.suffix.lower() in MACRO_KINDS:
        return "macro-enabled files are never converted"
    try:
        pkg = _qa()._Pkg(path)
    except Exception:
        return "the file could not be read to see what it links to"
    try:
        names = sorted(pkg.names)
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            return "the file carries macros, so it is never converted"
        for name in names[:5000]:
            if not name.endswith(".rels"):
                continue
            root = pkg.xml(name)
            for rel in (list(root) if root is not None else []):
                if (rel.get("TargetMode") or "") == "External" \
                        and (rel.get("Type") or "").rsplit("/", 1)[-1] in EXTERNAL_TYPES:
                    return "the file links to outside content, so it was not converted"
    except Exception:
        return "the file could not be read to see what it links to"
    finally:
        try:
            pkg.z.close()
        except Exception:
            pass
    return None


def _check_office(path: Path, kind: str, deadline: float, profile_dir: Optional[Path] = None) -> dict:
    found = _Found()
    try:
        requested = _NATIVE[path.suffix.lower()](path, found, deadline)
        capped = None
    except _Capped as c:
        requested, capped = set(), str(c)
    if capped:
        # Part of the file was not read: whatever was found stands, and the
        # file is never passed on what was not looked at.
        return _result(kind, "partial", found=found, method="structural", reason=capped)
    exe = find_converter()
    if not exe:
        # No converter here: the layout read from the file itself is the
        # visual check (and the summary says nothing was rendered).
        return _result(kind, "checked", found=found, method="structural")
    why = _not_convertible(path)
    if why is None:
        with _workdir() as work:
            pdf, why = _convert(exe, path, work, min(CONVERT_TIMEOUT_S, max(1.0, deadline - time.monotonic())),
                                profile_dir)
            if pdf is not None:
                fonts: Dict[str, bool] = {}
                res = _check_pdf(pdf, deadline + TIME_BUDGET_S, found, fonts, kind=kind)
                if res["status"] in ("checked", "partial"):
                    shown = {_font_family(f) for f in fonts}
                    if any(_font_family(f) and _font_family(f) not in shown for f in requested):
                        found.add("font_substituted")
                    return _result(kind, res["status"], found=found, method="converted", renderer=res["renderer"],
                                   converter="libreoffice", pages_checked=res["pages_checked"],
                                   pages_total=res["pages_total"], reason=res["reason"])
                why = res["reason"] or "the converted file could not be rendered"
    # A converter is here but this file was not rendered: the layout was
    # checked, the looks were not, so it is never "checked".
    return _result(kind, "partial", found=found, method="structural", converter="libreoffice",
                   reason=("layout checked, not rendered: " + (why or "the file could not be converted"))[:200])


# ── entry points ────────────────────────────────────────────────────────────

def check(path, *, budget_s: float = TIME_BUDGET_S, profile_dir=None) -> dict:
    """The visual check of one file. Never raises. See the module docstring
    for the result's shape and what each status means. `profile_dir` is the
    LibreOffice profile to convert with (default: the one under Friday's
    home)."""
    p = Path(path)
    kind = p.suffix.lower().lstrip(".")
    if p.suffix.lower() not in VISUAL_KINDS:
        return _result(kind, "not_applicable")
    found = _Found()
    try:
        _sweep_stale_temp()
        if not p.is_file():
            return _result(kind, "could_not_render", reason="the file is not there")
        if p.stat().st_size > MAX_BYTES:
            return _result(kind, "could_not_render", reason="the file is too large to look at")
        deadline = time.monotonic() + max(0.0, float(budget_s))
        if p.suffix.lower() == ".pdf":
            return _check_pdf(p, deadline, found, {})
        return _check_office(p, kind, deadline, Path(profile_dir) if profile_dir else None)
    except Exception as e:
        return _result(kind, "could_not_render", found=found,
                       reason="the file could not be read for a visual check (%s)" % type(e).__name__)


def _validated(res, kind: str) -> Optional[dict]:
    """The child's answer, accepted only in exactly the expected shape."""
    if not isinstance(res, dict) or res.get("status") not in STATUSES:
        return None
    fs = res.get("findings")
    if not isinstance(fs, list) or len(fs) > MAX_FINDINGS:
        return None
    found = _Found()
    for f in fs:
        if not (isinstance(f, dict) and f.get("code") in CODES and f.get("severity") in _RANK
                and (f.get("unit") is None or f.get("unit") in UNITS)
                and isinstance(f.get("pages"), list) and len(f["pages"]) <= MAX_LISTED_PAGES
                and all(isinstance(n, int) and 0 < n < 10 ** 6 for n in f["pages"])
                and isinstance(f.get("count"), int)):
            return None
        for n in f["pages"] or [None]:
            found.add(f["code"], n, unit=f["unit"], severity=f["severity"])
        found.items[(f["code"], f["unit"])]["count"] = max(0, min(f["count"], 10 ** 6))
    def text(k, allowed=None):
        v = res.get(k)
        return v if isinstance(v, str) and len(v) <= 200 and (allowed is None or v in allowed) else None

    def num(k):
        v = res.get(k)
        return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10 ** 7 else None
    return _result(kind, res["status"], found=found, method=text("method", ("rendered", "converted", "structural")),
                   renderer=text("renderer", ("pdfium",)), converter=text("converter", ("libreoffice",)),
                   pages_checked=num("pages_checked") or 0, pages_total=num("pages_total"),
                   reason=text("reason"))


def check_isolated(path, timeout: float = ISOLATED_TIMEOUT_S) -> dict:
    """`check`, run in a child Python with a hard time limit, a memory limit
    and a minimal environment (the renderer and parsers read untrusted
    files). On a time-out the child and everything it started (the
    converter) are killed. A child that does not finish, or answers in any
    other shape, is "could_not_render", never a pass. A frozen build cannot
    start a child: "unavailable". Never raises."""
    p = Path(path)
    kind = p.suffix.lower().lstrip(".")
    if p.suffix.lower() not in VISUAL_KINDS:
        return _result(kind, "not_applicable")
    if getattr(sys, "frozen", False):
        return _result(kind, "unavailable", reason="the visual check cannot run in this build")
    _sweep_stale_temp()
    code = ("import importlib.util,json,sys\n"
            "s=importlib.util.spec_from_file_location('visual_qa',sys.argv[1])\n"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
            "sys.stdout.write(json.dumps(m.check(sys.argv[2],profile_dir=sys.argv[3] or None)))\n")
    try:
        try:
            profile = str(_profile_dir())
        except Exception:
            profile = ""
        _rc, out = _run_contained([sys.executable, "-I", "-c", code, str(Path(__file__).resolve()), str(p), profile],
                                  timeout, memory_bytes=CHILD_MEMORY_BYTES)
        res = _validated(json.loads(out.decode("utf-8", "replace") or "{}"), kind)
        if res is not None:
            return res
        why = "the visual check gave no usable answer"
    except subprocess.TimeoutExpired:
        why = "the visual check did not finish within %d seconds" % timeout
    except Exception as e:
        why = "the visual check could not run (%s)" % type(e).__name__
    return _result(kind, "could_not_render", reason=why)


# ── words ───────────────────────────────────────────────────────────────────

def _where(f: dict) -> str:
    pages = f.get("pages") or []
    unit = f.get("unit")
    if not pages or not unit:
        return ""
    listed = ", ".join(str(n) for n in pages[:5])
    more = " and more" if len(pages) > 5 or f.get("count", 0) > len(pages) and len(pages) >= MAX_LISTED_PAGES else ""
    return "%s %s%s" % (unit if len(pages) == 1 else unit + "s", listed, more)


def describe(f: dict) -> str:
    """One finding in plain words, from fixed wording: never the file's text."""
    words = CODES.get(f.get("code"), ("warn", "a layout problem"))[1]
    where = _where(f)
    return "%s (%s)" % (words, where) if where else words


def messages(res: Optional[dict], severity: str) -> List[str]:
    return [describe(f) for f in ((res or {}).get("findings") or []) if f.get("severity") == severity]


def needs_attention(res: Optional[dict]) -> Optional[str]:
    """Why a visual result leaves the file unverified (it was not fully
    looked at), or None when it does not."""
    st = (res or {}).get("status")
    if st == "could_not_render":
        return "could not check how it looks here: " + ((res or {}).get("reason") or "no renderer")
    if st == "partial":
        return "how it looks was only partly checked: " + ((res or {}).get("reason") or "a limit was reached")
    return None


def summary(res: Optional[dict]) -> str:
    """One plain line for a card or a tool result ("" when not applicable)."""
    if not res or res.get("status") in (None, "not_applicable"):
        return ""
    st = res["status"]
    if st == "unavailable":
        return "Looks: not checked (the visual check cannot run in this build)."
    parts = []
    errors, warns = messages(res, "error"), messages(res, "warn")
    if errors:
        parts.append("Looks wrong: " + "; ".join(errors[:5]) + ".")
    if warns:
        parts.append("Looks worth fixing: " + "; ".join(warns[:5]) + ".")
    attention = needs_attention(res)
    if attention:
        parts.insert(0, "Looks: " + attention + ".")
    elif not parts:
        how = {"rendered": "%d page(s) rendered" % res.get("pages_checked", 0),
               "converted": "converted and %d page(s) rendered" % res.get("pages_checked", 0),
               "structural": "checked from the file's layout; no converter here to render it"}.get(
                   res.get("method"), "checked")
        parts.append("Looks: no problems found (%s)." % how)
    return " ".join(parts)


def compact(res: Optional[dict]) -> Optional[dict]:
    """What the artifact manifest keeps: codes, page numbers, counts, the
    status and the fixed-wording reason and summary. None when not applicable."""
    if not res or res.get("status") in (None, "not_applicable"):
        return None
    keep = ("status", "method", "renderer", "converter", "pages_checked", "pages_total", "reason", "ok")
    out = {k: res.get(k) for k in keep}
    out["findings"] = [dict(f) for f in (res.get("findings") or [])][:MAX_FINDINGS]
    out["summary"] = summary(res)
    return out
