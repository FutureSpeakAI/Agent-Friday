"""Friday's core instructions as versioned files, with history and undo.

What this replaces
------------------
Friday's core instructions (who she is, the autonomous-operation rule, what she
can do on this computer, honest degradation, and so on) used to be one Python
string constant in ``model_router``. Nothing could test a change to one part of
it, nothing recorded who changed it, and nothing could put it back.

Now each section is a Markdown file under ``resources/instructions/``, listed
in ``manifest.json`` in the order they are joined. With no override the joined
text is byte-identical to the old constant (``tests/unit/
test_instruction_files.py`` pins it).

Overrides
---------
An unlocked section may be replaced by ``<FRIDAY_HOME>/instructions/active/
<id>.md``. Only three things write one, all through ``write_override``: an
approved trial card (``services/trials``), the owner, and ``undo``. Each write
keeps the text it replaced under ``history/<id>/<n>.md`` and appends one line
to ``history/history.jsonl`` (who, when, which card, before and after hashes,
the reason).

An override is used only when all of these hold, otherwise the shipped text is
used and ``problems()`` says why (it never crashes the prompt):

* its signature (``<id>.sig.json``, HMAC-SHA256 over the id, the text's hash
  and the card id) verifies with the governance key. A file edited outside
  Friday therefore fails and is ignored. With no key, nothing is written and
  nothing is loaded (fail closed);
* it passes ``validate``: at most the shipped length + 15 %, no template
  braces, no sentence that claims authority over the action policy;
* the section is not locked.

Locked text
-----------
Sections that carry safety rules (the real-world-action rule in AUTONOMOUS
OPERATION, HONEST DEGRADATION) are ``locked``: an override of them is ignored
at load and refused at write, and ``services/trials`` refuses them as subjects.
They change only through a reviewed commit.

The three policy texts (the action permission policy, the cLaws, the honesty
floor) are also shipped here as ``policy/*.md``, for transparency and diffing
only. They are mirrors: the code constants stay the runtime source, nothing in
this module loads them into a prompt, and a guard test requires each mirror to
equal its constant byte for byte. That way a missing or edited file can never
weaken a safety rule.

Practice-task trials
--------------------
A trial child process (``FRIDAY_TRIAL=1``, a scratch ``FRIDAY_HOME``) receives
the candidate text in its scratch ``instructions/active/``, signed with an
ephemeral per-trial key passed only to that child in
``FRIDAY_TRIAL_INSTRUCTION_KEY``. In that mode the governance key is never
read (so a scratch child cannot touch the owner's keychain) and only the trial
key verifies. Outside trial mode the trial key is ignored.
"""
from __future__ import annotations

import difflib
import hashlib
import hmac as _hmac
import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

SHIPPED_DIR = Path(__file__).resolve().parent.parent / "resources" / "instructions"

#: An override may be at most this much longer than the shipped section.
SECTION_GROWTH = 0.15
#: All always-on sections together may grow at most this much.
TOTAL_GROWTH = 0.10

_TEMPLATE_MARKS = ("{{", "}}", "{%", "%}")

_LOCK = threading.RLock()
_MANIFEST: Optional[dict] = None
_SHIPPED: Dict[str, str] = {}
_LOAD_CACHE: Dict[str, tuple] = {}
_PROBLEMS: Dict[str, str] = {}
#: Sections whose signed override is paused because the shipped text changed.
_PAUSED: Dict[str, bool] = {}


from agent_friday.user_errors import UserFacingError as _UserFacingError  # noqa: E402


class InstructionError(_UserFacingError, ValueError):
    """A write or undo that was refused. The message says why, in plain words,
    and is safe to show the owner."""


# ── the shipped set ─────────────────────────────────────────────────────────

def _read_text(path: Path) -> str:
    # LF regardless of how the file was checked out: the manifest hashes LF text.
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def manifest() -> dict:
    global _MANIFEST
    with _LOCK:
        if _MANIFEST is None:
            _MANIFEST = json.loads((SHIPPED_DIR / "manifest.json").read_text(encoding="utf-8"))
        return _MANIFEST


