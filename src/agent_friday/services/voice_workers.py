"""Leased, process-bound voice engines (voice-system-clean-sheet.md §3.2, §5).

* :class:`VoiceWorker` spawns ``python -m agent_friday.voice.worker`` for one
  GPU engine, holds an arbiter ``gpu_vram`` lease for it, renews the lease on
  every job, unloads on idle, and releases the lease when the child exits —
  by any route. Release IS the process exiting; nothing here asks torch to
  give memory back.
* :func:`admit_gpu` is the ONLY admission path: the reconciled display
  reserve (``headroom_contract.resolve_display_reserve``) against the live
  free figure (``hardware_profile.vram_headroom``), corroborated by
  ``nemo_voice.gpu_status(fresh=True)``'s conservative (nvidia-smi) verdict.
  A refusal is a sentence with the numbers in it.
* :class:`GpuQueue` serialises ear and mouth jobs (one GPU job at a time). It
  is a queue, not a lock: a barge cancels every queued job of that turn.
* The engine ABCs (:class:`EarEngine`, :class:`MouthEngine`) put the worker
  engines and the in-process CPU engines behind one interface, so the session
  and the manifest never branch on where an engine runs.

Precedence (§5.4): foreign holds and the brain seat are never touched; voice
leases are ``evictable`` so an image job or a heavy turn may take them, and
an evicted stage falls to the CPU WITH a notice (``local_voice_gpu_evicted``).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from agent_friday.voice.worker import (  # framing only; no engine import
    _KIND_BIN, _KIND_JSON, read_frame, send_json, write_frame)

log = logging.getLogger("friday.voice_workers")

#: Declared working sets (MiB), corrected by measurement after the first
#: load (§3.2 rule 2). Kept beside the whisper cache so it survives restarts.
DECLARED_MIB = {"whisper-cuda": 900, "kokoro-cuda": 600}
_WORKING_SETS_FILE = None


def _working_sets_path() -> Path:
    global _WORKING_SETS_FILE
    if _WORKING_SETS_FILE is None:
        from agent_friday.services.local_voice import LOCAL_VOICE_DIR
        _WORKING_SETS_FILE = LOCAL_VOICE_DIR / "working_sets.json"
    return _WORKING_SETS_FILE


def declared_mib(engine: str) -> int:
    try:
        data = json.loads(_working_sets_path().read_text(encoding="utf-8"))
        v = int(data.get(engine) or 0)
        if v > 0:
            return v
    except Exception:
        pass
    return int(DECLARED_MIB.get(engine, 1024))


def record_measured_mib(engine: str, mib: int | None) -> None:
    if not mib or mib <= 0:
        return
    try:
        p = _working_sets_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        # Round up to a 64 MiB grain and never shrink below the measurement.
        data[engine] = int((int(mib) + 63) // 64 * 64)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        log.debug("working set not recorded: %s", e)


# ═══════════════════════════════════════════════════════════════════════════
#  Admission
# ═══════════════════════════════════════════════════════════════════════════

class GpuRefused(RuntimeError):
    """Admission refused. ``.code`` is a taxonomy code, ``.message`` a sentence."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _display_reserve_mib() -> int:
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services.headroom_contract import resolve_display_reserve
        return int(resolve_display_reserve(hwp.get())["mib"])
    except Exception:
        try:
            from agent_friday.services import hardware_profile as hwp
            return int(hwp.MIN_DISPLAY_RESERVE_MIB.get("windows", 2560))
        except Exception:
            return 2560


