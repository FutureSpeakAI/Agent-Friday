"""The app-specs job has room for its specs and the first-run walk after them.

The specs take about 45 minutes on a Windows runner, and every lane adds its own;
the first-run walk runs after them in the same job. At 60 minutes the walk was cut
off mid-run (cancelled), which reads as a red check on every PR.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "tests.yml"


def test_the_app_specs_job_has_ninety_minutes():
    wf = WORKFLOW.read_text(encoding="utf-8")
    job = wf[wf.index("  app-specs:"):]
    job = job[:job.index("\n    steps:")]
    m = re.search(r"timeout-minutes: (\d+)", job)
    assert m and int(m.group(1)) >= 90