"""laya.ask(): the one contract every Laya decision goes through.

Spec: ftv/program/reference/laya_swot_and_stack_spec_2026-10-10.md section c
(owner-approved 2026-10-10). Laya is a contract plus a ladder of rungs, and
this module is the contract:

    ask(state, questions, caller, budget_ms, profile_id=None) -> {
        answers:  {name: {value, p, set, abstain}},
        verdict:  act | ask | defer | no_tool | None,   (the router preset only)
        layer:    rule | t1 | t1+personal | t2 | brain | degraded,
        versions: {base, head, personal, calibration},
        latency_ms, receipt_id }

``state`` is {text, surface, mode, workspace?, focus?}; anything a preset needs
beyond those goes in ``state["opts"]`` (the router's tool list and trust flag,
for instance). ``questions`` is {name: {type: choice|noul|score, options|range,
preset?}}: a question runs the preset named by its ``preset`` key, or by its own
name. The presets that exist today, each a thin wrapper over a rung that was
already shipped:

  route          R0 sealed rules + R1 prototypes (services/laya_router.route,
                 byte-for-byte: ``route`` is untouched and ``ask`` calls it)
  point_act      R0 on-screen rows (laya_router.point_act_route)
  gate_opinion   the approval gate's union opinion (laya_backend.union_backend)
  resolve        the item resolver (laya_resolver.resolve)
  triage.<head>  the four background heads (services/triage_heads)

THE SIX INVARIANTS (tests/unit/test_laya_contract.py):
  1. ADD-ONLY. A preset may add a card, an ask, a defer or a restriction. The
     clamp in ``_clamp_add_only`` puts any answer a preset tries to lower back
     to the caller's own floor (the keyword scan for the gate) and says so.
  2. MUTATION CLASS comes from the governed tool table (``mutation_class``),
     never from a model, a preset's answer or a manifest. A claim of
     "mutates: false" in an answer is dropped.
  3. NEVER BLOCKS. Every question runs on a worker thread against one deadline.
     A rung that hangs, crashes or is missing makes that answer ``degraded``
     inside ``budget_ms``; the caller then takes its pre-Laya path.
  4. EVERY DECISION HAS A RECEIPT with versions and a plain-language account of
     which rung decided (``explain``). Receipts hold hashes and labels, never
     words, and are written append-only under runtime/laya2/ unless the caller
     says not to (off the record, or an eval run).
  5. NO USER WORDS IN SHIPPED WEIGHTS. Nothing here reads or writes weights;
     the shipped calibration table holds numbers only (services/laya_calibration).
  6. LEARNING ONLY TIGHTENS (services/laya_calibration): no function in this
     package lowers a shipped threshold without an owner-approval token.

ONE EMBEDDING. A preset that needs the turn's vector asks ``ctx.vector()``,
which is services/turn_vector: one encode of the message per turn, shared by the
router, the point-and-act ranker, the triage heads and the calibrated reading.
Nothing here loads a second MiniLM.

SHADOW. The route preset also computes the calibrated reading (temperature plus
conformal set, services/laya_calibration) and puts it in the receipt next to the
decision that was made. It changes nothing until the lead certifies it.

``profile_id`` None means the shipped base only. No personal layer exists yet,
so a profile id is recorded in the receipt and ``versions.personal`` stays
"none"; the contract is in place for the layer that will use it.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("friday.laya")

LAYERS = ("rule", "t1", "t1+personal", "t2", "brain", "degraded")
_LAYER_RANK = {l: i for i, l in enumerate(LAYERS)}
VERDICTS = ("act", "ask", "defer", "no_tool")
QUESTION_TYPES = ("choice", "noul", "score")

#: How long the sealed rules (and the item resolver they may call) may take on
#: top of a router tier-1 budget. route_turn adds it to the caller's budget.
RULE_ALLOWANCE_MS = 4000
#: A request-path triage read waits this long for the encoder, then the
#: incumbent answers; a background worker may wait much longer.
TRIAGE_REQUEST_BUDGET_MS = 400
TRIAGE_WORKER_BUDGET_MS = 120_000
POINT_ACT_BUDGET_MS = 4000
#: Worker threads left hanging by a rung that never returned. Past this the
#: contract answers degraded at once rather than leaking more of them.
MAX_STUCK = 16

LOG_DIRNAME = "laya2"
LOG_NAME = "ask.jsonl"
RING = 256

_UNSET = object()
_lock = threading.Lock()
_stuck = 0
_ring: "OrderedDict[str, dict]" = OrderedDict()
_versions_cache: Dict[str, str] = {}


# ---------------------------------------------------------------------------
#  the governed mutation class (invariant 2)
# ---------------------------------------------------------------------------

def mutation_class(tool: Optional[str]) -> str:
    """"read" or "write", from the governed tool table and nowhere else. A tool
    the table cannot describe is a writer (the verifier's own rule)."""
    if not tool:
        return "read"
    try:
        from agent_friday.services import call_verifier
        return "read" if call_verifier.annotations(str(tool)).get("readOnlyHint") else "write"
    except Exception:
        return "write"


# ---------------------------------------------------------------------------
#  versions and receipts (invariant 4)
# ---------------------------------------------------------------------------

def versions(profile_id: Optional[str] = None) -> Dict[str, str]:
    """{base, head, personal, calibration}: ids only, cheap, never loads a model."""
    out = {"base": _versions_cache.get("base"), "head": "none", "personal": "none", "calibration": "none"}
    if out["base"] is None:
        try:
            from agent_friday.services import laya2_encoder, laya2_seeds
            out["base"] = "encoder-%s/seeds-%s-%s" % (laya2_encoder.MODEL_SHA256[:12], laya2_seeds.SEED_SET_ID,
                                                      laya2_seeds.content_hash()[:8])
            _versions_cache["base"] = out["base"]
        except Exception:
            out["base"] = "unknown"
    try:
        from agent_friday.services import laya_router_head as lh
        rec = lh._receipt()
        if rec:
            out["head"] = str(rec.get("config_sha256") or rec.get("id") or "certified")[:12]
    except Exception:
        pass
    try:
        from agent_friday.services import laya_calibration
        out["calibration"] = laya_calibration.table_version()
    except Exception:
        pass
    return out


def _sha(text: str) -> str:
    return hashlib.sha256(" ".join(str(text or "").split()).encode("utf-8", "replace")).hexdigest()[:16]


def log_path():
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / LOG_DIRNAME / LOG_NAME


def _store(rec: dict, persist: bool) -> None:
    with _lock:
        _ring[rec["receipt_id"]] = rec
        while len(_ring) > RING:
            _ring.popitem(last=False)
    if not persist:
        return
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str, sort_keys=True) + "\n")
    except Exception:
        pass