def admit_gpu(need_mib: int, stage: str) -> dict:
    """Decide whether `need_mib` may be taken from the card for `stage`.

    Returns ``{"ok": True, "free_mib", "reserve_mib"}`` or raises
    :class:`GpuRefused` with ``local_voice_gpu_refused`` and the arithmetic.
    Never blocks; never loads anything.
    """
    from agent_friday.services import hardware_profile as hwp
    from agent_friday.services.nemo_voice import gpu_status
    reserve = _display_reserve_mib()
    g = gpu_status(fresh=True) or {}
    if not g.get("cuda"):
        raise GpuRefused("local_voice_gpu_refused",
                         f"GPU voice not loaded: no CUDA device is available "
                         f"({g.get('detail') or 'no GPU'}). Running the {stage} on the CPU.")
    hr = hwp.vram_headroom(reserve_mib=reserve) or {}
    # The CONSERVATIVE figure: nvidia-smi's "genuinely free" whenever it
    # answered (F2), else the headroom probe's free (also nvidia-smi based).
    real_gb = g.get("vram_free_real_gb")
    free_mib = (int(float(real_gb) * 1024) if real_gb is not None
                else int(hr.get("free_mib") or 0))
    if hr.get("ok") is False or free_mib - int(need_mib) < reserve:
        raise GpuRefused(
            "local_voice_gpu_refused",
            f"GPU voice not loaded: {free_mib:,} MiB free against a {reserve:,} MiB "
            f"display reserve (the {stage} needs about {int(need_mib):,} MiB). "
            f"Running the {stage} on the CPU instead.")
    return {"ok": True, "free_mib": free_mib, "reserve_mib": reserve,
            "need_mib": int(need_mib)}


# ═══════════════════════════════════════════════════════════════════════════
#  Lease broker (thin; swapped in tests)
# ═══════════════════════════════════════════════════════════════════════════

def acquire_lease(engine: str, holder: str, mib: int, ttl_s: float) -> dict:
    from agent_friday.services import arbiter
    return arbiter.acquire("gpu_vram", int(mib), holder, purpose=f"voice {engine}",
                           ttl_s=ttl_s, evictable=True, evict_cost_s=1,
                           restore_cost_s=60)


def renew_lease(lease_id: str, ttl_s: float) -> None:
    from agent_friday.services import arbiter
    arbiter.renew(lease_id, ttl_s)


def release_lease(lease_id: str) -> None:
    from agent_friday.services import arbiter
    arbiter.release(lease_id)


def lease_state(lease_id: str) -> str | None:
    from agent_friday.services import arbiter
    return arbiter.lease_state(lease_id)


# ═══════════════════════════════════════════════════════════════════════════
#  The worker handle
# ═══════════════════════════════════════════════════════════════════════════

_POPEN_FLAGS = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW


class WorkerDied(RuntimeError):
    pass


