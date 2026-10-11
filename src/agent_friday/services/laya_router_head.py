"""The trained tool head as an optional tier of the Laya router (DA-1a).

The router (services/laya_router) decides ``tool | no_tool | ask | defer``
with sealed rules (tier 0) and nearest prototypes (tier 1). A trained head
(tools/tool_head, trained and certified in a lane GPU window by DA-1b) can
take over tier 1 behind the SAME ``route()`` contract. This module is the
whole of that integration; with the setting off, nothing here runs.

    laya_tool_head   "off" (default) | "shadow" | "on"

  off     the router is exactly the Beta 1.0.1 router.
  shadow  the head's choice is computed off the turn's path and written
          beside the prototypes' to ``runtime/laya2/router_head_shadow.jsonl``
          (hashes, operations and numbers only: no words, no argument
          values). It changes nothing.
  on      for a turn tier 0 did not decide, a head decision at or above its
          CERTIFIED threshold replaces tier 1. Below it, or when the head
          hands the turn over ("brain"), the prototypes answer as before.

Whatever the mode, tier 0 runs first: the action rules, ``_NOT_A_REQUEST``
and the ``_ACT`` mutation guard decide before the head is asked, and the
head can only ever choose among the router's READ tools. Anything else it
names is ``defer`` (the deeper mind and the governance gate decide);
composed text is never an argument. Governance, cards and receipts are the
surface's own, exactly as for a prototype route.

A head exists only with a certification receipt. Under
``<runtime>/tool_head/<profile>/``:

    model/rl_agent_config.json   the calibrated checkpoint's config
    catalogue.json               the catalogue the head was trained on
    certification.json           {"v": 1, "certified": true, "threshold": 0.93,
                                  "target_error": 0.01, "delta": 0.05,
                                  "config_sha256": <sha256 of rl_agent_config.json>,
                                  "n_b": ..., "ucb": ...}

``config_sha256`` binds the receipt to the exact calibrated checkpoint: a
checkpoint retrained or re-calibrated after certification is not certified.
Arguments are CHOSEN, never generated: the head picks a ``query`` among
spans of the owner's own words (``candidate_spans``) and an unlisted value
is dropped.
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

_log = logging.getLogger("friday.laya_router_head")

SETTING = "laya_tool_head"
MODES = ("off", "shadow", "on")
DEFAULT_PROFILE = "owner"
SHADOW_LOG = "router_head_shadow.jsonl"
RECEIPT_NAME = "certification.json"
CONFIG_NAME = "rl_agent_config.json"
LOAD_RETRY_S = 60.0
MAX_SHADOW_BACKLOG = 4
MAX_SPANS = 14

#: The only tools the head may choose to RUN. Everything else it names is
#: ``defer``. (Kept in step with laya_router.TOOLS by a test.)
HEAD_READ_TOOLS = frozenset({
    "query_calendar", "check_email", "search_email", "search_news", "search_web",
    "get_briefing", "search_files", "search_wiki", "search_past_conversations"})
#: Operations that are not tools (tool_head.common).
_NO_TOOL, _ASK, _BRAIN = ("none", "no_tool"), "ask", "brain"
#: The only argument names the head may fill, and from what.
_QUERY_TOOLS = frozenset({"search_email", "search_news", "search_web", "search_files",
                          "search_wiki", "search_past_conversations"})


@dataclass
class Proposal:
    op: str                       # tool name | "none" | "ask" | "brain"
    args: Optional[dict] = None
    score: Optional[float] = None
    ms: float = 0.0


# ── setting, receipt, checkpoint ───────────────────────────────────────────

def configured_mode(settings: Optional[dict] = None) -> str:
    """The setting, read strictly: only a literal "shadow" or "on" counts."""
    cached = settings is None
    if cached:
        with _state_lock:
            if time.time() - float(_mode_cache["at"]) < MODE_TTL_S:
                return _mode_cache["mode"]
    try:
        if settings is None:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        v = settings.get(SETTING) if isinstance(settings, dict) else None
    except Exception:
        return "off"
    mode = v if v in ("shadow", "on") else "off"
    if cached:
        with _state_lock:
            _mode_cache.update(at=time.time(), mode=mode)
    return mode


def root_dir() -> Path:
    """``<runtime>/tool_head`` (tests replace this)."""
    from agent_friday import paths
    return Path(paths.runtime_dir()) / "tool_head"


def profile_dir(profile: str = DEFAULT_PROFILE) -> Path:
    return root_dir() / profile


def log_path() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / "laya2" / SHADOW_LOG


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_receipt(profile: str = DEFAULT_PROFILE) -> Optional[dict]:
    """The certification receipt, only when it is valid for the checkpoint
    on disk; else None. Never raises."""
    try:
        d = profile_dir(profile)
        rec = json.loads((d / RECEIPT_NAME).read_text(encoding="utf-8"))
        thr = rec.get("threshold")
        if (rec.get("v") != 1 or rec.get("certified") is not True
                or not isinstance(thr, (int, float)) or isinstance(thr, bool)
                or not (0.0 < float(thr) <= 1.0)):
            return None
        cfg = d / "model" / CONFIG_NAME
        if not cfg.is_file() or not (d / "catalogue.json").is_file():
            return None
        if rec.get("config_sha256") != _sha_file(cfg):
            return None
        return rec
    except Exception:
        return None


_state_lock = threading.Lock()
_cache: Dict[str, object] = {"receipt_at": 0.0, "receipt": None, "head": None,
                             "failed_at": 0.0}
RECEIPT_TTL_S = 10.0
#: The setting is read from disk at most this often (route() asks on every turn).
MODE_TTL_S = 2.0
_mode_cache: Dict[str, object] = {"at": 0.0, "mode": "off"}


def available(profile: str = DEFAULT_PROFILE) -> bool:
    """True when a certified head is on disk (cheap; cached for a few seconds)."""
    return _receipt(profile) is not None


def _receipt(profile: str = DEFAULT_PROFILE) -> Optional[dict]:
    now = time.time()
    with _state_lock:
        if now - float(_cache["receipt_at"]) < RECEIPT_TTL_S and _cache.get("profile") == profile:
            return _cache["receipt"]
    rec = load_receipt(profile)
    with _state_lock:
        _cache.update(receipt_at=now, receipt=rec, profile=profile)
        if rec is None:
            _cache["head"] = None
    return rec


def reset() -> None:
    """Forget the cached receipt and head (tests; a new checkpoint)."""
    with _state_lock:
        _cache.update(receipt_at=0.0, receipt=None, head=None, failed_at=0.0, profile=None)
        _mode_cache.update(at=0.0, mode="off")


def threshold(profile: str = DEFAULT_PROFILE) -> Optional[float]:
    rec = _receipt(profile)
    return float(rec["threshold"]) if rec else None


# ── the head ────────────────────────────────────────────────────────────────

def _tools_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "tools"


def import_tool_head(sub: str = ""):
    """``tools/tool_head`` (or one of its modules) imported as the package ``tool_head`` WITHOUT touching
    ``sys.path``: the package is registered under its name with its own directory as the search location."""
    import importlib
    import importlib.machinery
    import importlib.util
    if "tool_head" not in sys.modules:
        pkg = _tools_dir() / "tool_head"
        if not pkg.is_dir():
            raise ImportError("tools/tool_head is not shipped here")
        init = pkg / "__init__.py"
        if init.is_file():
            spec = importlib.util.spec_from_file_location("tool_head", init, submodule_search_locations=[str(pkg)])
        else:
            spec = importlib.machinery.ModuleSpec("tool_head", None, is_package=True)
            spec.submodule_search_locations = [str(pkg)]
        mod = importlib.util.module_from_spec(spec)
        sys.modules["tool_head"] = mod
        try:
            if spec.loader is not None:
                spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop("tool_head", None)
            raise
    return importlib.import_module("tool_head." + sub) if sub else sys.modules["tool_head"]


def make_span_head(runner, catalogue: dict) -> Callable[[dict], dict]:
    """tool_head's ``ToolHead`` with one change: a required free-text
    ``query`` is a CHOICE among the spans of the owner's words that this
    turn supplies (``case["candidates"]["query"]``), so the head fills it
    without ever writing a word. Needs tools/tool_head (imported without a sys.path change)."""
    ew = import_tool_head("eval_wiring")
    items_mod = import_tool_head("items")

    class SpanToolHead(ew.ToolHead):
        def _slots(self, case, tool, conf):
            entry = self.catalogue[tool]
            offers = {}
            for arg in entry["required"]:
                cands = (case.get("candidates") or {}).get(arg)
                if not cands:
                    return None, conf
                offers[arg] = cands
            args, score = {}, conf
            for arg, cands in offers.items():
                opts = {"c%d" % (i + 1): str(c) for i, c in enumerate(cands)}
                question = items_mod.slot_question(case, tool, entry, arg, opts)
                ans = self.runner.predict(items_mod.make_state(case),
                                          {"slot:" + arg: question})["answers"]["slot:" + arg]
                args[arg] = cands[int(ans["choice"][1:]) - 1]
                score *= float(ans["confidence"])
            return args, score

    return SpanToolHead(runner, catalogue)


def _default_loader(profile: str) -> Callable[[dict], dict]:  # pragma: no cover - loads a model
    import inspect
    import os
    if not _tools_dir().is_dir():
        raise RuntimeError("tools/tool_head is not shipped here")
    cat_mod = import_tool_head("catalogue")
    ew = import_tool_head("eval_wiring")
    pd = profile_dir(profile)
    cat = cat_mod.load_catalogue(str(pd / "catalogue.json"))
    # The head runs on the CPU, always: a runner that cannot be told so is not loaded (the brain may hold the GPU).
    if "device" not in inspect.signature(ew.LayaRunner).parameters:
        raise RuntimeError("the tool head's runner cannot be pinned to the CPU")
    return make_span_head(ew.LayaRunner(str(pd / "model"), os.environ.get("LAYA_SRC"), device="cpu"), cat)


#: ``loader(profile) -> head(case) -> {"operation", "args", "score"}`` (tests
#: replace this with a fake; the default loads the checkpoint).
_loader: Callable[[str], Callable[[dict], dict]] = _default_loader
#: ``provider(text, tool, arg) -> [candidates]`` for choice slots other than
#: ``query`` (a recipient, a label). None until a retrieval source is wired.
candidate_provider: Optional[Callable[[str, str, str], list]] = None


def _get_head(profile: str = DEFAULT_PROFILE):
    """The loaded head, or None (no certified checkpoint, or a recent failed
    load). Builds on the caller's thread: call it from the worker."""
    if _receipt(profile) is None:
        return None
    with _state_lock:
        if _cache["head"] is not None:
            return _cache["head"]
        if time.time() - float(_cache["failed_at"]) < LOAD_RETRY_S:
            return None
    try:
        h = _loader(profile)
    except Exception as e:  # noqa: BLE001
        _log.warning("tool head failed to load: %s", type(e).__name__)
        with _state_lock:
            _cache["failed_at"] = time.time()
        return None
    with _state_lock:
        _cache["head"] = h
    return h


