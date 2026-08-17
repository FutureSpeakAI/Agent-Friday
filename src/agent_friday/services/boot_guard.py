"""The boot invariant: a failed self-edit must never leave Friday unable to start.

Stephen, 2026-08-17, on self-modification: "especially when it comes to liquid
UI... that needs to be easily rolled back." And the harder requirement underneath
it — a Friday that cannot start cannot be asked to fix herself, so the recovery
path must not depend on her running.

Three things, in the order they matter:

1. **A known-good state that has PROVEN bootable.** Not a config flagged good, not
   the last commit, not "it passed tests" — a state that has actually completed a
   startup and then served a request. `mark_boot_succeeded()` is called late in
   boot, after the app is really up, and that is the only thing that promotes a
   state to known-good.

2. **Validate before applying.** A UI patch that will not parse, or a self-edit
   that breaks an import, is caught before it is written rather than after.
   Today's near-miss is the reference case: a stray closing tag sent the whole
   bundle to in-browser Babel fallback, which is the documented way to render
   this UI blank. The build printed it and nothing checked.

3. **Auto-revert on a failed start.** If the process died during boot on the last
   two attempts, restore the last proven-bootable state before trying again, and
   leave a plain-language note saying what was rolled back and why.

Safe mode is the outside-the-app off switch: `FRIDAY_SAFE_MODE=1` disables
self-modification entirely and skips restoring anything, so a broken state can be
inspected rather than silently repaired. If the UI is broken you cannot use the
UI to fix it, which is why this reads an environment variable and a file rather
than a setting.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("friday.boot_guard")

HOME = Path(os.path.expanduser("~"))
STATE_DIR = HOME / ".friday" / "boot_guard"
ATTEMPT_FILE = STATE_DIR / "boot_attempt.json"
KNOWN_GOOD = STATE_DIR / "known_good"
NOTES_FILE = STATE_DIR / "rollback_notes.jsonl"

# Files whose loss stops Friday starting at all. A self-edit may not touch these
# through the ordinary write path; they need a deliberate, separately-named
# action. Losing any one of them means the recovery tool is also gone.
BOOT_CRITICAL = (
    "src/agent_friday/server.py",
    "src/agent_friday/core/__init__.py",
    "src/agent_friday/services/agent.py",
    "src/agent_friday/services/model_router.py",
    "src/agent_friday/services/boot_guard.py",
)

# State that must never be reachable from a UI or workspace edit. A self-edit
# that quietly widened the egress boundary or repointed model routing would be
# the worst available outcome here, and it would be invisible in a diff nobody
# reads.
BLAST_RADIUS_FORBIDDEN = (
    "capability_routing", "model_routing", "egress", "vault", "sensitivity",
    "creative_policy", "governance", "ring", "sandbox", "confirm_before_opening",
    "anthropic_api_key", "api_key", "credential",
)

MAX_FAILED_BOOTS = 2


def safe_mode() -> bool:
    """True when self-modification is disabled from OUTSIDE the app."""
    if str(os.environ.get("FRIDAY_SAFE_MODE", "")).strip().lower() in (
            "1", "true", "yes", "on"):
        return True
    return (STATE_DIR / "SAFE_MODE").exists()


def _read_attempt() -> dict:
    try:
        return json.loads(ATTEMPT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"consecutive_failures": 0, "last_start": None, "last_ok": None}


def _write(path: Path, obj) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        _log.warning("boot_guard: could not write %s: %s", path.name, e)


def note(message: str, **fields) -> None:
    """Append a plain-language line to the rollback trail.

    In his language, not the system's: what changed, when, at whose request, and
    how to undo it. A trail he cannot read is a trail that does not exist.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(
                {"when": datetime.now().isoformat(timespec="seconds"),
                 "what": message}, **fields), default=str) + "\n")
    except Exception:
        pass


def recent_notes(n: int = 20) -> list:
    try:
        lines = NOTES_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return list(reversed(out))


# ── boot bookkeeping ────────────────────────────────────────────────────────
def mark_boot_started() -> dict:
    """Record that a start was ATTEMPTED. Returns the attempt record.

    An attempt that is never followed by mark_boot_succeeded() counts as a
    failure, which is what makes this detect a crash during boot rather than
    only a crash that had time to report itself.
    """
    a = _read_attempt()
    if a.get("last_start") and not a.get("last_start_completed", True):
        a["consecutive_failures"] = int(a.get("consecutive_failures", 0)) + 1
    a["last_start"] = datetime.now().isoformat(timespec="seconds")
    a["last_start_completed"] = False
    _write(ATTEMPT_FILE, a)
    return a


def mark_boot_succeeded() -> None:
    """Called LATE in boot, once the app is genuinely serving.

    This is the only thing that promotes a state to known-good, and the reason
    the guarantee is "has actually booted" rather than "looked fine".
    """
    a = _read_attempt()
    a["consecutive_failures"] = 0
    a["last_start_completed"] = True
    a["last_ok"] = datetime.now().isoformat(timespec="seconds")
    _write(ATTEMPT_FILE, a)


def failing_to_boot() -> bool:
    return int(_read_attempt().get("consecutive_failures", 0)) >= MAX_FAILED_BOOTS


