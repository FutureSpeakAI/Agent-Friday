"""Personal learning for Laya, levels U1 (phrasings) and U2 (calibration).

Spec: ftv/program/reference/laya_swot_and_stack_spec_2026-10-10.md sections d.2,
d.3, d.4, d.7 and e.5 (decisions 2, 3, 10, 11, 12, 14, 15, owner-approved
2026-10-10). Rung: R1. This module is the personal layer ``laya.ask(...,
profile_id=...)`` reads; it makes no decision of its own.

THE ONE RULE (d.2): labelled text is the source of truth. A personal artifact is
a derived thing that can always be rebuilt from the rows, so forgetting a row,
resetting, rolling back or a base update is "delete, then rebuild".

WHAT LEARNS
  gold   an explicit correction (said in a local voice call on this PC, or typed on Friday's own page when
         Laya picks the tool for typed chat: "no, I meant my work calendar"),
         an ask-back answer (the question always offers "something else"), or a
         brain choice the owner later confirmed. These can become U1 phrasings.
  weak   a card approved or declined, an undo inside the window. These tell U2
         whether an action was wanted; they are never phrasings.
  shadow the brain's own choice on a deferred turn (Decision 3). Never a label
         until the owner confirms it (confirm_shadow); until then it only lets
         us measure Laya.
  silence is nothing.

THE GATES (every one has a test)
  - profile: learning is for a signed-in profile only. ``profile_id`` None is the
    shipped base alone. Friday has no per-person profiles today (verified: the
    only principals are the owner and a read-only observer), so this is a
    single-user machine and the one profile is "owner" (single_user_machine()).
  - the owner's own turns only: ``note_routed`` is offered a turn by the router only for ``trusted``
    (the owner's voice connection on this PC, authenticated and a local request: per connection, not
    per speaker, so other voices on the same microphone are heard as the owner unless room mode is
    on) and refuses every surface but "voice" and "chat". A phone, a relay, a tunnel, cloud voice, a
    crew agent and a delegated task never reach it, and a label needs a held turn of the same
    conversation, so a card decision or an answer from anywhere else finds nothing to label. Typed chat
    reaches it only when Laya picks the tool for typed chat (``chat_tool_routing`` = laya), the seat is
    local and the line passed ``reflex_chat.trusted_surface`` (Friday's own page and its session token);
    otherwise nothing typed is held or labelled. A turn the screen rungs placed is not held either.
  - a minor profile (minor_mode, or moderation family mode, or a profile marked
    minor) learns nothing and keeps no text (Decision 14).
  - top-sensitivity turns are never stored, held or counted by their words
    (Decision 12); a classifier that cannot answer counts as sensitive.
  - off the record keeps nothing. With learning off nothing is kept, and a
    switched-off profile's rows are not read.
  - the vault: rows live sealed in the vault, or in the encrypted staging buffer
    while it is locked (services/laya_personal_store). Rebuilds, refits and
    reading rows need the vault open (Decision 10).

U1, PHRASINGS (d.2). A confirmed phrasing becomes one more example for the route
the owner meant, in the router's prototype mean. Two checks first:
  collision check   a phrasing closer to another route's prototype than to its
                    own is a correction for that one turn only, never added.
  sealed rerun      the shipped dev sets (1-3; set 4 is spent and set 5 belongs to
                    the lead, neither is read here) and the personal sealed set are
                    re-scored with the phrasing in; if anything that was right
                    becomes wrong, or a new unwanted action appears, it is rejected
                    with a plain reason. Applied one phrasing at a time, so by
                    induction nothing right at the base is wrong at the end.

PERSONAL SEALED SET (d.4). Drawn from ask-back answers and card outcomes only,
split by time (the newest quarter is held out for the promotion decision and
released into training on a later build), de-duplicated against the training rows
by embedding similarity, and never used as a U1 phrasing. Corrections are never
withheld.

FORGETTING (d.3). ``forget`` deletes the row, deletes every artifact that was
trained on it (rollback history never holds forgotten data), and rebuilds U1
at once. Retention is 180 days (Decision 11, adjustable).

ONE EMBEDDING PER TURN. A row's vector comes from services/turn_vector (the turn's
cached head reading) or the one shared encoder (services/laya2_encoder); nothing
here loads a second MiniLM.

Nothing here sends anything anywhere.
"""
from __future__ import annotations

import logging
import math
import queue
import re
import threading
import time
import uuid
from collections import deque
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from agent_friday.services import laya_personal_store as store

_log = logging.getLogger("friday.laya_personal")

OWNER_PROFILE = "owner"
SETTING_ON = "laya_personal_learning"
SETTING_RETENTION = "laya_personal_retention_days"
SETTING_SHADOW_DAYS = "laya_personal_shadow_days"
DEFAULT_RETENTION_DAYS = 180
DEFAULT_SHADOW_DAYS = 3
#: Artifacts kept for rollback, the live one included (d.4: the last 3, plus base only).
KEEP_ARTIFACTS = 3
MAX_ROWS = store.MAX_ROWS
#: The most phrasings one route takes. Each counts as one more seed in its mean.
MAX_PER_LABEL = 12
MIN_OWN_SIM = 0.30
#: Personal sealed set (d.4).
SEALED_FRACTION = 0.25
MIN_SEALED_TOTAL = 8
DEDUP_SIM = 0.95
ARTIFACT_SUB = store.ARTIFACT_DIR
STATE_BLOB = store.STATE_NAME
#: Windows in which a later event is read as the answer to a routed turn.
UNDO_WINDOW_S = 120.0
ASK_WINDOW_S = 120.0
CORRECTION_WINDOW_S = 120.0
DEFER_WINDOW_S = 600.0
CARD_WINDOW_S = 600.0
MAX_HELD = 20

GOLD, WEAK, SHADOW = "gold", "weak", "shadow"
#: Row kinds -> weight.
KIND_WEIGHT = {"correction": GOLD, "askback": GOLD, "confirmed": GOLD, "card": WEAK, "undo": WEAK, "shadow": SHADOW}
OTHER = "other"          # "something else": none of Laya's routes
#: Never a label (the router's own machinery).
_NOT_A_LABEL = frozenset({"delegate_to_friday", "answer_card", "undo_action", "task_control"})


def _can_label(tool: str) -> bool:
    """Only a tool the router itself can choose (services/laya_router.TOOLS) can be the deeper mind's choice for a
    deferral. Anything else (a write, a lookup on the way, a layout or status call) makes no row."""
    try:
        from agent_friday.services import laya_router
        return tool in laya_router.TOOLS and tool not in _NOT_A_LABEL
    except Exception:  # noqa: BLE001
        return False

NOTICE = ("Friday now learns from your corrections, on this PC only. When you correct her or answer a question "
          "she asks in a local voice call on this PC, or typed on Friday's own page when you have chosen Laya to "
          "pick the tool for typed chat, she keeps those words, sealed in your vault, and checks them against "
          "her test questions before she uses them. She learns from the voice connection on this PC, not from "
          "one speaker: if other people or a TV are heard in the call, they can be heard as you, so turn on room "
          "mode for those calls. She never learns from silence, a paired phone, a relay or cloud voice, and "
          "she never learns from what the deeper mind chose until you confirm it in Settings > Privacy & Data "
          "> What Laya learns about you (\"Waiting for your OK\"). She does "
          "not keep what she can tell is sensitive (money, health, legal, identity), and "
          "when she cannot tell, she keeps nothing. Other personal details, like a name or an address in a "
          "request, can be kept in the sealed vault for 180 days. You can see, undo, forget, reset or wipe all of "
          "it in Settings > Privacy & Data > What Laya learns about you, or by saying \"what have you learned?\" "
          "in a local voice call or typed chat on this PC. Turn it off there.")

#: ``_lock`` guards small in-memory things only (the held turns, counters). The router and the turn path
#: take nothing else. ``_build_lock`` serialises the long jobs (rebuild, refit, forget, reset ...) among
#: themselves; no turn, no tool call and no router worker ever waits on it.
_lock = threading.RLock()
_build_lock = threading.RLock()
now_fn: Callable[[], float] = time.time

# ---------------------------------------------------------------------------
#  seams for tests
# ---------------------------------------------------------------------------

_settings_override: Optional[dict] = None
_embedder: Optional[Callable[[str], Optional[List[float]]]] = None
_base_override: Optional[Tuple[List[str], List[List[float]], Dict[str, int]]] = None
_dev_override: Optional[list] = None
_sensitivity_probe: Optional[Callable[[str], bool]] = None
_scheduler: Optional[Callable[[str, str], None]] = None
_minor_probe: Optional[Callable[[str], bool]] = None


def use_settings(s: Optional[dict]) -> None:
    global _settings_override
    _settings_override = s
    _ls_cache.clear()


def set_embedder(fn) -> None:
    global _embedder
    _embedder = fn
    with _private_lock:
        _private_vecs.clear()


def set_base(base) -> None:
    """(labels, prototypes, seed counts) instead of the router's own. Tests."""
    global _base_override
    _base_override = base


def set_dev_cases(cases) -> None:
    """A list of Case objects instead of the shipped dev sets. Tests."""
    global _dev_override
    _dev_override = cases


def set_sensitivity_probe(fn) -> None:
    global _sensitivity_probe
    _sensitivity_probe = fn


def set_rebuild_scheduler(fn) -> None:
    """Replace the background rebuild trigger (tests run it inline or not at all)."""
    global _scheduler
    _scheduler = fn


def set_minor_probe(fn) -> None:
    global _minor_probe
    _minor_probe = fn
    _ls_cache.clear()


def reset() -> None:
    """Forget every in-memory thing (tests)."""
    global _settings_override, _embedder, _base_override, _dev_override, _sensitivity_probe, _scheduler, _minor_probe
    global capture_inline
    with _lock:
        _held.clear()
        _live_cache.clear()
        _stats.clear()
        _skipped.clear()
        _dev_cache.clear()
        _last_kept_at[0] = 0.0
        _notice_done[0] = False
        _ls_cache.clear()
        _control_turn.clear()
        _cal_cache[1] = None
        with _private_lock:
            _private_vecs.clear()
        _priming.clear()
        _forget_applied.clear()
        _epoch[0] = 0
        while not _capq.empty():
            try:
                _capq.get_nowait()
                _capq.task_done()
            except queue.Empty:
                break
    capture_inline = False
    _settings_override = _embedder = _base_override = _dev_override = None
    _sensitivity_probe = _scheduler = _minor_probe = None


# ---------------------------------------------------------------------------
#  settings and the gates
# ---------------------------------------------------------------------------

def settings() -> dict:
    if _settings_override is not None:
        return _settings_override
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def retention_days(s: Optional[dict] = None) -> float:
    v = (s if s is not None else settings()).get(SETTING_RETENTION, DEFAULT_RETENTION_DAYS)
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = DEFAULT_RETENTION_DAYS
    return max(7.0, min(730.0, v))


def profiles_exist() -> bool:
    """Does Friday keep separate per-person profiles with their own vault stores?
    Verified false for this build: the principals are the owner ("user") and a
    read-only observer, the Library has one owner principal, and voice has no
    speaker verification. The households section (d.7) waits on that."""
    return False


def single_user_machine() -> bool:
    return not profiles_exist()


def current_profile() -> Optional[str]:
    """The profile whose personal layer this call may use: the owner, on this
    single-user machine, for the owner's own process or a request that carries
    the owner's credential. A read-only observer (or anyone else) is None, and
    None is the shipped base alone."""
    try:
        from flask import g, has_request_context
        if has_request_context() and (getattr(g, "friday_principal", "user") or "user") != "user":
            return None
    except Exception:
        pass
    return OWNER_PROFILE


def is_minor(profile_id: Optional[str], s: Optional[dict] = None) -> bool:
    if _minor_probe is not None:
        try:
            return bool(_minor_probe(profile_id))
        except Exception:
            return True
    s = s if s is not None else settings()
    if s.get("minor_mode") is True:
        return True
    try:
        from agent_friday.services import moderation
        if moderation.get_policy().get("family_mode"):
            return True
    except Exception:
        return True                                  # cannot tell whether this is a minor: assume so
    return False


_ls_cache: Dict[str, tuple] = {}
LS_TTL_S = 3.0