def receipt(receipt_id: str) -> Optional[dict]:
    """A recent receipt by id (the ring holds the last RING of them)."""
    with _lock:
        r = _ring.get(receipt_id)
        return dict(r) if r else None


def explain(rec: dict) -> str:
    """The receipt in plain words: which rung decided, how sure, what else
    was consulted. Built from the receipt alone."""
    parts = []
    for name, q in (rec.get("questions") or {}).items():
        layer = q.get("layer")
        if layer == "degraded":
            parts.append("%s: Laya did not answer in time or could not (%s); the caller used its own path."
                         % (name, q.get("why") or "no reason given"))
            continue
        who = {"rule": "A sealed rule (rung R0)", "t1": "The shared reading of your words (rung R1)",
               "t1+personal": "The shared reading plus your own phrasings (rung R1)",
               "t2": "A stronger reader (rung R2)", "brain": "The brain (rung R3)"}.get(layer, "Laya")
        line = "%s: %s decided %r" % (name, who, q.get("value"))
        if q.get("p") is not None:
            line += " at %.2f" % float(q["p"])
        if q.get("why"):
            line += " (%s)" % q["why"]
        if q.get("clamped"):
            line += "; the answer was raised back to the caller's own floor, because Laya may only add checks"
        cal = q.get("calibrated")
        if cal:
            line += "; calibrated reading (shadow, changes nothing): %s over %s" % (cal.get("decision"),
                                                                                    ", ".join(cal.get("set") or ["nothing"]))
        parts.append(line + ".")
    return " ".join(parts) or "No question was asked."


# ---------------------------------------------------------------------------
#  context handed to a preset
# ---------------------------------------------------------------------------

