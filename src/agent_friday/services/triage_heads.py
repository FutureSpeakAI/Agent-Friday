"""Background triage on the shared encoder: four small heads, no LLM.

Spec: laya_deep_actions_addendum section 4.6 (DA-6). While the brain sleeps,
four jobs that used regexes (or nothing) get a second opinion from the same
22M-parameter MiniLM that `services/turn_vector` and `services/slice_head`
already use. Each head is a nearest-prototype lookup over synthetic seeds
written in this file, turned into probabilities with a softmax. No head is
trained and none is calibrated; `tools/triage_eval.py` scores each against
the incumbent on the labelled sets in tests/fixtures/triage_eval_*.json.

  MAIL LANE      `message_triage.classify`, ONLY when nothing matched at all
                 (no learned prior, no rule, not even a penalised one). It may
                 move such a message from the fallback lane to an important
                 (actionable) lane. It never touches a rule hit and never
                 moves anything toward a quieter lane.
  NEWS VALUE     `news_seen.news_value`: a blended hard / analysis / service /
                 promo reading. SHADOW ONLY: it logs how its order differs from
                 the regex order; the Front Page never uses it.
  NOTIFICATIONS  `notification_policy.tier_for` for kinds the policy does not
                 name. Escalate-only: it can raise FYI to NEEDS_YOU, nothing
                 else. NEEDS_YOU and approvals are decided before it is asked.
  MEMORY WRITE   `memory_dreaming._mine_facts` found 0 facts in 215 turns.
                 A sentence reader (preference / bio / decision / deadline /
                 none). It never writes `user_model`: its candidates go to
                 `memory_proposals` as PENDING proposals the owner reviews.

Modes and thresholds come from the decision heads (services/decision_heads,
resources/decision_heads/triage.*.json; decision-models spec DM3, owner
decisions S1 to S9 of 2026-10-24). Every head ships in "shadow". A decision
head in "suggest" or "act" maps to the head's own acting mode below; moving
it there takes Stephen's press on a laya_threshold card, and Laya never
moves it herself:

  triage.mail     off | shadow | fallback   (suggest stays shadow; act = fallback)
  triage.news     off | shadow              (shadow at most, whatever is set)
  triage.notify   off | shadow | escalate   (suggest stays shadow; act = escalate: it can only raise)
  triage.memory   off | shadow | propose    (suggest or act = propose: pending proposals only)

The act threshold of each decision head replaces the old TAU_* constants (kept
below as the shipped values the seed hash covers). A reading below the head's
abstain threshold is "other" ("none" for memory): with no label close enough,
the head says so rather than picking one (spec A9, DM-E08).

The rules it keeps:

  NO CONTENT   Logs under runtime/laya2/ hold hashes and labels: a 16-hex
               digest of the text, the head's label, the incumbent's label, p.
               Never a word of a message, headline, notice or sentence.
  OFF RECORD   While off-record is on, nothing is computed or written.
  NEVER BLOCKS Request-path work (a mail classify, a notification push) waits
               for no model. If the encoder or the prototypes are not ready the
               incumbent answers as it always did. Shadow work runs on its own
               bounded worker thread; a full queue drops the comparison.
  NO NETWORK   Embedding is the pinned local ONNX encoder. Nothing is fetched.
  CORRECTIONS  `note_correction` appends what the owner changed (the existing
               mail reclassify) to a DAgger log: hashes and labels only, plus a
               reference hash so a later trainer can fetch the text from the
               local store itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import queue
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

_log = logging.getLogger("friday.triage_heads")

SEED_SET_ID = "triage-seeds-v1"

HEADS = ("mail", "news", "notify", "memory")
#: The modes each head accepts. The default for every head is "shadow".
MODES = {"mail": ("off", "shadow", "fallback"),
         "news": ("off", "shadow"),
         "notify": ("off", "shadow", "escalate"),
         "memory": ("off", "shadow", "propose")}
DEFAULT_MODE = "shadow"
#: The decision-head mode (services/decision_heads.MODES) to this head's mode.
_FROM_DECISION_MODE = {
    "mail": {"off": "off", "shadow": "shadow", "suggest": "shadow", "act": "fallback"},
    "news": {"off": "off", "shadow": "shadow", "suggest": "shadow", "act": "shadow"},
    "notify": {"off": "off", "shadow": "shadow", "suggest": "shadow", "act": "escalate"},
    "memory": {"off": "off", "shadow": "shadow", "suggest": "propose", "act": "propose"},
}
#: The label a head gives when no label clears its abstain threshold.
ABSTAIN_LABEL = {"mail": "other", "news": "other", "notify": "other", "memory": "none"}
#: Set to any value to turn every head off (a machine that wants none of it).
DISABLE_ENV = "FRIDAY_NO_TRIAGE_HEADS"

#: Thresholds: the shipped act values (resources/decision_heads/triage.*.json
#: carries the same numbers; the decision head's value in force is what is used).
TAU_MAIL = 0.55          # p the head needs to move an unmatched message
TAU_NOTIFY = 0.70        # p the head needs to raise an unknown notice
TAU_MEMORY = 0.60        # p a sentence needs to become a proposal
K = {"mail": 24.0, "news": 24.0, "notify": 24.0, "memory": 24.0}
#: A proposal enters at the same confidence memory_proposals gives a model's
#: guess: under the 0.6 durability gate, so it can only be approved by a person.
PROPOSAL_CONFIDENCE = 0.45

# ---------------------------------------------------------------------------
#  SYNTHETIC SEEDS (invented; nothing a user typed)
# ---------------------------------------------------------------------------

MAIL_SEEDS: Dict[str, List[str]] = {
    "career": [
        "Interview invitation for the senior engineer role",
        "Thanks for applying, we would like to schedule a call",
        "Your job application status has been updated",
        "A recruiter has a new opening that matches your resume",
        "Offer letter attached for your review",
        "Following up on your application for the position",
        "Please confirm your availability for a second interview",
        "The hiring manager would like to chat about the role",
        "Salary range and benefits for the open position",
        "Your resume was viewed by a hiring team",
    ],
    "finance": [
        "Your invoice is due next week",
        "Payment received, thank you",
        "Your bank statement is ready to view",
        "Unusual activity on your card, please verify the charge",
        "Your tax document is available for download",
        "Your credit card payment is overdue",
        "Receipt for your recent purchase",
        "Wire transfer confirmation",
        "Your mortgage payment was processed",
        "Insurance premium renewal notice",
    ],
    "futurespeak": [
        "Project update: the milestone is on track",
        "Can you review the pull request before standup",
        "Client feedback on the latest deliverable",
        "Meeting notes and action items from the sprint review",
        "The release candidate is ready for testing",
        "Agenda for the project kickoff",
        "Please sign off on the design proposal",
        "The build failed on the main branch",
        "Roadmap planning for next quarter",
        "The contract scope needs your approval",
    ],
    "family": [
        "Dinner on Sunday at our place",
        "Photos from the weekend trip",
        "Happy birthday, hope you have a great day",
        "School pickup is changing tomorrow",
        "Can you call me when you get a chance",
        "Mom wants to know if you are coming for the holiday",
        "Family reunion plans and dates",
        "The kids have a recital on Thursday",
        "Grandma is visiting next week",
        "Parent teacher conference scheduling",
    ],
    "subscriptions": [
        "Your weekly newsletter is here",
        "Top stories of the week",
        "Your subscription will renew automatically",
        "New arrivals and a sale this weekend",
        "A digest of the most popular posts",
        "Limited time offer: save twenty percent",
        "Your monthly roundup from the service",
        "Manage your email preferences or unsubscribe",
        "Welcome to our mailing list",
        "Deals picked just for you",
    ],
    "noise": [
        "Verify your email address to continue",
        "Your one time passcode is below",
        "Automated notification, please do not reply",
        "You have a new follower",
        "Someone liked your comment",
        "Rate your recent experience",
        "Your package was delivered",
        "Reminder to complete your profile",
        "Survey: tell us how we did",
        "A new sign in was noticed on your account",
    ],
}

NEWS_LABELS = ("hard", "analysis", "service", "promo")
NEWS_SEEDS: Dict[str, List[str]] = {
    "hard": [
        "Senate passes the budget bill after an overnight vote",
        "Earthquake strikes the coast as rescue crews are dispatched",
        "Court rules on the merger and regulators respond",
        "Central bank raises interest rates by a quarter point",
        "Wildfire forces thousands to evacuate",
        "Police arrest a suspect in the downtown shooting",
        "Unemployment falls to the lowest level this year",
        "President signs an executive order on trade tariffs",
        "Flood warning issued as the river reaches a record level",
        "Company announces layoffs after a quarterly loss",
    ],
    "analysis": [
        "Why the housing market is shifting and what it means",
        "The long read: how the city rebuilt its transit system",
        "Opinion: the case for slower technology",
        "What the new study really tells us about sleep",
        "Inside the strategy behind the team's turnaround",
        "A look back at a decade of streaming",
        "How remote work reshaped downtown",
        "Explaining the debate over school funding",
        "Five lessons from the startup collapse",
        "The quiet rise of local manufacturing",
    ],
    "service": [
        "How to file your taxes before the deadline",
        "The best ways to save on your energy bill this winter",
        "Weather forecast: rain expected through the weekend",
        "Your guide to the weekend's events and road closures",
        "What to do if your flight is cancelled",
        "A simple weeknight pasta recipe",
        "Tips for keeping your phone battery healthy",
        "How to prepare your garden for frost",
        "Where to find free parking downtown",
        "A health checklist for the new school year",
    ],
    "promo": [
        "Sponsored: discover the new smartwatch everyone is talking about",
        "Join us at our annual conference, register today",
        "Paid partner content: the best credit cards",
        "Do not miss our biggest sale of the year",
        "Advertorial: ten products you will love",
        "Subscribe now and get your first month free",
        "Our summit returns and tickets are on sale now",
        "Brought to you by our sponsor: a better mattress",
        "Win a free trip, enter the giveaway",
        "Webinar: sign up to learn from our experts",
    ],
}

NOTIFY_LABELS = ("needs_you", "fyi")
NOTIFY_SEEDS: Dict[str, List[str]] = {
    "needs_you": [
        "Your payment failed and needs a new card",
        "Sign in again: your account connection expired",
        "Action required: approve or decline this request",
        "Your backup failed and nothing was saved",
        "Security alert: a new device signed in to your account",
        "Disk space is almost full and writes will fail",
        "A scheduled job failed three times in a row",
        "Your subscription lapsed, renew to keep access",
        "Calendar conflict: two meetings at the same time",
        "A deadline is today and the task is not done",
    ],
    "fyi": [
        "Finished indexing your documents",
        "A new article is ready to read",
        "Your weekly summary is available",
        "Download complete",
        "Sync finished without problems",
        "A new version is available",
        "Tip: you can ask by voice",
        "Your report was generated",
        "The news edition is ready",
        "Settings were saved",
    ],
}

MEMORY_LABELS = ("preference", "bio", "decision", "deadline", "none")
MEMORY_SEEDS: Dict[str, List[str]] = {
    "preference": [
        "I prefer short answers without extra explanation",
        "I always like my meetings in the morning",
        "I hate it when responses are too long",
        "I usually work best late at night",
        "Please never show me pop up ads",
        "I like dark mode everywhere",
        "I love when you summarise first and give detail later",
        "I tend to read email only twice a day",
    ],
    "bio": [
        "My name is Alex and I live in a small city",
        "I work as a nurse at a clinic",
        "My birthday is in March",
        "I have two kids and a dog",
        "I am a software engineer by training",
        "My email address is for work only",
        "I grew up near the coast",
        "My wife works in education",
    ],
    "decision": [
        "We decided to go with the second vendor",
        "I have chosen the blue option for the kitchen",
        "We agreed to move the launch to next month",
        "I settled on the cheaper plan",
        "I am going with the smaller laptop",
        "We decided to cancel the trip",
        "I picked the earlier flight",
        "We agreed that Friday is the cutoff",
    ],
    "deadline": [
        "My deadline for the report is next Friday",
        "The proposal is due on the fifteenth",
        "I need to submit the form by Monday",
        "The taxes are due in April",
        "The rent is due on the first of every month",
        "I have to finish this before the end of the week",
        "Our launch date is set for the first of June",
        "The application closes on Thursday",
    ],
    "none": [
        "Can you open the settings page",
        "What is the weather like today",
        "Thanks, that works",
        "Run the tests again",
        "Show me the latest news",
        "That looks wrong to me",
        "How do I fix this error",
        "Okay, go ahead",
        "Summarize this article for me",
        "Play something relaxing",
    ],
}

_SEEDS = {"mail": MAIL_SEEDS, "news": NEWS_SEEDS, "notify": NOTIFY_SEEDS,
          "memory": MEMORY_SEEDS}


def content_hash() -> str:
    blob = json.dumps({"id": SEED_SET_ID, "seeds": _SEEDS, "k": K,
                       "tau": [TAU_MAIL, TAU_NOTIFY, TAU_MEMORY]},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
#  EMBEDDING (the shared pinned encoder; one override for tests and --fake)
# ---------------------------------------------------------------------------

_embed_override: Optional[Callable[[List[str]], List[List[float]]]] = None


def set_embedder(fn: Optional[Callable[[List[str]], List[List[float]]]]) -> None:
    """Replace the encoder (tests, and the eval tool's --fake mode). None restores it."""
    global _embed_override
    _embed_override = fn
    reset_cache()


def hashed_embedder(dim: int = 384) -> Callable[[List[str]], List[List[float]]]:
    """A deterministic bag-of-words embedder: lowercase word and word-pair
    features hashed into `dim` buckets, L2-normalised. Not a language model;
    the offline stand-in so the heads' plumbing runs without the encoder."""
    def _one(text: str) -> List[float]:
        words = re.findall(r"[a-z0-9']+", str(text or "").lower())
        feats = list(words) + [a + "_" + b for a, b in zip(words, words[1:])]
        vec = [0.0] * dim
        for f in feats:
            h = int(hashlib.md5(f.encode("utf-8")).hexdigest()[:8], 16)
            vec[h % dim] += 1.0 if (h >> 31) & 1 else -1.0
            vec[(h >> 7) % dim] += 0.5
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]
    return lambda texts: [_one(t) for t in texts]


