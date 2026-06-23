"""
Agent Friday — Self-Evaluation Gates (QA)
FutureSpeak.AI · Asimov's Mind

Before presenting *significant* generated content, Friday runs a fast self-
critique: score the output against the stated intent, and if it falls below a
threshold either (a) silently improve it (regenerate with the critique folded
in) or (b) flag the gap to the user. Cheap insurance against confidently-wrong
or off-brief generations.

  • evaluate_text(content, intent)   → {score, passed, critique, suggestions}
  • evaluate_image(path/url, intent) → vision-model score (Gemini) when available
  • gate_text(generate_fn, intent)   → generate → evaluate → improve/flag loop

Everything is GATED by settings.qa_gates so users can trade quality for speed:
    {"enabled": true, "threshold": 0.7, "max_retries": 1,
     "mode": "improve", "vision_for_images": true}

Design rules: never raises (a failed gate must never break generation); fully
degrades when no model/key is available (returns "skipped", treated as a pass);
the text evaluator routes through _generate_text so it works on Anthropic,
OpenAI-compatible, OR local Ollama — same as every other non-chat generation.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Optional

from core import _load_settings

# Mirrors DEFAULT_SETTINGS["qa_gates"]; used when the key is absent/partial.
_QA_DEFAULTS = {
    "enabled": True,
    "threshold": 0.7,        # 0–1; below this the gate intervenes
    "max_retries": 1,        # silent-improve attempts before giving up
    "mode": "improve",       # "improve" (regenerate) | "flag" (surface the gap)
    "vision_for_images": True,
}


def qa_config() -> Dict[str, Any]:
    """Effective QA-gate config (settings overlaid on defaults)."""
    cfg = dict(_QA_DEFAULTS)
    try:
        user = (_load_settings() or {}).get("qa_gates") or {}
        if isinstance(user, dict):
            cfg.update({k: v for k, v in user.items() if k in _QA_DEFAULTS})
    except Exception:
        pass
    # Clamp the threshold to [0, 1] and retries to a sane range.
    try:
        cfg["threshold"] = max(0.0, min(1.0, float(cfg["threshold"])))
    except (TypeError, ValueError):
        cfg["threshold"] = _QA_DEFAULTS["threshold"]
    try:
        cfg["max_retries"] = max(0, min(3, int(cfg["max_retries"])))
    except (TypeError, ValueError):
        cfg["max_retries"] = _QA_DEFAULTS["max_retries"]
    return cfg


def is_enabled() -> bool:
    return bool(qa_config().get("enabled"))


# ═══════════════════════════════════════════════════════════════════════════
#  SCORE PARSING
# ═══════════════════════════════════════════════════════════════════════════

def _parse_score(raw: str) -> Dict[str, Any]:
    """Pull a {score, critique, suggestions} verdict out of a model reply.

    Tolerant: accepts a JSON object anywhere in the text, else falls back to a
    'score: 0.8' style line, else a bare number. Score is normalized to 0–1
    (a 0–10 or 0–100 scale is rescaled).
    """
    text = (raw or "").strip()
    verdict: Dict[str, Any] = {"score": None, "critique": "", "suggestions": ""}

    # 1) JSON object.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                if "score" in obj:
                    verdict["score"] = _norm_score(obj.get("score"))
                verdict["critique"] = str(obj.get("critique")
                                          or obj.get("reason") or "").strip()
                sug = obj.get("suggestions") or obj.get("improvements") or ""
                verdict["suggestions"] = (", ".join(sug) if isinstance(sug, list)
                                          else str(sug)).strip()
                if verdict["score"] is not None:
                    return verdict
        except Exception:
            pass

    # 2) "score: 0.8" / "score = 8/10".
    m = re.search(r"score\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*(?:/\s*(\d+))?", text, re.I)
    if m:
        num = float(m.group(1))
        denom = float(m.group(2)) if m.group(2) else None
        verdict["score"] = _norm_score(num if denom is None else num / denom)
        verdict["critique"] = text[:500]
        return verdict

    # 3) bare leading number.
    m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)", text)
    if m:
        verdict["score"] = _norm_score(float(m.group(1)))
        verdict["critique"] = text[:500]
    return verdict


def _norm_score(val: Any) -> Optional[float]:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f > 1.0:                 # 0–10 or 0–100 → 0–1
        f = f / 10.0 if f <= 10.0 else f / 100.0
    return max(0.0, min(1.0, f))


# ═══════════════════════════════════════════════════════════════════════════
#  TEXT EVALUATION
# ═══════════════════════════════════════════════════════════════════════════

_EVAL_SYSTEM = (
    "You are a strict but fair QA reviewer. You score whether a piece of "
    "generated content satisfies the user's stated intent. Be concise and "
    "honest — do not inflate scores. Respond with ONLY a JSON object: "
    '{"score": <0.0-1.0>, "critique": "<one sentence>", '
    '"suggestions": "<concrete fixes, or empty if it passes>"}.'
)


def evaluate_text(content: str, intent: str, *,
                  workspace: str = "", extra_criteria: str = "") -> Dict[str, Any]:
    """Score generated text against the user's intent. Returns
    {status, score, passed, critique, suggestions}. status='skipped' (a pass)
    when no model is reachable, so a missing key never blocks delivery."""
    content = (content or "").strip()
    intent = (intent or "").strip()
    if not content:
        return {"status": "skipped", "passed": True, "score": None,
                "critique": "empty content", "suggestions": ""}

    cfg = qa_config()
    prompt = (
        f"INTENT (what the user wanted):\n{intent or '(general quality)'}\n\n"
        + (f"ADDITIONAL CRITERIA:\n{extra_criteria}\n\n" if extra_criteria else "")
        + f"GENERATED CONTENT:\n{content[:6000]}\n\n"
        "Score how well the content satisfies the intent. Return the JSON verdict."
    )
    try:
        from services.model_router import _generate_text
        raw = _generate_text([{"role": "user", "content": prompt}],
                             system=_EVAL_SYSTEM, max_tokens=400,
                             temperature=0.2, workspace=workspace or "review")
    except Exception as e:
        return {"status": "skipped", "passed": True, "score": None,
                "critique": f"evaluator unavailable: {e}", "suggestions": ""}

    verdict = _parse_score(raw)
    score = verdict["score"]
    if score is None:
        return {"status": "skipped", "passed": True, "score": None,
                "critique": "could not parse a score", "suggestions": "",
                "raw": raw[:500]}
    passed = score >= cfg["threshold"]
    return {"status": "ok", "passed": passed, "score": round(score, 3),
            "threshold": cfg["threshold"], "critique": verdict["critique"],
            "suggestions": verdict["suggestions"]}


# ═══════════════════════════════════════════════════════════════════════════
#  IMAGE EVALUATION  (Gemini vision)
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_image(image_path: str, intent: str) -> Dict[str, Any]:
    """Score a generated image against intent using a Gemini vision model.

    Returns the same envelope shape as evaluate_text. status='skipped' (pass)
    when vision QA is off, no key is set, or the image can't be read."""
    cfg = qa_config()
    if not cfg.get("vision_for_images"):
        return {"status": "skipped", "passed": True, "score": None,
                "critique": "vision QA disabled", "suggestions": ""}
    try:
        from services import creative_engine as ce
        if not ce.is_available():
            return {"status": "skipped", "passed": True, "score": None,
                    "critique": "no vision key", "suggestions": ""}
        from pathlib import Path
        p = Path(image_path).expanduser()
        if not p.exists():
            cand = ce.CREATIONS_DIR / Path(image_path).name
            p = cand if cand.exists() else p
        if not p.exists():
            return {"status": "skipped", "passed": True, "score": None,
                    "critique": "image not found", "suggestions": ""}
        data = p.read_bytes()
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"

        from google.genai import types
        client = ce._client()
        prompt = (
            f"Intent: {intent or 'a high-quality, coherent image'}.\n"
            "Score how well this image satisfies the intent. Respond with ONLY "
            'JSON: {"score": <0.0-1.0>, "critique": "<one sentence>", '
            '"suggestions": "<prompt tweaks, or empty>"}.')
        # Use a text/reasoning model for evaluation, never an image-gen model.
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[types.Part.from_bytes(data=data, mime_type=mime), prompt],
        )
        raw = getattr(resp, "text", "") or ""
    except Exception as e:
        return {"status": "skipped", "passed": True, "score": None,
                "critique": f"vision evaluator unavailable: {e}", "suggestions": ""}

    verdict = _parse_score(raw)
    score = verdict["score"]
    if score is None:
        return {"status": "skipped", "passed": True, "score": None,
                "critique": "could not parse a score", "suggestions": ""}
    return {"status": "ok", "passed": score >= cfg["threshold"],
            "score": round(score, 3), "threshold": cfg["threshold"],
            "critique": verdict["critique"], "suggestions": verdict["suggestions"]}


