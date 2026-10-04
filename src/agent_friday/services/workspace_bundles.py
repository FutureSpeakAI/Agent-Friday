"""Bundle workspaces and "Improve this workspace"
(docs/design/active/vibe-coding-salon.md §4.9, §4.9.1 items 3 and 5; Phase 2b).

A bundle workspace is one the user built in the salon: a codebase from the
"bundle" template (manifest.json, index.html, icon.svg), installed under
``~/.friday/workspaces/<id>/`` and shown in the dock under "Mine". It runs
only in the sandboxed frame (B0): the page never holds Friday's origin.

The invariants this module keeps:

- **Every installed version is kept.** ``versions/<sha256>/`` holds the files
  of each version ever swapped in; ``current`` names one of them. Rollback
  moves ``current`` back and records it; nothing is deleted.
- **The swap is one approval.** ``request_swap`` raises ONE ``workspace_swap``
  card per codebase head after the manifest check, the brand check and the
  smoke run; the card's decision hook installs on approval and nothing else
  does. Declined installs nothing. Rollback needs no card: the version it
  restores was approved once already.
- **The brand check is a colour distance, not a string match.** Any colour
  literal in the bundle within ΔE2000 20 of a reserved status colour fails,
  naming the colour and the signal it would repaint.
- **A native workspace is never improved in place.** Its boundary is a
  declared set of components in the registry; improving it means Friday's own
  source (§7), which this module refuses with a typed blocker rather than
  pretending.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

KIND = "workspace_swap"
GROUP = "mine"
_WS_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_BUNDLE_FILES = ("manifest.json", "index.html", "icon.svg", "README.md", "styles.css")
_REQUIRED = ("manifest.json", "index.html")

#: The reserved status signals (avatar-visual-genome.md §1.3, §6.2) and the
#: distance no bundle colour may come within.
RESERVED = (
    ("amber", "#f59e0b"), ("approve green", "#00ff80"), ("deny pink", "#ff0080"),
    ("error red", "#ff0033"), ("error red", "#ef4444"), ("status green", "#00ff66"),
    ("status yellow", "#ffcc00"),
)
DELTA_E_FLOOR = 20.0

_registered = False


class BrandRefused(ValueError):
    """The bundle repaints a reserved status colour."""


class ManifestRefused(ValueError):
    """The bundle's manifest breaks the contract, or the codebase is not a bundle."""


class NativeWorkspace(ValueError):
    """A registry workspace: improved through Friday's own source (§7), never in place."""
    blocker = "needs_phase_7"


class SmokeFailed(RuntimeError):
    """The improved page throws at load; a typed blocker, not a card."""
    blocker = "run_failed"


# ── colour distance ──────────────────────────────────────────────────────────

_HEX = re.compile(r"#([0-9a-fA-F]{3,8})\b")
_RGB = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})")


def _parse_hex(h: str) -> Optional[tuple]:
    h = h.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h[:3])
    elif len(h) in (6, 8):
        h = h[:6]
    else:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _rgb_to_lab(rgb: tuple) -> tuple:
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.00000
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e2000(lab1: tuple, lab2: tuple) -> float:
    """CIEDE2000 (Sharma, Wu and Dalal 2005), kL = kC = kH = 1."""
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cbar = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cbar ** 7 / (Cbar ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)

    def hp(a, b):
        if a == 0 and b == 0:
            return 0.0
        h = math.degrees(math.atan2(b, a))
        return h + 360 if h < 0 else h
    h1p, h2p = hp(a1p, b1), hp(a2p, b2)
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    RC = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    SL = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    SC = 1 + 0.045 * Cbp
    SH = 1 + 0.015 * Cbp * T
    RT = -math.sin(math.radians(2 * dtheta)) * RC
    return math.sqrt((dLp / SL) ** 2 + (dCp / SC) ** 2 + (dHp / SH) ** 2 + RT * (dCp / SC) * (dHp / SH))


