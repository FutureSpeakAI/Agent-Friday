"""The streamed local voice turn (voice-system-clean-sheet.md §4.2, §6).

One :class:`VoiceSession` per socket. The route (``routes/voice.py``) does
auth, builds the session with the proven engines from the manifest and a few
route-owned hooks, then feeds it client frames; everything about the turn
lives here so it can be tested without a socket, a model or a GPU.

The turn is overlapped end to end:

* **Ear** — the VAD endpointer opens/closes the utterance. While speech is
  open, every ``chunk_s`` (2 s) window is transcribed speculatively and sent
  as ``partial_transcript``; at close a short utterance (<= ``rejoin_s``) is
  re-transcribed whole (cheap, and the chunk seams are the accuracy risk), a
  long one transcribes only its tail and the pieces are joined.
* **Mind** — the agentic pipeline streams text deltas (``on_text_delta``)
  through :class:`DeltaFilter` (drops channel/tool-call markup) into the
  :class:`ClauseChunker`, so the first clause synthesizes while the model is
  still writing. ``prefill_tokens`` (llama-server ``timings.prompt_n``) goes
  into the receipt.
* **Mouth** — each clause is one job on the session's speaker thread (a GPU
  worker serialises its own jobs; the ``GpuQueue`` orders them against the
  ear). A clause the primary mouth fails on is spoken by Piper — the
  clause-fallback rule — announced once per session as ``error-nonfatal``.
* **Barge** — cancels queued clauses, flags the running one, abandons the
  mind's stream, sends ``interrupted`` and marks the receipt ``aborted``.

Frames sent (§6.2): kept — ``status``, ``input_transcript``, ``text``,
``audio``, ``turn_end``, ``voice_turn_done``, ``interrupted``, ``hb``,
``error``, ``action``, ``context_reach``; new — ``manifest``, ``contract``,
``stage``, ``partial_transcript``, ``error-nonfatal``, ``turn_receipt``.
"""
from __future__ import annotations

import base64
import logging
import queue
import re
import threading
import time
import uuid

log = logging.getLogger("friday.voice_session")

ASR_RATE = 16000
PLAYBACK_RATE = 24000
PLAYBACK_CHUNK_BYTES = 9600

STATES = ("idle", "proving", "ready", "refused", "listening", "hearing",
          "thinking", "acting", "speaking")


# ═══════════════════════════════════════════════════════════════════════════
#  Text plumbing
# ═══════════════════════════════════════════════════════════════════════════

_CLAUSE_END = ".!?;:"
_MARKUP_OPEN = "<|"
_MARKUP_CLOSE = "|>"


class DeltaFilter:
    """Drop channel / tool-call markup from a token stream.

    The gemma4 e-series emits tool calls and thoughts inside the assistant
    text (``<|tool_call>call:…<tool_call|>``, ``<|channel>…<channel|>``).
    Everything from an opening ``<|`` to the matching ``|>`` is withheld; a
    marker split across deltas is handled by holding a trailing ``<``.
    Text BEFORE the first marker (the announcement sentence) passes through,
    which is exactly what the choreography wants audible before the tool.
    """

    def __init__(self):
        self._hold = ""
        self._in_markup = False

    def feed(self, delta: str) -> str:
        s = self._hold + (delta or "")
        self._hold = ""
        out = []
        while s:
            if self._in_markup:
                j = s.find(_MARKUP_CLOSE)
                if j < 0:
                    # keep the last char in case "|" arrives before ">"
                    self._hold = s[-1:] if s.endswith("|") else ""
                    return "".join(out)
                s = s[j + len(_MARKUP_CLOSE):]
                self._in_markup = False
                continue
            i = s.find(_MARKUP_OPEN)
            if i < 0:
                if s.endswith("<"):
                    out.append(s[:-1])
                    self._hold = "<"
                else:
                    out.append(s)
                return "".join(out)
            out.append(s[:i])
            s = s[i + len(_MARKUP_OPEN):]
            self._in_markup = True
        return "".join(out)

    def flush(self) -> str:
        h = self._hold
        self._hold = ""
        return "" if self._in_markup else h