_avail_cache: List = [0.0, False]
_AVAIL_TTL = 30.0


def available() -> bool:
    """Is the encoder installed? Cached for _AVAIL_TTL seconds: this sits on the
    path of every unmatched message and every notice, and asks the disk."""
    if _embed_override is not None:
        return True
    now = time.monotonic()
    if now - _avail_cache[0] < _AVAIL_TTL:
        return bool(_avail_cache[1])
    try:
        from agent_friday.services import laya2_encoder
        ok = bool(laya2_encoder.is_available())
    except Exception:
        ok = False
    _avail_cache[0], _avail_cache[1] = now, ok
    return ok


def _embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Unit vectors, or None when there is no encoder. Never raises."""
    if not texts:
        return []
    try:
        if _embed_override is not None:
            return _embed_override(list(texts))
        from agent_friday.services import laya2_encoder, turn_vector
        if not laya2_encoder.is_available():
            return None
        # A text the turn has already embedded (services/turn_vector) is not
        # embedded again; peek_full only answers where the cached vector is
        # exactly what embedding this text would give.
        out = [turn_vector.peek_full(t) for t in texts]
        missing = [i for i, v in enumerate(out) if v is None]
        if missing:
            got = laya2_encoder.embed([texts[i] for i in missing])
            if len(got) != len(missing):
                return None
            for i, v in zip(missing, got):
                out[i] = v
        return out
    except Exception as e:  # noqa: BLE001
        _log.debug("triage embed unavailable: %s", e)
        return None


# ---------------------------------------------------------------------------
#  THE HEAD (prototype per label, softmax over cosines)
# ---------------------------------------------------------------------------

def _mean_unit(vectors: Sequence[Sequence[float]]) -> List[float]:
    n = len(vectors)
    acc = [0.0] * len(vectors[0])
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    acc = [x / n for x in acc]
    norm = math.sqrt(sum(x * x for x in acc)) or 1.0
    return [x / norm for x in acc]


def softmax_scores(vec: Sequence[float], protos: Dict[str, Sequence[float]], k: float,
                   allowed: Optional[Iterable[str]] = None) -> Dict[str, float]:
    """P(label) for one unit vector over `allowed` labels (default all). Pure."""
    labels = [l for l in protos if allowed is None or l in set(allowed)]
    if not labels:
        return {}
    cos = {l: sum(a * b for a, b in zip(protos[l], vec)) for l in labels}
    top = max(cos.values())
    exps = {l: math.exp(k * (c - top)) for l, c in cos.items()}
    z = sum(exps.values()) or 1.0
    return {l: round(e / z, 4) for l, e in exps.items()}


class _Head:
    def __init__(self, name: str):
        self.name = name
        self.seeds = _SEEDS[name]
        self.k = K[name]
        self._protos: Optional[Dict[str, List[float]]] = None
        self._lock = threading.Lock()
        self._warming = False

    def _build(self) -> Optional[Dict[str, List[float]]]:
        labels = list(self.seeds)
        texts = [t for l in labels for t in self.seeds[l]]
        vecs = _embed(texts)
        if not vecs or len(vecs) != len(texts):
            return None
        out, i = {}, 0
        for l in labels:
            n = len(self.seeds[l])
            out[l] = _mean_unit(vecs[i:i + n])
            i += n
        return out

    def _warm(self) -> None:
        try:
            built = self._build()
            with self._lock:
                self._protos = built
        finally:
            with self._lock:
                self._warming = False

    def prototypes(self, block: bool = False) -> Optional[Dict[str, List[float]]]:
        """The prototypes, or None while they are being built (a request never
        waits). `block` builds in the calling thread (workers, tools, tests)."""
        with self._lock:
            if self._protos is not None:
                return self._protos
            if not block:
                if self._warming or not available():
                    return None
                self._warming = True
        if block:
            self._warm_blocking()
            with self._lock:
                return self._protos
        threading.Thread(target=self._warm, name="triage-warm-" + self.name,
                         daemon=True).start()
        return None

    def _warm_blocking(self) -> None:
        built = self._build()
        with self._lock:
            self._protos = built

    def reset(self) -> None:
        with self._lock:
            self._protos = None
            self._warming = False

    def score(self, texts: List[str], *, block: bool = False,
              allowed: Optional[Iterable[str]] = None) -> Optional[List[Dict[str, float]]]:
        """One {label: p} per text (batch encode), or None when no reading."""
        protos = self.prototypes(block=block)
        if protos is None or not texts:
            return None
        vecs = _embed([_clip(t) for t in texts])
        if not vecs or len(vecs) != len(texts):
            return None
        return [softmax_scores(v, protos, self.k, allowed) for v in vecs]


_heads: Dict[str, _Head] = {n: _Head(n) for n in HEADS}


def head(name: str) -> _Head:
    return _heads[name]


def reset_cache() -> None:
    """Forget prototypes and decision caches (tests, an encoder or seed change)."""
    for h in _heads.values():
        h.reset()
    with _cache_lock:
        _cache.clear()
        _pending.clear()
        _generation[0] += 1
        for k in _counters_cache:
            _counters_cache[k] = 0
    with _shadow_seen_lock:
        _shadow_seen.clear()


def _clip(text) -> str:
    return " ".join(str(text or "").split())[:400]


def digest(text) -> str:
    """16 hex of the normalised text. What logs hold instead of words."""
    return hashlib.sha256(" ".join(str(text or "").lower().split()).encode("utf-8", "replace")).hexdigest()[:16]


def argmax(scores: Dict[str, float]) -> Tuple[Optional[str], float]:
    if not scores:
        return None, 0.0
    label = max(scores, key=lambda l: scores[l])
    return label, float(scores[label])


# One-text decisions on the request path are cached by digest, so a message
# the list shows twice (and a retry) costs one encode.
#
# STALE WHILE REVALIDATE (DM7 gate). Each entry keeps when it was scored (and,
# for the record only, the decision head's tag then in force: the scores do not
# depend on the tag, only the decision drawn from them does, so a new tag does
# not re-embed anything). An entry older than _CACHE_FRESH_S is still answered
# at once and a fresh reading is queued on the worker, so a request never waits
# for the refresh. The cache is an LRU, so a busy inbox evicts the oldest
# message rather than emptying the whole cache. A generation number, bumped by
# reset_cache (an encoder or seed change), stops a reading that started before
# the reset from being stored after it.
#
# THE REQUEST PATH'S BOUND. A miss is read inline only when the encoder is
# loaded and its lock is free at that moment. That check and the embed are not
# atomic, and a long text's turn-vector peek takes the same lock, so in a rare
# race the read can still wait; laya.ask's request budget
# (TRIAGE_REQUEST_BUDGET_MS, 400 ms) caps that wait and the incumbent answers.
_cache_lock = threading.Lock()
_cache: "OrderedDict[Tuple[str, str], Tuple[float, Optional[str], Dict[str, float]]]" = OrderedDict()
_CACHE_MAX = 512
_CACHE_FRESH_S = 600.0
#: Refreshes share the shadow worker's queue; past this depth they are skipped
#: so shadow work is not crowded out (the stale reading keeps answering).
_REFRESH_MAX_QUEUE = 16
_pending: set = set()
_generation = [0]
_counters_cache = {"hit": 0, "stale": 0, "miss_inline": 0, "miss_queued": 0, "refreshed": 0,
                   "refresh_skipped": 0}


def cache_stats() -> dict:
    return dict(_counters_cache)


def _encoder_free() -> bool:
    """Can one short encode run on this thread without waiting? The encoder must
    be loaded and nobody may hold its lock at this moment. A test embedder is
    always free."""
    if _embed_override is not None:
        return True
    try:
        from agent_friday.services import laya2_encoder
        if (laya2_encoder.status() or {}).get("state") != "ready":
            return False
        lock = getattr(laya2_encoder, "_lock", None)
        return not (lock is not None and lock.locked())
    except Exception:  # noqa: BLE001
        return False


def _store(key, name: str, scores: Dict[str, float], gen: int) -> bool:
    t = tag(name)                                   # outside the lock: it may read a file
    with _cache_lock:
        if gen != _generation[0]:
            return False                            # read before a reset: not stored
        _cache[key] = (time.monotonic(), t, scores)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return True


def _revalidate(name: str, text: str, allowed, key) -> bool:
    """Queue a fresh reading of one text on the worker; at most one per key at a
    time, none while the queue is deep, and none for a turn off the record."""
    if _q.qsize() > _REFRESH_MAX_QUEUE:
        _counters_cache["refresh_skipped"] += 1
        return False
    with _cache_lock:
        if key in _pending:
            return False
        _pending.add(key)
        gen = _generation[0]

    def job():
        try:
            if _off_record():
                return                              # nothing is read off the record
            out = _heads[name].score([text], block=True, allowed=allowed)
            if out and _store(key, name, out[0], gen):
                _counters_cache["refreshed"] += 1
        finally:
            with _cache_lock:
                _pending.discard(key)
    if not _submit(job):
        with _cache_lock:
            _pending.discard(key)
        return False
    return True


def _cached_score(name: str, text: str, *, allowed: Optional[Iterable[str]] = None,
                  block: bool = False, request_path: bool = False) -> Optional[Dict[str, float]]:
    """``request_path``: an act-mode decision on a request or a push. A miss is
    scored inline only when the encoder is free at that moment; otherwise there
    is no reading now (the incumbent answers) and one is queued, so the next
    sighting of the same text has it."""
    key = (name, digest(text) + "|" + ",".join(sorted(allowed or ())))
    allowed = list(allowed) if allowed is not None else None
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
        gen = _generation[0]
    if hit is not None:
        ts, _htag, scores = hit
        if time.monotonic() - ts < _CACHE_FRESH_S:
            _counters_cache["hit"] += 1
        else:
            _counters_cache["stale"] += 1
            _revalidate(name, text, allowed, key)
        return scores
    if request_path and not block and not _encoder_free():
        _counters_cache["miss_queued"] += 1
        _revalidate(name, text, allowed, key)
        return None
    out = _heads[name].score([text], block=block, allowed=allowed)
    if not out:
        if request_path:
            _revalidate(name, text, allowed, key)
        return None
    _counters_cache["miss_inline"] += 1
    _store(key, name, out[0], gen)
    return out[0]


def _read_core(name: str, texts: List[str], *, allowed: Optional[Iterable[str]] = None,
               block: bool = False, cached: bool = False,
               request_path: bool = False) -> Optional[List[Dict[str, float]]]:
    """One {label: p} per text, or None with no reading: the head's own scoring,
    with no contract around it. services/laya's triage presets call this."""
    if cached and len(texts) == 1:
        got = _cached_score(name, texts[0], allowed=allowed, block=block, request_path=request_path)
        return [got] if got else None
    return _heads[name].score(list(texts), block=block, allowed=allowed)


