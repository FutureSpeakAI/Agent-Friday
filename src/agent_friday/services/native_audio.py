"""
Agent Friday - native audio-in as a second listening mode (Phase N of
docs/design/active/local-voice-repair-and-native-audio.md).

This module is the DECISION layer for `understand` mode. It answers one
question - "may native audio-in serve this turn, and if not, why not?" - and it
answers it as data. It does not capture audio, does not load a seat, and does
not change any mode. That separation is deliberate: C2 of the design
("surfaces and offers, never substitutes") is a property that is easy to assert
and easy to violate, and the way to make it structural is to give the deciding
code no capability to act on its own verdict.

Every refusal here returns a `{code, user_message, action}` triple in the shape
`voice-system-spec.md` section 8 already uses, carrying one of the section 6.1
codes. Nothing in this module switches a user from `understand` to
`transcribe`; it returns the offer and the caller presents it.

Why this is a mode and not a tier (section 5.2): native audio is WORSE at
transcription than the pipeline Friday already has - 13.15% word error rate
against faster-whisper's 11.48%, and roughly 41% against 16% on noisy audio.
What it buys is audio-to-reasoning in a single pass: prosody, hesitation,
emphasis, intent - everything destroyed the moment speech becomes a string.
That is a different job, so it gets a different mode the user chooses, never a
silent substitution inside dictation.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os

log = logging.getLogger("friday.native_audio")

# --- The two listening modes (section 5.3) ----------------------------------

MODE_TRANSCRIBE = "transcribe"
MODE_UNDERSTAND = "understand"
LISTENING_MODES = (MODE_TRANSCRIBE, MODE_UNDERSTAND)

#: The default is `transcribe`, always, on a fresh install and after any
#: upgrade (section 5.5). Native audio is never pre-selected. A constant rather
#: than a settings default so "never auto-selected" is directly testable.
DEFAULT_LISTENING_MODE = MODE_TRANSCRIBE

#: The registry capability keyword, sibling to the cloud spec's `duplex`
#: (section 5.4). Declared by the brain-side provider that serves audio-in,
#: because that is where the capability physically lives. `local-voice-lite`
#: and `nvidia-nemo` keep ["asr", "tts"] unchanged.
CAPABILITY_AUDIO_IN = "audio-in"

# --- Hard limits from the model family (I4) ---------------------------------

#: Hard per-request ceiling. Not a soft budget: at the boundary capture closes
#: cleanly and the turn is served. Windows are never stitched - stitching would
#: preserve the words and destroy the cross-utterance shape that is the entire
#: motivation for the mode (section 5.6).
AUDIO_INPUT_CEILING_SEC = 30.0

#: 16 kHz mono. Anything else is a caller bug, not a user error.
AUDIO_INPUT_RATE_HZ = 16000
AUDIO_INPUT_CHANNELS = 1

#: Gemma 4 native audio exists on E2B, E4B and 12B only (I4).
AUDIO_CAPABLE_MODEL_MARKERS = ("e2b", "e4b", "12b")

#: The multimodal-tower conversion bug was fixed in llama.cpp PR #24118 on
#: 2026-06-04, so any GGUF built before 2026-06-05 hears incorrectly (I3).
GGUF_AUDIO_FIX_DATE = _dt.date(2026, 6, 5)
GGUF_AUDIO_FIX_PR = "llama.cpp PR #24118 (2026-06-04)"

#: Audio understanding needs Friday's own model server. Ollama crashes on audio
#: every few requests (I2), and Ollama is what Friday runs for text today.
SUPPORTED_RUNTIME = "llama-server"
REFUSED_RUNTIMES = ("ollama",)

#: Seat admission threshold. Distinct from the Tier-2 contention probe, which
#: deliberately never blocks (V17): Tier 2 degrades to a CPU tier that still
#: works, while native audio's only fallback is a different listening mode -
#: which C2 forbids taking without the user. The probe reports; this refuses
#: and offers. Same VRAM measurement, different policy (section 5.6).
MIN_AUDIO_VRAM_GB = 3.0


def _refusal(code, user_message, action, detail=""):
    """A refusal in the existing {code, user_message, action} shape."""
    return {"available": False, "code": code, "user_message": user_message,
            "action": action, "detail": detail}


def _ok(detail=""):
    return {"available": True, "code": "", "user_message": "",
            "action": "", "detail": detail}


# --- Individual checks ------------------------------------------------------

def check_model_audio_capable(model_id):
    """Is `model_id` one of the Gemma 4 variants that has an audio tower?"""
    mid = str(model_id or "").lower()
    if any(marker in mid for marker in AUDIO_CAPABLE_MODEL_MARKERS):
        return _ok("model %r is audio-capable" % model_id)
    return _refusal(
        "local_audio_unsupported_model",
        "This model can't listen directly. Pick one that can, or use "
        "transcription.",
        "Switch model or mode",
        "Gemma 4 native audio exists on E2B, E4B and 12B only; got %r"
        % (model_id,))


def check_runtime(runtime):
    """Audio-in requires llama-server. Ollama is refused, with its reason."""
    rt = str(runtime or "").strip().lower()
    if any(bad in rt for bad in REFUSED_RUNTIMES):
        return _refusal(
            "local_audio_runtime_unsupported",
            "Audio understanding needs Friday's own model server, not Ollama.",
            "Start or point at llama-server",
            "Ollama crashes on audio input every few requests; the audio seat "
            "must be served by %s." % SUPPORTED_RUNTIME)
    if not rt:
        return _refusal(
            "local_audio_runtime_unsupported",
            "Audio understanding needs Friday's own model server, not Ollama.",
            "Start or point at llama-server",
            "no serving runtime was reported for the audio seat")
    return _ok("runtime %r accepted" % runtime)

def gguf_provenance(path, now=None):
    """Provenance verdict for a candidate GGUF (section 5.7).

    UNKNOWN IS TREATED AS STALE, deliberately. This rests on an inherited fact
    whose failure mode is silent: a pre-fix GGUF produces bad or absent audio
    understanding that is indistinguishable from "this feature does not work."
    The cost of a false positive is one re-download; the cost of a false
    negative is exactly the class of unattributable bug the instrumentation
    work (R0) exists to eliminate.
    """
    built = None
    detail = ""
    try:
        if path and os.path.exists(path):
            built = _dt.date.fromtimestamp(os.path.getmtime(path))
            detail = "file mtime %s" % built.isoformat()
        else:
            detail = "no such file: %r" % (path,)
    except Exception as e:  # an unreadable stat is unknown, therefore stale
        detail = "could not read provenance (%s)" % type(e).__name__

    if built is not None and built >= GGUF_AUDIO_FIX_DATE:
        return _ok("built %s, at or after the audio fix (%s)"
                   % (built.isoformat(), GGUF_AUDIO_FIX_PR))

    return _refusal(
        "local_audio_gguf_stale",
        "This model file was built before the audio fix and won't hear "
        "correctly.",
        "Re-pull the model",
        "%s; the multimodal-tower conversion bug was fixed in %s, so any GGUF "
        "built before %s must be re-pulled. Unknown provenance is treated as "
        "stale." % (detail or "provenance unknown", GGUF_AUDIO_FIX_PR,
                    GGUF_AUDIO_FIX_DATE.isoformat()))


def check_input_format(rate_hz, channels):
    """16 kHz mono, per I4. A caller bug, surfaced rather than resampled."""
    if int(rate_hz or 0) == AUDIO_INPUT_RATE_HZ and \
            int(channels or 0) == AUDIO_INPUT_CHANNELS:
        return _ok("16 kHz mono")
    return _refusal(
        "local_audio_unsupported_model",
        "This audio can't be used for listening in this mode.",
        "Capture at 16 kHz mono",
        "native audio requires %d Hz / %d channel; got %r Hz / %r channel"
        % (AUDIO_INPUT_RATE_HZ, AUDIO_INPUT_CHANNELS, rate_hz, channels))


def remaining_budget_sec(elapsed_sec):
    """Seconds of capture left. Surfaced DURING capture, never discovered at
    the end - a limit that lands after the user has finished their point is a
    limit that has already failed (section 5.6)."""
    try:
        left = AUDIO_INPUT_CEILING_SEC - float(elapsed_sec or 0.0)
    except (TypeError, ValueError):
        left = AUDIO_INPUT_CEILING_SEC
    return max(0.0, round(left, 2))


def check_input_budget(duration_sec):
    """Ceiling verdict for a captured utterance.

    Over the ceiling the offer is "serve this turn as transcription" - offered,
    taken by the user, never applied automatically.
    """
    try:
        dur = float(duration_sec or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur <= AUDIO_INPUT_CEILING_SEC:
        return _ok("%.2fs within the %.0fs ceiling"
                   % (dur, AUDIO_INPUT_CEILING_SEC))
    return _refusal(
        "local_audio_input_too_long",
        "That was longer than audio understanding can take in one turn.",
        "Serve as transcription this turn",
        "utterance %.2fs exceeds the hard %.0fs per-request ceiling; windows "
        "are not stitched because stitching preserves the words and destroys "
        "the cross-utterance shape the mode exists for."
        % (dur, AUDIO_INPUT_CEILING_SEC))

def check_vram_admission(gpu_status=None):
    """Admission check at seat load - refuses and offers (section 5.6).

    A voice mode may never evict a training job or a user-pinned seat. It
    yields; it does not compete.
    """
    info = gpu_status
    if info is None:
        try:
            from agent_friday.services.nemo_voice import gpu_status as _gs
            info = _gs(fresh=True)   # an admission decision: re-measure
        except Exception as e:
            return _refusal(
                "local_audio_no_vram",
                "Not enough free GPU right now - something else is using it.",
                "Use transcription for now",
                "VRAM could not be measured (%s)" % type(e).__name__)
    info = info or {}

    # Where torch and nvidia-smi disagree, admission takes the SMALLER figure.
    # torch counts memory it could obtain by making the driver page other work
    # out; nvidia-smi counts memory genuinely unused. Admitting on the larger
    # number is how a voice mode ends up slowing the training run it promised
    # never to compete with.
    candidates = [v for v in (info.get("vram_free_real_gb"),
                              info.get("vram_free_gb")) if v is not None]
    if not candidates:
        return _refusal(
            "local_audio_no_vram",
            "Not enough free GPU right now - something else is using it.",
            "Use transcription for now",
            "no VRAM figure was reported")
    free = min(float(c) for c in candidates)
    if free >= MIN_AUDIO_VRAM_GB:
        return _ok("%.1fGB free (conservative figure) >= %.1fGB needed"
                   % (free, MIN_AUDIO_VRAM_GB))
    holder = info.get("holder") or info.get("detail") or ""
    return _refusal(
        "local_audio_no_vram",
        "Not enough free GPU right now - something else is using it.",
        "Use transcription for now",
        "%.1fGB genuinely free, %.1fGB needed%s"
        % (free, MIN_AUDIO_VRAM_GB, ("; %s" % holder) if holder else ""))


def seat_evicted_refusal(detail=""):
    """The arbiter evicted the audio seat while `understand` was selected."""
    return _refusal(
        "local_audio_seat_evicted",
        "Friday's listening model was unloaded to make room.",
        "Reload, or switch to transcription",
        detail or "evicted by the residency arbiter")


# --- Composition ------------------------------------------------------------

def understand_mode_availability(model_id=None, runtime=None, gguf_path=None,
                                 gpu_status=None):
    """May `understand` be OFFERED right now, and if not, why not?

    Returns the first refusal in a deliberate order - identity of the model,
    then how it is served, then whether the file can hear, then whether the
    machine has room - so the reason a user meets is the most fundamental one
    rather than whichever check happened to run first.

    This never selects a mode. A caller that receives `available: False` while
    the user has chosen `understand` must SURFACE the refusal and OFFER
    `transcribe`; applying it silently is the substitution C2 forbids.
    """
    for verdict in (check_model_audio_capable(model_id),
                    check_runtime(runtime),
                    gguf_provenance(gguf_path),
                    check_vram_admission(gpu_status)):
        if not verdict["available"]:
            log.info("understand mode unavailable: %s - %s",
                     verdict["code"], verdict["detail"])
            return verdict
    return _ok("audio-in seat admissible")


def resolve_listening_mode(requested, availability):
    """The mode that will actually be used, plus whether an offer is pending.

    `understand` requested against an unavailable path does NOT become
    `transcribe` here. It stays requested, `active` reports the truth, and
    `offer` carries what the user is being asked to accept. The caller renders
    it; nothing in this function can apply it.
    """
    req = str(requested or DEFAULT_LISTENING_MODE).strip().lower()
    if req not in LISTENING_MODES:
        req = DEFAULT_LISTENING_MODE
    if req == MODE_TRANSCRIBE:
        return {"requested": req, "active": MODE_TRANSCRIBE, "offer": None}
    if availability and availability.get("available"):
        return {"requested": req, "active": MODE_UNDERSTAND, "offer": None}
    return {"requested": req, "active": None, "offer": availability}
