"""Q6 guard: a docstring that says a rule is "Held by tests/..." must name a test file that exists.

A rule pointing at a deleted or misspelled test is prose again. The scan is proved able to fail on a broken
copy below (Q2); to fix a finding, point the docstring at the real test or delete the claim.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAIM = re.compile(r"(?:Held|Enforced) by\s+(tests/[\w/.\-]+\.py)")


def missing_tests(src: Path, root: Path) -> list:
    out = []
    for path in sorted(src.rglob("*.py")):
        for rel in CLAIM.findall(path.read_text(encoding="utf-8", errors="replace")):
            if not (root / rel).is_file():
                out.append("%s names %s, which does not exist" % (path.relative_to(root).as_posix(), rel))
    return out


def test_every_named_enforcing_test_exists():
    assert missing_tests(ROOT / "src", ROOT) == []


def test_the_scan_catches_a_claim_that_points_nowhere(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "real.py").write_text("")
    (tmp_path / "src" / "good.py").write_text('"""Held by tests/real.py."""\n')
    (tmp_path / "src" / "bad.py").write_text('"""Held by tests/ghost.py."""\n')
    assert missing_tests(tmp_path / "src", tmp_path) == ["src/bad.py names tests/ghost.py, which does not exist"]
