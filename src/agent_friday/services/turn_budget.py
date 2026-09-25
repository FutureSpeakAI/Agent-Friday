"""How long a turn may run, and what actually stops a runaway one.

A local seat gets the same round budget as a cloud seat. A local round costs no
money, and a 27B reasoner such as Bonsai2 can reason across hundreds of rounds.

An asymmetric cap -- `_call_claude_agent` at `max_iters=999` while
`_oai_agentic_loop` and both OpenAI-format transports in `model_router` sit at
50, and a subagent scope that names no figure of its own at 25 -- gives the
local path twenty times less, for no reason beyond a 4B-model era when a small
model would loop and a low cap was the cheapest way to contain it. For a 27B
reasoner, 50 rounds is a cliff mid-task, and all it produces is a truncated
answer telling the USER to "Raise max_iters".

**50 was never safety. It was a proxy for safety**, and a bad one: it punished a
model making steady progress exactly as hard as one stuck in a loop, and it let a
tight loop burn 50 expensive rounds before anything noticed.

So the cap goes up to parity and the real limits go in:

* LOOP DETECTION (`LoopGuard`) -- the same tool with the same arguments, repeated.
  That is the failure the low cap was standing in for, caught directly: a genuine
  loop stops in three rounds instead of fifty, while paging through results or
  moving between tools is recognised as progress and left alone.
* A WALL CLOCK (`WallClock`) -- so a turn cannot run forever even while doing new
  things each round.
* A TOKEN CEILING (`TokenBudget`) -- input and output together across every
  round, since each round resends the whole conversation.
* THE USER'S STOP. This one was missing entirely on the local path: the cloud
  loop honoured the `~/.friday/AGENT_STOP` kill file and a background task could
  be stopped from the tasks tray, but an interactive turn had nothing, so the
  50-round cap WAS the stop. Both loops now check `core.turn_stop_requested()`
  between rounds, `POST /api/chat/turn/<id>/stop` sets it, and the chat shows a
  Stop button while a turn is in flight. Cooperative, so the turn ends with its
  transcript and receipts intact instead of being killed mid-tool-call.
* The per-task spend ceiling (`prompt_cache.task_budget`), which already existed
  and is unchanged.

And when a limit is reached, `limit_message()` says WHICH limit it was and offers
to continue, instead of truncating and naming an internal knob.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Optional

#: Rounds a turn may take. Parity with the cloud path, which has run at 999
#: without incident; the limits that matter are the ones below.
ROUND_BUDGET_DEFAULT = 999

#: Wall clock for one turn. Generous on purpose: bonsai2 on a contended card is
#: slow, and a clock tighter than a real turn would rebuild the cliff this module
#: exists to remove. 30 minutes.
WALL_CLOCK_DEFAULT_S = 1800

#: A subagent has a narrower remit than a chat turn, so it keeps a cap -- just not
#: one a competent model trips over. 25 was in the same era as the 50.
#:
#: This is only the FALLBACK. Every built-in scope in `subagents.py` names its own
#: figure (15 to 40) alongside a `time_budget_s`, and both are enforced with a
#: reason the caller can read. Those are deliberate per-role choices, not
#: leftovers, so they stay as they are; this default is what a scope gets when it
#: declares no budget at all.
SUBAGENT_STEP_DEFAULT = 200

#: Unattended work stays bounded: nobody is watching it, and a scheduled job that
#: runs away spends money in the dark.
SCHEDULED_ROUND_DEFAULT = 300

#: Tokens one turn may spend across all its rounds, in and out combined. The
#: round cap no longer bounds spend now that it sits at parity, and on a
#: cloud-compatible seat tokens are money. Generous enough that no honest turn
#: reaches it: a million tokens is far past the point where something has gone
#: wrong and worth stopping to ask.
TOKEN_BUDGET_DEFAULT = 1_000_000

#: How many identical calls before it is a loop rather than a retry. Two is a
#: legitimate retry after a transient failure; three is a pattern.
REPEAT_LIMIT_DEFAULT = 3


# ── Configuration ───────────────────────────────────────────────────────────

def _cfg() -> Dict[str, Any]:
    try:
        from agent_friday.core import _load_settings
        blk = (_load_settings() or {}).get("turn_budget") or {}
        return blk if isinstance(blk, dict) else {}
    except Exception:
        return {}


_unattended = threading.local()


class unattended:
    """Mark everything inside as work nobody is watching.

    Thread-local, matching `local_only_guard`: background tasks each run on their
    own thread, so marking one never constrains an interactive turn beside it.

    Why a context rather than a parameter: `_generate_agent` has no `max_iters`
    argument, and threading one down through every call site to express "this is a
    scheduled run" would touch far more code than the fact deserves. The fact is
    about the RUN, so it lives with the run.
    """

    def __enter__(self):
        self._prev = getattr(_unattended, "on", False)
        _unattended.on = True
        return self

    def __exit__(self, *exc):
        _unattended.on = self._prev
        return False


def is_unattended() -> bool:
    return bool(getattr(_unattended, "on", False))


def _positive(blk: Any, *keys) -> Optional[int]:
    """The first of `keys` set to a positive integer. Zero or nonsense means
    "not set", which is how the UI expresses "inherit".

    Anything that is not a mapping is "not set" too: a hand-edited settings.json
    must never be able to stop a turn from running.
    """
    if not isinstance(blk, dict):
        return None
    for key in keys:
        k = str(key or "").strip()
        if not k or k not in blk:
            continue
        try:
            v = int(blk[k])
        except Exception:
            continue
        if v > 0:
            return v
    return None


def rounds_for(seat: str = "") -> int:
    """Rounds allowed for `seat`, from settings, else the shared default.

    Per-seat because the seats differ in kind, not because the local one is less
    trusted: someone may want a tighter leash on an experimental local model
    without touching the seat that answers their chat.

    UNATTENDED WORK TAKES THE TIGHTER OF TWO FIGURES: the seat's (or the
    default) and `scheduled`. Both are honoured that way, and neither can
    surprise.

    The seat keys are shared with interactive turns, so if the seat figure
    simply won, someone raising `local` for their own chat would silently have
    raised every scheduled job with it -- and a scheduled job is bounded because
    nobody is watching it, which has nothing to do with which seat serves it.
    If `scheduled` simply won, a deliberately tight seat figure would be
    ignored on exactly the runs least worth letting loose. `min` respects both:
    a run never lasts longer than any limit its owner set.
    """
    blk = (_cfg().get("rounds") or {})
    asked = _positive(blk, seat, "default")
    if is_unattended():
        ceiling = _positive(blk, "scheduled") or SCHEDULED_ROUND_DEFAULT
        return min(asked, ceiling) if asked else ceiling
    return asked or ROUND_BUDGET_DEFAULT


def wall_clock_for(seat: str = "") -> int:
    return _positive(_cfg().get("wall_clock_s") or {}, seat, "default") \
        or WALL_CLOCK_DEFAULT_S


def token_budget_for(seat: str = "") -> int:
    return _positive(_cfg().get("tokens") or {}, seat, "default") \
        or TOKEN_BUDGET_DEFAULT


# ── Loop detection ──────────────────────────────────────────────────────────

class LoopGuard:
    """Counts identical (tool, arguments) calls within one turn.

    Counted across the WHOLE turn rather than consecutively, so an alternating
    A-B-A-B-A-B loop is caught too -- consecutive-only detection misses exactly
    the shape a confused model most often falls into.

    Progress is never penalised: different arguments (paging, a new query) or a
    different tool reset nothing and count as work.
    """

    def __init__(self, repeat_limit: int = REPEAT_LIMIT_DEFAULT):
        self.repeat_limit = max(2, int(repeat_limit or REPEAT_LIMIT_DEFAULT))
        self._seen: Dict[str, int] = {}

    @staticmethod
    def _key(name: str, args: Any) -> str:
        try:
            blob = json.dumps(args, sort_keys=True, default=str)
        except Exception:
            blob = repr(args)
        return "%s(%s)" % (str(name or ""), blob)

    def observe(self, name: str, args: Any = None) -> Optional[str]:
        """Record a call. Returns None, or a reason when it looks like a loop."""
        key = self._key(name, args)
        n = self._seen.get(key, 0) + 1
        self._seen[key] = n
        if n >= self.repeat_limit:
            return ("%s was called %d times with the same arguments"
                    % (str(name or "a tool"), n))
        return None


class TokenBudget:
    """Tokens spent so far this turn, against a ceiling.

    Counts input and output together, because on a long tool loop the input
    side dominates: every round resends the whole conversation, so the cost of
    round 40 is mostly the 39 rounds before it. A ceiling on output alone would
    miss the runaway it is meant to catch.
    """

    def __init__(self, limit: Optional[int] = None):
        self.limit = int(limit if limit else TOKEN_BUDGET_DEFAULT)
        self.spent = 0

    def add(self, tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.spent += max(0, int(tokens_in or 0)) + max(0, int(tokens_out or 0))

    def exceeded(self) -> bool:
        return self.spent >= self.limit

    def reason(self) -> str:
        return "%s of a %s-token limit for one turn" % (
            "{:,}".format(int(self.spent)), "{:,}".format(int(self.limit)))


class WallClock:
    """A per-turn deadline."""

    def __init__(self, seconds: Optional[float] = None):
        self.seconds = float(seconds if seconds else WALL_CLOCK_DEFAULT_S)
        self.started = time.time()

    def elapsed(self) -> float:
        return max(0.0, time.time() - self.started)

    def expired(self) -> bool:
        return self.elapsed() >= self.seconds

    def reason(self) -> str:
        return "%ds of a %ds limit" % (int(self.elapsed()), int(self.seconds))


# ── What the user is told ───────────────────────────────────────────────────

def stopped_message(*, used: int = 0, model: str = "") -> str:
    """What a turn says when the USER stopped it.

    Distinct from every branch of `limit_message`: none of those was a decision
    anybody made, and this one was. It does not offer to continue, because the
    person who stopped it does not need to be asked whether they meant it -- but
    it does say where the work got to, so picking it up again is possible.
    """
    n = int(used or 0)
    return (
        "Stopped, at your request%s. Nothing is still running in the "
        "background. Tell me what to do differently and I will pick it up from "
        "here. (%s)"
        % ((" after %d round%s" % (n, "" if n == 1 else "s")) if n else "",
           str(model or "the model")))


def limit_message(kind: str, *, detail: str = "", used: int = 0,
                  model: str = "") -> str:
    """Say which limit stopped the turn, and offer to carry on.

    The old ending was "[Agent hit its 50-step tool limit ... Raise max_iters, or
    narrow the task.]" -- which names an internal knob the user cannot see and
    reads like a fault of theirs. Every branch here names the real limit, says the
    work is paused rather than finished, and offers to continue.
    """
    m = str(model or "the model")
    k = str(kind or "").strip().lower()
    if k == "loop":
        return (
            "I stopped: %s, so I was going round in circles rather than making "
            "progress. That is a loop, not a long task. Say “continue” "
            "if you want me to try a different approach, or tell me what to do "
            "differently. (%s)" % (detail or "the same call kept repeating", m))
    if k == "clock":
        # Always at least one minute: "about 0 minutes" would be a worse
        # account of a turn that ran than a rounded-up one.
        mins = max(1, int(used or 0) // 60)
        return (
            "I paused after about %d minute%s on this one — it hit the time "
            "limit for a single turn, not a dead end. Say “continue” to "
            "give it another stretch. (%s%s)"
            % (mins, "" if mins == 1 else "s", m,
               (", " + detail) if detail else ""))
    if k == "tokens":
        return (
            "I stopped: this turn reached its token budget (%s). It was still "
            "working, not stuck — but that is a lot of reading for one "
            "question, so it is worth a look before spending more. Say "
            "“continue” to carry on, or narrow it down. (%s)"
            % (detail or "the per-turn limit", m))
    return (
        "I stopped after %d rounds of tool calls, which is this turn's round "
        "limit — the work was still going, not finished. Say "
        "“continue” and I will pick up where I left off, or narrow it "
        "down and I will be quicker. (%s)" % (int(used or 0), m))


# ── Output budget: how much the model may WRITE in one round ────────────────
#
# Stephen, 2026-09-25, after a 19-minute turn ended with no answer at all:
#
#   "[bonsai2:27b used its entire 4096-token output budget thinking and never
#    began the answer (16637 characters of reasoning, no reply). Raise
#    max_tokens for this call...]"
#
# A reasoning model spends the OUTPUT budget on its thinking. 4096 tokens is a
# reasonable answer length and a hopeless thinking-plus-answer length, so on a
# hard question the scratchpad consumes the whole allowance and the reply never
# starts. The round cap was the same mistake in a different unit: a number
# chosen for one kind of model, silently applied to another.
#
# Two things follow, and both are needed. A bigger budget, and -- because a
# bigger budget can still be exhausted -- a loop that carries on rather than
# handing the user advice about an internal knob.

#: What a non-reasoning call may write. Unchanged: it was never the problem.
OUTPUT_TOKENS_DEFAULT = 4096

#: What a local reasoning seat may write in one round, thinking included.
#: Generous on purpose -- the thinking is the expensive part and it is not
#: optional -- but see `output_tokens_for`, which clamps this to the context the
#: seat is actually served at.
REASONING_OUTPUT_DEFAULT = 32768

#: Never let the output allowance eat more than this share of the context
#: window. bonsai2 declares 262K but is SERVED at 65,536 here, so a flat 32K
#: ask would leave only half the window for a prompt that already carries 25
#: tool results. Half is the most that can be promised without starving the
#: conversation it is supposed to be answering.
OUTPUT_CONTEXT_SHARE = 0.5

#: When a round exhausts its budget mid-thought, the retry gets this much more.
#: Paired with thinking turned OFF, so the larger allowance goes to the answer
#: rather than funding a longer deliberation that ends the same way.
RETRY_OUTPUT_MULTIPLIER = 2


def looks_like_reasoning_model(model: str = "") -> bool:
    """Does this seat think before it answers?

    Name-based, deliberately: the transport has to size the request before it
    sees a single token back, so there is nothing else to go on at that point.
    A wrong YES costs a larger ceiling that an ordinary model simply will not
    use -- `max_tokens` is a limit, not an allocation -- while a wrong NO costs
    the user their answer. The asymmetry decides the default.
    """
    m = str(model or "").lower()
    if not m:
        return False
    return any(k in m for k in (
        "bonsai", "qwen3", "qwq", "deepseek-r", "r1", "o1", "o3", "o4",
        "reason", "think", "magistral", "phi-4-reasoning", "glm-z",
    ))


def output_tokens_for(model: str = "", *, num_ctx: Optional[int] = None,
                      seat: str = "") -> int:
    """Tokens one round may write, thinking included.

    `num_ctx` is the context the seat is actually served at, not the maximum it
    advertises. Those differ by a factor of four on this machine, and the served
    figure is the one that decides whether a reply fits.
    """
    blk = _cfg().get("output_tokens") or {}
    asked = _positive(blk, seat, model, "default")
    if asked is None:
        asked = (REASONING_OUTPUT_DEFAULT if looks_like_reasoning_model(model)
                 else OUTPUT_TOKENS_DEFAULT)
    if num_ctx:
        try:
            ceiling = int(int(num_ctx) * OUTPUT_CONTEXT_SHARE)
            # Never clamp below the ordinary default: a small context is a
            # reason to write less, not a reason to be unable to answer.
            asked = max(OUTPUT_TOKENS_DEFAULT, min(asked, ceiling))
        except Exception:
            pass
    return int(asked)


def clamp_output(tokens: Optional[int], num_ctx: Optional[int]) -> Optional[int]:
    """Hold any output allowance -- including one the loop asked for -- inside
    the served context.

    The loop asks for a bigger retry budget without knowing what window the seat
    is served at; only the transport knows that. Unclamped, a doubled ask can
    equal the whole context and leave the prompt no room at all.
    """
    if not tokens:
        return tokens
    if not num_ctx:
        return int(tokens)
    try:
        ceiling = max(OUTPUT_TOKENS_DEFAULT, int(int(num_ctx) * OUTPUT_CONTEXT_SHARE))
        return int(min(int(tokens), ceiling))
    except Exception:
        return int(tokens)


def retry_output_tokens(previous: Optional[int] = None, *, model: str = "",
                        num_ctx: Optional[int] = None) -> int:
    """The budget for the round that follows an exhausted one."""
    base = int(previous or output_tokens_for(model, num_ctx=num_ctx))
    bigger = base * RETRY_OUTPUT_MULTIPLIER
    if num_ctx:
        try:
            bigger = min(bigger, int(int(num_ctx) * OUTPUT_CONTEXT_SHARE))
        except Exception:
            pass
    return max(base, int(bigger))


def ran_long_message(*, model: str = "", rounds: int = 0) -> str:
    """What the user sees when a turn genuinely could not finish.

    Never names `max_tokens`, `num_predict` or any other knob. The old text
    ended a 19-minute turn by telling the person to raise a setting they cannot
    see, which reads as their fault and is not even advice they can act on.
    """
    where = (" after %d rounds of work" % rounds) if rounds else ""
    return (
        "I ran long on this one%s and did not get to a finished answer — I kept "
        "thinking past the point where I should have started writing. That is "
        "mine to fix, not yours. Say “continue” and I will pick it up and "
        "answer from what I already worked out, or narrow it down and I will be "
        "quicker. (%s)" % (where, str(model or "the model")))