class Ctx:
    """What a preset sees: the turn's text and surface, its options, the
    budget, and the turn's one shared vector."""

    def __init__(self, state: dict, caller: str, budget_ms: float, profile_id: Optional[str]):
        self.state = state
        self.text = " ".join(str(state.get("text") or "").split())
        self.surface = str(state.get("surface") or "")
        self.mode = state.get("mode")
        self.opts = dict(state.get("opts") or {})
        self.caller = caller
        self.budget_ms = budget_ms
        self.profile_id = profile_id
        self._vec: Any = _UNSET

    @property
    def person_present(self) -> bool:
        return self.surface in ("voice", "chat", "voice-live", "workspace") or bool(self.opts.get("person_present"))

    def vector(self, text: Optional[str] = None):
        """The unit vector for ``text`` (default: the turn), encoded once per
        turn through services/turn_vector. None when there is no encoder."""
        t = self.text if text is None else text
        if text is None and self._vec is not _UNSET:
            return self._vec
        try:
            from agent_friday.services import turn_vector
            v = turn_vector.compute(t)
        except Exception:
            v = None
        if text is None:
            self._vec = v
        return v


# ---------------------------------------------------------------------------
#  presets
# ---------------------------------------------------------------------------

PRESETS: Dict[str, Callable[[Ctx, dict], dict]] = {}


def preset(name: str):
    def deco(fn):
        PRESETS[name] = fn
        return fn
    return deco


def _answer(value, p=None, set_=None, abstain=False) -> dict:
    return {"value": value, "p": (None if p is None else round(float(p), 4)),
            "set": set_, "abstain": bool(abstain)}


_ROUTER_LAYER = {"rule": "rule", "none": "rule", "degraded": "degraded"}
_DECISION_VERDICT = {"tool": "act", "no_tool": "no_tool", "ask": "ask", "defer": "defer"}


def _calibrated_route(ctx: Ctx, r) -> Optional[dict]:
    """The calibrated reading of the router head for a turn tier 1 decided.
    Shadow only; any failure is silence."""
    try:
        if r.layer != "laya2":
            return None
        from agent_friday.services import laya_calibration, laya_router
        if not laya_calibration.is_warm():
            # The reading is shadow and changes nothing; it never spends the
            # decision's deadline on a first-use file read. warm() loads it.
            return None
        if laya_calibration.head_entry("router") is None:
            return None
        ranked = laya_router._score(ctx.text)
        cal = laya_calibration.reading("router", {l: s for l, _c, s in ranked},
                                       person_present=ctx.person_present)
        if cal is None:
            return None
        current = r.tool or ("conversation" if r.decision == "no_tool" else "act" if r.decision == "defer" else r.decision)
        cal["current"] = current
        cal["agrees"] = cal["label"] == current
        return cal
    except Exception as e:  # noqa: BLE001
        _log.debug("calibrated reading skipped: %s", e)
        return None


@preset("route")
def _preset_route(ctx: Ctx, spec: dict) -> dict:
    from agent_friday.services import laya_router
    # Exactly the arguments the caller gave, so route() sees what it always saw.
    r = laya_router.route(ctx.text, **dict(ctx.opts.get("route_kw") or {}))
    layer = _ROUTER_LAYER.get(r.layer, "t1")
    cal = _calibrated_route(ctx, r)
    verdict = _DECISION_VERDICT.get(r.decision, "no_tool")
    ans = _answer(r.decision, r.confidence, (cal or {}).get("set"), abstain=(layer == "degraded"))
    ans["tool"] = r.tool
    return {"answer": ans, "layer": layer, "verdict": verdict, "native": r, "calibrated": cal,
            "why": r.reason or r.layer, "tool": r.tool}


@preset("point_act")
def _preset_point_act(ctx: Ctx, spec: dict) -> dict:
    from agent_friday.services import laya_router
    o = ctx.opts
    if not hasattr(laya_router, "point_act_route"):
        # This build has no on-screen-row rung (point-and-act is another track):
        # degrade, so the caller takes the path it has today.
        return {"answer": _answer(None, abstain=True), "layer": "degraded", "verdict": None, "native": None,
                "why": "this build has no point-and-act rung"}
    r = laya_router.point_act_route(ctx.text, stage=o.get("stage", laya_router._UNSET),
                                    pointed=o.get("pointed", laya_router._UNSET), quiet=bool(o.get("quiet", False)))
    if r is None:
        return {"answer": _answer("none", abstain=True), "layer": "rule", "verdict": None, "native": None,
                "why": "no on-screen rows named"}
    return {"answer": dict(_answer(r.decision, r.confidence), tool=r.tool), "layer": "rule",
            "verdict": _DECISION_VERDICT.get(r.decision), "native": r, "why": r.reason, "tool": r.tool}


