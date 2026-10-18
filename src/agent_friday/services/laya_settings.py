"""Settings > Laya: what Laya does, her modes, her report card, the replay test and per-action bars.

Spec: ftv/program/specs/laya_settings_STORM_2026-10-17.md (Slice 1). Everything here is read on this
PC from what Friday already keeps; nothing is sent anywhere and no model is loaded to answer.

  overview()          the tab's whole read: what runs, modes, report card, bars
  run_replay()        replay the owner's own labelled turns through the router, plain and reshuffled
  set_bars(...)       change per-action bars or the bar mode (the route decides who may loosen)
  live_bars()         the bars the router may use on a live turn: {} unless the owner turned them to act

Naming. "Series One" is the user-facing name of the shipped Laya base generation: the sealed rules
(R0) and the MiniLM phrase matcher (R1) with seed set laya2-seeds-v1 and the calibration table,
pinned by ``laya.versions()["base"]``. Convai's ModernBERT (the approval check's second opinion,
"Laya 1" in older specs) is not Series One and is shown separately, always with its mode.

Two rules are code here, not copy. The approval check and safe writes have no "act" position:
MODE_TABLE gives them none, and nothing in this module can produce one. And a bar for an action
that changes something never goes below the shipped bar: a raised one comes back down only by the
owner's click, and never past shipped.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import statistics
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

SERIES_NAME = "Series One"

MODES = ("off", "shadow", "suggest", "act")
MODE_MEANING = {
    "off": "She does not run for this.",
    "shadow": "She runs and records what she would have done; nothing changes.",
    "suggest": "She can add a question or a card; you or the brain decide.",
    "act": "She does it herself, within her limits; anything that changes something still goes through its own card.",
}

#: Bars. Reads that change nothing may go from READ_MIN; everything else only up from the shipped score.
BAR_STEP = 0.05
READ_MIN = 0.40
BAR_MAX = 0.95
RECOMMENDED_READ_BAR = 0.45
BAR_MODES = ("shadow", "act")

#: Drift: shown (never notified) when the take rate moves this much with this many guesses each side.
DRIFT_POINTS = 10.0
DRIFT_MIN_GUESSES = 20
WEEKS_SHOWN = 8
LOG_TAIL_BYTES = 8 * 1024 * 1024

REPLAY_MAX_ROWS = 300
REPLAY_YIELD_S = 0.005
REPLAY_DIRNAME = "laya_replay"

_lock = threading.Lock()
_replay_lock = threading.Lock()
_cache: Dict[str, object] = {"checked": 0.0, "mtime": None, "path": None, "data": None}


# ---------------------------------------------------------------------------
#  paths
# ---------------------------------------------------------------------------

def _home() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR)


def bars_path() -> Path:
    return _home() / "laya" / "action_bars.json"


def bars_history_path() -> Path:
    return _home() / "laya" / "action_bars_history.jsonl"


def replay_dir() -> Path:
    return _home() / "bench" / REPLAY_DIRNAME


def _router_log() -> Path:
    from agent_friday.services import laya_router
    return laya_router.log_path()


# ---------------------------------------------------------------------------
#  what runs
# ---------------------------------------------------------------------------

def what_runs() -> dict:
    """The model cards. Reads ids, files and the decision log; never loads or warms a model."""
    out: dict = {"series": SERIES_NAME, "where": "This PC's processor (CPU). It uses no graphics memory.",
                 "process_note": "Laya runs inside Friday's own process, so her memory is not counted separately."}
    try:
        from agent_friday.services import laya
        out["versions"] = laya.versions()
    except Exception:
        out["versions"] = {}
    try:
        from agent_friday.services import laya2_encoder as enc
        path = enc.artifacts_dir() / enc.MODEL_NAME
        out["matcher"] = {"model": enc.MODEL_ID, "licence": enc.MODEL_LICENCE, "installed": bool(enc.is_available()),
                          "size_mb": round(path.stat().st_size / 1e6, 1) if path.is_file() else None}
    except Exception:
        out["matcher"] = {"model": None, "installed": False, "size_mb": None}
    try:
        from agent_friday.services import laya_router as lr
        out["budget_ms"] = lr.DEFAULT_BUDGET_MS
        out["shipped_bar"] = lr.ACT_THRESHOLD
        out["margin"] = lr.MARGIN_THRESHOLD
    except Exception:
        out["budget_ms"], out["shipped_bar"], out["margin"] = 50, 0.55, 0.10
    out["speed"] = _speed(_read_log())
    try:
        from agent_friday.services import laya_backend as lb
        st = lb.status()
        out["second_opinion"] = {"model": st.get("model"), "ready": bool(st.get("ready")),
                                 "loading": bool(st.get("loading")), "engine": st.get("engine"),
                                 "threads": st.get("threads"), "device": st.get("device") or "cpu",
                                 "mode": _gate_mode()}
    except Exception:
        out["second_opinion"] = {"model": None, "ready": False, "mode": _gate_mode()}
    return out


def _speed(rows: List[dict], days: int = 7) -> dict:
    since = datetime.now() - timedelta(days=days)
    ms = [float(r["elapsed_ms"]) for r in rows
          if isinstance(r.get("elapsed_ms"), (int, float)) and (_at(r) or datetime.min) >= since]
    if not ms:
        return {"turns": 0, "p50_ms": None, "p95_ms": None, "days": days}
    ms.sort()
    p95 = ms[min(len(ms) - 1, max(0, math.ceil(0.95 * len(ms)) - 1))]       # nearest rank
    return {"turns": len(ms), "p50_ms": round(statistics.median(ms), 1), "p95_ms": round(p95, 1), "days": days}


# ---------------------------------------------------------------------------
#  modes
# ---------------------------------------------------------------------------

def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _gate_mode(settings: Optional[dict] = None) -> str:
    """off | shadow | suggest | custom. The approval check's 'on' is SUGGEST: it can add a check, never approve."""
    try:
        from agent_friday.services import laya_backend as lb
        m = lb.current_mode(settings)
    except Exception:
        return "off"
    return {"off": "off", "shadow": "shadow", "on": "suggest"}.get(m, "custom")


