"""Decision heads: small, fast Laya questions with thresholds kept as versioned files.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md section 6 (DM2;
owner decisions S1 to S9, 2026-10-24). A decision head is one Laya preset
(``laya.ask``) answering one typed question: triage lanes, whether retrieved
passages suffice, whether a loop has stopped making progress. This module
holds what each head is allowed to do and the numbers it acts on. It never
loads a model, never answers a question and never writes a card.

WHAT LIVES WHERE

  resources/decision_heads/manifest.json   one row per head: file, version, sha256
  resources/decision_heads/<head>.json     the shipped head: preset, question type,
                                           labels, scorer, thresholds, budget, mode
  <FRIDAY_HOME>/decision_heads/active/<head>.json      an override in force
  <FRIDAY_HOME>/decision_heads/active/<head>.sig.json  its HMAC record
  <FRIDAY_HOME>/decision_heads/history.jsonl           every change, append-only

RULES THAT ARE CODE, NOT DATA (a file cannot change them)

  DIRECTIONS   each head's direction: add_only (it may add a check, a retrieval
               step or a card, never remove one), stop_only (it may end a loop
               with a typed blocker, never declare work done) or label_only (it
               labels or orders data and changes nothing else). A shipped file
               whose direction differs is refused and the head stays off.
  SCORERS      which scorers may sit behind a head. No generative model is ever
               one (a shortlist model with any role is refused): Laya judges,
               she never writes a word (spec A5, TRIO A4, S4).
  ABSTAIN      every choice head carries an "other", "unsure" or "none" label,
               so a missing option is an abstention, never a confident wrong
               pick (spec A9, DM-E08).
  SHIPPED MODE a shipped head starts "off" or "shadow", never further.

WHO MAY CHANGE A HEAD (spec 6.2, owner decision S7)

  Stricter (a higher threshold, a lower mode): ``tighten``, used by learning,
  applied at once with a receipt in the history. Bolder (a lower threshold, a
  higher mode) in any way, including an ``undo`` that would drop learning's
  tightening: only with an approved card of kind ``laya_threshold``
  (approvals.SCREEN_ONLY_KINDS: only ``owner:ui``, Stephen's press on screen)
  whose payload is exactly ``card_payload(...)`` for this head, this change and
  the shipped file in force, and whose title is the one ``card_text`` renders.
  One card authorises one application. An ``undo`` that only makes a head
  stricter (back from an owner's bolder setting to shipped) needs an owner
  request but no card: the safe direction is never harder than the risky one.

  Laya never promotes herself: no Laya module calls ``apply_owner_change`` or
  ``undo``, builds a card payload for a head, or creates a card
  (tests/unit/test_decision_heads.py greps for each).

An override is used only when its signature verifies with the governance key
and it was made against the shipped file that ships now. After an update
rewrites a head's file, the override is paused (shown, not used), as with
the instruction files (services/instruction_files). With no key nothing is
written and nothing is loaded (fail closed). A bolder override is re-checked
against its card on every load, so a card that is later expired stops counting.

Deferred (recorded in the PR): learning's "3 shadow days before a tighten
commits" is the caller's rule (DM3 and DM6 callers enforce it); per-head
history snapshots beyond the jsonl rows; ``scorer_sha256`` in head files,
which arrives with the first non-Series-One scorer.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SHIPPED_DIR = Path(__file__).resolve().parent.parent / "resources" / "decision_heads"

#: Each head's direction. Code, not data: the manifest and head files cannot change it.
DIRECTIONS: Dict[str, str] = {
    "triage.mail": "label_only",
    "triage.news": "label_only",
    "triage.notify": "add_only",
    "triage.memory": "label_only",
    "evidence.sufficient": "add_only",
    "evidence.holds": "label_only",
    "evidence.supports": "label_only",
    "loop.no_progress": "stop_only",
}
DIRECTION_NAMES = ("add_only", "stop_only", "label_only")

#: Scorers a head may use. ``generative`` is always False here: a generative model
#: is never a judge, and a scorer this table does not name is refused.
SCORERS: Dict[str, dict] = {
    "series_one": {"kind": "prototype", "generative": False,
                   "about": "R0 sealed rules plus the R1 MiniLM-L6 phrase matcher"},
    "rules": {"kind": "rules", "generative": False, "about": "deterministic features only"},
    "laya1": {"kind": "encoder", "generative": False,
              "about": "Convai laya 0.3.5 (ModernBERT), shadow only for the approval gate"},
}

#: Labels that mean "none of the offered options"; every choice head needs one.
ABSTAIN_LABELS = frozenset({"other", "unsure", "none"})
QUESTION_TYPES = ("choice", "noul", "score")
MODES = ("off", "shadow", "suggest", "act")
SHIPPED_MODES = ("off", "shadow")
THRESHOLD_NAMES = ("act", "margin", "abstain")
MAX_BUDGET_MS = 5000
APPROVAL_KIND = "laya_threshold"
#: The only decided_by that counts as Stephen's press (approvals.SCREEN_ONLY_KINDS).
PRESS = "owner:ui"
CARD_ORIGIN = "decision_heads"

_LOCK = threading.RLock()
_MANIFEST: Optional[dict] = None
_SHIPPED: Dict[str, Optional[dict]] = {}
_SHIPPED_SHA: Dict[str, str] = {}
_PROBLEMS: Dict[str, str] = {}
_PAUSED: Dict[str, bool] = {}
_EFFECTIVE: Dict[str, dict] = {}


class HeadChangeRefused(ValueError):
    """A change that was refused; the message says why in plain words."""


# --------------------------------------------------------------------------
#  shipped heads
# --------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest() -> dict:
    global _MANIFEST
    with _LOCK:
        if _MANIFEST is None:
            try:
                _MANIFEST = json.loads(_read_text(SHIPPED_DIR / "manifest.json"))
            except Exception as e:  # noqa: BLE001
                _PROBLEMS["manifest"] = "the decision-head manifest could not be read (%s)" % type(e).__name__
                _MANIFEST = {"version": 0, "heads": []}
        return _MANIFEST


def head_ids() -> List[str]:
    return [str(r.get("id")) for r in manifest().get("heads", []) if r.get("id")]


def _is_generative(scorer_id: str) -> bool:
    """A scorer counts as generative unless this module's own table says it is
    not. A shortlist model (any role: brain, system_one, coder, voice) is a
    generative model and is refused whatever the table says."""
    try:
        from agent_friday.services import model_shortlist
        if model_shortlist.get(str(scorer_id)):
            return True
    except Exception:  # noqa: BLE001
        pass
    entry = SCORERS.get(str(scorer_id))
    return entry is None or bool(entry.get("generative", True))


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except (OverflowError, ValueError):
        return None
    return f if math.isfinite(f) else None


def validate(spec) -> List[str]:
    """Reasons a head file may not be used; empty when it may."""
    if not isinstance(spec, dict):
        return ["the head file is not a JSON object"]
    bad: List[str] = []
    hid = str(spec.get("id") or "")
    if hid not in DIRECTIONS:
        bad.append("there is no decision head called %r in this build" % hid)
    elif spec.get("direction") != DIRECTIONS[hid]:
        bad.append("the file says direction %r; this head is %s and only a software update changes that"
                   % (spec.get("direction"), DIRECTIONS[hid]))
    if spec.get("question_type") not in QUESTION_TYPES:
        bad.append("question_type must be one of %s" % ", ".join(QUESTION_TYPES))
    if spec.get("question_type") == "choice":
        labels = spec.get("labels")
        if not isinstance(labels, list) or len(labels) < 2 or not all(isinstance(x, str) and x for x in labels):
            bad.append("a choice head needs at least two labels")
        elif len(set(labels)) != len(labels):
            bad.append("a label appears twice")
        elif not (set(labels) & ABSTAIN_LABELS):
            bad.append("a choice head needs an 'other', 'unsure' or 'none' label, so a missing option abstains")
        elif len(labels) > 10:
            bad.append("a choice head may offer at most 10 labels (larger sets are uncalibrated)")
    scorer = str(spec.get("scorer") or "")
    if _is_generative(scorer):
        bad.append("scorer %r is not an allowed judge (a generative model never decides)" % scorer)
    th = spec.get("thresholds")
    if not isinstance(th, dict) or set(th) - set(THRESHOLD_NAMES) or "act" not in th:
        bad.append("thresholds must hold 'act' and optionally 'margin' and 'abstain', nothing else")
    else:
        bad += _threshold_problems(th)
    b = _num(spec.get("budget_ms"))
    if b is None or not 0 < b <= MAX_BUDGET_MS:
        bad.append("budget_ms must be above 0 and at most %d" % MAX_BUDGET_MS)
    if spec.get("mode_default") not in SHIPPED_MODES:
        bad.append("a shipped head starts off or in shadow, never further")
    v = spec.get("version")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        bad.append("version must be a whole number from 1")
    return bad


def _threshold_problems(th: dict) -> List[str]:
    vals = {k: _num(v) for k, v in th.items()}
    if any(v is None or not 0.0 <= v <= 1.0 for v in vals.values()):
        return ["every threshold is a number from 0 to 1"]
    if vals.get("abstain") is not None and vals.get("act") is not None and vals["abstain"] > vals["act"]:
        return ["the abstain threshold may not be above the act threshold"]
    return []


def _manifest_row(hid: str) -> Optional[dict]:
    for r in manifest().get("heads", []):
        if r.get("id") == hid:
            return r
    return None


def shipped(hid: str) -> Optional[dict]:
    """The shipped head ``hid`` after every check, or None (and a health row)."""
    with _LOCK:
        if hid in _SHIPPED:
            return _SHIPPED[hid]
        row = _manifest_row(hid)
        spec = None
        if row is None:
            _PROBLEMS[hid] = "not in the decision-head manifest"
        else:
            try:
                text = _read_text(SHIPPED_DIR / str(row.get("file") or ""))
                if _sha(text) != row.get("sha256"):
                    _PROBLEMS[hid] = "the shipped file does not match its manifest hash; the head stays off"
                else:
                    cand = json.loads(text)
                    bad = validate(cand)
                    if cand.get("id") != hid:
                        bad.append("the file names head %r" % cand.get("id"))
                    if cand.get("version") != row.get("version"):
                        bad.append("the file's version differs from the manifest's")
                    if bad:
                        _PROBLEMS[hid] = "; ".join(bad)
                    else:
                        spec = cand
                        _SHIPPED_SHA[hid] = _sha(text)
            except Exception as e:  # noqa: BLE001
                _PROBLEMS[hid] = "the shipped file could not be read (%s)" % type(e).__name__
                _PROBLEMS[hid + "#read_error"] = "1"
        _SHIPPED[hid] = spec
        return spec


def shipped_sha(hid: str) -> str:
    shipped(hid)
    return _SHIPPED_SHA.get(hid, "")


# --------------------------------------------------------------------------
#  overrides: where, signing, the stricter-or-bolder test
# --------------------------------------------------------------------------

def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "decision_heads"


def _active_path(hid: str) -> Path:
    return _home() / "active" / ("%s.json" % hid)


def _sig_path(hid: str) -> Path:
    return _home() / "active" / ("%s.sig.json" % hid)


def _history_path() -> Path:
    return _home() / "history.jsonl"


def _key() -> bytes:
    """The governance key. Raises when there is none; callers fail closed. A
    practice-task trial child never signs or reads head overrides."""
    if os.environ.get("FRIDAY_TRIAL") == "1":
        raise RuntimeError("decision-head overrides are not used inside a practice-task trial")
    from agent_friday.governance.action_gate import _governance_key
    return _governance_key()


def _canon(values: dict) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def sign(hid: str, values: dict, card: str, base_sha256: str, key: bytes) -> str:
    body = json.dumps({"id": hid, "values": _canon(values), "card": str(card or ""),
                       "base_sha256": str(base_sha256 or "")}, sort_keys=True).encode("utf-8")
    return _hmac.new(key, body, hashlib.sha256).hexdigest()


def _mode_rank(m) -> int:
    return MODES.index(m) if m in MODES else -1


def bolder_parts(current: dict, values: dict) -> List[str]:
    """What in ``values`` is bolder than ``current`` (a lower threshold, a higher
    mode). Empty when the change is only stricter or the same."""
    out = []
    for name, v in (values.get("thresholds") or {}).items():
        cur = (current.get("thresholds") or {}).get(name)
        nv = _num(v)
        if cur is None or nv is None:
            out.append("%s is not a threshold this head ships" % name)
        elif nv < float(cur):
            out.append("%s %.2f is below %.2f" % (name, nv, float(cur)))
    if "mode" in values and _mode_rank(values["mode"]) > _mode_rank(current.get("mode")):
        out.append("mode %s is beyond %s" % (values["mode"], current.get("mode")))
    return out


def _check_values(hid: str, values: dict, shipped_names) -> List[str]:
    bad = []
    if not isinstance(values, dict):
        return ["a change is a JSON object"]
    if set(values) - {"thresholds", "mode"}:
        bad.append("a change may set thresholds and mode, nothing else")
    th = values.get("thresholds") or {}
    if not isinstance(th, dict) or set(th) - set(shipped_names):
        bad.append("a change may only set thresholds this head ships (%s)" % ", ".join(sorted(shipped_names)))
    else:
        bad += _threshold_problems(th)
    if "mode" in values and values["mode"] not in MODES:
        bad.append("mode must be one of %s" % ", ".join(MODES))
    return bad


# --------------------------------------------------------------------------
#  the owner's card
# --------------------------------------------------------------------------

def card_payload(hid: str, *, change: Optional[dict] = None, undo: bool = False) -> dict:
    """The exact payload a ``laya_threshold`` card must carry to make head ``hid``
    bolder: the full resulting values (``change``) or ``undo``, bound to the
    shipped file in force. The owner's Settings page builds the card from this;
    no Laya module may (A4 guard)."""
    p = {"decision_head": hid, "origin": CARD_ORIGIN, "base_sha256": shipped_sha(hid)}
    if undo:
        p["undo"] = True
    else:
        p["change"] = change or {}
    return p


def card_text(hid: str, *, change: Optional[dict] = None, undo: bool = False) -> Tuple[str, str]:
    """(title, description) the card must show, so what Stephen reads is what he approves."""
    if undo:
        return ("Put Laya's %s check back to how it shipped" % hid,
                "This drops what Laya learned for this check. It makes her act on less certainty than now.")
    th = ", ".join("%s %.2f" % (k, float(v)) for k, v in sorted(((change or {}).get("thresholds") or {}).items()))
    return ("Let Laya's %s check be bolder" % hid,
            "Mode: %s. Thresholds: %s." % ((change or {}).get("mode", "unchanged"), th or "unchanged"))


def _card_used(card_id: str) -> bool:
    return any(r.get("card") == card_id for r in history())


def card_valid(card_id, hid: str, *, change: Optional[dict] = None, undo: bool = False) -> bool:
    """True only for an APPROVED card of kind laya_threshold, decided by the
    owner's press on screen, whose payload is exactly ``card_payload(...)`` for
    this head and change against the shipped file in force, and whose title is
    the one ``card_text`` renders. Anything else is False."""
    try:
        if not card_id or not isinstance(card_id, str):
            return False
        from agent_friday.services import approvals
        rec = approvals.get_approval(card_id)
        if not rec or rec.get("status") != "approved" or rec.get("kind") != APPROVAL_KIND:
            return False
        if rec.get("decided_by") != PRESS:
            return False
        want = card_payload(hid, change=change, undo=undo)
        if _canon(rec.get("payload") or {}) != _canon(want):
            return False
        return rec.get("title") == card_text(hid, change=change, undo=undo)[0]
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
#  what is in force
# --------------------------------------------------------------------------

def _shipped_view(spec: dict) -> dict:
    return {"thresholds": {k: float(v) for k, v in spec["thresholds"].items()}, "mode": spec["mode_default"]}


def _load_override(hid: str, base: dict) -> Optional[dict]:
    """The verified override values for ``hid``, or None (with a health row)."""
    path, sig = _active_path(hid), _sig_path(hid)
    if not path.exists():
        _PROBLEMS.pop(hid, None)
        _PAUSED.pop(hid, None)
        return None
    _PAUSED.pop(hid, None)
    try:
        values = json.loads(_read_text(path))
        meta = json.loads(_read_text(sig)) if sig.exists() else None
    except Exception as e:  # noqa: BLE001
        _PROBLEMS[hid] = "ignored the override: it could not be read (%s)" % type(e).__name__
        return None
    if not isinstance(values, dict) or not isinstance(meta, dict) or meta.get("id") != hid \
            or not isinstance(meta.get("mac"), str):
        _PROBLEMS[hid] = "ignored the override: it has no valid signature record"
        return None
    try:
        key = _key()
    except Exception:  # noqa: BLE001
        _PROBLEMS[hid] = "ignored the override: the signing key is unavailable"
        return None
    if not _hmac.compare_digest(sign(hid, values, meta.get("card", ""), meta.get("base_sha256", ""), key),
                                meta["mac"]):
        _PROBLEMS[hid] = "ignored the override: its signature does not match (edited outside Friday?)"
        return None
    if meta.get("base_sha256") != shipped_sha(hid):
        _PAUSED[hid] = True
        _PROBLEMS[hid] = ("an update changed this head after the change was made, so the change is paused "
                          "and the shipped values are used")
        return None
    bad = _check_values(hid, values, base["thresholds"].keys())
    if bad:
        _PROBLEMS[hid] = "ignored the override: " + "; ".join(bad)
        return None
    if bolder_parts(base, values) and not card_valid(meta.get("card"), hid, change=values):
        _PROBLEMS[hid] = "ignored the override: it is bolder than shipped and its approval card no longer checks out"
        return None
    _PROBLEMS.pop(hid, None)
    return values


def _tag(spec: dict, over: Optional[dict]) -> str:
    t = "v%d-%s" % (spec["version"], _SHIPPED_SHA.get(spec["id"], "")[:12])
    if over:
        t += "+o" + _sha(_canon(over))[:12]
    return t


def effective(hid: str) -> Optional[dict]:
    """{mode, thresholds, version, source, tag, ...} in force for ``hid``, or None
    when the head does not exist or its shipped file was refused (the head is
    off). Reads the override and checks its card every time: a caller on a hot
    path keeps the result and refreshes it (DM3 consumers use a short TTL), and
    stamps its receipt with ``tag`` from the result it actually used."""
    spec = shipped(hid)
    if spec is None:
        return None
    base = _shipped_view(spec)
    try:
        over = _load_override(hid, base)
    except Exception as e:  # noqa: BLE001
        _PROBLEMS[hid] = "override check failed (%s); using the shipped values" % type(e).__name__
        over = None
    out = {"id": hid, "mode": base["mode"], "thresholds": dict(base["thresholds"]),
           "version": spec["version"], "source": "shipped", "direction": DIRECTIONS[hid],
           "scorer": spec["scorer"], "budget_ms": spec["budget_ms"], "preset": spec.get("preset", hid),
           "tag": _tag(spec, over)}
    if over:
        out["thresholds"].update({k: float(v) for k, v in (over.get("thresholds") or {}).items()})
        if "mode" in over:
            out["mode"] = over["mode"]
        out["source"] = "override"
    with _LOCK:
        _EFFECTIVE[hid] = out
    return dict(out)


def warm() -> None:
    """Read and check every shipped head and override once (off the turn). A
    head whose file could not be read is retried once (an antivirus lock, say)."""
    for hid in head_ids():
        effective(hid)
        if _PROBLEMS.pop(hid + "#read_error", None):
            with _LOCK:
                _SHIPPED.pop(hid, None)
            time.sleep(0.2)
            effective(hid)
            _PROBLEMS.pop(hid + "#read_error", None)


def is_warm() -> bool:
    return _MANIFEST is not None


def versions() -> Dict[str, str]:
    """{head: tag} as last loaded, e.g. "v1-<sha12>" or "v1-<sha12>+o<values12>".
    Takes no lock and reads no file (laya.versions calls it on every receipt);
    a cold module answers {}. A consumer stamps its own receipt with the tag of
    the ``effective()`` result it used, which is exact."""
    m = _MANIFEST
    if m is None:
        return {}
    out = {}
    for r in list(m.get("heads", [])):
        eff = _EFFECTIVE.get(str(r.get("id")))
        if eff:
            out[eff["id"]] = eff["tag"]
    return out


def problems() -> Dict[str, str]:
    return {k: v for k, v in _PROBLEMS.items() if not k.endswith("#read_error")}


# --------------------------------------------------------------------------
#  changes (every one under the lock, compared against what it was built on)
# --------------------------------------------------------------------------

def _append_history(row: dict) -> None:
    p = _history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def history(hid: Optional[str] = None) -> List[dict]:
    rows = []
    try:
        for line in _history_path().read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if hid is None or r.get("id") == hid:
                rows.append(r)
    except OSError:
        pass
    return rows


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%s.tmp" % (path.name, uuid.uuid4().hex[:8]))
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _view(e: dict) -> dict:
    return {"mode": e["mode"], "thresholds": dict(e["thresholds"])}


def _write(hid: str, values: dict, *, actor: str, card: str, reason: str, built_on: dict) -> dict:
    """Write ``values`` as the override, only if what is in force is still what
    the change was built on (compare and swap, under the lock)."""
    try:
        key = _key()
    except Exception:  # noqa: BLE001
        raise HeadChangeRefused("the governance key is unavailable, so the change cannot be signed")
    with _LOCK:
        before = effective(hid)
        if before is None or _canon(_view(before)) != _canon(_view(built_on)):
            raise HeadChangeRefused("the head changed while this change was being made; nothing was written")
        base_sha = shipped_sha(hid)
        _write_atomic(_active_path(hid), _canon(values).encode("utf-8"))
        meta = {"id": hid, "card": str(card or ""), "actor": actor, "at": time.time(), "base_sha256": base_sha,
                "mac": sign(hid, values, card, base_sha, key)}
        _write_atomic(_sig_path(hid), json.dumps(meta, sort_keys=True).encode("utf-8"))
        after = effective(hid)
        row = {"at": meta["at"], "id": hid, "action": "write", "actor": actor, "card": str(card or ""),
               "reason": str(reason or "")[:300], "before": _view(before), "after": _view(after),
               "before_tag": before["tag"], "after_tag": after["tag"], "base_sha256": base_sha}
        _append_history(row)
        return row


def _merged(hid: str, change: dict) -> Tuple[dict, dict]:
    """(current in force, the full override values the change produces)."""
    cur = effective(hid)
    if cur is None:
        raise HeadChangeRefused("there is no usable decision head called %r" % hid)
    bad = _check_values(hid, change, cur["thresholds"].keys())
    if bad:
        raise HeadChangeRefused("; ".join(bad))
    values = {"thresholds": dict(cur["thresholds"]), "mode": cur["mode"]}
    values["thresholds"].update({k: float(v) for k, v in (change.get("thresholds") or {}).items()})
    if "mode" in change:
        values["mode"] = change["mode"]
    bad = _threshold_problems(values["thresholds"])
    if bad:
        raise HeadChangeRefused("; ".join(bad))
    return cur, values


def tighten(hid: str, change: dict, *, actor: str, reason: str = "") -> dict:
    """Make a head stricter: raise thresholds, lower the mode. Used by learning.
    Refused (nothing written) if any part of ``change`` is bolder, or while the
    owner's own bolder setting is in force."""
    if not actor:
        raise HeadChangeRefused("a change must name who made it")
    with _LOCK:
        cur, values = _merged(hid, change)
        if cur["source"] == "override" and bolder_parts(_shipped_view(shipped(hid)), _view(cur)):
            # The owner pressed for a bolder head; that choice stands until the owner
            # changes it. Learning does not rewrite it (and could not re-sign the card).
            raise HeadChangeRefused("the owner's own setting for this head is in force; learning leaves it alone")
        bold = bolder_parts(_view(cur), values)
        if bold:
            raise HeadChangeRefused("only the owner's press on an approval card may make a head bolder (%s)"
                                    % "; ".join(bold))
        if _canon(values) == _canon(_view(cur)):
            raise HeadChangeRefused("nothing would change")
        return _write(hid, values, actor=actor, card="", reason=reason, built_on=cur)