class VoiceWorker:
    """Parent-side handle for one ``friday-voice-worker`` child.

    ``spawn`` is injectable for tests. ``on_exit(worker, reason)`` fires once,
    from whichever thread notices the child gone; the lease is released
    before it is called.
    """

    def __init__(self, engine: str, stage: str, *, holder: str | None = None,
                 idle_s: float = 600.0, stage_budget_ms: int = 20000,
                 lease: bool = True, spawn=None, args: dict | None = None,
                 on_exit=None, on_evicted=None):
        self.engine = engine
        self.stage = stage
        self.holder = holder or f"voice:{stage}"
        self.idle_s = float(idle_s)
        self.stage_budget_ms = int(stage_budget_ms)
        self.args = dict(args or {})
        self._spawn = spawn or self._default_spawn
        self._want_lease = lease
        self.lease_id = None
        self.declared_mib = declared_mib(engine)
        self.resident_mib = None
        self.device = None
        self.model = None
        self.voice = None
        self.load_ms = None
        self.proc = None
        # Re-entrant: the watchdog (_read -> stop) fires while a job holds it.
        self._io = threading.RLock()
        self._alive = False
        self._exit_reason = None
        self._exit_fired = False
        self._on_exit = on_exit
        self._on_evicted = on_evicted
        self.last_job_at = time.monotonic()
        self._idle_thread = None
        self._stop = threading.Event()
        self.jobs = 0

    # ── lifecycle ────────────────────────────────────────────────────────

    def _default_spawn(self):
        cmd = [sys.executable, "-m", "agent_friday.voice.worker", "--engine", self.engine]
        for k in ("voice", "model", "compute"):
            if self.args.get(k):
                cmd += [f"--{k}", str(self.args[k])]
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env,
                                creationflags=_POPEN_FLAGS)

    def start(self, progress=None) -> "VoiceWorker":
        """Admit, lease, spawn, load. Raises GpuRefused / WorkerDied."""
        if self._want_lease:
            admit_gpu(self.declared_mib, self.stage)
            dec = acquire_lease(self.engine, self.holder, self.declared_mib, self.idle_s)
            if not dec.get("granted"):
                raise GpuRefused("local_voice_gpu_refused",
                                 f"GPU voice not loaded: the arbiter declined "
                                 f"{self.declared_mib:,} MiB for the {self.stage} "
                                 f"({dec.get('reason') or 'no reason given'}). "
                                 f"Running the {self.stage} on the CPU instead.")
            self.lease_id = dec["lease_id"]
        try:
            if progress:
                progress(f"loading {self.engine}")
            self.proc = self._spawn()
            self._alive = True
            reply = self._call({"op": "load", "job": 0}, timeout_s=max(120.0, self.stage_budget_ms / 1000.0 * 6))
            if not reply.get("ok"):
                raise WorkerDied(reply.get("detail") or reply.get("error") or "load failed")
            self.resident_mib = reply.get("resident_mib")
            self.device = reply.get("device")
            self.model = reply.get("model")
            self.voice = reply.get("voice")
            self.load_ms = reply.get("load_ms")
            if self.resident_mib:
                record_measured_mib(self.engine, int(self.resident_mib))
        except Exception:
            self.stop(reason="load_failed")
            raise
        self.last_job_at = time.monotonic()
        self._idle_thread = threading.Thread(target=self._idle_loop, daemon=True,
                                             name=f"voice-idle-{self.stage}")
        self._idle_thread.start()
        return self

    def _idle_loop(self):
        while not self._stop.wait(2.0):
            if not self.alive():
                self._finish("died")
                return
            if self.lease_id:
                st = lease_state(self.lease_id)
                if st and st != "held":
                    reason = "evicted" if st == "evicted" else "lease_" + st
                    self.stop(reason=reason)
                    return
            if (time.monotonic() - self.last_job_at) >= self.idle_s:
                self.stop(reason="idle")
                return

    def alive(self) -> bool:
        return bool(self.proc is not None and self.proc.poll() is None)

    def idle_remaining_s(self) -> float:
        return max(0.0, self.idle_s - (time.monotonic() - self.last_job_at))

    def stop(self, reason: str = "stopped") -> None:
        """Terminate the child (releases GPU memory) and release the lease."""
        self._stop.set()
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                with self._io:
                    send_json(p.stdin, {"op": "quit"})
            except Exception:
                pass
            try:
                p.wait(timeout=2.0)
            except Exception:
                try:
                    p.terminate()
                    p.wait(timeout=3.0)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
        self._finish(reason)

    def _finish(self, reason: str) -> None:
        if self._exit_fired:
            return
        self._exit_fired = True
        self._exit_reason = reason
        self._alive = False
        if self.lease_id:
            try:
                release_lease(self.lease_id)
            except Exception as e:
                log.warning("lease release failed for %s: %s", self.lease_id, e)
        log.info("voice worker %s (%s) exited: %s; resident %s MiB released",
                 self.engine, self.stage, reason, self.resident_mib)
        if reason == "evicted" and self._on_evicted:
            try:
                self._on_evicted(self)
            except Exception:
                pass
        if self._on_exit:
            try:
                self._on_exit(self, reason)
            except Exception:
                pass

    # ── protocol ─────────────────────────────────────────────────────────

    def _read(self, timeout_s: float):
        """One frame with a deadline; the watchdog with a process to kill."""
        result = {}

        def _r():
            try:
                result["fr"] = read_frame(self.proc.stdout)
            except Exception as e:
                result["err"] = e
        t = threading.Thread(target=_r, daemon=True)
        t.start()
        t.join(timeout_s)
        if t.is_alive():
            self.stop(reason="timeout")
            raise WorkerDied(f"the {self.stage} worker did not answer within "
                             f"{int(timeout_s * 1000)} ms and was stopped")
        if "err" in result or result.get("fr") is None:
            self._finish("died")
            raise WorkerDied(f"the {self.stage} worker exited unexpectedly")
        return result["fr"]

    def _call(self, msg: dict, timeout_s: float | None = None) -> dict:
        with self._io:
            if not self.alive():
                self._finish("died")
                raise WorkerDied(f"the {self.stage} worker is not running")
            send_json(self.proc.stdin, msg)
            kind, payload = self._read(timeout_s or self.stage_budget_ms / 1000.0)
            if kind != _KIND_JSON:
                raise WorkerDied("protocol error: expected a JSON reply")
            return json.loads(payload.decode("utf-8"))

    def _touch(self):
        self.last_job_at = time.monotonic()
        self.jobs += 1
        if self.lease_id:
            try:
                renew_lease(self.lease_id, self.idle_s)
            except Exception:
                pass

    def transcribe(self, pcm: bytes) -> str:
        self._touch()
        with self._io:
            if not self.alive():
                self._finish("died")
                raise WorkerDied(f"the {self.stage} worker is not running")
            send_json(self.proc.stdin, {"op": "transcribe", "job": self.jobs})
            write_frame(self.proc.stdin, _KIND_BIN, pcm)
            kind, payload = self._read(self.stage_budget_ms / 1000.0)
            reply = json.loads(payload.decode("utf-8"))
        if reply.get("error"):
            raise RuntimeError(reply.get("detail") or reply["error"])
        return str(reply.get("text") or "")

    def synth_stream(self, text: str, cancel: threading.Event | None = None):
        """Yield PCM16 24 kHz chunks as the child produces them."""
        self._touch()
        with self._io:
            if not self.alive():
                self._finish("died")
                raise WorkerDied(f"the {self.stage} worker is not running")
            send_json(self.proc.stdin, {"op": "synth", "text": text, "job": self.jobs})
            sent_cancel = False
            while True:
                if cancel is not None and cancel.is_set() and not sent_cancel:
                    send_json(self.proc.stdin, {"op": "cancel"})
                    sent_cancel = True
                kind, payload = self._read(self.stage_budget_ms / 1000.0)
                if kind == _KIND_BIN:
                    if not (cancel is not None and cancel.is_set()):
                        yield payload
                    continue
                reply = json.loads(payload.decode("utf-8"))
                if reply.get("error"):
                    raise RuntimeError(reply.get("detail") or reply["error"])
                return

    def ping(self) -> bool:
        try:
            return bool(self._call({"op": "ping", "job": -1}, timeout_s=5.0).get("pong"))
        except Exception:
            return False

    def describe(self) -> dict:
        return {"engine": self.engine.split("-")[0] if self.engine != "fake" else "fake",
                "device": self.device or "cuda", "model": self.model,
                "voice": self.voice, "worker_pid": getattr(self.proc, "pid", None),
                "resident_mib": self.resident_mib, "lease_id": self.lease_id,
                "idle_remaining_s": int(self.idle_remaining_s())}