class ClauseChunker:
    """Cut a token stream into speakable clauses (§4.2 mouth contract).

    Boundaries: ``. ! ? ; :`` (followed by whitespace or end), ``,`` after at
    least ``comma_words`` words, and a hard cut at ``hard_words`` words. Never
    yields an empty clause; ``flush()`` returns the remainder.
    """

    def __init__(self, comma_words: int = 6, hard_words: int = 12):
        self.comma_words = comma_words
        self.hard_words = hard_words
        self._buf = ""

    @staticmethod
    def _words(s: str) -> int:
        return len(s.split())

    def feed(self, delta: str) -> list[str]:
        self._buf += delta or ""
        out = []
        while True:
            cut = self._find_cut(self._buf)
            if cut is None:
                break
            clause, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            if clause:
                out.append(clause)
        return out

    def _find_cut(self, s: str):
        # A boundary is trusted only once the character AFTER it has arrived,
        # so "3.5", "10:30" and "e.g." do not cut mid-token; flush() takes the
        # remainder at the end of the stream.
        n = len(s)
        for i, ch in enumerate(s):
            nxt = s[i + 1] if i + 1 < n else None
            if ch in _CLAUSE_END:
                if nxt is None:
                    break
                if nxt.isspace():
                    return i + 1
                if ch in ".:" and nxt.isdigit():
                    continue                      # 3.5 / 10:30
                if ch == ".":
                    continue                      # e.g. / U.S. — wait for a space
                return i + 1
            if ch == ",":
                if nxt is not None and nxt.isspace() \
                        and self._words(s[:i]) >= self.comma_words:
                    return i + 1
        # Hard cut at hard_words, on a whitespace boundary, only when more
        # words have already arrived (so a 12-word clause still ends at its
        # punctuation rather than one word early).
        if self._words(s) > self.hard_words:
            k = 0
            for m in re.finditer(r"\S+", s):
                k += 1
                if k == self.hard_words:
                    return m.end()
        return None

    def flush(self) -> str | None:
        rest, self._buf = self._buf.strip(), ""
        return rest or None


# ═══════════════════════════════════════════════════════════════════════════
#  The session
# ═══════════════════════════════════════════════════════════════════════════

