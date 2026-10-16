"""CI-only pytest plugin: after every test's teardown, read what the top-bar chip
would read (needs-you count, approvals, today's updates) from the process's shared
test home, and log every test that changed it. Load with -p leakwatch."""
from __future__ import annotations

import json
import os

import pytest

_last = {"needs_you": 0, "approvals": 0, "updates": 0, "notifs": 0}
_OUT = os.environ.get("LEAKWATCH_OUT", "leakwatch.jsonl")


def _read():
    from agent_friday.services import activity_lane as lane
    from agent_friday import notifications_engine as ne
    n = lane.needs_you()
    u = lane.updates(today_only=True)
    try:
        notifs = len(ne.list_notifications(limit=500))
    except Exception:
        notifs = -1
    return {"needs_you": n["count"], "approvals": n["approvals"], "updates": u["count"],
            "notifs": notifs,
            "kinds": sorted({(o.get("kind") or "") for o in n.get("others", [])}),
            "approval_kinds": sorted({(s.get("kind") or "") for s in n.get("singles", [])})}


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_teardown(item, nextitem):
    yield
    try:
        now = _read()
    except Exception as exc:                                  # the package may not be importable yet
        now = {"error": repr(exc)[:200]}
    keys = ("needs_you", "approvals", "updates", "notifs")
    if any(now.get(k) != _last.get(k) for k in keys):
        rec = {"test": item.nodeid, "before": {k: _last.get(k) for k in keys}, "after": now}
        with open(_OUT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        for k in keys:
            if k in now:
                _last[k] = now[k]
