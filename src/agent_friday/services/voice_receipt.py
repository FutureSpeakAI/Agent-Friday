"""
Agent Friday — Local voice receipts (R0 of docs/design/active/local-voice-repair-and-native-audio.md)

Local voice had no log. Its entire diagnostic output was two bare ``print()``
calls landing in a 136 MB unrotated ``server_stderr.log`` that the repo
elsewhere describes as lost, while every other subsystem writes to
``friday.log``. The consequence was that a local voice failure could not be
diagnosed after the fact — including a hang that "never speaks, never errors,
and never times out."

This module is the receipt channel. It writes into the logger tree that
already exists (``friday.local_voice``), so records land in ``~/.friday/friday.log``
under its existing ``RotatingFileHandler(10 MB x 3)`` and format. **No new log
file and no new rotation policy** — a receipt written where nobody reads it is
not a receipt.

Two record shapes:

* **The routing receipt** (:func:`log_route`) — emitted once per voice session,
  at session start. Which tier was *requested*, which was *selected*, and why
  they differ. This is the ``PREFERRED(t) -> ACTIVE(t')`` pair from
  ``voice-system-spec.md`` §7.1, written down at the moment the decision is made
  rather than reconstructed later from behaviour.

* **The turn receipt** (:class:`TurnReceipt`) — one structured record per turn,
  at turn end, carrying the five stage timestamps
  (VAD open / VAD close / input complete / first brain token / first audio out)
  and an ``outcome``.

**Silence is an outcome.** A turn that produces no audio leaves a record saying
so (``outcome=silent``) rather than leaving no record at all. That is the direct
answer to voice-mode.md's "Silence is the one failure mode audio cannot
express" — the class of failure that otherwise leaves no trace.

**No user content, ever.** The receipt records *routing*, not conversation:
durations, byte counts, model identifiers, reason codes. No transcript text, no
reply text, no audio. A diagnostic log that accumulates conversation content is
a privacy liability in a product whose premise is local-first, and it would be
read by anyone the user ever sends a log to.
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
import time

log = logging.getLogger("friday.local_voice")

_turn_counter = itertools.count(1)
_counter_lock = threading.Lock()


def _next_turn_id() -> int:
    with _counter_lock:
        return next(_turn_counter)


def _fmt(value) -> str:
    """Render one receipt field. ``None`` becomes ``-`` so a missing stage is
    visibly missing rather than silently absent from the line."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.0f}"
    return str(value)


# ═══════════════════════════════════════════════════════════════════════════
#  The routing receipt — one per session, at selection time
# ═══════════════════════════════════════════════════════════════════════════

def log_route(session_id, requested, selected, reason="", detail=""):
    """Record which tier was asked for, which was chosen, and why they differ.

    ``requested`` is the raw ``voice_engine`` setting as the user wrote it
    (``local`` / ``local-gpu`` / ``auto`` / ...), NOT a normalised tier — the
    whole point is to be able to see that a user who chose ``local-gpu`` got
    ``cpu``, which is invisible once both have been flattened to a tier name.

    ``reason`` is populated exactly when the two disagree. Empty ``reason`` with
    disagreeing tiers is itself diagnostic: it means a degrade happened on a
    path that does not explain itself, which is the ``auto`` case that
    ``resolve_tier`` deliberately leaves quiet.
    """
    try:
        agreed = str(selected).lower() in str(requested).lower() or (
            str(requested).lower() in ("local", "", "none") and selected == "cpu")
        line = (f"route session={_fmt(session_id)} requested={_fmt(requested)} "
                f"selected={_fmt(selected)}")
        if reason:
            line += f" reason={json.dumps(str(reason)[:400])}"
        if detail:
            line += f" detail={json.dumps(str(detail)[:400])}"
        if agreed and not reason:
            log.info(line)
        else:
            # A tier the user did not ask for is a WARNING, not an INFO. It is
            # the single most common cause of "voice was worse tonight" and it
            # should be findable by severity, not only by grep.
            log.warning(line)
    except Exception:
        pass


def log_readiness(component, ready, checked_path=None, missing=None, detail=""):
    """Record a readiness verdict AND the path it consulted.

    The specific defect this closes: a user sees "local voice unavailable" and
    the log does not say *which* directory was empty. Naming the path that was
    actually checked is the one sentence that ends this class of investigation,
    and it matters more than usual here because the asset root is overridable
    (``voice_assets_dir()`` under OS mode) — so two machines with identical
    settings can consult different directories and produce identical, useless
    diagnostics.
    """
    try:
        line = f"readiness {component}={'ok' if ready else 'NOT-READY'}"
        if checked_path is not None:
            line += f" checked={json.dumps(str(checked_path))}"
        if missing:
            line += f" missing={json.dumps(str(missing)[:300])}"
        if detail:
            line += f" detail={json.dumps(str(detail)[:300])}"
        (log.info if ready else log.warning)(line)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
#  The turn receipt — one per turn, at turn end
# ═══════════════════════════════════════════════════════════════════════════