_RESERVED_LAB = [(name, hx, _rgb_to_lab(_parse_hex(hx))) for name, hx in RESERVED]


def _colour_literals(text: str) -> list:
    """(literal, rgb) for every colour literal in the text, in order, deduplicated."""
    seen, out = set(), []
    for m in _HEX.finditer(text or ""):
        lit = m.group(0)
        rgb = _parse_hex(lit)
        if rgb and lit.lower() not in seen:
            seen.add(lit.lower())
            out.append((lit, rgb))
    for m in _RGB.finditer(text or ""):
        rgb = tuple(min(255, int(v)) for v in m.groups())
        lit = m.group(0) + ")"
        if lit.lower() not in seen:
            seen.add(lit.lower())
            out.append((lit, rgb))
    return out


def brand_check(html: str, css: str) -> list:
    """Every colour in the bundle that comes within ΔE2000 20 of a reserved
    status colour, each as one plain line naming what it would repaint."""
    problems = []
    for lit, rgb in _colour_literals((html or "") + "\n" + (css or "")):
        lab = _rgb_to_lab(rgb)
        name, hx, de = min(((n, h, delta_e2000(lab, rl)) for n, h, rl in _RESERVED_LAB), key=lambda t: t[2])
        if de < DELTA_E_FLOOR:
            problems.append("%s repaints the reserved %s (%s): colour distance %.1f, the floor is %.0f"
                            % (lit, name, hx, de, DELTA_E_FLOOR))
    return problems


# ── the manifest ─────────────────────────────────────────────────────────────

def check_manifest(text: str) -> list:
    """Plain-language problems with a bundle manifest; empty when it is sound."""
    try:
        m = json.loads(text or "")
    except Exception:
        return ["manifest.json is not valid JSON"]
    if not isinstance(m, dict):
        return ["manifest.json must be an object"]
    problems = []
    if not isinstance(m.get("id"), str) or not _WS_ID.match(m.get("id") or ""):
        problems.append("id must be a slug: lower-case letters, digits and dashes")
    if not isinstance(m.get("name"), str) or not m.get("name", "").strip():
        problems.append("name is required")
    if m.get("friday_api") != 1:
        problems.append("friday_api must be 1")
    caps = m.get("capabilities") if isinstance(m.get("capabilities"), dict) else {}
    if caps.get("network") != ["none"]:
        problems.append("capabilities.network must be [\"none\"]: a bundle reaches nothing on its own")
    return problems


# ── the store ────────────────────────────────────────────────────────────────

def _root() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR) / "workspaces"


def check_ws_id(ws_id: str) -> str:
    s = str(ws_id or "").strip()
    if not _WS_ID.match(s):
        raise ValueError("not a workspace id")
    return s


def _dir(ws_id: str) -> Path:
    return _root() / check_ws_id(ws_id)


