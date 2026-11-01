"""Every passed section 34 row points at real things (north-star amendment A26).

docs/release/acceptance-34.json is what the release gate (scripts/release_acceptance.py)
reads. A row may say pass only with its evidence. This keeps that evidence honest:

  * every file a passed row names is tracked in this repository (a path with a folder must
    match exactly; a bare file name must be some tracked file's name), and every
    ``file::test_name`` it names still defines that test;
  * it names at least one test or check (under tests/, scripts/, .github/workflows/,
    .githooks/ or an installer test script);
  * the CI runs it cites are runs this file knows, and a run that was not on main says so;
  * it records when, and against which commit, it was checked.

A cited file renamed or deleted later turns this red, so a pass cannot outlive what it rests on.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECKLIST = REPO / "docs" / "release" / "acceptance-34.json"
FILE = re.compile(r"(?<![\w/.-])(\.?[\w-]+(?:/[\w.-]+)*\.(?:py|ts|yml|yaml|ps1|js|json|md|iss|toml|html))(?:::(\w+))?")
CHECKS = ("tests/", "scripts/", ".github/workflows/", ".githooks/", "packaging/windows/tests/")
#: The CI runs evidence may cite: id -> (what it was, whether it ran main's code).
RUNS = {
    "40076304478": ("tests on main df2872452", True),
    "40076304491": ("codeql on main df2872452", True),
    "40076304471": ("restore on main df2872452", True),
    "39882247495": ("installer dispatch on 4a4dee190, not main", False),
    # 2026-10-30: runs on 13c3872b9, landing PR #444, whose tree is live c0853a0ab (not main).
    "41094103402": ("tests on PR #444 13c3872b9 (== live), not main", False),
    "41094117529": ("clean-machine on PR #444 13c3872b9 (== live), not main", False),
    "41094103383": ("supply-chain on PR #444 13c3872b9 (== live), not main", False),
}


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
        files = [l.strip() for l in out.splitlines() if l.strip()]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    return [p.relative_to(REPO).as_posix() for p in REPO.rglob("*") if p.is_file() and ".git" not in p.parts]


def _passed():
    return [r for r in json.loads(CHECKLIST.read_text(encoding="utf-8"))["rows"] if r["status"] == "pass"]


def _resolve(name: str, files: list[str], names: dict) -> list[str]:
    name = name.removeprefix("./")
    if "/" in name:
        return [f for f in files if f == name or f.endswith("/" + name)]
    return names.get(name, [])


def test_the_rows_met_on_main_are_recorded():
    # 2026-10-28: 48 rows were checked against main df2872452 and found met; 2026-10-30: 18 more
    # against live c0853a0ab (PR #444). The count only grows.
    assert len(_passed()) >= 48 + 18


def test_every_file_and_test_a_pass_names_exists():
    files = _tracked()
    names: dict = {}
    for f in files:
        names.setdefault(f.rsplit("/", 1)[-1], []).append(f)
    bad = {}
    for r in _passed():
        found = FILE.findall(r["evidence"])
        problems = []
        for name, test in found:
            hits = _resolve(name, files, names)
            if not hits:
                problems.append(name)
            elif test and not any(re.search(r"def %s\(" % re.escape(test), (REPO / h).read_text(encoding="utf-8", errors="replace"))
                                  for h in hits if h.endswith(".py")):
                problems.append(name + "::" + test)
        if problems:
            bad[r["id"]] = problems
    assert bad == {}, "passed rows naming files or tests that are not in the repository"


def test_every_pass_rests_on_a_test_or_check():
    files = _tracked()
    names: dict = {}
    for f in files:
        names.setdefault(f.rsplit("/", 1)[-1], []).append(f)
    for r in _passed():
        hits = [h for name, _t in FILE.findall(r["evidence"]) for h in _resolve(name, files, names)]
        assert any(h.startswith(CHECKS) for h in hits), f"{r['id']}: names no test or check"


def test_every_pass_names_a_known_run_and_when_it_was_checked():
    for r in _passed():
        runs = re.findall(r"\brun (\d{8,})", r["evidence"])
        assert runs, f"{r['id']}: no CI run named"
        for run in runs:
            assert run in RUNS, f"{r['id']}: run {run} is not one this file knows"
            if not RUNS[run][1]:
                assert "off main" in r["evidence"], f"{r['id']}: cites run {run} without saying it was off main"
        assert r.get("checked"), f"{r['id']}: no record of when and against what it was checked"

def test_the_security_suites_run_and_report_even_when_unit_tests_fail():
    """NS-34.2-18: the boundary suites are their own step, run unless the run was cancelled."""
    wf = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    step = wf[wf.index("- name: Run security + egress boundary suites"):]
    step = step[:step.index("\n      - name:")]
    assert "if: ${{ !cancelled() }}" in step
    assert "pytest tests/security tests/test_egress_adversarial.py tests/test_judgment_gate.py" in step