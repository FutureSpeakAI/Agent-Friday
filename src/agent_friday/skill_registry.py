"""
Portable Skill Registry — SKILL.md folder format.

A skill is a folder containing a ``SKILL.md`` file: YAML frontmatter (the
manifest) + a Markdown body (the procedure / prompt template). This matches the
agentskills.io / Anthropic Agent Skills convention, so skills are shareable as
folders or zips and importable from other agents (OpenClaw, Hermes).

It unifies Friday's two legacy skill stores:
  - ``~/.friday/skills/*.yaml``   — learn_skill "learnable" skills (single file)
  - ``skills/<name>/``            — bundled Python skills (some carry a SKILL.md)

Frontmatter (all optional except name/description):

    ---
    name: meeting-prep
    description: Prepare a briefing for an upcoming meeting
    version: 1
    triggers: ["prepare for my meeting with", "meeting prep"]
    tool_chain: [search_wiki, query_trust_graph, search_web]
    success_criteria: ["briefing covers attendees and agenda"]
    license: MIT
    source: friday            # friday | imported | openclaw | bundled
    permissions:              # what the skill NEEDS to do outside; grants nothing
      send_email: ["team@example.com", "@example.org"]
    ---
    <markdown body = the procedure Friday should follow>

``permissions`` states the outward actions (and their recipients) the skill
needs. It never grants anything by itself: the owner approves it, once, in
Settings (governance/send_grants.create_for_skill), and only that grant lets a
send go without a card.

Skills Friday writes herself (the ``learn_skill`` tool):

  - Authorship lives OUTSIDE the manifest. A skill is Friday's only when its
    folder holds a ``.provenance.json`` signed with the governance key (HMAC)
    that names this folder and the SHA-256 of the exact SKILL.md Friday wrote.
    Only ``agent_save_skill`` writes it; nothing the model sends is read to
    decide authorship. Frontmatter (``created_by``, ``source`` ...) is model
    text and is never trusted for it: a forged marker, a copied record, a
    hand edit, an import over the folder, or a record signed for another name
    all make the skill the owner's (read-only to Friday). No key: fail closed.
  - Friday may modify or archive only skills she wrote. Owner, imported,
    bundled, workflow and legacy YAML skills are read-only to her, and a
    skill of hers never shadows one (load_skills: a non-Friday skill wins a
    name collision; Friday's skills are named by their folder).
  - Archive, never delete. Archiving moves the whole folder (manifest,
    ``_history``, ledger, provenance) to ``.archive/<name>/<stamp>/``;
    ``restore_skill`` moves the newest copy back. Friday may restore only a
    copy she wrote AND archived herself (the copy's newest ledger line).
  - Ledger. Every create, update, archive and restore appends one JSON line to
    the skill's ``.ledger.jsonl``: action, actor (set by the calling code path,
    never by the model), reason, an evidence pointer, the manifest hash, the
    hash of the previous line (a chain) and an HMAC. ``verify_ledger`` checks
    the chain. The file is only ever opened for append.

Public API:
    load_skills(dirs=None)               -> list[Skill]
    list_skills(dirs=None)               -> list[dict]      (summaries for UI/API)
    get_skill(name, dirs=None)           -> Skill | None
    match_skills(message, ...)           -> list[Skill]
    build_injection(message, ...)        -> str             (system-prompt block)
    save_skill(...)                      -> Path            (write a SKILL.md folder)
    import_skill(src, name=None)         -> dict            (folder/zip/legacy yaml)
    export_skill(name, dest=None)        -> Path            (package a .zip)
    register_with_skillopt(skill)        -> bool            (feed the optimizer)
    agent_save_skill(name, fields, ...)  -> (Path, action)  (Friday's write, own skills only)
    skill_owner(name, root=None)         -> "agent" | "owner" | None
    archive_skill(name, ...)             -> Path            (move to .archive)
    restore_skill(name, ...)             -> Path            (move back)
    read_ledger(name) / verify_ledger(path)
"""

from __future__ import annotations

import re
import json
import os
import hashlib
import threading
import shutil
import zipfile
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from agent_friday.paths import friday_home

try:
    import yaml
    _HAS_YAML = True
except Exception:                                   # pragma: no cover
    _HAS_YAML = False

SKILLS_DIR = friday_home() / "skills"   # user/learned/imported skills
# Bundled skills ship as real package data at agent_friday/seed/skills (see
# agent_friday/seed/__init__.py) rather than a repo-root `skills/` directory -
# the old `parents[2]` computation here pointed at the repo root and returned
# nothing at all in a `pip install` (packages.find only ships src/). This is
# still scanned in ADDITION to SKILLS_DIR: `ensure_seed_skills_installed()`
# copies the same content into SKILLS_DIR on first run, but a fresh install
# whose first-run copy hasn't fired yet (or was skipped) still sees the
# bundled skills here.
BUNDLED_DIR = Path(__file__).resolve().parent / "seed" / "skills"

