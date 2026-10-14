"""The slice-need head: which context sections does this turn need?

Spec: lu_features_spec section 9.5(a) (prompt slicing, with data minimisation
to the cloud), 9.7 (failure modes) and 9.9 (what 1.1 ships).

Prompt assembly (`model_router._build_context_prompt`) already decides which
sections EXIST for a turn, by keyword. This head reads the turn's one shared
vector (services/turn_vector) and, for each of eleven slices, says whether the
turn needs it. In Beta 1.1 it can only ever take sections away:

  SHADOW       Always. The plan (include / withhold / stub, with p) is written
               to the TurnPlan and to the retrieval ledger. It changes nothing
               the model sees unless the owner chose "Send only what each turn
               needs" (setting `cloud_context_slicing` == "slice") AND the turn
               is going to a cloud provider. Learned heads are 1.1.x work; this
               one is a prototype head over synthetic seeds, uncalibrated.
  SUBTRACT     Applying the plan can only drop a section or swap it for one
               fixed stub. It never adds, rewrites or reorders anything, so
               the cloud payload is always a subset of the full payload.
  TAIL ONLY    Only sections after the cache boundary are touched. The head
               (personality, cLaws, honest limits, persona core, tools) and
               `ACTION_PERMISSION_POLICY`, which `seal_system_prompt` appends
               last, are the floor: never sliced. A section this module does
               not name is never sliced either.
  EXPAND       A slice the head is unsure about is kept (local models: any
               p >= TAU_LOW). For a cloud model a PRIVATE slice (tier >= 2)
               that is unsure becomes the fixed stub; a clear no is withheld.
  STUBS        The stub names categories only, from a fixed vocabulary that is
               the same bytes on every turn. It never says what the owner has.
  STICKY       A slice sent to a cloud model in a conversation stays in until
               `reset(conversation)`; follow-ups do not lose their context and
               the prefix stays stable.
  ASKING       `request_context(slice)` goes through `local_context.request`:
               the local model answers, the answer is scrubbed, the egress
               floor runs, and an approval card or an existing grant decides.
               A turn that carries untrusted content (a tool result, a web
               page, imported text) ALWAYS raises a card, even with a grant,
               and never becomes a training label.
  NO CONTENT   Nothing here stores the words of a turn or a section.
  OFF RECORD   No plan, no label, no sticky state, no offer is written.
  NEVER BLOCKS Scoring waits for no model: when the encoder or the prototypes
               are not ready the turn has no slice plan and goes as before.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

_log = logging.getLogger("friday.slice_head")

SEED_SET_ID = "slice-seeds-v1"

#: The eleven slices of the spec's multi-label head (the knowledge-graph slice
#: waits until /api/chat reads the graph at all).
SLICES = ("memory", "wiki", "trust", "career", "tasks", "calendar",
          "project_files", "skills", "screen", "continuity", "tone")
#: Slices whose sections hold the owner's own material. A cloud model that is
#: unsure about one of these gets the stub instead of the text.
PRIVATE_SLICES = ("memory", "wiki", "trust", "career", "tasks", "calendar",
                  "screen", "continuity", "tone")

#: The words a model and an owner see for a slice (stub, tool, receipt).
CATEGORY = {"memory": "memory", "wiki": "notes", "trust": "people",
            "career": "career", "tasks": "tasks", "calendar": "calendar",
            "screen": "screen", "continuity": "last session", "tone": "tone",
            "project_files": "project files", "skills": "skills"}
#: What `request_context` accepts: the private categories, by their words.
REQUESTABLE = tuple(CATEGORY[s] for s in PRIVATE_SLICES)
_SLICE_OF_CATEGORY = {v: k for k, v in CATEGORY.items()}

#: The fixed stub. The same bytes on every turn, whatever was held back: the
#: list is the vocabulary of private categories, not what the owner has.
STUB_TEXT = (
    "\n== CONTEXT HELD BACK ==\n"
    "[Some personal context was not sent. If you need it, call request_context "
    "with one of: " + ", ".join(REQUESTABLE) + ". The user's own computer "
    "answers, and they decide what leaves it.]\n")

#: Starting points for the shadow period, not certified numbers. P is the
#: logistic over the cosine to the slice prototype; `tools/planner_eval.py`
#: replaces these from labelled data (spec 9.8).
K = 14.0
BIAS = 0.40
TAU_HIGH = 0.60
TAU_LOW = 0.25

#: Derived section name (services/retrieval_ledger.derive_section_name) -> slice.
SECTION_SLICE = {
    "today_s_context": "calendar",
    "trust_data_specific_person": "trust",
    "trust_network": "trust",
    "career_ops": "career",
    "active_tasks": "tasks",
    "recent_memories": "memory",
    "relevant_past_conversations_recalled_from_memory": "memory",
    "wiki_briefing_data": "wiki",
    "personal_context_smart_loaded_for_this_turn": "wiki",
    "project_context_files": "project_files",
    "matched_skills_follow_when_relevant": "skills",
    "screen_vision_what_the_user_s_screen_shows": "screen",
}
_SECTION_PREFIX_SLICE = (("continuity_from_your_last_session", "continuity"),)


def section_slice(name: str) -> Optional[str]:
    """The slice a derived section name belongs to, or None (never sliced)."""
    name = str(name or "")
    hit = SECTION_SLICE.get(name)
    if hit:
        return hit
    for prefix, sl in _SECTION_PREFIX_SLICE:
        if name.startswith(prefix):
            return sl
    return None


# ---------------------------------------------------------------------------
#  SYNTHETIC SEEDS (invented; nothing a user typed)
# ---------------------------------------------------------------------------

SEEDS: Dict[str, List[str]] = {
    "memory": [
        "what did we talk about last week",
        "remind me what I told you about that",
        "do you remember what we decided earlier",
        "pick up where we left off yesterday",
        "what was that thing I mentioned before",
        "you said something about this the other day",
        "go back to our earlier conversation about it",
        "did I ever tell you about my preference",
    ],
    "wiki": [
        "what do my notes say about this",
        "look up what I wrote in my wiki",
        "what do I know about my background",
        "check my personal notes for the details",
        "what is in my profile about that",
        "find the page I saved about this topic",
        "what did I record about my family plans",
        "remind me what my notes say on budgeting",
    ],
    "trust": [
        "who is this person to me",
        "how well do I know them",
        "is this contact someone I trust",
        "tell me about my relationship with her",
        "who are the people closest to me",
        "what do I owe my friend",
        "can I rely on this colleague",
        "how long have I known him",
    ],
    "career": [
        "how is my job search going",
        "where do my applications stand",
        "what is in my hiring pipeline",
        "help me prepare for the interview",
        "which roles have I applied to recently",
        "update me on my resume and cover letters",
        "what is my salary range for this role",
        "which companies have I heard back from",
    ],
    "tasks": [
        "what is on my to-do list",
        "what do I still need to finish today",
        "show my open tasks",
        "which tasks are overdue",
        "add this to my task list",
        "what should I work on next from my list",
        "mark that task as done",
        "how many tasks are left this week",
    ],
    "calendar": [
        "what is on my calendar today",
        "what is on my schedule this afternoon",
        "do I have any meetings tomorrow",
        "when is my next appointment",
        "what is the date today",
        "give me my daily briefing",
        "how many days until the deadline",
        "am I free on friday morning",
    ],
    "project_files": [
        "look at the files in this project",
        "what does the readme in this folder say",
        "read the project context file",
        "what are the conventions in this repository",
        "open the agents file for this codebase",
        "check the project instructions before editing",
        "which files are part of this project",
        "follow the guidelines in the project folder",
    ],
    "skills": [
        "use the skill I taught you for this",
        "follow the procedure for this kind of task",
        "run the saved routine for reports",
        "which skills do you have for this job",
        "apply the workflow we built for invoices",
        "do it the way the learned skill says",
        "is there a skill that handles this",
        "use my saved template steps",
    ],
    "screen": [
        "what is on my screen right now",
        "look at what I am looking at",
        "read this window for me",
        "what does this page say",
        "summarize what is showing on my display",
        "help me with what I have open",
        "explain the error on my screen",
        "what is in the window in front of me",
    ],
    "continuity": [
        "continue from our last session",
        "what were we working on last time",
        "carry on with yesterday's work",
        "summarize where the previous session ended",
        "resume what we started earlier",
        "what is still unfinished from before",
        "pick the thread back up from last session",
        "what happened at the end of our last chat",
    ],
    "tone": [
        "I am feeling stressed about all of this",
        "I had a rough day and need a hand",
        "be gentle with me about this one",
        "I am excited and want to celebrate",
        "talk to me like a friend right now",
        "I am nervous about tomorrow",
        "cheer me up a little",
        "I feel overwhelmed and tired",
    ],
}


def content_hash() -> str:
    import hashlib
    blob = json.dumps({"id": SEED_SET_ID, "slices": SLICES, "seeds": SEEDS,
                       "k": K, "bias": BIAS}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
#  THE HEAD (prototype + logistic over the shared turn vector)
# ---------------------------------------------------------------------------

_proto_lock = threading.Lock()
_protos: Optional[Dict[str, List[float]]] = None
_warming = False
_stats = {"scored": 0, "warming": 0, "unavailable": 0}


def _mean_unit(vectors: List[List[float]]) -> List[float]:
    n = len(vectors)
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i in range(dim):
            acc[i] += v[i]
    acc = [x / n for x in acc]
    norm = math.sqrt(sum(x * x for x in acc)) or 1.0
    return [x / norm for x in acc]


def _build_prototypes() -> Dict[str, List[float]]:
    from agent_friday.services import laya2_encoder
    texts = [t for s in SLICES for t in SEEDS[s]]
    vecs = laya2_encoder.embed(texts)
    out, i = {}, 0
    for s in SLICES:
        k = len(SEEDS[s])
        out[s] = _mean_unit(vecs[i:i + k])
        i += k
    return out


def _warm() -> None:
    global _protos, _warming
    try:
        built = _build_prototypes()
        with _proto_lock:
            _protos = built
    except Exception as e:  # noqa: BLE001
        _log.debug("slice prototypes not built: %s", e)
    finally:
        with _proto_lock:
            _warming = False


def prototypes(block: bool = False) -> Optional[Dict[str, List[float]]]:
    """The slice prototypes, or None while they are being built.

    The first call starts the build on a background thread (the seeds are
    embedded once) and returns None, so a turn never waits for it. `block`
    builds in the calling thread (tests, tools).
    """
    global _warming
    with _proto_lock:
        if _protos is not None:
            return _protos
        if block:
            pass
        elif _warming:
            return None
        else:
            _warming = True
    if block:
        _warm()
        with _proto_lock:
            return _protos
    threading.Thread(target=_warm, name="slice-head-warm", daemon=True).start()
    return None


def reset_cache() -> None:
    """Forget the prototypes (tests, and after a seed or encoder change)."""
    global _protos, _warming
    with _proto_lock:
        _protos = None
        _warming = False
        for k in _stats:
            _stats[k] = 0


def p_of(cosine: float) -> float:
    return 1.0 / (1.0 + math.exp(-K * (float(cosine) - BIAS)))


def score_vector(vec: List[float], protos: Dict[str, List[float]]) -> Dict[str, float]:
    """P(slice is needed) per slice for one unit vector. Pure."""
    out = {}
    for s in SLICES:
        proto = protos.get(s)
        if proto is None:
            continue
        cos = sum(a * b for a, b in zip(proto, vec))
        out[s] = round(p_of(cos), 4)
    return out


def scores_for(text: str, *, block: bool = False) -> Optional[Dict[str, float]]:
    """{slice: p} for a turn's text, or None when there is no reading.

    None (never a guess) when the text is empty, the encoder is missing or the
    prototypes are still warming. Uses the turn's shared vector.
    """
    from agent_friday.services import turn_vector
    protos = prototypes(block=block)
    if protos is None:
        _stats["warming"] += 1
        return None
    vec = turn_vector.compute(text)
    if vec is None:
        _stats["unavailable"] += 1
        return None
    _stats["scored"] += 1
    return score_vector(vec, protos)


def stats() -> dict:
    return dict(_stats)


# ---------------------------------------------------------------------------
#  THE DECISION
# ---------------------------------------------------------------------------

def decide(slice_name: str, p: Optional[float], *, private: bool, cloud: bool,
           sticky: bool = False) -> Tuple[str, str]:
    """(action, reason) for one slice. Pure.

    Unknown is include: no reading means the section goes as it always did.
    """
    if p is None:
        return "include", "no reading"
    if sticky:
        return "include", "sticky"
    if p >= TAU_HIGH:
        return "include", "needed"
    if p >= TAU_LOW:
        if cloud and private:
            return "stub", "unsure; private"
        return "include", "unsure; kept"
    return "withhold", "not needed"


def plan_slices(scores: Optional[Dict[str, float]], *, cloud: bool,
                sticky: Iterable[str] = ()) -> Dict[str, dict]:
    """The TurnPlan.slices dict: {slice: {action, p, reason}}. Pure.

    {} when there are no scores.
    """
    if not scores:
        return {}
    sticky = set(sticky)
    out = {}
    for s in SLICES:
        if s not in scores:
            continue
        action, reason = decide(s, scores[s], private=s in PRIVATE_SLICES,
                                cloud=cloud, sticky=s in sticky)
        out[s] = {"action": action, "p": scores[s], "reason": reason}
    return out


# ---------------------------------------------------------------------------
#  PER-CONVERSATION STATE (memory only)
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_MAX_CONVERSATIONS = 128
_sticky: "OrderedDict[str, set]" = OrderedDict()
_withheld: "OrderedDict[str, set]" = OrderedDict()
_last_turn: "OrderedDict[str, str]" = OrderedDict()
_released: "OrderedDict[str, list]" = OrderedDict()


def _bounded(od: OrderedDict, key, default):
    if key in od:
        od.move_to_end(key)
        return od[key]
    od[key] = default
    while len(od) > _MAX_CONVERSATIONS:
        od.popitem(last=False)
    return od[key]


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record as _off
        return bool(_off.active())
    except Exception:
        return False


def sticky_for(conversation_id) -> frozenset:
    with _state_lock:
        return frozenset(_sticky.get(str(conversation_id or ""), ()))


def reset(conversation_id=None) -> None:
    """A new chat, or the topic moved on: forget what was sent and withheld."""
    with _state_lock:
        if conversation_id is None:
            for od in (_sticky, _withheld, _last_turn, _released):
                od.clear()
        else:
            key = str(conversation_id)
            for od in (_sticky, _withheld, _last_turn, _released):
                od.pop(key, None)


def withheld_for(conversation_id) -> frozenset:
    with _state_lock:
        return frozenset(_withheld.get(str(conversation_id or ""), ()))


def last_turn_for(conversation_id) -> Optional[str]:
    with _state_lock:
        return _last_turn.get(str(conversation_id or ""))


# ---------------------------------------------------------------------------
#  THE SLICER (one per prompt preparation)
# ---------------------------------------------------------------------------

def is_local(provider, model: str = "") -> bool:
    """The egress gate's own verdict on a provider; unknown means cloud. A model
    Ollama relays to its cloud (`glm-4.6:cloud`) is cloud whatever the provider."""
    try:
        from agent_friday.services.local_only_guard import is_cloud_model_tag
        if model and is_cloud_model_tag(model):
            return False
        from agent_friday.services.egress_gate import is_local_provider
        return bool(is_local_provider(provider))
    except Exception:
        return False


def opted_in(settings: Optional[dict]) -> bool:
    """The owner chose "Send only what each turn needs". Nothing else counts."""
    return str((settings or {}).get("cloud_context_slicing") or "") == "slice"


class Slicer:
    """The plan for one turn's prompt, and its application.

    `active` is the whole switch: opted in, going to a cloud provider, and a
    reading exists. Inactive, every method returns its input unchanged and only
    describes what the head would have done.
    """

    def __init__(self, scores: Optional[Dict[str, float]], *, conversation_id=None,
                 turn_id: Optional[str] = None, cloud: bool = False,
                 opted: bool = False):
        self.scores = dict(scores) if scores else {}
        self.conversation_id = str(conversation_id) if conversation_id else None
        self.turn_id = turn_id
        self.cloud = bool(cloud)
        self.opted = bool(opted)
        self.off_record = _off_record()
        self.active = bool(self.opted and self.cloud and self.scores)
        self.sticky = sticky_for(self.conversation_id) if self.conversation_id else frozenset()
        self.plan = plan_slices(self.scores, cloud=self.cloud, sticky=self.sticky)
        self._stub_emitted = False

    # -- decisions ---------------------------------------------------------
    def decision(self, slice_name: str, tier: int = 2) -> Optional[dict]:
        """{action, p, reason} for a slice, or None when the head has no reading.

        A section whose own tier is >= 2 counts as private whatever slice it is.
        """
        base = self.plan.get(slice_name)
        if base is None:
            return None
        if tier >= 2 and slice_name not in PRIVATE_SLICES:
            action, reason = decide(slice_name, base["p"], private=True,
                                    cloud=self.cloud,
                                    sticky=slice_name in self.sticky)
            return {"action": action, "p": base["p"], "reason": reason}
        return base

    def _row(self, slice_name: str, d: dict) -> dict:
        return {"slice": slice_name, "planner": d["action"], "p": d["p"],
                "applied": self.active}

    def _after(self, slice_name: str, action: str) -> None:
        """Remember what really happened (only when the plan was applied)."""
        if not self.active or self.off_record or not self.conversation_id:
            return
        with _state_lock:
            if action == "include":
                _bounded(_sticky, self.conversation_id, set()).add(slice_name)
            else:
                _bounded(_withheld, self.conversation_id, set()).add(slice_name)

    # -- sections of the prompt builder -------------------------------------
    def apply_sections(self, sections: list, start: int) -> Tuple[list, Dict[int, dict]]:
        """(new sections, ledger info by original index).

        `sections` is `_build_context_prompt`'s (tier, text) list and `start`
        the index where the tail begins; nothing before it is looked at.
        """
        from agent_friday.services import retrieval_ledger as _rl
        info: Dict[int, dict] = {}
        out = list(sections[:start])
        for i in range(start, len(sections)):
            tier, text = sections[i]
            sl = section_slice(_rl.derive_section_name(text, i)) if text else None
            d = self.decision(sl, tier) if sl else None
            if d is None:
                out.append((tier, text))
                continue
            info[i] = self._row(sl, d)
            if not self.active or d["action"] == "include":
                out.append((tier, text))
            elif d["action"] == "stub":
                if not self._stub_emitted:
                    self._stub_emitted = True
                    out.append((1, STUB_TEXT))
            self._after(sl, d["action"])
        # Off the record the ledger row carries no planner reading.
        return out, ({} if self.off_record else info)

    def gate_block(self, slice_name: str, text: str, *, tier: int = 2,
                   name: Optional[str] = None, marker: Optional[str] = None) -> str:
        """The text of a block the route appends itself, sliced like a section.

        `marker` keeps whatever comes before it (a block that opens with a live
        status line) and slices only from it. Writes the ledger row for it.
        """
        if not text:
            return text
        keep, body = "", text
        if marker and marker in text:
            at = text.index(marker)
            keep, body = text[:at], text[at:]
        d = self.decision(slice_name, tier)
        if d is None:
            return text
        self._ledger_row(name or slice_name, tier, body, self._row(slice_name, d))
        self._after(slice_name, d["action"])
        if not self.active or d["action"] == "include":
            return text
        if d["action"] == "stub":
            if self._stub_emitted:
                return keep
            self._stub_emitted = True
            return keep + STUB_TEXT
        return keep

    def _ledger_row(self, name: str, tier: int, text: str, row: dict) -> None:
        if self.off_record:
            return
        try:
            from agent_friday.services import retrieval_ledger as _rl
            _rl.record_assembly(
                [(tier, text)], turn_id=self.turn_id or _rl.new_turn_id(),
                destination_class="cloud" if self.cloud else "local",
                gated=False, policy_source="slice_head",
                names=[name], plan_info={0: row})
        except Exception:
            pass

    # -- the receipt ---------------------------------------------------------
    def record_plan(self, took_ms: Optional[float] = None) -> bool:
        """Write the slices into this turn's TurnPlan. False off the record."""
        if self.off_record or not self.turn_id or not self.plan:
            return False
        try:
            from agent_friday.services import turn_plan as _tp
            ok = _tp.attach_slices(self.turn_id, self.plan, took_ms)
            if ok and self.conversation_id:
                with _state_lock:
                    _bounded(_last_turn, self.conversation_id, self.turn_id)
                    _last_turn[self.conversation_id] = self.turn_id
            return ok
        except Exception:
            return False


