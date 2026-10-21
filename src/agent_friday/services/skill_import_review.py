"""A skill from outside is installed only after the owner has seen it and pressed Approve.

A skill is a procedure Friday follows: its body reaches her prompt whenever one
of its triggers matches, and it names the tools it uses. So an imported skill
changes what Friday does, and installing one is the owner's decision, made on a
card that says what the skill is and what it would make Friday do:

  1. ``stage(src)`` reads a .zip, a SKILL.md folder or a legacy .yaml WITHOUT
     extracting anything to disk. Bounded: package size, member count, total
     unpacked size and manifest size. It keeps exactly the fields an install
     writes (name, description, procedure, triggers, tools, success criteria,
     version, licence) in a staging record outside the skills folder, where no
     loader reads it, and raises one ``skill_import`` card.
  2. The card lists the procedure, the triggers, the tools, whether it
     replaces a skill already installed, the outward permissions the package
     asks for (never carried over: a skill's sending needs its own Settings
     approval) and the files in the package that are left out (scripts and
     anything else that is not the manifest are never installed).
  3. Approve is a press on that card on this PC's screen (a screen-only kind,
     services/approvals.SCREEN_ONLY_KINDS). The decision hook installs exactly
     the staged fields (bound by their hash) through
     ``skill_registry.save_skill``, which keeps the replaced version in the
     skill's ``_history`` and appends a ledger line, after the action
     checkpoint's integrity check and signed receipt. Decline removes the
     staging record. Either way the result is recorded on the card.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath

_log = logging.getLogger("friday.skill_import_review")

KIND = "skill_import"
SUBJECT_TYPE = "skill_import"

#: Bounds for reading a package. A skill is a text manifest; these are generous for that.
MAX_PACKAGE_BYTES = 10 * 1024 * 1024
MAX_MEMBERS = 500
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
#: How much of the procedure the card quotes; the whole text is in the staging record.
BODY_PREVIEW = 900

_MANIFEST_NAMES = ("SKILL.md", "skill.md", "Skill.md")
_HOOKED = {"done": False}
_LOCK = threading.Lock()


class ImportRefused(ValueError):
    """The package cannot be staged; the message says why in plain words."""


def _staging_dir() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "skill_imports"


def _registry():
    import agent_friday.skill_registry as reg
    return reg


# ── reading a package ──────────────────────────────────────────────────────

def _zip_manifest(src: Path):
    """(manifest text, folder name, left-out member names) from a zip, read in memory."""
    if src.stat().st_size > MAX_PACKAGE_BYTES:
        raise ImportRefused("the package is larger than %d MB" % (MAX_PACKAGE_BYTES // (1024 * 1024)))
    try:
        zf = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise ImportRefused("the file is not a readable .zip")
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ImportRefused("the package has more than %d files" % MAX_MEMBERS)
        if sum(max(0, i.file_size) for i in infos) > MAX_UNPACKED_BYTES:
            raise ImportRefused("the package unpacks to more than %d MB" % (MAX_UNPACKED_BYTES // (1024 * 1024)))
        manifests = []
        for info in infos:
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            if PurePosixPath(name).name in _MANIFEST_NAMES:
                manifests.append(info)
        if not manifests:
            raise ImportRefused("the package contains no SKILL.md")
        # The shallowest manifest is the skill (the same choice as a folder import).
        manifests.sort(key=lambda i: (i.filename.replace("\\", "/").count("/"), i.filename))
        man = manifests[0]
        if man.file_size > MAX_MANIFEST_BYTES:
            raise ImportRefused("the SKILL.md is larger than %d KB" % (MAX_MANIFEST_BYTES // 1024))
        with zf.open(man) as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ImportRefused("the SKILL.md is larger than %d KB" % (MAX_MANIFEST_BYTES // 1024))
        folder = PurePosixPath(man.filename.replace("\\", "/")).parent.name or src.stem
        left_out = sorted(i.filename for i in infos if not i.is_dir() and i is not man)
    return raw.decode("utf-8", errors="replace"), folder, left_out


def _folder_manifest(src: Path):
    man = next((src / n for n in _MANIFEST_NAMES if (src / n).is_file()), None)
    if man is None:
        raise ImportRefused("the folder has no SKILL.md")
    if man.stat().st_size > MAX_MANIFEST_BYTES:
        raise ImportRefused("the SKILL.md is larger than %d KB" % (MAX_MANIFEST_BYTES // 1024))
    left_out = []
    for i, p in enumerate(src.rglob("*")):
        if i >= MAX_MEMBERS:
            left_out.append("…")
            break
        if p.is_file() and p != man:
            left_out.append(p.relative_to(src).as_posix())
    return man.read_text(encoding="utf-8", errors="replace"), src.name, sorted(left_out)


def _read(src: Path):
    """The Skill a package describes, and the files an install would leave out."""
    reg = _registry()
    if src.is_file() and src.suffix.lower() == ".zip":
        text, folder, left_out = _zip_manifest(src)
        skill = reg._skill_from_manifest(Path(folder), text, default_source="imported")
    elif src.is_file() and src.suffix.lower() in (".yaml", ".yml"):
        if src.stat().st_size > MAX_MANIFEST_BYTES:
            raise ImportRefused("the skill file is larger than %d KB" % (MAX_MANIFEST_BYTES // 1024))
        skill = reg._skill_from_legacy_yaml(src)
        if skill is None:
            raise ImportRefused("the legacy skill file could not be read")
        left_out = []
    elif src.is_dir():
        text, folder, left_out = _folder_manifest(src)
        skill = reg._skill_from_manifest(Path(folder), text, default_source="imported")
    else:
        raise ImportRefused("choose a .zip, a skill folder or a .yaml skill file")
    return skill, left_out


# ── staging and the card ───────────────────────────────────────────────────

_FIELDS = ("name", "description", "body", "triggers", "tool_chain", "success_criteria",
           "version", "license")


def _fields_sha(fields: dict) -> str:
    return hashlib.sha256(json.dumps({k: fields.get(k) for k in _FIELDS}, sort_keys=True,
                                     ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _bullet(items, empty="none") -> str:
    items = [str(x) for x in items or [] if str(x).strip()]
    return ", ".join(items) if items else empty


def describe(staged: dict) -> str:
    """The card's text: what installing this skill would make Friday do."""
    f = staged["fields"]
    body = " ".join(str(f.get("body") or "").split())
    if len(body) > BODY_PREVIEW:
        body = body[:BODY_PREVIEW - 1] + "…"
    lines = [
        "Installing adds a skill Friday follows whenever one of its triggers matches what you ask.",
        "",
        "Skill: %s" % f["name"] + (" — %s" % f["description"] if f.get("description") else ""),
        "Triggers: %s" % _bullet(f.get("triggers"), "none (it is only used when chosen by name)"),
        "Tools it uses: %s" % _bullet(f.get("tool_chain")),
        "What it tells Friday to do: %s" % (body or "(empty)"),
    ]
    if staged.get("replaces"):
        lines.append("It REPLACES the installed skill “%s”; the current version is kept in that "
                     "skill's history." % staged["replaces"])
    asked = staged.get("permissions_asked") or {}
    if asked:
        lines.append("The package asks to send without asking you (%s). That is NOT carried over: a skill's "
                     "sending needs its own approval in Settings." % "; ".join(
                         "%s to %s" % (a, _bullet(r)) for a, r in asked.items()))
    left = staged.get("left_out") or []
    if left:
        shown = left[:12]
        lines.append("Left out (never installed): %s%s" % (", ".join(shown),
                                                          " and %d more" % (len(left) - len(shown)) if len(left) > len(shown) else ""))
    lines.append("Source: %s" % staged.get("source_name", "a package"))
    return "\n".join(lines)