_SEVERITY_ORDER = ["soft", "hard"]


@preset("gate_opinion")
def _preset_gate(ctx: Ctx, spec: dict) -> dict:
    """The approval gate's union opinion. ``union_backend`` already returns the
    keyword scan OR Laya's reading and never less than the keyword scan; the
    keyword answer rides along as the floor ``ask`` enforces again."""
    from agent_friday.services import laya_backend
    qname = ctx.opts.get("gate_question", "action_severity")
    value, conf, detail = laya_backend.union_backend(qname, ctx.text, **(ctx.opts.get("gate_kw") or {}))
    detail = detail if isinstance(detail, dict) else {}
    consulted = detail.get("union") == "or"
    out = {"answer": _answer(value, conf), "layer": "t2" if consulted else "degraded",
           "verdict": None, "native": (value, conf, detail),
           "why": ("keyword scan and Laya's reading, either may raise" if consulted
                   else str(detail.get("reason") or "keyword scan only"))}
    if qname == "action_severity":
        out["floor"] = detail.get("keyword") if detail.get("keyword") in _SEVERITY_ORDER else None
        out["order"] = _SEVERITY_ORDER
    return out


@preset("resolve")
def _preset_resolve(ctx: Ctx, spec: dict) -> dict:
    from agent_friday.services import laya_resolver
    res = laya_resolver.resolve(ctx.text, sources=ctx.opts.get("sources"))
    t = res.timings_ms or {}
    layer = "t2" if ("pick" in t or "gate" in t) else "rule"
    return {"answer": dict(_answer(res.status, res.confidence, abstain=res.status in ("brain", "not_found")),
                           kind=res.kind),
            "layer": layer, "verdict": None, "native": res, "why": res.reason}


def _triage_head(name: str, spec: dict) -> str:
    return str(spec.get("head") or name.split(".", 1)[-1])


def _calibrated_triage(ctx: Ctx, head: str, texts, allowed) -> Optional[dict]:
    """Shadow reading for a one-text triage call, from the cosines the head
    itself used (the vector is the turn's cached one). Silence on any failure."""
    try:
        from agent_friday.services import laya_calibration, triage_heads
        if len(texts) != 1 or laya_calibration.head_entry("triage." + head) is None:
            return None
        protos = triage_heads.head(head).prototypes(block=False)
        vecs = triage_heads._embed([triage_heads._clip(texts[0])])
        if not protos or not vecs:
            return None
        keep = set(allowed) if allowed else None
        sims = {l: sum(a * b for a, b in zip(p, vecs[0])) for l, p in protos.items() if keep is None or l in keep}
        return laya_calibration.reading("triage." + head, sims, person_present=False)
    except Exception as e:  # noqa: BLE001
        _log.debug("calibrated triage reading skipped: %s", e)
        return None


def _preset_triage(ctx: Ctx, spec: dict) -> dict:
    try:
        from agent_friday.services import triage_heads
    except ImportError:
        # The background triage heads are another track: without them the
        # incumbent (keyword or cache path) answers, which is what degraded means.
        return {"answer": _answer(None, abstain=True), "layer": "degraded", "verdict": None, "native": None,
                "why": "the triage heads are not part of this build"}
    head = _triage_head(spec.get("_name", "triage.mail"), spec)
    texts = ctx.opts.get("texts") or [ctx.text]
    if len(texts) == 1 and triage_heads._embed_override is None and triage_heads.available():
        ctx.vector(triage_heads._clip(texts[0]))       # the one embedding; the head's peek hits it
    out = triage_heads._read_core(head, list(texts), allowed=ctx.opts.get("allowed"),
                                  block=bool(ctx.opts.get("block", False)), cached=bool(ctx.opts.get("cached", False)))
    if not out:
        return {"answer": _answer(None, abstain=True), "layer": "degraded", "verdict": None, "native": None,
                "why": "the reading model or this head's prototypes are not ready"}
    label, p = triage_heads.argmax(out[0])
    cal = _calibrated_triage(ctx, head, texts, ctx.opts.get("allowed"))
    return {"answer": _answer(label, p, (cal or {}).get("set")), "layer": "t1", "verdict": None, "native": out,
            "calibrated": cal, "why": "nearest prototype over the %s head's seeds" % head}


