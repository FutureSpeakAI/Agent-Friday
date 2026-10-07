"""Audit B2: navigate_to(file | creation) lands on the file view with that file focused.

"studio" is an alias (WS_ALIASES) for Media, so a target addressed to it opened the Media library and
dropped root, path and file: the owner was told NAV_PARTIAL and saw the wrong screen. Files and
creations now open the Files browser inside the Library workspace (its "Browse this PC" view), a
workspace that exists, and the Library reports the folder and chosen file so the tool can confirm
the exact item.
"""
from __future__ import annotations

import re
from pathlib import Path

from agent_friday.services import desktop_targets as dt

ROOT = Path(__file__).resolve().parents[2]


def _aliases():
    """The workspace ids the page rewrites to another workspace (WS_ALIASES in index.html)."""
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    block = page[page.index("const WS_ALIASES = {"):page.index("function fridayWorkspaceId")]
    return set(re.findall(r"^  (\w+): \{", block, flags=re.M))


def _studio_roots(tmp_path, monkeypatch):
    from agent_friday.services import studio_files
    docs = tmp_path / "Documents"
    (docs / "Finance").mkdir(parents=True)
    (docs / "Finance" / "budget.xlsx").write_bytes(b"x")
    monkeypatch.setattr(studio_files, "roots", lambda: {"documents": docs})
    return docs


def test_the_alias_set_is_what_the_page_says():
    assert {"studio", "wiki", "draft", "content"} <= _aliases()


def test_a_file_target_names_a_workspace_that_exists_and_keeps_its_place(tmp_path, monkeypatch):
    docs = _studio_roots(tmp_path, monkeypatch)
    r = dt.resolve_file(id=str(docs / "Finance" / "budget.xlsx"))
    assert r["ok"], r
    t = r["target"]
    assert t["workspace"] not in _aliases(), "an aliased id lands on another workspace and loses the rest of the target"
    assert t == {"workspace": "library", "view": "pc", "root": "documents", "path": "Finance", "file": "budget.xlsx"}
    assert r["verify"] == {"workspace": "library", "key": "file", "value": "budget.xlsx"}


def test_a_creation_target_opens_the_creations_folder_with_the_file_chosen(tmp_path, monkeypatch):
    from agent_friday import core
    (tmp_path / "moon-over-the-harbor.png").write_bytes(b"x")
    monkeypatch.setattr(core, "CREATIONS_DIR", tmp_path)
    monkeypatch.setattr(core, "DAILY_CREATIONS_DIR", tmp_path / "none")
    r = dt.resolve("creation", query="moon over the harbor")
    assert r["ok"] and r["target"]["workspace"] not in _aliases(), r
    assert r["target"] == {"workspace": "library", "view": "pc", "root": "creations", "path": "",
                           "file": "moon-over-the-harbor.png"}
    assert r["verify"]["workspace"] == "library" and r["verify"]["key"] == "file"


def test_no_target_a_resolver_builds_is_addressed_to_an_alias():
    src = (ROOT / "src" / "agent_friday" / "services" / "desktop_targets.py").read_text(encoding="utf-8")
    built = set(re.findall(r'_ok\(\{"workspace": "(\w+)"', src))
    assert built, "found no targets"
    assert not (built & _aliases()), built & _aliases()


def test_the_library_takes_the_target_and_reports_what_it_shows():
    lib = (ROOT / "static" / "library_ws.js").read_text(encoding="utf-8")
    decl = lib[lib.index("__fridayNavDecls"):]
    assert "'root', 'path', 'file'" in decl, "the keys the Library accepts are declared"
    assert "window.__files3dOpen = { root: t.root, path: t.path || '', file: t.file || '' }" in lib
    assert "window.dispatchEvent(new Event('friday-files3d-open'))" in lib
    assert "root: f.root, path: f.path, file: f.file" in lib, "so the tool can confirm the exact file"
    assert "window.__files3dShowing" in (ROOT / "static" / "studio_files3d.js").read_text(encoding="utf-8")
