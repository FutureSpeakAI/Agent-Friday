"""One-click export as a plain project (docs/design/active/vibe-coding-salon.md
§4.11 item 10): no lock-in. The zip holds the working tree and nothing of
Friday's: no .git, no .friday receipts, no node_modules, no key binding. A
plain README says what it is. Exporting is INTERNAL: it reads Friday's own
folder and hands the user a file.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from agent_friday.services import codebases as cb


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


def test_the_export_is_the_working_tree_and_nothing_of_fridays():
    rec = cb.create("Rent Tracker", template="static")
    cb.step(rec["id"], {"notes.txt": "hello", "sub/deep.txt": "deep"}, "Files")
    (cb.repo_path(rec["id"]) / "node_modules").mkdir()
    (cb.repo_path(rec["id"]) / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    name, data = cb.export_zip(rec["id"])
    assert name == "rent-tracker.zip"
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert "rent-tracker/index.html" in names and "rent-tracker/notes.txt" in names and "rent-tracker/sub/deep.txt" in names
    assert not any(".git/" in n or ".friday/" in n or "node_modules/" in n or n.endswith(".gitignore") for n in names), names
    assert z.read("rent-tracker/notes.txt") == b"hello"


def test_the_readme_says_it_is_a_plain_project_and_is_not_duplicated():
    rec = cb.create("Tracker", template="static")
    name, data = cb.export_zip(rec["id"])
    z = zipfile.ZipFile(io.BytesIO(data))
    readme = z.read("tracker/README.md").decode("utf-8")
    assert "Tracker" in readme and "no lock-in" in readme.lower()
    assert readme.count("Exported from Friday") == 1
    # A second export does not stack a second note.
    name, data = cb.export_zip(rec["id"])
    assert zipfile.ZipFile(io.BytesIO(data)).read("tracker/README.md").decode("utf-8").count("Exported from Friday") == 1


def test_a_codebase_without_a_readme_gets_one():
    rec = cb.create("Tracker", template="static")
    cb.step(rec["id"], {"README.md": None}, "Drop readme")
    name, data = cb.export_zip(rec["id"])
    readme = zipfile.ZipFile(io.BytesIO(data)).read("tracker/README.md").decode("utf-8")
    assert readme.startswith("# Tracker")


def test_exporting_leaves_the_codebase_untouched():
    rec = cb.create("Tracker", template="static")
    before = cb.steps(rec["id"])
    cb.export_zip(rec["id"])
    assert cb.steps(rec["id"]) == before
    assert not (cb.repo_path(rec["id"]) / "Exported").exists()


def test_export_is_internal_for_the_gate():
    from agent_friday.governance import action_gate
    from agent_friday.services import agent as ag
    assert "codebase_export" in ag.CLAUDE_TOOL_HANDLERS
    assert action_gate.classify("codebase_export", {})[0] == action_gate.INTERNAL