# Files we recognize as a skill manifest, in priority order. OpenClaw/agentskills
# folders may use a lowercase or README variant.
_MANIFEST_NAMES = ("SKILL.md", "skill.md", "Skill.md")


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", (name or "").strip()) or "skill"


@dataclass
class Skill:
    name: str
    description: str = ""
    body: str = ""
    triggers: list = field(default_factory=list)
    tool_chain: list = field(default_factory=list)
    success_criteria: list = field(default_factory=list)
    version: int = 1
    license: str = "MIT"
    source: str = "friday"
    path: str = ""
    meta: dict = field(default_factory=dict)
    #: What the skill declares it NEEDS: {action: [recipients]}. Grants nothing.
    permissions: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "tool_chain": self.tool_chain,
            "version": self.version,
            "license": self.license,
            "source": self.source,
            "path": self.path,
            "has_body": bool(self.body.strip()),
            "permissions": self.permissions,
        }


# ── parsing ─────────────────────────────────────────────────────

#: A frontmatter delimiter is a WHOLE line of three dashes. A "---" inside a
#: value (YAML indents a multi-line string's continuation lines) is text, not
#: the end of the manifest: splitting on any "---" let a description end the
#: frontmatter early and smuggle in a forged name/source (2026-10-14 critic).
_FM_DELIM = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)


def _parse_frontmatter(text: str):
    """Split a SKILL.md into (frontmatter_dict, body_markdown)."""
    fm, body = {}, text
    stripped = text.lstrip("﻿ \t\r\n")
    opening = _FM_DELIM.match(stripped)
    if opening:
        closing = _FM_DELIM.search(stripped, opening.end())
        if closing:
            block = stripped[opening.end():closing.start()]
            body = stripped[closing.end():].lstrip("\r\n")
            if _HAS_YAML:
                try:
                    fm = yaml.safe_load(block) or {}
                except Exception:
                    fm = _parse_kv_block(block)
            else:
                fm = _parse_kv_block(block)
    if not isinstance(fm, dict):
        fm = {}
    return fm, body


def _parse_kv_block(block: str) -> dict:
    """Minimal key: value fallback when PyYAML is unavailable."""
    out = {}
    for line in block.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # comma-separated or single value
        return [p.strip() for p in s.split(",") if p.strip()]
    return [str(v)]


def _as_permissions(v) -> dict:
    """{action: [recipients]} from a manifest's `permissions`: a mapping, or a
    list of {"action": ..., "recipients": [...]}. Anything else is ignored."""
    out = {}
    items = []
    if isinstance(v, dict):
        items = [(k, val) for k, val in v.items()]
    elif isinstance(v, list):
        items = [((x or {}).get("action"), (x or {}).get("recipients"))
                 for x in v if isinstance(x, dict)]
    for action, rcpts in items:
        action = str(action or "").strip()
        if not action:
            continue
        lst = [str(r).strip() for r in (_as_list(rcpts) if not isinstance(rcpts, list) else rcpts)
               if str(r).strip()]
        out.setdefault(action, [])
        out[action] += [r for r in lst if r not in out[action]]
    return out