def _read(name: str, texts: List[str], *, allowed: Optional[Iterable[str]] = None,
          block: bool = False, cached: bool = False,
          request_path: bool = False) -> Optional[List[Dict[str, float]]]:
    """The decision calls below read through services/laya.ask (one contract,
    one receipt, a budget), and fall back to the head itself when the contract
    cannot be imported."""
    try:
        from agent_friday.services import laya
    except Exception:
        return _read_core(name, texts, allowed=allowed, block=block, cached=cached, request_path=request_path)
    return laya.triage_read(name, list(texts), allowed=allowed, block=block, cached=cached,
                            request_path=request_path)


# ---------------------------------------------------------------------------
#  SETTINGS, OFF RECORD, LOGS, WORKER
# ---------------------------------------------------------------------------

_mode_cache: Dict[str, Tuple[float, dict]] = {}
#: Forced modes for the eval tool and tests; the decision heads are not consulted for these.
_mode_override: Dict[str, str] = {}
_MODE_TTL = 3.0


def _decision(name: str) -> Optional[dict]:
    """The decision head in force for ``name`` (services/decision_heads), cached
    for _MODE_TTL seconds so a request path reads no file on most calls. None
    when the head is missing or its shipped file was refused: the head is off."""
    now = time.monotonic()
    hit = _mode_cache.get(name)
    if hit and now - hit[0] < _MODE_TTL:
        return hit[1] or None
    try:
        from agent_friday.services import decision_heads
        eff = decision_heads.effective("triage." + name)
    except Exception as e:  # noqa: BLE001
        _log.debug("decision head triage.%s unavailable: %s", name, e)
        eff = None
    _mode_cache[name] = (now, eff or {})
    return eff