def sections() -> List[dict]:
    """The prompt sections in the order they are joined (copies). A locked
    section an edited manifest left out is put back from the code, in its
    own place: the manifest can never drop a safety rule."""
    from agent_friday.services.instruction_locked import LOCKED_ORDER
    rows = [dict(s) for s in manifest()["sections"]]
    have = {s["id"] for s in rows}
    for sid, order in LOCKED_ORDER.items():
        if sid not in have:
            _PROBLEMS[sid] = "the manifest left out a locked section; the built-in text is used"
            rows.append({"id": sid, "file": f"{sid}.md", "title": sid, "slot": "always_on",
                         "order": order, "version": 1, "sha256": "", "locked": True, "owner": "shipped"})
    return sorted(rows, key=lambda s: s["order"])


def section_ids() -> List[str]:
    return [s["id"] for s in sections()]


def policies() -> List[dict]:
    return [dict(p) for p in manifest().get("policies", [])]


def _entry(sid: str) -> dict:
    for s in manifest()["sections"]:
        if s["id"] == sid:
            return s
    for p in manifest().get("policies", []):
        if p["id"] == sid:
            return p
    raise KeyError(sid)


def is_locked(sid: str) -> bool:
    """True for a safety section, a policy mirror, or anything unknown. The
    code's own list (instruction_locked) wins over the manifest, so an edited
    manifest cannot unlock a safety section."""
    from agent_friday.services.instruction_locked import LOCKED_SECTIONS
    if sid in LOCKED_SECTIONS or str(sid).startswith("policy/"):
        return True
    try:
        return bool(_entry(sid).get("locked", True))
    except KeyError:
        return True


def shipped_text(sid: str) -> str:
    """The shipped text of a section. A locked section comes from the code
    (instruction_locked), never from its data file: the file is a mirror, and
    a mismatch is a health row, not a change in what Friday is told."""
    from agent_friday.services.instruction_locked import LOCKED_SECTIONS
    with _LOCK:
        if sid not in _SHIPPED and sid in LOCKED_SECTIONS:
            try:
                _entry(sid)
            except KeyError:
                _SHIPPED[sid] = LOCKED_SECTIONS[sid]
        if sid not in _SHIPPED:
            entry = _entry(sid)
            try:
                text = _read_text(SHIPPED_DIR / entry["file"])
            except OSError:
                if sid not in LOCKED_SECTIONS:
                    raise
                text = LOCKED_SECTIONS[sid]
            if sid in LOCKED_SECTIONS:
                if text != LOCKED_SECTIONS[sid]:
                    _PROBLEMS[sid] = ("the shipped file of a locked section was changed; the "
                                      "built-in text is used")
                text = LOCKED_SECTIONS[sid]
            elif hashlib.sha256(text.encode("utf-8")).hexdigest() != entry["sha256"]:
                # A corrupted or hand-edited install. The text is still the
                # only copy there is, so it is used, and the health row says so.
                _PROBLEMS[sid] = "the shipped file does not match its manifest hash"
            _SHIPPED[sid] = text
        return _SHIPPED[sid]


def shipped_core() -> str:
    return "".join(shipped_text(s["id"]) for s in sections())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── where overrides live ────────────────────────────────────────────────────

def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "instructions"


def _active_path(sid: str) -> Path:
    return _home() / "active" / f"{sid}.md"


def _sig_path(sid: str) -> Path:
    return _home() / "active" / f"{sid}.sig.json"


def _history_dir() -> Path:
    return _home() / "history"


def trial_mode() -> bool:
    """A practice-task child: started by task_eval.runner with every one of
    these set. The real app never runs with them, so a stray FRIDAY_TRIAL in
    its environment alone cannot make it accept a self-signed override."""
    e = os.environ
    return (e.get("FRIDAY_TRIAL") == "1" and e.get("FRIDAY_TESTING") == "1"
            and e.get("FRIDAY_NO_ARBITER") == "1" and bool(e.get("FRIDAY_HOME")))


