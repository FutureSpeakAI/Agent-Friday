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

import hashlib
import json
import logging
import os
import re
import shutil
import sys
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
    # A start that reached serving vindicates any rollback that preceded it, and
    # re-arms the auto-revert for the next bad edit. Without this the loop guard
    # in restore_known_good() would disarm the mechanism permanently after its
    # first use.
    a["restore_pending"] = False
    _write(ATTEMPT_FILE, a)


def failing_to_boot() -> bool:
    return int(_read_attempt().get("consecutive_failures", 0)) >= MAX_FAILED_BOOTS


# ── known-good snapshots: what is covered, and how it is keyed ──────────────
#
# 2026-09-03. Until this change the covered set was `~/.friday/workspace_studio`
# and `~/.friday/settings.json` and nothing else, while the module's headline
# claim was that a failed self-edit must never leave Friday unable to start.
# Neither of those paths can break a boot, so the auto-revert restored things
# that cannot cause the failure it exists to cure. The set below is the app's
# own importable source and the UI entry — the things that CAN stop a start —
# plus the two it always had.
#
# Two consequences of widening it, both handled rather than hoped about:
#
#  * a partial snapshot used to be harmless and is now catastrophic, because
#    `restore_known_good` replaces whole directories. Snapshots are therefore
#    staged and swapped atomically, carry a per-entry integrity record, and are
#    refused at restore time if they do not verify.
#  * the live state being replaced used to be discarded. It is now MOVED to
#    STATE_DIR/failed/<timestamp>/, which is where `note()` has always told the
#    user to look and where, until today, nothing was ever written.

_SNAPSHOT_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", "*.pyd", ".git", "*.log", "*.tmp")


def _package_root() -> Path:
    """The importable package directory — src/agent_friday, or _MEIPASS frozen.

    Deliberately computed here rather than imported from `core._RES_DIR`: this
    module must stay stdlib-only so the recovery path never depends on the app
    it is recovering. `boot_guard.py` lives at <pkg>/services/boot_guard.py.
    """
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent.parent


def _self_editable_paths() -> list:
    """What a self-edit or a liquid-UI change is allowed to touch.

    Ordered widest-first only for readability; the snapshot is keyed by full
    path (see `_slug`), so order carries no meaning.
    """
    pkg = _package_root()
    paths = [pkg]
    ui = pkg.parent.parent / "index.html"     # repo root in a source checkout
    if ui.exists():
        paths.append(ui)
    paths.append(HOME / ".friday" / "workspace_studio")
    paths.append(HOME / ".friday" / "settings.json")
    return paths


def _slug(p: Path) -> str:
    """A stable per-path key for the snapshot store.

    The previous implementation keyed by `p.name`, so two covered paths sharing
    a basename would have silently overwritten each other in the store and then
    restored each other's contents over the top. Not reachable with two paths;
    reachable the moment the set grows, which is this change.
    """
    full = str(p).replace("\\", "/").rstrip("/")
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", p.name)[:40] or "path"
    return "%s-%s" % (stem, digest)


def _fingerprint(p: Path) -> dict:
    """Cheap integrity/change record: file count, total bytes, newest mtime.

    Not a hash of contents — this runs on every successful boot over a 25 MB
    tree and the job is to notice a change and to notice a truncated store, not
    to resist a forger who already has write access to both.
    """
    if not p.exists():
        return {"exists": False, "files": 0, "bytes": 0, "mtime": 0.0}
    if p.is_file():
        st = p.stat()
        return {"exists": True, "files": 1, "bytes": st.st_size,
                "mtime": round(st.st_mtime, 3)}
    files = 0
    total = 0
    newest = 0.0
    for f in p.rglob("*"):
        name = f.name
        if "__pycache__" in f.parts or name.endswith((".pyc", ".pyo", ".pyd")):
            continue
        if not f.is_file():
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        files += 1
        total += st.st_size
        newest = max(newest, st.st_mtime)
    return {"exists": True, "files": files, "bytes": total,
            "mtime": round(newest, 3)}


def _matches(fp_a: dict, fp_b: dict, *, ignore_mtime: bool = False) -> bool:
    if not fp_a or not fp_b:
        return False
    if fp_a.get("files") != fp_b.get("files"):
        return False
    if fp_a.get("bytes") != fp_b.get("bytes"):
        return False
    if ignore_mtime:
        return True
    return abs(float(fp_a.get("mtime", 0)) - float(fp_b.get("mtime", 0))) < 0.01