def mode(name: str) -> str:
    """The head's mode: from its decision head, mapped to this head's own modes.
    A missing or refused decision head is "off"; nothing defaults to acting."""
    if name in _mode_override:
        return _mode_override[name]
    if os.environ.get(DISABLE_ENV):
        return "off"
    eff = _decision(name)
    if not eff:
        return "off"
    value = _FROM_DECISION_MODE[name].get(str(eff.get("mode")), "off")
    return value if value in MODES[name] else DEFAULT_MODE


def threshold(name: str, which: str = "act") -> float:
    """The decision head's threshold in force (``act`` or ``abstain``), or the
    shipped constant when the decision head cannot be read."""
    eff = _decision(name) or {}
    v = (eff.get("thresholds") or {}).get(which)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if which == "abstain":
        return 0.0
    return {"mail": TAU_MAIL, "notify": TAU_NOTIFY, "memory": TAU_MEMORY}.get(name, 1.0)


def tag(name: str) -> Optional[str]:
    """The decision head's version tag (services/decision_heads), for receipts."""
    return (_decision(name) or {}).get("tag")


def decide(name: str, scores: Dict[str, float]) -> Tuple[Optional[str], float]:
    """(label, p) with the abstain rule: a top reading below the decision head's
    abstain threshold is the head's abstain label, with its p kept."""
    label, p = argmax(scores or {})
    if label is None:
        return None, 0.0
    if p < threshold(name, "abstain"):
        return ABSTAIN_LABEL[name], p
    return label, p