for _h in ("mail", "news", "notify", "memory"):
    PRESETS["triage.%s" % _h] = _preset_triage


def _clamp_add_only(ans: dict, res: dict) -> bool:
    """Invariant 1. If a preset names a floor and an order (low to high) and its
    answer sits below the floor, raise it to the floor. True when it did."""
    floor, order = res.get("floor"), res.get("order")
    if floor is None or not order or floor not in order:
        return False
    v = ans.get("value")
    if v in order and order.index(v) >= order.index(floor):
        return False
    ans["value"] = floor
    return True


# ---------------------------------------------------------------------------
#  the contract
# ---------------------------------------------------------------------------

# Per-call restrictions that live in threading.local rather than a ContextVar do
# not follow copy_context() onto the worker. These are the ones a rung could
# read, and each is carried over by value: the local-only guard (an active
# local-only run forces local_only; a cloud pin), the turn id, the reasoning
# trace id and the unattended mark. Everything else a rung might consult is a
# ContextVar (cloud_intent, taint, the private-session origin, turn_plan,
# decisions) and copy_context() already covers it. The remaining thread-locals
# (attribution, tool_receipts, judgment_gate, cost_meter, task_journal) are
# per-thread books a routing preset never reads or writes; carrying a book by
# reference would be wrong, so they are deliberately not carried.
_LOCAL_ONLY_FIELDS = ("active", "label", "pin", "pin_label")


def _carry_snapshot() -> dict:
    snap: dict = {}
    try:
        from agent_friday.services import local_only_guard as g
        snap["guard"] = {k: getattr(g._state, k) for k in _LOCAL_ONLY_FIELDS if hasattr(g._state, k)}
    except Exception:
        pass
    try:
        from agent_friday import core
        snap["turn_id"] = getattr(core._TURN_LOCAL, "turn_id", None)
    except Exception:
        pass
    try:
        from agent_friday.services import reasoning_trace as rt
        snap["trace_id"] = getattr(rt._THREAD, "trace_id", None)
    except Exception:
        pass
    try:
        from agent_friday.services import turn_budget as tb
        snap["unattended"] = getattr(tb._unattended, "on", None)
    except Exception:
        pass
    return snap


def _carry_apply(snap: dict) -> None:
    """Install ``snap`` on the current (worker) thread."""
    try:
        from agent_friday.services import local_only_guard as g
        for k, v in (snap.get("guard") or {}).items():
            setattr(g._state, k, v)
    except Exception:
        pass
    try:
        if "turn_id" in snap:
            from agent_friday import core
            core._TURN_LOCAL.turn_id = snap["turn_id"]
    except Exception:
        pass
    try:
        if snap.get("trace_id") is not None:
            from agent_friday.services import reasoning_trace as rt
            rt._THREAD.trace_id = snap["trace_id"]
    except Exception:
        pass
    try:
        if snap.get("unattended") is not None:
            from agent_friday.services import turn_budget as tb
            tb._unattended.on = snap["unattended"]
    except Exception:
        pass


def _worker(ctx: Ctx, questions: Dict[str, dict], results: dict, done: threading.Event, flags: dict,
            carried: Optional[dict] = None) -> None:
    global _stuck
    try:
        _carry_apply(carried or {})
        for name, spec in questions.items():
            if flags.get("abandoned"):
                break
            fn = PRESETS.get(str((spec or {}).get("preset") or name))
            if fn is None:
                results[name] = {"error": "no preset named %r" % ((spec or {}).get("preset") or name)}
                continue
            try:
                results[name] = fn(ctx, dict(spec or {}, _name=name))
            except Exception as e:  # noqa: BLE001
                results[name] = {"error": "%s" % type(e).__name__}
    finally:
        done.set()
        if flags.get("abandoned"):
            with _lock:
                _stuck = max(0, _stuck - 1)


