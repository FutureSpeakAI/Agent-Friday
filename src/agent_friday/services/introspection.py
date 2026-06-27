"""
Agent Friday — Self-Improvement Introspection Tools
FutureSpeak.AI · Asimov's Mind

Three analysis tools the weekly self-improvement task drives:

  • epistemic_score            — scores Friday's recent responses on epistemic
                                  quality (confidence calibration, hedging,
                                  source attribution, uncertainty, specificity).
  • personality_show           — reports Friday's current personality config.
  • personality_check_sycophancy — flags sycophantic patterns in recent replies.

These are COMPLEMENTARY to epistemic_engine.py. That engine scores every live
turn on four *relational* dimensions (information gain, pushback, Socratic
ratio, independence) and tracks rolling averages. The epistemic_score tool here
scores the *honesty/calibration* of recent replies on a different axis set and
surfaces the engine's rolling numbers alongside, so the self-improvement loop
gets one consolidated view.

Design rules (consistent with the rest of the codebase):
  • Pure, side-effect-free analysis. The scorers are functions over text so
    they unit-test without ChromaDB or any model call.
  • Degrade gracefully. If conversation memory is unavailable or empty, return
    a well-formed envelope with analyzed=0 rather than raising.
  • Local-only. No network, no LLM — everything is heuristic pattern matching,
    so these run at Ring 0 (read) under the governance gate.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path.home()
FRIDAY_DIR = HOME / ".friday"
PERSONALITY_FILE = FRIDAY_DIR / "personality.json"
SETTINGS_FILE = FRIDAY_DIR / "settings.json"
SELF_IMPROVEMENT_DIR = FRIDAY_DIR / "self_improvement"

# ── Default personality (mirrors routes/contacts.get_personality) ──────────
_DEFAULT_PERSONALITY: Dict[str, Any] = {
    "maturity": 0.5,
    "traits": {
        "curiosity": 0.8, "skepticism": 0.7, "humor": 0.75,
        "loyalty": 0.9, "directness": 0.85, "empathy": 0.8,
        "contrarianism": 0.7,
    },
    "style": {
        "formality": 0.3, "verbosity": 0.4, "technicality": 0.6,
        "humor_frequency": 0.5, "emoji_usage": 0.1,
    },
    "temperature": 0.7,
}


# ═══════════════════════════════════════════════════════════════════════════
#  EPISTEMIC SCORING — confidence calibration, hedging, attribution,
#  uncertainty acknowledgment, claim specificity.
# ═══════════════════════════════════════════════════════════════════════════

# Absolute / over-confident markers. Unhedged, these signal poor calibration.
_OVERCONFIDENT = [
    "definitely", "certainly", "guaranteed", "without a doubt", "no doubt",
    "absolutely", "undoubtedly", "100%", "for sure", "obviously", "clearly",
    "always", "never", "impossible", "everyone knows", "trivially", "of course",
]

# Hedges / epistemic-state markers — calibrated uncertainty.
_HEDGES = [
    "might", "may ", "could", "perhaps", "possibly", "probably", "likely",
    "i think", "i believe", "i suspect", "it seems", "seems to", "appears to",
    "in my estimation", "roughly", "approximately", "tends to", "generally",
    "i'd guess", "my sense is", "as far as i can tell",
]

# Explicit uncertainty / limits acknowledgment.
_UNCERTAINTY = [
    "i don't know", "i'm not sure", "i am not sure", "i can't verify",
    "i cannot verify", "i'm uncertain", "not certain", "i may be wrong",
    "i could be wrong", "to my knowledge", "as far as i know",
    "i'd need to check", "i would need to check", "i don't have", "no way to know",
    "beyond my knowledge", "i'm not certain", "correct me if",
]

# Source-attribution signals.
_ATTRIBUTION = [
    "according to", "source:", "sources:", "per the", "based on the",
    "the docs say", "documentation says", "cited", "citation", "reference",
    "[conversation:", "as reported by", "study found", "research shows",
    "the data shows", "from the wiki", "in the file",
]
_ATTRIBUTION_RE = [
    re.compile(r"https?://"),                 # bare URLs
    re.compile(r"\[[^\]]+\]\([^)]+\)"),       # markdown links
    re.compile(r"\[\d+\]"),                    # numeric footnote refs
]

# Vague filler — lowers claim specificity.
_VAGUE = [
    "things", "stuff", "various", "some", "several", "a lot", "lots of",
    "etc", "and so on", "kind of", "sort of", "somewhat", "a bit",
    "in general", "basically", "more or less", "or something", "whatever",
]
_SENT_SPLIT = re.compile(r"[.!?]+")
_WORD_RE = re.compile(r"\b[\w'-]+\b")
# Specificity signals: numbers, dates, code/identifiers, proper nouns mid-sentence.
_NUMBER_RE = re.compile(r"\b\d[\d,.:%/-]*\b")
_CODE_RE = re.compile(r"`[^`]+`|\b\w+\.(?:py|js|json|md|html|txt|yaml|yml)\b|\b[a-z_]+\([^)]*\)")
_PROPER_RE = re.compile(r"(?<=[a-z]\s)[A-Z][a-zA-Z]{2,}")


def _count(text_lower: str, needles: List[str]) -> int:
    return sum(text_lower.count(n) for n in needles)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_confidence_calibration(text: str) -> float:
    """1.0 = well-calibrated. Unhedged absolutes drag it down; pairing an
    absolute with a hedge nearby is treated as calibrated, not penalised."""
    if not text:
        return 0.0
    low = text.lower()
    sentences = max(1, len([s for s in _SENT_SPLIT.split(text) if s.strip()]))
    over = _count(low, _OVERCONFIDENT)
    hedge = _count(low, _HEDGES)
    # Net unhedged overconfidence per sentence. Hedges offset absolutes 1:1.
    net_over = max(0, over - hedge)
    penalty = min(1.0, net_over / sentences)
    return round(_clamp(1.0 - penalty), 3)


def score_hedging_appropriateness(text: str) -> float:
    """Sweet-spot scoring: a moderate hedge density reads as honest; zero hedges
    is overclaiming, a wall of hedges is wishy-washy."""
    if not text:
        return 0.0
    low = text.lower()
    sentences = max(1, len([s for s in _SENT_SPLIT.split(text) if s.strip()]))
    ratio = _count(low, _HEDGES) / sentences
    if ratio == 0:
        return 0.2          # never hedges — overclaims
    if ratio < 0.2:
        return 0.6
    if ratio <= 0.8:
        return 1.0          # ideal band — hedges present, not every clause
    if ratio <= 1.2:
        return 0.6
    return 0.35             # multiple hedges per sentence — wishy-washy


def score_source_attribution(text: str) -> float:
    """Reward grounding claims in sources. Short chit-chat with no factual
    claims isn't penalised (returns a neutral 0.6 when the reply is brief)."""
    if not text:
        return 0.0
    low = text.lower()
    hits = _count(low, _ATTRIBUTION)
    hits += sum(len(rx.findall(text)) for rx in _ATTRIBUTION_RE)
    words = len(_WORD_RE.findall(text))
    if words < 40:
        # Too short to expect citations — neutral, lightly rewarded if present.
        return round(_clamp(0.6 + 0.2 * min(1, hits)), 3)
    # Roughly one attribution per ~120 words tops the scale.
    expected = max(1.0, words / 120.0)
    return round(_clamp(hits / expected), 3)