class TurnReceipt:
    """Accumulates one turn's routing facts and emits a single record at the end.

    Usage is deliberately failure-tolerant: every method swallows its own
    exceptions. Instrumentation that can break the thing it instruments is worse
    than no instrumentation, and this sits directly in the voice turn path.

    Stages are marked as they happen::

        r = TurnReceipt(session_id, tier="cpu", asr_model="small", tts_voice="amy")
        r.mark("vad_open") ; r.mark("vad_close")
        r.mark("input_complete", audio_ms=1840)
        r.mark("first_brain_token")
        r.mark("first_audio_out")
        r.done()            # outcome inferred: served | silent

    ``done()`` infers ``silent`` when no audio byte was ever emitted, so the
    caller does not have to remember to report the failure mode that is, by
    definition, the one nobody notices.
    """

    STAGES = ("vad_open", "vad_close", "input_complete",
              "first_brain_token", "first_audio_out")

    def __init__(self, session_id=None, tier=None, asr_model=None,
                 tts_voice=None, brain_seat=None, listening_mode="transcribe"):
        self.turn_id = _next_turn_id()
        self.session_id = session_id
        self.tier = tier
        self.asr_model = asr_model
        self.tts_voice = tts_voice
        self.brain_seat = brain_seat
        self.listening_mode = listening_mode
        self.audio_ms_in = None
        self.chars_out = 0
        self.audio_bytes_out = 0
        # Stage-level failure flags. A turn can reach `served` with a broken
        # sentence in the middle of it, so these are recorded separately from
        # `outcome` rather than collapsed into it.
        self.tts_error = False
        self.brain_failed = False
        # Clean-sheet §4.4 / §6.2: what the seat actually prefilled this turn
        # (llama-server `timings.prompt_n`; < 2,000 on turn 2 is the
        # prefix-cache acceptance), how many clauses the mouth spoke, and
        # when the first clause was complete (ms since the turn began).
        self.prefill_tokens = None
        self.clauses = None
        self.first_clause_ms = None
        self._t0 = time.perf_counter()
        self._stamps = {}
        self._emitted = False

    # ── stage marking ──────────────────────────────────────────────────────

    def mark(self, stage, audio_ms=None):
        """Stamp ``stage`` at now, as milliseconds since the turn began.

        Relative rather than absolute on purpose: the question a receipt is read
        to answer is "where did the time go", and wall-clock timestamps make the
        reader do the subtraction. ``friday.log``'s own line prefix already
        carries the absolute time of the record.
        """
        try:
            if stage not in self.STAGES:
                return
            self._stamps[stage] = (time.perf_counter() - self._t0) * 1000.0
            if audio_ms is not None:
                self.audio_ms_in = float(audio_ms)
        except Exception:
            pass

    def count_audio_out(self, n_bytes):
        try:
            self.audio_bytes_out += int(n_bytes or 0)
        except Exception:
            pass

    def count_text_out(self, n_chars):
        try:
            self.chars_out += int(n_chars or 0)
        except Exception:
            pass

    def set(self, **fields):
        """Late-bind facts that are only known once the turn has run — most
        importantly ``brain_seat``, which is resolved inside the turn rather
        than at session start, and ``tier``, which ``ensure_ready`` may change
        underneath the session."""
        try:
            for k, v in fields.items():
                if hasattr(self, k):
                    setattr(self, k, v)
        except Exception:
            pass

    # ── emission ───────────────────────────────────────────────────────────

    def done(self, outcome=None, code="", detail=""):
        """Emit the receipt exactly once.

        ``outcome`` defaults to ``served`` when audio was produced and
        ``silent`` when it was not. An explicit outcome (``timeout``,
        ``error``, ``aborted``) always wins.
        """
        if self._emitted:
            return
        self._emitted = True
        try:
            if outcome is None:
                outcome = "served" if self.audio_bytes_out > 0 else "silent"
            total_ms = (time.perf_counter() - self._t0) * 1000.0
            parts = [
                f"turn session={_fmt(self.session_id)}",
                f"turn_id={self.turn_id}",
                f"tier={_fmt(self.tier)}",
                f"mode={_fmt(self.listening_mode)}",
                f"outcome={outcome}",
            ]
            if code:
                parts.append(f"code={code}")
            for stage in self.STAGES:
                parts.append(f"t_{stage}={_fmt(self._stamps.get(stage))}")
            parts += [
                f"total_ms={total_ms:.0f}",
                f"audio_ms_in={_fmt(self.audio_ms_in)}",
                f"chars_out={self.chars_out}",
                f"audio_bytes_out={self.audio_bytes_out}",
                f"asr={_fmt(self.asr_model)}",
                f"tts={_fmt(self.tts_voice)}",
                f"brain={_fmt(self.brain_seat)}",
            ]
            if self.tts_error:
                parts.append("tts_error=1")
            if self.brain_failed:
                parts.append("brain_failed=1")
            if self.prefill_tokens is not None:
                parts.append(f"prefill_tokens={_fmt(self.prefill_tokens)}")
            if self.clauses is not None:
                parts.append(f"clauses={_fmt(self.clauses)}")
            if self.first_clause_ms is not None:
                parts.append(f"first_clause_ms={_fmt(self.first_clause_ms)}")
            if detail:
                parts.append(f"detail={json.dumps(str(detail)[:300])}")
            line = " ".join(parts)
            # A silent turn is the failure this whole module exists to make
            # visible, so it is never logged at the level that gets filtered
            # out first.
            if outcome == "served":
                log.info(line)
            elif outcome in ("aborted",):
                log.info(line)
            else:
                log.error(line)
        except Exception:
            pass