def for_turn(message: str, *, conversation_id=None, provider: str = "cloud",
             turn_id: Optional[str] = None, settings: Optional[dict] = None,
             model: str = "") -> Slicer:
    """The Slicer for one preparation of one turn's prompt. Never raises.

    `provider` is the leg being prepared (and `model` the model on it, so a
    cloud-tagged Ollama seat counts as cloud); a local leg is never sliced. The
    plan is recorded for the turn (shadow) whether or not it is applied.
    """
    t0 = time.perf_counter()
    cloud = not is_local(provider, model)
    try:
        scores = scores_for(message)
    except Exception as e:  # noqa: BLE001
        _log.debug("slice scoring skipped: %s", e)
        scores = None
    try:
        sl = Slicer(scores, conversation_id=conversation_id, turn_id=turn_id,
                    cloud=cloud, opted=opted_in(settings))
        sl.record_plan((time.perf_counter() - t0) * 1000)
        if settings is not None:
            maybe_offer(settings, cloud=cloud)
        return sl
    except Exception:
        return Slicer(None)


# ---------------------------------------------------------------------------
#  THE OFFER (decision D8: a choice at the next cloud use, nothing preselected)
# ---------------------------------------------------------------------------

OFFER_CHOICES = ("slice", "everything")


def should_offer(settings: Optional[dict], *, cloud: bool) -> bool:
    s = settings or {}
    if not cloud:
        return False
    if str(s.get("cloud_context_slicing") or "") in OFFER_CHOICES:
        return False
    return str(s.get("cloud_context_slicing_offer") or "") == ""