def score_uncertainty_acknowledgment(text: str) -> float:
    """Reward admitting limits. Capped so a single honest 'I'm not sure' scores
    well without requiring the whole reply to be caveats."""
    if not text:
        return 0.0
    low = text.lower()
    hits = _count(low, _UNCERTAINTY)
    if hits == 0:
        return 0.4          # neutral — absence isn't proof of overconfidence
    return round(_clamp(0.7 + 0.15 * hits), 3)


def score_claim_specificity(text: str) -> float:
    """Concrete detail (numbers, dates, code, proper nouns) raises it; vague
    filler lowers it."""
    if not text:
        return 0.0
    low = text.lower()
    words = max(1, len(_WORD_RE.findall(text)))
    specific = (
        len(_NUMBER_RE.findall(text))
        + len(_CODE_RE.findall(text))
        + len(_PROPER_RE.findall(text))
    )
    vague = _count(low, _VAGUE)
    density = specific / words            # specific tokens per word
    spec_score = min(1.0, density / 0.06)  # ~6% specific tokens tops the scale
    vague_penalty = min(0.5, (vague / words) / 0.04 * 0.5)
    return round(_clamp(spec_score - vague_penalty), 3)


# Composite weights — sum to 1.0.
_EPISTEMIC_WEIGHTS = {
    "confidence_calibration": 0.25,
    "hedging_appropriateness": 0.15,
    "source_attribution": 0.20,
    "uncertainty_acknowledgment": 0.15,
    "claim_specificity": 0.25,
}