def fingerprint_parts(name, body, permissions) -> str:
    """A fingerprint of a skill's procedure and declared permissions, from
    the parts read_skill returns (name, procedure, permissions_needed). A
    skill send grant is bound to it (governance/send_grants): an edit to the
    procedure or the permissions makes it a different skill."""
    canonical = json.dumps({"name": str(name or ""), "body": str(body or ""),
                            "permissions": permissions or {}},
                           sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fingerprint(skill) -> str:
    """`fingerprint_parts` of an installed skill, as it is now."""
    return fingerprint_parts(skill.name, skill.body, skill.permissions)


def send_permissions(skill) -> dict:
    """The send actions a skill declares, with recipients that are valid
    addresses or @domains ({action: [recipients]}); declared needs only."""
    from agent_friday.governance.send_grants import SEND_ACTIONS, normalize_recipients
    out = {}
    for action, rcpts in (getattr(skill, "permissions", None) or {}).items():
        if action not in SEND_ACTIONS:
            continue
        good = []
        for r in rcpts:
            try:
                good += normalize_recipients([r])
            except ValueError:
                continue
        if good:
            out[action] = sorted(set(good))
    return out


def refused_send_permissions(skill) -> list:
    """What a skill declares that can never be pre-approved, each with the
    plain reason ([{"action", "recipient", "why"}]), so Settings can say so
    rather than drop it silently."""
    from agent_friday.governance.send_grants import SEND_ACTIONS, recipient_refusal
    out = []
    for action, rcpts in (getattr(skill, "permissions", None) or {}).items():
        if action not in SEND_ACTIONS:
            continue
        for r in rcpts:
            why = recipient_refusal(r)
            if why:
                out.append({"action": action, "recipient": str(r), "why": why})
    return out


def _skill_from_manifest(folder: Path, text: str, default_source="friday") -> Skill:
    fm, body = _parse_frontmatter(text)
    name = str(fm.get("name") or folder.name)
    return Skill(
        name=name,
        description=str(fm.get("description") or "").strip(),
        body=body.strip(),
        triggers=_as_list(fm.get("triggers") or fm.get("trigger_patterns")),
        tool_chain=_as_list(fm.get("tool_chain")),
        success_criteria=_as_list(fm.get("success_criteria")),
        version=int(fm.get("version") or 1),
        license=str(fm.get("license") or "MIT"),
        source=str(fm.get("source") or default_source),
        path=str(folder),
        meta={k: v for k, v in fm.items() if k not in (
            "name", "description", "triggers", "trigger_patterns", "tool_chain",
            "success_criteria", "version", "license", "source", "permissions")},
        permissions=_as_permissions(fm.get("permissions")),
    )


def _skill_from_legacy_yaml(yaml_path: Path) -> Optional[Skill]:
    """Convert a legacy single-file learn_skill YAML into a Skill."""
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) if _HAS_YAML else {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return Skill(
        name=str(data.get("name") or yaml_path.stem),
        description=str(data.get("description") or "").strip(),
        body=str(data.get("prompt_template") or "").strip(),
        triggers=_as_list(data.get("trigger_patterns") or data.get("triggers")),
        tool_chain=_as_list(data.get("tool_chain")),
        success_criteria=_as_list(data.get("success_criteria")),
        version=int(data.get("version") or 1),
        license=str(data.get("license") or "MIT"),
        source=str(data.get("source") or "friday"),
        path=str(yaml_path),
        meta={"legacy_yaml": True},
    )


def _find_manifest(folder: Path) -> Optional[Path]:
    for n in _MANIFEST_NAMES:
        p = folder / n
        if p.exists():
            return p
    return None


# ── loading ─────────────────────────────────────────────────────

def _default_dirs():
    return [SKILLS_DIR, BUNDLED_DIR]


def load_skills(dirs=None) -> list:
    """Discover all skills. Folder SKILL.md wins over a same-named legacy YAML.

    A skill Friday wrote (valid signed provenance, see the module docstring)
    is named by its folder, whatever its frontmatter says, and never wins a
    name collision: every other skill (the owner's, imported, bundled, legacy
    YAML) answers to its name first, so a skill of hers cannot shadow one."""
    dirs = dirs or _default_dirs()
    by_name = {}
    legacy = {}
    agents = {}
    for d in dirs:
        try:
            d = Path(d)
            if not d.is_dir():
                continue
            # folders with a manifest
            for child in sorted(d.iterdir()):
                if child.is_dir():
                    man = _find_manifest(child)
                    if man:
                        try:
                            raw = man.read_bytes()
                            sk = _skill_from_manifest(
                                child, raw.decode("utf-8", errors="replace"),
                                default_source=("bundled" if d == BUNDLED_DIR else "friday"))
                            # Authorship is never read from the manifest text.
                            sk.meta.pop(CREATED_BY_KEY, None)
                            sk.meta.pop(AUTHOR_KEY, None)
                            if d != BUNDLED_DIR and _provenance_ok(child, child.name, raw):
                                sk.name = child.name
                                sk.meta[AUTHOR_KEY] = AGENT_AUTHOR
                                agents[sk.name] = sk
                            else:
                                by_name[sk.name] = sk
                        except Exception:
                            pass
            # legacy single-file YAML skills (learn_skill)
            for y in sorted(d.glob("*.yaml")):
                sk = _skill_from_legacy_yaml(y)
                if sk:
                    legacy.setdefault(sk.name, sk)
        except Exception:
            continue
    # folder skills take precedence; fold in legacy ones not already present
    for name, sk in legacy.items():
        by_name.setdefault(name, sk)
    # Friday's own skills come last: they never take a name already held.
    for name, sk in agents.items():
        by_name.setdefault(name, sk)
    return list(by_name.values())


def friday_wrote(skill) -> bool:
    """True when ``skill`` (from load_skills) is one Friday wrote, by its
    signed provenance; never by anything in the manifest."""
    return str((getattr(skill, "meta", None) or {}).get(AUTHOR_KEY) or "") == AGENT_AUTHOR


def list_skills(dirs=None) -> list:
    return [s.summary() for s in load_skills(dirs)]


def get_skill(name, dirs=None) -> Optional[Skill]:
    for s in load_skills(dirs):
        if s.name == name:
            return s
    return None


# ── matching / injection ────────────────────────────────────────

def match_skills(message, dirs=None, limit=3) -> list:
    """Return skills whose triggers appear in the message, best first."""
    msg = (message or "").lower()
    scored = []
    for s in load_skills(dirs):
        if not s.triggers:
            continue
        hits = [t for t in s.triggers if t and t.lower() in msg]
        if hits:
            # score: number of triggers hit, tie-broken by longest match
            scored.append((len(hits), max(len(h) for h in hits), s))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [s for _, _, s in scored[:limit]]


def build_injection(message, dirs=None, limit=3, max_body=1200) -> str:
    """Index matched skills; retrieve the complete procedure with read_skill.

    ``max_body`` is retained for caller compatibility. A partial procedure can
    omit its verification or scope restrictions, so no body prefix is injected.
    """
    matched = match_skills(message, dirs=dirs, limit=limit)
    if not matched:
        return ""
    parts = []
    for s in matched:
        block = [f"### {s.name}"]
        if s.description:
            block.append(s.description)
        if s.tool_chain:
            block.append("Suggested tools: " + ", ".join(s.tool_chain))
        if s.body:
            block.append(f"Load the complete procedure with read_skill(name={s.name!r}) before using it.")
        if s.success_criteria:
            block.append("Success when: " + "; ".join(s.success_criteria))
        parts.append("\n".join(block))
    return "\n\n".join(parts)


# ── writing / import / export ───────────────────────────────────

def _render_skill_md(skill: Skill) -> str:
    fm = {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "triggers": skill.triggers,
        "tool_chain": skill.tool_chain,
        "success_criteria": skill.success_criteria,
        "license": skill.license,
        "source": skill.source,
    }
    if skill.permissions:
        fm["permissions"] = skill.permissions
    if _HAS_YAML:
        front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    else:
        front = "\n".join(f"{k}: {json.dumps(v)}" for k, v in fm.items())
    return f"---\n{front}\n---\n\n{skill.body.strip()}\n"


_SKILL_WRITE_LOCK = threading.RLock()


def _atomic_skill_write(target, data):
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_skill(name, description="", body="", triggers=None, tool_chain=None,
               success_criteria=None, source="friday", version=1, license="MIT",
               permissions=None, *, reason="", evidence=None, actor="owner",
               root=None) -> Path:
    """Create/overwrite a SKILL.md folder under ~/.friday/skills/.

    This is every writer's path EXCEPT Friday's own (``agent_save_skill``): the
    owner's, an import's, a workflow's. Such a save makes the skill one Friday
    did not write, so any provenance record in the folder is removed (it no
    longer matches anyway). Every write that changes the manifest appends one
    ledger line naming ``actor``."""
    with _SKILL_WRITE_LOCK:
        folder, _action, _content = _write_manifest(
            name, description=description, body=body, triggers=triggers,
            tool_chain=tool_chain, success_criteria=success_criteria, source=source,
            version=version, license=license, permissions=permissions,
            reason=reason, evidence=evidence, actor=_non_agent_actor(actor),
            root=root, agent=False)
    return folder


def _non_agent_actor(actor) -> str:
    """The public save path never records the actor as Friday herself; only
    agent_save_skill does."""
    a = str(actor or "owner").strip().lower() or "owner"
    return "owner" if a == AGENT_AUTHOR else a[:40]


def _write_manifest(name, *, description="", body="", triggers=None, tool_chain=None,
                    success_criteria=None, source="friday", version=1, license="MIT",
                    permissions=None, reason="", evidence=None, actor="owner",
                    root=None, agent=False):
    """Write the manifest (keeping the previous one in _history), then the
    provenance record (Friday's writes) or its removal (everyone else's), then
    one ledger line. Caller holds _SKILL_WRITE_LOCK. Returns (folder, action,
    content bytes); action is None when nothing changed."""
    root = Path(root) if root is not None else SKILLS_DIR
    root.mkdir(parents=True, exist_ok=True)
    folder = root / _safe_name(name)
    skill = Skill(
        name=name, description=description, body=body,
        triggers=_as_list(triggers), tool_chain=_as_list(tool_chain),
        success_criteria=_as_list(success_criteria),
        version=int(version), license=license, source=source, path=str(folder),
        permissions=_as_permissions(permissions),
    )
    target = folder / "SKILL.md"
    content = _render_skill_md(skill).encode("utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    previous = target.read_bytes() if target.exists() else None
    if previous == content and (agent == _provenance_ok(folder, folder.name, content)):
        return folder, None, content
    if previous is not None and previous != content:
        history = folder / "_history"
        history.mkdir(parents=True, exist_ok=True)
        # Content identity retains prior procedures even when an importer
        # reuses a version number. The manifest remains the active version.
        archived = history / (hashlib.sha256(previous).hexdigest() + ".md")
        if not archived.exists():
            _atomic_skill_write(archived, previous)
    _atomic_skill_write(target, content)
    sha = hashlib.sha256(content).hexdigest()
    if agent:
        _write_provenance(folder, sha)
    else:
        _drop_provenance(folder)
    action = "update" if previous is not None else "create"
    _append_ledger(folder, action, reason=reason, evidence=evidence, actor=actor,
                   sha256=sha, author=(AGENT_AUTHOR if agent else "owner"))
    return folder, action, content


# ── authorship (provenance), Friday's writes, archive, ledger ─────

#: Frontmatter key the old (held) design trusted. Ignored now; stripped on load.
CREATED_BY_KEY = "created_by"
#: In-memory meta key load_skills sets from the signed record (never parsed).
AUTHOR_KEY = "_friday_author"
AGENT_AUTHOR = "agent"
ARCHIVE_DIRNAME = ".archive"
LEDGER_NAME = ".ledger.jsonl"
PROVENANCE_NAME = ".provenance.json"
_PROVENANCE_VERSION = 1
_GENESIS = "0" * 64
_EVIDENCE_TEXT_MAX = 300

#: Caps on what Friday may put in a skill (single-line fields are flattened).
_AGENT_DESC_MAX = 500
_AGENT_ITEM_MAX = 200
_AGENT_ITEMS_MAX = 20
_AGENT_BODY_MAX = 20000


class SkillWriteRefused(PermissionError):
    """Friday asked to change a skill she did not write, or the write could
    not be proven to be exactly what she meant."""


_KEY_CACHE = {"key": None, "at": 0.0}
_KEY_TTL_S = 300.0


def _provenance_key() -> bytes:
    """The governance HMAC key (governance/proof_of_integrity), cached for a
    few minutes because load_skills runs on every turn. Raises when it cannot
    be read; callers fail closed (nothing counts as Friday's)."""
    import time as _time
    now = _time.monotonic()
    if _KEY_CACHE["key"] and now - _KEY_CACHE["at"] < _KEY_TTL_S:
        return _KEY_CACHE["key"]
    from agent_friday.governance.action_gate import _governance_key
    key = _governance_key()
    _KEY_CACHE.update(key=key, at=now)
    return key


def _mac(body: dict, key: bytes) -> str:
    import hmac as _hmac
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return _hmac.new(key, canonical, hashlib.sha256).hexdigest()


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _write_provenance(folder: Path, manifest_sha256: str) -> None:
    """Sign "Friday wrote <this folder name> with <this manifest>". Raises
    (SkillWriteRefused) when the key is unavailable: an unsigned skill of hers
    would silently become the owner's, so the write is refused before it."""
    record = {"v": _PROVENANCE_VERSION, "skill": folder.name, "author": AGENT_AUTHOR,
              "manifest_sha256": manifest_sha256, "signed": _now_iso()}
    record["mac"] = _mac(record, _provenance_key())
    _atomic_skill_write(folder / PROVENANCE_NAME,
                        (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))


def _drop_provenance(folder: Path) -> None:
    try:
        (folder / PROVENANCE_NAME).unlink()
    except FileNotFoundError:
        pass


def _provenance_ok(folder: Path, expect_name: str, manifest_bytes: Optional[bytes] = None) -> bool:
    """True only when ``folder`` holds a provenance record that (1) verifies
    under the governance key, (2) names ``expect_name`` and (3) carries the
    SHA-256 of the manifest that is in the folder now. Any doubt is False."""
    p = Path(folder) / PROVENANCE_NAME
    try:
        if not p.is_file():
            return False
        record = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("v") != _PROVENANCE_VERSION:
            return False
        if record.get("author") != AGENT_AUTHOR or record.get("skill") != _safe_name(expect_name):
            return False
        mac = record.get("mac")
        body = {k: v for k, v in record.items() if k != "mac"}
        import hmac as _hmac
        if not isinstance(mac, str) or not _hmac.compare_digest(_mac(body, _provenance_key()), mac):
            return False
        if manifest_bytes is None:
            man = Path(folder) / "SKILL.md"
            if not man.is_file():
                return False
            manifest_bytes = man.read_bytes()
        return hashlib.sha256(manifest_bytes).hexdigest() == record.get("manifest_sha256")
    except Exception:
        return False


def _clean_evidence(evidence):
    """An evidence POINTER: short identifiers, never a transcript."""
    if evidence is None:
        return None
    if isinstance(evidence, dict):
        out = {}
        for k, v in evidence.items():
            if v in (None, ""):
                continue
            out[str(k)[:40]] = str(v)[:_EVIDENCE_TEXT_MAX]
        return out or None
    return str(evidence)[:_EVIDENCE_TEXT_MAX] or None


def _last_line(path: Path) -> Optional[str]:
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    return lines[-1] if lines else None


def _append_ledger(folder, action, *, reason="", evidence=None, actor="owner",
                   sha256=None, author=None):
    """Append one line to ``<folder>/.ledger.jsonl``: chained to the previous
    line by its hash and signed with the governance key when it is readable
    (an unsigned line reads as unverified, never as Friday's). Append mode
    only: no caller in this module opens a ledger any other way."""
    path = Path(folder) / LEDGER_NAME
    prev = _last_line(path)
    entry = {"ts": _now_iso(), "action": action, "actor": str(actor or "owner")[:40],
             "reason": str(reason or "")[:_EVIDENCE_TEXT_MAX] or None,
             "evidence": _clean_evidence(evidence),
             "prev": hashlib.sha256(prev.encode("utf-8")).hexdigest() if prev else _GENESIS}
    if sha256:
        entry["sha256"] = sha256
    if author:
        entry["author"] = author
    try:
        entry["mac"] = _mac(entry, _provenance_key())
    except Exception:
        entry["mac"] = None
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())
    return entry


def _ledger_lines(path: Path) -> list:
    try:
        return [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


def _entry_signed(entry: dict) -> bool:
    mac = entry.get("mac") if isinstance(entry, dict) else None
    if not isinstance(mac, str) or not mac:
        return False
    try:
        import hmac as _hmac
        body = {k: v for k, v in entry.items() if k != "mac"}
        return _hmac.compare_digest(_mac(body, _provenance_key()), mac)
    except Exception:
        return False


def verify_ledger(path) -> dict:
    """Check a ledger file: every line parses, chains to the one before it,
    and carries a valid signature. {"ok", "entries", "problems": [...]}"""
    problems, prev = [], None
    lines = _ledger_lines(Path(path))
    for i, raw in enumerate(lines, 1):
        try:
            entry = json.loads(raw)
        except ValueError:
            problems.append(f"line {i}: not JSON")
            prev = raw
            continue
        want = hashlib.sha256(prev.encode("utf-8")).hexdigest() if prev else _GENESIS
        if entry.get("prev") != want:
            problems.append(f"line {i}: does not chain to the line before it")
        if not _entry_signed(entry):
            problems.append(f"line {i}: signature missing or wrong")
        prev = raw
    return {"ok": not problems, "entries": len(lines), "problems": problems}


def _read_ledger_file(path: Path) -> list:
    out = []
    for raw in _ledger_lines(path):
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def read_ledger(name, root=None) -> list:
    """The ledger of a live skill, or of its newest archived copy."""
    root = Path(root) if root is not None else SKILLS_DIR
    live = root / _safe_name(name) / LEDGER_NAME
    if live.exists():
        return _read_ledger_file(live)
    copies = _archived_copies(name, root)
    return _read_ledger_file(copies[-1] / LEDGER_NAME) if copies else []


def skill_owner(name, root=None) -> Optional[str]:
    """Who holds ``name``: "agent" (Friday wrote it, by signed provenance),
    "owner" (anything else that answers to the name: the owner's, imported,
    bundled, workflow, legacy YAML, or a folder whose record does not verify),
    or None when nothing does.

    A name is Friday's only when the folder she would write carries a valid
    record AND no other skill answers to the same name (a write there would
    shadow it)."""
    root = Path(root) if root is not None else SKILLS_DIR
    safe = _safe_name(name)
    folder = root / safe
    if (root / f"{safe}.yaml").exists() or (root / f"{safe}.yml").exists():
        return "owner"
    for s in load_skills([root, BUNDLED_DIR]):
        if s.name in (name, safe) and not friday_wrote(s):
            return "owner"
    if folder.exists():
        return AGENT_AUTHOR if _provenance_ok(folder, safe) else "owner"
    return None


def _flat(v, cap) -> str:
    """One line: newlines and runs of space collapse, capped."""
    return " ".join(str(v if v is not None else "").split())[:cap]


def _agent_fields(fields) -> dict:
    """Friday's skill text, shaped so nothing in it can act as structure:
    single-line fields are flattened, lists are capped, no permissions, the
    source is fixed. The body may hold anything (it is below the manifest)."""
    fields = dict(fields or {})

    def _items(v):
        return [s for s in (_flat(x, _AGENT_ITEM_MAX) for x in _as_list(v)) if s][:_AGENT_ITEMS_MAX]
    try:
        version = max(1, min(int(fields.get("version") or 1), 10_000))
    except (TypeError, ValueError):
        version = 1
    return {
        "description": _flat(fields.get("description"), _AGENT_DESC_MAX),
        "body": str(fields.get("body") or "").strip()[:_AGENT_BODY_MAX],
        "triggers": _items(fields.get("triggers")),
        "tool_chain": _items(fields.get("tool_chain")),
        "success_criteria": _items(fields.get("success_criteria")),
        "version": version,
    }


def _round_trip_ok(content: bytes, safe: str, want: dict) -> bool:
    """Parse the manifest about to be written exactly as load_skills will and
    require it to say what was meant: the folder's name, source friday, no
    permissions, no extra keys, the same fields and body."""
    try:
        fm, body = _parse_frontmatter(content.decode("utf-8"))
    except Exception:
        return False
    allowed = {"name", "description", "version", "triggers", "tool_chain",
               "success_criteria", "license", "source"}
    if set(fm) - allowed:
        return False
    sk = _skill_from_manifest(Path(safe), content.decode("utf-8"))
    return (sk.name == safe and sk.source == "friday" and not sk.permissions
            and sk.description == want["description"] and sk.body == want["body"]
            and sk.triggers == want["triggers"] and sk.tool_chain == want["tool_chain"]
            and sk.success_criteria == want["success_criteria"]
            and sk.version == want["version"] and sk.license == "MIT")


def agent_save_skill(name, fields, *, reason="", evidence=None, root=None):
    """Friday's create/update. Writes only a new name or a skill she wrote,
    signs the provenance record, ledgers it, and returns (folder, action).
    Raises SkillWriteRefused for anything else, with the files untouched."""
    root = Path(root) if root is not None else SKILLS_DIR
    safe = _safe_name(name)
    want = _agent_fields(fields)
    with _SKILL_WRITE_LOCK:
        try:
            _provenance_key()
        except Exception:
            raise SkillWriteRefused(
                "the governance key cannot be read, so a skill Friday writes could not be "
                "signed as hers. Nothing was written.")
        owner = skill_owner(safe, root)
        if owner == "owner":
            raise SkillWriteRefused(
                f"'{safe}' is not a skill Friday wrote, so Friday cannot change it. "
                "Only the owner can edit it.")
        probe = Skill(name=safe, description=want["description"], body=want["body"],
                      triggers=want["triggers"], tool_chain=want["tool_chain"],
                      success_criteria=want["success_criteria"], version=want["version"],
                      source="friday")
        if not _round_trip_ok(_render_skill_md(probe).encode("utf-8"), safe, want):
            raise SkillWriteRefused(
                "the skill text would not read back as written (it may try to change the "
                "skill's name, source or permissions). Nothing was written.")
        folder, action, _content = _write_manifest(
            safe, description=want["description"], body=want["body"],
            triggers=want["triggers"], tool_chain=want["tool_chain"],
            success_criteria=want["success_criteria"], source="friday",
            version=want["version"], reason=reason, evidence=evidence,
            actor=AGENT_AUTHOR, root=root, agent=True)
    return folder, (action or ("update" if owner == AGENT_AUTHOR else "create"))


def _archived_copies(name, root) -> list:
    base = Path(root) / ARCHIVE_DIRNAME / _safe_name(name)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and _find_manifest(p))


def list_archived(root=None) -> list:
    root = Path(root) if root is not None else SKILLS_DIR
    base = root / ARCHIVE_DIRNAME
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and _archived_copies(p.name, root))


