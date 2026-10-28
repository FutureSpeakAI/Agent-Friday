"""The support bundle (north star §36.0-9; A3): one zip, made on this PC by the
owner's press, to attach by hand when asking for help. Friday never sends it.

What is in it:

* the Doctor report, as .json and .txt (services/doctor.py: no content, no
  file names, no addresses);
* the store inventory (services/store_inventory.public);
* log excerpts: the newest warning and error lines of friday.log, the traceback
  lines of server_stderr.log and the crash lines of logs/crashes.log, redacted
  word by word (below);
* the start-up guard's newest notes, redacted the same way;
* README.txt, and manifest.json with each file's SHA-256.

How a log line is redacted. A line keeps its time, level and logger name (a
logger outside Friday's own is "other_logger"). Every other word is kept only
when it is one of a fixed list of plain log words, a small number (at most
three digits) or a number with its unit, a code name (snake_case made of those
words, a dotted Friday module, a built-in exception class), or an HTTP method;
any other word becomes "~" (runs of them one "..."), and numbers written in
groups become "#". Paths, web addresses, email addresses, keys, quoted text,
this PC's user and host names (and any owner name settings.json holds) are
replaced before that. A traceback frame keeps its line number and function,
and its file only when it is one of Friday's own (agent_friday/...).
So a name, a sentence or a file name the owner wrote does not survive; what is
left is the shape of the error.

Never in it: conversations, memories, wiki pages, settings values, the vault,
credentials, contacts, file names from the data folder. Nothing is made while
off the record; log excerpts are left out for 30 days after an off-record
session (the same rule as the weekly report), and after that only lines written
since it are kept. The zip is written outside
Friday's data folder and never overwrites an earlier one; a folder synced to
the cloud (OneDrive and the like) is asked about first. One receipt (kind
'export') goes into the action ledger.
"""
from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional

from agent_friday.user_errors import UserFacingValueError

SUPPORT_VERSION = 1
TAIL_BYTES = 2_000_000
MAX_LINES_PER_LOG = 300
FOLDER_NAME = "Friday Support"


class SupportError(UserFacingValueError):
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
#: A line is cut to this length before any pattern looks at it.
MAX_LINE = 4000
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]{0,30}://\S{0,2000}")
_EMAIL = re.compile(r"[^\s@<>\"']{1,64}@[^\s@<>\"']{1,255}")
_WINPATH = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][^\\/\s\"'<>|:*?]{1,255}){2,64}[\\/]?"
                      r"|[A-Za-z]:[\\/]\S{0,2000}|\\\\[^\s\\/]{1,255}[\\/]\S{0,2000}")
_QUOTED = re.compile(r"\"[^\"]{0,2000}\"|'[^']{0,2000}'")
#: A number is kept when it is small (at most three digits, at most two
#: decimals) or carries a unit: a phone number, a code, a card or a date of
#: birth never does.
_SMALL = re.compile(r"^[-+]?\d{1,3}(\.\d{1,2})?%?$")
_WITH_UNIT = re.compile(r"^[-+]?\d{1,9}(\.\d{1,3})?(ms|s|kb|mb|gb|%)$", re.I)
_UNITS = {"ms", "s", "sec", "secs", "second", "seconds", "minute", "minutes", "hour", "hours",
          "byte", "bytes", "kb", "mb", "gb", "%"}
#: key=value pairs whose (whole-number) value is kept.
_NUMBER_KEYS = {"status", "port", "pid", "line", "attempt", "attempts", "retries", "count", "code",
                "seq", "errors", "size", "bytes", "ms", "timeout"}
_DIGITS = re.compile(r"^\d{1,9}$")
_SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")
_FRIDAY_MODULE = re.compile(r"^agent_friday(\.[a-z_][a-z0-9_]*){1,8}$")
_EXC = re.compile(r"^[A-Z][A-Za-z0-9]*(Error|Exception|Warning|Exit|Interrupt)$")
_STRIP = ",.;:()[]{}!?"
_LINE = re.compile(r"^(?P<ts>\S{1,40}) (?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s{1,20}"
                   r"(?P<logger>[A-Za-z_][A-Za-z0-9_.]{0,80}) — (?P<msg>.*)$")