def maybe_offer(settings: Optional[dict], *, cloud: bool) -> bool:
    """Raise the one-time choice at the first cloud turn. Never raises.

    Writes the offer as pending (so a reloaded page still shows it) and tells
    the open pages. Off the record it does nothing at all; it is shown at the
    next cloud turn that is on the record.
    """
    try:
        if _off_record() or not should_offer(settings, cloud=cloud):
            return False
        from agent_friday.core import _save_settings
        _save_settings({"cloud_context_slicing_offer": "pending"})
        try:
            from agent_friday.services import notice_registry as _nr
            _nr.bus_broadcast("E72", {"type": "slicing_offer"}, kind="desktop")
        except Exception:
            pass
        return True
    except Exception as e:  # noqa: BLE001
        _log.debug("slicing offer skipped: %s", e)
        return False


# ---------------------------------------------------------------------------
#  ASKING FOR WHAT WAS HELD BACK
# ---------------------------------------------------------------------------

def labels_path():
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / "laya2" / "slice_labels.jsonl"


def is_tainted(taint_key: Optional[str]) -> bool:
    """The turn has read untrusted content (a tool result, a page, an import)."""
    try:
        from agent_friday.services import taint
        return bool(taint.recent_content(taint_key or "default"))
    except Exception:
        # Unknown provenance is treated as tainted: the safe side is a card.
        return True


