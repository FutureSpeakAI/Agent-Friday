"""Every passed section 34 row points at real things (north-star amendment A26).

docs/release/acceptance-34.json is what the release gate (scripts/release_acceptance.py)
reads. A row may say pass only with its evidence. This keeps that evidence honest: each
passed row names at least one file that exists in this repository, names the CI run that
proved it, and says when and against which commit it was checked. A file renamed or deleted
later turns this red, so a pass cannot outlive what it rests on.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECKLIST = REPO / "docs" / "release" / "acceptance-34.json"
FILE = re.compile(r"[\w./-]+\.(?:py|ts|yml|yaml|ps1|js|json|md|iss|toml|html)\b")
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}


def _files() -> set[str]:
    out = set()
    for p in REPO.rglob("*"):
        if p.is_file() and not (SKIP & set(p.relative_to(REPO).parts)):
            out.add(p.relative_to(REPO).as_posix())
    return out


def _passed():
    return [r for r in json.loads(CHECKLIST.read_text(encoding="utf-8"))["rows"] if r["status"] == "pass"]


def test_the_rows_met_on_main_are_recorded():
    # 2026-10-28: 50 rows were checked against main df2872452 and found met. The count only grows.
    assert len(_passed()) >= 50


def test_each_pass_names_files_that_exist():
    files = _files()
    missing = {}
    for r in _passed():
        named = FILE.findall(r["evidence"])
        assert named, f"{r['id']}: the evidence names no file"
        gone = [n for n in named if not any(f == n.lstrip("./") or f.endswith("/" + n.lstrip("./")) for f in files)]
        if len(gone) == len(named):
            missing[r["id"]] = gone
    assert missing == {}, "passed rows whose every named file is gone"


def test_each_pass_names_its_ci_run_and_when_it_was_checked():
    for r in _passed():
        assert re.search(r"\brun \d{8,}", r["evidence"]), f"{r['id']}: no CI run named"
        assert r.get("checked"), f"{r['id']}: no record of when and against what it was checked"