class VoiceSession:
    """See the module docstring.

    Parameters (all injectable; the route supplies the real ones):

    * ``send(obj) -> bool`` — one JSON frame to the client.
    * ``ear`` / ``mouth`` — engines (voice_workers ABCs); ``fallback_mouth``
      is Piper (or None) for the clause-fallback rule.
    * ``vad`` — a VADEndpointer-like object with ``feed``/``flush``/``_buf``.
    * ``generate(user_text, on_text_delta, cancel) -> str`` — the mind. The
      route binds ``_generate_agent`` with the prefix-stable system prompt.
    * ``hooks`` — optional callables: ``persist(user, agent, conversation_id)``,
      ``distill(turn_log)``, ``actions(user_text) -> list``,
      ``receipt() -> TurnReceipt | None``, ``timings() -> dict | None``.
    * ``manifest_snapshot`` — the dict sent as the ``manifest`` frame.
    * ``gpu_queue`` — a GpuQueue (or None to run every job on the speaker
      thread directly).
    """

    def __init__(self, send, *, ear, mouth, vad, generate, fallback_mouth=None,
                 hooks=None, manifest_snapshot=None, contract=None,
                 gpu_queue=None, session_id=None, chunk_s: float = 2.0,
                 rejoin_s: float = 8.0, clock=time.monotonic):
        self.send = send
        self.ear = ear
        self.mouth = mouth
        self.fallback_mouth = fallback_mouth
        self.vad = vad
        self.generate = generate
        self.hooks = dict(hooks or {})
        self.manifest_snapshot = manifest_snapshot
        self.contract = contract or {}
        self.gpu_queue = gpu_queue
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.chunk_s = float(chunk_s)
        self.rejoin_s = float(rejoin_s)
        self._clock = clock
        self.state = "idle"
        self.done = threading.Event()
        self.turn_log: list = []
        self.conversation_id = None
        self._turn_lock = threading.Lock()
        self._turn_seq = 0
        self._current_turn = None       # dict per turn
        self._notified: set = set()
        self._fallback_announced = False
        self._speak_q: "queue.Queue" = queue.Queue()
        self._speaker = threading.Thread(target=self._speak_loop, daemon=True,
                                         name=f"voice-speaker-{self.session_id}")
        self._speaker.start()
        # chunked ear state
        self._chunk_bytes = int(self.chunk_s * ASR_RATE * 2)
        self._chunk_pos = 0
        self._partials: list = []
        self._hearing = False
        self.stage_state = {"ear": "idle", "mind": "idle", "mouth": "idle"}
        self.last_receipt: dict | None = None

    # ── frames ───────────────────────────────────────────────────────────

    def _status(self, text: str) -> None:
        self.send({"type": "status", "text": text})

    def _set_state(self, state: str) -> None:
        self.state = state

    def stage(self, stage: str, state: str, detail: str = "") -> None:
        self.stage_state[stage] = state
        self.send({"type": "stage", "stage": stage, "state": state, "detail": detail})

    def notify_once(self, code: str, message: str, action=None) -> None:
        """§6.2 ``error-nonfatal``: one notice per code per session."""
        if code in self._notified:
            return
        self._notified.add(code)
        self.send({"type": "error-nonfatal", "code": code, "message": message,
                   "action": action})

    def start(self) -> None:
        """Session-start frames: manifest, contract (+ context_reach alias)."""
        if self.manifest_snapshot is not None:
            self.send({"type": "manifest", **self.manifest_snapshot})
        c = dict(self.contract)
        self.send({"type": "contract", "engine": "local", **c})
        self.send({"type": "context_reach", "engine": "local",
                   "tool_capable": bool(c.get("tools")),
                   "tools": len(c.get("tools") or []) or None,
                   "knowledge_graph": bool(c.get("knowledge_graph")),
                   "memory": bool(c.get("memory")),
                   "full_context": bool(c.get("knowledge_graph")) and bool(c.get("memory")),
                   "notice": ""})
        for k in ("ear", "mind", "mouth"):
            self.stage(k, "idle")
        self._set_state("listening")
        self._status("live")

    # ── client → server ──────────────────────────────────────────────────

    def handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "audio" and msg.get("data"):
            try:
                pcm = base64.b64decode(msg["data"])
            except Exception:
                return
            self.feed_audio(pcm)
        elif t == "barge":
            self.barge()
        elif t == "conversation":
            self.conversation_id = (msg.get("id") or "").strip() or None
        elif t == "text" and msg.get("text"):
            self.spawn_turn(str(msg["text"]))
        elif t == "end":
            utt = self.vad.flush()
            if utt:
                text, ms = self._final_transcript(utt)
                if text:
                    self.run_turn(text, audio_ms=ms)
            self.done.set()

    # ── ear ──────────────────────────────────────────────────────────────

    def _transcribe(self, pcm: bytes) -> str:
        if self.gpu_queue is not None and getattr(self.ear, "device", "") == "cuda":
            d = self.gpu_queue.submit(("ear", self.session_id),
                                      lambda cancel: self.ear.transcribe(pcm))
            d.wait()
            if d.error:
                raise d.error
            return d.result or ""
        return self.ear.transcribe(pcm)

    def feed_audio(self, pcm: bytes) -> None:
        utterance = self.vad.feed(pcm)
        buf = getattr(self.vad, "_buf", None)
        in_speech = bool(getattr(self.vad, "_in_speech", False))
        if utterance is None and in_speech and buf is not None:
            if not self._hearing:
                self._hearing = True
                self._chunk_pos = 0
                self._partials = []
                self._set_state("hearing")
                self.stage("ear", "busy", "hearing")
            # Speculative chunked transcription while speech continues.
            if len(buf) - self._chunk_pos >= self._chunk_bytes:
                window = bytes(buf[self._chunk_pos:self._chunk_pos + self._chunk_bytes])
                self._chunk_pos += self._chunk_bytes
                try:
                    part = self._transcribe(window)
                except Exception as e:
                    log.warning("partial transcription failed: %s", e)
                    part = ""
                if part:
                    self._partials.append(part)
                    self.send({"type": "partial_transcript",
                               "text": " ".join(self._partials)})
            return
        if utterance:
            text, ms = self._final_transcript(utterance)
            self._hearing = False
            if text:
                self.spawn_turn(text, audio_ms=ms)
            else:
                r = self._new_receipt()
                if r is not None:
                    r.done(outcome="silent", code="local_voice_empty_transcript",
                           detail="VAD endpointed an utterance; ASR returned no text")
            self.stage("ear", "idle")

    def _final_transcript(self, utterance: bytes):
        """§4.2: re-do a short utterance whole; join the tail of a long one."""
        ms = (len(utterance) / 2) / ASR_RATE * 1000.0
        t0 = self._clock()
        try:
            if ms <= self.rejoin_s * 1000.0 or not self._partials:
                text = self._transcribe(utterance)
            else:
                tail = utterance[self._chunk_pos:]
                tail_text = self._transcribe(tail) if len(tail) > ASR_RATE * 2 * 0.15 else ""
                text = " ".join([*self._partials, tail_text]).strip()
        except Exception as e:
            log.error("ASR failed: %s: %s", type(e).__name__, e)
            r = self._new_receipt()
            if r is not None:
                r.done(outcome="error", code="local_voice_asr_failed",
                       detail=f"{type(e).__name__}: {e}")
            text = ""
        self._chunk_pos = 0
        self._partials = []
        self.last_ear_ms = (self._clock() - t0) * 1000.0
        return (text or "").strip(), ms

    # ── mind + mouth ─────────────────────────────────────────────────────

    def _new_receipt(self):
        fn = self.hooks.get("receipt")
        if fn is None:
            return None
        try:
            return fn()
        except Exception:
            return None

    def spawn_turn(self, user_text: str, audio_ms: float | None = None) -> None:
        threading.Thread(target=self.run_turn, args=(user_text, audio_ms),
                         daemon=True).start()

    def barge(self) -> None:
        t = self._current_turn
        if t is not None:
            t["cancel"].set()
            if self.gpu_queue is not None:
                self.gpu_queue.cancel_turn(t["id"])
            # Drop queued clauses for this turn. Each drained item is marked
            # done, or run_turn's join() on the queue would wait forever.
            try:
                while True:
                    self._speak_q.get_nowait()
                    self._speak_q.task_done()
            except queue.Empty:
                pass
            t["aborted"] = True
        self.send({"type": "interrupted"})
        self._status("listening")
        self._set_state("listening")

    def run_turn(self, user_text: str, audio_ms: float | None = None) -> None:
        user_text = (user_text or "").strip()
        if not user_text or self.done.is_set():
            return
        with self._turn_lock:
            self._turn_seq += 1
            turn = {"id": f"{self.session_id}-{self._turn_seq}", "cancel": threading.Event(),
                    "aborted": False, "clauses": 0, "first_clause_ms": None,
                    "first_audio_ms": None, "t0": self._clock(), "spoken": [],
                    "audio_bytes": 0, "fallback_used": False}
            self._current_turn = turn
            if self.gpu_queue is not None:
                self.gpu_queue.clear_turn(turn["id"])
            receipt = self._new_receipt()
            if receipt is not None:
                receipt.mark("vad_open")
                receipt.mark("vad_close")
                receipt.mark("input_complete", audio_ms=audio_ms)
            self.send({"type": "input_transcript", "text": user_text})
            self._status("thinking")
            self._set_state("thinking")
            self.stage("mind", "busy", "thinking")
            chunker = ClauseChunker()
            dfilter = DeltaFilter()
            streamed = {"chars": 0, "first": False}

            def on_delta(piece: str):
                if turn["cancel"].is_set():
                    return
                if not streamed["first"]:
                    streamed["first"] = True
                    if receipt is not None:
                        receipt.mark("first_brain_token")
                    self._set_state("speaking")
                clean = dfilter.feed(piece)
                if not clean:
                    return
                streamed["chars"] += len(clean)
                for clause in chunker.feed(clean):
                    self._enqueue_clause(turn, clause, receipt)

            try:
                reply = self.generate(user_text, on_delta, turn["cancel"]) or ""
                if receipt is not None and not streamed["first"]:
                    receipt.mark("first_brain_token")
            except Exception as e:
                reply = f"Sorry, I hit an error thinking that through: {e}"
                log.error("brain call failed: %s: %s", type(e).__name__, e)
                if receipt is not None:
                    receipt.set(brain_failed=True)
            reply = (reply or "").strip()
            tail = dfilter.flush()
            if tail:
                for clause in chunker.feed(tail):
                    self._enqueue_clause(turn, clause, receipt)
            if streamed["chars"] == 0 and reply and not turn["cancel"].is_set():
                # Non-streaming leg: speak the whole reply through the chunker.
                for clause in chunker.feed(reply):
                    self._enqueue_clause(turn, clause, receipt)
            rest = chunker.flush()
            if rest and not turn["cancel"].is_set():
                self._enqueue_clause(turn, rest, receipt)
            self.stage("mind", "idle")
            if reply:
                self.send({"type": "text", "text": reply})
                if receipt is not None:
                    receipt.count_text_out(len(reply))
            # Wait for the mouth to drain this turn's clauses.
            self._speak_q.join()
            prefill = None
            try:
                tf = self.hooks.get("timings")
                tm = tf() if tf else None
                prefill = (tm or {}).get("prompt_n")
            except Exception:
                prefill = None
            if receipt is not None:
                try:
                    receipt.set(prefill_tokens=prefill, clauses=turn["clauses"],
                                first_clause_ms=turn["first_clause_ms"])
                except Exception:
                    pass
                receipt.done(outcome="aborted" if turn["aborted"] else None,
                             detail="barge" if turn["aborted"] else "")
            rec = {"type": "turn_receipt", "turn_id": turn["id"],
                   "prefill_tokens": prefill, "clauses": turn["clauses"],
                   "first_clause_ms": turn["first_clause_ms"],
                   "first_audio_ms": turn["first_audio_ms"],
                   "audio_bytes_out": turn["audio_bytes"],
                   "outcome": "aborted" if turn["aborted"] else
                   ("served" if turn["audio_bytes"] else "silent"),
                   "ear_ms": int(getattr(self, "last_ear_ms", 0) or 0),
                   "engines": {"ear": getattr(self.ear, "name", "?"),
                               "mouth": getattr(self.mouth, "name", "?")
                               + (" (+piper fallback)" if turn["fallback_used"] else "")},
                   "gpu_queue_depth": self.gpu_queue.depth() if self.gpu_queue else 0}
            self.last_receipt = rec
            self.send(rec)
            self.send({"type": "turn_end"})
            self.send({"type": "voice_turn_done", "user_text": user_text,
                       "agent_text": reply})
            self.turn_log.append((user_text, reply))
            try:
                p = self.hooks.get("persist")
                if p:
                    p(user_text, reply, self.conversation_id)
            except Exception:
                pass
            try:
                a = self.hooks.get("actions")
                acts = a(user_text) if a else None
                if acts:
                    self.send({"type": "action", "actions": acts})
            except Exception:
                pass
            if not turn["aborted"]:
                self._status("listening")
                self._set_state("listening")
            self._current_turn = None

    def _enqueue_clause(self, turn: dict, clause: str, receipt) -> None:
        if turn["cancel"].is_set():
            return
        if turn["first_clause_ms"] is None:
            turn["first_clause_ms"] = int((self._clock() - turn["t0"]) * 1000)
        turn["clauses"] += 1
        self._speak_q.put((turn, clause, receipt))

    def _synth(self, engine, clause: str, cancel: threading.Event):
        if self.gpu_queue is not None and getattr(engine, "device", "") == "cuda":
            out = []
            d = self.gpu_queue.submit(self._current_turn["id"] if self._current_turn else None,
                                      lambda c: out.extend(engine.synthesize_stream(clause, c)) or True)
            d.wait()
            if d.error:
                raise d.error
            return out
        return list(engine.synthesize_stream(clause, cancel))

    def _speak_loop(self) -> None:
        while not self.done.is_set():
            try:
                turn, clause, receipt = self._speak_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if turn["cancel"].is_set():
                    continue
                self.stage("mouth", "busy", clause[:40])
                chunks = None
                try:
                    chunks = self._synth(self.mouth, clause, turn["cancel"])
                except Exception as e:
                    log.error("mouth failed on a clause (%s): %s", type(e).__name__, e)
                    if receipt is not None:
                        receipt.set(tts_error=True)
                    if self.fallback_mouth is not None and self.fallback_mouth is not self.mouth:
                        turn["fallback_used"] = True
                        self.notify_once(
                            "local_voice_clause_fallback",
                            f"Friday's {getattr(self.mouth, 'name', 'voice')} engine failed on "
                            f"a phrase; Piper is speaking the phrases it cannot. "
                            f"({type(e).__name__})")
                        try:
                            chunks = self._synth(self.fallback_mouth, clause, turn["cancel"])
                        except Exception as e2:
                            log.error("fallback mouth failed too: %s", e2)
                            chunks = None
                if not chunks:
                    continue
                pcm = b"".join(chunks)
                for off in range(0, len(pcm), PLAYBACK_CHUNK_BYTES):
                    if turn["cancel"].is_set() or self.done.is_set():
                        break
                    piece = pcm[off:off + PLAYBACK_CHUNK_BYTES]
                    self.send({"type": "audio",
                               "data": base64.b64encode(piece).decode("ascii")})
                    if turn["first_audio_ms"] is None:
                        turn["first_audio_ms"] = int((self._clock() - turn["t0"]) * 1000)
                    turn["audio_bytes"] += len(piece)
                    turn["spoken"].append(clause)
                    if receipt is not None:
                        receipt.mark("first_audio_out")
                        receipt.count_audio_out(len(piece))
            finally:
                self.stage("mouth", "idle")
                self._speak_q.task_done()

    # ── lifecycle ────────────────────────────────────────────────────────

    def close(self) -> None:
        self.done.set()
        t = self._current_turn
        if t is not None:
            t["cancel"].set()
        if self.turn_log:
            try:
                d = self.hooks.get("distill")
                if d:
                    d(self.turn_log)
            except Exception:
                pass