def note_omission(slice_name: str, turn_id: Optional[str], *, tainted: bool) -> bool:
    """An omission label: the model asked for a slice the head had held back.

    Only an untainted turn on the record produces one; the owner's explicit
    labels are the other source. Returns whether a label was written.
    """
    if tainted or _off_record():
        return False
    try:
        path = labels_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": round(time.time(), 3), "turn_id": turn_id, "slice": slice_name,
               "label": "needed", "source": "request_context"}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
        return True
    except Exception:
        return False


def release_to_chat(conversation_id, text: str, kind: str = "context") -> bool:
    """Hand approved (or declined) context to the next turn of a text chat.

    The chat counterpart of voice_live_channel.deliver: a text turn has ended
    when its card is decided, so the answer waits for the next one.
    """
    if not conversation_id or not str(text or "").strip():
        return False
    with _state_lock:
        q = _bounded(_released, str(conversation_id), [])
        q.append({"kind": kind, "text": str(text), "turn": None})
        del q[:-10]
    return True


def render_released(conversation_id, turn_id: Optional[str]) -> str:
    """The tail block for context released since the last turn, or ''."""
    key = str(conversation_id or "")
    with _state_lock:
        q = _released.get(key)
        if not q:
            return ""
        keep, out = [], []
        for item in q:
            if item["turn"] is None:
                item["turn"] = turn_id
            if item["turn"] == turn_id:
                keep.append(item)
                out.append(item)
        q[:] = keep
    blocks = []
    for item in out:
        if item["kind"] == "context":
            blocks.append("\n== CONTEXT THE USER APPROVED (you asked for it) ==\n"
                          + item["text"] + "\n")
        else:
            blocks.append("\n== CONTEXT NOT SHARED ==\n" + item["text"] + "\n")
    return "".join(blocks)


