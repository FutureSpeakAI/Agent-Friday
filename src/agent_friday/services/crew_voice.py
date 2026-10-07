"""One playback floor for a Gemini host and attributed Crew reports.

The room grants speech, not task authority. Interrupting this floor invalidates
audio and synthesis results without touching the worker that produced them.
Client playback receipts distinguish rendered audio from generated text; without
word timestamps an interrupted utterance has no fabricated heard-word cutoff.
"""
from __future__ import annotations

import base64
import copy
import logging
import threading
import time
import uuid
from collections import deque

log = logging.getLogger(__name__)
RATE = 24000
MAX_TEXT = 5000
MAX_AUDIO_BYTES = 4 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
MAX_SAMPLES = RATE * 300
MAX_PENDING = 8
ACK_TIMEOUT = 20.0
SYNTH_TIMEOUT = 130.0
_UNSET = object()


def _room(conversation_id):
    from agent_friday.services import crew_runtime
    return crew_runtime.voice_room(conversation_id)


def _synthesize(profile, text, task_id):
    from agent_friday.services import cloud_voice
    voice = profile.get("voice") or {}
    if not all(isinstance(voice.get(k), str) and voice[k].strip()
               for k in ("provider", "model", "voice_id")):
        raise ValueError("This Crew agent needs a complete voice binding.")
    return cloud_voice.synthesize(
        text, provider=voice.get("provider"), model=voice.get("model"),
        voice_id=voice.get("voice_id"),
        session_ctx={"surface": "crew-voice", "crew_agent_id": profile["id"],
                     "task_id": task_id})


