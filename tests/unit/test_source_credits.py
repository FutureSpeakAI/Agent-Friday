"""Every provenance marker in the source has a credit, and the corrected attributions hold.

The repository is public. A file that says it was ported, adapted, vendored or copied from
another project names that project in `CREDITS.md`, and a project whose licence requires
its notice (MIT, BSD, Apache) has that notice reproduced in `THIRD_PARTY_LICENSES.md`. The
marker grammar is the one the source already uses: "ported from X", "adapted from X",
"vendored (verbatim) from X", "copied from X", "port of X's", "borrowed from X".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CREDITS = ROOT / "CREDITS.md"
THIRD_PARTY = ROOT / "THIRD_PARTY_LICENSES.md"
NOTICE = ROOT / "NOTICE"

#: Where provenance markers are looked for. Tests and docs are not shipped code.
SCAN_ROOTS = ("src", "static", "scripts")
SCAN_SUFFIXES = {".py", ".js", ".ts", ".txt", ".md"}

#: "ported from obsidian-wiki's graph_analysis.py", "vendored verbatim from graphrag-workbench",
#: "Python port of graphrag-workbench's force-simulation", "adapted from podcastfy".
MARKER = re.compile(
    r"\b(?:ported|adapted|vendored|copied|borrowed|derived)\s+(?:verbatim\s+)?from\s+(?:the\s+)?"
    r"[`'\"]?([A-Za-z][A-Za-z0-9_.-]*(?:-[A-Za-z0-9_.-]+)*)|"
    r"\bport\s+of\s+[`'\"]?([A-Za-z][A-Za-z0-9_.-]*(?:-[A-Za-z0-9_.-]+)*)",
    re.IGNORECASE,
)

#: Words the grammar can catch that are not projects ("derived from the passphrase").
NOT_PROJECTS = {
    "a", "an", "it", "its", "this", "that", "the", "their", "there", "what", "which", "where",
    "here", "them", "those", "these", "each", "one", "two", "both", "any", "all", "scratch",
    "settings", "setting", "description", "frontmatter", "metadata", "wikilinks", "sentinel",
    "message", "passphrase", "user", "users", "page", "pages", "python", "scripts", "git",
    "source", "sources", "upstream", "main", "disk", "memory", "mail", "chat", "text",
}


def _project_names_in(text: str) -> set[str]:
    names = set()
    for m in MARKER.finditer(text):
        raw = (m.group(1) or m.group(2) or "").rstrip(".,;:")
        raw = re.sub(r"'s$", "", raw)
        if not raw or raw.lower() in NOT_PROJECTS or len(raw) < 3:
            continue
        # A bare module name is not a project; a project name has a hyphen, a dot or a capital.
        if raw.islower() and "-" not in raw and "." not in raw:
            continue
        names.add(raw)
    return names


def _source_files():
    for root in SCAN_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.suffix in SCAN_SUFFIXES and p.is_file() and "node_modules" not in p.parts and "vendor" not in p.parts:
                yield p


def _markers_by_file() -> dict[Path, set[str]]:
    found: dict[Path, set[str]] = {}
    for p in _source_files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        names = _project_names_in(text)
        if names:
            found[p] = names
    return found


def credit_gaps(credits_text: str, markers: dict[Path, set[str]]) -> list[str]:
    """Markers whose project is not named in the credits. Pure, so it can be shown red
    against an older credits file and green against this one."""
    low = credits_text.lower()
    gaps = []
    for p, names in sorted(markers.items()):
        for n in sorted(names):
            if n.lower() not in low:
                gaps.append(f"{p.relative_to(ROOT).as_posix()}: '{n}' is not credited in CREDITS.md")
    return gaps


def test_the_scanner_sees_the_known_ports():
    markers = _markers_by_file()
    names = {n.lower() for ns in markers.values() for n in ns}
    assert "obsidian-wiki" in names, names
    assert "graphrag-workbench" in names, names


def test_every_provenance_marker_has_a_credit():
    gaps = credit_gaps(CREDITS.read_text(encoding="utf-8"), _markers_by_file())
    assert gaps == [], "\n".join(gaps)


@pytest.mark.parametrize("project,holder", [
    ("obsidian-wiki", "Copyright (c) 2026 Ar9av"),
    ("graphrag-workbench", "Copyright (c) 2026 Lyon Industries"),
    ("GraphRAG", "Copyright (c) Microsoft Corporation"),
])
def test_ported_mit_code_carries_its_notice(project, holder):
    """MIT: the copyright notice and the permission notice travel with the code."""
    third = THIRD_PARTY.read_text(encoding="utf-8")
    assert project.lower() in third.lower(), project
    assert holder in third, holder
    assert third.count("Permission is hereby granted, free of charge") >= 1
    notice = NOTICE.read_text(encoding="utf-8")
    assert project.lower() in notice.lower(), f"{project} missing from NOTICE"


def test_skillopt_is_credited_to_microsoft_not_karpathy():
    engine = (ROOT / "src/agent_friday/skillopt_engine.py").read_text(encoding="utf-8")
    guide = (ROOT / "docs/user-guide/skills.md").read_text(encoding="utf-8")
    for text, where in ((engine, "skillopt_engine.py"), (guide, "skills.md")):
        assert "karpathy" not in text.lower(), f"{where} still credits SkillOpt to Karpathy"
        assert "microsoft" in text.lower(), f"{where} does not name Microsoft"
    credits = CREDITS.read_text(encoding="utf-8")
    assert "SkillOpt" in credits and "microsoft/SkillOpt" in credits


def test_podcastfy_is_credited_as_ideas_only():
    credits = CREDITS.read_text(encoding="utf-8")
    assert "podcastfy" in credits
    assert "ideas from" in credits.lower() or "only the idea" in credits.lower()
    # No podcastfy code or prompt tags in the podcast engine.
    src = (ROOT / "src/agent_friday/services/podcast_engine.py").read_text(encoding="utf-8")
    assert "<Person1>" not in src and "podcastfy" not in src.lower()
