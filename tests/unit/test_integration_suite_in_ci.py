"""The integration suite runs in CI, and the document engine test cannot pass by skipping.

Before this change tests/integration never ran in CI: the office end-to-end test (the
real pinned OfficeCLI making a deck and a spreadsheet, looking at them, fixing one) and
the cross-service flows were only ever run by hand on a machine that had the binary.

Now tests.yml runs ``pytest tests/integration`` on every OS. On Windows the pinned
binary is put in place first by the installer's own code
(packaging/windows/lib/OfficeCli.ps1: download, size and SHA-256 against the pin,
INSTALL.json), and FRIDAY_REQUIRE_OFFICECLI turns the office tests' skip into a failure
there, so a broken install cannot pass as "skipped". The live residency tests stay
opt-in (multi-GB local models). These tests read the workflow and the test file only.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WF = REPO / ".github" / "workflows"


def _steps():
    return yaml.safe_load((WF / "tests.yml").read_text(encoding="utf-8"))["jobs"]["pytest"]["steps"]


def _run(step) -> str:
    return str(step.get("run") or "")


def test_the_integration_suite_runs_in_ci():
    runs = [s for s in _steps() if "pytest tests/integration" in _run(s)]
    assert runs, "tests/integration is not run by tests.yml"
    step = runs[0]
    assert not step.get("continue-on-error")
    env = step.get("env") or {}
    assert "FRIDAY_REQUIRE_OFFICECLI" in env
    assert "Windows" in str(env["FRIDAY_REQUIRE_OFFICECLI"])


def test_ci_installs_the_document_engine_with_the_installers_own_code_first():
    steps = _steps()
    install = [i for i, s in enumerate(steps) if "Install-OfficeCli" in _run(s)]
    integ = [i for i, s in enumerate(steps) if "pytest tests/integration" in _run(s)]
    assert install, "nothing puts the pinned officecli in place before the integration suite"
    assert install[0] < integ[0]
    run = _run(steps[install[0]])
    assert "packaging\\windows\\lib\\OfficeCli.ps1" in run
    assert "throw" in run                       # a failed install fails the job
    assert "Windows" in str(steps[install[0]].get("if", ""))
    assert not steps[install[0]].get("continue-on-error")


def test_the_office_tests_fail_rather_than_skip_when_required():
    src = (REPO / "tests" / "integration" / "test_office_end_to_end.py").read_text(encoding="utf-8")
    assert 'os.environ.get("FRIDAY_REQUIRE_OFFICECLI") == "1"' in src
    assert "not _REQUIRED and not _REAL_BINARY.exists()" in src


def test_the_live_residency_tests_stay_opt_in():
    src = (REPO / "tests" / "integration" / "test_residency_live.py").read_text(encoding="utf-8")
    assert "live_residency" in src