def warm(block: bool = True) -> bool:
    """Build every head's prototypes now (tools, tests). True when all are ready."""
    return all(h.prototypes(block=block) is not None for h in _heads.values())


def clear_mode_cache() -> None:
    _mode_cache.clear()


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record as _off
        return bool(_off.active())
    except Exception:
        return False


def runtime_dir() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / "laya2"


def shadow_path() -> Path:
    return runtime_dir() / "triage_shadow.jsonl"


def corrections_path() -> Path:
    return runtime_dir() / "triage_corrections.jsonl"


_MAX_LOG_BYTES = 2_000_000
_log_lock = threading.Lock()


def _append(path: Path, rows: List[dict]) -> bool:
    if not rows:
        return False
    try:
        with _log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
                path.replace(path.with_suffix(".jsonl.1"))
            with path.open("a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
        return True
    except Exception as e:  # noqa: BLE001
        _log.debug("triage log skipped: %s", e)
        return False


_counters = {"shadow_rows": 0, "applied": 0, "dropped": 0, "failed": 0,
             "unavailable": 0, "proposals": 0, "deduped": 0}


def stats() -> dict:
    return dict(_counters)


#: True runs shadow jobs inline (tests). False uses the bounded worker.
RUN_INLINE = False
_q: "queue.Queue" = queue.Queue(maxsize=32)
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()
_idle = threading.Event()
_idle.set()


def _work() -> None:
    while True:
        job = _q.get()
        _idle.clear()
        try:
            job()
        except Exception as e:  # noqa: BLE001
            _counters["failed"] += 1
            _log.debug("triage shadow job failed: %s", e)
        finally:
            if _q.empty():
                _idle.set()


def _submit(job: Callable[[], None]) -> bool:
    """Run `job` off the caller's thread. A full queue drops it."""
    global _worker
    if not available():
        return False                  # no encoder on this machine: nothing to compare
    if RUN_INLINE:
        try:
            job()
        except Exception as e:  # noqa: BLE001
            _counters["failed"] += 1
            _log.debug("triage shadow job failed: %s", e)
        return True
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="triage-shadow", daemon=True)
            _worker.start()
    try:
        _idle.clear()
        _q.put_nowait(job)
        return True
    except queue.Full:
        _counters["dropped"] += 1
        return False