def archive_skill(name, *, reason="", evidence=None, actor="owner", root=None) -> Path:
    """Move a skill folder, with its history, ledger and provenance, into
    ``.archive/<name>/<stamp>/``. Nothing is deleted. ``actor="agent"`` may
    archive only a skill Friday wrote."""
    root = Path(root) if root is not None else SKILLS_DIR
    safe = _safe_name(name)
    folder = root / safe
    with _SKILL_WRITE_LOCK:
        if actor == AGENT_AUTHOR and skill_owner(safe, root) != AGENT_AUTHOR:
            raise SkillWriteRefused(
                f"'{safe}' is not a skill Friday wrote, so Friday cannot archive it.")
        if not (folder.is_dir() and _find_manifest(folder)):
            raise FileNotFoundError(f"skill not found: {safe}")
        stamp = _now_iso().replace(":", "").replace(".", "").replace("-", "")
        dest = root / ARCHIVE_DIRNAME / safe / stamp
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), str(dest))
        _append_ledger(dest, "archive", reason=reason, evidence=evidence, actor=actor)
    return dest


def _archived_by_agent(copy: Path) -> bool:
    """The copy's newest ledger line is a signed archive by Friday."""
    lines = _ledger_lines(copy / LEDGER_NAME)
    if not lines:
        return False
    try:
        last = json.loads(lines[-1])
    except ValueError:
        return False
    return (last.get("action") == "archive" and last.get("actor") == AGENT_AUTHOR
            and _entry_signed(last))