# ── candidates, validation ─────────────────────────────────────────────────

_STOP = frozenset("a an the my me i is are was were do does did any anything some of to for on in at "
                  "about from with and or please can you could would tell show give what whats what's "
                  "when where how who which that this it there have has had get got".split())


def candidate_spans(text: str) -> List[str]:
    """Spans of the owner's own words a ``query`` may be: contiguous runs of
    content words (1 to 4), the router's trigger-stripped reading, and the
    run after a qualifier (from, about, on, for, regarding). Deterministic;
    every span is a substring of the words, so nothing is invented."""
    from agent_friday.services import laya_router as lr
    t = " ".join(str(text or "").split())
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", t)
    spans: List[str] = []

    def add(s: str):
        s = s.strip(" ?.!,\"'")
        if s and s.lower() in t.lower() and s.lower() not in {x.lower() for x in spans}:
            spans.append(s)

    add(lr._strip_triggers(t))
    for m in re.finditer(r"\b(?:from|about|on|for|regarding|re|called|named)\s+(.+?)\s*[?.!]*$", t, re.I):
        add(lr._clean_query(m.group(1)))
    n = len(words)
    for size in (4, 3, 2, 1):
        for i in range(0, n - size + 1):
            run = words[i:i + size]
            if run[0].lower() in _STOP or run[-1].lower() in _STOP:
                continue
            if any(w.lower() in _STOP for w in run[1:-1]) and size > 2:
                continue
            add(" ".join(run))
    return spans[:MAX_SPANS]


