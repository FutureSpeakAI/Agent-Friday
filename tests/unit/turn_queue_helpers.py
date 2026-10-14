"""Shared fakes for the turn-queue tests: a fake single-slot seat and a way to
run a waiting turn on a thread. No model, no network, no server."""
import threading
import time

from agent_friday.services import turn_queue as tq


def fast(monkeypatch):
    """Tick fast so a test waits milliseconds, not the real 5 seconds."""
    monkeypatch.setattr(tq, "DEFAULT_STATUS_EVERY_S", 0.03)
    monkeypatch.setattr(tq, "_WAIT_POLL_S", 0.01)
    # The idle window is the 1.0.2 one (services/background_gate: Windows'
    # last-input time AND Friday's own marks). A test's "owner away" is Friday's
    # own clock, so the OS clock must not answer for the machine it runs on.
    from agent_friday.services import background_gate as bg
    monkeypatch.setattr(bg, "OS_IDLE_READER", lambda: 1e9)
    # Direct background calls becoming queue jobs (local_scheduler.WRAP_BACKGROUND)
    # is covered in test_local_queue_single_source; these tests are about the turn queue.
    from agent_friday.services import local_scheduler as _ls
    monkeypatch.setattr(_ls, "WRAP_BACKGROUND", [False])
    # The owner has been away all day unless a test says it is here
    # (local_scheduler.mark_activity): background work waits for idle, loaded model or not.
    from agent_friday.services import work_queue as _wq
    monkeypatch.setattr(_wq, "_LAST_ACTIVITY", [time.time() - 86400.0])
    tq.reset_for_tests()


def wait_until(pred, timeout=3.0, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


class Runner:
    """A turn on its own thread: acquire, record when admitted, hold until told
    to let go."""

    def __init__(self, seat, cls, turn_id, log, **kw):
        self.turn_id = turn_id
        self.go = threading.Event()
        self.admission = None
        self.statuses = []
        self._log = log

        def _run():
            self.admission = tq.acquire(seat, cls, turn_id,
                                        on_status=self.statuses.append, **kw)
            self._log.append((turn_id, self.admission.outcome))
            if self.admission.held:
                self.go.wait(5)
                self.admission.release()

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()

    def waiting(self):
        return tq.status_for(self.turn_id) is not None and \
            (tq.status_for(self.turn_id) or {}).get("state") == "waiting"

    def finish(self):
        self.go.set()
        self.thread.join(5)
