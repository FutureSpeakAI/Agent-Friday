"""CI runs the workspace / Laya contract test on every PR (docs audit 2026-10-17, decision 3).

tests/test_laya_workspace_contracts.py holds the "every workspace has a manifest" check, the
gate-class equality check and the Laya coverage ratchet. It sits outside tests/unit and tests/api,
so the pytest job never collected it. The workflow must name it in a step that runs on pull requests.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_the_tests_workflow_runs_the_contract_file():
    wf = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "pull_request" in wf
    runs = [line.strip() for line in wf.splitlines() if line.strip().startswith("run: pytest")]
    assert any("tests/test_laya_workspace_contracts.py" in r for r in runs), runs
    assert (REPO / "tests" / "test_laya_workspace_contracts.py").exists()
