"""The support bundle (north star §36.0-9; A3): one zip, made on this PC by the
owner's press, to attach by hand when asking for help. Friday never sends it.

What is in it:

* the Doctor report, as .json and .txt (services/doctor.py: no content, no
  file names, no addresses);
* the store inventory (services/store_inventory.public);
* log excerpts: the newest warning, error and crash lines of friday.log,
  server_stderr.log and logs/crashes.log, redacted word by word (below);
* the start-up guard's newest notes, redacted the same way;
* README.txt, and manifest.json with each file's SHA-256.

How a log line is redacted. A line keeps its time, level and logger name (a
logger outside Friday's own is "other_logger"). Every other word is kept only
when it is one of a fixed list of plain log words, a number, a code name
(snake_case, a dotted Friday module, an exception class), or an HTTP method or
status; any other word becomes "~" (runs of them one "..."). Paths, web
addresses, email addresses, keys, and this PC's user and host names are
replaced before that. A traceback frame keeps its line number and function,
and its file only when it is one of Friday's own (agent_friday/...).
So a name, a sentence or a file name the owner wrote does not survive; what is
left is the shape of the error.

Never in it: conversations, memories, wiki pages, settings values, the vault,
credentials, contacts, file names from the data folder. Nothing is made while
off the record, and log excerpts are left out for 30 days after an off-record
session (the same rule as the weekly report). The zip is written outside
Friday's data folder and never overwrites an earlier one; one receipt (kind
'export') goes into the action ledger.
"""
from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Optional

SUPPORT_VERSION = 1
TAIL_BYTES = 2_000_000
MAX_LINES_PER_LOG = 300
FOLDER_NAME = "Friday Support"


class SupportError(ValueError):
    """Said plainly to the owner."""


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


# ── redaction ────────────────────────────────────────────────────────────────

#: Plain words that log lines are made of. Anything not here, and not a
#: number or a code name, is withheld.
WORDS = frozenset("""
a an the and or not no nor of to in on at by for from with without into onto over under after before
is are was were be been being has have had do does did done can could cannot will would should may
might must it its this that these those there here than then as if else when while because so but
all any each every some none one two three four five six seven eight nine ten first last next
error errors warning warnings critical fatal failed failure fail fails exception exceptions traceback
most recent call calls line lines file files folder path module function method class object
timeout timed out retry retrying retried attempt attempts again giving up gave skipped skip skipping
missing invalid denied refused reset closed open opened opening close closing read reading write
writing wrote written load loading loaded save saving saved start starting started stop stopping
stopped restart restarting restarted ready running run runs ran exit exited exiting shutdown
connection connect connecting connected disconnected socket port host request requests response
responses status code codes http https server client api route routes handler thread threads
process processes pid memory disk cpu gpu model models seat seats provider providers tool tools
task tasks job jobs queue queued schedule scheduled scheduler routine plugin plugins extension
extensions voice audio mic speaker camera screen window browser page database db sqlite json
yaml settings setting config configuration key keys token tokens permission permissions access
unavailable available busy locked lock unlock empty full too large small size bytes kb mb gb
ms sec secs second seconds minute minutes hour hours day days took took took elapsed slow fast
ok yes true false null none nan unknown unexpected expected got value values type types
argument arguments keyword missing required optional found not_found new old current version
update updated updating upgrade installed install installing download downloading downloaded
cache cached index indexed indexing rebuild sync syncing synced import imported export exported
backup bundle restore restored verify verified check checked checking health healthy degraded
broken safe mode guard gate gates blocked allowed held hold receipt receipts ledger chain
""".split())

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
_EMAIL = re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+")
_WINPATH = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][^\\/\s\"'<>|:*?]+){2,}[\\/]?|[A-Za-z]:[\\/]\S*|\\\\[^\s\\/]+[\\/]\S*")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_NUMBER = re.compile(r"^[-+]?\d{1,6}(\.\d+)?(%|ms|s|kb|mb|gb)?$", re.I)
_SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")
_FRIDAY_MODULE = re.compile(r"^(agent_friday|friday)(\.[A-Za-z_][A-Za-z0-9_]*)+$")
_EXC = re.compile(r"^[A-Z][A-Za-z0-9]*(Error|Exception|Warning|Exit|Interrupt)$")
_STRIP = ",.;:()[]{}!?"
_LINE = re.compile(r"^(?P<ts>\S+) (?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
                   r"(?P<logger>[A-Za-z_][A-Za-z0-9_.]{0,80}) — (?P<msg>.*)$")
_FRAME = re.compile(r'^\s*File "(?P<file>[^"]*)", line (?P<line>\d+)(?:, in (?P<fn>\S+))?')
_FAULT_FRAME = re.compile(r'^\s*File "(?P<file>[^"]*)", line (?P<line>\d+) in (?P<fn>\S+)')
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?$")


def _markers() -> list[str]:
    from agent_friday.services import doctor
    return doctor._private_markers()