def stage(src, name=None, *, source_name: str = "", requested_by: str = "owner") -> dict:
    """Read a package and raise the card that installs it. Installs nothing.

    Returns {"status": "pending_review", "approval_id", "stage_id", "preview"}.
    Raises ImportRefused (with a plain reason) when the package cannot be read."""
    src = Path(src)
    if not src.exists():
        raise ImportRefused("the import source was not found")
    skill, left_out = _read(src)
    reg = _registry()
    if name:
        skill.name = str(name)
    safe = reg._safe_name(skill.name)
    fields = {
        "name": skill.name, "description": skill.description, "body": skill.body,
        "triggers": list(skill.triggers), "tool_chain": list(skill.tool_chain),
        "success_criteria": list(skill.success_criteria), "version": int(skill.version or 1),
        "license": str(skill.license or "MIT"),
    }
    existing = None
    try:
        installed = {reg._safe_name(s.name): s for s in reg.load_skills([reg.SKILLS_DIR])}
        if safe in installed:
            existing = installed[safe].name
    except Exception as e:  # noqa: BLE001 - the card says "replaces" only when it knows
        _log.warning("could not list installed skills for an import preview: %s", type(e).__name__)
    stage_id = "sk" + uuid.uuid4().hex[:16]
    staged = {
        "stage_id": stage_id, "fields": fields, "sha256": _fields_sha(fields),
        "replaces": existing, "left_out": left_out,
        "permissions_asked": dict(skill.permissions or {}),
        "source_name": (source_name or src.name)[:200],
    }
    d = _staging_dir()
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".stage-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(staged, fh, ensure_ascii=False)
        os.replace(tmp, d / (stage_id + ".json"))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    ensure_hook()
    from agent_friday.services import approvals as ap
    card = ap.create_approval(
        kind=KIND, subject_type=SUBJECT_TYPE, subject_id=stage_id,
        title=("Replace the skill “%s”" if existing else "Install the skill “%s”") % skill.name[:120],
        description=describe(staged),
        action_description="install a skill from %s" % staged["source_name"],
        payload={"handler": KIND, "stage_id": stage_id, "sha256": staged["sha256"]},
        requested_by=requested_by, action_class="outward", force_gate=True)
    return {"status": "pending_review", "approval_id": card.get("approval_id"), "stage_id": stage_id,
            "preview": {"name": skill.name, "triggers": fields["triggers"], "tool_chain": fields["tool_chain"],
                        "replaces": existing, "left_out": left_out,
                        "permissions_asked": staged["permissions_asked"]}}


