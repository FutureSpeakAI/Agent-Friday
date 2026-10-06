"""Closing a call while its first turn is being assembled cannot start a mind."""
import threading
import time

from agent_friday.services.voice_session import VoiceSession


def test_close_before_turn_publication_does_not_start_generation(monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "_settings", lambda: {})
    entered, release = threading.Event(), threading.Event()
    generated = []
    def clock():
        if threading.current_thread().name == "pending-call-turn" and not entered.is_set():
            entered.set()
            assert release.wait(2), "fixture must release turn construction"
        return time.monotonic()
    class Mouth:
        def synthesize_stream(self, *args):
            return []
    class Vad:
        _buf = bytearray()
    session = VoiceSession(lambda frame: True, ear=object(), mouth=Mouth(), vad=Vad(),
        clock=clock, generate=lambda *args: generated.append(args) or "Late answer.")
    worker = threading.Thread(target=lambda: session.run_turn("Question"), name="pending-call-turn")
    worker.start()
    try:
        assert entered.wait(2)
        assert session._current_turn is None
        session.close()
        release.set()
        worker.join(2)
        assert not generated
        assert not worker.is_alive()
    finally:
        release.set()
        session.close()
        worker.join(2)
        session._speaker.join(2)
