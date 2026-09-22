"""The Voice Manifest — the ONE source of facts about the voice stack (D1).

docs/design/active/voice-system-clean-sheet.md §3.1. Three stages — ``ear``,
``mind``, ``mouth`` — and for each: what the user *selected*, what is
*effective*, and a *proof*. The proof is the only thing that may say a stage is
ready, and a proof is set only by :func:`prove` actually running the stage:

* the ear transcribes the bundled ``resources/voice_proof.wav`` and must return
  the expected words;
* the mind sends one short request to the resident seat with the REAL tool
  list and must receive a completion;
* the mouth synthesizes a fixed nine-word line and must return PCM of a
  plausible duration.

Importability, file existence and ``torch.cuda.is_available()`` are inputs to
``effective``, never to ``proof``. Every surface — ``/api/voice/session-info``,
the Settings card, the HUD, the session-start ``manifest`` frame and the
model's own self-description — renders :meth:`VoiceManifest.snapshot` or
:meth:`VoiceManifest.describe_for_model`; none computes its own version.

Nothing here does network I/O on the local path except the mind proof, which
talks to a loopback seat (C3 is about the *cloud*; a resident seat on
127.0.0.1 is the local path).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import wave
from pathlib import Path

log = logging.getLogger("friday.voice_manifest")

STAGES = ("ear", "mind", "mouth")
PROOF_STATES = ("unproven", "proving", "proven", "refused")
DEFAULT_TTL_S = 900

#: The nine-word line every mouth proof speaks. Fixed so latency is comparable
#: across engines and across days.
PROOF_LINE = "Friday is ready to speak with you right now, Stephen."

#: What the ear must hear in the bundled WAV (lower-case, punctuation-free
#: word set; ALL must be present). The asset was synthesized with Piper Amy
#: from "Friday, what time is it right now?" and transcribes exactly on
#: faster-whisper small (CPU int8, beam 1) — verified 2026-09-16.
PROOF_WAV = Path(__file__).resolve().parent.parent / "resources" / "voice_proof.wav"
PROOF_WORDS = ("friday", "time", "right", "now")

#: GPU policy per stage (§8.1 B). ``required`` refuses rather than falling to
#: the CPU; ``never`` keeps the card for the seat; ``if_free`` is the default.
GPU_POLICIES = ("never", "if_free", "required")


def _now() -> float:
    return time.time()


def _blank_stage(selected: dict | None = None) -> dict:
    return {
        "selected": dict(selected or {}),
        "effective": {},
        "proof": {"state": "unproven", "at": None, "ttl_s": DEFAULT_TTL_S,
                  "latency_ms": None, "sample": "", "code": ""},
        "reason": "",
        "action": None,
        "progress": "",
    }


def load_proof_pcm() -> bytes:
    """16 kHz mono PCM16 of the bundled proof utterance."""
    with wave.open(str(PROOF_WAV), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise RuntimeError("voice_proof.wav must be 16 kHz mono PCM16")
        return w.readframes(w.getnframes())


def transcript_matches(text: str, words=PROOF_WORDS) -> bool:
    got = set("".join(ch if ch.isalnum() or ch.isspace() else " "
                      for ch in (text or "").lower()).split())
    return all(w in got for w in words)


# ═══════════════════════════════════════════════════════════════════════════
#  Selection — what the user asked for, read from settings
# ═══════════════════════════════════════════════════════════════════════════

def _settings():
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _policy(value, default="if_free") -> str:
    v = str(value or default).strip().lower().replace("-", "_")
    return v if v in GPU_POLICIES else default


def read_selection(settings: dict | None = None) -> dict:
    """The three selections, from settings. Pure (no I/O beyond settings)."""
    s = settings if settings is not None else _settings()
    mode = str(s.get("voice_engine") or "local").strip().lower()
    if mode == "auto":            # settled 2026-09-09: auto is a synonym for local
        mode = "local"
    return {
        "mode": mode,
        "ear": {"engine": "faster-whisper",
                "model": str(s.get("local_voice_asr_model") or "small"),
                "device_policy": _policy(s.get("voice_ear_gpu"))},
        "mind": {"engine": "seat", "reply_cap": _reply_cap(s)},
        "mouth": {"engine": str(s.get("local_voice_tts_engine") or "piper").strip().lower(),
                  "voice": (str(s.get("local_voice_kokoro_voice") or "af_heart")
                            if str(s.get("local_voice_tts_engine") or "piper").lower() == "kokoro"
                            else str(s.get("local_voice_tts_voice") or "en_US-amy-medium")),
                  "device_policy": _policy(s.get("voice_mouth_gpu"))},
        "idle_unload_s": int(s.get("voice_idle_unload_s") or 600),
    }


def _reply_cap(s: dict) -> int:
    try:
        n = int(s.get("voice_max_tokens") or 0)
    except Exception:
        n = 0
    return 300 if n <= 0 else max(64, min(n, 2048))


# ═══════════════════════════════════════════════════════════════════════════
#  Provers — the ONLY code that can set proof.state = proven
# ═══════════════════════════════════════════════════════════════════════════
#
# Each prover takes the stage's selection and a progress callback and returns
# ``{"effective": {...}, "latency_ms": int, "sample": str}`` on success or
# raises ``ProofRefused(code, message, action)``. Tests replace these through
# ``ENGINE_RUNNERS`` (the functions that actually run an engine), never by
# poking ``proof.state`` — there is no setter for it.

class ProofRefused(RuntimeError):
    def __init__(self, code: str, message: str, action: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action


def _run_ear(selection: dict, pcm: bytes, progress) -> tuple[str, dict]:
    """Phase 1: the ear behind the engine ABC — a leased CUDA worker when the
    selection's GPU policy allows and admission passes, else in-process CPU.
    The engine the proof loads is HELD for the session (voice_workers.held)."""
    from agent_friday.services import voice_workers
    return voice_workers.run_ear_proof(selection, pcm, progress)


def _run_mouth(selection: dict, text: str, progress) -> tuple[bytes, dict]:
    from agent_friday.services import voice_workers
    return voice_workers.run_mouth_proof(selection, text, progress)


def _run_mind(selection: dict, progress) -> dict:
    """One short completion on the resident brain seat with the REAL tools.

    Returns ``{"seat", "base", "window", "tools", "prompt_tokens",
    "timings", "content"}``. Raises ProofRefused when there is no seat.
    """
    from agent_friday.services import local_seats
    seat = local_seats.resolve("brain")
    if not seat:
        raise ProofRefused("local_voice_brain_absent",
                           "Local voice can hear you, but no local model is "
                           "loaded to answer.",
                           {"label": "Load the model", "kind": "settings"})
    from agent_friday.services import tool_budget
    base = tool_budget._seat_base(seat)
    if not base:
        raise ProofRefused("local_voice_brain_absent",
                           f"The brain seat {seat} is installed but nothing is "
                           "serving it right now.",
                           {"label": "Load the model", "kind": "settings"})
    if progress:
        progress(f"asking {seat}")
    contract = compute_contract(seat)
    tools = contract.pop("_oai_tools", None)
    body = {
        "model": seat,
        "messages": [{"role": "system", "content": contract.get("_system") or
                      "You are Friday. Reply with one word."},
                     {"role": "user", "content": "Say OK."}],
        "max_tokens": 8, "temperature": 0,
    }
    # The gemma4 e-series thinks first, inside <|channel>thought, and eight
    # tokens of thinking is an empty `content`. Observed live on
    # 2026-09-18 against the FridayWeaver seat: content '' with
    # reasoning 'Thinking Process:\n1', finish_reason 'length' -- and this
    # proof called that "answered with no completion" on a seat that was
    # fine. The router's real turns already disable thinking for these
    # models (model_router._call_openai, channel_toolcalls.
    # needs_thinking_disabled); the proof has to ask the same way, or it
    # refuses a seat the turn would have used.
    try:
        from agent_friday.services import channel_toolcalls as _ct
        if _ct.needs_thinking_disabled(seat):
            body["chat_template_kwargs"] = {"enable_thinking": False}
    except Exception:
        pass
    contract.pop("_system", None)
    if tools:
        body["tools"] = tools
    import urllib.request
    req = urllib.request.Request(
        base + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    choices = resp.get("choices") or []
    msg = (choices[0].get("message") or {}) if choices else {}
    if not choices or not (msg.get("content") or msg.get("tool_calls")):
        raise ProofRefused("voice_stage_unproven",
                           f"{seat} answered with no completion.",
                           {"label": "Prove again", "kind": "retry"})
    return {"seat": seat, "base": base, "contract": contract,
            "timings": resp.get("timings") or {}, "usage": resp.get("usage") or {},
            "content": (msg.get("content") or "")[:40]}


#: Indirection so a test can make every engine fail without touching a proof
#: field. Replacing an entry with ``lambda *a, **k: (_ for _ in ()).throw(...)``
#: is how ``test_manifest_cannot_be_proven_without_running`` works.
ENGINE_RUNNERS = {"ear": _run_ear, "mouth": _run_mouth, "mind": _run_mind}


def compute_contract(seat: str, system_prompt: str | None = None) -> dict:
    """§3.3: the capability contract for `seat`, computed BEFORE a session.

    Uses ``tool_budget.fit_tools_to_seat`` with the real voice system prompt
    (via the registered prompt builder) so the tool list handed to the API
    is the tool list the prompt is rendered from. ``fits`` is false when the
    floor tools cannot be held; the session refuses on it (§3.3 rule 2).
    """
    from agent_friday.services import tool_budget
    from agent_friday.services.agent import CLAUDE_TOOLS
    window = tool_budget._window(seat)
    if system_prompt is None:
        system_prompt = _prompt_for_contract()
    fitted, note = tool_budget.fit_tools_to_seat(
        seat, CLAUDE_TOOLS, system=system_prompt, messages=[])
    names = [str(t.get("name")) for t in fitted]
    offered = {str(t.get("name")) for t in CLAUDE_TOOLS}
    floor = [n for n in tool_budget._FLOOR_TOOLS if n in offered]
    floor_present = all(n in names for n in floor)
    prompt_tokens = (tool_budget.measure_request(seat, system_prompt, [], None)
                     or (len(system_prompt or "") // 4))
    tool_tokens = tool_budget._tokens(list(fitted))
    reply_cap = _reply_cap(_settings())
    loop_reserve = tool_budget._LOOP_RESERVE
    fits = bool(names) and floor_present and (
        prompt_tokens + tool_tokens + loop_reserve + reply_cap <= window)
    contract = {
        "tools": names, "floor_present": floor_present,
        "knowledge_graph": "knowledge_query" in names,
        "memory": any(n in names for n in ("memory_recall", "recall_memory",
                                             "search_memory", "memory_search",
                                             "remember")),
        "window": window, "prompt_tokens": int(prompt_tokens),
        "tool_tokens": int(tool_tokens),
        "loop_reserve": loop_reserve, "reply_cap": reply_cap, "fits": fits,
        "note": note or "",
        "reason": "" if fits else (
            f"This seat holds {window:,} tokens; the voice prompt needs "
            f"{int(prompt_tokens):,}, the tools {int(tool_tokens):,}, the loop "
            f"reserve {loop_reserve:,} and the reply {reply_cap:,}. "
            + ("There is no room for Friday's tools. " if not floor_present else "")
            + "Raise the seat's context or shorten the prompt."),
    }
    try:
        from agent_friday.routing.model_router import anthropic_to_openai_tools
        contract["_oai_tools"] = anthropic_to_openai_tools(list(fitted))
    except Exception:
        contract["_oai_tools"] = None
    contract["_system"] = system_prompt
    return contract


# The voice prompt builder is registered by routes/voice.py (it owns the
# persona rules and the vault gating); the manifest must not import routes.
_PROMPT_BUILDER = None


def set_prompt_builder(fn) -> None:
    global _PROMPT_BUILDER
    _PROMPT_BUILDER = fn


def _seat_label(model_id) -> str:
    """The seat's human label from the model store ("FridayWeaver-1.0"),
    never its id: the id is a tag a small model repeats verbatim."""
    try:
        from agent_friday.services import model_store
        lab = ((model_store.get(model_id) or {}).get("label") or "").strip()
        if lab:
            return lab
    except Exception:
        pass
    return str(model_id or "Friday's local model")


def _prompt_for_contract() -> str:
    if _PROMPT_BUILDER is None:
        return "You are Agent Friday in a live voice conversation."
    try:
        return _PROMPT_BUILDER() or ""
    except Exception as e:
        log.warning("voice prompt builder failed for the contract: %s", e)
        return "You are Agent Friday in a live voice conversation."


# ═══════════════════════════════════════════════════════════════════════════
#  The manifest
# ═══════════════════════════════════════════════════════════════════════════

class VoiceManifest:
    """One object; see the module docstring. Thread-safe; proofs single-flight."""

    def __init__(self, ttl_s: int = DEFAULT_TTL_S, clock=_now):
        self._lock = threading.RLock()
        self._clock = clock
        self.ttl_s = int(ttl_s)
        self.mode = "local"
        self.stages = {k: _blank_stage() for k in STAGES}
        self._proving = False
        self._runtime: dict = {}   # proven engine handles (Phase 1 workers)
        self._listeners: list = []
        self.refresh_selection()

    # ── selection ────────────────────────────────────────────────────────

    def refresh_selection(self, settings: dict | None = None) -> None:
        sel = read_selection(settings)
        with self._lock:
            self.mode = sel["mode"]
            self.idle_unload_s = sel["idle_unload_s"]
            for k in STAGES:
                st = self.stages[k]
                if st["selected"] != sel[k]:
                    # A changed selection invalidates the proof: what was
                    # proven is no longer what is selected.
                    if st["proof"]["state"] == "proven":
                        st["proof"] = dict(st["proof"], state="unproven")
                        st["reason"] = "selection changed since the last proof"
                    st["selected"] = dict(sel[k])

    # ── proofs ───────────────────────────────────────────────────────────

    def is_stale(self, stage: str) -> bool:
        p = self.stages[stage]["proof"]
        if p["state"] != "proven" or not p.get("at"):
            return True
        return (self._clock() - float(p["at"])) > float(p.get("ttl_s") or self.ttl_s)

    def _set_progress(self, stage: str, text: str) -> None:
        with self._lock:
            self.stages[stage]["progress"] = str(text or "")[:160]

    def _begin(self, stage: str) -> None:
        with self._lock:
            st = self.stages[stage]
            st["proof"] = dict(st["proof"], state="proving", code="")
            st["reason"] = ""
            st["action"] = None
            st["progress"] = "starting"

    def _refuse(self, stage: str, code: str, message: str, action=None) -> None:
        with self._lock:
            st = self.stages[stage]
            st["proof"] = dict(st["proof"], state="refused", code=code,
                               at=self._clock(), latency_ms=None, sample="")
            st["effective"] = {}
            st["reason"] = message
            st["action"] = action
            st["progress"] = ""
        try:
            from agent_friday.services.voice_receipt import log_readiness
            log_readiness(f"voice.{stage}", False, detail=f"{code}: {message}")
        except Exception:
            pass

    def _prove_ok(self, stage: str, effective: dict, latency_ms: float,
                  sample: str, reason: str = "") -> None:
        with self._lock:
            st = self.stages[stage]
            st["effective"] = dict(effective)
            st["proof"] = {"state": "proven", "at": self._clock(),
                           "ttl_s": self.ttl_s, "latency_ms": int(latency_ms),
                           "sample": sample, "code": ""}
            st["reason"] = reason
            st["action"] = None
            st["progress"] = ""
        try:
            from agent_friday.services.voice_receipt import log_readiness
            log_readiness(f"voice.{stage}", True,
                          detail=f"{effective.get('engine')} on "
                                 f"{effective.get('device')} {int(latency_ms)} ms")
        except Exception:
            pass

    def prove(self, stage: str) -> dict:
        """Run ONE stage's proof synchronously. Returns the stage dict."""
        if stage not in STAGES:
            raise ValueError(stage)
        self._begin(stage)
        prog = lambda m: self._set_progress(stage, m)  # noqa: E731
        sel = dict(self.stages[stage]["selected"])
        t0 = time.perf_counter()
        try:
            if stage == "ear":
                self._prove_ear(sel, prog, t0)
            elif stage == "mouth":
                self._prove_mouth(sel, prog, t0)
            else:
                self._prove_mind(sel, prog, t0)
        except ProofRefused as e:
            self._refuse(stage, e.code, e.message, e.action)
        except Exception as e:  # noqa: BLE001
            log.warning("voice %s proof failed: %s: %s", stage, type(e).__name__, e)
            # An admission refusal (voice_workers.GpuRefused under policy
            # `required`) carries its own taxonomy code and sentence.
            code = getattr(e, "code", None)
            if code and getattr(e, "message", None):
                self._refuse(stage, str(code), str(e.message),
                             {"label": "Set GPU to 'if free'", "kind": "settings"}
                             if code == "local_voice_gpu_refused" else
                             {"label": "Prove again", "kind": "retry"})
            else:
                self._refuse(stage, "voice_stage_unproven",
                             f"Friday couldn't prove her {self._noun(stage)} works "
                             f"right now: {type(e).__name__}: {str(e)[:160]}",
                             {"label": "Prove again", "kind": "retry"})
        self._notify()
        return self.snapshot_stage(stage)

    def _prove_ear(self, sel, prog, t0):
        if self.mode == "gemini":
            raise ProofRefused("cloud_stage", "Gemini Live hears; nothing local to prove.")
        pcm = load_proof_pcm()
        runner = ENGINE_RUNNERS["ear"]
        text, effective = runner(sel, pcm, prog)
        # Load time is included: it is the cost the user pays on arm, and the
        # row shows what arming cost. Per-utterance figures are the receipts'.
        ms = (time.perf_counter() - t0) * 1000.0
        if not transcript_matches(text):
            raise ProofRefused("voice_stage_unproven",
                               f"The ear heard {text!r} where it should have "
                               f"heard {' '.join(PROOF_WORDS)!r}.",
                               {"label": "Prove again", "kind": "retry"})
        reason = ""
        if sel.get("device_policy") == "required" and effective.get("device") != "cuda":
            raise ProofRefused("local_voice_gpu_refused",
                               "GPU policy is 'required' for the ear and no GPU "
                               "engine is admitted in this phase.",
                               {"label": "Set GPU to 'if free'", "kind": "settings"})
        if sel.get("device_policy") == "if_free" and effective.get("device") != "cuda":
            reason = "you chose GPU if free; serving on the CPU"
        self._prove_ok("ear", effective, ms,
                       f"{len(pcm) / 2 / 16000:.1f} s of audio → {text!r}", reason)

    def _prove_mouth(self, sel, prog, t0):
        if self.mode == "gemini":
            raise ProofRefused("cloud_stage", "Gemini Live speaks; nothing local to prove.")
        runner = ENGINE_RUNNERS["mouth"]
        pcm, effective = runner(sel, PROOF_LINE, prog)
        ms = (time.perf_counter() - t0) * 1000.0
        seconds = len(pcm or b"") / 2 / 24000.0
        if not pcm or not (0.5 <= seconds <= 20.0):
            raise ProofRefused("voice_stage_unproven",
                               f"The mouth returned {seconds:.2f} s of audio for a "
                               f"nine-word line; that is not speech.",
                               {"label": "Prove again", "kind": "retry"})
        reason = ""
        if sel.get("device_policy") == "required" and effective.get("device") != "cuda":
            raise ProofRefused("local_voice_gpu_refused",
                               "GPU policy is 'required' for the voice and it is "
                               "serving on the CPU.",
                               {"label": "Set GPU to 'if free'", "kind": "settings"})
        if effective.get("engine") != sel.get("engine"):
            reason = f"you chose {sel.get('engine')}; serving {effective.get('engine')}"
        elif sel.get("device_policy") == "if_free" and effective.get("device") != "cuda":
            reason = "you chose GPU if free; serving on the CPU"
        self._prove_ok("mouth", effective, ms,
                       f"{seconds:.1f} s of audio from a {len(PROOF_LINE.split())}-word line",
                       reason)

    def _prove_mind(self, sel, prog, t0):
        out = ENGINE_RUNNERS["mind"](sel, prog)
        ms = (time.perf_counter() - t0) * 1000.0
        contract = out.get("contract") or {}
        effective = {"engine": "seat", "model": out.get("seat"),
                     "device": "local", "base": out.get("base"),
                     "window": contract.get("window"),
                     "contract": contract,
                     "prefill_tokens": (out.get("timings") or {}).get("prompt_n"),
                     "cloud_relay": self.mode == "gemini"}
        if not contract.get("fits", True):
            self._refuse("mind", "voice_contract_does_not_fit",
                         "The local model's window can't hold Friday's tools "
                         "alongside this conversation. " + contract.get("reason", ""),
                         {"label": "Raise seat context", "kind": "settings"})
            with self._lock:
                self.stages["mind"]["effective"] = effective
            return
        self._prove_ok("mind", effective, ms,
                       f"{len(contract.get('tools') or [])} tools; replied "
                       f"{out.get('content')!r}")

    def prove_all(self, stages=STAGES) -> dict:
        """Prove every stage, ear first (§5.4). Single-flight: a second caller
        while one runs gets the current snapshot without starting another."""
        with self._lock:
            if self._proving:
                return self.snapshot()
            self._proving = True
        try:
            self.refresh_selection()
            for k in stages:
                self.prove(k)
            # F5 / §4.4: the proofs change the self-description, which is the
            # FIRST line of the session prompt, so the seat's prefix cache is
            # cold on the first utterance even right after arming (measured
            # 2026-09-18: 27,460 prefill tokens and ~8 s to first audio on
            # turn 1, then 15 tokens and ~1.5 s from turn 2). The route
            # registers a warm hook that sends the session's exact prompt
            # once, through the same code path a turn uses.
            hook = getattr(self, "after_prove", None)
            if hook is not None and self.mode == "local" and \
                    self.snapshot_stage("mind").get("ready"):
                try:
                    hook()
                except Exception as e:  # noqa: BLE001
                    log.warning("after_prove hook failed: %s", e)
        finally:
            with self._lock:
                self._proving = False
        return self.snapshot()

    def prove_async(self, stages=STAGES) -> bool:
        """Start prove_all on a thread. Returns False if one is running."""
        with self._lock:
            if self._proving:
                return False
        threading.Thread(target=self.prove_all, args=(stages,),
                         name="voice-prove", daemon=True).start()
        return True

    @property
    def proving(self) -> bool:
        return self._proving

    # ── listeners (the ws session forwards `manifest` frames) ─────────────

    def subscribe(self, fn) -> None:
        with self._lock:
            self._listeners.append(fn)

    def unsubscribe(self, fn) -> None:
        with self._lock:
            self._listeners = [f for f in self._listeners if f is not fn]

    def _notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn(self.snapshot())
            except Exception:
                pass

    # ── rendering ────────────────────────────────────────────────────────

    @staticmethod
    def _noun(stage: str) -> str:
        return {"ear": "ear", "mind": "reasoning", "mouth": "voice"}[stage]

    def snapshot_stage(self, stage: str) -> dict:
        with self._lock:
            st = json.loads(json.dumps(self.stages[stage]))
        p = st["proof"]
        if p["state"] == "proven" and self.is_stale(stage):
            age = int((self._clock() - float(p["at"])) // 60)
            p["state"] = "unproven"
            p["stale"] = True
            st["reason"] = (f"last proven {age} min ago"
                            + (f"; {st['reason']}" if st["reason"] else ""))
        else:
            p["stale"] = False
        if self.mode == "gemini" and stage in ("ear", "mouth"):
            st["effective"] = {"engine": "gemini-live", "device": "cloud"}
            if p["state"] in ("refused", "unproven"):
                p["state"] = "cloud"
                st["reason"] = "Gemini Live; proven by the live connection, not a local probe"
                st["action"] = None
        st["stage"] = stage
        st["ready"] = p["state"] == "proven"
        return st

    def snapshot(self) -> dict:
        stages = {k: self.snapshot_stage(k) for k in STAGES}
        local_ready = all(stages[k]["ready"] for k in STAGES)
        cloud_ready = stages["mind"]["ready"]
        mind_eff = stages["mind"].get("effective") or {}
        contract = dict(mind_eff.get("contract") or {})
        if self.mode == "gemini":
            contract = self.cloud_contract(contract)
        # Phase 1: a GPU row shows its idle-unload countdown while its worker
        # is resident, and the admission/eviction notices ride along so the
        # card and the HUD can show them once (§3.2 rule 4, §7).
        notices = []
        try:
            from agent_friday.services import voice_workers
            for k in ("ear", "mouth"):
                e = voice_workers.held(k)
                if e is not None and hasattr(e, "worker"):
                    stages[k]["idle_remaining_s"] = int(e.worker.idle_remaining_s())
                    stages[k]["worker_pid"] = getattr(e.worker.proc, "pid", None)
            notices = list(voice_workers.NOTICES)
        except Exception:
            pass
        return {
            "mode": self.mode,
            "stages": stages,
            "ready": cloud_ready if self.mode == "gemini" else local_ready,
            "proving": self._proving,
            "contract": contract,
            "idle_unload_s": getattr(self, "idle_unload_s", 600),
            "notices": notices,
            "description": self.describe_for_model(),
            "at": self._clock(),
        }

    @staticmethod
    def cloud_contract(local_contract: dict) -> dict:
        try:
            from agent_friday.services.voice_engine import _voice_tool_names
            names = list(_voice_tool_names())
        except Exception:
            names = []
        return {"tools": names, "native_tools": len(names),
                "ask_friday": "ask_friday" in names,
                "knowledge_graph": bool(local_contract.get("knowledge_graph")) and "ask_friday" in names,
                "memory": bool(local_contract.get("memory")) and "ask_friday" in names,
                "fits": True, "window": None,
                "line": (f"{len(names) - 1} native tools + ask_friday → your context "
                         "is reached through Friday's local model"
                         if "ask_friday" in names else
                         f"{len(names)} native tools; no path to your context")}

    def describe_for_model(self) -> str:
        """The model's self-description, generated from the proofs (§3.1 rule 3).

        A stage that is not proven is described AS unproven; no engine is
        named for a refused stage, so the model cannot claim a pipeline the
        manifest does not hold.
        """
        s = {k: self.snapshot_stage(k) for k in STAGES}

        def _name(k):
            eff = s[k].get("effective") or {}
            e = eff.get("engine") or "?"
            if e == "faster-whisper":
                e = f"faster-whisper {eff.get('model') or ''}".strip()
            elif e == "seat":
                e = f"{_seat_label(eff.get('model'))} running locally"
            elif e == "kokoro":
                e = f"Kokoro ({eff.get('voice') or 'af_heart'})"
            elif e == "piper":
                e = "Piper"
            dev = eff.get("device")
            if dev in ("cuda", "gpu"):
                e += " on the GPU"
            elif dev == "cpu":
                e += " on the CPU"
            return e

        if self.mode == "gemini":
            mind = s["mind"]
            if mind["ready"]:
                tail = ("Questions about Stephen's own notes, memory or knowledge "
                        "graph are answered by his local model through the "
                        "`ask_friday` tool.")
            else:
                tail = ("Friday's local model is NOT available right now, so you "
                        "have no path to Stephen's notes, memory or knowledge "
                        "graph; say so plainly if asked.")
            return ("You are Gemini Live; the microphone audio is sent to Google. "
                    + tail)

        parts = []
        unproven = []
        for k, label in (("ear", "Your ears are"), ("mind", "your reasoning is"),
                         ("mouth", "your voice is")):
            if s[k]["ready"]:
                parts.append(f"{label} {_name(k)}")
            else:
                st = s[k]["proof"]["state"]
                unproven.append(f"{self._noun(k)} ({st}"
                                + (": " + s[k]["reason"] if s[k]["reason"] else "")
                                + ")")
        text = ""
        if parts:
            text = ", ".join(parts) + ". "
        if unproven:
            text += ("The following has NOT been proven to work this session: "
                     + "; ".join(unproven) + ". Do not describe it as running.")
        else:
            text += "All three run on this machine; nothing leaves it."
        # Measured 2026-09-18 on the reference machine: given its engine ids
        # in this line, the 4.6B seat parroted them into replies ("Gemma 4 is
        # handling that for you", "I'm running on gemma4:e2b-fridayweaver-1.0
        # for this one", and once a bare "Gemma 4"). The facts stay so the
        # model cannot claim a pipeline it does not have; the register is
        # set explicitly so they do not leak into every answer.
        text += (" You are Friday. These are facts for you to know, not to "
                 "announce: never name these engines or models in a reply "
                 "unless the user asks what you are running on.")
        return text.strip()


# ═══════════════════════════════════════════════════════════════════════════
#  Process singleton
# ═══════════════════════════════════════════════════════════════════════════

_MANIFEST = None
_MANIFEST_LOCK = threading.Lock()


def get_manifest() -> VoiceManifest:
    global _MANIFEST
    with _MANIFEST_LOCK:
        if _MANIFEST is None:
            _MANIFEST = VoiceManifest()
        return _MANIFEST


def reset_for_tests() -> None:
    global _MANIFEST
    with _MANIFEST_LOCK:
        _MANIFEST = None