def drain(timeout: float = 5.0) -> bool:
    """Wait for queued shadow work (tests, tools). True when it finished."""
    return _idle.wait(timeout)


#: SHADOW-ROW DEDUPE (DM7 gate). The same message is classified every time a
#: list is drawn, the same notice can be pushed twice, and the same story sits
#: in many news pools. One row per (head, text digest, incumbent, label, head
#: tag, UTC day) is enough to compare the head with the incumbent; more would
#: weight the comparison towards whatever is redrawn most. Bounded memory: the
#: oldest keys are forgotten first, so after a restart a row may repeat once.
_shadow_seen_lock = threading.Lock()
_shadow_seen: "OrderedDict[tuple, None]" = OrderedDict()
_SHADOW_SEEN_MAX = 8192


def _shadow_key(row: dict) -> tuple:
    # A news row also names its pool, so a story seen in a different pool the
    # same day is logged with that pool's ranking.
    return (row.get("head"), row.get("h"), row.get("incumbent"), row.get("label"), row.get("tag"),
            row.get("pool"),
            time.strftime("%Y-%m-%d", time.gmtime(row.get("ts") or time.time())))


def _append_shadow(rows: List[dict]) -> int:
    """Append the rows not already logged today; returns how many were written."""
    fresh = []
    with _shadow_seen_lock:
        for r in rows:
            k = _shadow_key(r)
            if k in _shadow_seen:
                _shadow_seen.move_to_end(k)
                _counters["deduped"] += 1
                continue
            _shadow_seen[k] = None
            while len(_shadow_seen) > _SHADOW_SEEN_MAX:
                _shadow_seen.popitem(last=False)
            fresh.append(r)
    if fresh and _append(shadow_path(), fresh):
        return len(fresh)
    if fresh:
        # Not written: forget the keys so a later attempt can write them.
        with _shadow_seen_lock:
            for r in fresh:
                _shadow_seen.pop(_shadow_key(r), None)
    return 0


def _shadow_row(head_name: str, text: str, label: Optional[str], incumbent: Optional[str],
                p: float, **extra) -> dict:
    row = {"ts": round(time.time(), 3), "head": head_name, "mode": mode(head_name),
           "h": digest(text), "label": label, "incumbent": incumbent, "p": round(float(p), 3),
           "tag": tag(head_name)}
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
#  1. MAIL LANE
# ---------------------------------------------------------------------------

def mail_text(subject, snippet) -> str:
    return _clip("%s. %s" % (subject or "", snippet or ""))


def mail_label(subject, snippet, lane_ids: Optional[Iterable[str]] = None, *,
               request_path: bool = False) -> Tuple[Optional[str], float]:
    """(lane, p) the head would give this message, or (None, 0) with no reading.
    Never blocks; the first calls after start return no reading while it warms."""
    text = mail_text(subject, snippet)
    if len(text) < 6:
        return None, 0.0
    allowed = [l for l in (lane_ids or MAIL_SEEDS) if l in MAIL_SEEDS]
    got = _read("mail", [text], allowed=allowed, cached=True, request_path=request_path)
    return decide("mail", (got[0] if got else None) or {})


def mail_fallback(subject, snippet, *, fallback: str, lane_ids: Iterable[str],
                  actionable: Iterable[str]) -> Optional[Tuple[str, float, List[str]]]:
    """Called by `message_triage.classify` ONLY when nothing matched at all.

    In "fallback" mode it may return (lane, confidence, reasons) moving the
    message from the fallback lane to an actionable lane. Otherwise None, and
    the caller keeps its fallback. In "shadow" mode it only logs.
    """
    m = mode("mail")
    if m == "off" or _off_record():
        return None
    lane_ids, actionable = list(lane_ids), set(actionable)
    text = mail_text(subject, snippet)
    if m == "shadow":
        def job():
            if _off_record():
                return
            allowed = [l for l in lane_ids if l in MAIL_SEEDS]
            sc = _read("mail", [text], allowed=allowed, block=True)
            if not sc:
                _counters["unavailable"] += 1
                return
            lane, p = decide("mail", sc[0])
            _counters["shadow_rows"] += _append_shadow([_shadow_row("mail", text, lane, fallback, p)])
        _submit(job)
        return None
    lane, p = mail_label(subject, snippet, lane_ids, request_path=True)
    if lane is None:
        _counters["unavailable"] += 1
        return None
    _append_shadow([_shadow_row("mail", text, lane, fallback, p)])
    # Escalate-only: from a quiet fallback to an important lane, and only when
    # the head is sure. Never toward a quieter lane, never off an actionable one.
    if fallback in actionable or lane not in actionable or lane == fallback or p < threshold("mail"):
        return None
    _counters["applied"] += 1
    return lane, round(min(0.5, p * 0.5), 3), [
        "no rule matched; %s's local reader thinks this belongs here" % _assistant()]