def take_released_text(conversation_id) -> str:
    """Pop the context just released under a grant (for the tool result)."""
    key = str(conversation_id or "")
    with _state_lock:
        q = _released.get(key) or []
        texts = [i["text"] for i in q if i["kind"] == "context" and i["turn"] is None]
        q[:] = [i for i in q if not (i["kind"] == "context" and i["turn"] is None)]
    return "\n".join(texts)


def category_to_slice(word: str) -> Optional[str]:
    w = " ".join(str(word or "").lower().replace("_", " ").split())
    if w in _SLICE_OF_CATEGORY:
        return _SLICE_OF_CATEGORY[w]
    return w if w in SLICES and w in PRIVATE_SLICES else None


def request_context(category: str, why: str = "", *, conversation_id, cloud_model: str,
                    tainted: bool, turn_id: Optional[str] = None,
                    answer_fn=None) -> dict:
    """Ask the owner's own computer for a slice that was held back.

    Routes through `local_context.request`: the local model answers, people
    become relationship placeholders, the egress floor runs, and a card or an
    existing grant decides. A tainted turn passes `force_card` so even a grant
    cannot release it, and it writes no label.
    """
    sl = category_to_slice(category)
    if sl is None or sl not in PRIVATE_SLICES:
        return {"status": "unavailable",
                "reason": "Ask for one of: " + ", ".join(REQUESTABLE) + "."}
    if sl not in withheld_for(conversation_id):
        return {"status": "unavailable",
                "reason": "Nothing in that category was held back from this conversation."}
    word = CATEGORY[sl]
    question = (f"The cloud assistant asked for the user's {word} for the request in "
                f"front of it. Give only what that request needs, "
                f"and nothing else. What it said it needs: {str(why or '').strip()[:300]}")
    from agent_friday.services import local_context
    out = local_context.request(
        question, conversation_id=conversation_id, cloud_model=cloud_model,
        answer_fn=answer_fn, force_card=bool(tainted), channel="chat")
    note_omission(sl, turn_id, tainted=bool(tainted))
    return out