_FRAME = re.compile(r'^\s*File "(?P<file>[^"]{0,1000})", line (?P<line>\d{1,7})(?:,? in (?P<fn>\S{1,200}))?')
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?$")
_PLACEHOLDERS = ("<path>", "<url>", "<email>", "<text>", "<withheld>", "<time>")


def _markers() -> list[str]:
    """This PC's user, host and home names (the Doctor's list), and the
    owner's own names from settings."""
    from agent_friday.services import doctor
    out = set(doctor._private_markers())
    try:
        d = json.loads((_home() / "settings.json").read_text(encoding="utf-8"))
        for k in ("user_name", "owner_name", "display_name", "owner_email", "email", "full_name"):
            v = d.get(k) if isinstance(d, dict) else None
            if isinstance(v, str) and len(v.strip()) >= 3:
                out.add(v.strip().lower())
                out.update(p.lower() for p in v.split() if len(p) >= 3)
    except (OSError, ValueError):
        pass
    return sorted(out, key=len, reverse=True)


def _is_number_word(core: str) -> bool:
    return bool(_SMALL.match(core) or _WITH_UNIT.match(core))


def _word(tok: str, first: bool, nxt: str = "") -> str:
    """One word kept as it is, or '~'. `first`: the message's first word (a
    capital there is the sentence's, not a name's). `nxt`: the next word, for
    a number followed by its unit."""
    core = tok.strip(_STRIP)
    if not core or core in _PLACEHOLDERS:
        return tok
    low = core.lower()
    if low in WORDS and (core == low or first or core.isupper() and len(core) <= 8):
        return tok
    if core in _METHODS or core in _LEVELS:
        return tok
    if _TS.match(core):
        return "<time>"                      # a time in the words can be an appointment's
    if _is_number_word(core):
        return tok
    if _DIGITS.match(core) and nxt.strip(_STRIP).lower() in _UNITS:
        return tok
    if _FRIDAY_MODULE.match(core) and core in sys.modules:
        return tok                           # a module Friday has loaded, not a made-up name
    if _SNAKE.match(core) and all(p in WORDS or p.isdigit() for p in core.split("_")):
        return tok                           # read_timeout, not_found: never a name
    if _EXC.match(core):
        return tok if isinstance(getattr(builtins, core, None), type) else \
            tok.replace(core, "other_exception")
    if "=" in core:
        k, _, v = core.partition("=")
        if k in WORDS or (_SNAKE.match(k) and all(p in WORDS for p in k.split("_"))):
            if k in _NUMBER_KEYS and _DIGITS.match(v) and len(v) <= 6 and (k != "code" or int(v) < 600):
                return k + "=" + v
            return k + "=" + (_word(v, False) if v else "")
    return "~"


def redact_text(text: str, markers: Optional[list] = None) -> str:
    """Free text reduced to its plain log words (see the module doc)."""
    from agent_friday.services import pasted_secrets
    markers = _markers() if markers is None else markers
    s = str(text)[:MAX_LINE]
    s = pasted_secrets.withhold(s, whole=False)[0]
    s = _URL.sub("<url>", s)
    s = _EMAIL.sub("<email>", s)
    s = _QUOTED.sub("<text>", s)
    s = _WINPATH.sub("<path>", s)
    low = s.lower()
    for m in markers:
        if m and m in low:
            s = re.sub(re.escape(m), "<withheld>", s, flags=re.I)
            low = s.lower()
    toks = s.split()
    out: list[str] = []
    for n, tok in enumerate(toks):
        w = _word(tok, n == 0, toks[n + 1] if n + 1 < len(toks) else "")
        if w == "~" and out and out[-1] in ("~", "..."):
            out[-1] = "..."
            continue
        # two numbers side by side can be one number written in groups
        if out and _SMALL.match(w.strip(_STRIP)) and _SMALL.match(out[-1].strip(_STRIP)):
            out[-1] = "#"
            continue
        if out and out[-1] == "#" and _SMALL.match(w.strip(_STRIP)):
            continue
        out.append(w)
    return " ".join(out)[:400]


def _own_file(rel: str) -> bool:
    """Whether agent_friday/<rel> is one of Friday's own installed files."""
    try:
        import agent_friday
        pkg = Path(agent_friday.__file__).resolve().parent
        return (pkg / rel).is_file()
    except Exception:  # noqa: BLE001
        return False


