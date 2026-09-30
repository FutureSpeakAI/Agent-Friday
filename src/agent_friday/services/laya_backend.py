"""A Laya scorer for services/decisions.py, and a shadow mode to prove it out.

WHAT THIS IS FOR

`dissent_gate.classify_severity` decides whether an action needs the owner's
sign-off - including sending mail as them. It is a scan for
~40 substrings. Its own docstring records the failure it could not avoid:
"spend" and "order " are hard markers and also ordinary nouns, so "Analyze our
spend trends" gated, and the fix was a hand-written regex for leading drafting
verbs. That is a keyword classifier accumulating patches.

Laya is a small typed-decision encoder (ModernBERT-large, 421M) that answers a
fixed question over a state and returns calibrated probabilities. Against the
severity question it takes ~300 ms on CPU, and it
answers the two patched cases correctly without the patches.

HOW IT IS WIRED, AND WHY IT CHANGES NOTHING YET

Registered as the `laya` backend in `decisions.py`, which is NOT the default.
The default stays `keyword` until there is a number, per that module's own
argument. What this module adds on top is SHADOW MODE:

    FRIDAY_DECISION_SHADOW=laya

With that set, `keyword` still decides - the verdict the user experiences is
byte-identical - and Laya scores the same state alongside it, on a background
thread, with both answers written to the decision log. Disagreements become
data instead of an argument.

That ordering is deliberate. The alternative - switch the backend and watch -
puts an unmeasured model in front of an irreversible action, and the first
evidence it was wrong would be a sent email.

THREE THINGS THAT MUST STAY TRUE

  * It never blocks a verdict. Loading takes ~42 s the first time; that
    happens on a warm-up thread, and any decision arriving before the model is
    ready is answered by `keyword` rather than waiting. Once loaded, an
    answer is waited for at most `_SCORE_TIMEOUT_S`; a slower one (a laptop
    CPU) is answered by `keyword` too, and counted in `status()`.
  * It never raises into the gate. `decisions.decide` already falls back to
    `keyword` on a backend exception, and shadow scoring is fire-and-forget.
  * It runs on CPU. bonsai2 owns ~10 GB of the 12 GB card. A decision that
    happens before an action can afford 300 ms; taking VRAM from the model
    that does the actual work would be a bad trade for a 0.4B encoder.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import weakref
from collections import OrderedDict
from typing import Any, Dict, Optional

_log = logging.getLogger("friday.laya")

#: transformers deadlocks probing for TensorFlow in a frozen build, and the
#: xet transfer backend aborts natively (see services/crash_forensics.py).
#: Both are set before any import of laya, not after.
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

MODEL_ID = "convaiinnovations/laya"

#: The question, phrased as the gate's own rule rather than a generic one.
#: `choice` (not `noul`) because it returns per-option probabilities, which is
#: what any later calibration or abstain band needs. A bare yes/no would throw
#: that away, which is the exact mistake sensitivity_classifier makes when it
#: computes a cosine similarity and keeps only the thresholded tier.
SEVERITY_QUESTION: Dict[str, Dict[str, Any]] = {
    "severity": {
        "type": "choice",
        "instructions": ("Does this action reach outside the machine, spend "
                         "money, or change something that cannot be undone?"),
        "criteria": {
            "hard": ("sends, posts, publishes, emails, deletes, pays, orders, "
                     "or otherwise acts on the outside world or irreversibly"),
            "soft": ("internal only: reading, analysing, summarising, "
                     "drafting, searching, or planning"),
        },
    },
}

_agent = None
_agent_lock = threading.Lock()
#: Serialises the ML-stack import against any other thread doing the same.
#: See `_load_now` - transformers 5.x lazy namespace + concurrent boot import.
from agent_friday.services.ml_imports import ML_IMPORT_LOCK, ML_ROOTS  # noqa: E402
from agent_friday.user_errors import ExceptionText, exception_text
_IMPORT_LOCK = ML_IMPORT_LOCK   # shared with every ML importer (services/ml_imports)
_load_error: Optional[str] = None
_loading = False

#: A FAILED load is retried, a few times, slowly. It used to be terminal.
#:
#: A boot-time load can raise `ImportError: cannot import name 'AutoTokenizer'
#: from 'transformers'` while a fresh interpreter imports it fine seconds
#: later: a boot-time race and nothing more. A terminal `_load_error` would
#: leave the no-retry guard in `union_backend` (keyed on it) holding the gate
#: keyword-only for the rest of the process's life.
#:
#: That is the wrong shape of failure. A missing checkpoint SHOULD stay
#: refused - retrying it per approval would be a thread per decision. A
#: transient one should heal on its own, because the alternative is a feature
#: that is silently off until a human happens to read a status line.
#:
#: Bounded on both axes: at most `_MAX_LOAD_ATTEMPTS`, never closer together
#: than `_RETRY_AFTER_S`. A genuinely absent model costs five slow attempts
#: and then stops for good.
_MAX_LOAD_ATTEMPTS = 5
_RETRY_AFTER_S = 120.0
#: How long the FIRST load waits for startup to finish importing the same ML
#: stack. Measured: Friday's boot settles well inside this. Cheap insurance -
#: the gate is keyword-only meanwhile, which is its pre-Laya behaviour.
_BOOT_SETTLE_S = 45.0
_load_attempts = 0
_last_attempt_ts = 0.0

#: How long a decision waits for Laya's answer before taking the keyword half
#: of the union. ~300 ms on a desktop CPU; a laptop CPU can take several times
#: that, and the gate asks inside a chat turn. Past this bound the decision is
#: answered exactly as it is while the model loads, and the miss is counted so
#: the Settings panel can say the second opinion is too slow on this PC.
_SCORE_TIMEOUT_S = 2.5
#: At most this many scorings in flight. A call that finds them all busy (a
#: model still grinding on states it was handed earlier) does not queue behind
#: them; it takes the keyword half at once.
_MAX_SCORING = 2
_scoring = threading.BoundedSemaphore(_MAX_SCORING)
_SCORING_ADMISSION_LOCK = threading.Lock()
_SCORING_ADMISSIONS = weakref.WeakKeyDictionary()
_slow_answers = 0
_last_slow_ts: Optional[float] = None


# ---------------------------------------------------------------------------
#  LOADING
# ---------------------------------------------------------------------------

def is_ready() -> bool:
    return _agent is not None


# ---------------------------------------------------------------------------
#  CALIBRATION GUARD
# ---------------------------------------------------------------------------
#
# laya 0.3.5 clamps fitted temperatures to [0.5, 5.0], because the shipped
# `choice:11+` bucket is 0.1006: it sharpens logits about tenfold and publishes
# a coin flip as near-certainty. Verified against every cached snapshot of this
# checkpoint -- all four carry 0.1006, and 0.3.5 applies 0.5 instead.
#
# Friday is not exposed to that bucket. Its only question is a two-option
# choice, so it lands in `choice:2` at 1.906: inside the range, and softening
# rather than sharpening. Nothing here gates on the confidence number either,
# and temperature is monotonic, so it could not move the answer even if it were
# wrong.
#
# This is therefore a guard on a position that is currently comfortable, against
# the two ways it could stop being so without anyone noticing: a future
# checkpoint distorting the bucket Friday DOES use, or this question growing
# past ten options into the bucket that is distorted today. In both cases the
# clamp would keep working correctly and nobody would hear about it, because
# laya reports through `warnings` and nothing in Friday was listening.

#: What laya 0.3.5 considers a usable temperature. Mirrored rather than imported
#: so this module can describe the range without importing the ML stack (see the
#: module docstring: importing laya is expensive and deliberately lazy).
TEMP_MIN, TEMP_MAX = 0.5, 5.0

_calibration_warnings = []


def reset_calibration_warnings():
    _calibration_warnings.clear()


def calibration_warnings():
    """Warnings laya raised while loading the checkpoint, as plain strings."""
    return list(_calibration_warnings)


def capture_load_warnings(load):
    """Run `load()` and keep any calibration warning it raises.

    laya warns through `warnings.warn(..., RuntimeWarning)` when it clamps a
    shipped temperature. That reaches stderr at best and is swallowed entirely
    under a warning filter, so the one moment the checkpoint tells us its
    calibration is off was the one moment nothing was listening. A load failure
    still propagates: this captures warnings, it does not handle errors.
    """
    import warnings
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent = load()
    for w in caught:
        text = str(w.message)
        if "temperature" in text.lower() or "clamp" in text.lower():
            _calibration_warnings.append(text)
            _log.warning("laya calibration: %s", text)
    return agent


def question_bucket():
    """The calibration bucket Friday's own question falls into.

    Derived from SEVERITY_QUESTION rather than written down, so a question that
    grows an option changes this answer instead of quietly invalidating a
    hardcoded string -- which is precisely how this gate would wander into
    `choice:11+` without anybody editing this file.
    """
    q = (SEVERITY_QUESTION or {}).get("severity") or {}
    qtype = str(q.get("type") or "choice")
    k = len(q.get("criteria") or {})
    size = "2" if k <= 2 else "3-5" if k <= 5 else "6-10" if k <= 10 else "11+"
    return "%s:%s" % (qtype, size)


def calibration_report(agent=None):
    """Is the calibration behind THIS gate's confidence trustworthy?

    `ok` is False only when the bucket Friday actually uses was shipped outside
    the usable range. Other buckets being clamped is reported and is not a
    fault: `choice:11+` is clamped on every build of this checkpoint and Friday
    never asks an eleven-option question.

    Never raises. An agent that does not expose the attributes reports
    `known: False` rather than an alarm -- not being able to tell is not the
    same as being wrong, and this must never be why a gate stops working.
    """
    bucket = question_bucket()
    out = {"bucket": bucket, "known": False, "ok": True, "in_range": True,
           "temperature": None, "shipped": None, "clamped": {},
           "range": [TEMP_MIN, TEMP_MAX], "warnings": calibration_warnings(),
           "detail": "calibration not reported by this build of laya"}
    a = agent if agent is not None else _agent
    if a is None:
        out["detail"] = "laya not loaded"
        return out
    raw = getattr(a, "temperature_by_options_raw", None)
    applied = getattr(a, "temperature_by_options", None)
    if not isinstance(raw, dict) or not isinstance(applied, dict):
        return out
    out["known"] = True
    for key, val in sorted(raw.items()):
        try:
            shipped, used = float(val), float(applied.get(key, val))
        except (TypeError, ValueError):
            continue
        if shipped != used:
            out["clamped"][key] = {"shipped": shipped, "applied": used}
    if bucket in raw:
        try:
            out["shipped"] = float(raw[bucket])
            out["temperature"] = float(applied.get(bucket, raw[bucket]))
        except (TypeError, ValueError):
            return out
        out["in_range"] = TEMP_MIN <= out["shipped"] <= TEMP_MAX
        out["ok"] = out["in_range"]
        if out["ok"]:
            out["detail"] = ("%s temperature %.4g, within [%g, %g]"
                             % (bucket, out["temperature"], TEMP_MIN, TEMP_MAX))
        else:
            out["detail"] = (
                "%s was fitted at %.4g, outside [%g, %g]; laya is applying "
                "%.4g instead. Confidence from this gate is UNCALIBRATED. The "
                "hard/soft answer is unaffected -- temperature cannot change "
                "which option wins, and the union only ever adds a card."
                % (bucket, out["shipped"], TEMP_MIN, TEMP_MAX,
                   out["temperature"]))
    else:
        out["detail"] = ("%s is not calibrated separately; the default "
                         "temperature applies" % bucket)
    return out


def status() -> dict:
    """Honest state, for the settings UI and for `friday doctor`.

    Reports `loading` distinctly from `not started` so a user who sees
    keyword verdicts during warm-up is not told the model is broken.
    """
    return {
        "model": MODEL_ID,
        "ready": _agent is not None,
        "loading": bool(_loading),
        "error": _load_error,
        "shadow": shadow_backend(),
        "device": "cpu",
        "engine": getattr(_agent, "_friday_engine", None),
        "threads": getattr(_agent, "_friday_threads", None),
        # Decisions Laya did not answer within _SCORE_TIMEOUT_S; each was
        # decided by the keyword scan alone.
        "slow_answers": int(_slow_answers),
        "last_slow_ts": _last_slow_ts,
        "score_timeout_s": _SCORE_TIMEOUT_S,
        # Whether the confidence this gate reports can be believed,
        # and whether the checkpoint shipped a temperature that had
        # to be clamped.
        "calibration": calibration_report(),
        # Repeated questions answered from memory instead of rescored.
        "answer_cache": answer_cache_stats(),
        # Union questions the keyword scan answered alone, by reason.
        "missed": dict(_missed),
        "last_missed_ts": _last_missed_ts,
        "last_missed_reason": _last_missed_reason,
    }


#: The ML stack, in dependency order. Purged together or not at all.
_ML_ROOTS = ML_ROOTS


def _purge_partial_imports() -> None:
    """Evict half-built ML modules, under the lock every ML importer holds.

    It used to take Laya's own lock only, and a module that is "half-built"
    by this test is also one another thread is importing right now: the
    privacy classifier lost Layer 3 to exactly that (services/ml_imports).
    """
    from agent_friday.services.ml_imports import purge_partial_imports
    purge_partial_imports()


def _load_now():
    """Import and load. Slow (~40 s cold). Never called on the request path."""
    global _agent, _load_error, _loading
    with _agent_lock:
        if _agent is not None:
            return _agent
        _loading = True
    try:
        # IMPORT transformers FROM THIS THREAD, BEFORE laya DOES, AND UNDER A
        # LOCK. transformers 5.x builds its namespace lazily, so two threads
        # touching it during boot can leave one of them holding a half-built
        # module - which surfaces as `cannot import name 'AutoTokenizer' from
        # 'transformers'` and looks, wrongly, like a broken install. The
        # memory tier imports the same stack on the main thread while this
        # runs on a warm-up thread, and that race can cost the approval gate
        # its second opinion for a whole boot.
        #
        # Doing the import here, eagerly, means the expensive namespace build
        # happens once where its failure is caught and retried, instead of
        # inside laya where it is three frames from anything that knows what
        # to do about it.
        with _IMPORT_LOCK:
            _purge_partial_imports()
            import huggingface_hub  # noqa: F401,PLC0415
            import transformers  # noqa: PLC0415
            from transformers import AutoTokenizer  # noqa: F401,PLC0415
            import laya  # noqa: PLC0415 - lazy; see module docstring
        t0 = time.time()
        reset_calibration_warnings()
        agent = capture_load_warnings(
            lambda: laya.load(MODEL_ID, device="cpu"))
        _use_engine(agent)
        with _agent_lock:
            _agent = agent
            _load_error = None
        _log.info("laya ready in %.1fs (cpu)", time.time() - t0)
        # Say it in the log at the level it deserves: the bucket this
        # gate depends on being distorted is a warning; another bucket
        # clamped is worth one line and no alarm.
        _cal = calibration_report(agent)
        if not _cal["ok"]:
            _log.warning("laya calibration: %s", _cal["detail"])
        elif _cal["clamped"]:
            _log.info(
                "laya calibration: %s; clamped elsewhere: %s",
                _cal["detail"],
                ", ".join(
                    "%s %.4g->%.4g" % (k, v["shipped"], v["applied"])
                    for k, v in sorted(_cal["clamped"].items())))
        return agent
    except Exception as e:
        with _agent_lock:
            _load_error = ExceptionText("%s: %s" % (type(e).__name__, e))
        # SCHEDULE THE NEXT ATTEMPT RATHER THAN WAITING FOR A DECISION.
        #
        # A boot-time failure here is usually an import race, not a
        # missing model: this load runs on a background thread while the
        # memory tier is importing the same ML stack on the main one, and
        # transformers 5.x lazy-loads its submodules, so a concurrent
        # `from transformers import AutoTokenizer` can see a half-built
        # module and raise ImportError. Sequentially it never fails, which is
        # why it reproduced in the server and nowhere else.
        #
        # Retrying only when a decision arrives was not enough: on an idle
        # machine no decision arrives, so the second opinion stayed off with
        # nobody to notice. A timer makes the recovery independent of traffic.
        try:
            if _load_attempts < _MAX_LOAD_ATTEMPTS:
                t = threading.Timer(_RETRY_AFTER_S, lambda: start_warming())
                t.daemon = True
                t.start()
        except Exception:
            pass
        # A missing model is a degraded feature, never a broken gate. The
        # caller falls back to keyword and the UI reports why.
        _log.warning("laya unavailable (%s) - decisions stay on keyword",
                     _load_error)
        return None
    finally:
        _loading = False


def _use_engine(agent) -> None:
    """Put the loaded model on the engine `laya_runtime` selects.

    A faster engine that cannot run here (no artifact, failed agreement
    check, a runtime error) leaves laya's own fp32 path in place and says so;
    it is never a reason for the gate to be without its second opinion.
    """
    from agent_friday.services import laya_runtime
    try:
        from agent_friday.core import _load_settings
        wanted = (_load_settings() or {}).get("laya_runtime") or "auto"
    except Exception:
        wanted = "auto"
    engine = laya_runtime.choose_engine(wanted)
    try:
        laya_runtime.apply_engine(agent, engine)
    except Exception as e:
        _log.warning("laya engine %s unavailable (%s); running fp32", engine, e)
        laya_runtime.apply_engine(agent, "torch-fp32")
    _log.info("laya engine: %s, %s threads", getattr(agent, "_friday_engine", "?"),
              getattr(agent, "_friday_threads", "?"))


def start_warming(force: bool = False) -> None:
    """Kick the load on a background thread. Safe to call more than once.

    Called from the server's warm-up alongside the other caches, so the ~40 s
    is spent while Friday is starting rather than in front of the user's first
    approval card.

    RETRIES AFTER A FAILURE, within limits - see `_MAX_LOAD_ATTEMPTS`. A
    transient failure heals on its own; a genuinely absent checkpoint costs a
    few slow attempts and then stops. `force=True` ignores both the cooldown
    and the attempt budget, for the operator path where a human has just fixed
    whatever was broken and should not have to restart the server.

    INERT UNDER FRIDAY_TESTING=1, like every other daemon in this codebase.
    Without that guard the self-healing call in `union_backend` fires inside
    the suite: each xdist worker that exercises the union starts a real 808 MB
    checkpoint load, and the first thing that went wrong was not a wrong
    verdict but `OSError: [Errno 28] No space left on device` in the middle of
    an unrelated test. The tests that care about warming monkeypatch this
    function, so they are unaffected.
    """
    global _load_attempts, _last_attempt_ts
    if os.environ.get("FRIDAY_TESTING") == "1":
        return
    if _agent is not None or _loading:
        return
    if _load_error is not None and not force:
        if _load_attempts >= _MAX_LOAD_ATTEMPTS:
            return
        if (time.time() - _last_attempt_ts) < _RETRY_AFTER_S:
            return
    _load_attempts += 1
    _last_attempt_ts = time.time()

    # DO NOT RACE THE BOOT. Prevention, not just recovery: the first attempt
    # waits for the rest of startup to finish importing the same ML stack,
    # because two threads building `huggingface_hub` or `transformers` at once
    # is what poisons sys.modules in the first place. The gate is keyword-only
    # for those seconds, which is exactly what it was before Laya existed and
    # what the Settings panel already says.
    #
    # Later attempts (a retry, or an operator `?retry=1`) do not wait - boot
    # is long over by then and the delay would only be dead time.
    delay = _BOOT_SETTLE_S if _load_attempts == 1 and not force else 0.0
    if delay:
        t = threading.Timer(delay, _load_now)
        t.name = "laya-warm-delayed"
        t.daemon = True
        t.start()
    else:
        threading.Thread(target=_load_now, name="laya-warm", daemon=True).start()


# ---------------------------------------------------------------------------
#  THE BACKEND
# ---------------------------------------------------------------------------

def _reserve_scoring(*, pilot: bool):
    """Return an idempotent release callback, or None; never queue.

    A pilot starts only on an idle scorer and occupies at most one of the two
    slots. Approval work can still enter the other slot. Each lease retains
    its own semaphore so a replaced scorer cannot release another pool.
    """
    slot = _scoring
    with _SCORING_ADMISSION_LOCK:
        counts = _SCORING_ADMISSIONS.setdefault(slot, {"approval": 0, "pilot": 0})
        if pilot and (counts["approval"] or counts["pilot"] or _MAX_SCORING < 2):
            return None
        if not slot.acquire(blocking=False):
            return None
        kind = "pilot" if pilot else "approval"
        counts[kind] += 1
    released = False

    def release():
        nonlocal released
        with _SCORING_ADMISSION_LOCK:
            if not released:
                released = True
                counts[kind] -= 1
                slot.release()

    return release


def try_start_pilot_score(state: str, questions: dict, callback) -> str:
    """Warm-only opportunistic prediction; callback receives result or None.

    Admission and thread creation never wait for inference. The worker owns
    its slot until inference ends, even after a caller has stopped waiting.
    Input and provider errors are never logged or retained by this helper.
    """
    agent = _agent
    if agent is None or _loading:
        return "cold"
    release = _reserve_scoring(pilot=True)
    if release is None:
        return "busy"

    def run():
        nonlocal state
        result = None
        started = time.monotonic()
        try:
            result = agent.predict(state, questions)
        except Exception:
            pass
        finally:
            state = ""
            release()
        try:
            callback(result, (time.monotonic() - started) * 1000)
        except Exception:
            pass
        finally:
            result = None

    try:
        threading.Thread(target=run, name="laya-pilot", daemon=True).start()
    except Exception:
        release()
        return "error"
    return "started"


# ---------------------------------------------------------------------------
#  ANSWER MEMORY
# ---------------------------------------------------------------------------
#
# The same model asked the same question about the same state gives the same
# answer, and the gate asks the same things constantly: the Grants screen
# classifies every connector read tool each time it opens, about thirty
# questions in one burst. Scoring each afresh held both slots and sent most of
# a burst to the keyword half ("still busy"), which is the second opinion
# going missing for no reason but repetition.
#
# So an answer is remembered, per loaded model instance, and a remembered one
# is served without taking a scoring slot. It is Laya's own verdict, so the
# union's add-only property is unchanged. Only real answers are remembered: an
# error, a timeout or a keyword-only fallback never is. A scoring that timed
# out still finishes on its thread, and its answer serves the next ask.

_ANSWER_CACHE_MAX = 512
_answer_cache: "OrderedDict[str, tuple]" = OrderedDict()
_answer_cache_owner = None
_answer_cache_lock = threading.Lock()
_answer_cache_hits = 0
_answer_cache_misses = 0


def clear_answer_cache() -> None:
    global _answer_cache_owner, _answer_cache_hits, _answer_cache_misses
    with _answer_cache_lock:
        _answer_cache.clear()
        _answer_cache_owner = None
        _answer_cache_hits = _answer_cache_misses = 0


def answer_cache_stats() -> dict:
    with _answer_cache_lock:
        return {"size": len(_answer_cache), "max": _ANSWER_CACHE_MAX,
                "hits": _answer_cache_hits, "misses": _answer_cache_misses}


def _remembered(state: str):
    """A previous answer from the model loaded NOW, or None."""
    global _answer_cache_hits, _answer_cache_misses
    key = str(state or "")
    with _answer_cache_lock:
        hit = (_answer_cache.get(key)
               if _answer_cache_owner is not None and _answer_cache_owner is _agent
               else None)
        if hit is None:
            _answer_cache_misses += 1
            return None
        _answer_cache.move_to_end(key)
        _answer_cache_hits += 1
    choice, conf, detail = hit
    return choice, conf, dict(detail, cached=True)


def _remember(agent, state: str, answer: tuple) -> None:
    global _answer_cache_owner
    with _answer_cache_lock:
        if _answer_cache_owner is not agent:
            _answer_cache.clear()
            _answer_cache_owner = agent
        _answer_cache[str(state or "")] = answer
        _answer_cache.move_to_end(str(state or ""))
        while len(_answer_cache) > max(1, int(_ANSWER_CACHE_MAX)):
            _answer_cache.popitem(last=False)


def _answer_unreserved(state: str, *, also: tuple = ()) -> tuple:
    """Severity, plus the `also` questions in the same forward pass.

    `also` answers ride in detail["also"] as {question: choice}. The shadow
    asks the gate's two finer questions this way (laya_questions.GATE_SHADOW):
    they cost one batched pass, not three calls, and they change no verdict.
    """
    agent = _agent
    if agent is None:
        raise RuntimeError("laya not loaded yet")
    if also:
        from agent_friday.services import laya_questions
        questions = laya_questions.select(("severity",) + tuple(also))
    else:
        questions = SEVERITY_QUESTION
    r = agent.predict(str(state or ""), questions)
    a = (r.get("answers") or {}).get("severity") or {}
    choice = a.get("choice")
    if choice not in ("hard", "soft"):
        raise ValueError("laya returned %r, not hard/soft" % (choice,))
    conf = a.get("confidence")
    detail = {"source": "laya", "model": MODEL_ID,
              "probabilities": a.get("probabilities") or {}}
    if also:
        detail["also"] = {q: ((r.get("answers") or {}).get(q) or {}).get("choice")
                          for q in also}
    answer = (choice, (float(conf) if conf is not None else None), detail)
    _remember(agent, state, answer)
    return answer


class LayaTooSlow(TimeoutError):
    """Laya did not answer within `_SCORE_TIMEOUT_S`."""


class LayaBusy(LayaTooSlow):
    """Every scoring slot was still held by an earlier question."""


# ---------------------------------------------------------------------------
#  MISSED ANSWERS
# ---------------------------------------------------------------------------
#
# Every union question Laya does not answer is decided by the keyword half.
# That fallback is safe and must never be silent, so each one is counted here
# by reason, and the time of the latest is kept so the gate status can say the
# second opinion has been missing RECENTLY, not merely at some point since boot.

MISS_REASONS = ("loading", "not_loaded", "busy", "slow", "error")
_missed = dict.fromkeys(MISS_REASONS, 0)
_last_missed_ts: Optional[float] = None
_last_missed_reason: Optional[str] = None
_missed_lock = threading.Lock()


def _missed_one(reason: str) -> None:
    global _last_missed_ts, _last_missed_reason
    with _missed_lock:
        _missed[reason] = _missed.get(reason, 0) + 1
        _last_missed_ts = time.time()
        _last_missed_reason = reason


def reset_missed() -> None:
    global _last_missed_ts, _last_missed_reason
    with _missed_lock:
        for k in list(_missed):
            _missed[k] = 0
        _last_missed_ts = _last_missed_reason = None


#: How long a BACKGROUND scoring (a shadow) waits for a free slot. Nothing is
#: waiting on it, so it queues briefly instead of giving up.
_BACKGROUND_WAIT_S = 60.0


def _answer(state: str, *, wait_s: float = 0.0, also: tuple = ()) -> tuple:
    """Direct and shadow scoring share admission with bounded approvals.

    `wait_s` > 0 waits that long for a slot, polling; the default never waits.
    A remembered answer serves only if it carries every `also` question.
    """
    hit = _remembered(state)
    if hit is not None and all(q in (hit[2].get("also") or {}) for q in also):
        return hit
    if _agent is None:
        if wait_s and not _loading:
            start_warming()
        raise RuntimeError("laya not loaded yet")
    release = _reserve_scoring(pilot=False)
    deadline = time.monotonic() + max(0.0, float(wait_s))
    while release is None and time.monotonic() < deadline:
        time.sleep(0.05)
        release = _reserve_scoring(pilot=False)
    if release is None:
        raise LayaBusy("laya is still busy with earlier actions")
    try:
        return _answer_unreserved(state, also=also)
    finally:
        release()


def _answer_bounded(state: str) -> tuple:
    """`_answer`, waiting at most `_SCORE_TIMEOUT_S`.

    The scoring runs on its own daemon thread. On timeout the caller stops
    waiting and the thread finishes on its own, holding one of the
    `_MAX_SCORING` slots until it does, so a stuck model is never handed an
    ever-growing backlog.
    """
    global _slow_answers, _last_slow_ts
    hit = _remembered(state)
    if hit is not None:
        return hit
    release = _reserve_scoring(pilot=False)
    if release is None:
        _slow_answers += 1
        _last_slow_ts = time.time()
        raise LayaBusy("laya is still busy with earlier actions (too slow on this PC)")
    box: Dict[str, Any] = {}
    done = threading.Event()

    def _run():
        try:
            box["result"] = _answer_unreserved(state)
        except BaseException as e:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = e
        finally:
            release()
            done.set()

    try:
        threading.Thread(target=_run, name="laya-score", daemon=True).start()
    except Exception:
        release()
        raise
    if not done.wait(_SCORE_TIMEOUT_S):
        _slow_answers += 1
        _last_slow_ts = time.time()
        _log.info("laya took longer than %.1fs; the keyword scan decided",
                  _SCORE_TIMEOUT_S)
        raise LayaTooSlow("laya took longer than %.1fs (too slow on this PC)"
                          % _SCORE_TIMEOUT_S)
    if "error" in box:
        raise box["error"]
    return box["result"]


def laya_backend(question: str, state: str, **kw):
    """`decisions.py` backend signature: (answer, confidence, detail).

    Only answers `action_severity` and `policy_class`. Anything else raises,
    and `decide` falls back to keyword and records that it did - which is the
    wanted behaviour for a question this model was never given.
    """
    background = bool(kw.get("background"))
    wait_s = _BACKGROUND_WAIT_S if background else 0.0
    # A background (shadow) scoring asks the gate's finer questions too, in
    # the same pass: evidence for a later two-answer gate, no verdict change.
    also = ("leaves_machine", "changes_outside") if background else ()
    if question == "action_severity":
        return _answer(state, wait_s=wait_s, also=also)
    if question == "policy_class":
        severity, conf, detail = _answer(state, wait_s=wait_s, also=also)
        if severity == "soft":
            return "internal", conf, dict(detail, severity=severity)
        # The LABEL for a hard action stays with the incumbent. It is
        # cosmetic (approvals.py says so), the model was not asked about it,
        # and inventing a label from a severity call would be a second
        # classifier hiding inside the first.
        from agent_friday.services import approvals as _ap
        return (_ap._label_hard_class(state), conf,
                dict(detail, severity=severity, label_source="keyword"))
    raise KeyError("laya backend has no answer for %r" % question)


# ---------------------------------------------------------------------------
#  SHADOW MODE
# ---------------------------------------------------------------------------
#
# Shadow scoring now lives in `decisions.py`, not here. It was written in this
# module first, reaching into `_dec._record`, `_dec._clip`, `_dec._scrub` and
# `_dec._state_digest` to do it - four private names across a module boundary,
# which is how a seam stops being one. Running a candidate alongside the
# incumbent is the seam's job for ANY backend, not a favour this one does for
# itself, so `decisions.shadow_backend()` and `decisions._run_shadow()` own it
# and this module keeps only the name.
#
#     FRIDAY_DECISION_SHADOW=laya        (or settings: decision_shadow)
#
# With that set, `keyword` still decides - the verdict the user experiences is
# byte-identical - and Laya scores the same state on a background thread, with
# both answers in the log. Disagreements become data instead of an argument.


def shadow_backend() -> Optional[str]:
    """Kept as a re-export so `status()` and the settings UI have one import."""
    from agent_friday.services import decisions
    return decisions.shadow_backend()


# ---------------------------------------------------------------------------
#  UNION MODE - the one worth actually turning on
# ---------------------------------------------------------------------------
#
# MEASURED on tools/severity_eval.py, 27 firm cases:
#
#     rules only      17/27 (63%)   MISSED hard = 5   false hard = 5
#     laya only       23/27 (85%)   MISSED hard = 1   false hard = 3
#     UNION (either)  22/27 (81%)   MISSED hard = 0   false hard = 5
#
# The union scores LOWER than Laya alone and is nonetheless the right mode,
# because the two error classes are not equal. A missed `hard` sends mail with
# no human in the loop. A false `hard` costs one approval card. Their misses
# are fully disjoint - no firm hard case was missed by both - so taking either
# vote drives the expensive error to zero on this set.
#
# The safety property is structural, not statistical, and that is the actual
# argument for shipping it: union can only ever ADD gating. There is no input
# for which it lets through something `keyword` would have caught, because
# `keyword`'s verdict is one of the two inputs. The worst case of a Laya
# regression, a bad fine-tune or a corrupted download is more approval cards,
# never fewer. This mirrors judgment_gate, which is allowed to rescue a
# blocked span but never to authorise one.
#
# HONEST LIMIT ON THAT 63%. The eval set is adversarial BY CONSTRUCTION: it
# deliberately carries five outward actions phrased with no marker word at
# all, which a substring scan cannot catch by design. So 63% is where the
# keyword scan's boundary is, NOT its accuracy on real traffic,
# which is mostly unambiguous and where it does much better. Nobody should
# quote that number as a field measurement. The disjointness is the finding;
# the percentages are a probe of the boundary.

def union_backend(question: str, state: str, **kw):
    """keyword OR laya, escalating only. Never de-escalates.

    Falls back to a pure keyword verdict whenever Laya is unavailable, still
    loading, or errors - so the gate's behaviour on a broken model is exactly
    today's behaviour, not an open door.
    """
    from agent_friday.services import decisions as _dec

    kw_answer, _, kw_detail = _dec._keyword_backend(question, state, **kw)

    if not is_ready():
        # SELF-HEALING, AND STILL NOT BLOCKING. However this backend got
        # selected - the Settings panel, a hand-edited settings.json, a CLI
        # write - the first decision that needs it kicks the load on a
        # background thread and is answered by `keyword` right now. Without
        # this, flipping the switch on a running server would select a model
        # that never loads until the next restart, and the panel would sit on
        # "still loading" forever.
        #
        # `start_warming` owns the retry policy now, so a previous failure is
        # no longer terminal here. It was: this call was guarded on
        # `_load_error is None`, so one boot-time blip left the gate
        # keyword-only for the life of the process. The budget and cooldown
        # live in one place rather than being re-decided at each call site.
        if not _loading:
            start_warming()
        _missed_one("loading" if _loading else "not_loaded")
        return kw_answer, None, dict(kw_detail, union="keyword-only",
                                     reason="laya still loading"
                                     if _loading else "laya not loaded")
    try:
        # BOUNDED. This runs inside a chat turn; a laptop CPU must not turn
        # the second opinion into a stall. Too slow is answered like loading.
        severity, conf, detail = _answer_bounded(state)
    except LayaTooSlow as e:
        _missed_one("busy" if isinstance(e, LayaBusy) else "slow")
        return kw_answer, None, dict(kw_detail, union="keyword-only",
                                     reason=exception_text(e))
    except Exception as e:
        _missed_one("error")
        return kw_answer, None, dict(kw_detail, union="keyword-only",
                                     reason="laya error: %s" % e)

    if question == "action_severity":
        escalated = (kw_answer == "soft" and severity == "hard")
        return (("hard" if "hard" in (kw_answer, severity) else "soft"),
                conf, dict(detail, union="or", keyword=kw_answer,
                           laya=severity, escalated=escalated))

    if question == "policy_class":
        # `internal` is the only soft class in the policy table; anything
        # else is gated. So escalation means: if keyword said internal and
        # Laya says hard, take the keyword LABEL machinery for the hard case
        # rather than inventing one.
        if kw_answer == "internal" and severity == "hard":
            from agent_friday.services import approvals as _ap
            return (_ap._label_hard_class(state), conf,
                    dict(detail, union="or", keyword=kw_answer,
                         laya=severity, escalated=True,
                         label_source="keyword"))
        return kw_answer, conf, dict(detail, union="or", keyword=kw_answer,
                                     laya=severity, escalated=False)

    raise KeyError("union backend has no answer for %r" % question)


# ---------------------------------------------------------------------------
#  THE THREE STATES, AS ONE DEFINITION
# ---------------------------------------------------------------------------
#
# The settings file carries two generic keys - `decision_backend` and
# `decision_shadow` - because `decisions.py` is deliberately backend-agnostic
# and shadowing is the seam's job for ANY candidate, not a favour this module
# does itself. But four combinations of two keys is not a control a person can
# reason about, and two of them are states nobody should be able to pick:
#
#   decision_backend="laya"  would REPLACE the keyword scan rather than adding
#   to it, which throws away the structural safety property - that the union
#   can only ever ADD an approval card - and leaves only the 85% accuracy.
#   It stays registered and reachable by hand for evaluation; it is not
#   offered in the UI, and turning the switch to "on" never selects it.
#
# So the UI gets three named states and this table is the only place they are
# translated. A mapping written once in Python and again in two HTML files is
# a mapping that will disagree with itself.

MODES = {
    "off":    {"decision_backend": "keyword",     "decision_shadow": ""},
    "shadow": {"decision_backend": "keyword",     "decision_shadow": "laya"},
    "on":     {"decision_backend": "laya-union",  "decision_shadow": ""},
}

MODE_MEANING = {
    "off": "the keyword scan alone decides which actions need your sign-off",
    "shadow": "the keyword scan still decides; Laya scores the same actions "
              "alongside it and both answers are logged, changing no verdict",
    "on": "an action is held for your sign-off when EITHER the keyword scan "
          "or Laya says it should be - so this can add approval cards, never "
          "remove one",
}


def settings_for_mode(mode: str) -> dict:
    """The settings delta that puts the gate in `mode`. Unknown -> off."""
    return dict(MODES.get(str(mode or "").lower()) or MODES["off"])


def current_mode(settings=None) -> str:
    """Which of the three states the CURRENT settings describe.

    Anything that is not one of the three - a hand-edited `decision_backend:
    "laya"`, or a shadow set alongside a union - reports "custom" rather than
    being rounded to the nearest switch position. The UI shows that plainly
    instead of displaying a state the file does not contain.
    """
    if settings is None:
        try:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        except Exception:
            settings = {}
    have = {
        "decision_backend": str((settings or {}).get("decision_backend") or ""),
        "decision_shadow": str((settings or {}).get("decision_shadow") or ""),
    }
    for name, want in MODES.items():
        if have == want:
            return name
    return "custom"


def register() -> None:
    """Make `laya` and `laya-union` selectable. Neither becomes the default.

    The default stays `keyword` until the owner chooses otherwise. Registering a
    backend is not adopting it - `decisions.active_backend()` reads a setting,
    and an unknown name falls back to keyword loudly.
    """
    from agent_friday.services import decisions
    decisions.register_backend("laya", laya_backend)
    decisions.register_backend("laya-union", union_backend)