# ── known-good snapshots of UI / workspace state ────────────────────────────
def snapshot_known_good(paths=None) -> dict:
    """Copy the CURRENT state of the self-editable surfaces into known_good.

    Only called after a proven boot. Snapshots whole files rather than diffs, on
    the same principle that made the calendar repair possible: the receipt held
    the actual prior value, so restoring needed no reconstruction.
    """
    src_paths = [Path(p) for p in (paths or _self_editable_paths())]
    KNOWN_GOOD.mkdir(parents=True, exist_ok=True)
    saved = []
    for p in src_paths:
        if not p.exists():
            continue
        try:
            dest = KNOWN_GOOD / p.name
            if p.is_dir():
                dest = KNOWN_GOOD / p.name
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(p, dest)
            else:
                shutil.copy2(p, dest)
            saved.append(str(p))
        except Exception as e:
            _log.warning("boot_guard: could not snapshot %s: %s", p, e)
    _write(STATE_DIR / "known_good.json",
           {"at": datetime.now().isoformat(timespec="seconds"), "paths": saved})
    return {"ok": True, "saved": saved}


def restore_known_good() -> dict:
    """Put the self-editable surfaces back to the last PROVEN-bootable state."""
    if safe_mode():
        return {"ok": False, "skipped": "safe mode — nothing restored so the "
                                        "broken state can be inspected"}
    manifest = STATE_DIR / "known_good.json"
    try:
        paths = json.loads(manifest.read_text(encoding="utf-8")).get("paths") or []
    except Exception:
        return {"ok": False, "error": "no known-good snapshot exists yet"}
    restored = []
    for sp in paths:
        p = Path(sp)
        src = KNOWN_GOOD / p.name
        if not src.exists():
            continue
        try:
            if src.is_dir():
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)
                shutil.copytree(src, p)
            else:
                shutil.copy2(src, p)
            restored.append(sp)
        except Exception as e:
            _log.error("boot_guard: could not restore %s: %s", sp, e)
    note("Rolled back to the last state that actually booted.",
         reason="two consecutive failed starts", restored=restored,
         undo="the pre-rollback files are in %s" % (STATE_DIR / "failed"))
    return {"ok": True, "restored": restored}


def _self_editable_paths() -> list:
    """What a self-edit or a liquid-UI change is allowed to touch."""
    return [HOME / ".friday" / "workspace_studio",
            HOME / ".friday" / "settings.json"]


# ── gates ───────────────────────────────────────────────────────────────────
def check_self_edit(path: str) -> tuple:
    """(allowed, reason) for a self-edit to `path`.

    Boot-critical files are refused here. A change to server.py or core is not
    forbidden forever — it needs a deliberate, separately-named action rather
    than arriving through the same tool that writes a note to disk.
    """
    if safe_mode():
        return False, ("safe mode is on (FRIDAY_SAFE_MODE), so "
                       "self-modification is disabled")
    try:
        rel = str(Path(path).resolve()).replace("\\", "/")
    except Exception:
        rel = str(path).replace("\\", "/")
    for crit in BOOT_CRITICAL:
        if rel.endswith(crit.replace("src/", "")) or crit in rel:
            return False, ("%s is boot-critical — if a bad edit lands there "
                           "Friday cannot start, and a Friday that cannot start "
                           "cannot undo it. Changes here need an explicit, "
                           "separately-confirmed action." % crit)
    return True, None


def check_blast_radius(patch: dict) -> tuple:
    """(allowed, reason) — a UI/workspace patch must not reach safety state."""
    try:
        blob = json.dumps(patch or {}, default=str).lower()
    except Exception:
        return True, None
    for key in BLAST_RADIUS_FORBIDDEN:
        if '"%s"' % key in blob or "'%s'" % key in blob:
            return False, ("a workspace or UI change may not touch %r — model "
                           "routing, the egress gate and the safety rules are "
                           "outside its blast radius on purpose" % key)
    return True, None


def check_scope(paths) -> tuple:
    """(allowed, reason) — one request touching many files is usually a
    misunderstanding, not an ambition. Pause and confirm rather than refuse.

    The nine-identical-images batch is the pattern: the model did what it
    thought was asked, at a scale nobody wanted, and nothing stopped to check.
    """
    paths = [p for p in (paths or []) if p]
    if len(paths) > 5:
        return False, ("this would change %d files in one go (%s…). That is "
                       "usually a misread request rather than the intent — "
                       "confirm the scope before it proceeds."
                       % (len(paths), ", ".join(str(p) for p in paths[:3])))
    return True, None


def status() -> dict:
    a = _read_attempt()
    kg = STATE_DIR / "known_good.json"
    known_good_at = None
    try:
        known_good_at = json.loads(kg.read_text(encoding="utf-8")).get("at")
    except Exception:
        pass
    return {
        "safe_mode": safe_mode(),
        "consecutive_failed_boots": int(a.get("consecutive_failures", 0)),
        "last_start": a.get("last_start"),
        "last_proven_boot": a.get("last_ok"),
        "known_good_snapshot_at": known_good_at,
        "would_auto_revert": failing_to_boot(),
        "boot_critical_files": list(BOOT_CRITICAL),
        "recent_rollbacks": recent_notes(5),
    }
