"""Late notice writers, run by tests/unit/test_late_notice_writers_do_not_outlive_their_file.py.

Not collected on their own (the name does not start with test_): that test runs
one of these, then the clean-top-bar probe, in a child pytest process, the way
an xdist worker runs one file after another. tests/late_notice_wait.py (loaded
with -p) waits for these threads before the probe's file starts, so whatever
they write lands where the probe's file would inherit it.
"""
from __future__ import annotations

import threading
import time

import agent_friday.notifications_engine as ne


def _titles() -> list:
    try:
        import json
        data = json.loads(ne.NOTIF_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return [str(n.get("title")) for n in items or [] if isinstance(n, dict)]


def test_laya_first_use_notice_is_posted_after_the_turn_returns():
    """A routed turn kicks Laya's one-time notice on its own thread
    (laya_personal._kick_first_notice); here it posts a second after the turn."""
    from agent_friday.services import laya_personal as lp
    real = lp._first_notice
    started = threading.Event()

    def slow_first_notice(profile):
        lp._first_notice = real
        started.set()
        time.sleep(1.0)
        lp.notify("first", "Laya learns from your corrections", lp.NOTICE)

    lp.capture_inline = False
    lp._notice_done[0] = False
    lp._notice_busy[0] = False
    lp._first_notice = slow_first_notice
    lp._kick_first_notice("owner")
    assert started.wait(5), "the first-use notice thread did not start"


def test_a_push_that_read_before_the_put_back_writes_after_it():
    """A push on another thread reads every card, adds its own and writes them
    all back (an FYI shaped like the pre-approved-send ones). This one has read the file's card before the file ends, and
    writes once that card is gone from disk (the put-back ran), or after 5 s."""
    ne.push(title="Synthetic early update", body="", priority="medium", kind="info",
            source="gmail_send", emitter="E43", dedupe_key="late-notice:early")
    assert "Synthetic early update" in _titles()
    real_save = ne._save
    entered = threading.Event()

    def save_after_the_put_back(items, ledger=None):
        ne._save = real_save
        entered.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and "Synthetic early update" in _titles():
            time.sleep(0.02)
        real_save(items, ledger)

    ne._save = save_after_the_put_back
    t = threading.Thread(target=lambda: ne.push(
        title="Synthetic late update", body="", priority="medium", kind="info",
        source="gmail_send", emitter="E43", dedupe_key="late-notice:late"), name="late-notice-racer", daemon=True)
    t.start()
    assert entered.wait(5), "the racing push never reached its write"