def restore_skill(name, *, reason="", evidence=None, actor="owner", root=None) -> Path:
    """Move the newest archived copy of ``name`` back into place. Refuses if
    a live skill already answers to the name. ``actor="agent"`` may restore
    only a copy Friday wrote AND archived herself: a skill of hers the owner
    archived stays archived until the owner brings it back."""
    root = Path(root) if root is not None else SKILLS_DIR
    safe = _safe_name(name)
    folder = root / safe
    with _SKILL_WRITE_LOCK:
        copies = _archived_copies(safe, root)
        if not copies:
            raise FileNotFoundError(f"no archived copy of: {safe}")
        source = copies[-1]
        if actor == AGENT_AUTHOR and not (_provenance_ok(source, safe) and _archived_by_agent(source)):
            raise SkillWriteRefused(
                f"The archived '{safe}' is not a skill Friday wrote and archived herself, "
                "so only the owner can restore it.")
        if folder.exists() or skill_owner(safe, root) is not None:
            raise FileExistsError(f"a live skill already uses the name: {safe}")
        shutil.move(str(source), str(folder))
        _append_ledger(folder, "restore", reason=reason, evidence=evidence, actor=actor)
        try:
            source.parent.rmdir()          # only when no older copy remains
        except OSError:
            pass
    return folder