# ---------------------------------------------------------------------------
#  READING THE RECEIPT BACK ("what did you send?")
# ---------------------------------------------------------------------------

def describe_receipt(receipt: dict, *, names: bool) -> str:
    """Plain words for what a turn's receipt says was sent and held back.

    `names` False (a cloud model is asking) gives counts and never a slice name.
    """
    if not receipt or receipt.get("off_record"):
        return "That turn was off the record, so there is nothing to read back."
    sections = receipt.get("sections") or []
    orders = _describe_work_orders(receipt.get("work_orders"))
    if not sections:
        return orders or "I have no record of what was sent for that turn."
    sent = [s for s in sections if s.get("sent", True)]
    held = [s for s in sections if not s.get("sent", True)]
    cloud = [s for s in sent if s.get("destination_class") not in (None, "local")]
    shadow = [s for s in sections if s.get("planner") and not s.get("applied")
              and s.get("planner") != "include"]
    parts = [f"{len(sent)} of {len(sections)} sections went into the prompt"
             + (f", {len(cloud)} of them to a cloud model." if cloud else ".")]
    if held:
        if names:
            parts.append("Held back: " + ", ".join(
                f"{s.get('name')} ({s.get('planner')})" for s in held) + ".")
        else:
            parts.append(f"{len(held)} were held back.")
    if shadow:
        parts.append("The planner would have trimmed "
                     f"{len(shadow)}, but trimming is off, so they were sent.")
    elif not any(s.get("planner") for s in sections):
        parts.append("The planner had no reading for that turn.")
    if orders:
        parts.append(orders)
    return " ".join(parts)


def _describe_work_orders(section) -> str:
    """The work-order part of a receipt in words: how many handles the cloud
    model was shown and what was returned to it, by kind, size and class. Never
    content, so a cloud model may hear all of it."""
    if not section:
        return ""
    seen, rows = section.get("handles_seen") or [], section.get("returns") or []
    bits = [f"The cloud model was shown {len(seen)} handle"
            + ("" if len(seen) == 1 else "s") + " for work orders."]
    if rows:
        bits.append("It was sent back: " + "; ".join(
            f"{r.get('kind')} of {r.get('size')} characters ({r.get('class')})" for r in rows) + ".")
    else:
        bits.append("Nothing was sent back from a work order.")
    return " ".join(bits)
