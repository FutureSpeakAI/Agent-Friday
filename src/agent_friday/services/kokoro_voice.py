"""
Agent Friday — Kokoro TTS (local, GPU-preferred), alongside Piper

Kokoro-82M is the current quality leader among small open TTS models. It was
named in ``residency_policy.py`` and in the design docs, its weights have been
on this machine since August, and no implementation existed anywhere in ``src/``
until this module. The reason it was never wired up is in the constraint below,
and the reason it is being wired up now is that the constraint is negotiable in
a way it previously was not.

**The constraint, stated honestly.** Kokoro is not usable as a conversational
synthesizer on CPU — it is roughly realtime there, which means a sentence takes
about as long to synthesize as it takes to say, and a voice assistant that
pauses for the length of its own reply before speaking it is not a voice
assistant. On GPU it runs at roughly thirty times realtime in under 1 GB of
VRAM. So this backend **prefers CUDA and says so when it cannot get it** rather
than quietly falling back to a CPU path that will disappoint.

**It degrades visibly, never silently.** ``load()`` raises
:class:`KokoroUnavailable` with a specific, actionable reason — missing package,
no CUDA, no VRAM — and the caller surfaces that and offers Piper. It does not
become Piper on its own. This is the "surfaces and offers, never substitutes"
rule from ``cloud-voice-providers.md`` §6.3, applied inside the local path: a
user who chose Kokoro and got Piper without being told would have no way to
attribute the quality difference, which is the exact class of unattributable
behaviour the receipts work (``services/voice_receipt.py``) exists to end.

**Piper is not replaced.** Piper remains the default and the CPU-capable
fallback: it is the only local synthesizer that runs acceptably on a machine
with no GPU, which is most machines. Kokoro is an addition.

Licensing note, because it will matter if Friday ships and it is easy to miss:
**Piper was relicensed from MIT to GPL-3.0 in October 2025.** Kokoro-82M is
Apache-2.0. A GPL-3.0 dependency in a distributed binary has obligations that an
MIT one does not; nothing in this module changes that, but the person choosing
what ships should be choosing with it in view rather than discovering it during
a license audit.

Interface parity with ``local_voice.PiperTTS`` is exact — ``load(progress)`` and
``synthesize(text) -> 24 kHz PCM16 mono bytes`` — so ``LocalVoiceEngine`` swaps
it in with no change to the ``/ws/voice-local`` contract, the browser audio
plumbing, or the holographic signals. Kokoro synthesizes natively at 24 kHz,
which is exactly the playback rate, so unlike Piper (22.05 kHz) and NeMo
(22.05 kHz) this path needs no resample at all.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from agent_friday.services.local_voice import PLAYBACK_RATE, _module_installed

log = logging.getLogger("friday.kokoro_voice")

#: Kokoro's native output rate. Equal to PLAYBACK_RATE by happy accident, which
#: is asserted rather than assumed: if either ever changes, the resample that is
#: currently unnecessary becomes necessary and this is where that is noticed.
KOKORO_NATIVE_RATE = 24000

# ── Bounds on one utterance ─────────────────────────────────────────────────
#
# Measured on this machine (RTX, 12 GB) with hostile input: long invented
# words, 200 dashes, emoji, stacked diacritics, "Kokoro" x40. Output length
# tracks the phoneme count (0.04-0.08 s of audio per phoneme, 0.28 s at the
# worst), and synthesis ran 2.5x faster than realtime even on CPU. Kokoro did
# not run away on any of it.
#
# What DID hang, for over 40 minutes, was a call waiting on a GPU that another
# Kokoro, a local LLM and a test suite had filled: CUDA work queued behind the
# contention while the Python thread spun inside istftnet.inverse. A user whose
# GPU is oversubscribed gets the same thing, and a voice turn that never ends.
#
# So each utterance gets a time budget and an output cap. Past the budget the
# call is abandoned with a named refusal the voice session already knows how
# to report; the stuck worker cannot be killed (Python has no way to), so
# later calls refuse at once instead of queuing behind it, and the engine
# recovers by itself when that worker returns.
SYNTH_BUDGET_BASE_S = 15.0
SYNTH_BUDGET_PER_CHAR_S = 0.25
MAX_AUDIO_BASE_S = 2.0
MAX_AUDIO_PER_PHONEME_S = 0.5


def synthesis_budget_s(text: str) -> float:
    """Seconds one utterance may take: roughly 5-10x the slowest CPU run
    measured, so a working engine never trips it and a wedged one is cut off."""
    return SYNTH_BUDGET_BASE_S + SYNTH_BUDGET_PER_CHAR_S * len(text or "")

#: Default voice. Kokoro ships a set of named voices ("af_heart", "af_bella",
#: "am_michael", ...); af_heart is its highest-rated English voice.
DEFAULT_KOKORO_VOICE = "af_heart"

#: American English. Kokoro's lang_code selects the g2p frontend.
KOKORO_LANG_CODE = "a"

#: What Kokoro-82M actually wants resident, in GB. It is a small model; the
#: figure is deliberately conservative to leave room for the g2p frontend and
#: CUDA context rather than just the weights.
KOKORO_WORKING_SET_GB = 1.0


class KokoroUnavailable(RuntimeError):
    """Kokoro cannot run, with a reason a user can act on.

    Carries a ``code`` matching the failure taxonomy so the WS layer can send a
    structured error rather than a string, and an ``offer`` naming what the user
    can do instead. Raising this — rather than returning silence or returning
    Piper — is the whole point of the class.
    """

    def __init__(self, code, message, offer="Use Piper (the CPU-capable local voice) instead."):
        super().__init__(message)
        self.code = code
        self.message = message
        self.offer = offer


# ═══════════════════════════════════════════════════════════════════════════
#  Probing — cheap, never imports torch or kokoro
# ═══════════════════════════════════════════════════════════════════════════

def kokoro_deps_status() -> dict:
    """Which Kokoro dependencies are importable, WITHOUT importing them.

    ``misaki`` is Kokoro's g2p frontend and ``espeakng_loader`` is what it falls
    back to for out-of-dictionary words. Both are listed because their absence
    produces a failure at synthesis time rather than at load time — the model
    loads happily and then refuses the first unusual word, which reads as
    "Kokoro is broken" rather than "one frontend package is missing". This is
    the same lesson ``nemo_voice.nemo_deps_installed`` learned about ``nltk``.
    """
    return {
        "kokoro": _module_installed("kokoro"),
        "torch": _module_installed("torch"),
        "misaki": _module_installed("misaki"),
        "espeakng_loader": _module_installed("espeakng_loader"),
        "soundfile": _module_installed("soundfile"),
    }


def kokoro_deps_installed() -> bool:
    d = kokoro_deps_status()
    return bool(d["kokoro"] and d["torch"])


#: Cached outcome of the real-import probe; ``None`` until first asked. The
#: probe costs a torch import (seconds), so it is memoised rather than run on
#: every health poll -- but it IS run before anything claims Kokoro is usable.
_import_check = None
_import_check_lock = threading.Lock()
#: When the last FAILED import was attempted. Success is cached forever; a
#: failure is retried after this cooldown. See kokoro_import_status.
_import_check_at = 0.0
_IMPORT_RETRY_S = 30.0


def _named_dep_is_installed(name) -> bool:
    """Is the module the ImportError blamed actually present?

    If it is, "you installed with --no-deps" is the wrong story, and sending
    someone to reinstall a package they already have wastes their evening.
    """
    if not name:
        return False
    try:
        import importlib.util
        return importlib.util.find_spec(str(name)) is not None
    except Exception:
        return False


def kokoro_import_status(refresh: bool = False) -> dict:
    """Actually import Kokoro, and report whether that worked.

    ``kokoro_deps_status()`` asks whether the *names* resolve, which is what
    ``importlib.util.find_spec`` answers. That is not the same question as
    "will it run", and the difference is not academic: a ``pip install
    --no-deps kokoro`` leaves a package whose name resolves and whose import
    raises ``ModuleNotFoundError: No module named 'spacy'``. A readiness check
    built on ``find_spec`` calls that installation ready, the settings UI
    offers it, and the user discovers otherwise when Friday does not speak.

    SUCCESS is cached forever. FAILURE is retried after a cooldown, and that
    distinction is the whole point of this function's memory. Importing Kokoro
    pulls in torch and transformers, and when several subsystems reach for them
    at once during boot the import can lose a race and raise ``cannot import
    name 'AlbertModel' from 'transformers'`` against a transformers that is
    present and perfectly healthy.

    Caching that answer permanently meant one unlucky moment at startup
    disabled local voice until the process was restarted: ``models_ready()``
    stayed False, the engine resolver refused the local session, and the
    microphone did nothing -- on a server that reports every Kokoro
    dependency installed and the import broken, while a separate process
    imports Kokoro and speaks a sentence fine seconds later.

    Returns ``{"ok", "error", "missing"}``; ``missing`` names the transitive
    module that was absent when the exception tells us.
    """
    global _import_check, _import_check_at
    with _import_check_lock:
        if _import_check is not None and not refresh:
            if _import_check.get("ok"):
                return dict(_import_check)
            if (time.monotonic() - _import_check_at) < _IMPORT_RETRY_S:
                return dict(_import_check)
            # Failed, and the cooldown has passed: ask again rather than
            # repeat an answer that may have been a boot-time accident.
        try:
            from kokoro import KPipeline  # noqa: F401
            res = {"ok": True, "error": "", "missing": ""}
        except BaseException as e:  # noqa: BLE001 - any failure means unusable
            res = {"ok": False,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:160]),
                   "missing": getattr(e, "name", "") or ""}
            log.warning("kokoro import failed: %s (missing=%s)",
                        res["error"], res["missing"] or "?")
        _import_check = res
        _import_check_at = time.monotonic()
        return dict(res)


def kokoro_gpu_status() -> dict:
    """CUDA availability and headroom for Kokoro specifically.

    Reuses ``nemo_voice.gpu_status()`` rather than probing separately, so the
    two GPU consumers cannot disagree about the state of the same card — and so
    Kokoro inherits, for free, the disputed-measurement reporting that gate
    grew: when torch and nvidia-smi disagree materially, this says so instead
    of quoting whichever number is more convenient.
    """
    try:
        from agent_friday.services.nemo_voice import gpu_status
        g = dict(gpu_status())
    except Exception as e:
        return {"cuda": False, "detail": f"GPU probe failed: {str(e)[:100]}",
                "sufficient_for_kokoro": False}
    # Kokoro's bar is far lower than NeMo ASR's, so `sufficient` (which is
    # keyed to MIN_VRAM_GB = 4.0) is the wrong question. Ask Kokoro's own.
    real = g.get("vram_free_real_gb")
    reachable = g.get("vram_free_gb") or 0.0
    basis = real if real is not None else reachable
    g["sufficient_for_kokoro"] = bool(g.get("cuda") and basis >= KOKORO_WORKING_SET_GB)
    g["kokoro_headroom_basis_gb"] = basis
    return g


def kokoro_available() -> bool:
    """True when Kokoro could be *selected* -- the package is present AND it
    actually imports. Not a claim that it will run on the GPU; that is
    ``kokoro_gpu_ready()``.

    The import check is what makes this honest; see ``kokoro_import_status``.
    Removing it turns this function back into a claim about filenames.
    """
    if not kokoro_deps_installed():
        return False
    return bool(kokoro_import_status()["ok"])


def kokoro_gpu_ready() -> bool:
    if not kokoro_available():
        return False
    return bool(kokoro_gpu_status().get("sufficient_for_kokoro"))


def kokoro_health() -> dict:
    """Status block for provider_health / /api/health/full. Never raises."""
    try:
        deps = kokoro_deps_status()
        if not kokoro_deps_installed():
            missing = [k for k in ("kokoro", "torch") if not deps.get(k)]
            return {
                "engine": "local-kokoro", "status": "missing",
                "detail": ("Kokoro voice not installed (missing: "
                           + ", ".join(missing) + "). Install with "
                           "`pip install kokoro misaki espeakng-loader` into "
                           "the server environment."),
                "deps": deps, "available": False, "gpu_ready": False,
            }
        imp = kokoro_import_status()
        if not imp["ok"]:
            _miss = imp.get("missing") or ""
            return {
                "engine": "local-kokoro", "status": "broken",
                # Two very different faults wear the same ImportError, and the
                # advice for one is a waste of an evening for the other. If the
                # module the error blamed is actually installed, this is the
                # boot race, not a `--no-deps` install -- so say that, and say
                # it heals itself, rather than sending someone to reinstall a
                # package they already have.
                "detail": ("Kokoro is installed but fails to import"
                           + (" -- no module named '%s'" % _miss if _miss else "")
                           + (". That module IS installed, so this is very "
                              "likely a transient import race during startup "
                              "rather than a broken install. It is retried "
                              "automatically; if it persists past a restart, "
                              "reinstall with `pip install kokoro misaki "
                              "espeakng-loader`. "
                              if _named_dep_is_installed(_miss)
                              else ". This is what a `--no-deps` install looks "
                                   "like. Reinstall it with its dependencies: "
                                   "`pip install kokoro misaki espeakng-loader`. ")
                           + "Piper is unaffected and remains available."),
                "deps": deps, "import": imp,
                "available": False, "gpu_ready": False,
            }
        g = kokoro_gpu_status()
        if not g.get("cuda"):
            return {
                "engine": "local-kokoro", "status": "down",
                "detail": ("Kokoro is installed but no CUDA GPU is reachable from "
                           "this environment. Kokoro runs at roughly realtime on "
                           "CPU, which is too slow for conversation — so it is "
                           "offered rather than used. Piper remains available."),
                "deps": deps, "gpu": g, "available": True, "gpu_ready": False,
            }
        if not g.get("sufficient_for_kokoro"):
            return {
                "engine": "local-kokoro", "status": "down",
                "detail": (f"Not enough free VRAM for Kokoro "
                           f"({g.get('kokoro_headroom_basis_gb')}GB free; needs "
                           f"about {KOKORO_WORKING_SET_GB}GB)."),
                "deps": deps, "gpu": g, "available": True, "gpu_ready": False,
            }
        detail = "Kokoro GPU voice ready"
        if g.get("vram_measurement_disputed"):
            detail += (" — note: free VRAM cannot be measured reliably right "
                       "now; " + (g.get("contention_detail") or ""))
        return {
            "engine": "local-kokoro", "status": "ok", "detail": detail,
            "deps": deps, "gpu": g, "available": True, "gpu_ready": True,
        }
    except Exception as e:
        return {"engine": "local-kokoro", "status": "error",
                "detail": str(e)[:160], "available": False, "gpu_ready": False}


# ═══════════════════════════════════════════════════════════════════════════
#  The backend — interface-identical to local_voice.PiperTTS
# ═══════════════════════════════════════════════════════════════════════════

def ensure_espeak_fallback() -> dict:
    """Point phonemizer at the bundled espeak-ng, BEFORE misaki.espeak imports.

    This is the fix for a crash that reaches the user as garbled failure rather
    than as anything actionable. `misaki/espeak.py` runs its `set_espeak_library()`
    at IMPORT time, and on Windows that function only looks at one hardcoded
    path: ``C:\\Program Files\\eSpeak NG\\libespeak-ng.dll``. It has no knowledge of
    the ``espeakng_loader`` wheel, which is where pip actually puts the library.
    So on a pip-only install the lookup fails, ``EspeakWrapper._ESPEAK_LIBRARY``
    stays unset, and misaki's G2P is constructed with ``fallback = None``.

    That matters because misaki's English G2P leaves ``token.phonemes is None``
    for any out-of-vocabulary word and relies on the fallback to fill it in
    (``misaki/en.py`` ~676-686). With no fallback, nothing fills it in, and the
    join at ``misaki/en.py:693`` raises
    ``TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'``.
    Measured here: 16 of 26 ordinary words trip it, including "Kubernetes",
    "Nguyen", "Ryzen" and "Ceph". It is not an edge case, it is most speech.

    Setting the library first makes misaki wire its own fallback normally.
    Returns a report dict; never raises.
    """
    out = {"wired": False, "library": "", "data": "", "detail": ""}
    try:
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
    except BaseException as e:  # noqa: BLE001
        out["detail"] = "espeak support not importable (%s)" % type(e).__name__
        return out
    try:
        if not getattr(EspeakWrapper, "_ESPEAK_LIBRARY", None):
            lib = espeakng_loader.get_library_path()
            EspeakWrapper.set_library(lib)
            out["library"] = str(lib)
        else:
            out["library"] = str(EspeakWrapper._ESPEAK_LIBRARY)
        try:
            data = espeakng_loader.get_data_path()
            EspeakWrapper.set_data_path(data)
            out["data"] = str(data)
        except Exception:
            # Older wrappers manage the data path themselves.
            pass
        out["wired"] = bool(getattr(EspeakWrapper, "_ESPEAK_LIBRARY", None))
    except BaseException as e:  # noqa: BLE001
        out["detail"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    return out


def attach_espeak_fallback(pipeline) -> dict:
    """Ensure the pipeline's g2p has a pronunciation fallback, building one if
    kokoro did not.

    ``ensure_espeak_fallback()`` fixes the *library lookup*, and in the common
    case kokoro then wires its own fallback. This function removes the
    remaining dependence on that happening: it inspects the constructed g2p and,
    if the fallback is still ``None``, builds ``EspeakFallback`` directly and
    attaches it.

    The distinction matters because a missing fallback is not a degraded voice,
    it is a crashing one, and only for a specific class of word:
    out-of-dictionary proper nouns. Numbers, times, percentages and paths all
    resolve without it, so the failure hides during casual testing and then
    fires on exactly the words Friday says most -- people's names. Repairing the
    object we hold is not dependent on anyone's import ordering.

    Returns a report; never raises.
    """
    out = {"fallback": False, "repaired": False, "detail": ""}
    g2p = getattr(pipeline, "g2p", None)
    if g2p is None:
        out["detail"] = "pipeline exposes no g2p"
        return out
    if getattr(g2p, "fallback", None) is not None:
        out["fallback"] = True
        return out
    try:
        from misaki import espeak as _mespeak
        g2p.fallback = _mespeak.EspeakFallback(british=False)
        out["fallback"] = True
        out["repaired"] = True
        log.info("kokoro g2p had no fallback; attached EspeakFallback directly")
    except BaseException as e:  # noqa: BLE001
        out["detail"] = "%s: %s" % (type(e).__name__, str(e)[:140])
        log.warning("could not attach espeak fallback: %s", out["detail"])
    return out


class KokoroTTS:
    """Kokoro-82M TTS → 24 kHz PCM16 mono bytes (playback-ready).

    ``allow_cpu`` exists but defaults to False, and that default is the design
    decision: a CPU Kokoro turn is roughly realtime, so enabling it silently
    would trade a visible failure for an invisible one — Friday would simply
    feel sluggish and nobody would know why. A caller that genuinely wants CPU
    Kokoro (an offline render, a test) opts in explicitly.
    """

    def __init__(self, voice=DEFAULT_KOKORO_VOICE, allow_cpu=False, lang_code=KOKORO_LANG_CODE):
        self.voice = voice or DEFAULT_KOKORO_VOICE
        self.allow_cpu = bool(allow_cpu)
        self.lang_code = lang_code or KOKORO_LANG_CODE
        self._pipeline = None
        self._device = None
        self._lock = threading.Lock()
        # The worker of an utterance abandoned at its time budget, while it is
        # still running. See the bounds above.
        self._stuck = None

    # ── loading ────────────────────────────────────────────────────────────

    def _resolve_device(self) -> str:
        """Decide cuda vs cpu, and REFUSE rather than downgrade by default."""
        deps = kokoro_deps_status()
        if not deps.get("kokoro"):
            raise KokoroUnavailable(
                "local_voice_kokoro_missing",
                "Kokoro is not installed in Friday's server environment. The "
                "weights are on this machine, but the `kokoro` package is not "
                "importable from the process that serves voice.")
        if not deps.get("torch"):
            raise KokoroUnavailable(
                "local_voice_kokoro_missing",
                "Kokoro needs PyTorch, which is not importable here.")
        # Presence is not function. `deps` above is a find_spec probe, which
        # answers "does the name resolve" -- a `pip install --no-deps` satisfies
        # it and then raises on import. Ask the real question BEFORE promising a
        # device, so the refusal contract fires here, with a code and an offer,
        # instead of escaping load() as a bare ModuleNotFoundError that carries
        # no reason and no fallback.
        _imp = kokoro_import_status()
        if not _imp["ok"]:
            raise KokoroUnavailable(
                "local_voice_kokoro_missing",
                "Kokoro is installed but fails to import"
                + (" -- no module named '%s'" % _imp["missing"]
                   if _imp["missing"] else "")
                + ". This is what a `--no-deps` install leaves behind. "
                  "Reinstall it with its dependencies: `pip install kokoro "
                  "misaki espeakng-loader`. (%s)" % _imp["error"])
        try:
            import torch
            cuda = bool(torch.cuda.is_available())
        except Exception as e:
            raise KokoroUnavailable(
                "local_voice_kokoro_no_gpu",
                f"Could not query CUDA ({type(e).__name__}).") from e
        if cuda:
            return "cuda"
        if self.allow_cpu:
            log.warning("Kokoro loading on CPU by explicit opt-in — expect "
                        "roughly realtime synthesis, too slow for conversation")
            return "cpu"
        # The visible degrade. Named reason, named offer, no substitution.
        raise KokoroUnavailable(
            "local_voice_kokoro_no_gpu",
            "Kokoro needs a CUDA GPU and this environment's PyTorch build "
            "reports none. Kokoro runs at roughly realtime on CPU, which is "
            "too slow to hold a conversation, so Friday will not quietly use "
            "it that way.")

    def load(self, progress=None):
        if self._pipeline is not None:
            return
        with self._lock:
            if self._pipeline is not None:
                return
            device = self._resolve_device()
            if progress:
                progress(f"Loading Kokoro voice ({self.voice})…")
            # Belt and braces. The probe in _resolve_device should have caught
            # an unusable install, but if anything slips past it the failure
            # must still be WELL-FORMED: a bare ModuleNotFoundError reaches the
            # voice session as an unlabelled crash -- no code, no reason, no
            # offer of Piper. KPipeline() is included because misaki and
            # espeakng_loader are imported during construction, not at the
            # `from kokoro import` line, so that is a second way in.
            # MUST run before kokoro/misaki import, or the g2p fallback is
            # silently None and synthesis crashes on ordinary words.
            esp = ensure_espeak_fallback()
            if not esp["wired"]:
                log.warning("espeak fallback not wired: %s",
                            esp.get("detail") or "unknown reason")
            try:
                from kokoro import KPipeline
                log.info("kokoro load voice=%s device=%s lang=%s espeak=%s",
                         self.voice, device, self.lang_code, esp["wired"])
                pipeline = KPipeline(lang_code=self.lang_code, device=device)
            except KokoroUnavailable:
                raise
            except BaseException as e:  # noqa: BLE001
                _missing = getattr(e, "name", "") or ""
                raise KokoroUnavailable(
                    "local_voice_kokoro_missing",
                    "Kokoro failed to load"
                    + (" -- no module named '%s'" % _missing if _missing else "")
                    + " (%s: %s). Reinstall it with its dependencies: "
                      "`pip install kokoro misaki espeakng-loader`."
                      % (type(e).__name__, str(e)[:120])) from e
            # A pipeline with no g2p fallback is not a working pipeline: it
            # synthesizes common words and raises TypeError on the rest. Refuse
            # with a reason and an offer rather than shipping a voice that dies
            # mid-sentence.
            _fb = attach_espeak_fallback(pipeline)
            if not _fb["fallback"]:
                raise KokoroUnavailable(
                    "local_voice_kokoro_no_g2p_fallback",
                    "Kokoro loaded but its pronunciation fallback is missing, so "
                    "it would crash on any word outside its dictionary (about "
                    "half of ordinary speech). espeak-ng could not be located: "
                    + (_fb.get("detail") or esp.get("detail")
                       or "no espeak library was wired")
                    + ". Installing `espeakng-loader` and `phonemizer-fork` into "
                      "the server environment is what fixes it.")
            self._pipeline = pipeline
            self._device = device

    def _native_rate(self) -> int:
        return KOKORO_NATIVE_RATE

    # ── synthesis ──────────────────────────────────────────────────────────

    def synthesize(self, text: str) -> bytes:
        """Synthesize `text` → 24 kHz PCM16 mono bytes.

        No resample: Kokoro is natively 24 kHz, which is the playback rate. The
        equality is checked rather than assumed, so that a future change to
        either constant surfaces here instead of as pitch-shifted speech.
        """
        if not text or not str(text).strip():
            return b""
        self.load()
        import numpy as np
        text = str(text)
        stuck = self._stuck
        if stuck is not None and stuck.is_alive():
            raise KokoroUnavailable(
                "local_voice_kokoro_busy",
                "Kokoro is still finishing an earlier sentence that ran past its "
                "time limit (the GPU is likely overloaded). This sentence was not "
                "spoken; voice recovers by itself when that one ends.")
        self._stuck = None
        chunks = []
        failure = []
        abandoned = threading.Event()

        # The safety net, kept even though load() now refuses a fallback-less
        # pipeline. Generation runs third-party phonemisation over arbitrary
        # user text, so it can fail in ways no pre-flight check anticipates --
        # and a raw TypeError from inside a dependency reaches the voice session
        # as an unhandled crash: no code, no reason, no offer of Piper. Any
        # failure here becomes a refusal that names itself instead.
        def generate():
            try:
                for _gs, _ps, audio in self._pipeline(text, voice=self.voice):
                    if abandoned.is_set():
                        return                 # nobody is waiting; stop working
                    if audio is None:
                        continue
                    arr = np.asarray(audio, dtype="float32").reshape(-1)
                    cap = int((MAX_AUDIO_BASE_S + MAX_AUDIO_PER_PHONEME_S
                               * len(_ps or "")) * KOKORO_NATIVE_RATE)
                    if arr.size > cap:
                        log.warning("kokoro produced %.1fs of audio for %d phonemes; "
                                    "cut to %.1fs", arr.size / KOKORO_NATIVE_RATE,
                                    len(_ps or ""), cap / KOKORO_NATIVE_RATE)
                        arr = arr[:cap]
                    if arr.size:
                        chunks.append(arr)
            except BaseException as e:  # noqa: BLE001
                failure.append(e)

        budget = synthesis_budget_s(text)
        worker = threading.Thread(target=generate, name="kokoro-synth", daemon=True)
        worker.start()
        worker.join(timeout=budget)
        if worker.is_alive():
            abandoned.set()
            self._stuck = worker
            log.error("kokoro synthesis of %d chars passed its %.0fs budget; "
                      "abandoned (device=%s)", len(text), budget, self._device)
            raise KokoroUnavailable(
                "local_voice_kokoro_timeout",
                "Kokoro took longer than %.0f seconds for one sentence, so it was "
                "not spoken. That usually means the GPU is overloaded by other "
                "work. Nothing was sent anywhere." % budget)
        if failure:
            e = failure[0]
            if isinstance(e, KokoroUnavailable):
                raise e
            log.error("kokoro synthesis failed on %d chars: %s: %s",
                      len(text), type(e).__name__, str(e)[:160], exc_info=e)
            raise KokoroUnavailable(
                "local_voice_kokoro_synthesis_failed",
                "Kokoro failed while speaking this text (%s: %s). Nothing was "
                "sent anywhere." % (type(e).__name__, str(e)[:120])) from e
        if not chunks:
            log.warning("kokoro produced no audio for %d chars", len(text))
            return b""
        wav = np.concatenate(chunks)
        wav = np.clip(wav, -1.0, 1.0)
        pcm = (wav * 32767.0).astype("<i2").tobytes()
        if self._native_rate() != PLAYBACK_RATE:  # pragma: no cover - guard
            from agent_friday.services.local_voice import _resample_pcm16
            pcm = _resample_pcm16(pcm, self._native_rate(), PLAYBACK_RATE)
        return pcm
