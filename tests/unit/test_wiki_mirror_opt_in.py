"""The wiki mirror is opt-in and goes only to a folder the owner chose.

Wiki pages are private notes. A copy of every write to a fixed, cloud-synced
location would move them off the machine without the owner deciding it, so
the mirror is off unless `wiki_mirror_dir` names an existing absolute folder.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import agent_friday.core as core
import agent_friday.services.wiki_engine as we
from agent_friday.core import WIKI_DIR


def _settings(monkeypatch, **values):
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: dict(values))


@pytest.fixture
def page():
    rel = "mirror-test/page.md"
    yield rel
    (WIKI_DIR / rel).unlink(missing_ok=True)


def test_no_hard_coded_mirror_location_in_source():
    src = Path(we.__file__).read_text(encoding="utf-8")
    assert "My Drive" not in src
    assert not re.search(r"Path\(r?[\"'][A-Za-z]:\\", src), "no drive-letter path literals"


def test_mirror_is_off_by_default(monkeypatch):
    _settings(monkeypatch)
    assert we._wiki_mirror_dir() is None
    assert core.DEFAULT_SETTINGS.get("wiki_mirror_dir") == ""


def test_write_with_no_mirror_touches_only_the_wiki(monkeypatch, tmp_path, page):
    _settings(monkeypatch, wiki_mirror_dir="")
    we._mirror_wiki_file(page, "# Private\n")
    assert (WIKI_DIR / page).read_text(encoding="utf-8") == "# Private\n"
    assert list(tmp_path.iterdir()) == []


def test_owner_chosen_folder_receives_writes_and_deletes(monkeypatch, tmp_path, page):
    _settings(monkeypatch, wiki_mirror_dir=str(tmp_path))
    we._mirror_wiki_file(page, "# Mirrored\n")
    copy = tmp_path / "mirror-test" / "page.md"
    assert copy.read_text(encoding="utf-8") == "# Mirrored\n"
    assert we._delete_wiki_file(page) is True
    assert not copy.exists()


def test_missing_or_relative_folder_disables_the_mirror(monkeypatch, tmp_path):
    _settings(monkeypatch, wiki_mirror_dir=str(tmp_path / "does-not-exist"))
    assert we._wiki_mirror_dir() is None
    _settings(monkeypatch, wiki_mirror_dir="relative/folder")
    assert we._wiki_mirror_dir() is None
