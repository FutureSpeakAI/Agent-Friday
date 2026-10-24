"""North star §34.4-1 (the installer's signature is verified) is BLOCKED on the
owner's decision: there is no code-signing certificate yet (Stephen, 2026-10-22:
"not yet"). Until there is, nothing a person reads may say the setup program is
signed, and the release notes must say plainly that it is not.

The release job publishes RELEASE_NOTES.md as the release's notes, and README.md
is the download page's text. installer.yml's signing step runs only when the
certificate secrets exist; when they do, this test and the notes change together,
on purpose.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Sentences that would claim the setup program is signed.
_CLAIM = re.compile(
    r"(signed (setup|installer|setup program|download)"
    r"|(setup program|installer|setup|download) (is|was|comes) (now )?(digitally |code[- ])?signed"
    r"|digitally signed by|signed by futurespeak|verified publisher)", re.I)
_HONEST = re.compile(r"not code-signed|unsigned", re.I)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_release_notes_say_the_installer_is_not_signed():
    notes = _read("RELEASE_NOTES.md")
    assert _HONEST.search(notes), "the release notes must say the setup program is not code-signed"


def test_nothing_a_person_reads_claims_the_installer_is_signed():
    pages = ["RELEASE_NOTES.md", "README.md"] + [
        str(p.relative_to(ROOT)) for p in (ROOT / "docs" / "getting-started").rglob("*.md")
    ] + [str(p.relative_to(ROOT)) for p in (ROOT / "docs" / "user-guide").rglob("*.md")]
    for rel in pages:
        for line_no, line in enumerate(_read(rel).splitlines(), 1):
            m = _CLAIM.search(line)
            if m and not re.search(r"\b(not|never|no|isn't|unsigned|once|until|when)\b", line, re.I):
                raise AssertionError("%s:%d claims a signed installer: %s" % (rel, line_no, line.strip()))


def test_the_release_job_checks_its_notes_before_publishing():
    wf = _read(".github/workflows/installer.yml")
    rel = wf[wf.index("\n  release:"):]
    assert "The notes never claim a signed installer" in rel