def _key() -> bytes:
    """The signing key: the per-trial key inside a trial child, otherwise the
    governance key. Raises when there is none; callers fail closed."""
    if trial_mode():
        raw = os.environ.get("FRIDAY_TRIAL_INSTRUCTION_KEY", "")
        try:
            key = bytes.fromhex(raw)
        except ValueError:
            key = b""
        if len(key) < 16:
            raise RuntimeError("no trial instruction key in this trial process")
        return key
    from agent_friday.governance.action_gate import _governance_key
    return _governance_key()


def sign(sid: str, text: str, card: str, key: bytes, base_sha256: str) -> str:
    """HMAC over the section id, the override's hash, the card that approved
    it, and the hash of the SHIPPED text it replaced. The last binds an
    override to one shipped version: after an update rewrites that section,
    the override no longer applies (see _load_override)."""
    body = json.dumps({"id": sid, "sha256": _sha(text), "card": str(card or ""),
                       "base_sha256": str(base_sha256 or "")},
                      sort_keys=True).encode("utf-8")
    return _hmac.new(key, body, hashlib.sha256).hexdigest()


def shipped_sha(sid: str) -> str:
    return _sha(shipped_text(sid))


# ── validation (deterministic, code) ────────────────────────────────────────

def cap_chars(sid: str) -> int:
    return int(math.ceil(len(shipped_text(sid)) * (1 + SECTION_GROWTH)))


def total_cap_chars() -> int:
    return int(math.ceil(len(shipped_core()) * (1 + TOTAL_GROWTH)))


def validate(sid: str, text) -> List[str]:
    """Reasons `text` may not replace section `sid`; empty when it may."""
    problems: List[str] = []
    try:
        _entry(sid)
    except KeyError:
        return [f"there is no instruction section called {sid!r}"]
    if is_locked(sid):
        problems.append("this section carries safety rules and changes only in a software update")
    if not isinstance(text, str) or not text.strip():
        return problems + ["the text is empty"]
    if "\x00" in text:
        problems.append("the text contains a NUL character")
    if len(text) > cap_chars(sid):
        problems.append(f"the text is {len(text)} characters; the limit for this section is {cap_chars(sid)}")
    if any(m in text for m in _TEMPLATE_MARKS):
        problems.append("the text contains template braces")
    try:
        from agent_friday.services.action_policy import contains_authority_override
        if contains_authority_override(text):
            problems.append("the text claims authority over the action permission policy")
    except Exception:
        problems.append("the authority-override check could not run")
    return problems


# ── loading ─────────────────────────────────────────────────────────────────

