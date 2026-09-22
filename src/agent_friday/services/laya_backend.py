"""A Laya scorer for services/decisions.py, and a shadow mode to prove it out.

WHAT THIS IS FOR

`dissent_gate.classify_severity` decides whether an action needs Stephen's
sign-off - including, since 2026-09-20, sending mail as him. It is a scan for
~40 substrings. Its own docstring records the failure it could not avoid:
"spend" and "order " are hard markers and also ordinary nouns, so "Analyze our
spend trends" gated, and the fix was a hand-written regex for leading drafting
verbs. That is a keyword classifier accumulating patches.

Laya is a small typed-decision encoder (ModernBERT-large, 421M) that answers a
fixed question over a state and returns calibrated probabilities. Measured on
this machine 2026-09-22 against the severity question: ~300 ms on CPU, and it
answers the two patched cases correctly without the patches.

HOW IT IS WIRED, AND WHY IT CHANGES NOTHING YET

Registered as the `laya` backend in `decisions.py`, which is NOT the default.
The default stays `keyword` until there is a number, per that module's own
argument. What this module adds on top is SHADOW MODE:

    FRIDAY_DECISION_SHADOW=laya

With that set, `keyword` still decides - the verdict Stephen experiences is
byte-identical - and Laya scores the same state alongside it, on a background
thread, with both answers written to the decision log. Disagreements become
data instead of an argument.

That ordering is deliberate. The alternative - switch the backend and watch -
puts an unmeasured model in front of an irreversible action, and the first
evidence it was wrong would be a sent email.

THREE THINGS THAT MUST STAY TRUE

  * It never blocks a verdict. Loading takes ~42 s the first time; that
    happens on a warm-up thread, and any decision arriving before the model is
    ready is answered by `keyword` rather than waiting.
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
from typing import Any, Dict, Optional

_log = logging.getLogger("friday.laya")

#: transformers deadlocks probing for TensorFlow in a frozen build, and the
#: xet transfer backend was responsible for 2 of 7 crashes measured on
#: 2026-09-21. Both are set before any import of laya, not after.
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
_load_error: Optional[str] = None
_loading = False


# ---------------------------------------------------------------------------
#  LOADING
# ---------------------------------------------------------------------------

def is_ready() -> bool:
    return _agent is not None


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
    }


def _load_now():
    """Import and load. Slow (~42 s cold). Never called on the request path."""
    global _agent, _load_error, _loading
    with _agent_lock:
        if _agent is not None:
            return _agent
        _loading = True
    try:
        import laya  # noqa: PLC0415 - deliberately lazy; see module docstring
        t0 = time.time()
        agent = laya.load(MODEL_ID, device="cpu")
        with _agent_lock:
            _agent = agent
            _load_error = None
        _log.info("laya ready in %.1fs (cpu)", time.time() - t0)
        return agent
    except Exception as e:
        with _agent_lock:
            _load_error = "%s: %s" % (type(e).__name__, e)
        # A missing model is a degraded feature, never a broken gate. The
        # caller falls back to keyword and the UI reports why.
        _log.warning("laya unavailable (%s) - decisions stay on keyword",
                     _load_error)
        return None
    finally:
        _loading = False


def start_warming() -> None:
    """Kick the load on a background thread. Safe to call more than once.

    Called from the server's warm-up alongside the other caches, so the ~42 s
    is spent while Friday is starting rather than in front of Stephen's first
    approval card.
    """
    if _agent is not None or _loading:
        return
    threading.Thread(target=_load_now, name="laya-warm", daemon=True).start()


# ---------------------------------------------------------------------------
#  THE BACKEND
# ---------------------------------------------------------------------------

def _answer(state: str) -> tuple:
    agent = _agent
    if agent is None:
        raise RuntimeError("laya not loaded yet")
    r = agent.predict(str(state or ""), SEVERITY_QUESTION)
    a = (r.get("answers") or {}).get("severity") or {}
    choice = a.get("choice")
    if choice not in ("hard", "soft"):
        raise ValueError("laya returned %r, not hard/soft" % (choice,))
    conf = a.get("confidence")
    return choice, (float(conf) if conf is not None else None), {
        "source": "laya", "model": MODEL_ID,
        "probabilities": a.get("probabilities") or {},
    }


def laya_backend(question: str, state: str, **kw):
    """`decisions.py` backend signature: (answer, confidence, detail).

    Only answers `action_severity` and `policy_class`. Anything else raises,
    and `decide` falls back to keyword and records that it did - which is the
    wanted behaviour for a question this model was never given.
    """
    if question == "action_severity":
        return _answer(state)
    if question == "policy_class":
        severity, conf, detail = _answer(state)
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
# With that set, `keyword` still decides - the verdict Stephen experiences is
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
# MEASURED 2026-09-22 on tools/severity_eval.py, 27 firm cases:
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
# keyword scan's boundary is, NOT its accuracy on Stephen's real traffic,
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
        return kw_answer, None, dict(kw_detail, union="keyword-only",
                                     reason="laya not loaded")
    try:
        severity, conf, detail = _answer(state)
    except Exception as e:
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


def register() -> None:
    """Make `laya` and `laya-union` selectable. Neither becomes the default.

    The default stays `keyword` until Stephen chooses otherwise. Registering a
    backend is not adopting it - `decisions.active_backend()` reads a setting,
    and an unknown name falls back to keyword loudly.
    """
    from agent_friday.services import decisions
    decisions.register_backend("laya", laya_backend)
    decisions.register_backend("laya-union", union_backend)