def _safe_writes_mode() -> str:
    try:
        from agent_friday.services import laya_safe_writes as sw
        m = sw.mode()
    except Exception:
        return "off"
    return {"off": "off", "shadow": "shadow", "on": "suggest"}.get(m, "off")


def _three(v, default="off") -> str:
    return {"off": "off", "shadow": "shadow", "on": "act"}.get(str(v or default), "off")


#: Capabilities whose top position is suggest: never act, whatever a file says.
NEVER_ACT = frozenset({"approval_check", "safe_writes"})


def modes(settings: Optional[dict] = None) -> List[dict]:
    s = _settings() if settings is None else settings
    bars = load_bars()
    rows = [
        ("voice_routing", "Spoken tool routing", "act" if str(s.get("voice_tool_routing") or "laya") == "laya" else "off",
         "Opens screens and runs reads you ask for by voice, without waking the brain.", "voice"),
        ("chat_routing", "Typed tool routing", "act" if str(s.get("chat_tool_routing") or "") == "laya" else "off",
         "The same, for what you type in chat.", ("laya", "Instant answers (no model)")),
        ("approval_check", "Approval check's second opinion", _gate_mode(s),
         "Can add a check before an action. She can never approve one.", ("privacy", "What needs your sign-off")),
        ("tool_head", "Trained tool head", _three(s.get("laya_tool_head"), "shadow"),
         "Inert until a trained head is certified on this PC.", None),
        ("live_reflex", "Live-call reflex", _three(s.get("voice_live_reflex"), "shadow"),
         "Quick routing beside a live cloud call.", "voice"),
        ("safe_writes", "Safe writes on mail", _safe_writes_mode(),
         "Puts the approval card up for mail changes you point at. You approve every card.",
         ("laya", "Safe writes (off until you turn them on)")),
        ("personal_learning", "Learning from your corrections", "act" if s.get("laya_personal_learning", True) is not False else "off",
         "What she learns can only make her more careful.", ("laya", "What Laya learns about you")),
        ("calibration", "Confidence scoring", "shadow",
         "Measures how far her confidence can be believed. It never changes a decision.", None),
        ("bars", "Your confidence bars", (bars["mode"] if bars["bars"] else "off"),
         "Your own act score per action. Off until you set one.", ("laya", "Confidence bars")),
    ]
    out = []
    for cid, label, mode, what, where in rows:
        if cid in NEVER_ACT and mode == "act":     # cannot happen from the mappings above; held here anyway
            mode = "suggest"
        tab, section = (where if isinstance(where, tuple) else (where, None))
        out.append({"id": cid, "label": label, "mode": mode, "meaning": MODE_MEANING.get(mode, "Set by hand in the settings file."),
                    "what": what, "change_in": tab, "change_section": section, "never_act": cid in NEVER_ACT})
    return out


# ---------------------------------------------------------------------------
#  teach her a phrase (Slice 2)
# ---------------------------------------------------------------------------

TEACH_NOTHING = "conversation"


def teach_labels() -> List[dict]:
    """What a taught phrase may mean: every action the router can choose that may be a label
    (laya_personal._can_label), and "just talk to me" (no action)."""
    try:                                                  # never the reason the tab cannot load
        lr = _router()
        from agent_friday.services import laya_personal as lp
        out = [{"id": t, "label": _label(t), "class": bar_class(t)} for t in lr.TOOLS if lp._can_label(t)]
    except Exception:
        return []
    out.append({"id": TEACH_NOTHING, "label": "nothing: just talk to me", "class": "none"})
    return out


def teach(text: str, label: str, profile: Optional[str] = None) -> dict:
    """Teach her one phrasing, typed by the owner on this page: it becomes a gold correction row through the same
    capture, sensitivity and test-question checks as a spoken correction (laya_personal.teach_phrase)."""
    if label not in {r["id"] for r in teach_labels()}:
        return {"ok": False, "why": "Pick one of her actions, or \"nothing: just talk to me\"."}
    from agent_friday.services import laya_personal as lp
    return lp.teach_phrase(text, label, profile=profile)