def _frame(m: re.Match) -> str:
    f = m.group("file").replace("\\", "/")
    i = f.rfind("agent_friday/")
    rel = f[i + len("agent_friday/"):] if i >= 0 else ""
    own = bool(rel) and re.fullmatch(r"([a-z0-9_]+/){0,6}[a-z0-9_]+\.py", rel) is not None and _own_file(rel)
    where = "agent_friday/" + rel if own else "<other>"
    fn = m.group("fn") or ""
    # a function name is kept only in Friday's own files: elsewhere (the
    # owner's scripts, an extension) it can be a word the owner chose
    fn = fn if own and re.fullmatch(r"[<>A-Za-z0-9_.]{1,80}", fn) else "~"
    return '  File "%s", line %s, in %s' % (where, m.group("line"), fn)


def _logger(name: str) -> str:
    from agent_friday.services import doctor
    return doctor._logger_name(name)


def _ts_of(line: str) -> Optional[float]:
    m = _LINE.match(line[:MAX_LINE])
    if not m or not _TS.match(m.group("ts")):
        return None
    from datetime import datetime
    raw = m.group("ts").replace(",", ".")
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.timestamp()


def redact_log_line(line: str, markers: Optional[list] = None) -> Optional[str]:
    """A friday.log line (or a traceback line) redacted, or None when it is
    not a warning, an error or part of a traceback."""
    markers = _markers() if markers is None else markers
    line = line.rstrip("\r\n")[:MAX_LINE]
    m = _LINE.match(line)
    if m:
        if m.group("level") in ("DEBUG", "INFO"):
            return None
        ts = m.group("ts") if _TS.match(m.group("ts")) else "~"
        return "%s %s %s — %s" % (ts, m.group("level"), _logger(m.group("logger")),
                                       redact_text(m.group("msg"), markers))
    f = _FRAME.match(line)
    if f:
        return _frame(f)
    s = line.strip()
    if s.startswith("Traceback (most recent call last)"):
        return "Traceback (most recent call last):"
    if re.match(r"^(Fatal Python error|Windows fatal exception|Current thread|Thread 0x)", s):
        return redact_text(s, markers)
    e = re.match(r"^([A-Za-z_][A-Za-z0-9_.]{0,200}(?:Error|Exception|Exit|Interrupt))(:\s?(.*))?$", s)
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


def log_excerpt(p: Path, markers: Optional[list] = None, crash: bool = False,
                after: Optional[float] = None) -> list[str]:
    """The newest warning, error and traceback lines of `p`, redacted. With
    `after`, only lines written after that time (a line without a time
    belongs to the timed line before it)."""
    markers = _markers() if markers is None else markers
    out = []
    lines = _tail(p).splitlines()
    if lines and not crash:
        lines = lines[1:]                      # the first may be cut mid-line
    keep = after is None
    for raw in lines:
        if after is not None:
            t = _ts_of(raw)
            if t is not None:
                keep = t > after
            if not keep:
                continue
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


class _Unknown(Exception):
    pass


def _last_off_record() -> Optional[float]:
    """When the last off-record session ended (the weekly report's record),
    or None. Raises _Unknown when that cannot be read."""
    try:
        from agent_friday.services import doctor_weekly
        last = doctor_weekly._state().get("last_off_record_at")
        raw = None
        p = doctor_weekly._path()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            raw = d.get("last_off_record_at") if isinstance(d, dict) else "unreadable"
    except Exception as e:  # noqa: BLE001
        raise _Unknown() from e
    if last is None and raw is not None:
        # recorded, but not usable (a time in the future after the clock moved)
        raise _Unknown()
    return float(last) if isinstance(last, (int, float)) and not isinstance(last, bool) else None


def _recently_off_record() -> bool:
    """Off the record within OFF_RECORD_SHADOW_S, or not known."""
    try:
        last = _last_off_record()
    except _Unknown:
        return True
    from agent_friday.services import doctor_weekly
    return bool(last and time.time() - last < doctor_weekly.OFF_RECORD_SHADOW_S)


def default_dest() -> Path:
    from agent_friday.services import friday_bundle
    return friday_bundle.documents_dir() / FOLDER_NAME


class CloudFolder(SupportError):
    """The folder is synced to the cloud: asked first."""


