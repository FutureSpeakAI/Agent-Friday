"""Gauntlet finding F51, and the structural fix findings.jsonl F49 asked
for once a second instance turned up: "if there's a common place that
should be doing cleanup for all of them, fixing that is worth more than
fixing the third one you find next week" (the maintainer, 2026-09-04).

Three independent instances of the SAME leak class have now been found in
one day, each in a different top-level file under tests/, each because
that file hand-rolled its own isolated-home redirect instead of relying
on tests/conftest.py's shared one (which pytest already applies to every
file under tests/ automatically, and which has real crash-safe cleanup --
a startup sweep plus a retry-backed pytest_sessionfinish):

  - tests/conftest.py's OWN fixture (F47) -- the original, now fixed.
  - tests/test_judgment_gate.py (F49) -- 97 leaked dirs, ~110GB, since
    2026-08-17. Fixed by deleting the duplicate block.
  - tests/test_egress_adversarial.py (F51) -- 327 leaked dirs, ~262MB,
    since 2026-06-28 (nearly two and a half months, the oldest of the
    three). Found by the broad sweep this file's own existence is the
    fix for; fixed the same way.

A green tests/gauntlet/ suite cannot catch any of these by running once --
the leak is a side effect of a run, not a test outcome, and each instance
lived in a plain sibling file nothing else was watching. This test is the
"a fourth instance can never silently reappear" guard: it scans every
top-level .py file under tests/ (excluding conftest.py itself, which is
SUPPOSED to do this) for the exact anti-pattern -- a module-level
tempfile.mkdtemp with a "friday"-ish prefix, paired with a hardcoded
USERPROFILE/HOMEDRIVE/HOMEPATH/HOME environment redirect -- and fails
loudly, naming the file, if it finds one. New test files should never
need this pattern at all: conftest.py's isolation already applies to them.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TESTS_DIR = _REPO_ROOT / "tests"

# conftest.py files are the ONE place this pattern is supposed to live.
_EXEMPT_NAMES = {"conftest.py"}

_MKDTEMP_FRIDAY_RE = re.compile(r'mkdtemp\(\s*prefix\s*=\s*["\']friday_')
_HOME_ENV_RE = re.compile(
    r'os\.environ\[["\'](?:USERPROFILE|HOMEDRIVE|HOMEPATH|HOME)["\']\]\s*=')


def _offending_files():
    offenders = []
    for path in _TESTS_DIR.rglob("*.py"):
        if path.name in _EXEMPT_NAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _MKDTEMP_FRIDAY_RE.search(text) and _HOME_ENV_RE.search(text):
            offenders.append(path.relative_to(_REPO_ROOT))
    return offenders


def test_no_test_file_hand_rolls_its_own_isolated_home():
    offenders = _offending_files()
    assert not offenders, (
        "the following test file(s) mint their OWN isolated home via "
        "tempfile.mkdtemp(prefix='friday_...') plus a hardcoded "
        "USERPROFILE/HOMEDRIVE/HOMEPATH/HOME redirect, instead of relying "
        "on tests/conftest.py's shared, crash-safe one (which pytest "
        "already applies to every file under tests/ automatically): "
        + ", ".join(str(p) for p in offenders)
        + " -- this is the exact leak class findings.jsonl F47/F49/F51 "
        "each independently hit (one leaked home per test run, forever, "
        "because a hand-rolled block has no cleanup of its own). Delete "
        "the duplicate block; the file already gets the shared isolation "
        "for free."
    )


def test_conftest_py_files_are_syntactically_the_only_expected_exception():
    """Grounding check: confirms this probe's exemption list is doing
    something real (conftest.py DOES use this exact pattern, by design)
    rather than accidentally exempting everything."""
    conftest = _TESTS_DIR / "conftest.py"
    text = conftest.read_text(encoding="utf-8")
    assert _MKDTEMP_FRIDAY_RE.search(text), (
        "tests/conftest.py no longer matches the isolated-home pattern this "
        "probe watches for -- if its mechanism changed shape, this probe's "
        "own pattern-matching may need to change with it, not just its "
        "exemption list"
    )