def _load_staged(stage_id: str):
    if not isinstance(stage_id, str) or not stage_id.startswith("sk") or not stage_id[2:].isalnum():
        return None
    p = _staging_dir() / (stage_id + ".json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _drop_staged(stage_id: str) -> None:
    try:
        (_staging_dir() / (stage_id + ".json")).unlink()
    except OSError:
        pass


def install_approved(record: dict) -> dict:
    """Install exactly the staged skill for an approved, screen-pressed card. Once."""
    from agent_friday.services import approvals as ap
    if record.get("kind") != KIND or record.get("status") != "approved":
        return {"ok": False, "why": "not an approved skill import"}
    if record.get("decided_by") not in ap.SCREEN_ONLY_KINDS.get(KIND, frozenset()):
        return {"ok": False, "why": "a skill installs only from a press on its card"}
    payload = record.get("payload") or {}
    with _LOCK:
        if not ap.claim_for_execution(record["approval_id"]):
            return {"ok": False, "why": "this card was already used"}
        staged = _load_staged(payload.get("stage_id"))
        if staged is None or staged.get("sha256") != payload.get("sha256") \
                or _fields_sha(staged.get("fields") or {}) != payload.get("sha256"):
            out = {"ok": False, "why": "the staged skill is gone or no longer matches its card; nothing was installed"}
            ap.mark_used(record["approval_id"], KIND, out)
            _tell("The skill was not installed", out["why"], record["approval_id"])
            return out
        from agent_friday.governance import action_gate as _gate
        try:
            _gate.record_external("skill:import", surface="approval_card",
                                  approval_id=record["approval_id"], target=staged["fields"]["name"][:200])
        except _gate.Held as e:
            out = {"ok": False, "why": "the action checkpoint held the install (%s); nothing was installed" % e}
            ap.mark_used(record["approval_id"], KIND, out)
            _tell("The skill was not installed", out["why"], record["approval_id"])
            return out
        f = staged["fields"]
        try:
            folder = _registry().save_skill(
                f["name"], f.get("description") or "", f.get("body") or "", f.get("triggers"),
                f.get("tool_chain"), f.get("success_criteria"), source="imported",
                version=f.get("version") or 1, license=f.get("license") or "MIT",
                reason="imported after the owner approved card %s" % record["approval_id"],
                evidence={"source": staged.get("source_name"), "approval": record["approval_id"]},
                actor="import")
        except Exception as e:  # noqa: BLE001 - the owner is told; the card records it
            out = {"ok": False, "why": "the skill could not be written (%s); nothing was installed" % type(e).__name__}
            ap.mark_used(record["approval_id"], KIND, out)
            _tell("The skill was not installed", out["why"], record["approval_id"])
            return out
        _drop_staged(staged["stage_id"])
        out = {"ok": True, "installed": f["name"], "path": str(folder)}
        ap.mark_used(record["approval_id"], KIND, out)
    _tell("Skill installed", "“%s” is installed. It is listed under Skills and can be archived there." % f["name"],
          record["approval_id"])
    return out


def _tell(title: str, body: str, approval_id: str = "") -> None:
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=title, body=body, priority="low", kind="skill_import", source="skills",
                emitter="SK1", dedupe_key="skill-import:%s" % approval_id)
    except Exception as e:  # noqa: BLE001 - the card already holds the outcome
        _log.info("skill import notice not shown: %s", type(e).__name__)


def _on_decision(record: dict) -> None:
    if record.get("status") == "approved":
        install_approved(record)
    elif record.get("status") in ("denied", "expired", "blocked"):
        _drop_staged(str((record.get("payload") or {}).get("stage_id") or ""))


def ensure_hook() -> None:
    if _HOOKED["done"]:
        return
    from agent_friday.services import approvals as ap
    ap.register_decision_hook(KIND, _on_decision)
    _HOOKED["done"] = True
