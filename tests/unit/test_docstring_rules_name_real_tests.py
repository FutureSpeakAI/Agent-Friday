"""Q6 guard: every ``tests/<file>.py`` a source comment or docstring names must exist.

Any ``tests/...py`` mention counts ("Held by", "Enforced by", "tests/x.py fails if", a second path after "and",
backticks, a path wrapped onto the next line): the repo words these claims many ways, so the guard reads the
path, not the phrase. A bare name without the ``tests/`` prefix is out of scope. A rule pointing at a deleted or
misspelled test is prose again. The scan is proved able to fail on a broken copy below (Q2); to fix a finding,
point the comment at the real test or delete the claim.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAIM = re.compile(r"tests/[\w/\-]+\.py")
# "tests/unit/" at a line end with the file name on the next line (a comment marker may sit between them)
WRAPPED = re.compile(r"(tests/[\w/\-]*/)[ \t]*\n[ \t]*(?:#[ \t]*)?(?=[\w\-])")


def missing_tests(src: Path, root: Path) -> list:
    out = []
    for path in sorted(src.rglob("*.py")):
        text = WRAPPED.sub(r"\1", path.read_text(encoding="utf-8", errors="replace"))
        for rel in CLAIM.findall(text):
            if not (root / rel).is_file():
                out.append("%s names %s, which does not exist" % (path.relative_to(root).as_posix(), rel))
    return out


def test_every_named_enforcing_test_exists():
    assert missing_tests(ROOT / "src", ROOT) == []


def test_the_scan_catches_a_claim_that_points_nowhere_in_any_phrasing(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "real.py").write_text("")
    (tmp_path / "src" / "good.py").write_text('"""Held by tests/real.py and `tests/real.py`."""\n')
    (tmp_path / "src" / "bad.py").write_text('"""Held by tests/real.py and tests/ghost.py."""\n')
    (tmp_path / "src" / "fails.py").write_text("# tests/nope.py fails if this drifts\n")
    (tmp_path / "src" / "wrap.py").write_text('"""Pinned by ``tests/unit/\ntest_gone.py`` (wrapped).\n# see tests/api/\n# test_gone_too.py"""\n')
    assert missing_tests(tmp_path / "src", tmp_path) == [
        "src/bad.py names tests/ghost.py, which does not exist",
        "src/fails.py names tests/nope.py, which does not exist",
        "src/wrap.py names tests/unit/test_gone.py, which does not exist",
        "src/wrap.py names tests/api/test_gone_too.py, which does not exist"]