# ---------------------------------------------------------------------------
#  the decision log
# ---------------------------------------------------------------------------

def _read_log(path: Optional[Path] = None) -> List[dict]:
    p = path or _router_log()
    try:
        size = p.stat().st_size
    except OSError:
        return []
    try:
        with open(p, "rb") as f:
            if size > LOG_TAIL_BYTES:
                f.seek(size - LOG_TAIL_BYTES)
                f.readline()                     # drop the partial first line
            data = f.read()
    except OSError:
        return []
    rows = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _at(row: dict) -> Optional[datetime]:
    try:
        return datetime.strptime(str(row.get("at"))[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None


def _week(d: datetime) -> str:
    y, w, _ = d.isocalendar()
    return "%d-W%02d" % (y, w)


def take_rate(rows: List[dict], now: Optional[datetime] = None, weeks: int = WEEKS_SHOWN) -> dict:
    """Per ISO week: of the turns the phrase matcher guessed at (layer laya2), the share she acted on; and the
    same over all turns. Drift compares this week with the mean of up to four earlier weeks."""
    now = now or datetime.now()
    keys = []
    for i in range(weeks - 1, -1, -1):
        k = _week(now - timedelta(weeks=i))
        if k not in keys:
            keys.append(k)
    by = {k: {"week": k, "guesses": 0, "guess_acted": 0, "turns": 0, "acted": 0} for k in keys}
    for r in rows:
        d = _at(r)
        if d is None:
            continue
        b = by.get(_week(d))
        if b is None:
            continue
        acted = r.get("decision") == "tool"
        b["turns"] += 1
        b["acted"] += acted
        if r.get("layer") == "laya2":
            b["guesses"] += 1
            b["guess_acted"] += acted
    out = []
    for k in keys:
        b = by[k]
        b["rate"] = round(100.0 * b["guess_acted"] / b["guesses"], 1) if b["guesses"] else None
        b["all_rate"] = round(100.0 * b["acted"] / b["turns"], 1) if b["turns"] else None
        out.append(b)
    cur = out[-1]
    prior = [w for w in out[-5:-1] if w["guesses"] >= DRIFT_MIN_GUESSES]
    drift = {"flag": False, "points": None, "note": "Not enough turns yet to tell."}
    if cur["guesses"] >= DRIFT_MIN_GUESSES and prior:
        base = statistics.mean(w["rate"] for w in prior)
        pts = round(cur["rate"] - base, 1)
        flag = abs(pts) >= DRIFT_POINTS
        drift = {"flag": flag, "points": pts, "baseline": round(base, 1),
                 "note": ("This week she acted on %.0f%% of her guesses, %s than her usual %.0f%%." % (
                     cur["rate"], "more" if pts > 0 else "fewer", base)) if flag else "Steady."}
    return {"weeks": out, "drift": drift}


# ---------------------------------------------------------------------------
#  bars
# ---------------------------------------------------------------------------

def _router():
    from agent_friday.services import laya_router as lr
    return lr


def bar_class(tool: str) -> str:
    """read | change | outward. From the governed tool table (laya.mutation_class) and the router's own
    outward set; a tool the table cannot describe is a change."""
    lr = _router()
    if tool in lr.ASK_BEFORE_GUESSING:
        return "outward"
    try:
        from agent_friday.services import laya
        return "read" if laya.mutation_class(tool) == "read" else "change"
    except Exception:
        return "change"


def bar_range(tool: str) -> Optional[Tuple[float, float]]:
    cls = bar_class(tool)
    if cls == "outward":
        return None
    return (READ_MIN if cls == "read" else _router().ACT_THRESHOLD, BAR_MAX)


def _snap(v: float) -> float:
    return round(round(float(v) / BAR_STEP) * BAR_STEP, 2)


def load_bars() -> dict:
    """{"mode": shadow|act, "bars": {tool: value}}. A missing or damaged file is no bars, in shadow;
    any stored value outside today's rules is dropped, never trusted."""
    try:
        raw = json.loads(bars_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    mode = raw.get("mode") if raw.get("mode") in BAR_MODES else "shadow"
    bars = {}
    lr = _router()
    for tool, v in (raw.get("bars") or {}).items():
        if tool not in lr.TOOLS:
            continue
        rng = bar_range(tool)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if rng and rng[0] - 1e-9 <= v <= rng[1] + 1e-9:
            bars[tool] = _snap(v)
    return {"mode": mode, "bars": bars}


def bars_view() -> dict:
    """Every action the router can choose, with its class, the shipped bar, the owner's bar, the range
    and the labelled recommendation (never preselected)."""
    lr = _router()
    cur = load_bars()
    rows = []
    for tool, (label, _t, _r) in lr.TOOLS.items():
        cls = bar_class(tool)
        rng = bar_range(tool)
        rows.append({"tool": tool, "label": label, "class": cls, "shipped": lr.ACT_THRESHOLD,
                     "yours": cur["bars"].get(tool), "min": rng[0] if rng else None, "max": rng[1] if rng else None,
                     "recommended": RECOMMENDED_READ_BAR if cls == "read" else None,
                     "why": {"read": "A wrong read is cheap and you can undo it, so it may be set lower.",
                             "change": "This changes something, so here she can only be made stricter.",
                             "outward": "A guess at an outward search always asks first, whatever the bar."}[cls]})
    # Reads first: they are the bars a person can lower, and the ones a recommendation is offered for.
    order = {"read": 0, "change": 1, "outward": 2}
    rows.sort(key=lambda r: order[r["class"]])
    return {"mode": cur["mode"], "rows": rows, "step": BAR_STEP}


def is_loosening(new_bars: Dict[str, Optional[float]], new_mode: Optional[str],
                 cur: Optional[dict] = None) -> bool:
    """True when the change would let her act more: a bar set below what is in force now (the owner's bar,
    else the shipped one), the mode turned to act, or the mode turned from act to shadow while a stricter
    bar is in force (in shadow every action falls back to the shipped score, so that bar stops binding)."""
    cur = cur if cur is not None else load_bars()
    if new_mode == "act" and cur["mode"] != "act":
        return True
    shipped = _router().ACT_THRESHOLD
    if new_mode == "shadow" and cur["mode"] == "act" and any(v > shipped + 1e-9 for v in cur["bars"].values()):
        return True
    for tool, v in (new_bars or {}).items():
        now = cur["bars"].get(tool, shipped)
        if v is None:
            if now > shipped + 1e-9:        # clearing a stricter bar loosens back to shipped
                return True
            continue
        try:
            if float(v) < now - 1e-9:
                return True
        except (TypeError, ValueError):
            continue
    return False


def set_bars(new_bars: Optional[Dict[str, Optional[float]]] = None, mode: Optional[str] = None,
             *, actor: str = "owner", may_loosen: bool = True) -> dict:
    """Apply a change. Refuses (ok False) a tool the router cannot choose, an outward search, a value off
    the range (a change-class bar below the shipped score is off its range) or an unknown mode. With
    ``may_loosen`` False (no click on the page: routes/laya_settings), a change that lets her act more is
    refused with ``needs_click``; the check reads the bars under the same lock that writes them, so a
    change made in between cannot be undone without a click."""
    lr = _router()
    new_bars = dict(new_bars or {})
    if mode is not None and mode not in BAR_MODES:
        return {"ok": False, "why": "The mode is shadow or act."}
    with _lock:
        cur = load_bars()
        if not may_loosen and is_loosening(new_bars, mode, cur):
            return {"ok": False, "needs_click": True,
                    "why": "Letting Laya act more needs a click on the screen, in Friday's own page."}
        bars = dict(cur["bars"])
        for tool, v in new_bars.items():
            if tool not in lr.TOOLS:
                return {"ok": False, "why": "%s is not an action Laya can take." % tool}
            rng = bar_range(tool)
            if rng is None:
                return {"ok": False, "why": "A guess at an outward search always asks first; it has no bar."}
            if v is None:
                bars.pop(tool, None)
                continue
            try:
                raw = float(v)
            except (TypeError, ValueError):
                return {"ok": False, "why": "A bar is a number."}
            fv = _snap(raw)
            if not (rng[0] - 1e-9 <= raw <= rng[1] + 1e-9):
                if bar_class(tool) == "change" and fv < rng[0]:
                    return {"ok": False, "why": "%s changes something, so its bar can only be raised (from %.2f)." % (tool, rng[0])}
                return {"ok": False, "why": "The bar for %s goes from %.2f to %.2f." % (tool, rng[0], rng[1])}
            bars[tool] = fv
        out = {"mode": mode or cur["mode"], "bars": bars}
        p = bars_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
        with open(bars_history_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "actor": actor,
                                "before": cur, "after": out}, sort_keys=True) + "\n")
        _cache.update(checked=0.0, mtime=None, data=None)
    return {"ok": True, "mode": out["mode"], "bars": out["bars"]}


def live_bars() -> Dict[str, float]:
    """The bars a live turn may use: empty unless the owner turned them to act. Re-filtered against today's
    rules on every read (a change-class bar below the shipped score never reaches the router). Cached; the
    file's modification time is checked at most once a second, so a turn pays no file read."""
    now = time.monotonic()
    try:
        p = bars_path()
    except Exception:
        return {}
    if now - float(_cache["checked"]) < 1.0 and _cache["path"] == str(p) and _cache["data"] is not None:
        return dict(_cache["data"])
    _cache["checked"] = now
    try:
        st = p.stat()
        mt = (st.st_mtime_ns, st.st_size)
    except OSError:
        mt = None
    if mt == _cache["mtime"] and _cache["path"] == str(p) and _cache["data"] is not None:
        return dict(_cache["data"])
    data: Dict[str, float] = {}
    if mt is not None:
        b = load_bars()
        if b["mode"] == "act":
            data = dict(b["bars"])
    _cache.update(mtime=mt, path=str(p), data=data)
    return dict(data)


def learned_threshold(profile: Optional[str]) -> float:
    """The act score this profile's own corrections raised (never below the shipped one); the router's
    rule is that it still binds over a lower bar."""
    lr = _router()
    if not profile:
        return lr.ACT_THRESHOLD
    try:
        from agent_friday.services import laya_personal
        return max(lr.ACT_THRESHOLD, float(laya_personal.act_threshold(profile, lr.ACT_THRESHOLD)))
    except Exception:
        return lr.ACT_THRESHOLD


def apply_bar(decision: Optional[str], tool: Optional[str], layer: Optional[str], conf, runner_conf,
              bars: Dict[str, float], learned: Optional[float] = None) -> Optional[str]:
    """What a guessed turn would have been under ``bars`` (None when the bars do not touch it). Only the
    phrase matcher's own guesses (layer laya2) at a tool with a bar move; rules never do. ``learned`` is a
    threshold the owner's corrections raised: as in the router, it still binds over a lower bar."""
    if layer != "laya2" or not tool or tool not in bars or decision not in ("tool", "ask"):
        return None
    lr = _router()
    bar = float(bars[tool])
    if learned is not None and learned > lr.ACT_THRESHOLD + 1e-9:
        bar = max(bar, float(learned))
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return None
    try:
        rc = float(runner_conf) if runner_conf is not None else 0.0
    except (TypeError, ValueError):
        rc = 0.0
    if c >= bar and (c - rc) >= lr.MARGIN_THRESHOLD:
        return "tool"
    return "ask" if c >= lr.ASK_THRESHOLD else "no_tool"


def bars_preview(rows: List[dict], bars: Dict[str, float], days: int = 28,
                 learned: Optional[float] = None) -> dict:
    """Shadow: over the last ``days`` of the decision log, how many turns would have gone from asking to
    acting, or from acting to asking, under ``bars``. Needs no new logging. An estimate: the log has no
    turn vector, so a learned phrasing's own guard is not replayed."""
    since = datetime.now() - timedelta(days=days)
    more = fewer = 0
    by: Dict[str, Dict[str, int]] = {}
    for r in rows:
        if (_at(r) or datetime.min) < since:
            continue
        ru = r.get("runner_up")
        rc = ru[1] if isinstance(ru, (list, tuple)) and len(ru) > 1 else None
        new = apply_bar(r.get("decision"), r.get("tool"), r.get("layer"), r.get("confidence"), rc, bars, learned)
        if new is None or new == r.get("decision"):
            continue
        t = by.setdefault(r.get("tool"), {"more": 0, "fewer": 0})
        if new == "tool":
            more += 1
            t["more"] += 1
        elif r.get("decision") == "tool":
            fewer += 1
            t["fewer"] += 1
    return {"days": days, "would_act_more": more, "would_ask_more": fewer, "by_tool": by}


# ---------------------------------------------------------------------------
#  the replay test
# ---------------------------------------------------------------------------

_POLITE_PRE = ("could you ", "please ", "can you ", "hey, ")
_POLITE_POST = (" please", " for me", " now", "")
_SYNONYMS = (
    (r"\bopen\b", "bring up"), (r"\bshow\b", "pull up"), (r"\bplay\b", "put on"), (r"\bcheck\b", "look at"),
    (r"\bsearch\b", "look through"), (r"\bfind\b", "look for"), (r"\bstop\b", "halt"), (r"\bpause\b", "hold"),
    (r"\bstart\b", "kick off"), (r"\bwhat's\b", "what is"), (r"\bwhats\b", "what is"), (r"\btell me\b", "let me know"),
    (r"\bgo to\b", "take me to"), (r"\bturn on\b", "switch on"), (r"\bturn off\b", "switch off"),
)


def reshuffle(text: str, seed: str) -> str:
    """A deterministic rewording of ``text`` (seeded by the row id): a politeness frame, verb synonyms, lower
    case without end punctuation. Same input, same output; different from the input for any text that has a
    letter."""
    rnd = random.Random(hashlib.sha256(("laya-reshuffle:" + str(seed)).encode("utf-8")).hexdigest())
    t = " ".join(str(text or "").split()).lower().rstrip(" .!?")
    for pat, rep in _SYNONYMS:
        t = re.sub(pat, rep, t)
    pre = rnd.choice(_POLITE_PRE)
    post = rnd.choice(_POLITE_POST)
    if t.startswith(pre.strip(", ")):
        pre = ""
    out = (pre + t + post).strip()
    if out == " ".join(str(text or "").split()) and t:
        out = "could you " + t
    return out


def _labelled(rows: List[dict]) -> Tuple[List[dict], int, int]:
    """(scored rows, newest first, capped; rows waiting for the owner's OK; rows set aside)."""
    from agent_friday.services import laya_personal as lp
    keep, waiting, aside = [], 0, 0
    for r in rows:
        if r.get("weight") == lp.SHADOW:
            waiting += 1
            continue
        if r.get("off"):
            aside += 1
            continue
        if r.get("weight") not in (lp.GOLD, lp.WEAK) or not r.get("text"):
            continue
        if lp.expect_for_label(r.get("label")) is None:
            continue
        keep.append(r)
    keep.sort(key=lambda r: float(r.get("at") or 0), reverse=True)
    return keep[:REPLAY_MAX_ROWS], waiting, aside


def _outcome(r) -> Tuple[str, Optional[str]]:
    return (r.decision, r.tool if r.decision == "tool" else None)


def score(expect: Tuple[str, Optional[str]], got: Tuple[str, Optional[str]]) -> str:
    """right | miss | wrong | safe. A miss: she should have acted and asked or handed it on. Wrong: she acted
    and it was not the right action (including acting where none was wanted). Safe: neither, and not right."""
    if got == expect:
        return "right"
    if got[0] == "tool":
        return "wrong"
    if expect[0] == "tool":
        return "miss"
    return "safe"


def _route_one(text: str, profile: Optional[str]):
    lr = _router()
    tok = lr.PROFILE_CTX.set(profile)
    try:
        return lr.route(text, log=False, trusted=False, head_mode="off", surface="chat", budget_ms=1000)
    finally:
        lr.PROFILE_CTX.reset(tok)


def _tally(cases: List[dict], key: str) -> dict:
    tot = {"right": 0, "miss": 0, "wrong": 0, "safe": 0}
    per: Dict[str, Dict[str, int]] = {}
    for c in cases:
        res = c[key]
        tot[res] += 1
        action = c["expect"][1] or ("no action" if c["expect"][0] == "no_tool" else "the brain")
        p = per.setdefault(action, {"right": 0, "miss": 0, "wrong": 0, "safe": 0, "of": 0})
        p[res] += 1
        p["of"] += 1
    n = len(cases)
    return {"of": n, **tot, "accuracy": round(100.0 * tot["right"] / n, 1) if n else None, "per_action": per}


def run_replay(*, profile: Optional[str] = None, route_fn=None) -> dict:
    """Replay the owner's own labelled turns, plain and reshuffled. Reads the sealed store (nothing when the
    vault is locked), writes counts and row ids only, append-only, under ~/.friday/bench/laya_replay/."""
    if not _replay_lock.acquire(blocking=False):
        return {"ok": False, "why": "A replay is already running."}
    try:
        from agent_friday.services import laya_personal as lp
        from agent_friday.services import laya_personal_store as store
        profile = profile or lp.current_profile()
        if not profile:
            return {"ok": False, "why": "Only the signed-in owner's history can be replayed."}
        rows = store.load_rows(profile)
        if rows is None:
            return {"ok": False, "why": "Your vault is locked, so the replay cannot read your history."}
        scored, waiting, aside = _labelled(rows)
        route_fn = route_fn or _route_one
        bars = load_bars()
        shadow_bars = bars["bars"] if bars["mode"] != "act" else {}
        learned = learned_threshold(profile)
        t0 = time.perf_counter()
        cases = []
        for r in scored:
            exp = lp.expect_for_label(r["label"])
            plain = route_fn(r["text"], profile)
            shuf = route_fn(reshuffle(r["text"], r.get("id") or ""), profile)
            got = _outcome(plain)
            case = {"id": r.get("id"), "expect": list(exp), "got": list(got), "plain": score(exp, got),
                    "reshuffled_got": list(_outcome(shuf)), "reshuffled": score(exp, _outcome(shuf))}
            if shadow_bars:
                cands = list(getattr(plain, "candidates", None) or [])
                rc = cands[1][1] if len(cands) > 1 else None
                nd = apply_bar(plain.decision, plain.tool, plain.layer, plain.confidence, rc, shadow_bars, learned)
                if nd is not None:
                    case["with_your_bars"] = score(exp, (nd, plain.tool if nd == "tool" else None))
            cases.append(case)
            time.sleep(REPLAY_YIELD_S)       # the router's one worker serves live turns between rows
        res = {"ok": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "series": SERIES_NAME, "profile": profile,
               "plain": _tally(cases, "plain"), "reshuffled": _tally(cases, "reshuffled"),
               "waiting_not_scored": waiting, "set_aside_not_scored": aside, "capped_at": REPLAY_MAX_ROWS,
               "elapsed_s": round(time.perf_counter() - t0, 2), "cases": cases}
        if shadow_bars:
            moved = [c for c in cases if "with_your_bars" in c]
            res["with_your_bars"] = {"moved": len(moved),
                                     "right_after": sum(1 for c in moved if c["with_your_bars"] == "right"),
                                     "wrong_after": sum(1 for c in moved if c["with_your_bars"] == "wrong")}
        p, r_ = res["plain"]["accuracy"], res["reshuffled"]["accuracy"]
        res["memory_gap"] = round(p - r_, 1) if p is not None and r_ is not None else None
        if not cases:
            res["note"] = ("Nothing to replay yet. Corrections, answered questions and the choices you confirm "
                           "in \"What Laya learns about you\" become her test.")
        _save_result(res)
        return res
    finally:
        _replay_lock.release()


def _save_result(res: dict) -> None:
    try:
        d = replay_dir()
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        n = 0
        while (d / ("%s-%d.json" % (stamp, n))).exists():
            n += 1
        body = json.dumps(res, sort_keys=True)
        (d / ("%s-%d.json" % (stamp, n))).write_text(body, encoding="utf-8")
        tmp = d / "latest.tmp"
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, d / "latest.json")
    except OSError as e:
        _log.warning("replay result not saved: %s", type(e).__name__)