# ---------------------------------------------------------------------------
#  2. NEWS VALUE (shadow only)
# ---------------------------------------------------------------------------

#: How much each reading is worth when two orderings are compared: hard news
#: first, as the regex ranking has it.
NEWS_WEIGHT = {"hard": 3.0, "analysis": 2.0, "service": 1.0, "promo": 0.0}
NEWS_BLEND = 0.5        # share of the head in the blended value (the rest is the regex's)


def news_text(item: dict) -> str:
    return _clip(item.get("title") or "")


def news_blend(incumbent: str, scores: Dict[str, float]) -> Tuple[str, float]:
    """(label, value) of the blended reading: the regex's one-hot label mixed
    with the head's probabilities. Pure."""
    mixed = {l: (1.0 - NEWS_BLEND) * (1.0 if l == incumbent else 0.0)
             + NEWS_BLEND * float(scores.get(l, 0.0)) for l in NEWS_LABELS}
    label, _ = argmax(mixed)
    value = sum(NEWS_WEIGHT[l] * mixed[l] for l in NEWS_LABELS)
    return label or incumbent, round(value, 4)


def blended_order(items: List[dict], incumbents: List[str],
                  scores: List[Dict[str, float]]) -> List[int]:
    """Item indices ordered by blended value, best first; ties keep the incoming
    order. Pure: this is what the head WOULD rank, and nothing uses it."""
    vals = [news_blend(inc, sc)[1] for inc, sc in zip(incumbents, scores)]
    return sorted(range(len(items)), key=lambda i: (-vals[i], i))


def news_shadow(items: List[dict], incumbents: Optional[List[str]] = None) -> bool:
    """Log how the blended order differs from the regex order for a ranked
    pool. Changes nothing: the caller's list is only read. True when queued."""
    if mode("news") != "shadow" or _off_record() or not items:
        return False
    try:
        from agent_friday.services import news_seen
        inc = list(incumbents) if incumbents is not None else [news_seen.news_value(i) for i in items]
    except Exception:
        return False
    texts = [news_text(i) for i in items]
    pool = digest("\n".join(texts))

    def job():
        if _off_record():
            return
        sc = _read("news", texts, block=True)
        if not sc:
            _counters["unavailable"] += 1
            return
        order = blended_order(items, inc, sc)
        pos_blend = {idx: pos for pos, idx in enumerate(order)}
        rows = []
        for idx, (t, s, inc_label) in enumerate(zip(texts, sc, inc)):
            lab, p = decide("news", s)
            rows.append(_shadow_row("news", t, lab, inc_label, p,
                                    pos=idx, pos_blend=pos_blend[idx],
                                    blend=news_blend(inc_label, s)[0], pool=pool))
        _counters["shadow_rows"] += _append_shadow(rows)
    return _submit(job)


def note_not_interested(item: dict) -> bool:
    """The hook for a "not interested" signal on a story. The app has no such
    control yet (only a per-source ban); when one exists it calls this, and the
    story's reading becomes a DAgger row."""
    return note_correction("news", text=news_text(item or {}), owner_label="not_interested",
                           incumbent=None, source="news_card")


# ---------------------------------------------------------------------------
#  3. NOTIFICATION FLAG (escalate-only)
# ---------------------------------------------------------------------------

def notification_tier(base: str, kind: str, source: str, priority: str,
                      text: str, *, needs_you: str, known_kinds: Iterable[str]) -> str:
    """The tier after the head's opinion. `base` is the policy's answer.

    Only a base of FYI for a kind the policy does not name can change, and only
    to NEEDS_YOU. Anything else, including a NEEDS_YOU or LOG_ONLY base, comes
    back unchanged. Never raises, never waits for a model.
    """
    try:
        m = mode("notify")
        if m == "off" or base == needs_you or str(kind or "") in set(known_kinds) or _off_record():
            return base
        if base != "fyi" or len(_clip(text)) < 6:
            return base
        text = _clip(text)
        if m == "shadow":
            def job():
                if _off_record():
                    return
                sc = _read("notify", [text], block=True)
                if not sc:
                    _counters["unavailable"] += 1
                    return
                lab, p = decide("notify", sc[0])
                _counters["shadow_rows"] += _append_shadow([_shadow_row("notify", text, lab, base, p)])
            _submit(job)
            return base
        got = _read("notify", [text], cached=True, request_path=True)
        sc = got[0] if got else None
        if not sc:
            _counters["unavailable"] += 1
            return base
        lab, p = decide("notify", sc)
        _append_shadow([_shadow_row("notify", text, lab, base, p)])
        if lab == "needs_you" and p >= threshold("notify"):
            _counters["applied"] += 1
            return needs_you
        return base
    except Exception as e:  # noqa: BLE001
        _log.debug("notification head skipped: %s", e)
        return base


# ---------------------------------------------------------------------------
#  4. MEMORY WRITE (proposals only)
# ---------------------------------------------------------------------------