def _word(tok: str) -> str:
    """One word kept as it is, or '~'."""
    core = tok.strip(_STRIP)
    if not core:
        return tok
    if core in ("<path>", "<url>", "<email>", "<text>", "<withheld>"):
        return tok
    low = core.lower()
    if low in WORDS or core in _METHODS or core in _LEVELS:
        return tok
    if _NUMBER.match(core) or _FRIDAY_MODULE.match(core) or _TS.match(core):
        return tok
    if _SNAKE.match(core) and all(p in WORDS or p.isdigit() for p in core.split("_")):
        return tok                           # read_timeout, not_found: never a name
    if _EXC.match(core):
        return tok if isinstance(getattr(builtins, core, None), type) else \
            tok.replace(core, "other_exception")
    if "=" in core:
        k, _, v = core.partition("=")
        if _SNAKE.match(k) or re.fullmatch(r"[a-z]+", k):
            return k + "=" + (_word(v) if v else "")
    return "~"


def redact_text(text: str, markers: Optional[list] = None) -> str:
    """Free text reduced to its plain log words (see the module doc)."""
    from agent_friday.services import pasted_secrets
    markers = _markers() if markers is None else markers
    s = pasted_secrets.withhold(str(text), whole=False)[0]
    s = _URL.sub("<url>", s)
    s = _EMAIL.sub("<email>", s)
    s = _QUOTED.sub("<text>", s)
    s = _WINPATH.sub("<path>", s)
    low = s.lower()
    for m in markers:
        if m and m in low:
            s = re.sub(re.escape(m), "<withheld>", s, flags=re.I)
            low = s.lower()
    out = []
    for tok in s.split():
        w = _word(tok)
        if w == "~" and out and out[-1] in ("~", "..."):
            out[-1] = "..."
            continue
        out.append(w)
    return " ".join(out)[:400]


def _frame(m: re.Match, markers) -> str:
    f = m.group("file").replace("\\", "/")
    i = f.rfind("agent_friday/")
    where = f[i:] if i >= 0 and re.fullmatch(r"agent_friday(/[A-Za-z0-9_]+)+\.py", f[i:]) else "<other>"
    fn = m.group("fn") or ""
    # a function name is kept only in Friday's own files: elsewhere (the
    # owner's scripts, an extension) it can be a word the owner chose
    fn = fn if where != "<other>" and re.fullmatch(r"[<>A-Za-z0-9_.]{1,80}", fn) else "~"
    return '  File "%s", line %s, in %s' % (where, m.group("line"), fn)


def _logger(name: str) -> str:
    from agent_friday.services import doctor
    return doctor._logger_name(name)


def redact_log_line(line: str, markers: Optional[list] = None) -> Optional[str]:
    """A friday.log line (or a traceback line) redacted, or None when it is
    not a warning, an error or part of a traceback."""
    markers = _markers() if markers is None else markers
    line = line.rstrip("\r\n")
    m = _LINE.match(line)
    if m:
        if m.group("level") in ("DEBUG", "INFO"):
            return None
        ts = m.group("ts") if _TS.match(m.group("ts")) else "~"
        return "%s %s %s — %s" % (ts, m.group("level"), _logger(m.group("logger")),
                                       redact_text(m.group("msg"), markers))
    f = _FRAME.match(line) or _FAULT_FRAME.match(line)
    if f:
        return _frame(f, markers)
    s = line.strip()
    if s.startswith("Traceback (most recent call last)"):
        return "Traceback (most recent call last):"
    if re.match(r"^(Fatal Python error|Windows fatal exception|Current thread|Thread 0x)", s):
        return redact_text(s, markers)
    e = re.match(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Interrupt))(:\s?(.*))?$", s)
    if e:
        name = e.group(1).rsplit(".", 1)[-1]
        name = name if isinstance(getattr(builtins, name, None), type) else "other_exception"
        return name + (": " + redact_text(e.group(3) or "", markers) if e.group(2) else "")
    return None