# ═══════════════════════════════════════════════════════════════════════════
#  The GPU queue
# ═══════════════════════════════════════════════════════════════════════════

class GpuQueue:
    """One GPU job at a time, in order; a barge drops every queued job of a
    turn and flags the running one. Depth is exposed for the HUD's debug
    drawer. It orders Friday's voice work only — the seat is its own process.
    """

    def __init__(self):
        self._cv = threading.Condition()
        self._q: list = []           # [(turn_id, fn, done_event, box)]
        self._running_turn = None
        self._cancel_turns: set = set()
        self._thread = threading.Thread(target=self._run, daemon=True, name="voice-gpu-queue")
        self._thread.start()

    def submit(self, turn_id, fn) -> "threading.Event":
        """`fn(cancel_event)` runs on the queue thread. Returns a done event
        whose `.result` / `.error` attributes carry the outcome."""
        done = threading.Event()
        done.result = None
        done.error = None
        done.cancelled = False
        with self._cv:
            if turn_id in self._cancel_turns:
                done.cancelled = True
                done.set()
                return done
            self._q.append((turn_id, fn, done))
            self._cv.notify_all()
        return done

    def cancel_turn(self, turn_id) -> int:
        """Drop queued jobs for `turn_id`; flag a running one. Returns dropped."""
        with self._cv:
            self._cancel_turns.add(turn_id)
            keep, dropped = [], 0
            for item in self._q:
                if item[0] == turn_id:
                    item[2].cancelled = True
                    item[2].set()
                    dropped += 1
                else:
                    keep.append(item)
            self._q = keep
            if self._running_turn == turn_id and self._running_cancel is not None:
                self._running_cancel.set()
            self._cv.notify_all()
        return dropped

    def clear_turn(self, turn_id) -> None:
        with self._cv:
            self._cancel_turns.discard(turn_id)

    def depth(self) -> int:
        with self._cv:
            return len(self._q) + (1 if self._running_turn is not None else 0)

    _running_cancel = None
    _stopped = False

    def stop(self) -> None:
        """End the queue thread (tests; process shutdown). Queued jobs are
        dropped as cancelled."""
        with self._cv:
            self._stopped = True
            for item in self._q:
                item[2].cancelled = True
                item[2].set()
            self._q = []
            self._cv.notify_all()

    def _run(self):
        while True:
            with self._cv:
                while not self._q and not self._stopped:
                    self._cv.wait()
                if self._stopped:
                    return
                turn_id, fn, done = self._q.pop(0)
                self._running_turn = turn_id
                self._running_cancel = threading.Event()
                if turn_id in self._cancel_turns:
                    self._running_cancel.set()
                cancel = self._running_cancel
            try:
                done.result = fn(cancel)
            except Exception as e:  # noqa: BLE001
                done.error = e
            finally:
                done.cancelled = cancel.is_set()
                with self._cv:
                    self._running_turn = None
                    self._running_cancel = None
                done.set()


