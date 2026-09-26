"""A wiki write or delete never reaches outside WIKI_DIR (or the mirror root).

`_mirror_wiki_file` and `_delete_wiki_file` are the wiki's write and delete
sinks. They contain their own `rel` rather than trusting every caller to have
run `_safe_wiki_path` first, so a `..` segment cannot land a write or a delete
beside the wiki (SOUL.md, settings.json).
"""
from __future__ import annotations

import pytest

import agent_friday.core as core
import agent_friday.services.wiki_engine as we
from agent_friday.core import WIKI_DIR


@pytest.fixture
def victim():
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    p = WIKI_DIR.parent / "wiki-containment-victim.md"
    p.write_text("keep me", encoding="utf-8")
    yield p
    p.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _no_mirror(monkeypatch):
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})


@pytest.mark.parametrize("rel", [
    "../wiki-containment-victim.md",
    "notes/../../wiki-containment-victim.md",
    "..\\wiki-containment-victim.md",
])
def test_delete_outside_the_wiki_deletes_nothing(victim, rel):
    assert we._delete_wiki_file(rel) is False
    assert victim.read_text(encoding="utf-8") == "keep me"


def test_delete_by_absolute_path_deletes_nothing(victim):
    assert we._delete_wiki_file(str(victim)) is False
    assert victim.exists()


def test_write_outside_the_wiki_is_refused(victim):
    with pytest.raises(ValueError):
        we._mirror_wiki_file("../wiki-containment-victim.md", "overwritten")
    assert victim.read_text(encoding="utf-8") == "keep me"


def test_write_into_the_mirror_is_contained(monkeypatch, tmp_path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    monkeypatch.setattr(core, "_load_settings",
                        lambda *a, **k: {"wiki_mirror_dir": str(mirror)})
    rel = "containment/page.md"
    try:
        we._mirror_wiki_file(rel, "# ok\n")
        assert (mirror / "containment" / "page.md").read_text(encoding="utf-8") == "# ok\n"
        assert we._delete_wiki_file(rel) is True
        assert not (mirror / "containment" / "page.md").exists()
    finally:
        (WIKI_DIR / rel).unlink(missing_ok=True)


def test_inside_paths_still_work():
    rel = "containment/inside.md"
    try:
        we._mirror_wiki_file(rel, "# inside\n")
        assert (WIKI_DIR / rel).read_text(encoding="utf-8") == "# inside\n"
        assert we._delete_wiki_file(rel) is True
        assert not (WIKI_DIR / rel).exists()
    finally:
        (WIKI_DIR / rel).unlink(missing_ok=True)