#: The head's labels in memory_proposals' vocabulary.
MEMORY_CATEGORY = {"preference": "preference", "bio": "bio",
                   "decision": "workflow", "deadline": "constraint"}
MAX_SENTENCES = 200
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def user_sentences(turns: Iterable[dict]) -> List[str]:
    """The owner's own sentences from a day's turns, 12-240 characters each,
    newest last, capped. Assistant turns are never read."""
    out, seen = [], set()
    for t in turns or []:
        if t.get("role") not in (None, "user", "human"):
            continue
        for s in _SENT_SPLIT.split(str(t.get("text") or "")):
            s = s.strip()
            if 12 <= len(s) <= 240 and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
    return out[-MAX_SENTENCES:]


def classify_sentences(sentences: List[str], *, block: bool = True) -> Optional[List[Tuple[str, float]]]:
    sc = _read("memory", sentences, block=block)
    if sc is None:
        return None
    return [decide("memory", s) for s in sc]  # type: ignore[misc]


def memory_pass(turns: List[dict], day: str, regex_facts: Optional[List[dict]] = None) -> dict:
    """Read a day's user sentences. In "shadow" it logs what it would propose
    (hashes and labels, with whether the regex found the same sentence); in
    "propose" it also stores PENDING proposals for the owner to keep or skip.

    It never calls `user_model`. Returns a small report; never raises.
    """
    rep = {"mode": mode("memory"), "read": 0, "candidates": 0, "proposed": 0}
    try:
        if rep["mode"] == "off" or _off_record():
            return rep
        sentences = user_sentences(turns)
        rep["read"] = len(sentences)
        if not sentences:
            return rep
        got = classify_sentences(sentences)
        if got is None:
            _counters["unavailable"] += 1
            return rep
        regex_hit = {" ".join(str(f.get("text") or "").lower().split()) for f in (regex_facts or [])}
        cands, rows = [], []
        for s, (lab, p) in zip(sentences, got):
            if lab and lab != "none" and p >= threshold("memory"):
                cands.append({"category": MEMORY_CATEGORY[lab], "text": s, "label": lab, "p": p})
            rows.append(_shadow_row("memory", s, lab, "regex" if " ".join(s.lower().split()) in regex_hit else "none",
                                    p, day=str(day)))
        rep["candidates"] = len(cands)
        _counters["shadow_rows"] += _append_shadow(rows)
        if rep["mode"] == "propose" and cands:
            from agent_friday.services import memory_proposals
            rep["proposed"] = memory_proposals.propose_from_head(cands, day)
            _counters["proposals"] += rep["proposed"]
    except Exception as e:  # noqa: BLE001
        _counters["failed"] += 1
        _log.debug("memory head skipped: %s", e)
    return rep


# ---------------------------------------------------------------------------
#  CORRECTIONS (DAgger)
# ---------------------------------------------------------------------------

def note_correction(head_name: str, *, text: str, owner_label: str,
                    head_label: Optional[str] = None, incumbent: Optional[str] = None,
                    ref: Optional[str] = None, source: str = "owner") -> bool:
    """One DAgger row: the owner changed (or refused) a reading. Hashes and
    labels only; `ref_h` is the hash of the store's own id for the item, so a
    later trainer can fetch the text from the local store, not from here."""
    if head_name not in HEADS or _off_record() or mode(head_name) == "off":
        return False
    row = {"ts": round(time.time(), 3), "head": head_name, "h": digest(text),
           "ref_h": digest(ref) if ref else None, "head_label": head_label,
           "incumbent": incumbent, "owner_label": str(owner_label)[:40],
           "source": str(source)[:24], "tag": tag(head_name)}
    return _append(corrections_path(), [row])


def note_mail_correction(subject, snippet, *, owner_lane: str, previous_lane: Optional[str] = None,
                         ref: Optional[str] = None) -> bool:
    """The owner moved a message (routes/messages reclassify). Off the request
    thread: when the mail head is off or the owner is off the record it returns
    at once, computing and writing nothing; otherwise the head's reading is
    taken on its worker and one DAgger row is written. True when queued."""
    if mode("mail") == "off" or _off_record():
        return False
    text = mail_text(subject, snippet)

    def job():
        if _off_record():
            return
        sc = _read("mail", [text], block=True)
        head_label = decide("mail", sc[0])[0] if sc else None
        note_correction("mail", text=text, owner_label=owner_lane, head_label=head_label,
                        incumbent=previous_lane, ref=ref, source="reclassify")
    return _submit(job)


def _assistant() -> str:
    try:
        from agent_friday.services import agent_identity
        return agent_identity.assistant_name() or "Friday"
    except Exception:
        return "Friday"


# ---------------------------------------------------------------------------
#  SPEED
# ---------------------------------------------------------------------------

P95_TARGET_MS = 20.0


def percentile(values: Sequence[float], q: float) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    i = max(0, min(len(vals) - 1, int(math.ceil(q * len(vals))) - 1))
    return vals[i]


def measure_ms_per_item(name: str, texts: List[str], *, batch: int = 16,
                        clock: Callable[[], float] = time.perf_counter,
                        repeats: int = 3) -> dict:
    """Per-item cost of a head, in ms, from batch encodes. `clock` is injected
    so a test can prove the arithmetic and the gate without a model."""
    h = _heads[name]
    if h.prototypes(block=True) is None:
        return {"ok": False, "reason": "no encoder"}
    per_item = []
    for _ in range(max(1, repeats)):
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            t0 = clock()
            h.score(chunk, block=True)
            per_item.append((clock() - t0) * 1000.0 / len(chunk))
    p95 = percentile(per_item, 0.95)
    return {"ok": True, "head": name, "items": len(texts), "p50_ms": round(percentile(per_item, 0.5), 2),
            "p95_ms": round(p95, 2), "target_ms": P95_TARGET_MS, "within_target": p95 <= P95_TARGET_MS}