_QUEUE = None
_QUEUE_LOCK = threading.Lock()


def gpu_queue() -> GpuQueue:
    global _QUEUE
    with _QUEUE_LOCK:
        if _QUEUE is None:
            _QUEUE = GpuQueue()
        return _QUEUE


# ═══════════════════════════════════════════════════════════════════════════
#  Engine ABCs and the factory
# ═══════════════════════════════════════════════════════════════════════════

class EarEngine:
    name = "?"
    device = "?"

    def transcribe(self, pcm16_16k: bytes) -> str:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"engine": self.name, "device": self.device}

    def close(self) -> None:
        pass

    def alive(self) -> bool:
        return True


class MouthEngine:
    name = "?"
    device = "?"

    def synthesize_stream(self, text: str, cancel: threading.Event | None = None):
        raise NotImplementedError

    def describe(self) -> dict:
        return {"engine": self.name, "device": self.device}

    def close(self) -> None:
        pass

    def alive(self) -> bool:
        return True


class CpuWhisperEar(EarEngine):
    name = "faster-whisper"
    device = "cpu"

    def __init__(self, model_size: str = "auto"):
        from agent_friday.services.local_voice import WhisperASR, resolve_whisper_model
        # CPU by construction: this engine is what runs when the GPU worker
        # was refused, so it must not load onto that card in-process.
        self._asr = WhisperASR(model_size, force_cpu=True)
        self.model = f"{resolve_whisper_model(model_size, 'cpu')} int8"

    def load(self, progress=None):
        self._asr.load(progress=progress)

    def transcribe(self, pcm16_16k: bytes) -> str:
        return self._asr.transcribe(pcm16_16k)

    def describe(self) -> dict:
        return {"engine": self.name, "device": "cpu", "model": self.model}


class WorkerEar(EarEngine):
    name = "faster-whisper"
    device = "cuda"

    def __init__(self, worker: VoiceWorker):
        self.worker = worker

    def transcribe(self, pcm16_16k: bytes) -> str:
        return self.worker.transcribe(pcm16_16k)

    def describe(self) -> dict:
        d = self.worker.describe()
        d["engine"] = self.name
        return d

    def close(self) -> None:
        self.worker.stop("closed")

    def alive(self) -> bool:
        return self.worker.alive()


