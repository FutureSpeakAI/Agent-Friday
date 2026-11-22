"""A test file starts with no leftover Local AI queue job or Activity task from an earlier file.

Alone this always passes. Its job is to run AFTER other files in one process
(tests/unit/test_local_queue_does_not_leak_between_files.py does that): then it
fails if an earlier file left a job in the process-wide seat supervisor, which
the top bar counts as "1 task in Activity; 1 local-queue job" and which made
tests/api/test_workspace_job_card_ui.py error at setup on a full run (b60 gate,
2026-11-22; also CI on #652 #810 #828 #974 #1041).
"""
from __future__ import annotations

from tests.api.conftest import top_bar_leftovers


def test_no_earlier_file_left_a_local_queue_job_or_an_activity_task(client):
    left = [x for x in top_bar_leftovers(client) if "local-queue" in x or "in Activity" in x]
    assert not left, "left on the top bar by earlier files: %s" % "; ".join(left)