def ask(state: dict, questions: Dict[str, dict], caller: str, budget_ms: float,
        profile_id: Optional[str] = None) -> dict:
    """Ask Laya ``questions`` about ``state`` within ``budget_ms``. Never raises
    and never waits longer than the budget."""
    global _stuck
    t0 = time.perf_counter()
    state = dict(state or {})
    ctx = Ctx(state, str(caller or "unknown"), float(budget_ms), profile_id)
    questions = {str(k): dict(v or {}) for k, v in (questions or {}).items()}
    results: dict = {}
    flags: dict = {}
    done = threading.Event()
    with _lock:
        saturated = _stuck >= MAX_STUCK
    if saturated:
        done.set()
        timed_out = True
        note = "too many earlier answers are still stuck"
    else:
        cvars = contextvars.copy_context()
        threading.Thread(target=cvars.run, args=(_worker, ctx, questions, results, done, flags, _carry_snapshot()),
                         name="laya-ask", daemon=True).start()
        timed_out = not done.wait(max(0.0, float(budget_ms)) / 1000.0)
        note = "over budget (%d ms)" % int(budget_ms)
        if timed_out:
            with _lock:
                if done.is_set():
                    timed_out = False
                else:
                    flags["abandoned"] = True
                    _stuck += 1
    answers: Dict[str, dict] = {}
    native: Dict[str, Any] = {}
    qrec: Dict[str, dict] = {}
    verdict = None
    layers: List[str] = []
    for name, spec in questions.items():
        res = dict(results.get(name) or {})
        if not res or "error" in res:
            why = res.get("error") if res else (note if timed_out else "no answer")
            answers[name] = _answer(None, abstain=True)
            layers.append("degraded")
            qrec[name] = {"preset": spec.get("preset") or name, "layer": "degraded", "value": None, "p": None,
                          "why": why, "set": None}
            continue
        ans = dict(res["answer"])
        clamped = _clamp_add_only(ans, res)
        ans.pop("mutates", None)                    # invariant 2: a claim of "mutates" is never taken
        tool = res.get("tool") or ans.get("tool")
        if tool:
            ans["mutation_class"] = mutation_class(tool)
        answers[name] = ans
        native[name] = res.get("native")
        layer = res.get("layer") if res.get("layer") in LAYERS else "t1"
        layers.append(layer)
        if verdict is None and res.get("verdict") in VERDICTS:
            verdict = res["verdict"]
        qrec[name] = {"preset": spec.get("preset") or name, "layer": layer, "value": ans.get("value"),
                      "p": ans.get("p"), "set": ans.get("set"), "why": res.get("why"), "clamped": clamped,
                      "calibrated": res.get("calibrated"), "tool": tool,
                      "mutation_class": ans.get("mutation_class")}
    live = [l for l in layers if l != "degraded"]
    layer = max(live, key=lambda l: _LAYER_RANK[l]) if live else "degraded"
    latency = round((time.perf_counter() - t0) * 1000.0, 2)
    rec = {"receipt_id": "lr_" + uuid.uuid4().hex[:16], "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "caller": ctx.caller, "surface": ctx.surface, "mode": ctx.mode, "profile_id": profile_id,
           "text_sha": _sha(ctx.text) if ctx.text else None, "budget_ms": float(budget_ms),
           "questions": qrec, "verdict": verdict, "layer": layer, "versions": versions(profile_id),
           "latency_ms": latency, "degraded": layer == "degraded"}
    rec["explain"] = explain(rec)
    persist = bool(ctx.opts.get("log", True))
    _store(rec, persist)
    return {"answers": answers, "verdict": verdict, "layer": layer, "versions": rec["versions"],
            "latency_ms": latency, "receipt_id": rec["receipt_id"], "native": native}


# ---------------------------------------------------------------------------
#  presets as functions the callers use
# ---------------------------------------------------------------------------

def route_question() -> Dict[str, dict]:
    from agent_friday.services import laya_router
    return {"route": {"type": "choice", "options": list(laya_router.DECISIONS), "preset": "route"}}


_SURFACE_CALLER = {"voice": "voice.local", "chat": "chat", "voice-live": "voice.live"}


