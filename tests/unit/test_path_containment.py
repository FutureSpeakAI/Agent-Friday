"""agent_friday.paths.contained / safe_name: a caller-supplied name stays inside
its Friday-owned root, or nothing touches the disk.

Every request-, model- or tool-supplied name joined under a fixed directory
goes through these two helpers. The cases below are the ways a joined path
leaves its root: parent segments, absolute paths, Windows drive and UNC
forms, and a symlink or junction inside the root that points out of it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_friday.paths import contained, safe_name


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    (r / "sub").mkdir(parents=True)
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    return r


def test_plain_names_resolve_inside(root):
    assert contained(root, "a.md") == Path(os.path.realpath(root)) / "a.md"
    assert contained(root, "sub/b.md") == Path(os.path.realpath(root)) / "sub" / "b.md"
    # a `..` that stays inside is fine
    assert contained(root, "sub/../c.md") == Path(os.path.realpath(root)) / "c.md"


@pytest.mark.parametrize("rel", [
    "..", "../outside.txt", "sub/../../outside.txt", "..\\outside.txt"
    if os.name == "nt" else "../outside.txt",
])
def test_parent_traversal_is_refused(root, rel):
    with pytest.raises(ValueError):
        contained(root, rel)


def test_absolute_path_is_refused(root, tmp_path):
    with pytest.raises(ValueError):
        contained(root, str(tmp_path / "outside.txt"))
    with pytest.raises(ValueError):
        contained(root, "/etc/passwd")


@pytest.mark.skipif(os.name != "nt", reason="Windows path forms")
@pytest.mark.parametrize("rel", [
    "C:\\Windows\\win.ini", "C:/Windows/win.ini", "D:relative.txt",
    "\\\\server\\share\\x.txt", "//server/share/x.txt",
    "\\\\?\\C:\\Windows\\win.ini", "\\Windows\\win.ini",
])
def test_windows_drive_unc_and_device_paths_are_refused(root, rel):
    with pytest.raises(ValueError):
        contained(root, rel)


def _make_dir_link(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":  # a junction needs no privilege
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True)
        return r.returncode == 0
    return False


def test_symlink_inside_root_pointing_out_is_refused(root, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.md").write_text("x", encoding="utf-8")
    if not _make_dir_link(root / "link", outside):
        pytest.skip("cannot create a symlink or junction here")
    with pytest.raises(ValueError):
        contained(root, "link/x.md")


def test_root_itself_only_when_allowed(root):
    with pytest.raises(ValueError):
        contained(root, "")
    with pytest.raises(ValueError):
        contained(root, ".")
    assert contained(root, "", allow_root=True) == Path(os.path.realpath(root))


def test_nul_byte_is_refused(root):
    with pytest.raises(ValueError):
        contained(root, "a\x00.md")


@pytest.mark.parametrize("name", ["abc", "conv-1a2b", "goal_1.json", "tl-x"])
def test_safe_name_accepts_single_components(name):
    assert safe_name(name) == name


@pytest.mark.parametrize("name", [
    "", None, ".", "..", "a/b", "a\\b", "..\\..", "C:x", "x\x00y",
])
def test_safe_name_refuses_anything_else(name):
    with pytest.raises(ValueError):
        safe_name(name)