def _check_dest(dest: Path, cloud_ok: bool) -> Path:
    dest = Path(dest).expanduser()
    if not dest.is_absolute():
        raise SupportError("Name the folder in full (for example D:\\Support).")
    home = _home().resolve()
    try:
        dest.resolve().relative_to(home)
    except ValueError:
        pass
    else:
        raise SupportError("Choose a folder outside Friday's own data folder.")
    from agent_friday.services import readable_export
    if readable_export.cloud_synced(dest) and not cloud_ok:
        raise CloudFolder("%s is synced to the cloud, so the support bundle would be copied off "
                          "this PC. Make it there anyway?" % dest)
    return dest


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
    try:
        last = _last_off_record()
        known = True
    except _Unknown:
        last, known = None, False
    if _recently_off_record():
        notes.append("Log excerpts are left out: part of the last 30 days was off the record"
                     + ("." if known else ", or that could not be checked."))
    else:
        home = _home()
        parts = []
        for label, rel in (("friday.log", "friday.log"), ("server_stderr.log", "server_stderr.log")):
            lines = log_excerpt(home / rel, markers, after=last)
            parts.append("== %s: %d line(s) ==" % (label, len(lines)))
            parts += lines
            parts.append("")
        if last is None:
            lines = log_excerpt(home / "logs" / "crashes.log", markers, crash=True)
            parts.append("== logs/crashes.log: %d line(s) ==" % len(lines))
            parts += lines
        else:
            # crash lines carry no time of their own, so none from before an
            # off-record session can be told apart
            notes.append("Crash lines are left out: they carry no time, and an off-the-record "
                         "session ended earlier.")
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
        "  words, small numbers or code names, so names, sentences, paths, addresses, long numbers",
        "  and keys read \"~\", \"...\", \"#\", <path>, <url>, <email> or <withheld>.",
        "- boot-notes.json: the start-up guard's newest notes, redacted the same way.",
        "- manifest.json: each file's SHA-256.",
        "",
        "Read it before you share it.",
    ] + (["", "Notes:"] + ["- " + n for n in notes] if notes else [])
    files["README.txt"] = ("\n".join(readme) + "\n").encode("utf-8")
    return {"files": files, "notes": notes, "report_id": rep.get("id")}


_MAKE_LOCK = threading.Lock()


def make(dest_parent: Optional[Path] = None, *, by: str = "owner", cloud_ok: bool = False) -> dict:
    """Write friday-support-<stamp>.zip into dest_parent (default: Documents,
    Friday Support). Returns {file, members, notes}. One at a time."""
    if not _MAKE_LOCK.acquire(blocking=False):
        raise SupportError("A support bundle is being made already.")
    try:
        return _make(dest_parent, by=by, cloud_ok=cloud_ok)
    finally:
        _MAKE_LOCK.release()


def _make(dest_parent, *, by: str, cloud_ok: bool) -> dict:
    parent = _check_dest(Path(dest_parent) if dest_parent else default_dest(), cloud_ok)
    c = contents()
    files = c["files"]
    manifest = {"support_version": SUPPORT_VERSION,
                "made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "report_id": c["report_id"], "notes": c["notes"],
                "files": {n: hashlib.sha256(b).hexdigest() for n, b in sorted(files.items())}}
    files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            z.writestr(name, data)
    data = buf.getvalue()
    parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = None
    for k in range(1, 100):
        cand = parent / ("friday-support-%s%s.zip" % (stamp, "" if k == 1 else "-%d" % k))
        try:
            # "x": made only if no file of that name exists, so nothing is overwritten
            with open(cand, "xb") as f:
                try:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                except BaseException:
                    f.close()
                    cand.unlink(missing_ok=True)
                    raise
        except FileExistsError:
            continue
        except OSError as e:
            raise SupportError("The support bundle could not be written there (%s)." % type(e).__name__)
        out = cand
        break
    if out is None:
        raise SupportError("Too many support bundles were made this second; try again.")
    try:
        from agent_friday.services import action_ledger
        action_ledger.try_append("export", {"event": "support_bundle", "by": str(by)[:40],
                                            "members": len(files), "file": out.name})
    except Exception:  # noqa: BLE001
        pass
    return {"file": str(out), "members": sorted(files), "notes": c["notes"]}