def _current_profile() -> Optional[str]:
    try:
        from agent_friday.services import laya_personal as lp
        return lp.current_profile()
    except Exception:
        return None


def last_replay(include_cases: bool = False) -> Optional[dict]:
    """The newest replay of the profile asking (the signed-in owner); another profile's result is not shown."""
    try:
        res = json.loads((replay_dir() / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not res.get("profile") or res.get("profile") != _current_profile():
        return None
    if not include_cases:
        res.pop("cases", None)
    return res


def spoken_report_line() -> str:
    """One sentence for "what have you learned?": the last replay, or nothing."""
    res = last_replay()
    if not res or not res.get("ok") or not (res.get("plain") or {}).get("of"):
        return ""
    p, s = res["plain"], res.get("reshuffled") or {}
    return ("On her last replay test she got %d of %d of your requests right, and %d of %d when they were reworded." % (
        p["right"], p["of"], s.get("right", 0), s.get("of", 0)))


# ---------------------------------------------------------------------------
#  by voice (Slice 2): the replay, and bars in the safe direction only
# ---------------------------------------------------------------------------

#: How an owner names an action when speaking about her bars. "everything" names every action with a bar.
SPOKEN_ACTIONS = (
    (r"\b(?:calendar|schedule|agenda)\b", ("query_calendar",)),
    (r"\b(?:e-?mails?|inbox|mail)\b", ("check_email", "search_email")),
    (r"\bbriefing\b", ("get_briefing",)),
    (r"\b(?:files?|documents?)\b", ("search_files",)),
    (r"\b(?:notes|wiki)\b", ("search_wiki",)),
    (r"\b(?:conversations?|chats)\b", ("search_past_conversations",)),
    (r"\bqueue\b", ("queue_status",)),
    (r"\b(?:music|media|videos?)\b", ("media_play",)),
    (r"\bpodcasts?\b", ("podcast_play",)),
    (r"\b(?:screens?|opening things|navigat\w*)\b", ("navigate_to",)),
    (r"\bcards\b", ("set_chat_tray",)),
    (r"\b(?:web|news|online)\b", ("search_web", "search_news")),
)
VOICE_RAISE_STEP = 0.10
_V_SHADOW = re.compile(r"\b(?:stop using|turn off|switch off) (?:my|your|laya'?s|the) (?:confidence )?bars\b"
                       r"|\bbars (?:back )?(?:to|in|on) shadow\b", re.I)
_V_ACT = re.compile(r"\b(?:use|start using|turn on|switch on) (?:my|your|laya'?s|the) (?:confidence )?bars\b"
                    r"|\bbars (?:back )?(?:to|in|on) act\b", re.I)
_V_RESET = re.compile(r"\breset (?:my|your|laya'?s|the) (?:confidence )?bars\b", re.I)
_V_LOOSER = re.compile(r"\b(?:bolder|braver|looser|less careful|less cautious|lower)\b", re.I)
_V_STRICTER = re.compile(r"\b(?:stricter|more careful|more cautious|raise|higher)\b", re.I)
NEEDS_CLICK_LINE = ("NEEDS_SCREEN: letting Laya act more needs a click on the screen. Settings > Laya > Confidence bars "
                    "has the control. Nothing changed.")


def spoken_actions(words: str) -> Tuple[List[str], List[str]]:
    """(actions with a bar the words name, outward searches they name). "everything" or "all" is every action
    with a bar."""
    w = " ".join(str(words or "").lower().split())
    lr = _router()
    if re.search(r"\b(?:everything|all of (?:it|them)|all actions|every action)\b", w):
        names = [t for t in lr.TOOLS if bar_range(t)]
        return names, []
    picked: List[str] = []
    for pat, tools in SPOKEN_ACTIONS:
        if re.search(pat, w):
            picked += [t for t in tools if t in lr.TOOLS and t not in picked]
    return [t for t in picked if bar_range(t)], [t for t in picked if not bar_range(t)]


def _label(tool: str) -> str:
    return tool.replace("_", " ")


def voice_bars(words: str) -> dict:
    """One spoken bar control, the owner's own words. Only the safe direction runs here: raising a bar, turning the
    bars back to shadow, and clearing bars that are LOWER than shipped. Anything that would let her act more
    (lowering, turning the bars to act, clearing a stricter bar) changes nothing and says where the click is."""
    w = " ".join(str(words or "").split())
    cur = load_bars()
    shipped = _router().ACT_THRESHOLD
    if _V_ACT.search(w):
        return {"ok": False, "needs_screen": True, "text": NEEDS_CLICK_LINE}
    if _V_SHADOW.search(w) or _V_RESET.search(w):
        return _voice_back_down(cur, shipped, reset=bool(_V_RESET.search(w)))
    tools, outward = spoken_actions(w)
    if _V_LOOSER.search(w) and not _V_STRICTER.search(w):
        return {"ok": False, "needs_screen": True, "text": NEEDS_CLICK_LINE}
    if not _V_STRICTER.search(w):
        return {"ok": False, "text": "NOT DONE: say stricter, or bars back to shadow. Lowering a bar is a click in "
                                     "Settings > Laya."}
    if not tools:
        if outward:
            return {"ok": False, "text": "NOT DONE: a guess at a web or news search always asks first, so it has no bar."}
        return {"ok": False, "text": "NOT DONE: name the action, for example the calendar, email or music."}
    new = {}
    for t in tools:
        # Stricter than what is in force: a lowered bar steps up from the shipped score, not from itself.
        base = max(cur["bars"].get(t, shipped), shipped)
        if base >= BAR_MAX - 1e-9:
            continue                                      # already as strict as a bar goes: nothing to write or announce
        new[t] = min(BAR_MAX, _snap(base + VOICE_RAISE_STEP))
    if not new:
        return {"ok": False, "text": "NOT DONE: that is already as strict as a bar goes (%.2f)." % BAR_MAX}
    res = set_bars(new, actor="owner:voice", may_loosen=False)
    if not res["ok"]:
        return {"ok": False, "text": "NOT DONE: " + res.get("why", "")}
    said = ", ".join("%s %.2f" % (_label(t), v) for t, v in new.items())
    mode = "They apply now." if res["mode"] == "act" else "They stay in shadow until you set your bars to act."
    tail = " Web and news searches always ask first." if outward else ""
    return {"ok": True, "text": "Stricter: %s. %s%s" % (said, mode, tail)}


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _voice_back_down(cur: dict, shipped: float, *, reset: bool) -> dict:
    """"stop using my bars" / "reset my bars" by voice. Lowered bars go back to shipped (that only makes her
    stricter). The mode goes to shadow only when no stricter bar is in force, because in shadow every action
    falls back to the shipped score and a stricter bar would stop binding. Stricter bars are never cleared here."""
    lowered = {t: None for t, v in cur["bars"].items() if v < shipped - 1e-9}
    stricter = [t for t, v in cur["bars"].items() if v > shipped + 1e-9]
    binding = cur["mode"] == "act" and bool(stricter)
    mode = None if binding else "shadow"
    if not lowered and binding:
        return {"ok": False, "needs_screen": True, "text": NEEDS_CLICK_LINE}     # the only change asked for loosens
    if not lowered and cur["mode"] == "shadow":
        text = "Your bars are already in shadow: they are measured and change nothing."
        if reset and stricter:
            text += (" %d stricter %s kept for when you set them to act again; clearing %s is a click in Settings > "
                     "Laya." % (len(stricter), _plural(len(stricter), "bar is", "bars are"),
                                _plural(len(stricter), "it", "them")))
        return {"ok": True, "text": text}
    res = set_bars(lowered, mode, actor="owner:voice", may_loosen=False)
    if not res["ok"]:
        return {"ok": False, "needs_screen": bool(res.get("needs_click")),
                "text": NEEDS_CLICK_LINE if res.get("needs_click") else "NOT DONE: " + res.get("why", "")}
    parts = []
    if lowered:
        parts.append("%d lowered %s back to how she shipped." % (len(lowered), _plural(len(lowered), "bar is", "bars are")))
    if binding:
        parts.append("%d stricter %s still in force; turning your bars off would let her act more, so that is a click "
                     "in Settings > Laya." % (len(stricter), _plural(len(stricter), "bar is", "bars are")))
    else:
        parts.append("Your bars are in shadow: they are measured and change nothing.")
        if reset and stricter:
            parts.append("%d stricter %s kept for when you set them to act again; clearing %s is a click in Settings > "
                         "Laya." % (len(stricter), _plural(len(stricter), "bar is", "bars are"),
                                    _plural(len(stricter), "it", "them")))
    return {"ok": True, "text": " ".join(parts)}


def voice_replay(profile: Optional[str] = None) -> dict:
    """The replay test, run by voice: the same run as the button, answered in one or two sentences of counts."""
    res = run_replay(profile=profile)
    if not res.get("ok"):
        return {"ok": False, "text": "NOT DONE: " + str(res.get("why") or "the replay could not run.")}
    p, s = res["plain"], res["reshuffled"]
    if not p["of"]:
        return {"ok": True, "text": res.get("note") or "Nothing to replay yet."}
    return {"ok": True, "text": ("Replay done: %d of %d of your requests right, %d of %d when reworded. She missed %d "
                                 "(asked or handed them on) and did the wrong thing %d time%s. Settings > Laya has the "
                                 "breakdown." % (p["right"], p["of"], s["right"], s["of"], p["miss"], p["wrong"],
                                                 "" if p["wrong"] == 1 else "s"))}


# ---------------------------------------------------------------------------
#  the tab
# ---------------------------------------------------------------------------

def overview() -> dict:
    rows = _read_log()
    cur = load_bars()
    return {"what_runs": what_runs(), "modes": modes(), "mode_meaning": MODE_MEANING,
            "report": {"take_rate": take_rate(rows), "replay": last_replay(),
                       "bars_preview": (bars_preview(rows, cur["bars"], learned=learned_threshold(_current_profile()))
                                        if cur["bars"] else None)},
            "bars": bars_view(), "teach_labels": teach_labels()}
