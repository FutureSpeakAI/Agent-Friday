"""ensure_all(sync=True) returns only when every preview is built, including one a background worker
holds: a card the worker is still building is not built inline (the queue skips it), so a caller that
returned at once read the card's old title (the prompt-audit gate's media-search failure)."""
import threading
import time

from agent_friday.services import media_previews as mp


def test_a_sync_pass_waits_for_a_card_the_worker_is_still_building(monkeypatch):
    monkeypatch.setattr(mp, "_close_browser", lambda: None)
    done = threading.Event()

    def worker_holds_a_card():
        with mp._LOCK:
            mp._STATE["building"] = "card-held"
            mp._QUEUED.add("card-held")
        time.sleep(0.6)
        with mp._LOCK:
            mp._QUEUED.discard("card-held")
            mp._STATE["building"] = None
        done.set()

    t = threading.Thread(target=worker_holds_a_card)
    t.start()
    time.sleep(0.05)
    mp.ensure_all(cards=[{"id": "card-held"}], sync=True, timeout=5)
    assert done.is_set(), "the sync pass returned while the worker was still building a card"
    t.join()
