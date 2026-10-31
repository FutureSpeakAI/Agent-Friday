"""A PR labelled red-evidence runs only the test files it changes (CI budget).

A red-evidence PR carries new tests on unchanged code, to show they fail by
assertion before the fix; the fix PR runs the full suites. Runners are shared
by every lane, so the label must skip every other job in every workflow a pull
request starts: the pytest matrix, the app specs, the package build, the
repository guards, CodeQL, and the clean-machine, restore and dependency-review
jobs when their paths match. What runs instead is the changed pytest files and
the changed app specs, nothing else.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
RED = "contains(github.event.pull_request.labels.*.name, 'red-evidence')"
SKIPS = ("!" + RED, "!(github.event_name == 'pull_request' && " + RED + ")")
#: Conditions under which a job never runs on a pull request at all.
NEVER_ON_A_PR = ("${{ inputs.called == true }}",
                 "${{ inputs.called != true && github.event_name != 'pull_request' }}")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    on = wf.get("on") or wf.get(True) or {}
    return on if isinstance(on, dict) else {k: None for k in on}


def _pr_workflows():
    out = [p for p in sorted(WORKFLOWS.glob("*.yml")) if "pull_request" in _on(_load(p))]
    assert {p.name for p in out} >= {"tests.yml", "codeql.yml"}
    return out


@pytest.mark.parametrize("path", _pr_workflows(), ids=lambda p: p.name)
def test_every_full_job_is_skipped_for_a_red_evidence_pr(path):
    for name, job in _load(path)["jobs"].items():
        cond = " ".join(str(job.get("if") or "").split())
        if name.startswith("red-evidence"):
            assert RED in cond and not any(s in cond for s in SKIPS), (path.name, name, cond)
            continue
        if cond in NEVER_ON_A_PR:
            continue
        assert any(s in cond for s in SKIPS), \
            "%s: job %r runs on a red-evidence PR (if: %r)" % (path.name, name, cond)


def test_a_red_evidence_pr_runs_its_changed_pytest_files_and_app_specs_only():
    jobs = _load(WORKFLOWS / "tests.yml")["jobs"]
    red = jobs["red-evidence"]
    listing = next(s for s in red["steps"] if s.get("id") == "changed")["run"]
    assert "'tests/*.py' 'tests/**/*.py'" in listing and "'tests/app/**/*.spec.ts'" in listing
    assert "--diff-filter=AM" in listing and "exit 1" in listing
    run = next(s for s in red["steps"] if s.get("name") == "Run the changed test files")["run"]
    assert run.strip() == "pytest ${{ steps.changed.outputs.py }} -q"
    specs = jobs["red-evidence-specs"]
    assert specs["needs"] == "red-evidence"
    assert "needs.red-evidence.outputs.specs != ''" in specs["if"]
    cmd = " ".join(next(s for s in specs["steps"] if s.get("name") == "Run the changed app specs")["run"].split())
    assert cmd.endswith("${{ needs.red-evidence.outputs.specs }}"), cmd
    assert "settings_walk.spec.ts" not in cmd and "first_run_setup.spec.ts" not in cmd