def import_skill(src, name=None) -> dict:
    """Import a skill from a folder, a .zip, or a legacy single-file .yaml.

    Normalizes OpenClaw/agentskills folders (which already use SKILL.md) and
    Friday's legacy YAML into the canonical SKILL.md folder format under
    ~/.friday/skills/, tagging ``source: imported``.
    """
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"import source not found: {src}")

    tmp = None
    try:
        # 1) zip -> extract to temp, then treat as a folder
        if src.is_file() and src.suffix.lower() == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix="skillimp_"))
            with zipfile.ZipFile(src) as zf:
                zf.extractall(tmp)
            folder = _locate_skill_folder(tmp)
            if folder is None:
                raise ValueError("zip contains no SKILL.md")
            skill = _skill_from_manifest(
                folder, _find_manifest(folder).read_text(encoding="utf-8", errors="replace"),
                default_source="imported")

        # 2) legacy single-file YAML
        elif src.is_file() and src.suffix.lower() in (".yaml", ".yml"):
            skill = _skill_from_legacy_yaml(src)
            if skill is None:
                raise ValueError("could not parse legacy YAML skill")
            skill.source = "imported"

        # 3) folder containing a manifest
        elif src.is_dir():
            man = _find_manifest(src)
            if man is None:
                raise ValueError(f"folder has no {_MANIFEST_NAMES[0]}")
            skill = _skill_from_manifest(
                src, man.read_text(encoding="utf-8", errors="replace"),
                default_source="imported")
        else:
            raise ValueError(f"unsupported import source: {src}")

        if name:
            skill.name = name
        skill.source = "imported"
        folder = save_skill(
            skill.name, skill.description, skill.body, skill.triggers,
            skill.tool_chain, skill.success_criteria, source="imported",
            version=skill.version, license=skill.license,
            # Only manifest fields are copied: a package's own .provenance.json
            # or ledger is never brought in, so an import is never Friday's.
            reason="imported", evidence={"source": src.name}, actor="import")
        return {"imported": skill.name, "path": str(folder),
                "triggers": skill.triggers, "source": "imported"}
    finally:
        if tmp and tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _locate_skill_folder(root: Path) -> Optional[Path]:
    """Find the first folder (root or a child) that contains a manifest."""
    if _find_manifest(root):
        return root
    for child in sorted(root.rglob("*")):
        if child.is_dir() and _find_manifest(child):
            return child
    # some zips put SKILL.md at top level alongside files but root has no subdir
    for n in _MANIFEST_NAMES:
        hits = list(root.rglob(n))
        if hits:
            return hits[0].parent
    return None