class CrewVoiceSession:
    """Thread-safe, bounded speech queue; all sends are serialized with aborts.

    ``room`` reloads membership/profile revisions before every synthesis and
    again before its output leaves. ``spoken`` receives an attributed report
    only after the client acknowledges its explicit end marker.
    """

    def __init__(self, send, conversation_id, *, room=None, synthesize=None,
                 spoken=None, receipt=None, clock=time.monotonic, session_id=None,
                 launch=None, off_record=None, off_record_generation=None):
        self.send = send
        self.conversation_id = str(conversation_id or "")
        self.session_id = session_id or uuid.uuid4().hex
        self.epoch = 0
        self._room = room or _room
        self._synthesize = synthesize or _synthesize
        self._spoken = spoken
        self._receipt = receipt
        self._off_record = off_record or (lambda: False)
        self._off_record_generation = off_record_generation or (lambda: None)
        self._clock = clock
        self._launch = launch or self._launch_job
        self._lock = threading.RLock()
        self._pending = deque()
        self._active = None
        self._worker_busy = False
        self._closed = False
        self._user_until = 0.0
        self._native_open = False
        self._native_suppressed = False

    def _base(self, utterance=None):
        out = {"session_id": self.session_id,
               "conversation_id": self.conversation_id, "epoch": self.epoch}
        if utterance:
            out.update({k: utterance[k] for k in
                        ("utterance_id", "speaker_id", "epoch", "task_id")})
        return out

    def _emit(self, kind, utterance=None, **fields):
        if self._closed:
            return False
        try:
            return self.send({"type": kind, **self._base(utterance), **fields}) is not False
        except Exception:
            return False

    def start(self):
        with self._lock:
            self._emit("crew_session", max_audio_bytes=MAX_AUDIO_BYTES,
                       max_text_chars=MAX_TEXT, sample_rate=RATE)

    def _profile(self, profile, *, project_id=_UNSET):
        snapshot = self._room(self.conversation_id)
        if project_id is not _UNSET and (snapshot or {}).get("project_id") != project_id:
            raise ValueError("The Crew room project changed; request a new report.")
        for member in (snapshot or {}).get("members", []):
            if (isinstance(member, dict) and member.get("id") == profile.get("id")
                    and member.get("status") == "active"
                    and member.get("revision") == profile.get("revision")):
                return copy.deepcopy(member)
        raise ValueError("Crew membership or the speaker profile changed; request a new report.")

    def _privacy_origin(self):
        generation = self._off_record_generation()
        return {"off_record_generation": generation, "off_record": bool(self._off_record())}

    def _origin_current(self, item):
        if item["off_record"] and not self._off_record():
            return False
        generation = item["off_record_generation"]
        return generation is None or generation == self._off_record_generation()

    def deliver(self, profile, text, *, task_id=None, off_record=None,
                off_record_generation=None):
        if not isinstance(profile, dict) or not str(text or "").strip():
            return False
        text = str(text).strip()
        if len(text) > MAX_TEXT:
            with self._lock:
                self._emit("crew_speech_error", code="crew_text_limit", task_id=str(task_id or ""),
                           message="This reply is too long to speak. Its full text is available in the chat.")
            return False
        if ((off_record is not None and type(off_record) is not bool)
                or (off_record_generation is not None and type(off_record_generation) is not int)):
            return False
        origin = self._privacy_origin()
        if off_record_generation is not None:
            origin["off_record_generation"] = off_record_generation
        origin["off_record"] = origin["off_record"] or bool(off_record)
        if not self._origin_current(origin):
            return False
        try:
            profile = self._profile(profile)
            snapshot = self._room(self.conversation_id)
            if not snapshot:
                return False
        except Exception:
            return False
        with self._lock:
            if self._closed:
                return False
            if len(self._pending) >= MAX_PENDING:
                self._emit("crew_speech_error", code="crew_queue_full", task_id=str(task_id or ""),
                           message="The speaking queue is full. This reply is available in the chat.")
                return False
            self._pending.append({"profile": profile, "text": text,
                                  "task_id": str(task_id or ""), "epoch": self.epoch,
                                  "project_id": snapshot.get("project_id"), **origin})
        self.tick()
        return True

    def _new(self, speaker_id, *, task_id="", text="", profile=None, privacy_origin=None):
        now = self._clock()
        snapshot = self._room(self.conversation_id)
        if not snapshot:
            raise ValueError("This Crew room is no longer available.")
        origin = privacy_origin if privacy_origin is not None else self._privacy_origin()
        return {"utterance_id": uuid.uuid4().hex, "speaker_id": speaker_id,
                "task_id": task_id, "epoch": self.epoch, "text": text,
                "profile": profile, "seq": 0, "samples": 0, "bytes": 0,
                "sealed": False, "started": False, "played_samples": 0,
                "encoding": "pcm_s16le", "deadline": now + ACK_TIMEOUT,
                "created": now, "cancel": threading.Event(),
                "off_record": origin["off_record"],
                "off_record_generation": origin["off_record_generation"],
                "project_id": snapshot.get("project_id"),
                "shared_with": {p["id"]: p["revision"] for p in snapshot.get("members", [])
                                if isinstance(p, dict) and p.get("status") == "active"}}

    @property
    def external_active(self):
        with self._lock:
            return bool(self._active and self._active["speaker_id"] != "friday")

    @property
    def busy(self):
        with self._lock:
            return self._active is not None or self._native_open

    def host_begin(self, *, model="", voice_id="", label="Friday", expected_epoch=None):
        with self._lock:
            if (self._closed or self._native_suppressed
                    or (expected_epoch is not None and expected_epoch != self.epoch)):
                return False
            self._native_open = True
            if self._active:
                if self._active["speaker_id"] != "friday" or self._active["sealed"]:
                    self._native_suppressed = True
                    return False
                return True
            try:
                self._active = self._new("friday")
            except Exception:
                self._native_suppressed = True
                self._emit("crew_speech_error", code="crew_room_unavailable",
                           message="This Crew room is no longer available. Reopen its conversation.")
                return False
            self._active["deadline"] = self._clock() + SYNTH_TIMEOUT
            self._active.update(provider="google-gemini", model=model,
                                voice_id=voice_id, label=label)
            self._emit("crew_speech_start", self._active, label=label,
                       provider="google-gemini", model=model, voice_id=voice_id,
                       encoding="pcm_s16le", sample_rate=RATE, text="")
            return True

    def host_allowed(self):
        with self._lock:
            return not (self._closed or self._native_suppressed or self.external_active)

    def host_text(self, text, **host):
        with self._lock:
            if not self.host_begin(**host):
                return False
            item = self._active
            delta = str(text)[:max(0, MAX_TEXT - len(item["text"]))]
            item["text"] += delta
            if delta:
                self._emit("crew_speech_text", item, text=delta, append=True)
            return True

    def host_audio(self, data, **host):
        with self._lock:
            if not self.host_begin(**host):
                return False
            item = self._active
            if (len(data) % 2 or item["bytes"] + len(data) > MAX_AUDIO_BYTES
                    or item["samples"] + len(data) // 2 > MAX_SAMPLES):
                self._fail(item, "crew_audio_limit", "The spoken response exceeded its audio limit.")
                self._native_suppressed = True
                return False
            item["samples"] += len(data) // 2
            item["bytes"] += len(data)
            item["deadline"] = self._clock() + ACK_TIMEOUT
            self._audio(item, data)
            return True

    def _audio(self, item, data):
        for offset in range(0, len(data), CHUNK_BYTES):
            self._emit("crew_audio", item, seq=item["seq"],
                       data=base64.b64encode(data[offset:offset + CHUNK_BYTES]).decode("ascii"))
            item["seq"] += 1

    def host_end(self):
        with self._lock:
            self._native_open = False
            if self._native_suppressed:
                self._native_suppressed = False
                return False
            item = self._active
            if not item or item["speaker_id"] != "friday":
                return False
            item["sealed"] = True
            item["deadline"] = self._clock() + ACK_TIMEOUT
            self._emit("crew_speech_end", item, total_samples=item["samples"])
            return True

    def note_user(self):
        with self._lock:
            self._user_until = self._clock() + 0.7

    def interrupt(self, reason="user"):
        with self._lock:
            self.epoch += 1
            self._pending.clear()
            self._native_suppressed = self._native_open
            if self._active:
                self._active["cancel"].set()
                self._record(self._active, "interrupted")
            self._active = None
            self._user_until = self._clock() + 0.7
            self._emit("crew_interrupted", reason=reason)

    def _fail(self, item, code, message):
        item["cancel"].set()
        self._record(item, "failed")
        self._emit("crew_speech_error", item, code=code, message=message)
        if self._active is item:
            self._active = None

    def _private(self, item):
        private = item["off_record"] or bool(self._off_record())
        current_generation = self._off_record_generation()
        return (private
                or (current_generation is not None
                    and current_generation != item["off_record_generation"]))

    def _record(self, item, status):
        if item.get("recorded") or not self._receipt:
            return
        item["recorded"] = True
        try:
            self._receipt({**self._base(item), "status": status,
                           "text": item["text"], "played_samples": item["played_samples"],
                           "profile": item["profile"], "project_id": item["project_id"],
                           "shared_with": item["shared_with"],
                           "provider": item.get("provider", ""),
                           "model": item.get("model", ""),
                           "voice_id": item.get("voice_id", ""),
                           "label": item.get("label", ""),
                           "off_record": self._private(item),
                           "off_record_generation": item["off_record_generation"]})
        except Exception:
            log.warning("Crew playback receipt could not be recorded")

    def acknowledge(self, frame):
        report = None
        with self._lock:
            item = self._active
            if (not item or isinstance(frame.get("epoch"), bool)
                    or frame.get("session_id") != self.session_id
                    or frame.get("epoch") != item["epoch"]
                    or frame.get("utterance_id") != item["utterance_id"]):
                return False
            n = frame.get("played_samples", 0)
            if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_SAMPLES:
                return False
            if n < item["played_samples"]:
                return False
            if item["encoding"] == "pcm_s16le" and n > item["samples"]:
                return False
            status = frame.get("status")
            if status not in ("started", "progress", "finished", "interrupted", "failed"):
                return False
            if status == "finished" and not item["sealed"]:
                return False
            if (status == "finished" and item["encoding"] == "pcm_s16le"
                    and n != item["samples"]):
                return False
            if status == "finished" and item["bytes"] and not n:
                return False
            if status == "finished" and item["encoding"] != "pcm_s16le":
                total = frame.get("total_samples")
                if isinstance(total, bool) or not isinstance(total, int) or total != n:
                    return False
            item["played_samples"] = n
            item["started"] = item["started"] or status in ("started", "progress", "finished")
            item["deadline"] = self._clock() + ACK_TIMEOUT
            if status in ("finished", "interrupted", "failed"):
                if item["speaker_id"] == "friday" and self._native_open:
                    self._native_suppressed = True
                self._active = None
                item["cancel"].set()
                self._record(item, status)
                if status == "finished" and item["speaker_id"] != "friday":
                    report = {"speaker_id": item["speaker_id"], "task_id": item["task_id"],
                              "text": item["text"], "profile": item["profile"],
                              "played_samples": n, "utterance_id": item["utterance_id"],
                              "conversation_id": self.conversation_id,
                              "off_record": self._private(item),
                              "off_record_generation": item["off_record_generation"]}
        if report and self._spoken:
            try:
                self._spoken(report)
            except Exception:
                log.warning("Crew spoken report could not be handed to its host")
        self.tick()
        return True

    def tick(self):
        job = None
        with self._lock:
            if self._closed:
                return
            now = self._clock()
            if self._active and now > self._active["deadline"]:
                item = self._active
                self._fail(item, "crew_playback_timeout", "Speech stopped because playback did not complete.")
                if item["speaker_id"] == "friday":
                    self._native_suppressed = self._native_open
            if (self._active or self._native_open or self._worker_busy
                    or now < self._user_until or not self._pending):
                return
            request = self._pending.popleft()
            if request["epoch"] != self.epoch:
                return
            if not self._origin_current(request):
                return
            profile = request["profile"]
            try:
                self._profile(profile, project_id=request["project_id"])
                job = self._new(profile["id"], task_id=request["task_id"],
                                text=request["text"], profile=profile, privacy_origin=request)
                if job["project_id"] != request["project_id"]:
                    raise ValueError("Crew room project changed")
            except Exception:
                self._emit("crew_speech_error", code="crew_room_unavailable",
                           message="The Crew room changed before this report could speak.")
                return
            job["deadline"] = now + SYNTH_TIMEOUT
            self._active = job
            self._worker_busy = True
        self._launch(self._produce, job)

    @staticmethod
    def _launch_job(fn, job):
        threading.Thread(target=fn, args=(job,), daemon=True, name="crew-speech").start()

    def _produce(self, item):
        try:
            profile = self._profile(item["profile"], project_id=item["project_id"])
            if item["cancel"].is_set():
                return
            if self._expire_private(item):
                return
            result = self._synthesize(profile, item["text"], item["task_id"])
            profile = self._profile(profile, project_id=item["project_id"])
            if result.mime not in ("audio/mpeg", "audio/wav"):
                raise ValueError("The voice provider returned an unsupported audio format.")
            if not result.audio or len(result.audio) > MAX_AUDIO_BYTES:
                raise ValueError("The voice provider returned empty or oversized audio.")
            with self._lock:
                if (self._closed or self._active is not item or item["cancel"].is_set()
                        or item["epoch"] != self.epoch):
                    return
                if self._expire_private(item):
                    return
                item["encoding"] = result.mime
                item["bytes"] = len(result.audio)
                item["deadline"] = self._clock() + ACK_TIMEOUT
                label = (profile.get("caption") or {}).get("label") or profile["name"]
                item.update(provider=result.provider, model=result.model, label=label,
                            voice_id=getattr(result, "voice_id", "") or profile["voice"]["voice_id"])
                self._emit("crew_speech_start", item, label=label,
                           provider=result.provider, model=result.model,
                           voice_id=item["voice_id"],
                           encoding=result.mime, sample_rate=RATE, text=item["text"])
                self._audio(item, result.audio)
                item["sealed"] = True
                self._emit("crew_speech_end", item, total_samples=None)
        except Exception as exc:
            with self._lock:
                if self._active is item and not item["cancel"].is_set():
                    # Provider exception details can contain request data; show
                    # only its public message or a fixed recovery sentence.
                    message = getattr(exc, "user_message", None)
                    self._fail(item, getattr(exc, "code", "crew_synthesis_failed"),
                               str(message or "This agent's voice could not speak. Its written result is available."))
        finally:
            with self._lock:
                self._worker_busy = False
            self.tick()

    def _expire_private(self, item):
        if self._origin_current(item):
            return False
        with self._lock:
            if self._active is item and not item["cancel"].is_set():
                self._fail(item, "crew_private_session_ended",
                           "This report ended with its off-record session.")
        return True

    def retarget(self, conversation_id):
        with self._lock:
            self.interrupt("conversation_changed")
            self.conversation_id = str(conversation_id or "")
            self._native_open = False
            self._native_suppressed = False
            self.start()

    def reset_native(self):
        """A new provider leg cannot inherit queued speech from its old leg."""
        with self._lock:
            self.interrupt("reconnecting")
            self._native_open = False
            self._native_suppressed = False

    def close(self):
        with self._lock:
            self.interrupt("closed")
            self._closed = True
