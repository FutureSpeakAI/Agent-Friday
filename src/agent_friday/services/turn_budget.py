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

#: NO BUILT-IN ROUND CAP.
#:
#: Stephen, 2026-09-25: "why 999? How about making it unlimited? I bet we get
#: local models that can run way longer, and soon." And on the rest: "I want no
#: caps unless I set them myself in the cost metering UI."
#:
#: 999 was never a safety property either -- it was the same guess as the 50 it
#: replaced, one order of magnitude further out. A round on a local seat costs
#: nothing but time the owner chose to spend, and on a cloud seat it costs money
#: that is METERED rather than forbidden. What actually protects a turn is the
#: stuck-loop detector below, the Stop button, and context compression; none of
#: those is a number, and none of them gets in the way of a model that is
#: genuinely working.
#:
#: `None` means unlimited. A figure here would be a cap by another name.
ROUND_BUDGET_DEFAULT = None

#: NO BUILT-IN WALL CLOCK. Unlimited unless the owner sets one.
#:
#: It was also never the guard it looked like: it was only consulted BETWEEN
#: rounds, so a single long round ran past it untouched. A limit that cannot
#: interrupt the case it was written for is not worth the surprise it causes in
#: the cases it can.
WALL_CLOCK_DEFAULT_S = None

#: NO BUILT-IN SUBAGENT STEP CAP. A delegated task is still the owner's work.
#:
#: This is only the FALLBACK. Every built-in scope in `subagents.py` names its own
#: figure (15 to 40) alongside a `time_budget_s`, and both are enforced with a
#: reason the caller can read. Those are deliberate per-role choices, not
#: leftovers, so they stay as they are; this default is what a scope gets when it
#: declares no budget at all.
SUBAGENT_STEP_DEFAULT = None

#: NO SEPARATE CAP FOR UNATTENDED WORK EITHER.
#:
#: The privacy control on a scheduled job is its cloud OPT-IN -- whether it may
#: leave the machine at all -- and that is untouched. Spend is the spending
#: limit's business, and the owner sets that. Two different concerns; only one
#: of them was ever mine to decide.
SCHEDULED_ROUND_DEFAULT = None

#: NO BUILT-IN TOKEN CEILING. Tokens are metered, not rationed.
#:
#: On a local seat they cost nothing; on a cloud seat they cost money the
#: spending limit governs, if the owner set one.
TOKEN_BUDGET_DEFAULT = None

#: How many identical calls before it is a loop rather than a retry. Two is a
#: legitimate retry after a transient failure; three is a pattern.
REPEAT_LIMIT_DEFAULT = 3


# ── Configuration ───────────────────────────────────────────────────────────

#: The figures this module used to SHIP in DEFAULT_SETTINGS. They were never
#: anybody's choice -- they were written into every install's settings.json the
#: first time it saved, so removing them from the defaults is not enough on its
#: own: an existing machine would keep the caps Stephen just abolished, and the
#: whole change would be cosmetic for the one person running it.
#:
#: A value that still matches one of these exactly is therefore treated as the
#: leftover it is. Anything else is a figure someone typed, and is obeyed.
_SHIPPED_LEGACY = {
    "rounds": {"default": 999, "scheduled": 300},
    "wall_clock_s": {"default": 1800},
    "tokens": {"default": 1000000},
}


def _drop_legacy(blk: Dict[str, Any]) -> Dict[str, Any]:
    """Ignore a cap group that is still exactly what this module shipped.

    The match is on the WHOLE group, not value by value. Value-by-value would
    mean a 999 the owner typed himself was silently ignored forever, which is a
    worse surprise than the one it fixes. An untouched group is the leftover; a
    group with anything changed in it is his, and is obeyed in full.
    """
    out = {}
    for group, vals in (blk or {}).items():
        if isinstance(vals, dict) and vals == _SHIPPED_LEGACY.get(group):
            out[group] = {}
        else:
            out[group] = vals
    return out


def _cfg() -> Dict[str, Any]:
    try:
        from agent_friday.core import _load_settings
        blk = (_load_settings() or {}).get("turn_budget") or {}
        return _drop_legacy(blk) if isinstance(blk, dict) else {}
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