def _read_manifest() -> dict:
    try:
        return json.loads((STATE_DIR / "known_good.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _copy_into(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, ignore=_SNAPSHOT_IGNORE)
    else:
        shutil.copy2(src, dest)


def snapshot_known_good(paths=None) -> dict:
    """Copy the CURRENT state of the self-editable surfaces into known_good.

    Only called after a proven boot. Snapshots whole files rather than diffs, on
    the same principle that made the calendar repair possible: the receipt held
    the actual prior value, so restoring needed no reconstruction.

    Staged-then-swapped: everything is written to `known_good.staging` and moved
    into place only once every entry has copied, and the manifest's `complete`
    flag is written last. A crash at any point leaves the PREVIOUS known-good
    intact, which matters now that a restore replaces the source tree.

    Skips the copy entirely when nothing covered has changed since the last
    snapshot, because this runs after every successful start.

    MEASURED 2026-09-03 on the reference machine: 309 files, 7.4 MB, 0.31 s for
    a full copy and 0.05 s when unchanged. (`du` reports the package tree at
    25 MB; the difference is `__pycache__`, which `_SNAPSHOT_IGNORE` drops.)
    """
    src_paths = [Path(p) for p in (paths or _self_editable_paths())]
    live = {}
    for p in src_paths:
        if p.exists():
            live[str(p)] = _fingerprint(p)

    prior = _read_manifest()
    if prior.get("complete") and set(prior.get("entries", {})) == set(live):
        if all(_matches(prior["entries"][k].get("fingerprint"), live[k])
               for k in live):
            return {"ok": True, "unchanged": True,
                    "saved": sorted(live), "at": prior.get("at")}

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    staging = STATE_DIR / "known_good.staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    entries = {}
    saved = []
    for p in src_paths:
        if not p.exists():
            continue
        slug = _slug(p)
        try:
            _copy_into(p, staging / slug)
        except Exception as e:
            # One unreadable path must not silently produce a snapshot that
            # LOOKS complete and restores a hole into the live tree.
            _log.warning("boot_guard: could not snapshot %s: %s", p, e)
            shutil.rmtree(staging, ignore_errors=True)
            return {"ok": False, "error": "could not snapshot %s (%s)" % (p, e),
                    "saved": []}
        entries[str(p)] = {
            "slug": slug,
            "kind": "dir" if p.is_dir() else "file",
            "fingerprint": _fingerprint(p),
            "stored": _fingerprint(staging / slug),
        }
        saved.append(str(p))

    old = STATE_DIR / "known_good.previous"
    shutil.rmtree(old, ignore_errors=True)
    if KNOWN_GOOD.exists():
        try:
            KNOWN_GOOD.rename(old)
        except OSError:
            shutil.rmtree(KNOWN_GOOD, ignore_errors=True)
    staging.rename(KNOWN_GOOD)
    shutil.rmtree(old, ignore_errors=True)

    _write(STATE_DIR / "known_good.json",
           {"at": datetime.now().isoformat(timespec="seconds"),
            "paths": saved,          # kept for readers of the old shape
            "entries": entries,
            "complete": True})
    return {"ok": True, "saved": saved}


def _verify_snapshot(manifest: dict) -> tuple:
    """(ok, reason) — is this store safe to copy over a live tree?

    Checked BEFORE anything is moved. The previous implementation assumed the
    store was whole because the directory existed, which was survivable while it
    held workspace JSON and is not survivable now that it holds the source.
    """
    if not manifest:
        return False, "no known-good snapshot exists yet"
    if not manifest.get("complete"):
        return False, ("the last snapshot did not finish, so restoring it would "
                       "replace working files with a fragment")
    entries = manifest.get("entries") or {}
    if not entries:
        return False, "the known-good manifest names no paths"
    for target, meta in entries.items():
        stored = KNOWN_GOOD / meta.get("slug", "")
        if not stored.exists():
            return False, "the snapshot of %s is missing from the store" % target
        # mtime is not preserved by every copy path across volumes; size and
        # count are what a truncation actually changes.
        if not _matches(meta.get("stored"), _fingerprint(stored), ignore_mtime=True):
            return False, ("the snapshot of %s does not match what was recorded "
                           "when it was taken" % target)
    return True, None


def restore_known_good() -> dict:
    """Put the self-editable surfaces back to the last PROVEN-bootable state.

    Honest about two things the previous version overstated.

    First, WHEN it takes effect. `server.py` calls this from `__main__`, long
    after the module imported the package at the top of the file, so replacing
    `.py` files on disk cannot change the code already in memory. A restore of
    the source lands on the NEXT start. The report says so; claiming otherwise
    would be the invisible-success failure one layer down.

    Second, WHAT it kept. The note has always pointed at STATE_DIR/"failed" and
    nothing ever wrote there. The state being replaced is now moved there rather
    than deleted, so the sentence is true and the broken state is inspectable.

    A restore that has not yet been vindicated by a successful boot is not
    repeated: it did not help the first time and re-running it costs a full
    tree copy per failed start.
    """
    if safe_mode():
        return {"ok": False, "skipped": "safe mode — nothing restored so the "
                                        "broken state can be inspected"}

    attempt = _read_attempt()
    if attempt.get("restore_pending"):
        return {"ok": False, "skipped": (
            "a restore to the last proven-bootable state was already applied at "
            "%s and no successful start has happened since. Restoring again "
            "would repeat something that did not help. Start with "
            "FRIDAY_SAFE_MODE=1 to inspect, or look in %s"
            % (attempt.get("restored_at"), STATE_DIR / "failed"))}

    manifest = _read_manifest()
    ok, why = _verify_snapshot(manifest)
    if not ok:
        note("Did not roll back — the saved state could not be trusted.",
             reason=why)
        return {"ok": False, "error": why}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    failed_dir = STATE_DIR / "failed" / stamp
    failed_dir.mkdir(parents=True, exist_ok=True)

    restored, kept, problems = [], [], []
    for target, meta in (manifest.get("entries") or {}).items():
        p = Path(target)
        stored = KNOWN_GOOD / meta["slug"]
        staged = STATE_DIR / "restore_staging" / meta["slug"]
        shutil.rmtree(staged.parent, ignore_errors=True)
        try:
            # Copy out of the store first, so the store itself is never the
            # thing that gets moved and a failure here changes nothing.
            _copy_into(stored, staged)
            if p.exists():
                shutil.move(str(p), str(failed_dir / meta["slug"]))
                kept.append(target)
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(p))
            restored.append(target)
        except Exception as e:
            _log.error("boot_guard: could not restore %s: %s", target, e)
            problems.append({"path": target, "error": str(e)})
            # Put the live state back rather than leaving a hole.
            aside = failed_dir / meta["slug"]
            if aside.exists() and not p.exists():
                try:
                    shutil.move(str(aside), str(p))
                except Exception:
                    problems.append({"path": target,
                                     "error": "left in %s" % aside})
        finally:
            shutil.rmtree(staged.parent, ignore_errors=True)

    attempt["consecutive_failures"] = 0
    attempt["restore_pending"] = True
    attempt["restored_at"] = datetime.now().isoformat(timespec="seconds")
    _write(ATTEMPT_FILE, attempt)

    takes_effect = ("Source changes land on the next start — this process "
                    "already loaded its code before the rollback ran.")
    note("Rolled back to the last state that actually booted.",
         reason="two consecutive failed starts",
         restored=restored, takes_effect=takes_effect,
         undo="the pre-rollback files are in %s" % failed_dir)
    return {"ok": True, "restored": restored, "kept_for_inspection": str(failed_dir),
            "takes_effect": takes_effect,
            "problems": problems or None}

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
        "known_good_covers": [e for e in (_read_manifest().get("entries") or {})],
        "would_auto_revert": failing_to_boot() and not a.get("restore_pending"),
        "restore_pending": bool(a.get("restore_pending")),
        "restored_at": a.get("restored_at"),
        # The trail was written by note() and read by nobody: recent_notes() had
        # no callers, so "a trail he cannot read is a trail that does not exist"
        # described its own module.
        "recent_notes": recent_notes(10),
        "boot_critical_files": list(BOOT_CRITICAL),
        "recent_rollbacks": recent_notes(5),
    }
