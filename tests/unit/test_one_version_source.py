"""Guard: exactly ONE place in the shipped Python reads the app's version.

WHY. On 2026-08-30 there were three answers to "what version is this?":

  * cli.py                — a regex over pyproject.toml, falling back to package
                            metadata, falling back to the string "unknown"
  * routes/core_routes.py — a tomllib parse of the SAME file, falling back to
                            package metadata, falling back to a hardcoded
                            release number
  * install-manifest.json — whatever the installer intended, which for every
                            5.6.0..5.6.4 upgrade was a version the machine was
                            not actually running

Three readers, three fallbacks, and one of them INVENTED a version number. That
is the same class of defect as the manifest bug it sits next to: a version
report that can be confidently wrong is worse than no version report, because
the user stops looking.

The weekly update check makes this load-bearing. A checker that disagrees with
`friday status` about the current version will either nag someone who is
current or reassure someone who is not.

So: services/app_version.py owns it, everything else delegates, and this test
fails if a fourth reader appears.

NOTE ON SELF-SCANNING. This file is scanned like every other tracked file, so
it must not contain the literals it forbids — the same discipline as
test_no_vendored_telemetry.py, which assembles its PostHog sample from
fragments at runtime rather than pasting a real key and allowlisting itself.
Every sample here is built the same way, and the prose describes the banned
patterns instead of quoting them.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# The module that is ALLOWED to read the version off disk.
OWNER = "src/agent_friday/services/app_version.py"

READS_PYPROJECT = re.compile(r"pyproject\.toml", re.IGNORECASE)

# The ACT of extracting a version, in the spellings this repo has actually
# used. The first alternative is the literal TEXT of cli.py's old hand-rolled
# regex as it appears in source — we are grepping FOR a regex, not applying
# one, which is why its backslash and star are themselves escaped.
PARSES_A_VERSION = re.compile(
    "|".join([
        r"\^version\\s\*=",                        # a hand-rolled pyproject regex
        r"tomllib\.load",                          # a TOML parse...
        r"toml\.load",
        r"""\[["']project["']\][^\n]*version""",   # ...indexed for the version
    ]),
    re.IGNORECASE,
)

# A hardcoded version literal assigned to a version variable — the invented
# fallback. Built here as a pattern, never as a sample.
INVENTS_A_VERSION = re.compile(r"""_?app_version\s*=\s*["']\d+\.\d+""")

# Paths that legitimately mention pyproject.toml without being a second reader.
# Each entry needs a reason; the point is that exceptions are visible in review.
ALLOWED = {
    OWNER: "owns the answer",
}


def _tracked_python() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "src/*.py", "*.py"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.decode("utf-8", "replace")
    return [p for p in out.split("\0") if p.endswith(".py")]


def test_only_app_version_parses_pyproject_for_the_version():
    offenders = []
    for rel in _tracked_python():
        if rel.replace("\\", "/") in ALLOWED:
            continue
        try:
            text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Naming the file is fine — comments explain WHY it is the source of
        # truth. Parsing a version out of it a second time is not. A module
        # that DELEGATES (calls running_version) is also fine, which is how
        # cli.py stays legal while still discussing pyproject.toml at length.
        if not READS_PYPROJECT.search(text):
            continue
        if PARSES_A_VERSION.search(text) and "running_version" not in text:
            offenders.append(rel)

    assert not offenders, (
        "A second implementation of version detection appeared:\n\n"
        + "\n".join(f"  {o}" for o in offenders)
        + "\n\nservices/app_version.py owns this. `friday status` and the weekly\n"
          "update check must not be able to disagree about what version is\n"
          "running — see that module's docstring for the 5.6.4 manifest bug\n"
          "this rule exists to prevent recurring."
    )


def test_no_shipped_code_invents_a_version_number():
    """No module may fall back to a version number it made up.

    /api/health used to assign a hardcoded release number to its version
    variable when it could not read pyproject.toml, so a machine that did not
    know its own version reported one it had invented to the About panel.
    Unknown must stay unknown all the way to the user.
    """
    offenders = []
    for rel in _tracked_python():
        if rel.replace("\\", "/") == OWNER:
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if INVENTS_A_VERSION.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Shipped code falls back to an invented version number:\n\n"
        + "\n".join(f"  {o}" for o in offenders)
        + "\n\nReturn None. A guessed version is what the manifest bug WAS."
    )


def test_the_guard_would_notice_a_new_reader():
    """Show every pattern firing, so a green run means something.

    All samples are assembled from fragments at runtime so this file never
    contains the text it forbids — otherwise the guard could not scan itself.
    """
    second_reader = (
        "import tomllib\n"
        'with open("pyproject.toml", "rb") as f:\n'
        '    v = tomllib.load(f)["project"]["version"]\n'
    )
    assert READS_PYPROJECT.search(second_reader), "missed the file being read"
    assert PARSES_A_VERSION.search(second_reader), "missed the version being parsed"

    hand_rolled = "m = re.search(r'(?m)" + chr(94) + "version" + chr(92) + "s*=', text)"
    assert PARSES_A_VERSION.search(hand_rolled), "missed a hand-rolled regex"

    invented = "_app" + "_version" + ' = "' + "5.0" + '.0"'
    assert INVENTS_A_VERSION.search(invented), "missed an invented version"

    # ...and none of them fire on prose about the rule, or this test would be
    # deleted by the next person who has to read its output.
    prose = "# pyproject.toml is the version's only source of truth.\n"
    assert not PARSES_A_VERSION.search(prose)
    assert not INVENTS_A_VERSION.search(prose)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