class PiperMouth(MouthEngine):
    name = "piper"
    device = "cpu"

    def __init__(self, voice: str = "en_US-amy-medium"):
        from agent_friday.services.local_voice import PiperTTS
        self._tts = PiperTTS(voice)
        self.voice = voice

    def load(self, progress=None):
        self._tts.load(progress=progress)

    def synthesize_stream(self, text: str, cancel=None):
        pcm = self._tts.synthesize(text)
        step = 9600
        for off in range(0, len(pcm), step):
            if cancel is not None and cancel.is_set():
                return
            yield pcm[off:off + step]

    def describe(self) -> dict:
        return {"engine": "piper", "device": "cpu", "model": self.voice, "voice": self.voice}


class KokoroCpuMouth(MouthEngine):
    name = "kokoro"
    device = "cpu"

    def __init__(self, voice: str = "af_heart"):
        from agent_friday.services.kokoro_voice import KokoroTTS
        self._tts = KokoroTTS(voice, allow_cpu=True)
        self.voice = voice

    def load(self, progress=None):
        self._tts.load(progress=progress)
        self.device = self._tts._device or "cpu"

    def synthesize_stream(self, text: str, cancel=None):
        pcm = self._tts.synthesize(text)
        step = 9600
        for off in range(0, len(pcm), step):
            if cancel is not None and cancel.is_set():
                return
            yield pcm[off:off + step]

    def describe(self) -> dict:
        return {"engine": "kokoro", "device": self.device, "model": "kokoro-82M",
                "voice": self.voice}


class WorkerMouth(MouthEngine):
    name = "kokoro"
    device = "cuda"

    def __init__(self, worker: VoiceWorker):
        self.worker = worker

    def synthesize_stream(self, text: str, cancel=None):
        return self.worker.synth_stream(text, cancel)

    def describe(self) -> dict:
        d = self.worker.describe()
        d["engine"] = self.name
        d.setdefault("model", "kokoro-82M")
        return d

    def close(self) -> None:
        self.worker.stop("closed")

    def alive(self) -> bool:
        return self.worker.alive()


#: Engines the manifest's proofs have loaded and are holding for the session
#: (§3.1: the proof's engine IS the session's engine). Keyed by stage.
_HELD: dict = {}
_HELD_LOCK = threading.Lock()
#: One-per-session notices raised by admission/eviction, drained by whoever
#: renders them (the ws session, the Settings card).
NOTICES: list = []


def held(stage: str):
    with _HELD_LOCK:
        e = _HELD.get(stage)
    if e is not None and not e.alive():
        with _HELD_LOCK:
            if _HELD.get(stage) is e:
                _HELD.pop(stage, None)
        return None
    return e


def _hold(stage: str, engine) -> None:
    with _HELD_LOCK:
        old = _HELD.get(stage)
        _HELD[stage] = engine
    if old is not None and old is not engine:
        try:
            old.close()
        except Exception:
            pass


def release_all(reason: str = "released") -> None:
    with _HELD_LOCK:
        items = list(_HELD.items())
        _HELD.clear()
    for _k, e in items:
        try:
            e.close()
        except Exception:
            pass


def _notice(code: str, message: str) -> None:
    NOTICES.append({"code": code, "message": message, "at": time.time()})
    del NOTICES[:-20]
    log.warning("%s: %s", code, message)


def _idle_s() -> float:
    try:
        from agent_friday.core import _load_settings
        return float((_load_settings() or {}).get("voice_idle_unload_s") or 600)
    except Exception:
        return 600.0


def _on_evicted(worker: VoiceWorker) -> None:
    _notice("local_voice_gpu_evicted",
            f"Friday's {worker.stage} was moved off the GPU to make room for "
            f"another job. Continuing on the CPU.")