def _save(rec: dict) -> dict:
    d = _dir(rec["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "bundle.json.tmp"
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "bundle.json")
    return rec


def get(ws_id: str) -> Optional[dict]:
    try:
        p = _dir(ws_id) / "bundle.json"
    except ValueError:
        return None
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_installed() -> list:
    root = _root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        rec = get(d.name) if d.is_dir() else None
        if rec:
            out.append(rec)
    return sorted(out, key=lambda r: (r.get("label") or "").lower())


def _version_dir(ws_id: str, sha: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        raise KeyError(sha)
    return _dir(ws_id) / "versions" / sha


def version_files(ws_id: str, sha: Optional[str] = None) -> dict:
    """The files of one installed version (the current one by default)."""
    rec = get(ws_id)
    if rec is None:
        raise KeyError(ws_id)
    vd = _version_dir(ws_id, sha or rec["current"])
    if not vd.is_dir():
        raise KeyError(sha)
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(vd.iterdir()) if p.is_file()}


def html(ws_id: str) -> str:
    return version_files(ws_id).get("index.html", "")


# ── from a codebase ──────────────────────────────────────────────────────────

def _files_from_codebase(cid: str) -> tuple:
    from agent_friday.services import codebases as cb
    rec = cb.load(cid)
    if rec is None:
        raise KeyError(cid)
    files = {}
    for name in _BUNDLE_FILES:
        text = cb.read(cid, name)
        if text is not None:
            files[name] = text
    missing = [n for n in _REQUIRED if n not in files]
    if missing:
        raise ManifestRefused("not a workspace bundle: %s missing (a bundle is manifest.json plus index.html)" % ", ".join(missing))
    return rec, files


def _check(files: dict) -> dict:
    problems = check_manifest(files.get("manifest.json", ""))
    if problems:
        raise ManifestRefused("manifest.json: " + "; ".join(problems))
    bad = brand_check(files.get("index.html", ""), files.get("styles.css", ""))
    if bad:
        raise BrandRefused("the brand check failed: " + "; ".join(bad))
    return json.loads(files["manifest.json"])


def _sha(files: dict) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\0")
    return h.hexdigest()


def _blurb(files: dict, manifest: dict) -> str:
    text = files.get("README.md", "")
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:90]
    return ("%s, a workspace you built in the salon." % manifest.get("name", "A bundle"))[:90]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def install(cid: str, *, ws_id: Optional[str] = None, by: str = "you", approval_id: Optional[str] = None,
            kind: str = "install") -> dict:
    """Make the codebase's working tree the current version of its workspace,
    creating the workspace on the first install. Every earlier version stays."""
    from agent_friday.services import codebases as cb
    crec, files = _files_from_codebase(cid)
    manifest = _check(files)
    target = check_ws_id(ws_id or crec.get("workspace_id") or manifest["id"])
    sha = _sha(files)
    vd = _dir(target) / "versions" / sha
    if not vd.is_dir():
        tmp = vd.with_name(sha + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for name, text in files.items():
            (tmp / name).write_text(text, encoding="utf-8", newline="\n")
        tmp.replace(vd)
    rec = get(target)
    head = cb.head(cid)
    if rec is None:
        label = str(manifest.get("name") or crec.get("title") or target).strip()
        rec = {"id": target, "label": label, "group": GROUP, "core": False, "icon": None, "glyph": "🧩",
               "accent": "cyan", "blurb": _blurb(files, manifest), "aliases": [label.lower()], "tab": True,
               "boundary": {"kind": "bundle"}, "codebase_id": cid, "current": sha, "versions": [], "history": [],
               "created_at": _now()}
    else:
        rec["label"] = str(manifest.get("name") or rec["label"]).strip()
        rec["blurb"] = _blurb(files, manifest)
    if not any(v["sha256"] == sha for v in rec["versions"]):
        rec["versions"].append({"sha256": sha, "at": _now(), "by": by, "codebase_id": cid, "step": head[:7] if head else "",
                                "approval_id": approval_id, "version": str(manifest.get("version") or "")})
    changed = rec["current"] != sha or not rec["history"]
    rec["current"] = sha
    rec["codebase_id"] = rec.get("codebase_id") or cid
    if changed:
        rec["history"].append({"kind": kind, "sha256": sha, "at": _now(), "by": by, "approval_id": approval_id})
    rec["updated_at"] = _now()
    _save(rec)
    try:
        cb.set_workspace(cid, target)
    except Exception as e:
        _log.warning("could not mark codebase %s as workspace %s: %s", cid, target, e)
    _announce(rec, kind)
    return rec


def rollback(ws_id: str, sha256: str, *, by: str = "you") -> dict:
    """One click: an earlier installed version becomes current again. Nothing is deleted."""
    rec = get(ws_id)
    if rec is None:
        raise KeyError(ws_id)
    if not any(v["sha256"] == sha256 for v in rec["versions"]) or not _version_dir(ws_id, sha256).is_dir():
        raise KeyError(sha256)
    rec["current"] = sha256
    rec["history"].append({"kind": "rollback", "sha256": sha256, "at": _now(), "by": by, "approval_id": None})
    rec["updated_at"] = _now()
    _save(rec)
    _announce(rec, "rollback")
    return rec


def _announce(rec: dict, kind: str) -> None:
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.broadcast({"type": "workspace_bundle_changed", "workspace_id": rec["id"], "kind": kind,
                               "current": rec["current"], "label": rec.get("label")}, kind="chat")
    except Exception:
        pass


# ── improve ──────────────────────────────────────────────────────────────────

def is_native(ws_id: str) -> bool:
    from agent_friday.services import workspace_registry as reg
    return ws_id in reg.ids()


def improve(ws_id: str) -> dict:
    """Open (creating once) the codebase chat that improves a bundle workspace.
    Native workspaces are refused with the reason."""
    ws_id = check_ws_id(ws_id)
    if is_native(ws_id):
        from agent_friday.services import workspace_registry as reg
        raise NativeWorkspace("%s is part of Friday herself. Improving it means editing Friday's own source, "
                              "which is not built yet. The workspaces you built in the salon, under Mine in the "
                              "dock, can be improved now." % (reg.label(ws_id) or ws_id))
    rec = get(ws_id)
    if rec is None:
        raise KeyError(ws_id)
    from agent_friday.services import codebases as cb, conversations as convs
    imp = rec.get("improve") or {}
    if imp.get("conversation_id") and imp.get("codebase_id") and convs.load(imp["conversation_id"]) \
            and cb.load(imp["codebase_id"]):
        return {"workspace_id": ws_id, "conversation_id": imp["conversation_id"], "codebase_id": imp["codebase_id"],
                "created": False, "label": rec["label"]}
    conv = convs.create("Improve %s" % rec["label"])
    crec = cb.create(rec["label"], template="bundle", conversation_id=conv["id"], files=version_files(ws_id))
    cb.set_workspace(crec["id"], ws_id)
    rec["improve"] = {"conversation_id": conv["id"], "codebase_id": crec["id"], "at": _now()}
    _save(rec)
    return {"workspace_id": ws_id, "conversation_id": conv["id"], "codebase_id": crec["id"], "created": True,
            "label": rec["label"]}


# ── the swap card ────────────────────────────────────────────────────────────

def _spoken(label: str, is_new: bool, changed: int, head7: str, smoke: dict) -> str:
    ran = smoke.get("ran")
    smoke_line = ("It loaded cleanly in a test browser." if ran and smoke.get("ok")
                  else "I could not run the browser check, so this is unchecked." if not ran else "")
    if is_new:
        return ('Install "%s" as a new workspace in your dock? It runs only in its own sandboxed frame and '
                'reaches nothing on its own. %s Say "yes" to install it, "no" to leave it, or "change it".'
                % (label, smoke_line)).replace("  ", " ")
    return ('Swap "%s" to the improved version from step %s? %d file%s changed since the version you use now; '
            'the old version stays one click away. %s Say "yes" to swap it in, "no" to leave it, or "change it".'
            % (label, head7 or "?", changed, "" if changed == 1 else "s", smoke_line)).replace("  ", " ")


def request_swap(cid: str, *, requested_by: str = "friday") -> dict:
    """ONE card to swap a codebase's working tree in as its workspace's current
    version (or to install a fresh bundle as a new workspace). Refuses before
    the card when the manifest or brand check fails; a page that throws at
    load is a run_failed blocker, not a card."""
    from agent_friday.services import approvals as _ap, codebases as cb
    crec, files = _files_from_codebase(cid)
    manifest = _check(files)
    smoke = cb.smoke(cid)
    if smoke.get("ran") and smoke.get("ok") is False:
        raise SmokeFailed("the improved page throws at load: %s" % "; ".join(smoke.get("errors") or ["unknown error"]))
    ws_id = crec.get("workspace_id")
    rec = get(ws_id) if ws_id else None
    is_new = rec is None
    target = ws_id or manifest["id"]
    sha = _sha(files)
    if rec is not None and rec.get("current") == sha:
        raise ValueError("that version is already the one in use")
    head = cb.head(cid) or ""
    changed = 0
    if rec is not None:
        cur = version_files(ws_id)
        changed = sum(1 for n in set(files) | set(cur) if files.get(n) != cur.get(n))
    label = str(manifest.get("name") or crec.get("title") or target)
    payload = {
        "workspace_id": ws_id if not is_new else None, "target_id": target, "codebase_id": cid, "sha": sha,
        "head": head[:7], "label": label, "is_new": is_new, "changed": changed, "brand": "ok",
        "manifest": {k: manifest.get(k) for k in ("id", "name", "version")},
        "smoke": smoke, "spoken": _spoken(label, is_new, changed, head[:7], smoke),
        "conversation_id": crec.get("conversation_id"), "requested_by": requested_by,
        "steps": [s.get("summary") for s in cb.steps(cid, limit=6)],
    }
    title = ('Install "%s" as a new workspace' % label) if is_new else ('Swap "%s" to the improved version' % label)
    card = _ap.create_approval(
        kind=KIND, subject_type="codebase", subject_id="%s@%s" % (cid, sha), title=title,
        description=("A new bundle workspace in the dock. " if is_new else "%d file%s changed. " % (changed, "" if changed == 1 else "s"))
        + ("Loaded cleanly in a test browser." if smoke.get("ran") and smoke.get("ok") else "Browser check not run."),
        action_description=title.lower(), payload=payload, requested_by=requested_by, force_gate=True)
    return card


def register() -> None:
    """Attach the installer to approval decisions. Idempotent."""
    global _registered
    if _registered:
        return
    from agent_friday.services import approvals as _ap
    _ap.register_decision_hook(KIND, _on_decision)
    _registered = True


def _post_back(record: dict, text: str) -> None:
    cid = ((record.get("payload") or {}).get("conversation_id") or "").strip()
    if not cid:
        return
    try:
        from agent_friday.services import conversations as _convs
        _convs.append(cid, {"role": "friday", "text": text, "ts": time.time(),
                            "meta": {"kind": "approval_result", "approval_id": record.get("approval_id")}})
        from agent_friday.services import desktop_bus as _bus
        _bus.broadcast({"type": "approval_result", "conversation_id": cid,
                        "approval_id": record.get("approval_id")}, kind="chat")
    except Exception as e:
        _log.warning("could not post the swap result into %s: %s", cid, e)


def _on_decision(record: dict) -> None:
    if not isinstance(record, dict) or record.get("kind") != KIND:
        return
    payload = record.get("payload") or {}
    label = str(payload.get("label") or "the workspace")
    if record.get("status") != "approved":
        if record.get("status") in ("denied", "expired"):
            _post_back(record, '"%s" stays as it is; nothing was swapped in.' % label)
        return
    aid = record.get("approval_id")
    from agent_friday.services import approvals as _ap
    if not _ap.claim_for_execution(aid):
        return
    try:
        rec = install(str(payload.get("codebase_id")), ws_id=payload.get("workspace_id") or payload.get("target_id"),
                      by=str(record.get("decided_by") or "you"), approval_id=aid,
                      kind="install" if payload.get("is_new") else "swap")
    except Exception as e:
        _ap.mark_used(aid, KIND, detail={"ok": False, "error": str(e)})
        _post_back(record, 'I could not swap "%s" in: %s. It stays as it was.' % (label, e))
        return
    _ap.mark_used(aid, KIND, detail={"ok": True, "workspace_id": rec["id"], "sha": rec["current"]})
    if payload.get("is_new"):
        _post_back(record, '"%s" is installed as a workspace; it is in your dock under Mine.' % rec["label"])
    else:
        _post_back(record, 'Swapped "%s" to the improved version. The earlier version is one click away in its history.' % rec["label"])
