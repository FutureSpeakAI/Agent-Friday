"""Probe: no earlier test file left the Content publisher job switched on.

The Content publisher ships switched off, and arming a post switches it on
(services/publisher.ensure_publisher_running). tests/conftest.py puts
schedules.json back after every file, so a file that starts after one that
armed posts finds the shared home's schedules.json without an enabled
sch_content_publisher. tests/unit/test_schedules_do_not_leak_between_files.py
runs this after files that arm posts, in one process; on its own it passes.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_friday.services import scheduler as sch


def test_no_earlier_file_left_the_content_publisher_switched_on():
    path = Path(sch.SCHEDULES_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = []
    recs = data.get("schedules", []) if isinstance(data, dict) else data
    on = [r for r in recs
          if isinstance(r, dict) and r.get("id") == "sch_content_publisher"
          and r.get("enabled", True)]
    assert not on, ("an earlier test file left the Content publisher switched on in the "
                    "shared test home (%s): %s" % (path, on))