def build_ear(selection: dict, progress=None) -> EarEngine:
    """The ear for `selection`, honouring its GPU policy. Reuses a held,
    alive worker. Raises GpuRefused only under policy `required`."""
    from agent_friday.services.local_voice import resolve_whisper_model
    policy = str(selection.get("device_policy") or "if_free")
    model = str(selection.get("model") or "auto")
    cur = held("ear")
    if policy != "never":
        # A held GPU worker is reused; a held CPU ear is NOT — "if free" means
        # ask the card again on every arm, and fall back to the held CPU
        # engine (no reload) when it is refused again.
        if isinstance(cur, WorkerEar):
            return cur
        try:
            w = VoiceWorker("whisper-cuda", "ear", idle_s=_idle_s(),
                            args={"model": resolve_whisper_model(model, "cuda")},
                            on_evicted=_on_evicted)
            w.start(progress=progress)
            eng = WorkerEar(w)
            eng.model = w.model
            _hold("ear", eng)
            return eng
        except GpuRefused as e:
            if policy == "required":
                raise
            _notice(e.code, e.message)
        except Exception as e:  # noqa: BLE001
            if policy == "required":
                raise GpuRefused("voice_worker_died",
                                 f"Friday's ear engine could not start on the GPU "
                                 f"({type(e).__name__}: {str(e)[:120]}).")
            _notice("voice_worker_died",
                    f"Friday's ear engine crashed on the GPU and was restarted "
                    f"on the CPU ({type(e).__name__}).")
    if (isinstance(cur, CpuWhisperEar)
            and cur.model == f"{resolve_whisper_model(model, 'cpu')} int8"):
        return cur
    eng = CpuWhisperEar(model)
    eng.load(progress=progress)
    _hold("ear", eng)
    return eng


def build_mouth(selection: dict, progress=None) -> MouthEngine:
    """The mouth for `selection`. Kokoro on the GPU when leased; Kokoro on the
    CPU only by the explicit opt-in; Piper is the floor."""
    policy = str(selection.get("device_policy") or "if_free")
    engine = str(selection.get("engine") or "piper")
    voice = str(selection.get("voice") or ("af_heart" if engine == "kokoro" else "en_US-amy-medium"))
    cur = held("mouth")
    if engine == "kokoro":
        if policy != "never":
            if isinstance(cur, WorkerMouth) and getattr(cur, "voice", None) == voice:
                return cur
            try:
                w = VoiceWorker("kokoro-cuda", "mouth", idle_s=_idle_s(),
                                args={"voice": voice}, on_evicted=_on_evicted)
                w.start(progress=progress)
                eng = WorkerMouth(w)
                eng.voice = voice
                _hold("mouth", eng)
                return eng
            except GpuRefused as e:
                if policy == "required":
                    raise
                _notice(e.code, e.message)
            except Exception as e:  # noqa: BLE001
                if policy == "required":
                    raise GpuRefused("voice_worker_died",
                                     f"Friday's voice engine could not start on the "
                                     f"GPU ({type(e).__name__}: {str(e)[:120]}).")
                _notice("voice_worker_died",
                        f"Friday's voice engine crashed on the GPU "
                        f"({type(e).__name__}); using the CPU.")
        try:
            from agent_friday.core import _load_settings
            allow_cpu = bool((_load_settings() or {}).get("local_voice_kokoro_allow_cpu"))
        except Exception:
            allow_cpu = False
        if allow_cpu:
            if isinstance(cur, KokoroCpuMouth) and cur.voice == voice:
                return cur
            eng = KokoroCpuMouth(voice)
            eng.load(progress=progress)
            _hold("mouth", eng)
            return eng
        # Kokoro refused off the GPU and CPU is not opted in: Piper is the
        # floor, and the manifest says "you chose kokoro; serving piper".
    piper_voice = voice if engine == "piper" else "en_US-amy-medium"
    if isinstance(cur, PiperMouth) and cur.voice == piper_voice:
        return cur
    eng = PiperMouth(piper_voice)
    eng.load(progress=progress)
    _hold("mouth", eng)
    return eng


# ── manifest runners (the ENGINE_RUNNERS entries for ear and mouth) ─────────

def run_ear_proof(selection: dict, pcm: bytes, progress=None):
    eng = build_ear(selection, progress=progress)
    return eng.transcribe(pcm), eng.describe()


def run_mouth_proof(selection: dict, text: str, progress=None):
    eng = build_mouth(selection, progress=progress)
    pcm = b"".join(eng.synthesize_stream(text))
    return pcm, eng.describe()