def _valid_args(tool: str, args: Optional[dict], spans: List[str]) -> dict:
    """Only what the head may fill, and only from what it was offered."""
    out: dict = {}
    if not isinstance(args, dict):
        return out
    q = args.get("query")
    if tool in _QUERY_TOOLS and isinstance(q, str):
        pick = next((s for s in spans if s.lower() == q.strip().lower()), None)
        if pick:
            out["query"] = pick
    u = args.get("urgent_only")
    if tool == "check_email" and isinstance(u, bool):
        out["urgent_only"] = u
    return out


def propose(text: str, profile: str = DEFAULT_PROFILE) -> Optional[Proposal]:
    """Ask the head. None when there is no certified head or it fails."""
    head = _get_head(profile)
    if head is None:
        return None
    spans = candidate_spans(text)
    case = {"id": "live", "kind": "tool", "user": text, "history": [],
            "candidates": {"query": spans}}
    t0 = time.perf_counter()
    try:
        # A model job is always visible (shadow or on): it names the model and shows in the Activity lane.
        # Where the door is unavailable the head does not run.
        from agent_friday.services import visible_jobs as _vj
        with _vj.visible_job("Laya tool head", kind="laya_head", workspace="system", eager=False):
            _vj.note_inference("laya-tool-head", local=True)
            out = head(case)
    except Exception as e:  # noqa: BLE001
        _log.warning("tool head failed on a turn: %s", type(e).__name__)
        return None
    op = str(out.get("operation") or "")
    score = out.get("score")
    score = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
    raw = out.get("args") if isinstance(out.get("args"), dict) else None
    args = _valid_args(op, raw, spans) if op in HEAD_READ_TOOLS else raw
    return Proposal(op=op, args=args, score=score, ms=round((time.perf_counter() - t0) * 1000, 2))