def _stat(path: Path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _load_override(sid: str) -> Optional[str]:
    """The verified override text for `sid`, or None (and a problem noted)."""
    path, sig = _active_path(sid), _sig_path(sid)
    st = (_stat(path), _stat(sig))
    if st[0] is None:
        _PROBLEMS.pop(sid, None)
        return None
    cached = _LOAD_CACHE.get(sid)
    if cached and cached[0] == st:
        return cached[1]
    _PAUSED.pop(sid, None)
    text = None
    reason = ""
    try:
        cand = _read_text(path)
        meta = json.loads(sig.read_text(encoding="utf-8")) if st[1] else None
        if not isinstance(meta, dict) or meta.get("id") != sid or not isinstance(meta.get("mac"), str):
            reason = "the override has no valid signature record"
        else:
            try:
                key = _key()
            except Exception:
                key = None
            if key is None:
                reason = "the signing key is unavailable, so the override cannot be checked"
            elif not _hmac.compare_digest(sign(sid, cand, meta.get("card", ""), key,
                                               meta.get("base_sha256", "")), meta["mac"]):
                reason = "the override's signature does not match (edited outside Friday?)"
            elif meta.get("base_sha256") != shipped_sha(sid):
                # An update changed this section's shipped text after the owner
                # approved their version. Their version is paused, not lost:
                # Settings shows both, and they decide again.
                reason = ("an update changed this section after your version was approved, so your "
                          "version is paused and the new shipped text is used")
                _PAUSED[sid] = True
            else:
                bad = validate(sid, cand)
                if bad:
                    reason = "; ".join(bad)
                else:
                    text = cand
    except Exception as e:  # unreadable, not UTF-8, bad JSON
        reason = f"the override could not be read ({type(e).__name__})"
    if text is None:
        _PROBLEMS[sid] = (reason if _PAUSED.get(sid)
                          else f"ignored {path.name}: {reason}; using the shipped text")
    else:
        _PROBLEMS.pop(sid, None)
    _LOAD_CACHE[sid] = (st, text)
    return text


def load(sid: str) -> str:
    """The text Friday uses for `sid`: a verified override, else the shipped text.
    Locked sections always come from the shipped files."""
    if is_locked(sid):
        if _stat(_active_path(sid)) is not None:
            _PROBLEMS[sid] = f"ignored an override of locked section {sid}"
        return shipped_text(sid)
    try:
        override = _load_override(sid)
    except Exception as e:
        _PROBLEMS[sid] = f"override check failed ({type(e).__name__}); using the shipped text"
        override = None
    return override if override is not None else shipped_text(sid)


def assemble_core() -> str:
    """Friday's core instructions, joined. Never raises for a bad override: the
    worst case is the shipped text with a health row."""
    try:
        parts = [load(s["id"]) for s in sections()]
        text = "".join(parts)
        if len(text) > total_cap_chars():
            _PROBLEMS["core"] = (f"the overrides together are {len(text)} characters, over the "
                                 f"{total_cap_chars()} limit; using the shipped text")
            return shipped_core()
        _PROBLEMS.pop("core", None)
        return text
    except Exception as e:
        _PROBLEMS["core"] = f"assembling the instructions failed ({type(e).__name__}); using the shipped text"
        return shipped_core()


def problems() -> Dict[str, str]:
    """Health rows: why an override was ignored, by section id."""
    return dict(_PROBLEMS)


# ── writing, history and undo ───────────────────────────────────────────────

def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def history(sid: Optional[str] = None) -> List[dict]:
    path = _history_dir() / "history.jsonl"
    rows: List[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if sid is None or row.get("id") == sid:
                rows.append(row)
    except OSError:
        pass
    return rows


def _append_history(row: dict) -> None:
    path = _history_dir() / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _keep_previous(sid: str, text: str) -> str:
    d = _history_dir() / sid
    d.mkdir(parents=True, exist_ok=True)
    n = len([p for p in d.glob("*.md")]) + 1
    _write_atomic(d / f"{n}.md", text.encode("utf-8"))
    return f"{sid}/{n}.md"


def write_override(sid: str, text: str, *, actor: str, card: str = "", reason: str = "") -> dict:
    """Make `text` the active version of section `sid`.

    `actor` names who decided: ``owner:ui`` for the owner's own edit, ``card:<id>``
    for an approved trial card, ``undo``. Refused (InstructionError) for a locked
    section, text that fails validation, or a missing key; nothing is written then.
    """
    if not actor:
        raise InstructionError("a change must name who made it")
    bad = validate(sid, text)
    if bad:
        raise InstructionError("; ".join(bad))
    try:
        key = _key()
    except Exception:
        raise InstructionError("the governance key is unavailable, so the change cannot be signed")
    with _LOCK:
        before = load(sid)
        if before == text:
            raise InstructionError("the text is the same as the version in use")
        kept = _keep_previous(sid, before)
        _write_atomic(_active_path(sid), text.encode("utf-8"))
        base = shipped_sha(sid)
        meta = {"id": sid, "card": str(card or ""), "actor": actor, "at": time.time(),
                "base_sha256": base, "mac": sign(sid, text, card, key, base)}
        _write_atomic(_sig_path(sid), json.dumps(meta, sort_keys=True).encode("utf-8"))
        _LOAD_CACHE.pop(sid, None)
        row = {"at": meta["at"], "id": sid, "action": "write", "actor": actor, "card": str(card or ""),
               "reason": str(reason or "")[:500], "before_sha256": _sha(before),
               "after_sha256": _sha(text), "before_file": kept, "base_sha256": base}
        _append_history(row)
        return row


def undo(sid: str, *, actor: str = "undo") -> dict:
    """Put back the text that was in use before the last change to `sid`."""
    rows = history(sid)
    if not rows:
        raise InstructionError("this section has never been changed")
    last = rows[-1]
    try:
        previous = _read_text(_history_dir() / last["before_file"])
    except Exception:
        raise InstructionError("the previous version is missing from the history folder")
    if _sha(previous) != last.get("before_sha256"):
        raise InstructionError("the previous version in the history folder does not match its record")
    with _LOCK:
        current = load(sid)
        # Back to the shipped text, never to an older shipped version: when the
        # text being restored was the shipped text at the time of the change,
        # or the change is paused because an update rewrote the section,
        # undoing it means using what ships now.
        was_shipped = bool(last.get("base_sha256")) and last.get("before_sha256") == last.get("base_sha256")
        if previous == shipped_text(sid) or was_shipped or _PAUSED.get(sid):
            kept = _keep_previous(sid, current)
            for p in (_active_path(sid), _sig_path(sid)):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            _LOAD_CACHE.pop(sid, None)
            row = {"at": time.time(), "id": sid, "action": "undo", "actor": actor, "card": "",
                   "reason": "back to the shipped text", "before_sha256": _sha(current),
                   "after_sha256": _sha(shipped_text(sid)), "before_file": kept}
            _append_history(row)
            return row
    row = write_override(sid, previous, actor=actor, card="", reason="undo")
    return row


def diff(a: str, b: str, *, label_a: str = "current", label_b: str = "candidate") -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
                                        fromfile=label_a, tofile=label_b))


