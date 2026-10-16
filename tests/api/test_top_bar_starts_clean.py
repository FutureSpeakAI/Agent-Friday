"""A test file starts with no leftover approvals, notices or updates on the top bar.

Alone this always passes. Its job is to run AFTER other files in one process
(tests/unit/test_notices_do_not_leak_between_files.py does that): then it fails
if an earlier file left an approval card, a needs-you notice or an update where
the Activity chip counts it (services/activity_lane reads notifications.json
and approvals.json).
"""
from __future__ import annotations

from tests.api.conftest import top_bar_leftovers


def test_no_earlier_file_left_cards_notices_or_updates(client):
    left = [x for x in top_bar_leftovers(client) if "need you" in x or "updates" in x]
    assert not left, "left on the top bar by earlier files: %s" % "; ".join(left)