def rounds_for(seat: str = ""):
    """Rounds allowed for `seat`. **None means unlimited**, which is the default.

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
        ceiling = _positive(blk, "scheduled")
        if asked and ceiling:
            return min(asked, ceiling)
        return asked or ceiling            # None when the owner set neither
    return asked                           # None = unlimited


def wall_clock_for(seat: str = ""):
    return _positive(_cfg().get("wall_clock_s") or {}, seat, "default")


def token_budget_for(seat: str = ""):
    return _positive(_cfg().get("tokens") or {}, seat, "default")


# ── Loop detection ──────────────────────────────────────────────────────────

def loop_guard_enabled() -> bool:
    """Is the stuck-model guard on? Default yes; the owner may turn it off in
    Settings > Spending."""
    try:
        from agent_friday.core import _load_settings
        blk = (_load_settings() or {}).get("cost_budget") or {}
        if "loop_guard_enabled" in blk:
            return bool(blk["loop_guard_enabled"])
    except Exception:
        pass
    return True


class LoopGuard:
    """Counts identical (tool, arguments) calls within one turn.

    Counted across the WHOLE turn rather than consecutively, so an alternating
    A-B-A-B-A-B loop is caught too -- consecutive-only detection misses exactly
    the shape a confused model most often falls into.

    Progress is never penalised: different arguments (paging, a new query) or a
    different tool reset nothing and count as work.

    It also watches the SHAPE of the calls, so a model cycling through the same
    few tools with slightly different arguments each pass is caught even though
    no single call ever repeats. See `_cycling`.
    """

    def __init__(self, repeat_limit: int = REPEAT_LIMIT_DEFAULT):
        self.repeat_limit = max(2, int(repeat_limit or REPEAT_LIMIT_DEFAULT))
        self._seen: Dict[str, int] = {}
        #: Tool names in call order, for the drifting-cycle check below.
        self._sequence: list = []
        #: The full (tool, args) key in the same order, so the cycle check can
        #: ask whether the model is covering NEW ground or circling old.
        self._keys: list = []

    @staticmethod
    def _key(name: str, args: Any) -> str:
        try:
            blob = json.dumps(args, sort_keys=True, default=str)
        except Exception:
            blob = repr(args)
        return "%s(%s)" % (str(name or ""), blob)

    def observe(self, name: str, args: Any = None) -> Optional[str]:
        """Record a call. Returns None, or a reason when it looks stuck."""
        key = self._key(name, args)
        n = self._seen.get(key, 0) + 1
        self._seen[key] = n
        self._sequence.append(str(name or ""))
        self._keys.append(key)
        if n >= self.repeat_limit:
            return ("%s was called %d times with the same arguments"
                    % (str(name or "a tool"), n))
        return self._cycling()

    # ── the same few tools, round and round, with the arguments drifting ────
    #
    # The identical-call check above needs ONE call to recur three times. A
    # model bouncing between two searches and two files repeats a shape long
    # before any single call reaches three, so the sequence is caught here
    # first: two tools over two argument sets trips this at round eight, where
    # the identical rule would wait for round nine, and wider pools of the same
    # shape it would not reach for far longer.
    #
    # What this does NOT do is flag a model whose arguments are genuinely new
    # each pass. search -> read -> search -> read over four new topics is
    # research, and the novelty gate below lets it run for ever. That is
    # deliberate: this exists to catch a model going nowhere, and a long run
    # over new material is the behaviour the removed caps used to punish.
    #
    # This is deliberately a STUCK-MODEL guard, not a usage limit -- it is the
    # one thing kept now that the round, time and token caps are gone, because
    # it fires on a shape rather than on an amount. A model doing genuinely
    # varied work never trips it: the pattern has to repeat unbroken.

    #: How many times a short tool pattern must repeat back-to-back before it
    #: is a cycle rather than a rhythm. Three passes of A-B is a coincidence a
    #: real task can produce; four is a model going round.
    CYCLE_REPEATS = 4

    #: The longest pattern worth looking for. Beyond this a "cycle" is long
    #: enough to be a legitimate multi-step routine the model is repeating over
    #: different material.
    CYCLE_MAX_LEN = 4

    def _cycling(self) -> Optional[str]:
        seq = self._sequence
        for size in range(1, self.CYCLE_MAX_LEN + 1):
            need = size * self.CYCLE_REPEATS
            if len(seq) < need:
                continue
            tail = seq[-need:]
            pattern = tail[:size]
            if not all(tail[i:i + size] == pattern
                       for i in range(0, need, size)):
                continue
            # SHAPE ALONE IS NOT STUCKNESS.
            #
            # search -> read -> search -> read over four new topics is
            # research, and flagging it would punish exactly the behaviour a
            # long-running agent is for. What makes a cycle stuck is that it
            # covers no new ground, so the arguments decide: when the window's
            # calls are mostly ones already made, it is circling; when most are
            # new, it is working.
            window = self._keys[-need:]
            if len(set(window)) > need // 2:
                continue
            # A single tool repeated is the identical-call case's territory
            # unless the arguments differ, which is exactly the gap this
            # closes, so it is reported either way.
            return ("the same %s ran %d times in a row without the "
                    "conversation moving on (%s)"
                    % ("call" if size == 1 else "sequence of %d calls" % size,
                       self.CYCLE_REPEATS, " -> ".join(pattern)))
        return None


class TokenBudget:
    """Tokens spent so far this turn, against a ceiling.

    Counts input and output together, because on a long tool loop the input
    side dominates: every round resends the whole conversation, so the cost of
    round 40 is mostly the 39 rounds before it. A ceiling on output alone would
    miss the runaway it is meant to catch.
    """

    def __init__(self, limit: Optional[int] = None):
        # There is no default ceiling any more, so a caller that constructs
        # this without one is asking for a budget that cannot exist. Say so
        # here rather than raising `int(None)` three frames away.
        _limit = limit if limit else TOKEN_BUDGET_DEFAULT
        if not _limit:
            raise ValueError(
                "TokenBudget needs an explicit limit: there is no built-in "
                "token ceiling. Construct this only when the owner set one.")
        self.limit = int(_limit)
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
        # As TokenBudget: no default deadline exists, so a deadline-less
        # construction is a caller bug and is named as one.
        _secs = seconds if seconds else WALL_CLOCK_DEFAULT_S
        if not _secs:
            raise ValueError(
                "WallClock needs an explicit number of seconds: there is no "
                "built-in per-turn deadline. Construct this only when the "
                "owner set one.")
        self.seconds = float(_secs)
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
        # A CLOUD MODEL GETS ITS OWN MAXIMUM.
        #
        # Stephen, 2026-09-25: "We're metering cloud calls, not limiting them."
        # A `max_tokens` we pick is our cap, and one below the model's real
        # ceiling truncates a long answer in a way that reads as the model
        # giving up. So when the catalog knows the figure, that figure is used;
        # spend is the spending limit's business, not this function's.
        catalog_max = None
        if num_ctx is None:            # no served window => not a seat we host
            try:
                from agent_friday.services.model_catalog import max_output_for
                catalog_max = max_output_for(model)
            except Exception:
                catalog_max = None
        if catalog_max:
            return int(catalog_max)
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


def cloud_output_tokens(model: str = "", *, seat: str = "") -> Optional[int]:
    """What to send as `max_tokens` to a model WE DO NOT SERVE, or None to send
    no ceiling at all.

    Stephen, 2026-09-25: "We're metering cloud calls, not limiting them." A
    `max_tokens` we choose IS our cap, and one below the model's real ceiling
    truncates a long answer in a way that reads as the model giving up. So:

      * a figure the owner typed wins, as everywhere else;
      * otherwise the catalog's own maximum for that model;
      * otherwise NOTHING -- the key is left out and the provider applies its
        own maximum.

    That last branch is the point. Falling back to a number of our own is how a
    model the catalog has not learned yet (a new id, a provider we have not
    priced) would silently inherit a 4,096-token ceiling -- which is exactly
    the failure this whole change exists to remove. Not knowing a model's
    maximum is a reason to impose nothing, not a reason to guess low.

    Returning None is meaningful, so callers must OMIT the key rather than send
    `max_tokens: null`; `_positive` already treats 0 as unset.
    """
    asked = _positive(_cfg().get("output_tokens") or {}, seat, model, "default")
    if asked:
        return int(asked)
    try:
        from agent_friday.services.model_catalog import max_output_for
        known = max_output_for(model)
    except Exception:
        known = None
    return int(known) if known else None


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


# ── Bounded deliberation ────────────────────────────────────────────────────
#
# The budget fix gives a reasoning seat room to think AND answer. It does not
# stop the seat from spending that room badly, and Stephen's trace showed
# exactly that: the plan was already sound -- Higgsfield image, save_output, an
# HTML resume in the creations folder, a relative path, offer PDF, and a
# correct refusal to open files without permission -- and then the model kept
# going. "Actually... Hmm... wait... Let me reconsider", over and over, on
# whether to base64-embed a background image or reference it by relative path,
# whether to offer PDF export, whether a brand stamp applied. It never emitted
# a tool call.
#
# That is not a reasoning failure. Every one of those is a small, reversible
# implementation choice with a reasonable default, and the deliberation was
# worth less than the answer it displaced. So the nudge is narrow: it targets
# RE-deliberation of settled minor details, and says what to do instead --
# choose, say so in one line, act, and offer the alternative afterwards.
#
# Deliberately NOT "think less". A hard problem should still get hard thinking;
# the seat is told where the ceiling is, not to stay away from it.
DELIBERATION_NUDGE = (
    "HOW TO SPEND YOUR THINKING. You have a generous but finite budget for one "
    "reply, and the thinking comes out of it. Think as hard as the problem "
    "genuinely deserves — then finish.\n"
    "Watch for one specific trap: re-deciding a small implementation detail you "
    "have already settled. File path or embedded data, one format or another, "
    "whether to offer an export — these have reasonable defaults and are "
    "reversible. When you notice yourself starting \"actually\", \"wait\" or "
    "\"let me reconsider\" about a detail like that, stop: keep your first "
    "reasonable choice, say which you chose in one line, and ACT.\n"
    "Offer the alternatives after the work exists, not instead of it. A "
    "delivered draft with a note saying \"I embedded the image; say the word "
    "and I'll switch to a linked file\" is worth far more than flawless "
    "reasoning the user never sees.\n"
    "Never end a turn having only deliberated. If you have a plan, the next "
    "thing you produce is a tool call or the answer itself."
)