def status() -> List[dict]:
    """One row per section and policy mirror, for Settings and the API."""
    out = []
    for s in sections() + policies():
        sid = s["id"]
        rows = history(sid)
        text = load(sid) if sid in section_ids() else shipped_text(sid)
        out.append({
            "id": sid, "title": s.get("title", sid), "locked": bool(s.get("locked")),
            "shipped_version": s.get("version", 1),
            "changes": len(rows),
            "changed_by": rows[-1]["actor"] if rows and text != shipped_text(sid) else "shipped",
            "changed_at": rows[-1]["at"] if rows and text != shipped_text(sid) else None,
            "chars": len(text), "cap_chars": None if s.get("locked") else cap_chars(sid),
            "problem": _PROBLEMS.get(sid, ""), "paused": bool(_PAUSED.get(sid)),
            "can_undo": bool(rows) and not s.get("locked"),
        })
    return out


def detail(sid: str) -> dict:
    """Everything the owner needs to judge one section: the shipped text, the
    text in use, a stored override that is paused or ignored (and why), the
    difference, and the history of changes. Read-only."""
    shipped = shipped_text(sid)
    in_use = load(sid) if sid in section_ids() else shipped
    stored = None
    try:
        if _active_path(sid).exists():
            stored = _read_text(_active_path(sid))
    except OSError:
        stored = None
    return {"id": sid, "locked": is_locked(sid), "shipped": shipped, "in_use": in_use,
            "stored_override": stored, "paused": bool(_PAUSED.get(sid)),
            "problem": _PROBLEMS.get(sid, ""),
            "diff": diff(shipped, in_use, label_a="shipped", label_b="in use") if in_use != shipped else "",
            "history": history(sid)}


def _reset_caches_for_tests() -> None:
    global _MANIFEST
    with _LOCK:
        _MANIFEST = None
        _SHIPPED.clear()
        _LOAD_CACHE.clear()
        _PROBLEMS.clear()
        _PAUSED.clear()