def apply_owner_change(hid: str, change: dict, *, card_id: str, reason: str = "") -> dict:
    """A change Stephen approved with a press on a ``laya_threshold`` card whose
    payload is ``card_payload(hid, change=<the full resulting values>)``. One
    card, one application: a card already in the history is refused."""
    with _LOCK:
        cur, values = _merged(hid, change)
        if _card_used(str(card_id)):
            raise HeadChangeRefused("this approval card has already been used; a new change needs a new card")
        if not card_valid(card_id, hid, change=values):
            raise HeadChangeRefused("this change needs an approved card, pressed on screen, for exactly this head, "
                                    "these values and the version of the head that ships now")
        return _write(hid, values, actor="card:%s" % card_id, card=card_id, reason=reason, built_on=cur)


def undo(hid: str, *, actor: str, card_id: str = "") -> dict:
    """Back to the shipped values. An owner request; when shipped would be
    bolder than what is in force (it drops learning's tightening), it also
    needs Stephen's press on a card from ``card_payload(hid, undo=True)``."""
    if not str(actor or "").startswith("owner"):
        raise HeadChangeRefused("only the owner may put a head back to how it shipped")
    with _LOCK:
        before = effective(hid)
        if before is None:
            raise HeadChangeRefused("there is no usable decision head called %r" % hid)
        if not _active_path(hid).exists():
            raise HeadChangeRefused("this head already uses its shipped values")
        if bolder_parts(_view(before), _shipped_view(shipped(hid))):
            if _card_used(str(card_id)) or not card_valid(card_id, hid, undo=True):
                raise HeadChangeRefused("going back to shipped would make this head bolder, so it needs your "
                                        "press on its approval card")
        for p in (_active_path(hid), _sig_path(hid)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        after = effective(hid)
        row = {"at": time.time(), "id": hid, "action": "undo", "actor": actor, "card": str(card_id or ""),
               "reason": "back to the shipped values", "before": _view(before), "after": _view(after),
               "before_tag": before["tag"], "after_tag": after["tag"]}
        _append_history(row)
        return row


def status() -> List[dict]:
    """One row per head, for Settings > Laya and the API. Read-only."""
    rows = []
    for hid in head_ids():
        eff = effective(hid)
        rows.append({"id": hid, "direction": DIRECTIONS.get(hid), "usable": eff is not None,
                     "mode": (eff or {}).get("mode", "off"), "thresholds": (eff or {}).get("thresholds"),
                     "source": (eff or {}).get("source"), "tag": (eff or {}).get("tag"),
                     "paused": bool(_PAUSED.get(hid)), "problem": problems().get(hid, ""),
                     "changes": len(history(hid))})
    return rows


def _reset_caches_for_tests() -> None:
    global _MANIFEST
    with _LOCK:
        _MANIFEST = None
        _SHIPPED.clear()
        _SHIPPED_SHA.clear()
        _PROBLEMS.clear()
        _PAUSED.clear()
        _EFFECTIVE.clear()
