"""Speculative prefill (local voice spec §4.2, P2): a stable partial is sent
to the front's cache before the endpoint, in the same request shape the turn
uses, so the turn reuses the common prefix; a partial that later changed is
simply superseded (its one generated token is never spoken), and the reply
comes only from the final transcript.
"""
import json
import threading
import time

from agent_friday.services import voice_front as vf
from agent_friday.services import voice_session as vs

CONTRACT = {"tools": [{"type": "function", "function": {
    "name": "query_calendar", "description": "c",
    "parameters": {"type": "object", "properties": {}}}}]}


class _Resp:
    def __init__(self, body=None, lines=None):
        self.body, self.lines = body, lines

    def raise_for_status(self):
        pass

    def json(self):
        return self.body

    def iter_lines(self, decode_unicode=True):
        yield from self.lines

    def close(self):
        pass


def test_the_prefill_request_has_the_turns_shape_so_the_prefix_is_shared(monkeypatch):
    sent = []

    def fake_post(self, body, stream):
        sent.append(json.loads(json.dumps(body)))
        if stream:
            return _Resp(lines=["data: " + json.dumps({"choices": [{"delta": {
                "content": "Sure."}, "finish_reason": "stop"}]})])
        return _Resp(body={"timings": {"prompt_n": 7}})
    monkeypatch.setattr(vf.FrontSeat, "_post", fake_post)
    seat = vf.FrontSeat()
    seat.model = "qwen3-4b-instruct-2507"
    vol = "== NOW ==\n09:12\n\n== THE USER JUST SAID ==\n"
    seat.prefill_partial("SYS", [{"role": "user", "content": vol + "what's on my"}], CONTRACT)
    out = seat.run_turn("SYS", [{"role": "user", "content": vol + "what's on my calendar"}],
                        CONTRACT)
    spec, turn = sent
    assert spec["max_tokens"] == 1 and spec["cache_prompt"] and spec["id_slot"] == 0
    assert spec["tools"] == turn["tools"] and spec["id_slot"] == turn["id_slot"]
    assert spec["messages"][0] == turn["messages"][0]
    assert turn["messages"][1]["content"].startswith(spec["messages"][1]["content"]), (
        "the speculative text must be a prefix of the turn's, or the cache is wasted")
    assert out == "Sure.", "the reply comes from the final turn, not the speculation"


class _VAD:
    def __init__(self):
        self.n = 0
        self._buf = bytearray()
        self._in_speech = True

    def feed(self, pcm):
        self._buf += pcm
        return None

    def flush(self):
        return None


class _Ear:
    """A streaming ear that changes its mind once, then holds."""

    def __init__(self, script):
        self.script = list(script)

    def feed(self, pcm):
        return self.script.pop(0) if self.script else None

    def finish(self):
        return "what's on my calendar"


def test_only_stable_partials_are_speculated_and_a_change_is_re_speculated():
    speculated = []
    lock = threading.Event()

    def speculate(text):
        speculated.append(text)
        lock.set()
    script = [("what's on", False), ("what's on", True),
              ("what's on my", False), ("what's on my", True)]
    s = vs.VoiceSession(lambda o: True, ear=object(), mouth=object(), vad=_VAD(),
                        generate=lambda *a: "", hooks={"speculate": speculate},
                        stream_ear=_Ear(script))
    try:
        for _ in range(4):
            lock.clear()
            s.feed_audio(b"\x00\x00" * 160)
            time.sleep(0.05)
    finally:
        s.close()
    assert speculated == ["what's on", "what's on my"]