def as_decision(p: Proposal) -> tuple:
    """(decision, tool) the proposal stands for, in the router's words."""
    if p.op in HEAD_READ_TOOLS:
        return "tool", p.op
    if p.op in _NO_TOOL:
        return "no_tool", None
    if p.op == _ASK:
        return "ask", None
    if p.op == _BRAIN or not p.op:
        return "abstain", None
    return "defer", None


def route_from(text: str, p: Optional[Proposal], thr: float):
    """The Route for a proposal at or above ``thr``, else None (the
    prototypes answer). Never a tool outside HEAD_READ_TOOLS."""
    from agent_friday.services import laya_router as lr
    if p is None or p.score is None or p.score < thr:
        return None
    decision, tool = as_decision(p)
    conf = round(p.score, 3)
    if decision == "abstain":
        return None
    if decision == "no_tool":
        return lr.Route("no_tool", confidence=conf, layer="head", reason="the tool head: no tool needed")
    if decision == "ask":
        return lr.Route("ask", confidence=conf, layer="head", question=lr._ASK_WHICH,
                        reason="the tool head: which tool or what to look for is not clear")
    if decision == "defer":
        return lr.Route("defer", confidence=conf, layer="head",
                        reason="the tool head chose a tool the router never runs; the deeper mind decides",
                        chosen=_chosen(text, p),
                        handoff="compose_text" if lr._COMPOSE.search(text) else "")
    label, template, required = lr.TOOLS[tool]
    args = dict(p.args or {})
    explicit = bool(args.get("query"))
    if tool in lr.ASK_BEFORE_GUESSING and not explicit:
        # The head found no subject of its own: the whole utterance would be
        # the query. A guess at an outward search is asked, never run.
        return lr.Route("ask", tool=tool, confidence=conf, layer="head", label=label,
                        question=lr._ASK_OUTWARD.get(tool, "Do you want me to look that up online?"),
                        reason="the head named no subject for an outward search")
    if not explicit:
        base = template(text)
        base.update(args)
        args = base
    elif tool == "check_email":
        args = dict(template(text), **args)
    r = lr._with_args(tool, args, conf, "head", label, required,
                      reason="the tool head" + (" (subject chosen from your words)" if explicit else ""),
                      text=text)
    r.candidates = [(tool, conf)]
    return r


def _chosen(text: str, p: Proposal) -> dict:
    """Choice-slot picks for a deferred write: only values the candidate
    provider offered this turn, and never a composed-text argument."""
    from agent_friday.services import laya_router as lr
    out: dict = {}
    args = p.args if isinstance(p.args, dict) else {}
    if candidate_provider is None:
        return out
    for k, v in args.items():
        if k in lr.COMPOSED_ARGS or not isinstance(v, str):
            continue
        try:
            offered = [str(c) for c in (candidate_provider(text, p.op, k) or [])]
        except Exception:
            offered = []
        if v in offered:
            out[k] = v
    return out


def decide(text: str, profile: str = DEFAULT_PROFILE):
    """``on`` mode, on the worker thread: the head's Route for this turn, or
    None to let the prototypes answer."""
    thr = threshold(profile)
    if thr is None:
        return None
    return route_from(text, propose(text, profile), thr)


# ── shadow ──────────────────────────────────────────────────────────────────

#: Shadow runs are queued and drained by ONE runner thread inside ONE visible job: a burst of routed turns
#: shows a single "Laya tool head (shadow)" card, not one card per turn. The card closes after this many
#: seconds with nothing left to run, so the next burst (the next idle window) opens a new one.
SHADOW_IDLE_WINDOW_S = 20.0
_shadow_lock = threading.Lock()
_shadow_cv = threading.Condition(_shadow_lock)
_shadow_q: "collections.deque" = collections.deque()
_shadow_runner = False
_shadow_pending = 0
shadow_jobs_opened = 0          # visible jobs opened by the runner (a test and a diagnostic)


