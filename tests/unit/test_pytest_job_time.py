"""The pytest matrix job has room for the whole suite on a Windows runner.

The Windows legs took about 45 minutes and grow with every batch; at 60 minutes
the py3.11 leg was cancelled after its last test, during pytest's temp-dir
clean-up, with every test passed, which reads as a red check on every PR.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "tests.yml"


def test_the_pytest_job_has_an_hour_and_a_half():
    wf = WORKFLOW.read_text(encoding="utf-8")
    job = wf[wf.index("  pytest:"):]
    job = job[:job.index("\n    strategy:")]
    m = re.search(r"timeout-minutes: (\d+)", job)
    assert m and int(m.group(1)) >= 90