def learning_state(profile_id: Optional[str], s: Optional[dict] = None) -> dict:
    """{on, code, why}: may anything be learned or kept for this profile? Read on the router's worker and
    on every routed turn and tool call, so the answer (settings and the moderation policy are files) is
    kept for a few seconds; a settings change reaches her within LS_TTL_S."""
    if s is None and profile_id:
        hit = _ls_cache.get(profile_id)
        if hit is not None and time.monotonic() - hit[0] < LS_TTL_S:
            return hit[1]
        out = _learning_state(profile_id, settings())
        _ls_cache[profile_id] = (time.monotonic(), out)
        return out
    return _learning_state(profile_id, s if s is not None else settings())


def _learning_state(profile_id: Optional[str], s: dict) -> dict:
    if not profile_id:
        return {"on": False, "code": "no_profile", "why": "Nobody is signed in to a profile, so Laya uses her shipped self only."}
    if is_minor(profile_id, s):
        return {"on": False, "code": "minor", "why": "This is a minor profile, so Laya learns nothing and keeps no words."}
    if s.get(SETTING_ON, True) is False:
        return {"on": False, "code": "off_setting", "why": "Personal learning is switched off in Settings > Laya."}
    return {"on": True, "code": "on", "why": "Laya learns from your corrections on this PC."}


def vault_state() -> str:
    """open | locked (a staging key exists, so new words are buffered) | no_key."""
    if store.vault_open():
        return "open"
    return "locked" if store.has_staging_key(OWNER_PROFILE) else "no_key"


def top_sensitivity(text: str, deep: bool = False) -> bool:
    """True when the words are top-sensitivity (tier 3) or when that cannot be told. Such a turn is
    never stored (Decision 12). The fast pass is the regex and keyword layers. ``deep`` also runs the
    embedding layer the privacy gate uses (and Presidio when it is enforcing): that layer catches
    what no keyword names ("find the therapist who does panic attacks"). If a layer that should
    answer cannot, the tier is unknown, and unknown counts as sensitive: nothing is stored."""
    if _sensitivity_probe is not None:
        try:
            return bool(_sensitivity_probe(text))
        except Exception:
            return True
    try:
        from agent_friday.services import sensitivity_classifier as sc
        top = int(sc.Tier.SENSITIVE)
        if int(sc.classify(text, use_presidio=False, use_embeddings=False)) >= top:
            return True
        if not deep:
            return False
        emb_tier, _sim = sc._embedding_tier(text)
        if emb_tier < 0 or emb_tier >= top:
            return True
        try:
            from agent_friday.services.privacy_layers import enforcement_enabled
            enforcing = bool(enforcement_enabled())
        except Exception:
            enforcing = False
        if enforcing and (sc._load_presidio() is None or sc._presidio_tier(text) >= top):
            return True
        return False
    except Exception:
        return True


def sensitivity_ready() -> bool:
    """Can the deep sensitivity pass answer on this PC? If not, Laya keeps nothing, and the report
    card says so rather than showing an empty list."""
    if _sensitivity_probe is not None:
        return True
    try:
        from agent_friday.services import sensitivity_classifier as sc
        return sc._embedding_tier("hello") [0] >= 0
    except Exception:
        return False


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.skip("laya_learning"))
    except Exception:
        return True                                  # cannot tell whether the turn is off the record: assume it is


# ---------------------------------------------------------------------------
#  words for routes
# ---------------------------------------------------------------------------

_SPECIAL_WORDS = {"conversation": "just talking", "act": "making a change for you", OTHER: "something else"}


def route_words(label: Optional[str]) -> str:
    if not label:
        return "nothing"
    if label in _SPECIAL_WORDS:
        return _SPECIAL_WORDS[label]
    try:
        from agent_friday.services import laya_router as lr
        return str(lr.TOOLS[label][0])
    except Exception:
        return str(label).replace("_", " ")


# ---------------------------------------------------------------------------
#  vectors
# ---------------------------------------------------------------------------