def export_skill(name, dest=None) -> Path:
    """Package a skill folder into a portable .zip. Materializes a SKILL.md for
    legacy-YAML-only skills so the export is always canonical."""
    skill = get_skill(name)
    if skill is None:
        raise FileNotFoundError(f"skill not found: {name}")
    dest = Path(dest) if dest else (SKILLS_DIR / f"{_safe_name(name)}.zip")

    src_folder = Path(skill.path)
    staging = Path(tempfile.mkdtemp(prefix="skillexp_"))
    try:
        out_folder = staging / _safe_name(name)
        out_folder.mkdir(parents=True, exist_ok=True)
        if src_folder.is_dir():
            # copy the whole folder, then ensure a canonical SKILL.md exists
            for item in src_folder.iterdir():
                # Local records stay local: the ledger names conversations,
                # and provenance is meaningless on another machine.
                if item.name in (LEDGER_NAME, PROVENANCE_NAME, ARCHIVE_DIRNAME):
                    continue
                if item.is_file():
                    shutil.copy2(item, out_folder / item.name)
                elif item.is_dir():
                    shutil.copytree(item, out_folder / item.name, dirs_exist_ok=True)
        if not _find_manifest(out_folder):
            (out_folder / "SKILL.md").write_text(_render_skill_md(skill), encoding="utf-8")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in out_folder.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(staging))
        return dest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ── closed-loop integration ─────────────────────────────────────

def register_with_skillopt(skill: Skill) -> bool:
    """Register a skill's body as a SkillOpt version so the optimizer can
    version/score/improve it. Best-effort; never raises."""
    try:
        from agent_friday.skillopt_engine import get_engine
        content = skill.body or skill.description or skill.name
        get_engine().register_skill(skill.name, content, notes=f"source={skill.source}")
        return True
    except Exception:
        return False