def _route_kw() -> frozenset:
    """The keyword arguments ``laya_router.route`` takes in this build, read from
    its signature so the contract never promises one the router cannot honour
    (the head, trust and on-screen-row arguments arrive with their own tracks)."""
    import inspect
    from agent_friday.services import laya_router
    base = frozenset({"tools", "budget_ms", "surface", "log", "pending_card", "last_route"})
    try:
        params = inspect.signature(laya_router.route).parameters.values()
    except (TypeError, ValueError):
        return base
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):      # a wrapper: the signature says nothing
        return base
    return frozenset(p.name for p in params if p.kind is inspect.Parameter.KEYWORD_ONLY)


def route_turn(text: str, *, caller: Optional[str] = None, mode: Optional[str] = None, **kw):
    """``laya_router.route`` as a preset of ``ask``: the same keyword arguments
    (whatever ``route`` takes in this build: tools, budget_ms, surface, log,
    pending_card, last_route), the same Route. ``budget_ms`` stays the
    router's tier-1 budget; the contract deadline adds RULE_ALLOWANCE_MS for the
    sealed rules. A degraded ask (the router hung or crashed) is the router's
    own degraded Route: ``no_tool``, so the model answers."""
    from agent_friday.services import laya_router
    bad = set(kw) - _route_kw()
    if bad:
        raise TypeError("route_turn() got unexpected keyword argument %r" % sorted(bad)[0])
    surface = kw.get("surface", "voice")
    tier1 = int(kw.get("budget_ms", laya_router.DEFAULT_BUDGET_MS))
    res = ask({"text": text, "surface": surface, "mode": mode,
               "opts": {"route_kw": kw, "log": kw.get("log", True),
                        "trusted": bool(kw.get("trusted", False))}},
              route_question(), caller or _SURFACE_CALLER.get(surface, "surface.%s" % surface),
              budget_ms=tier1 + RULE_ALLOWANCE_MS)
    r = res["native"].get("route")
    if r is None:
        r = laya_router.Route("no_tool", layer="degraded", reason="Laya over budget or unavailable",
                              elapsed_ms=res["latency_ms"])
    r.laya_receipt_id = res["receipt_id"]       # not a Route field: as_dict() is unchanged
    return r


def point_act_turn(text: str, *, quiet: bool = False, stage=_UNSET, pointed=_UNSET,
                   caller: str = "chat.point", log: bool = True):
    """``laya_router.point_act_route`` through ``ask``: the Route, or None when
    the words name no on-screen rows (or Laya could not answer in time, which
    the caller treats the same: take today's path)."""
    opts: dict = {"quiet": quiet, "log": log}
    if stage is not _UNSET:
        opts["stage"] = stage
    if pointed is not _UNSET:
        opts["pointed"] = pointed
    res = ask({"text": text, "surface": "chat", "opts": opts}, {"point_act": {"type": "choice", "preset": "point_act"}},
              caller, budget_ms=POINT_ACT_BUDGET_MS)
    return res["native"].get("point_act")


def triage_read(head: str, texts: List[str], *, allowed=None, block: bool = False, cached: bool = False,
                caller: Optional[str] = None):
    """A triage head's {label: p} per text through ``ask``, or None when the
    reading model or prototypes are not ready (the incumbent then answers)."""
    opts = {"texts": list(texts), "allowed": None if allowed is None else list(allowed), "block": block,
            "cached": cached}
    name = "triage.%s" % head
    res = ask({"text": texts[0] if texts else "", "surface": "background", "opts": opts},
              {name: {"type": "choice", "preset": name}}, caller or name,
              budget_ms=TRIAGE_WORKER_BUDGET_MS if block else TRIAGE_REQUEST_BUDGET_MS)
    return res["native"].get(name)


def warm() -> None:
    """Pay the first-use costs once, off the request path, in a daemon thread:
    the version ids, the calibration table and the router module. CPU imports
    only; no model is loaded (laya_router.warm does that, separately)."""
    def run():
        try:
            from agent_friday.services import laya_calibration, laya_router  # noqa: F401
            laya_calibration.load_table()
            versions()
        except Exception as e:  # noqa: BLE001
            _log.debug("laya warm skipped: %s", e)
    threading.Thread(target=run, name="laya-warm", daemon=True).start()


def reset() -> None:
    """Forget receipts and stuck-thread accounting (tests)."""
    global _stuck
    with _lock:
        _ring.clear()
        _stuck = 0
    _versions_cache.clear()