def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _unit(v: Sequence[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(text: str) -> Optional[List[float]]:
    """The unit vector for a turn's words, from the turn's one cached embedding
    or the one shared encoder. None when there is no encoder."""
    if _embedder is not None:
        try:
            return _embedder(text)
        except Exception:
            return None
    try:
        from agent_friday.services import laya2_encoder, turn_vector
        v = turn_vector.compute_head(text)
        if v is None and laya2_encoder.is_available():
            (v,) = laya2_encoder.embed([text])
        return list(v) if v is not None else None
    except Exception:
        return None


_private_vecs: Dict[str, List[float]] = {}
_private_lock = threading.Lock()
#: Per profile: has the forget list (services/forget_person tombstones) been applied to what is kept
#: since the last time it could have changed? Until it has, the router is served nothing (``_live``).
_forget_applied: Dict[str, bool] = {}
#: Bumped by wipe, reset and rollback; a captured row from an earlier epoch is dropped, so a row queued
#: before the owner's click cannot land after it.
_epoch = [0]
#: Taken for the final check and the write of a captured row, and to bump the epoch: a row that is still
#: embedding when the owner clicks cannot be written after the click.
_write_lock = threading.Lock()
#: Profiles whose forget list is being applied right now (the purge refreshes the snapshot, which must not
#: ask again).
_applying: set = set()


def _mark_pending(profile: str) -> None:
    """The forget list may have changed: serve nothing, and drop the cached artifact, until it is applied."""
    _forget_applied[profile] = False
    _live_cache.pop(profile, None)


def scrub_memory(pats=None) -> None:
    """Forget the owner's text held in process memory: the vector cache keyed by raw sentences, the held
    turns waiting for an answer, and the last control sentence. With no ``pats`` (every forget of one item,
    and wipe) all of it goes; with the patterns of a forgotten person only what names them goes."""
    if pats is None:
        with _private_lock:
            _private_vecs.clear()
        with _lock:
            _held.clear()
        _control_turn.clear()
        return
    with _private_lock:
        for k in [k for k in _private_vecs if _names_person(k, pats)]:
            _private_vecs.pop(k, None)
    with _lock:
        keep = [e for e in _held if not _names_person(e.get("text"), pats)]
        _held.clear()
        _held.extend(keep)
    if _names_person(str(_control_turn.get("text") or ""), pats):
        _control_turn.clear()


def embed_private(text: str) -> Optional[List[float]]:
    """The same encoder, with a cache of its own. A rebuild's 150 dev texts and the writer's re-reads must
    not push the live turn's vector out of the shared 64-entry turn cache. Bounded; the words are keys
    only in memory."""
    key = " ".join(str(text).split())
    with _private_lock:
        hit = _private_vecs.get(key)
    if hit is not None:
        return hit
    if _embedder is not None:
        try:
            v = _embedder(text)
        except Exception:
            return None
    else:
        try:
            from agent_friday.services import laya2_encoder
            if not laya2_encoder.is_available():
                return None
            (v,) = laya2_encoder.embed([key])
        except Exception:
            return None
    if v is None:
        return None
    v = list(v)
    with _private_lock:
        if len(_private_vecs) >= 512:
            _private_vecs.pop(next(iter(_private_vecs)))
        _private_vecs[key] = v
    return v


def base_state() -> Optional[Tuple[List[str], List[List[float]], Dict[str, int]]]:
    """(labels, shipped prototypes, seed count per label), or None when the
    router's prototypes cannot be built (no encoder)."""
    if _base_override is not None:
        return _base_override
    try:
        from agent_friday.services import laya_router as lr
        labels, protos = lr._prototypes()
        return list(labels), [list(p) for p in protos], {l: len(lr.SEEDS.get(l) or []) or 1 for l in labels}
    except Exception:
        return None


def fold_in(base_proto: Sequence[float], n_seeds: int, phrasings: Sequence[Sequence[float]]) -> List[float]:
    """One more example each in the prototype's mean: the shipped prototype stands
    for its ``n_seeds`` seeds, each phrasing is one more."""
    if not phrasings:
        return list(base_proto)
    acc = [x * max(1, n_seeds) for x in base_proto]
    for p in phrasings:
        acc = [a + b for a, b in zip(acc, p)]
    return _unit(acc)


def adjusted_protos(labels: Sequence[str], base_protos: Sequence[Sequence[float]], seed_counts: Dict[str, int],
                    phrasings: Dict[str, List[List[float]]]) -> List[List[float]]:
    return [fold_in(base_protos[i], seed_counts.get(l, 1), phrasings.get(l) or []) if phrasings.get(l) else list(base_protos[i])
            for i, l in enumerate(labels)]


# ---------------------------------------------------------------------------
#  the sealed rerun: cases, outcomes, a cache of cosines
# ---------------------------------------------------------------------------

class Case:
    """One scored item. ``expect`` is (decision, tool or None) when the answer is
    known; ``forbid`` is a tool the owner declined (weak, no known answer)."""

    __slots__ = ("id", "text", "vec", "expect", "forbid", "group")

    def __init__(self, id, text, vec, expect=None, forbid=None, group="dev"):
        self.id, self.text, self.vec, self.expect, self.forbid, self.group = id, text, vec, expect, forbid, group


def expect_for_label(label: str) -> Optional[Tuple[str, Optional[str]]]:
    if label == "conversation":
        return ("no_tool", None)
    if label == "act":
        return ("defer", None)
    if not label or label == OTHER:
        return None
    return ("tool", label)


def is_right(outcome: Tuple[str, Optional[str]], case: Case) -> bool:
    return case.expect is not None and outcome == case.expect


def is_unwanted(outcome: Tuple[str, Optional[str]], case: Case) -> bool:
    """An unwanted action: the router acted with a tool the owner did not want."""
    if outcome[0] != "tool":
        return False
    if case.forbid and outcome[1] == case.forbid:
        return True
    return case.expect is not None and outcome != case.expect


class Rerun:
    """Cosines of a fixed list of cases against a prototype set, with the router's
    own tier-1 decision per case. ``trial`` replaces one prototype without
    committing; ``commit`` keeps it. Cheap: one column of cosines per change."""

    def __init__(self, labels: Sequence[str], protos: Sequence[Sequence[float]], cases: Sequence[Case],
                 act_threshold: Optional[float] = None):
        from agent_friday.services import laya_router as lr
        self._lr = lr
        self.labels = list(labels)
        self.cases = list(cases)
        self.act = float(lr.ACT_THRESHOLD if act_threshold is None else act_threshold)
        self.protos = [list(p) for p in protos]
        self.sims = [[_dot(c.vec, p) for p in self.protos] for c in self.cases]
        self.outcomes = [self._outcome(i, self.sims[i]) for i in range(len(self.cases))]

    def _outcome(self, i: int, row: Sequence[float]) -> Tuple[str, Optional[str]]:
        c = self.cases[i]
        r = self._lr._decide(self._lr._rank_sims(self.labels, row), c.text, self.act)
        return (r.decision, r.tool if r.decision == "tool" else None)

    def trial(self, j: int, proto: Sequence[float]):
        sims = [list(row) for row in self.sims]
        for i, c in enumerate(self.cases):
            sims[i][j] = _dot(c.vec, proto)
        outs = [self._outcome(i, sims[i]) for i in range(len(self.cases))]
        return sims, outs

    def commit(self, j: int, proto: Sequence[float], trial) -> None:
        self.protos[j] = list(proto)
        self.sims, self.outcomes = trial

    def with_act(self, act: float) -> "Rerun":
        other = Rerun.__new__(Rerun)
        other._lr, other.labels, other.cases = self._lr, self.labels, self.cases
        other.act, other.protos, other.sims = float(act), self.protos, self.sims
        other.outcomes = [other._outcome(i, other.sims[i]) for i in range(len(other.cases))]
        return other


def compare(ref_outcomes, cand_outcomes, cases: Sequence[Case]) -> dict:
    """What a candidate changed against a reference, over ``cases``."""
    flips, new_unwanted, fixed = [], [], 0
    for c, ro, co in zip(cases, ref_outcomes, cand_outcomes):
        r_right, c_right = is_right(ro, c), is_right(co, c)
        if r_right and not c_right:
            flips.append(c)
        elif c_right and not r_right:
            fixed += 1
        if is_unwanted(co, c) and not is_unwanted(ro, c):
            new_unwanted.append(c)
    return {"flips": flips, "new_unwanted": new_unwanted, "fixed": fixed}


_dev_cache: Dict[str, list] = {}


def dev_cases() -> list:
    """The shipped dev sets (1-3) as scored cases: those a prototype (not a sealed rule) decides, with
    their vectors. Set 4 is spent and set 5 is the lead's; neither is read here. The rule check is
    pool-free (laya_router.rule_route), so a busy router cannot make a case drop out. ALL OR NOTHING:
    if any text cannot be embedded, or the sets are missing, this returns [] (and the rerun then adds
    nothing: fail closed). A partial list is never cached."""
    if _dev_override is not None:
        return _dev_override
    cached = _dev_cache.get("cases")
    if cached:
        return cached
    out: list = []
    try:
        import json
        from pathlib import Path
        from agent_friday.services import laya_calibration, laya_router as lr
        res = Path(__file__).resolve().parents[1] / "resources" / "laya_dev_sets.json"
        sets = json.loads(res.read_text(encoding="utf-8"))["sets"]
        laya_calibration.check_fit_sources(sets.keys())          # dev sets 1-3 only, judged by name
        for name, cases in sets.items():
            for k, c in enumerate(cases):
                text = " ".join(str(c["text"]).split())
                if lr.rule_route(text) is not None:
                    continue                                   # a rule decides it; a prototype cannot change it
                v = embed_private(text)
                if v is None:
                    return []
                dec = c["decision"]
                out.append(Case("%s#%d" % (name, k), text, v, (dec, c.get("tool") if dec == "tool" else None), None, "dev"))
    except Exception as e:  # noqa: BLE001
        _log.debug("dev sets not scored: %s", type(e).__name__)
        return []
    if out:
        _dev_cache["cases"] = out
    return out


# ---------------------------------------------------------------------------
#  the held turns, and the capture hooks (called from the router's and agent's seams)
# ---------------------------------------------------------------------------

_held: "deque[dict]" = deque(maxlen=MAX_HELD)
_stats: Dict[str, int] = {}
#: Turns not kept because the words were top-sensitivity, by the route they took.
#: Counts only, no words (the report card says those routes are under-learned).
_skipped: Dict[str, int] = {}

_CORRECTION = re.compile(
    r"^(?:friday,?\s+|laya,?\s+)?(?:"
    r"no+\b[\s,.!]*(?:i\s+(?:meant|mean|said|wanted|was asking)|not (?:that|this|the)|that'?s (?:not|wrong)|wrong|the\b|my\b|look in|check|use)"
    r"|i\s+(?:meant|mean|said|wanted)\b|not that\b|that'?s not what i (?:meant|said|asked|wanted)"
    r"|actually,?\s+i\s+(?:meant|mean|wanted)|wrong one\b)", re.I)
_SOMETHING_ELSE = re.compile(
    r"^(?:friday,?\s+|laya,?\s+)?(?:something else|none of (?:those|them|these)|neither(?: of (?:those|them))?|"
    r"(?:the )?other(?: one)?|not (?:any of )?(?:those|them))\b", re.I)


def is_correction(text: str) -> bool:
    return bool(_CORRECTION.search(" ".join(str(text or "").split())))


def is_something_else(text: str) -> bool:
    return bool(_SOMETHING_ELSE.search(" ".join(str(text or "").split())))


_last_kept_at = [0.0]


def _count(key: str) -> None:
    with _lock:
        _stats[key] = _stats.get(key, 0) + 1


def recent_learning(window: float = 600.0) -> bool:
    """Did Laya keep something from the owner in the last ``window`` seconds? A bare
    "forget that" or "reset" means this only right after she learned something."""
    return now_fn() - _last_kept_at[0] <= window


# What the owner said, read as one of the controls. A control that changes or deletes
# what she learned runs only on the owner's own words in this turn (never on a model's
# say-so, and never on text a page or a mail put in front of it).
REFUSED_LINE = "NOT DONE: that needs the owner's own words in this turn, and there were none for it."
_AGE = [(re.compile(r"\blast week\b|\ba week ago\b", re.I), 7.0), (re.compile(r"\byesterday\b", re.I), 1.0),
        (re.compile(r"\blast month\b|\ba month ago\b", re.I), 30.0)]


def _intent_of(phrase: str) -> str:
    p = re.sub(r"^(?:(?:hey |ok |okay )?(?:friday|laya),?\s+|please\s+|can you\s+|could you\s+)+", "", " ".join(phrase.lower().split()))
    if p.startswith("wipe") or "forget everything" in p:
        return "wipe"
    if p.startswith(("reset", "clear")) or "start over" in p:
        return "reset"
    if p.startswith("go back"):
        return "rollback"
    if "forget" in p or p.startswith("undo that learning"):
        return "forget_last"
    return "summary"


def owner_intent(words: str) -> set:
    """Which control the owner's own utterance asks for. The utterance must match the router's own anchored
    rule for these controls (``laya_router._LEARNING_CONTROL``, or the bare form right after something was
    kept): "what have you learned?" and "reset what you learned" count; "don't forget the milk", "reset the
    timer" and "go back to the previous song" do not, and neither does "don't forget what you learned" (the
    rule is anchored to the whole utterance). The caller passes the turn's own text (the router's hand-off),
    never a rolling buffer."""
    w = " ".join(str(words or "").split())
    if len(w) > 200:
        return set()                                     # a control is a short utterance, not a paragraph or a buffer
    try:
        from agent_friday.services import laya_router as lr
        m = lr._LEARNING_CONTROL.search(w)
        if m:
            return {_intent_of(m.group(0))}
        if lr._LEARNING_BARE.match(w) and recent_learning():
            return {_intent_of(w)}
    except Exception:
        pass
    return set()


def rollback_days(words: str) -> float:
    for rx, d in _AGE:
        if rx.search(str(words or "")):
            return d
    m = re.search(r"\b(\d{1,3})\s+days?\s+ago\b", str(words or ""), re.I)
    return float(m.group(1)) if m else 7.0


def run_control(op: str, words: str, profile: Optional[str] = None) -> dict:
    """Carry out one control, only on the owner's own words in this turn (a model-initiated
    call with none is refused with a fixed line). Read, forget-one, reset and roll back
    run at once: each is the owner's right or can be put back. A WIPE cannot be put back,
    so it only raises a card that a click on the screen approves; a spoken yes never does."""
    profile = profile or current_profile()
    if not profile:
        if op == "summary":
            return {"ok": False, "text": learning_state(profile)["why"]}
        profile = OWNER_PROFILE                           # forgetting never needs a signed-in profile
    if op not in owner_intent(words):
        return {"ok": False, "text": REFUSED_LINE}
    if op != "summary" and not flush_capture(CONTROL_FLUSH_S):
        # The row the owner means may still be on its way to the store; acting now could remove the wrong one.
        return {"ok": False, "text": "NOT DONE: I am still writing down what you just said. Say that again in a moment."}
    if op == "summary":
        return {"ok": True, "text": spoken_summary(profile)}
    if op == "wipe":
        from agent_friday.services import laya_personal_fit as fit
        n = store.staged_count(profile) + (len(store.load_rows(profile) or []) if store.vault_open() else 0)
        card = fit.request_wipe(profile, n)
        return {"ok": False, "needs_screen": True,
                "text": "NEEDS_SCREEN: a wipe deletes %d thing%s I learned and every saved version for good, so only a click on the "
                        "screen can do it. A card is waiting, and Settings > Privacy & Data > What Laya learns about you has the button. "
                        "Nothing has been deleted; reset sets everything aside instead." % (n, "" if n == 1 else "s")
                if card.get("ok") else "NOT DONE: " + str(card.get("why") or "a wipe could not be offered.")}
    # Forget, reset and go back only shrink what is kept, so they run whatever the learning state
    # (switched off, a minor profile): the owner's right to forget never depends on a setting.
    if op == "forget_last":
        out = forget_last(profile)
        done = "Forgot 1 thing"
    elif op == "reset":
        out = reset_to_base(profile)
        done = "Went back to how Laya shipped"
    else:
        out = rollback_by_age(profile, rollback_days(words))
        done = "Went back to an earlier version"
    out.setdefault("text", "Done." if out.get("ok") else out.get("why", "That did not work."))
    if out.get("ok"):
        notify("done:" + op, done, "Laya's learning: " + done.lower() + ". Settings > Privacy & Data > What Laya learns about you shows the rest and can undo it.")
    return out


def notify(what: str, title: str, body: str) -> bool:
    """A visible tray notice (emitter LP1, updates, not safety-critical, stable key). It never
    carries the owner's words. A failure here is never the caller's. True when it was delivered."""
    try:
        import agent_friday.notifications_engine as ne
        return bool(ne.push(title=title, body=body, priority="low", source="laya", kind="laya_learning",
                            emitter="LP1", dedupe_key="laya-learning:" + what))
    except Exception as e:  # noqa: BLE001
        _log.debug("laya notice skipped: %s", type(e).__name__)
        return False


def _same_conv(ent: dict, cid: Optional[str]) -> bool:
    """An entry belongs to a conversation only when BOTH ids exist and are equal. No conversation id,
    no label: a call from a background task or another conversation can never resolve a waiting turn."""
    a = ent.get("conversation_id")
    return bool(a) and bool(cid) and str(a) == str(cid)


def _active(profile_id: Optional[str]) -> bool:
    """May anything be kept for this profile right now? (Counts the decline when
    the turn is off the record.)"""
    if not learning_state(profile_id)["on"]:
        return False
    return not _off_record()


def _room_mode(surface: str) -> bool:
    """A voice call with several people in the room: voices are not told apart, so nothing is learned."""
    if not str(surface).startswith("voice"):
        return False
    try:
        from agent_friday.services.voice_engine import _voice_room_mode
        return bool(_voice_room_mode())
    except Exception:
        return True


#: How an owner names a route in a short reply to "which one do you mean?".
ANSWER_WORDS = {
    "query_calendar": r"\b(?:calendar|schedule|agenda)\b",
    "check_email": r"\b(?:e-?mails?|inbox|mail)\b",
    "search_email": r"\b(?:e-?mails?|inbox|mail)\b",
    "get_briefing": r"\b(?:briefing|brief)\b",
    "search_files": r"\b(?:files?|documents?|folders?)\b",
    "search_wiki": r"\b(?:notes?|wiki)\b",
    "search_web": r"\b(?:web|online|internet)\b",
    "search_news": r"\b(?:news|headlines)\b",
    "search_past_conversations": r"\b(?:past conversations?|earlier chats?|conversations?)\b",
}


_NEGATED = re.compile(r"\b(?:not|no|nope|nah|never|neither|nor|without|don'?t|do not|didn'?t|isn'?t|wasn'?t|won'?t|stop)\b", re.I)


def _named_option(text: str, cands) -> Optional[str]:
    """The ask's option the owner picked, when the reply IS a selection: a short phrase (five words at most,
    not a question or a request) that names exactly one of the ask's own candidates. A fresh request that
    happens to name a candidate ("never mind, what's on my calendar") is not a selection."""
    words = str(text).split()
    if not words or len(words) > 5 or str(text).rstrip().endswith("?"):
        return None
    if _NEGATED.search(str(text)):
        return None                                      # "not the calendar" refuses a route; it never chooses one
    try:
        from agent_friday.services import laya_router as lr
        if lr._REQUEST_FORM.search(text):
            return None
    except Exception:
        return None                                      # cannot tell: not a selection
    hits = [c for c in dict.fromkeys(cands or []) if c in ANSWER_WORDS and re.search(ANSWER_WORDS[c], text, re.I)]
    return hits[0] if len(hits) == 1 else None


#: The surfaces a turn may come from: the local voice call, and typed chat routed by Laya on this PC
#: (services/chat_tool_routing: the setting chosen, a local model, and a line that passed
#: ``reflex_chat.trusted_surface``, i.e. Friday's own page with its session token). ``note_routed``
#: refuses every other surface (cloud voice "voice-live", a phone, a relay, a delegated task); the
#: caller must also pass ``trusted`` to the router.
OWNER_SURFACES = frozenset({"voice", "chat"})
#: Router layers whose answer comes from the owner's screen or the item index, not from the words (see
#: services/laya_router): such a turn is never held for learning.
SCREEN_LAYERS = frozenset({"point_act", "resolver", "safe_write"})

#: The last learning control the owner said as a whole utterance and the router handed over (a rule whose
#: reason is ``laya_router.LEARNING_CONTROL_REASON``). The tool acts on THIS, never on the rolling buffer of
#: what was said minutes ago.
_control_turn: Dict[str, object] = {}
CONTROL_WINDOW_S = 120.0


def _is_control_route(r) -> bool:
    try:
        from agent_friday.services import laya_router as lr
        return getattr(r, "layer", "") == "rule" and getattr(r, "reason", "") == lr.LEARNING_CONTROL_REASON
    except Exception:
        return False


def _note_control_turn(text: str, conversation_id: Optional[str]) -> None:
    text = " ".join(str(text).split())
    ops = owner_intent(text)
    if not ops:
        return
    _control_turn.clear()
    _control_turn.update(op=next(iter(ops)), text=text, at=now_fn(), cid=conversation_id or "")


def note_control_utterance(text: str, conversation_id: Optional[str] = None) -> None:
    """The owner's own current utterance on a surface that does not hand its turns to ``note_routed`` (typed
    chat with the router off, on a page on this PC): the same anchored whole-utterance rule decides whether it is a
    learning control, and nothing else is kept from it. Never raises."""
    try:
        if not conversation_id:
            return                                       # bound to its own conversation, or not armed at all
        t = " ".join(str(text or "").split())
        if t and owner_intent(t):
            _note_control_turn(t, conversation_id)
        elif t and _control_turn.get("text") != t:
            _control_turn.clear()                        # a later sentence replaces the control that came before it
    except Exception:  # noqa: BLE001
        pass


def take_control_turn(conversation_id: Optional[str] = None) -> Optional[Dict[str, object]]:
    """The owner's learning-control utterance of the last two minutes, once. None when there is none, it
    is for another conversation, or it was already used."""
    t = dict(_control_turn)
    if not t or now_fn() - float(t.get("at") or 0) > CONTROL_WINDOW_S:
        return None
    if not conversation_id or not t.get("cid") or str(t["cid"]) != str(conversation_id):
        return None                                      # a control is bound to its own conversation, on both sides
    _control_turn.clear()
    return t


def note_routed(text: str, r, *, surface: str = "voice", conversation_id: Optional[str] = None) -> None:
    """A turn the router just routed (the owner's own words). Held in memory for the answers that may
    follow. A label comes ONLY from the owner's own choice, as the router itself read it: the line after a
    routed turn ("no, I meant my work calendar") routed by a rule or tier 1 to a tool, or an answer to an
    ask-back routed to one of the ask's own options. What the deeper mind decides is never a label here.
    Never raises, never blocks, keeps nothing when learning is off."""
    try:
        if surface not in OWNER_SURFACES:
            return                                               # cloud voice, a phone, a relay: never the owner's own turn here
        profile = current_profile()
        if text and r is not None and _is_control_route(r):
            _note_control_turn(text, conversation_id)             # a learning control, said as a whole utterance
            return
        if not text or r is None or getattr(r, "tool", None) == "answer_card" or not _active(profile):
            return
        if not conversation_id or _room_mode(surface):
            return
        _kick_first_notice(profile)
        text = " ".join(str(text).split())
        now = now_fn()
        with _lock:
            prior = [e for e in _held if not e["resolved"] and _same_conv(e, conversation_id)]
        sens = top_sensitivity(text)
        decision, tool, layer = getattr(r, "decision", ""), getattr(r, "tool", None), getattr(r, "layer", "")
        # A turn the screen rungs placed ("open the second one") means what the screen showed, not what the
        # phrasing says: it is never held, so nothing is learned from it and no deeper-mind choice attaches to it.
        screen_turn = layer in SCREEN_LAYERS
        # A correction is read from Laya's sealed RULES only: the line is not routed by the personal layer, so a
        # learned phrasing cannot label its own next example.
        by_laya = layer == "rule"
        if is_something_else(text) and not sens:
            ask = [e for e in prior if e["decision"] == "ask" and now - e["at"] <= ASK_WINDOW_S]
            if len(ask) == 1:
                ask[0]["resolved"] = True
                _capture(profile, "askback", ask[0], label=OTHER, outcome="meant", source=_source(surface))
                return
        if is_correction(text) and not sens:
            recent = [e for e in prior if now - e["at"] <= CORRECTION_WINDOW_S
                      and e["decision"] in ("tool", "ask", "defer", "no_tool")]
            if recent:
                ent = recent[-1]
                label = None
                if decision == "no_tool" and by_laya:
                    label = "conversation"
                elif decision == "tool" and tool and by_laya and tool not in _NOT_A_LABEL and tool != ent["tool"]:
                    label = tool
                ent["resolved"] = True
                if label:
                    _capture(profile, "correction", ent, label=label, outcome="meant", source=_source(surface))
                return                                           # the correction line is not itself a phrasing
        if not sens:
            ask = [e for e in prior if e["decision"] == "ask" and now - e["at"] <= ASK_WINDOW_S]
            if len(ask) == 1:
                named = _named_option(text, ask[0]["cands"])     # a SELECTION of the ask, not a fresh request
                if named:
                    ask[0]["resolved"] = True
                    _capture(profile, "askback", ask[0], label=named, outcome="meant", source=_source(surface))
                    return
        if sens:
            label = tool or ("conversation" if decision == "no_tool" else "act")
            with _lock:
                _skipped[label] = _skipped.get(label, 0) + 1
            return
        if screen_turn:
            return
        cands = [c[0] for c in (getattr(r, "candidates", None) or []) if c] + ([tool] if tool else [])
        with _lock:
            _held.append({"at": now, "text": text, "decision": decision, "tool": tool,
                          "confidence": getattr(r, "confidence", None), "layer": layer, "cands": cands,
                          "surface": surface, "conversation_id": conversation_id, "resolved": False})
    except Exception as e:  # noqa: BLE001
        _log.debug("note_routed skipped: %s", type(e).__name__)


def _source(surface: str) -> str:
    return "spoken" if str(surface).startswith("voice") else "typed"


def on_delegated(task_id: str, *, conversation_id: Optional[str] = None, request: Optional[str] = None) -> None:
    """The deferred turn just handed to the deeper mind is background task ``task_id`` (the private voice
    hand-over, laya_router_learn.note_delegated). ``request`` is the request the voice route built for that
    turn: only the waiting voice deferral of this conversation whose words it carries is tied to the task, and
    only that task's own tool calls can become its shadow row. A deferral with other words, a typed one, and a
    call with no id, conversation or request tie nothing."""
    try:
        from agent_friday.services import laya_router_learn as _ll
        words = _ll.delegated_words(request)
        if not task_id or not conversation_id or not words or not _active(current_profile()):
            return
        now = now_fn()
        with _lock:
            ent = next((e for e in reversed(_held) if not e["resolved"] and e["decision"] == "defer"
                        and not e.get("task_id") and now - e["at"] <= DEFER_WINDOW_S
                        and str(e.get("surface", "")).startswith("voice")
                        and " ".join(str(e.get("text") or "").split())[:3000] == words[:3000]
                        and _same_conv(e, conversation_id)), None)
            if ent is not None:
                ent["task_id"] = str(task_id)
    except Exception as e:  # noqa: BLE001
        _log.debug("on_delegated skipped: %s", type(e).__name__)


def on_executed(tool: str, args: Optional[dict] = None, *, ok: bool = True, tainted=False, denied: bool = False,
                conversation_id: Optional[str] = None, task_id: Optional[str] = None) -> None:
    """A tool really ran (laya_router_learn.note_executed passes it on from the governed runner, with the
    CALL's own conversation, task and taint). The only thing this resolves is a deferred turn that was handed
    to the deeper mind as ``task_id`` (on_delegated): that task's first call of a tool the router itself
    can choose (_can_label; a write, a layout or status call or the router's own machinery is not one) becomes a SHADOW row, which measures Laya and is never a label until the
    owner confirms it (Decision 3). A call with no task id (an agent loop, typed chat, a voice tool, Laya's
    own routed tool, a cloud voice call), no conversation id, from a tainted turn, a crew agent or a paired
    phone (the caller passes tainted=True for those), a failed or denied call never records anything."""
    try:
        if (not ok or denied or not tool or not _can_label(tool) or tainted is True
                or not conversation_id or not task_id):
            return
        profile = current_profile()
        if not _active(profile):
            return
        now = now_fn()
        with _lock:
            waiting = [e for e in _held if not e["resolved"] and _same_conv(e, conversation_id)
                       and e["decision"] == "defer" and now - e["at"] <= DEFER_WINDOW_S
                       and e.get("task_id") == str(task_id)]
        if len(waiting) != 1:
            return
        ent = waiting[0]
        if callable(tainted):
            try:
                if tainted():
                    return                                       # read untrusted content: never a label
            except Exception:
                return
        ent["resolved"] = True
        _capture(profile, "shadow", ent, label=tool, outcome="meant", source="brain")
    except Exception as e:  # noqa: BLE001
        _log.debug("on_executed skipped: %s", type(e).__name__)


def on_undo(*, conversation_id: Optional[str] = None, tainted=False) -> None:
    """An undo of Friday's change within the window of a routed turn: weak, and
    the routed action was unwanted. ``tainted`` (a bool, or a callable read only once a turn is found)
    is for an undo that came from a turn which read untrusted content: that is never a correction. A
    missing conversation id matches nothing."""
    try:
        profile = current_profile()
        if tainted is True or not conversation_id or not _active(profile):
            return
        now = now_fn()
        with _lock:
            ent = next((e for e in reversed(_held) if not e["resolved"] and e["decision"] in ("tool", "defer")
                        and now - e["at"] <= UNDO_WINDOW_S and _same_conv(e, conversation_id)), None)
        if ent is None:
            return
        if callable(tainted):
            try:
                if tainted():
                    return                                       # read untrusted content: never a correction
            except Exception:
                return
        ent["resolved"] = True
        _capture(profile, "undo", ent, label=None, outcome="unwanted", source="owner")
    except Exception as e:  # noqa: BLE001
        _log.debug("on_undo skipped: %s", type(e).__name__)


def on_card_decision(approved: bool, *, conversation_id: Optional[str] = None) -> None:
    """The owner approved or declined a card shortly after a routed turn: weak. A decline mixes "wrong
    action" with "changed my mind", which is why it is weak. Weak rows tell U2 whether an action was
    wanted; they are not counted toward a loosening offer (the card is not matched to the turn)."""
    try:
        profile = current_profile()
        if not conversation_id or not _active(profile):
            return
        now = now_fn()
        with _lock:
            ent = next((e for e in reversed(_held) if not e["resolved"] and e["decision"] in ("tool", "defer")
                        and now - e["at"] <= CARD_WINDOW_S and _same_conv(e, conversation_id)), None)
        if ent is None:
            return
        ent["resolved"] = True
        if approved and ent["decision"] == "tool" and ent.get("tool"):
            _capture(profile, "card", ent, label=ent["tool"], outcome="wanted", source="owner")
        elif not approved:
            _capture(profile, "card", ent, label=None, outcome="unwanted", source="owner")
    except Exception as e:  # noqa: BLE001
        _log.debug("on_card_decision skipped: %s", type(e).__name__)


#: Capture never runs on the turn's or the tool's thread: a row is queued and one daemon thread embeds it,
#: runs the deep sensitivity pass and appends ONE sealed line (nothing already stored is rewritten).
capture_inline = False
_capq: "queue.Queue" = queue.Queue(maxsize=200)
_writer: List[Optional[threading.Thread]] = [None]


def _capture(profile: str, kind: str, ent: dict, *, label: Optional[str], outcome: str, source: str) -> None:
    """Make one row and hand it to the writer. The words were checked by the fast pass already."""
    text = ent.get("text")
    if not text:
        return
    row = {"id": "lr_" + uuid.uuid4().hex[:12], "at": now_fn(), "kind": kind, "weight": KIND_WEIGHT[kind],
           "text": text, "vec": None, "label": label, "outcome": outcome, "source": source,
           "surface": ent.get("surface"), "_epoch": _epoch[0],
           "routed": {"decision": ent.get("decision"), "tool": ent.get("tool"),
                      "confidence": ent.get("confidence"), "layer": ent.get("layer")},
           "u1": {"status": "pending", "reason": "", "at": None}}
    if capture_inline:
        _persist(profile, row)
        return
    try:
        _capq.put_nowait((profile, row))
    except queue.Full:
        _count("dropped")
        return
    with _lock:
        t = _writer[0]
        if t is None or not t.is_alive():
            t = threading.Thread(target=_write_loop, name="laya-personal-writer", daemon=True)
            _writer[0] = t
            t.start()


def _write_loop() -> None:
    while True:
        try:
            profile, row = _capq.get(timeout=30)
        except queue.Empty:
            with _lock:
                if _capq.empty():
                    _writer[0] = None
                    return
            continue
        batch = [(profile, row)]
        t_end = time.monotonic() + BATCH_WINDOW_S
        while len(batch) < MAX_BATCH:               # rows said close together share one visible job
            try:
                batch.append(_capq.get(timeout=max(0.0, t_end - time.monotonic())))
            except queue.Empty:
                break
        try:
            _persist_batch(batch)
        finally:
            for _ in batch:
                _capq.task_done()


#: Rows queued within this window are embedded together, under one task card.
BATCH_WINDOW_S = 0.5
MAX_BATCH = 20


def _persist_batch(batch) -> None:
    """The writer's batch: the deep sensitivity pass and the embedding of every queued row run on the CPU
    reading model inside ONE visible job (kind ``laya_personal_embed``), never one card per correction."""
    try:
        from agent_friday.services.visible_jobs import note_inference, visible_job
    except Exception:  # noqa: BLE001 - no door, no embedding: the batch is dropped (fail closed), never run bare
        _count("dropped_no_job")
        return
    try:
        with visible_job("Laya learning", kind="laya_personal_embed", workspace="settings", queued=False):
            note_inference("laya-reading-model", local=True)
            _persist_rows(batch)
    except Exception as e:  # noqa: BLE001
        _log.debug("a batch was not kept: %s", type(e).__name__)


def _persist_rows(batch) -> None:
    for profile, row in batch:
        try:
            _persist(profile, row)
        except Exception as e:  # noqa: BLE001
            _log.debug("a row was not kept: %s", type(e).__name__)


CONTROL_FLUSH_S = 10.0


def _settle_capture() -> None:
    """Before a click that wipes, resets or rolls back: let the queued rows land (bounded), then start a
    new epoch, so a row said before the click can never be written after it."""
    flush_capture(CONTROL_FLUSH_S)
    with _write_lock:                                    # waits for a row that is being written right now
        _epoch[0] += 1


def flush_capture(timeout: Optional[float] = None) -> bool:
    """Wait for queued rows to be written (tests, a report that must show what was just said, and every
    control that changes what is kept). True when the queue is empty; False when ``timeout`` ran out first."""
    deadline = None if timeout is None else time.monotonic() + timeout
    with _capq.all_tasks_done:
        while _capq.unfinished_tasks:
            if deadline is None:
                _capq.all_tasks_done.wait()
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _capq.all_tasks_done.wait(left)
    return True


def _persist(profile: str, row: dict) -> Optional[str]:
    """The writer's half: the deep sensitivity pass, the row's vector, one sealed append."""
    text = row.get("text") or ""
    epoch = row.pop("_epoch", _epoch[0])
    if epoch != _epoch[0]:
        _count("dropped_epoch")                           # a wipe, reset or roll back came after this was said
        return None
    if not _active(profile):
        _count("dropped_inactive")                        # learning went off, or the owner went off the record, since
        return None
    if not text or top_sensitivity(text, deep=True):
        label = (row.get("routed") or {}).get("tool") or "act"
        with _lock:
            _skipped[label] = _skipped.get(label, 0) + 1
        return None
    if _names_forgotten_person(text):
        _count("dropped_forgotten_person")                # Friday will not build records about a forgotten person
        return None
    v = embed_private(text)
    if v is not None:
        row["vec"] = [round(float(x), 5) for x in v]
    with _write_lock:
        # The embedding can take seconds. Judge again, now, with the click excluded: a wipe, reset or roll
        # back, learning going off, or the owner going off the record, since the check above.
        if epoch != _epoch[0] or not _active(profile):
            _count("dropped_late")
            return None
        res = keep_row(profile, row)
    _count(res.split(":")[0])
    if res in ("stored", "staged"):
        _last_kept_at[0] = now_fn()
    if res == "stored" and (row["weight"] == GOLD and row.get("label") not in (None, OTHER)
                            or store.over_cap(profile, MAX_ROWS)):
        schedule_rebuild(profile, "a new " + row["kind"])           # a rebuild also trims the oldest rows past the cap
    return res


_notice_done = [False]
_notice_busy = [False]


def _kick_first_notice(profile: str) -> None:
    """Show the first-use notice without the turn waiting on a file stat or a notification push."""
    if _notice_done[0] or _notice_busy[0]:
        return
    if capture_inline:
        _first_notice(profile)
        return
    _notice_busy[0] = True

    def run():
        try:
            _first_notice(profile)
        finally:
            _notice_busy[0] = False
    threading.Thread(target=run, name="laya-personal-notice", daemon=True).start()


def _first_notice(profile: str) -> None:
    """The one-time tray notice that Laya learns from corrections (Decision 2), shown at the first routed
    turn, before anything is kept. The flag is set only after it was delivered, so a failure is retried."""
    if _notice_done[0]:
        return
    try:
        if store.notice_seen(profile):
            _notice_done[0] = True
            return
        if notify("first", "Laya learns from your corrections", NOTICE):
            _notice_done[0] = True
            store.mark_notice_seen(profile)
    except Exception as e:  # noqa: BLE001
        _log.debug("first notice skipped: %s", type(e).__name__)


def keep_row(profile: str, row: dict) -> str:
    """Store one already-gated row: stored, staged or dropped:<why>."""
    if row.get("kind") == "shadow" and row.get("weight") != SHADOW:
        raise ValueError("a shadow row is never a label")
    return store.append(profile, row)


def schedule_rebuild(profile: str, why: str) -> None:
    """Rebuild U1 off the turn's path. A phrasing is on confirmation (d.2)."""
    if _scheduler is not None:
        _scheduler(profile, why)
        return
    threading.Thread(target=lambda: _safe_rebuild(profile, why), name="laya-personal-rebuild", daemon=True).start()


def _safe_rebuild(profile: str, why: str) -> None:
    # rebuild() is itself the visible job (kind laya_personal_rebuild), for every caller.
    try:
        rebuild(profile, why)
    except Exception as e:  # noqa: BLE001
        _log.warning("personal rebuild failed: %s", type(e).__name__)


# ---------------------------------------------------------------------------
#  state and artifacts
# ---------------------------------------------------------------------------

def _state(profile: str) -> dict:
    st = store.read_blob(profile, STATE_BLOB)
    base = {"live": None, "history": [], "pending_u2": None, "notice_ack": False, "last_gate": None,
            "skipped": {}, "last_build": None}
    if isinstance(st, dict):
        base.update(st)
    return base


def _save_state(profile: str, st: dict) -> bool:
    return store.write_blob(profile, STATE_BLOB, st)


def _artifact(profile: str, aid: str) -> Optional[dict]:
    return store.read_blob(profile, aid + ".enc", sub=ARTIFACT_SUB)


def _base_version() -> str:
    try:
        from agent_friday.services import laya
        return str(laya.versions().get("base") or "unknown")
    except Exception:
        return "unknown"


class _Snap:
    """An immutable view of one profile's live artifact, read by the router with no lock and no I/O."""
    __slots__ = ("artifact", "adj")

    def __init__(self, artifact: Optional[dict]):
        self.artifact = artifact
        self.adj = None                                 # (key, protos), set once on first use


_live_cache: Dict[str, _Snap] = {}
_priming: set = set()
K_GRADUATE = 3                                         # Decision 13: confirmations before a guess may act
GRADUATE_SIM = 0.95


def invalidate(profile: Optional[str] = None) -> None:
    """Drop the cached view (the router then uses the shipped base until the next load)."""
    if profile is None:
        _live_cache.clear()
    else:
        _live_cache.pop(profile, None)


def _load_snapshot(profile: str) -> None:
    """Read the live artifact from the store and publish it (an atomic dict set). Runs on a builder
    or the priming thread, never on the router's worker or a turn."""
    if not store.vault_open():
        return
    if not _forget_applied.get(profile) and profile not in _applying and not _apply_forget_list(profile):
        _live_cache.pop(profile, None)                  # the forget list is unreadable: serve nothing
        return
    st = _state(profile)
    art = _artifact(profile, st["live"]) if st.get("live") else None
    _live_cache[profile] = _Snap(art)


def refresh(profile: str) -> None:
    """Replace the cached view with what the store says now (after a build, forget, reset or rollback)."""
    invalidate(profile)
    _load_snapshot(profile)


def _kick_prime(profile: str) -> None:
    if profile in _priming:
        return
    _priming.add(profile)

    def run():
        try:
            _load_snapshot(profile)
        except Exception as e:  # noqa: BLE001
            _log.debug("priming skipped: %s", type(e).__name__)
        finally:
            _priming.discard(profile)
    threading.Thread(target=run, name="laya-personal-prime", daemon=True).start()


def _live(profile: str) -> Optional[dict]:
    """The profile's live artifact, or None for base only. Router-safe: a dict read, no lock, no I/O. A
    profile not loaded yet is the shipped base for that turn while a thread loads it."""
    if not store.vault_open():
        return None
    if not _forget_applied.get(profile):
        _kick_prime(profile)                            # the forget list has not been applied since it could have changed:
        return None                                     # the shipped base serves until it has
    snap = _live_cache.get(profile)
    if snap is None:
        _kick_prime(profile)
        return None
    return snap.artifact


def has_live(profile_id: Optional[str]) -> bool:
    if not profile_id or not learning_state(profile_id)["on"]:
        return False
    art = _live(profile_id)
    return bool(art and art.get("phrasings"))


def version_id(profile_id: Optional[str]) -> str:
    """The personal artifact in force, for the receipt's versions.personal."""
    if not profile_id or not learning_state(profile_id)["on"]:
        return "none"
    art = _live(profile_id)
    return str(art["id"]) if art else "none"


def personal_protos(profile_id: str, labels, protos):
    """The router's prototypes with this profile's live phrasings folded in. The router calls this on
    tier 1; it is a lock-free read of an immutable snapshot."""
    if not learning_state(profile_id)["on"]:
        return labels, protos
    art = _live(profile_id)
    if not art or not art.get("phrasings"):
        return labels, protos
    snap = _live_cache.get(profile_id)
    key = (id(protos), len(protos))
    if snap is not None and snap.adj is not None and snap.adj[0] == key:
        return labels, snap.adj[1]
    by_label: Dict[str, List[List[float]]] = {}
    for p in art["phrasings"]:
        by_label.setdefault(p["label"], []).append(p["vec"])
    seed_counts = (base_state() or ([], [], {}))[2]
    new = adjusted_protos(labels, protos, seed_counts, by_label)
    if snap is not None:
        snap.adj = (key, new)
    return labels, new


def graduation_tool_allowed(tool: str) -> bool:
    """May learning ever let this tool ACT where the base would only ask? Only a read-only, local,
    ring-0 tool the action gate calls internal. An outward tool (search_web, search_email, check_email,
    search_past_conversations, anything at ring 2) or a sensitive one never graduates. The class comes from
    the governed registry, never from a model or a manifest (invariant 2). Cached per tool for the process
    (the registry does not change while she runs)."""
    hit = _grad_tool_cache.get(tool)
    if hit is None:
        hit = _grad_tool_cache[tool] = _graduation_tool_allowed(tool)
    return hit


_grad_tool_cache: Dict[str, bool] = {}


def _graduation_tool_allowed(tool: str) -> bool:
    try:
        from agent_friday.governance import action_gate as ag
        from agent_friday.services import agent, laya_router, tool_catalogue
        if tool in laya_router.ASK_BEFORE_GUESSING:
            return False
        a = tool_catalogue.annotations(tool)
        if a.get("openWorldHint") or a.get("destructiveHint") or not a.get("readOnlyHint"):
            return False
        if int(agent.TOOL_RINGS.get(tool, 2)) != 0:
            return False
        return ag.classify(tool, {})[0] == ag.INTERNAL
    except Exception:
        return False


def graduation_ok(profile_id: str, vec, tool: str) -> bool:
    """Decision-time test for a guess the personal layer would ACT on where the shipped base would not:
    the tool may graduate (above), the owner has confirmed this route at least three times (Decision 13),
    and the utterance is a near-exact match (cosine >= 0.95) of one of those confirmed phrasings."""
    if not tool or not graduation_tool_allowed(tool):
        return False
    art = _live(profile_id)
    mine = [p["vec"] for p in (art or {}).get("phrasings") or [] if p.get("label") == tool]
    if len(mine) < K_GRADUATE:
        return False
    return any(_dot(vec, v) >= GRADUATE_SIM for v in mine)


def suggestion_question(tool: str) -> str:
    return "I think you mean %s. Is that right?" % route_words(tool)


_cal_cache: List[object] = [0.0, None]


def calibration_view() -> Tuple[Optional[float], Optional[float]]:
    """(shipped act threshold, the one in force) for the router head, read from the calibration files at
    most every few seconds: the router and the guard ask on every turn and must not read a file each time.
    An owner-approved loosening calls ``invalidate_thresholds`` so it takes effect at once."""
    now = time.monotonic()
    cached = _cal_cache[1]
    if cached is not None and now - float(_cal_cache[0]) < LS_TTL_S:
        return cached                                    # type: ignore[return-value]
    try:
        from agent_friday.services import laya_calibration as lc
        view = (lc.shipped_threshold("router", "act"), lc.effective_threshold("router", "act"))
    except Exception:
        view = (None, None)
    _cal_cache[0], _cal_cache[1] = now, view
    return view


def invalidate_thresholds() -> None:
    _cal_cache[1] = None


def act_threshold(profile_id: str, floor: float) -> float:
    """What a guess must clear to act for this profile. Invariant 6: never lower
    than the shipped value, except by an owner-approved card (laya_calibration),
    which is the owner's own per-head choice and wins over anything learned."""
    shipped, eff = calibration_view()
    eff = float(eff) if eff is not None else float(floor)
    if shipped is not None and eff < float(shipped) - 1e-9:
        return eff                                               # the owner loosened this head, with their card
    if not learning_state(profile_id)["on"]:
        return max(float(floor), eff)
    art = _live(profile_id)
    learned = ((art or {}).get("u2") or {}).get("act")
    return max(float(floor), eff, float(learned)) if learned is not None else max(float(floor), eff)


# ---------------------------------------------------------------------------
#  U1: build
# ---------------------------------------------------------------------------

def _usable_label(row: dict) -> bool:
    return row.get("label") not in (None, OTHER)


def _ensure_vecs(rows: List[dict]) -> bool:
    """Embed any row that has no vector yet. True when anything changed."""
    changed = False
    for r in rows:
        if r.get("vec") is None and r.get("text"):
            v = embed_private(r["text"])
            if v is not None:
                r["vec"] = [round(float(x), 5) for x in v]
                changed = True
    return changed


def personal_sealed(rows: Sequence[dict], *, vec_key="vec") -> Tuple[List[dict], List[dict]]:
    """(sealed rows, the rest). The sealed set is drawn from ask-back answers and
    card outcomes, the newest quarter by time, minus any row that is a near
    duplicate of a row that trains (so it cannot be memorised). Corrections and
    brain confirmations are never withheld."""
    live = [r for r in rows if not r.get("off") and r.get("weight") != SHADOW and r.get("vec") is not None]
    eligible = sorted((r for r in live if r["kind"] in ("askback", "card")
                       and (_usable_label(r) or r.get("outcome") == "unwanted")), key=lambda r: r["at"])
    if len(eligible) < MIN_SEALED_TOTAL:
        return [], list(rows)
    k = max(1, math.ceil(SEALED_FRACTION * len(eligible)))
    cand = eligible[-k:]
    cand_ids = {r["id"] for r in cand}
    train = [r for r in live if r["id"] not in cand_ids]
    sealed = []
    for r in cand:
        if any(_dot(r[vec_key], t[vec_key]) >= DEDUP_SIM for t in train):
            continue                                              # a near copy of something that trains
        sealed.append(r)
    sealed_ids = {r["id"] for r in sealed}
    return sealed, [r for r in rows if r["id"] not in sealed_ids]


def _sealed_cases(sealed: Sequence[dict]) -> List[Case]:
    out = []
    for r in sealed:
        if r.get("vec") is None or not r.get("text"):
            continue
        if _usable_label(r):
            out.append(Case(r["id"], r["text"], r["vec"], expect_for_label(r["label"]), None, "personal"))
        elif r.get("outcome") == "unwanted" and (r.get("routed") or {}).get("tool"):
            out.append(Case(r["id"], r["text"], r["vec"], None, r["routed"]["tool"], "personal"))
    return out


def _plain_reason(code: str, **kw) -> str:
    if code == "collision":
        return ("This sounds closer to %s than to %s, so I used it for that one request only and did not keep it as an example."
                % (route_words(kw["other"]), route_words(kw["label"])))
    if code == "far":
        return "This is too different from anything I know for %s, so I used it for that one request only." % route_words(kw["label"])
    if code == "cap":
        return "I already keep %d of your phrasings for %s; the newest ones win." % (MAX_PER_LABEL, route_words(kw["label"]))
    if code == "flip_dev":
        return ("Keeping it would have made me get %d of my own shipped test question%s wrong (for example \"%s\"), so I did not keep it."
                % (kw["n"], "" if kw["n"] == 1 else "s", kw["example"]))
    if code == "flip_personal":
        return ("Keeping it would have made me get %d of your own earlier answers wrong, so I did not keep it." % kw["n"])
    if code == "unwanted":
        return "Keeping it would have made me act where I should not, so I did not keep it."
    if code == "no_check":
        return "I could not re-check my shipped test questions just now (my reading model is not ready), so I held off. I will try again."
    if code == "unknown_route":
        return "That route is not one I have any more, so I did not keep it."
    return code


def check_phrasing(vec, label: str, labels: Sequence[str], protos: Sequence[Sequence[float]], dev: Rerun, personal: Optional[Rerun],
                   seed_counts: Dict[str, int], taken: Dict[str, List[List[float]]], base_protos: Sequence[Sequence[float]]):
    """Decide one candidate phrasing against the current prototypes. Returns
    (status, reason, new prototype or None) with status live | correction_only | rejected | pending."""
    if label not in labels:
        return "rejected", _plain_reason("unknown_route"), None
    j = list(labels).index(label)
    if len(taken.get(label, [])) >= MAX_PER_LABEL:
        return "rejected", _plain_reason("cap", label=label), None
    own = _dot(vec, protos[j])
    others = [(_dot(vec, p), labels[i]) for i, p in enumerate(protos) if i != j]
    best_other = max(others) if others else (-1.0, None)
    from agent_friday.services import laya_router as lr
    if own < lr.FLOOR:
        return "correction_only", _plain_reason("far", label=label), None
    if own < best_other[0]:
        return "correction_only", _plain_reason("collision", label=label, other=best_other[1]), None
    if not dev.cases:
        return "pending", _plain_reason("no_check"), None
    new = fold_in(base_protos[j], seed_counts.get(label, 1), taken.get(label, []) + [list(vec)])
    for rr, kind in ((dev, "dev"), (personal, "personal")):
        if rr is None or not rr.cases:
            continue
        trial = rr.trial(j, new)
        res = compare(rr.outcomes, trial[1], rr.cases)
        if res["flips"]:
            if kind == "dev":
                return "rejected", _plain_reason("flip_dev", n=len(res["flips"]), example=res["flips"][0].text), None
            return "rejected", _plain_reason("flip_personal", n=len(res["flips"])), None
        if res["new_unwanted"]:
            return "rejected", _plain_reason("unwanted"), None
    return "live", "Kept: it passed my collision check and every re-check.", new


def rebuild(profile: str, why: str = "") -> dict:
    """Rebuild U1 from the rows, as ONE visible job (kind ``laya_personal_rebuild``). Every rebuild and
    every pass of the reading model over the kept words goes through here: forget, "forget that", undo,
    confirm, forget-person, a correction, the scheduled refit. When the visible-job door cannot be
    opened nothing is embedded (it fails closed) instead of running bare."""
    ls = learning_state(profile)
    if not ls["on"]:                                       # nothing will be embedded: no card for it
        return {"status": "off", "why": ls["why"]}
    if not store.vault_open():
        return {"status": "locked", "why": "The vault is locked, so nothing is rebuilt until it opens."}
    try:
        from agent_friday.services.visible_jobs import note_inference, visible_job
    except Exception:  # noqa: BLE001
        return {"status": "no_job", "why": "The Activity lane is not available, so nothing is rebuilt."}
    with visible_job("Applying your correction", kind="laya_personal_rebuild", workspace="settings", queued=False):
        note_inference("laya-reading-model", local=True)
        return _rebuild(profile, why)


def _rebuild(profile: str, why: str = "") -> dict:
    """The rebuild itself. Needs the vault open and the reading model. Applies the forget list and
    retention, drains the staging buffer, sets each row's U1 status, and writes a new artifact when the
    set of live phrasings changed. Returns a summary."""
    with _build_lock:
        if not learning_state(profile)["on"]:
            return {"status": "off", "why": learning_state(profile)["why"]}
        if not store.vault_open():
            return {"status": "locked", "why": "The vault is locked, so nothing is rebuilt until it opens."}
        if _forgotten_patterns() is None:
            return {"status": "waiting", "why": "The list of people to forget cannot be read, so nothing is rebuilt until it can."}
        enforce_retention_quiet(profile)
        rows = store.load_rows(profile) or []
        base = base_state()
        if base is None:
            return {"status": "no_model", "why": "Laya's reading model is not ready."}
        labels, base_protos, seed_counts = base
        if _ensure_vecs(rows):
            store.replace_rows(profile, rows)
        sealed, _rest = personal_sealed(rows)
        sealed_ids = {r["id"] for r in sealed}
        dev_list = dev_cases()
        if not dev_list:
            # No way to re-check the shipped test questions: change nothing (a live
            # phrasing stays, a new one waits). Fail closed, never open.
            return {"status": "no_check", "why": _plain_reason("no_check")}
        from agent_friday.services import laya_router as _lr
        thr = act_threshold(profile, _lr.ACT_THRESHOLD)           # what is actually in force (learned tightening, owner loosening)
        dev = Rerun(labels, base_protos, dev_list, thr)
        pcases = _sealed_cases(sealed)
        personal = Rerun(labels, base_protos, pcases, thr) if pcases else None
        cands = sorted((r for r in rows if r["weight"] == GOLD and r.get("outcome") == "meant" and _usable_label(r)
                        and not r.get("off") and r["id"] not in sealed_ids and r.get("vec") is not None and r.get("text")),
                       key=lambda r: -r["at"])
        protos = [list(p) for p in base_protos]
        taken: Dict[str, List[List[float]]] = {}
        accepted: List[dict] = []
        stamp = now_fn()
        for r in rows:
            if r["id"] in sealed_ids:
                r["u1"] = {"status": "held_out", "reason": "Held back to check my next update against; it will be used for learning on a later one.", "at": stamp}
            elif r.get("off"):
                r["u1"] = {"status": "off", "reason": "You set this aside.", "at": stamp}
            elif r["weight"] == SHADOW:
                r["u1"] = {"status": "waiting", "reason": "The deeper mind's choice. It teaches me only after you confirm it.", "at": stamp}
            elif r["weight"] == WEAK:
                r["u1"] = {"status": "weak", "reason": "Tells me whether an action was wanted; not used as an example.", "at": stamp}
            elif r.get("label") == OTHER:
                r["u1"] = {"status": "noted", "reason": "None of my routes. Noted for measuring me; no example to add.", "at": stamp}
        for r in cands:
            status, reason, new = check_phrasing(r["vec"], r["label"], labels, protos, dev, personal, seed_counts, taken, base_protos)
            r["u1"] = {"status": status, "reason": reason, "at": stamp}
            if status == "live":
                j = labels.index(r["label"])
                trial_d = dev.trial(j, new)
                dev.commit(j, new, trial_d)
                if personal is not None:
                    personal.commit(j, new, personal.trial(j, new))
                protos[j] = new
                taken.setdefault(r["label"], []).append(list(r["vec"]))
                accepted.append(r)
        # defence in depth: the finished set against the shipped base, once
        base_dev = Rerun(labels, base_protos, dev.cases, thr)
        final = compare(base_dev.outcomes, dev.outcomes, dev.cases)
        if final["flips"] or final["new_unwanted"]:
            for r in accepted:
                r["u1"] = {"status": "rejected", "reason": _plain_reason("flip_dev", n=len(final["flips"]) or 1,
                                                                        example=(final["flips"] or final["new_unwanted"])[0].text), "at": stamp}
            accepted = []
        store.replace_rows(profile, rows)
        st = _state(profile)
        live = _artifact(profile, st["live"]) if st.get("live") else None
        new_ids = sorted(r["id"] for r in accepted)
        old_ids = sorted(p["row"] for p in (live or {}).get("phrasings") or [])
        out = {"status": "ok", "accepted": len(accepted), "candidates": len(cands), "sealed": len(sealed),
               "rejected": sum(1 for r in cands if r["u1"]["status"] == "rejected"),
               "correction_only": sum(1 for r in cands if r["u1"]["status"] == "correction_only"),
               "built": False, "why": why}
        if new_ids != old_ids:
            art = {"id": "a_%d_%s" % (int(stamp), uuid.uuid4().hex[:4]), "created": stamp, "base": _base_version(), "reason": why,
                   "phrasings": [{"row": r["id"], "label": r["label"], "vec": r["vec"]} for r in accepted],
                   "u2": (live or {}).get("u2"),
                   "row_ids": sorted({r["id"] for r in accepted} | set(((live or {}).get("u2") or {}).get("row_ids") or []))}
            _commit_artifact(profile, st, art)
            out["built"] = True
            out["artifact"] = art["id"]
        refresh(profile)
        return out


def enforce_retention(profile: str) -> int:
    """Delete what is older than the retention days (Decision 11), whether or not
    learning is on, and delete every artifact trained on it, as a forget does. The
    locked-vault buffer ages out too. Returns how many rows went. This only DELETES: it never
    rebuilds and never embeds, so a sweep (the report card, the daily job before its checks) cannot run
    the reading model; the next correction, or the gated refit, rebuilds from what is left."""
    with _build_lock:
        n = purge_forgotten_people(profile)              # a person forgotten while the vault was locked goes now
        days = retention_days()
        ids = set(store.expired_ids(profile, days))
        n += store.purge_expired(profile, days)
        over = store.excess_ids(profile, MAX_ROWS)
        if over:
            n += store.delete_ids(profile, over)
            ids |= set(over)
        if ids and store.vault_open():
            st = _state(profile)
            _drop_artifacts_containing(profile, st, ids)
            _save_state(profile, st)
            refresh(profile)
        return n


# ---------------------------------------------------------------------------
#  forgetting a person (Contacts > Forget): services/forget_registry
# ---------------------------------------------------------------------------

def _norm_words(s: str) -> str:
    """The same normalisation forget_person uses for names: lower case, no _ or -, single spaces."""
    return " ".join(str(s or "").lower().replace("_", " ").replace("-", " ").split())


def _person_patterns(names, emails) -> list:
    pats = []
    for n in {_norm_words(x) for x in (names or ())}:
        if len(n) >= 2:
            pats.append(re.compile(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])"))
    for e in {str(x or "").strip().lower() for x in (emails or ())}:
        if "@" in e:
            pats.append(re.compile(re.escape(e)))
    return pats


def _names_person(text: str, pats) -> bool:
    t = _norm_words(text)
    raw = str(text or "").lower()
    return any(p.search(t) or p.search(raw) for p in pats)


def _purge_matching(profile: str, pats, rebuild_after: bool = True) -> int:
    """Delete every kept row whose words name the person, with every saved version built on it, and
    (unless ``rebuild_after`` is False) rebuild from what is left. Needs the vault open and
    ``_build_lock`` held. Returns rows deleted."""
    rows = store.load_rows(profile) or []                 # drains the buffer into the store first
    ids = {r["id"] for r in rows if r.get("id") and _names_person(r.get("text"), pats)}
    if not ids:
        return 0
    n = store.delete_ids(profile, ids)
    st = _state(profile)
    _drop_artifacts_containing(profile, st, ids)
    _save_state(profile, st)
    refresh(profile)
    scrub_memory()
    if rebuild_after and learning_state(profile)["on"]:
        rebuild(profile, "forgot a person")
    return n


def people_find(names, emails) -> int:
    """How many of the owner's kept corrections name the person (the forget registry's finder). With the
    vault locked nothing can be read: the buffered rows are counted, the sealed ones are checked at the
    next unlock."""
    pats = _person_patterns(names, emails)
    if not pats:
        return 0
    profile = current_profile() or OWNER_PROFILE
    rows = store.load_rows(profile)
    if rows is None:
        return store.staged_count(profile)
    return sum(1 for r in rows if _names_person(r.get("text"), pats))


def people_purge(names, emails) -> int:
    """Forget a person: delete every correction that names them, every saved version built from it, and
    anything of theirs still waiting to be written (the forget registry's purger). With the vault
    locked the sealed rows cannot be read, so the buffer is dropped whole, what the router is serving is
    withdrawn until the forget list has been applied, and the sealed rows are removed at the next unlock
    (``_apply_forget_list``, which runs before anything is served or rebuilt); the registry's receipt shows
    the error until then, never a clean zero."""
    pats = _person_patterns(names, emails)
    if not pats:
        return 0
    profile = current_profile() or OWNER_PROFILE
    flush_capture(timeout=5.0)                            # a correction still in the queue is not left behind
    scrub_memory(pats)
    with _build_lock:
        _mark_pending(profile)                            # the forget list changed: serve nothing until it is applied
        if not store.vault_open():
            dropped = store.clear_staging(profile)
            try:
                sealed = (store.profile_dir(profile) / store.LABELS_NAME).exists()
            except Exception:  # noqa: BLE001
                sealed = True
            if not dropped and not sealed:
                _forget_applied[profile] = True
                return 0                                  # nothing was ever kept: nothing to forget
            raise RuntimeError("the vault is locked: %d waiting correction(s) were dropped; the sealed ones are "
                               "removed when it next opens" % dropped)
        _applying.add(profile)
        try:
            n = _purge_matching(profile, pats)
            _forget_applied[profile] = True               # only after the purge has completed
        finally:
            _applying.discard(profile)
        return n


def _forgotten_patterns():
    """Patterns for every person on the forget list (services/forget_person tombstones): a list, empty
    when nobody is forgotten, or None when the list cannot be read. None FAILS CLOSED: nothing is
    captured, served or rebuilt until it is readable."""
    try:
        import json
        from agent_friday.services import forget_person as fp
        p = fp._tombstone_path()
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        entries = data.get("forgotten") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return None
    except Exception:  # noqa: BLE001
        return None
    names, emails = set(), set()
    for e in entries:
        if isinstance(e, dict):
            names.add(e.get("name"))
            names.update(e.get("aliases") or [])
            emails.update(e.get("emails") or [])
    return _person_patterns(names, emails)


def _names_forgotten_person(text: str) -> bool:
    pats = _forgotten_patterns()
    if pats is None:
        return True                                       # the list is unreadable: keep nothing
    return bool(pats) and _names_person(text, pats)


def _apply_forget_list(profile: str) -> bool:
    """Apply the forget list to what is kept: delete the rows that name a forgotten person and every saved
    version built on them, without rebuilding (the caller decides). Safe to call anywhere: it takes the
    build lock and does nothing the owner would not already have asked for. Returns True when the list was
    readable and applied (or the vault is not open, so nothing is served anyway)."""
    if not store.vault_open():
        return True
    pats = _forgotten_patterns()
    if pats is None:
        return False
    with _build_lock:
        _applying.add(profile)                            # the purge refreshes the snapshot, which must not ask again
        try:
            if pats:
                _purge_matching(profile, pats, rebuild_after=False)
            _forget_applied[profile] = True               # only AFTER the purge has completed
        except Exception:
            _mark_pending(profile)
            raise
        finally:
            _applying.discard(profile)
    return True


def purge_forgotten_people(profile: str, rebuild_after: bool = False) -> int:
    """Apply the forget list to the kept corrections: a person forgotten while the vault was locked, or a
    correction that named them after they were forgotten. Needs the vault open; a no-op otherwise.
    Returns rows deleted. It never rebuilds unless asked (``rebuild_after``): a rebuild is a visible job
    and is not started by a sweep."""
    if not store.vault_open():
        return 0
    pats = _forgotten_patterns()
    if not pats:
        return 0
    with _build_lock:
        _applying.add(profile)
        try:
            n = _purge_matching(profile, pats, rebuild_after=rebuild_after)
            _forget_applied[profile] = True
        finally:
            _applying.discard(profile)
        return n


def enforce_retention_quiet(profile: str) -> None:
    """Retention inside a rebuild: delete the old rows and the artifacts built on them,
    without recursing into another rebuild. The forget list is applied here too, so no rebuild ever
    reads a row that names a forgotten person."""
    pats = _forgotten_patterns()
    if pats:
        _purge_matching(profile, pats, rebuild_after=False)
    days = retention_days()
    ids = set(store.expired_ids(profile, days))
    store.purge_expired(profile, days)
    over = store.excess_ids(profile, MAX_ROWS)               # the cap: the oldest rows go, with forget semantics
    if over:
        store.delete_ids(profile, over)
        ids |= set(over)
    if ids:
        st = _state(profile)
        _drop_artifacts_containing(profile, st, ids)
        _save_state(profile, st)
        refresh(profile)


def _commit_artifact(profile: str, st: dict, art: dict) -> None:
    """Write ``art``, make it live, keep the last KEEP_ARTIFACTS."""
    store.write_blob(profile, art["id"] + ".enc", art, sub=ARTIFACT_SUB)
    hist = [art["id"]] + [h for h in st.get("history") or [] if h != art["id"]]
    for old in hist[KEEP_ARTIFACTS:]:
        store.delete_blob(profile, old + ".enc", sub=ARTIFACT_SUB)
    st["history"], st["live"] = hist[:KEEP_ARTIFACTS], art["id"]
    st["last_build"] = {"at": art["created"], "artifact": art["id"], "reason": art.get("reason", "")}
    _save_state(profile, st)
    refresh(profile)


# ---------------------------------------------------------------------------
#  the controls: undo an item, forget, reset, wipe, roll back, confirm
# ---------------------------------------------------------------------------

def _drop_artifacts_containing(profile: str, st: dict, row_ids: set) -> int:
    """Delete every artifact trained on any of ``row_ids`` (rollback history never
    holds forgotten data). Returns how many went."""
    gone = 0
    keep = []
    for aid in st.get("history") or []:
        art = _artifact(profile, aid)
        if art is None or (set(art.get("row_ids") or []) & row_ids):
            store.delete_blob(profile, aid + ".enc", sub=ARTIFACT_SUB)
            gone += 1
        else:
            keep.append(aid)
    st["history"] = keep
    if st.get("live") not in keep:
        st["live"] = None
    pend = st.get("pending_u2")
    if pend and set(pend.get("row_ids") or []) & row_ids:
        st["pending_u2"] = None
    return gone


def forget(profile: str, row_id: str) -> dict:
    """Forget one row for good: delete it, delete every artifact trained on it, and
    rebuild at once from what is left. Needs the vault open (to find the row)."""
    with _build_lock:
        if not store.vault_open():
            return {"ok": False, "why": "The vault is locked. I can forget it once it opens."}
        if not store.delete_row(profile, row_id):
            return {"ok": False, "why": "I do not have that one."}
        scrub_memory()                                    # the sentence is not left in the process either
        st = _state(profile)
        gone = _drop_artifacts_containing(profile, st, {row_id})
        _save_state(profile, st)
        refresh(profile)
        res = rebuild(profile, "forgot an item") if learning_state(profile)["on"] else {"status": "off"}
        return {"ok": True, "artifacts_deleted": gone, "rebuild": res.get("status"),
                "text": "Forgotten, and everything built from it is gone too."}


def forget_last(profile: str) -> dict:
    """"Forget that": the newest thing she learned (by voice or the screen). With the
    vault locked it forgets the newest buffered one without opening it."""
    with _build_lock:
        if not store.vault_open():
            if store.drop_last_staged(profile):
                return {"ok": True, "text": "Forgotten. That was the newest thing waiting for the vault to open."}
            return {"ok": False, "text": "I have nothing waiting to forget, and the vault is locked."}
        _apply_forget_list(profile)                       # a forgotten person's rows are not "the newest thing"
        rows = [r for r in (store.load_rows(profile) or []) if r.get("weight") != SHADOW]
        if not rows:
            return {"ok": False, "text": "There is nothing learned to forget."}
        newest = max(rows, key=lambda r: r["at"])
        out = forget(profile, newest["id"])
        # The owner's words are not repeated here: this text goes back to whichever model is speaking, which may be a cloud one.
        out["text"] = ("Forgotten: the newest thing I learned, as %s. Everything built from it is gone too." % route_words(newest.get("label"))
                       if out.get("ok") else out.get("why", ""))
        return out


def set_item_off(profile: str, row_id: str, off: bool, reason: str = "undo") -> dict:
    """Undo (or put back) one item: it stays stored but stops teaching. Reversible."""
    with _build_lock:
        if not store.vault_open():
            return {"ok": False, "why": "The vault is locked."}
        rows = store.load_rows(profile) or []
        hit = next((r for r in rows if r["id"] == row_id), None)
        if hit is None:
            return {"ok": False, "why": "I do not have that one."}
        if off:
            hit["off"] = reason
        else:
            hit.pop("off", None)
        store.replace_rows(profile, rows)
        st = _state(profile)
        if off:
            _drop_artifacts_containing(profile, st, {row_id})       # an undone item leaves no trace in rollback history
            _save_state(profile, st)
        refresh(profile)
        res = rebuild(profile, "undid an item" if off else "put an item back")
        return {"ok": True, "rebuild": res.get("status")}


def confirm_shadow(profile: str, row_id: str, confirm: bool) -> dict:
    """The owner confirms (or dismisses) a brain choice. Only a confirmation turns
    it into a label (Decision 3); a dismissal forgets it."""
    with _build_lock:
        if not store.vault_open():
            return {"ok": False, "why": "The vault is locked."}
        if not confirm:
            return forget(profile, row_id)
        rows = store.load_rows(profile) or []
        hit = next((r for r in rows if r["id"] == row_id and r.get("kind") == "shadow"), None)
        if hit is None:
            return {"ok": False, "why": "That is not waiting for your OK."}
        hit.update(kind="confirmed", weight=GOLD, outcome="meant")
        store.replace_rows(profile, rows)
        res = rebuild(profile, "you confirmed a choice")
        return {"ok": True, "rebuild": res.get("status")}


def reset_to_base(profile: str) -> dict:
    """Back to the shipped Laya. Every artifact goes; every row is set aside (kept,
    switched off, put back one by one on the report card). Wipe deletes them."""
    _settle_capture()
    with _build_lock:
        n = 0
        if store.vault_open():
            rows = store.load_rows(profile) or []
            for r in rows:
                if not r.get("off"):
                    r["off"] = "reset"
                    n += 1
            store.replace_rows(profile, rows)
        st = _state(profile) if store.vault_open() else {}
        for aid in list(st.get("history") or []):
            store.delete_blob(profile, aid + ".enc", sub=ARTIFACT_SUB)
        if st:
            st.update(live=None, history=[], pending_u2=None)
            _save_state(profile, st)
        refresh(profile)
        return {"ok": True, "set_aside": n,
                "text": "Done. I am back to how I shipped (a score you loosened yourself stays as you set it). Your %d correction%s %s set aside, not deleted. Say wipe to delete them."
                        % (n, "" if n == 1 else "s", "is" if n == 1 else "are")}


def wipe(profile: str) -> dict:
    """Delete everything kept for the profile, including the locked-vault buffer."""
    _settle_capture()
    with _build_lock:
        out = store.wipe(profile)
        refresh(profile)
        scrub_memory()
        return {"ok": not out.get("remaining"), "deleted": out,
                "text": ("Wiped. I deleted %d correction%s, %d waiting in the vault buffer and every saved version. A score you loosened yourself stays as you set it."
                         % (out["rows"], "" if out["rows"] == 1 else "s", out["staged"]))
                if not out.get("remaining") else
                "Part of it is deleted, but a file is still held open by another program (a scanner or an indexer). Try the wipe again in a minute."}


def rollback(profile: str, target: str) -> dict:
    """Make ``target`` (an artifact id, or "base") live. Rows newer than the target
    are set aside so the next rebuild does not just remake what you rolled back from."""
    _settle_capture()
    with _build_lock:
        if not store.vault_open():
            return {"ok": False, "why": "The vault is locked."}
        st = _state(profile)
        if target == "base":
            cutoff = 0.0
            st["live"] = None
        else:
            if target not in (st.get("history") or []):
                return {"ok": False, "why": "That version is not kept any more."}
            art = _artifact(profile, target)
            if art is None:
                return {"ok": False, "why": "That version could not be read."}
            cutoff = float(art["created"])
            st["live"] = target
        rows = store.load_rows(profile) or []
        for r in rows:
            if float(r["at"]) > cutoff and not r.get("off"):
                r["off"] = "rollback"
        store.replace_rows(profile, rows)
        st["pending_u2"] = None
        _save_state(profile, st)
        refresh(profile)
        return {"ok": True, "live": st["live"] or "base", "text": "Rolled back."}


def rollback_by_age(profile: str, days: float = 7.0) -> dict:
    """"Go back to how you were last week": the newest kept version at least ``days``
    old, or base when none is."""
    with _build_lock:
        if not store.vault_open():
            return {"ok": False, "text": "The vault is locked, so I cannot go back yet."}
        st = _state(profile)
        cutoff = now_fn() - float(days) * 86400.0
        pick = None
        for aid in st.get("history") or []:
            art = _artifact(profile, aid)
            if art and float(art["created"]) <= cutoff:
                pick = aid
                break
        out = rollback(profile, pick or "base")
        out["text"] = ("Done. I am back to how I was %d days ago." % int(days) if pick
                       else "I do not keep a version that old, so I went back to how I shipped.") if out.get("ok") else out.get("why", "")
        return out


def acknowledge_notice(profile: str) -> bool:
    if not store.vault_open():
        return False
    st = _state(profile)
    st["notice_ack"] = True
    return _save_state(profile, st)


# ---------------------------------------------------------------------------
#  what she learned: the report card and the spoken summary
# ---------------------------------------------------------------------------

def _item(r: dict) -> dict:
    return {"id": r["id"], "at": r["at"], "kind": r["kind"], "weight": r["weight"], "text": r.get("text"),
            "route": route_words(r.get("label")), "route_id": r.get("label"), "outcome": r.get("outcome"),
            "source": r.get("source"), "off": r.get("off"), "status": (r.get("u1") or {}).get("status"),
            "reason": (r.get("u1") or {}).get("reason")}


def report(profile: Optional[str] = None) -> dict:
    """Everything the report card shows. The owner's own words are in it (it is
    their screen), so it is served to the owner only (routes/laya_learning)."""
    profile = profile or current_profile()
    ls = learning_state(profile)
    vs = vault_state()
    out = {"profile": profile, "single_user_machine": single_user_machine(), "learning": ls, "vault": vs,
           "staged": store.staged_count(profile) if profile else 0, "retention_days": retention_days(),
           "notice": NOTICE, "notice_ack": True, "stats": dict(_stats), "notes": _notes(vs),
           "households": {"profiles_exist": profiles_exist(),
                          "note": ("Friday has one owner profile on this PC, so personal learning is for that profile. "
                                   "Separate profiles for other people do not exist yet; a minor profile will have learning off.")}}
    if not profile or vs != "open":
        out.update(learned=[], rejected=[], waiting=[], under_learned=_under_learned({}), versions=[], u2=None, loosen=[])
        if vs != "open":
            out["vault_note"] = ("The vault is locked, so new corrections are held in an encrypted buffer and nothing is read or "
                                 "rebuilt until it opens." if vs == "locked" else
                                 "There is no vault key on this PC, so Laya cannot keep anything she learns. Set one up first.")
        return out
    enforce_retention(profile)
    rows = store.load_rows(profile) or []
    st = _state(profile)
    out["notice_ack"] = bool(st.get("notice_ack"))
    out["learned"] = [_item(r) for r in sorted(rows, key=lambda r: -r["at"])
                      if r["weight"] != SHADOW and (r.get("u1") or {}).get("status") in ("live", "off", "weak", "noted", "held_out", "pending")]
    out["rejected"] = [_item(r) for r in sorted(rows, key=lambda r: -r["at"])
                       if (r.get("u1") or {}).get("status") in ("rejected", "correction_only")]
    out["waiting"] = [_item(r) for r in sorted(rows, key=lambda r: -r["at"]) if r["weight"] == SHADOW]
    out["under_learned"] = _under_learned(st.get("skipped") or {})
    out["versions"] = []
    for aid in st.get("history") or []:
        a = _artifact(profile, aid)
        if a:
            out["versions"].append({"id": aid, "created": a["created"], "phrasings": len(a.get("phrasings") or []),
                                    "live": aid == st.get("live"), "reason": a.get("reason", "")})
    out["live"] = st.get("live") or "base"
    live = _artifact(profile, st["live"]) if st.get("live") else None
    out["u2"] = {"live": (live or {}).get("u2"), "pending": st.get("pending_u2"), "last_gate": st.get("last_gate")}
    try:
        from agent_friday.services import laya_personal_fit
        out["loosen"] = laya_personal_fit.loosening_offers(profile, rows)
    except Exception:
        out["loosen"] = []
    return out


def _notes(vault: str) -> list:
    """Plain facts the report card states: how sensitivity is judged, when she trains, and what a locked
    vault means for ageing out."""
    out = []
    if not sensitivity_ready():
        out.append("Laya cannot tell on this PC whether something is sensitive, so she is keeping nothing until she can.")
    out.append("She retunes herself only while you are signed in and at the PC (the Windows session unlocked and you "
               "active in the last 30 minutes), on AC power, never overnight. Idle but present is fine.")
    if vault != "open":
        out.append("While the vault key is not available, only the sealed buffer ages out; older corrections and saved "
                   "versions are deleted once it is.")
    return out


def _under_learned(persisted: dict) -> list:
    merged = dict(persisted)
    with _lock:
        for k, v in _skipped.items():
            merged[k] = merged.get(k, 0) + v
    return [{"route": route_words(k), "route_id": k, "skipped": v,
             "why": "Some of what you said here was private or sensitive, so I never keep it, and I learn less about this."}
            for k, v in sorted(merged.items(), key=lambda kv: -kv[1])]


def flush_skipped(profile: str) -> None:
    """Fold the in-memory skipped counts into the state (counts only)."""
    with _build_lock:
        if not _skipped or not store.vault_open():
            return
        st = _state(profile)
        sk = dict(st.get("skipped") or {})
        for k, v in _skipped.items():
            sk[k] = sk.get(k, 0) + v
        st["skipped"] = sk
        _skipped.clear()
        _save_state(profile, st)


def spoken_summary(profile: Optional[str] = None) -> str:
    """"What have you learned?": a short answer for speech. The full list is on the
    report card; this names no more than three things and never reads long text."""
    profile = profile or current_profile()
    ls = learning_state(profile)
    if not ls["on"]:
        return ls["why"]
    vs = vault_state()
    if vs == "no_key":
        return "I cannot keep anything yet, because this PC has no vault key. The report card in Settings, Laya, explains."
    if vs == "locked":
        n = store.staged_count(profile)
        return ("The vault is locked. %d thing%s %s waiting in my sealed buffer, and I will use %s once it opens."
                % (n, "" if n == 1 else "s", "is" if n == 1 else "are", "it" if n == 1 else "them"))
    _apply_forget_list(profile)                           # a forgotten person's rows are not counted
    rows = [r for r in (store.load_rows(profile) or []) if r["weight"] != SHADOW and not r.get("off")]
    live = [r for r in rows if (r.get("u1") or {}).get("status") == "live"]
    rej = [r for r in rows if (r.get("u1") or {}).get("status") in ("rejected", "correction_only")]
    wait = [r for r in (store.load_rows(profile) or []) if r["weight"] == SHADOW]
    if not rows and not wait:
        return "Nothing yet. When you correct me or answer one of my questions, I will keep it here and tell you."
    by: Dict[str, int] = {}
    for r in live:
        by[r["label"]] = by.get(r["label"], 0) + 1
    parts = []
    for lab, n in sorted(by.items(), key=lambda kv: -kv[1])[:3]:
        parts.append("%d for %s" % (n, route_words(lab)))
    s = "I have learned %d of your phrasing%s" % (len(live), "" if len(live) == 1 else "s")
    s += (": " + ", ".join(parts) + ".") if parts else "."
    if rej:
        s += " I turned down %d, and the report card says why." % len(rej)
    if wait:
        s += " %d choice%s from the deeper mind %s waiting for your OK." % (len(wait), "" if len(wait) == 1 else "s", "is" if len(wait) == 1 else "are")
    return s + " Say forget that, reset, or go back to last week, whenever you like."


# ---------------------------------------------------------------------------
#  status for the API and the receipt
# ---------------------------------------------------------------------------

def status(profile: Optional[str] = None) -> dict:
    profile = profile or current_profile()
    ls = learning_state(profile)
    return {"profile": profile, "learning": ls, "vault": vault_state(), "single_user_machine": single_user_machine(),
            "retention_days": retention_days(), "personal_version": version_id(profile), "stats": dict(_stats)}