def _sha16(obj) -> Optional[str]:
    if obj is None:
        return None
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _keyed(text: str) -> str:
    from agent_friday.services import laya_safe_writes
    return laya_safe_writes.keyed_hash(text, 16)


def shadow_row(text: str, p: Optional[Proposal], final, proto, surface: str, thr: Optional[float]) -> dict:
    """One log row: hashes, operations and numbers. No words, no values."""
    head = None
    if p is not None:
        decision, tool = as_decision(p)
        head = {"op": p.op, "decision": decision, "tool": tool, "score": p.score, "ms": p.ms,
                "args_sha": _sha16(p.args), "args_keys": sorted((p.args or {}).keys()),
                "above_threshold": bool(p.score is not None and thr is not None and p.score >= thr)}
    ref = proto if proto is not None else final
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "surface": surface, "mode": "shadow",
           "text_sha": _keyed(text), "threshold": thr,
           "head": head,
           "proto": ({"decision": proto.decision, "tool": proto.tool, "confidence": proto.confidence,
                      "args_sha": _sha16(proto.args)} if proto is not None else None),
           "final": {"decision": final.decision, "tool": final.tool, "layer": final.layer}}
    if head is not None:
        row["agree"] = bool(head["decision"] == ref.decision and head["tool"] == ref.tool)
        row["args_agree"] = bool(row["agree"] and head["args_sha"] == _sha16(ref.args)) if ref.args or p.args else row["agree"]
    return row


def _write_shadow(row: dict) -> None:
    if not row.get("text_sha"):
        return                              # no local key to hash with: no hashed row is written
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _shadow_job(text, final, proto, surface, profile):
    global _shadow_pending
    try:
        p = propose(text, profile)
        _write_shadow(shadow_row(text, p, final, proto, surface, threshold(profile)))
    except Exception:
        pass
    finally:
        with _shadow_lock:
            _shadow_pending -= 1


def _shadow_runner_loop(profile):
    """The runner thread: one visible job for the whole burst, then it closes."""
    global _shadow_runner, shadow_jobs_opened
    try:
        from agent_friday.services import visible_jobs as _vj
        # queued=False: the head makes no local-model call, so it never needs a place in the Local AI queue.
        with _vj.visible_job("Laya tool head (shadow)", kind="laya_head", workspace="system",
                             eager=False, queued=False):
            with _shadow_lock:
                shadow_jobs_opened += 1
            while True:
                with _shadow_cv:
                    if not _shadow_q:
                        _shadow_cv.wait(SHADOW_IDLE_WINDOW_S)
                    if not _shadow_q:
                        _shadow_runner = False          # decided under the lock: the next shadow() starts a new runner
                        return
                    item = _shadow_q.popleft()
                _shadow_job(*item)
    except Exception as e:  # noqa: BLE001
        _log.debug("shadow runner stopped: %s", type(e).__name__)
    finally:
        with _shadow_lock:
            _shadow_runner = False
            while _shadow_q:                            # nothing is left queued behind a dead runner
                _shadow_q.popleft()
                globals()["_shadow_pending"] -= 1


def shadow(text: str, final, proto, surface: str, profile: str = DEFAULT_PROFILE, *, sync: bool = False) -> bool:
    """Log the head's choice beside the router's, off the turn's path. Drops
    the row when a backlog builds. Returns whether a row was queued."""
    global _shadow_runner, _shadow_pending
    if not text or not available(profile):
        return False
    try:
        from agent_friday.services import off_record
        if off_record.active():
            return False                    # nothing about the conversation is written while off the record
    except Exception:
        pass
    if sync:
        with _shadow_lock:
            _shadow_pending += 1
        _shadow_job(text, final, proto, surface, profile)
        return True
    with _shadow_cv:
        if _shadow_pending >= MAX_SHADOW_BACKLOG:
            return False
        _shadow_pending += 1
        _shadow_q.append((text, final, proto, surface, profile))
        start = not _shadow_runner
        if start:
            _shadow_runner = True
        else:
            _shadow_cv.notify()
    if start:
        try:
            threading.Thread(target=_shadow_runner_loop, args=(profile,), name="laya-router-head",
                             daemon=True).start()
        except Exception:
            with _shadow_lock:
                _shadow_runner = False
                while _shadow_q:
                    _shadow_q.popleft()
                    _shadow_pending -= 1
            return False
    return True