# ═══════════════════════════════════════════════════════════════════════════
#  TEXT GATE  — generate → evaluate → silently improve / flag
# ═══════════════════════════════════════════════════════════════════════════

def gate_text(generate_fn: Callable[[str], str], intent: str, *,
              workspace: str = "", extra_criteria: str = "") -> Dict[str, Any]:
    """Run a text generation through the QA gate.

    ``generate_fn(critique_hint)`` produces the content; it is called once with
    "" and, on a below-threshold result in "improve" mode, again with the
    critique/suggestions so it can regenerate better. Returns:
        {content, passed, score, critique, attempts, gated, action}
    ``gated`` is False when QA is disabled (generation runs once, ungated).
    """
    cfg = qa_config()
    if not cfg.get("enabled"):
        return {"content": generate_fn(""), "gated": False, "passed": True,
                "score": None, "attempts": 1, "action": "ungated"}

    hint = ""
    last: Dict[str, Any] = {}
    attempts = 0
    max_attempts = 1 + (cfg["max_retries"] if cfg["mode"] == "improve" else 0)
    content = ""
    while attempts < max_attempts:
        attempts += 1
        content = generate_fn(hint) or ""
        last = evaluate_text(content, intent, workspace=workspace,
                             extra_criteria=extra_criteria)
        if last.get("passed", True):
            return {"content": content, "gated": True, "passed": True,
                    "score": last.get("score"), "critique": last.get("critique"),
                    "attempts": attempts, "action": "passed"}
        # Below threshold → fold the critique into the next attempt (improve mode).
        hint = (f"A previous draft scored {last.get('score')} "
                f"(below {cfg['threshold']}). Reviewer critique: "
                f"{last.get('critique')}. Fixes: {last.get('suggestions')}. "
                "Produce a markedly improved version.")

    action = "flagged" if cfg["mode"] == "flag" else "best_effort"
    return {"content": content, "gated": True, "passed": False,
            "score": last.get("score"), "critique": last.get("critique"),
            "suggestions": last.get("suggestions"), "attempts": attempts,
            "action": action}
