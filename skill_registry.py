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
    ---
    <markdown body = the procedure Friday should follow>

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
"""

from __future__ import annotations

import os
import re
import io
import json
import shutil
import zipfile
import tempfile
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    import yaml
    _HAS_YAML = True
except Exception:                                   # pragma: no cover
    _HAS_YAML = False

HOME = Path(os.path.expanduser("~"))
SKILLS_DIR = HOME / ".friday" / "skills"            # user/learned/imported skills
BUNDLED_DIR = Path(__file__).resolve().parent / "skills"   # shipped Python skills

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
        }


# ── parsing ─────────────────────────────────────────────────────

def _parse_frontmatter(text: str):
    """Split a SKILL.md into (frontmatter_dict, body_markdown)."""
    fm, body = {}, text
    if text.lstrip().startswith("---"):
        stripped = text.lstrip()
        parts = stripped.split("---", 2)
        if len(parts) >= 3:
            block, body = parts[1], parts[2].lstrip("\n")
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
            "success_criteria", "version", "license", "source")},
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
    """Discover all skills. Folder SKILL.md wins over a same-named legacy YAML."""
    dirs = dirs or _default_dirs()
    by_name = {}
    legacy = {}
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
                            sk = _skill_from_manifest(
                                child, man.read_text(encoding="utf-8", errors="replace"),
                                default_source=("bundled" if d == BUNDLED_DIR else "friday"))
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
    return list(by_name.values())


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
    """Build a system-prompt block for skills matched by the message. '' if none."""
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
            block.append(s.body[:max_body])
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
    if _HAS_YAML:
        front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    else:
        front = "\n".join(f"{k}: {json.dumps(v)}" for k, v in fm.items())
    return f"---\n{front}\n---\n\n{skill.body.strip()}\n"


def save_skill(name, description="", body="", triggers=None, tool_chain=None,
               success_criteria=None, source="friday", version=1, license="MIT") -> Path:
    """Create/overwrite a SKILL.md folder under ~/.friday/skills/."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    folder = SKILLS_DIR / _safe_name(name)
    folder.mkdir(parents=True, exist_ok=True)
    skill = Skill(
        name=name, description=description, body=body,
        triggers=_as_list(triggers), tool_chain=_as_list(tool_chain),
        success_criteria=_as_list(success_criteria),
        version=int(version), license=license, source=source, path=str(folder),
    )
    (folder / "SKILL.md").write_text(_render_skill_md(skill), encoding="utf-8")
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
            version=skill.version, license=skill.license)
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
        from skillopt_engine import get_engine
        content = skill.body or skill.description or skill.name
        get_engine().register_skill(skill.name, content, notes=f"source={skill.source}")
        return True
    except Exception:
        return False