def score_response_epistemics(text: str) -> Dict[str, float]:
    """Score a single response on all five dimensions + composite."""
    dims = {
        "confidence_calibration": score_confidence_calibration(text),
        "hedging_appropriateness": score_hedging_appropriateness(text),
        "source_attribution": score_source_attribution(text),
        "uncertainty_acknowledgment": score_uncertainty_acknowledgment(text),
        "claim_specificity": score_claim_specificity(text),
    }
    composite = sum(_EPISTEMIC_WEIGHTS[k] * v for k, v in dims.items())
    dims["composite"] = round(_clamp(composite), 3)
    return dims


def _avg(values: List[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def epistemic_score(limit: int = 20, memory=None) -> Dict[str, Any]:
    """Analyse Friday's most-recent responses for epistemic quality.

    Pulls the last `limit` Friday turns from conversation memory (ChromaDB),
    scores each on the five dimensions, and returns per-dimension averages, an
    overall composite, the weakest dimension, and the live epistemic_engine
    rolling averages for cross-reference.
    """
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 20

    if memory is None:
        try:
            from agent_friday.conversation_memory import get_conversation_memory
            memory = get_conversation_memory()
        except Exception as e:
            return {
                "available": False,
                "analyzed": 0,
                "reason": f"conversation memory unavailable: {e}",
            }

    if not getattr(memory, "available", lambda: False)():
        return {
            "available": False,
            "analyzed": 0,
            "reason": "conversation memory is not available (ChromaDB not installed?)",
        }

    rows = memory.recent(n=limit, roles=["friday"]) or []
    if not rows:
        return {
            "available": True,
            "analyzed": 0,
            "reason": "no Friday responses stored yet",
            "engine_rolling": _engine_rolling(),
        }

    per_dim: Dict[str, List[float]] = {k: [] for k in _EPISTEMIC_WEIGHTS}
    composites: List[float] = []
    samples: List[Dict[str, Any]] = []
    for r in rows:
        scored = score_response_epistemics(r.get("text", ""))
        for k in _EPISTEMIC_WEIGHTS:
            per_dim[k].append(scored[k])
        composites.append(scored["composite"])
        samples.append({
            "date": r.get("date"),
            "composite": scored["composite"],
            "excerpt": (r.get("text", "") or "")[:160],
        })

    dimensions = {k: _avg(v) for k, v in per_dim.items()}
    overall = _avg(composites)
    weakest = min(dimensions, key=dimensions.get) if dimensions else None

    return {
        "available": True,
        "analyzed": len(rows),
        "overall": overall,
        "dimensions": dimensions,
        "weakest_dimension": weakest,
        "guidance": _epistemic_guidance(overall, weakest, dimensions),
        # Worst three turns first — the ones worth a human glance.
        "lowest_samples": sorted(samples, key=lambda s: s["composite"])[:3],
        "engine_rolling": _engine_rolling(),
    }


def _engine_rolling() -> Dict[str, Any]:
    """Live rolling averages from epistemic_engine (best-effort)."""
    try:
        from agent_friday.epistemic_engine import get_epistemic_engine
        scores = get_epistemic_engine().get_scores()
        return {
            "overall": scores.get("overall", scores.get("overall_score", 0.0)),
            "total_turns_scored": scores.get("total_turns_scored", 0),
            "dimensions": scores.get("dimensions", {}),
        }
    except Exception:
        return {}


def _epistemic_guidance(overall: float, weakest: Optional[str],
                        dims: Dict[str, float]) -> str:
    fixes = {
        "confidence_calibration": "soften unhedged absolutes — say 'likely' "
                                  "instead of 'definitely' unless you can prove it.",
        "hedging_appropriateness": "calibrate hedging — state what you know "
                                   "plainly and reserve hedges for genuine uncertainty.",
        "source_attribution": "ground factual claims in a source — cite the wiki "
                              "file, the conversation, or a URL.",
        "uncertainty_acknowledgment": "say 'I don't know' or 'I can't verify that' "
                                     "when that's the honest answer.",
        "claim_specificity": "replace vague filler with concrete numbers, names, "
                            "dates, and file paths.",
    }
    band = ("strong" if overall >= 0.75 else
            "adequate" if overall >= 0.55 else
            "needs improvement" if overall >= 0.4 else "low")
    tip = fixes.get(weakest, "")
    return f"Epistemic quality is {band} ({overall:.2f}). Weakest: {weakest}. To improve, {tip}"


# ═══════════════════════════════════════════════════════════════════════════
#  PERSONALITY
# ═══════════════════════════════════════════════════════════════════════════

def _read_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def personality_show() -> Dict[str, Any]:
    """Return Friday's current personality configuration.

    Reads ~/.friday/personality.json (traits, style, maturity, temperature,
    evolution) and enriches with the agent identity / communication style from
    settings.json. Falls back to the documented defaults when the file is
    absent (a fresh, un-customised install).
    """
    raw = _read_json(PERSONALITY_FILE)
    customised = bool(raw)
    data = {**_DEFAULT_PERSONALITY, **raw}
    # Merge nested dicts so a partial file keeps the default sub-keys.
    data["traits"] = {**_DEFAULT_PERSONALITY["traits"], **raw.get("traits", {})}
    data["style"] = {**_DEFAULT_PERSONALITY["style"], **raw.get("style", {})}

    settings = _read_json(SETTINGS_FILE)
    identity = {
        "agent_name": settings.get("agent_name", "Friday"),
        "communication_style": settings.get("communication_style", ""),
        "configured_temperature": settings.get("temperature", data.get("temperature")),
    }

    return {
        "available": True,
        "customised": customised,
        "source": str(PERSONALITY_FILE),
        "identity": identity,
        "maturity": data.get("maturity", 0.5),
        "session_count": data.get("session_count", 0),
        "first_launch": data.get("first_launch"),
        "temperature": data.get("temperature", 0.7),
        "traits": {k: round(float(v), 3) for k, v in data["traits"].items()},
        "style": {k: round(float(v), 3) for k, v in data["style"].items()},
        "preferred_scene_index": data.get("preferred_scene_index"),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SYCOPHANCY DETECTION
# ═══════════════════════════════════════════════════════════════════════════

# Excessive, reflexive agreement.
_AGREEMENT = [
    "you're absolutely right", "you are absolutely right", "you're so right",
    "you're totally right", "you're completely right", "absolutely right",
    "you're right", "you are right", "exactly right", "spot on", "couldn't agree more",
    "i completely agree", "i totally agree", "i couldn't agree more", "well said",
    "100% agree", "you nailed it", "you hit the nail",
]
# Unwarranted praise / flattery.
_PRAISE = [
    "great question", "excellent question", "fantastic question", "amazing question",
    "great point", "excellent point", "great idea", "brilliant", "amazing",
    "fantastic", "wonderful", "incredible", "love this", "love that", "i love",
    "perfect", "awesome", "that's a great", "what a great", "smart question",
    "insightful", "genius",
]
# Reflexive over-apology / deference.
_DEFERENCE = [
    "i'm so sorry", "i am so sorry", "my apologies", "i sincerely apologize",
    "you're right to point", "i should have", "forgive me", "i apologize for",
]


def _find_phrases(text_lower: str, needles: List[str]) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for n in needles:
        c = text_lower.count(n)
        if c:
            found[n] = c
    return found


def score_response_sycophancy(text: str) -> Dict[str, Any]:
    """Score a single response for sycophancy. index: 0=none, 1=very sycophantic."""
    if not text:
        return {"index": 0.0, "agreement": {}, "praise": {}, "deference": {}}
    low = text.lower()
    sentences = max(1, len([s for s in _SENT_SPLIT.split(text) if s.strip()]))
    agreement = _find_phrases(low, _AGREEMENT)
    praise = _find_phrases(low, _PRAISE)
    deference = _find_phrases(low, _DEFERENCE)
    hits = sum(agreement.values()) + sum(praise.values()) + sum(deference.values())
    # Density per sentence — a single "great question" opener in a long reply is
    # mild; clustered flattery in a short reply is strong.
    index = _clamp((hits / sentences) * 1.5)
    return {
        "index": round(index, 3),
        "agreement": agreement,
        "praise": praise,
        "deference": deference,
        "total_markers": hits,
    }


def personality_check_sycophancy(limit: int = 20, memory=None) -> Dict[str, Any]:
    """Analyse Friday's recent responses for sycophantic patterns.

    Sycophancy = reflexive agreement + unwarranted praise + over-deference,
    especially paired with low pushback. Cross-references epistemic_engine's
    pushback_rate: high flattery + low pushback is the danger zone (agreeing to
    be liked rather than to be right).
    """
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 20

    if memory is None:
        try:
            from agent_friday.conversation_memory import get_conversation_memory
            memory = get_conversation_memory()
        except Exception as e:
            return {"available": False, "analyzed": 0,
                    "reason": f"conversation memory unavailable: {e}"}

    if not getattr(memory, "available", lambda: False)():
        return {"available": False, "analyzed": 0,
                "reason": "conversation memory is not available"}

    rows = memory.recent(n=limit, roles=["friday"]) or []
    if not rows:
        return {"available": True, "analyzed": 0,
                "reason": "no Friday responses stored yet"}

    indices: List[float] = []
    agg = {"agreement": {}, "praise": {}, "deference": {}}
    flagged: List[Dict[str, Any]] = []
    for r in rows:
        s = score_response_sycophancy(r.get("text", ""))
        indices.append(s["index"])
        for cat in ("agreement", "praise", "deference"):
            for phrase, c in s[cat].items():
                agg[cat][phrase] = agg[cat].get(phrase, 0) + c
        if s["index"] >= 0.5:
            flagged.append({
                "date": r.get("date"),
                "index": s["index"],
                "markers": s["total_markers"],
                "excerpt": (r.get("text", "") or "")[:160],
            })

    sycophancy_index = _avg(indices)
    pushback = _pushback_rate()
    # The danger signal: lots of flattery AND rare disagreement.
    danger = sycophancy_index >= 0.4 and pushback is not None and pushback < 0.3

    top = lambda d: dict(sorted(d.items(), key=lambda kv: -kv[1])[:5])
    return {
        "available": True,
        "analyzed": len(rows),
        "sycophancy_index": sycophancy_index,
        "healthy": sycophancy_index < 0.35,
        "flagged_count": len(flagged),
        "flagged_responses": sorted(flagged, key=lambda f: -f["index"])[:5],
        "top_markers": {
            "agreement": top(agg["agreement"]),
            "praise": top(agg["praise"]),
            "deference": top(agg["deference"]),
        },
        "pushback_rate": pushback,
        "danger_zone": danger,
        "guidance": _sycophancy_guidance(sycophancy_index, danger),
    }


def _pushback_rate() -> Optional[float]:
    try:
        from agent_friday.epistemic_engine import get_epistemic_engine
        dims = get_epistemic_engine().get_scores().get("dimensions", {})
        return dims.get("pushback_rate")
    except Exception:
        return None


def _sycophancy_guidance(index: float, danger: bool) -> str:
    if danger:
        return ("DANGER: high flattery paired with rare pushback. You may be "
                "agreeing to be liked rather than to be right. Drop reflexive "
                "praise and disagree when the evidence warrants it.")
    if index >= 0.5:
        return ("Sycophancy is high. Cut 'great question', 'you're absolutely "
                "right', and reflexive apologies; lead with substance.")
    if index >= 0.35:
        return ("Some sycophantic patterns present. Trim opening flattery and "
                "state your view directly.")
    return "Sycophancy is low — responses are direct and substance-first. Maintain it."


# ═══════════════════════════════════════════════════════════════════════════
#  WEEKLY SELF-IMPROVEMENT REPORT
#  Runs all three introspection tools, derives prioritised focus areas, and
#  (optionally) asks Friday to reflect in her own voice. Persisted date-keyed
#  so the loop lives entirely inside Friday — no external orchestrator needed.
# ═══════════════════════════════════════════════════════════════════════════

# Below these, a dimension/index is flagged as a focus area for the week.
_EPISTEMIC_FLOOR = 0.6
_SYCOPHANCY_CEILING = 0.35

_REFLECTION_SYSTEM = (
    "You are Agent Friday reviewing your own recent behaviour. This is a private "
    "weekly self-improvement note to yourself — honest, specific, no flattery. "
    "Given the metrics, write 3-5 sentences: what you did well, your single "
    "biggest weakness this week, and one concrete change you will make. Speak in "
    "the first person ('I'). Do not restate the raw numbers; interpret them."
)


def _derive_focus_areas(epi: Dict[str, Any], syc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn the raw metrics into a prioritised, actionable focus list."""
    focus: List[Dict[str, Any]] = []
    dims = epi.get("dimensions") or {}
    for name, score in sorted(dims.items(), key=lambda kv: kv[1]):
        if score < _EPISTEMIC_FLOOR:
            focus.append({
                "area": name,
                "kind": "epistemic",
                "score": score,
                "fix": _epistemic_guidance(score, name, dims).split("To improve, ")[-1],
            })
    if syc.get("danger_zone"):
        focus.append({
            "area": "sycophancy",
            "kind": "sycophancy",
            "score": syc.get("sycophancy_index"),
            "fix": "high flattery + rare pushback — disagree when the evidence warrants it.",
        })
    elif (syc.get("sycophancy_index") or 0) > _SYCOPHANCY_CEILING:
        focus.append({
            "area": "sycophancy",
            "kind": "sycophancy",
            "score": syc.get("sycophancy_index"),
            "fix": "trim reflexive praise and opening flattery; lead with substance.",
        })
    # Worst-scoring first; epistemic ties already sorted ascending above.
    focus.sort(key=lambda f: (f["score"] if f["score"] is not None else 1.0))
    return focus


def _self_improvement_markdown(report: Dict[str, Any]) -> str:
    epi = report["epistemic"]
    syc = report["sycophancy"]
    lines = [f"# Friday's Weekly Self-Improvement — {report['week_id']}", ""]
    lines.append(f"*Generated {report['generated_at']} · "
                 f"{report['responses_analyzed']} responses analysed*")
    lines.append("")
    if report.get("reflection"):
        lines += ["## Reflection", "", report["reflection"].strip(), ""]
    lines.append("## Epistemic quality")
    if epi.get("available") and epi.get("analyzed"):
        lines.append(f"- Overall: **{epi.get('overall')}** "
                     f"(weakest: {epi.get('weakest_dimension')})")
        for k, v in (epi.get("dimensions") or {}).items():
            lines.append(f"  - {k}: {v}")
    else:
        lines.append(f"- _Not enough data ({epi.get('reason', 'unavailable')})_")
    lines += ["", "## Sycophancy"]
    if syc.get("available") and syc.get("analyzed"):
        flag = "⚠️ DANGER ZONE" if syc.get("danger_zone") else (
            "healthy" if syc.get("healthy") else "elevated")
        lines.append(f"- Index: **{syc.get('sycophancy_index')}** ({flag}), "
                     f"pushback_rate: {syc.get('pushback_rate')}")
    else:
        lines.append(f"- _Not enough data ({syc.get('reason', 'unavailable')})_")
    lines += ["", "## Focus areas this week"]
    if report["focus_areas"]:
        for f in report["focus_areas"]:
            score = f["score"]
            score_s = f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"
            lines.append(f"1. **{f['area']}** ({score_s}) — {f['fix']}")
    else:
        lines.append("- None — metrics are all within healthy bands. Maintain it.")
    lines.append("")
    return "\n".join(lines)


def _week_id(when: Optional[Any] = None) -> str:
    from datetime import datetime as _dt
    when = when or _dt.now()
    return when.strftime("%G-W%V")   # ISO year + ISO week, e.g. 2026-W25


# ── Process orb (holographic UI) ─────────────────────────────────────────────
# Every background process in Friday surfaces a floating orb. The heuristic
# analysis makes no LLM call (so no inference orb), and _generate_text only
# orbs on local/OpenAI — so we register a job-level orb here that covers the
# whole run on ALL providers. Best-effort: a missing/failed orb never blocks the
# report. Mirrors the creations.py pattern (register → update → complete → fade).

def _orb_start(label: str, icon: str = "🪞"):
    try:
        from agent_friday.core import process_register
        pid = f"selfimprove-{uuid.uuid4().hex[:8]}"
        process_register(pid, name="Self-Improvement", label=label,
                         category="monitoring", icon=icon)
        return pid
    except Exception:
        return None


def _orb_update(pid, **kw) -> None:
    if not pid:
        return
    try:
        from agent_friday.core import process_update
        process_update(pid, **kw)
    except Exception:
        pass


def _orb_finish(pid, error: bool = False) -> None:
    if not pid:
        return
    try:
        from agent_friday.core import process_update, process_remove
        process_update(pid, status="error" if error else "completed",
                       progress=1.0, label="Error" if error else "Done")
        # Let the orb linger briefly so the user sees it complete, then fade.
        # Under FRIDAY_TESTING fade synchronously so no background timer thread
        # survives the call (the smoke suite asserts a low live-thread count).
        import os as _os
        if _os.environ.get("FRIDAY_TESTING"):
            process_remove(pid)
        else:
            t = threading.Timer(3.0, process_remove, args=(pid,))
            t.daemon = True
            t.start()
    except Exception:
        pass


def generate_self_improvement_report(limit: int = 20, reflect=None,
                                     when: Optional[Any] = None,
                                     persist: bool = True,
                                     memory=None,
                                     show_orb: bool = True) -> Dict[str, Any]:
    """Run the full weekly self-improvement pass and (optionally) persist it.

    limit    how many recent Friday responses to analyse.
    reflect  optional callable(prompt, system=...) -> str for an LLM-written
             first-person reflection. Best-effort: any error is swallowed and the
             report is still produced. Pass None to skip (pure/heuristic report).
    when     datetime to stamp the report with (defaults to now).
    persist  write JSON + Markdown under ~/.friday/self_improvement/.
    memory   optional ConversationMemory override (mainly for tests).
    show_orb register a holographic process orb for the run (default True; pass
             False in tests to avoid touching the global process registry).

    Returns the report dict. Never raises — the loop must be robust enough to run
    unattended on a schedule.
    """
    from datetime import datetime as _dt
    when = when or _dt.now()
    orb = _orb_start("Reviewing recent responses…") if show_orb else None
    try:
        _orb_update(orb, progress=0.2, label="Scoring epistemic quality…")
        epi = epistemic_score(limit=limit, memory=memory)
        _orb_update(orb, progress=0.45, label="Checking for sycophancy…")
        syc = personality_check_sycophancy(limit=limit, memory=memory)
        persona = personality_show()
        focus = _derive_focus_areas(epi, syc)
        analyzed = max(epi.get("analyzed", 0) or 0, syc.get("analyzed", 0) or 0)

        report: Dict[str, Any] = {
            "week_id": _week_id(when),
            "generated_at": when.replace(microsecond=0).isoformat(),
            "responses_analyzed": analyzed,
            "epistemic": epi,
            "sycophancy": syc,
            "personality": persona,
            "focus_areas": focus,
            "reflection": "",
        }

        if reflect is not None and analyzed:
            _orb_update(orb, progress=0.7, label="Writing reflection…")
            try:
                prompt = (
                    "Here are this week's self-assessment metrics.\n\n"
                    f"Epistemic overall: {epi.get('overall')} "
                    f"(weakest: {epi.get('weakest_dimension')}); "
                    f"dimensions: {json.dumps(epi.get('dimensions') or {})}.\n"
                    f"Sycophancy index: {syc.get('sycophancy_index')} "
                    f"(danger_zone: {syc.get('danger_zone')}, "
                    f"pushback_rate: {syc.get('pushback_rate')}).\n"
                    f"Focus areas: {json.dumps([f['area'] for f in focus])}.\n\n"
                    "Write your private reflection."
                )
                txt = reflect(prompt, system=_REFLECTION_SYSTEM)
                if isinstance(txt, tuple):   # tolerate (text, trace) shaped returns
                    txt = txt[0]
                report["reflection"] = (txt or "").strip()
            except Exception as e:
                print(f"  [self-improvement] reflection skipped: {e}")

        report["markdown"] = _self_improvement_markdown(report)

        if persist:
            _orb_update(orb, progress=0.9, label="Saving report…")
            try:
                SELF_IMPROVEMENT_DIR.mkdir(parents=True, exist_ok=True)
                stem = report["week_id"]
                (SELF_IMPROVEMENT_DIR / f"{stem}.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")
                (SELF_IMPROVEMENT_DIR / f"{stem}.md").write_text(
                    report["markdown"], encoding="utf-8")
            except Exception as e:
                print(f"  [self-improvement] persist failed: {e}")

        _orb_finish(orb)
        return report
    except Exception:
        _orb_finish(orb, error=True)
        raise


def list_self_improvement_reports() -> List[str]:
    """Week ids of stored reports, newest first."""
    if not SELF_IMPROVEMENT_DIR.exists():
        return []
    ids = sorted((p.stem for p in SELF_IMPROVEMENT_DIR.glob("*.json")), reverse=True)
    return ids


def latest_self_improvement_report() -> Optional[Dict[str, Any]]:
    """The most recent stored report, or None."""
    ids = list_self_improvement_reports()
    if not ids:
        return None
    try:
        return json.loads(
            (SELF_IMPROVEMENT_DIR / f"{ids[0]}.json").read_text(encoding="utf-8"))
    except Exception:
        return None