def _tail(p: Path) -> str:
    try:
        st = os.lstat(p)
        from agent_friday.services import store_inventory
        if store_inventory._is_link(st):
            return ""
        with open(p, "rb") as f:
            f.seek(max(0, st.st_size - TAIL_BYTES))
            return f.read(TAIL_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def log_excerpt(p: Path, markers: Optional[list] = None, crash: bool = False) -> list[str]:
    markers = _markers() if markers is None else markers
    out = []
    lines = _tail(p).splitlines()
    if lines and not crash:
        lines = lines[1:]                      # the first may be cut mid-line
    for raw in lines:
        r = redact_log_line(raw, markers)
        if r is None and crash and raw.strip():
            r = redact_text(raw.strip(), markers)
        if r is not None:
            out.append(r)
    return out[-MAX_LINES_PER_LOG:]


def _boot_notes(markers) -> list:
    try:
        from agent_friday.services import boot_guard
        notes = boot_guard.recent_notes(20)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for n in notes or []:
        if not isinstance(n, dict):
            continue
        row = {}
        for k, v in list(n.items())[:20]:
            if not re.fullmatch(r"[a-z_]{1,40}", str(k)):
                continue
            if isinstance(v, bool) or v is None or (isinstance(v, (int, float)) and abs(v) < 1e12):
                row[k] = v
            elif isinstance(v, str):
                row[k] = redact_text(v, markers)
        out.append(row)
    return out


# ── the zip ─────────────────────────────────────────────────────────────────

def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:  # noqa: BLE001 - unknown is treated as on
        return True


def _recently_off_record() -> bool:
    try:
        from agent_friday.services import doctor_weekly
        last = doctor_weekly._state().get("last_off_record_at")
        return bool(last and time.time() - last < doctor_weekly.OFF_RECORD_SHADOW_S)
    except Exception:  # noqa: BLE001
        return True


def default_dest() -> Path:
    from agent_friday.services import friday_bundle
    return friday_bundle.documents_dir() / FOLDER_NAME


def _check_dest(dest: Path) -> Path:
    dest = Path(dest).expanduser()
    home = _home().resolve()
    try:
        dest.resolve().relative_to(home)
    except ValueError:
        return dest
    raise SupportError("Choose a folder outside Friday's own data folder.")


def contents() -> dict:
    """What the zip would hold: {name: bytes}, and notes. Built in memory."""
    if _off_record():
        raise SupportError("Off the record, no support bundle is made.")
    from agent_friday.services import doctor, store_inventory
    markers = _markers()
    rep = doctor.build()
    notes = []
    files = {
        "doctor-report.json": json.dumps(rep, indent=1, sort_keys=True).encode("utf-8"),
        "doctor-report.txt": doctor.render_text(rep).encode("utf-8"),
        "stores.json": json.dumps(store_inventory.public(store_inventory.cached(_home())),
                                  indent=1, sort_keys=True).encode("utf-8"),
    }
    if _recently_off_record():
        notes.append("Log excerpts are left out: part of the last 30 days was off the record.")
    else:
        home = _home()
        parts = []
        for label, rel, crash in (("friday.log", "friday.log", False),
                                  ("server_stderr.log", "server_stderr.log", False),
                                  ("logs/crashes.log", "logs/crashes.log", True)):
            lines = log_excerpt(home / rel, markers, crash=crash)
            parts.append("== %s: %d line(s) ==" % (label, len(lines)))
            parts += lines
            parts.append("")
        files["log-excerpts.txt"] = ("\n".join(parts) + "\n").encode("utf-8")
        files["boot-notes.json"] = json.dumps(_boot_notes(markers), indent=1).encode("utf-8")
    readme = [
        "Agent Friday support bundle",
        "",
        "Made on this PC by you. Friday never sends it anywhere: attach it yourself if you want help.",
        "",
        "What is in it:",
        "- doctor-report.json and .txt: versions, checks, error counts, settings drift and speed.",
        "  No conversations, memories, file names, addresses or vault content.",
        "- stores.json: every store Friday keeps, with its version, size and place in the data folder.",
        "- log-excerpts.txt: the newest warning, error and crash lines. Each keeps its time, level",
        "  and the part of Friday that wrote it; other words are kept only when they are plain log",
        "  words, numbers or code names, so names, sentences, paths, addresses and keys read",
        "  \"~\", \"...\", <path>, <url>, <email> or <withheld>.",
        "- boot-notes.json: the start-up guard's newest notes, redacted the same way.",
        "- manifest.json: each file's SHA-256.",
        "",
        "Read it before you share it.",
    ] + (["", "Notes:"] + ["- " + n for n in notes] if notes else [])
    files["README.txt"] = ("\n".join(readme) + "\n").encode("utf-8")
    return {"files": files, "notes": notes, "report_id": rep.get("id")}


def make(dest_parent: Optional[Path] = None, *, by: str = "owner") -> dict:
    """Write friday-support-<stamp>.zip into dest_parent (default: Documents,
    Friday Support). Returns {file, members, notes}."""
    parent = _check_dest(Path(dest_parent) if dest_parent else default_dest())
    c = contents()
    files = c["files"]
    manifest = {"support_version": SUPPORT_VERSION,
                "made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "report_id": c["report_id"], "notes": c["notes"],
                "files": {n: hashlib.sha256(b).hexdigest() for n, b in sorted(files.items())}}
    files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")
    parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = parent / ("friday-support-%s.zip" % stamp)
    k = 2
    while out.exists():
        out = parent / ("friday-support-%s-%d.zip" % (stamp, k))
        k += 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            z.writestr(name, data)
    tmp = out.with_name(out.name + ".partial")
    with open(tmp, "xb") as f:
        f.write(buf.getvalue())
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, out)                     # never replaces a file that appeared meanwhile
        os.unlink(tmp)
    except OSError:
        if out.exists():
            os.unlink(tmp)
            raise SupportError("A support bundle with that name appeared meanwhile; try again.")
        os.replace(tmp, out)
    try:
        from agent_friday.services import action_ledger
        action_ledger.try_append("export", {"event": "support_bundle", "by": str(by)[:40],
                                            "members": len(files), "file": out.name})
    except Exception:  # noqa: BLE001
        pass
    return {"file": str(out), "members": sorted(files), "notes": c["notes"]}
