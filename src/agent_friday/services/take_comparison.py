"""
Agent Friday — Take Comparison
FutureSpeak.AI · Asimov's Mind

For important creative decisions, don't ship the first take — generate 2–3
candidates, let Friday score and recommend the best, and let the user pick or
ask for more. A creative director's "let's see a few options" instinct.

  • compare_images(prompt, n)        → N image takes, each scored, best recommended
  • compare_text(intent, gen_fn, n)  → N text takes, each scored, best recommended
  • rank_takes(takes, scorer)        → generic scorer/ranker over pre-made takes

Scoring reuses the QA-gate evaluators (services/qa_gates), so a "take" is judged
by the same yardstick as a self-eval gate. Never raises; degrades to an
unscored list (recommending take 1) when no evaluator/key is available.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def _clamp_n(n: Any, lo: int = 2, hi: int = 4, default: int = 3) -> int:
    try:
        return max(lo, min(hi, int(n)))
    except (TypeError, ValueError):
        return default


def _recommend(takes: List[Dict[str, Any]]) -> int:
    """Index of the highest-scoring take. Unscored takes sort last; ties and
    all-unscored fall back to the first take."""
    best_idx, best_score = 0, -1.0
    found = False
    for i, t in enumerate(takes):
        s = t.get("score")
        if s is None:
            continue
        found = True
        if s > best_score:
            best_score, best_idx = s, i
    return best_idx if found else 0


# ═══════════════════════════════════════════════════════════════════════════
#  IMAGE TAKES
# ═══════════════════════════════════════════════════════════════════════════

def compare_images(prompt: str, *, n: int = 3, model: Optional[str] = None,
                   style: Optional[str] = None, aspect_ratio: str = "1:1",
                   intent: str = "") -> Dict[str, Any]:
    """Generate N image candidates and recommend the best.

    Each take is a separate single-image generation (so the files are distinct),
    scored by the vision QA evaluator against ``intent`` (defaults to the prompt).
    Returns {status, takes:[{filename,url,score,critique,...}], recommended_index}.
    """
    n = _clamp_n(n)
    intent = (intent or prompt or "").strip()
    try:
        from agent_friday.services import creative_engine as ce
        from agent_friday.services import qa_gates
    except Exception as e:
        return {"status": "error", "message": f"take comparison unavailable: {e}"}

    if not ce.is_available():
        return {"status": "unavailable",
                "message": "Image generation needs a Gemini API key."}

    takes: List[Dict[str, Any]] = []
    for i in range(n):
        res = ce.generate_image(prompt, model=model, style=style,
                                aspect_ratio=aspect_ratio, n=1)
        if res.get("status") == "blocked":
            return {"status": "blocked", "reason": res.get("reason")}
        if res.get("status") != "ok" or not res.get("files"):
            takes.append({"take": i + 1, "status": "error",
                          "message": res.get("message"), "score": None})
            continue
        f = res["files"][0]
        verdict = qa_gates.evaluate_image(f.get("path") or f.get("filename"), intent)
        takes.append({
            "take": i + 1, "status": "ok",
            "filename": f.get("filename"), "url": f.get("url"),
            "path": f.get("path"),
            "score": verdict.get("score"),
            "critique": verdict.get("critique"),
            "suggestions": verdict.get("suggestions"),
        })

    ok_takes = [t for t in takes if t.get("status") == "ok"]
    if not ok_takes:
        return {"status": "error", "message": "No takes were generated.",
                "takes": takes}
    rec = _recommend(takes)
    return {"status": "ok", "kind": "image", "prompt": prompt, "intent": intent,
            "takes": takes, "recommended_index": rec,
            "recommended": takes[rec]}


# ═══════════════════════════════════════════════════════════════════════════
#  TEXT TAKES
# ═══════════════════════════════════════════════════════════════════════════

def compare_text(intent: str, generate_fn: Callable[[int], str], *,
                 n: int = 3, workspace: str = "") -> Dict[str, Any]:
    """Generate N text candidates and recommend the best.

    ``generate_fn(i)`` returns the i-th candidate (0-based) — callers typically
    vary temperature or an angle hint by ``i`` to get diverse takes. Each take
    is scored by the text QA evaluator against ``intent``.
    """
    n = _clamp_n(n)
    try:
        from agent_friday.services import qa_gates
    except Exception as e:
        return {"status": "error", "message": f"take comparison unavailable: {e}"}

    takes: List[Dict[str, Any]] = []
    for i in range(n):
        try:
            content = (generate_fn(i) or "").strip()
        except Exception as e:
            takes.append({"take": i + 1, "status": "error",
                          "message": str(e), "score": None})
            continue
        if not content:
            takes.append({"take": i + 1, "status": "error",
                          "message": "empty candidate", "score": None})
            continue
        verdict = qa_gates.evaluate_text(content, intent, workspace=workspace)
        takes.append({"take": i + 1, "status": "ok", "content": content,
                      "score": verdict.get("score"),
                      "critique": verdict.get("critique"),
                      "suggestions": verdict.get("suggestions")})

    ok_takes = [t for t in takes if t.get("status") == "ok"]
    if not ok_takes:
        return {"status": "error", "message": "No candidates were generated.",
                "takes": takes}
    rec = _recommend(takes)
    return {"status": "ok", "kind": "text", "intent": intent,
            "takes": takes, "recommended_index": rec, "recommended": takes[rec]}


# ═══════════════════════════════════════════════════════════════════════════
#  GENERIC RANKER
# ═══════════════════════════════════════════════════════════════════════════

def rank_takes(takes: List[Dict[str, Any]],
               scorer: Optional[Callable[[Dict[str, Any]], float]] = None) -> Dict[str, Any]:
    """Score + rank a list of pre-made takes with an arbitrary scorer.

    ``scorer(take)`` returns a 0–1 score; if omitted, each take's existing
    'score' field is used. Returns {takes (sorted desc, with .rank),
    recommended_index:0, recommended}.
    """
    takes = list(takes or [])
    for t in takes:
        if scorer is not None:
            try:
                t["score"] = scorer(t)
            except Exception:
                t.setdefault("score", None)
    ordered = sorted(
        takes, key=lambda t: (t.get("score") is not None, t.get("score") or 0.0),
        reverse=True)
    for rank, t in enumerate(ordered):
        t["rank"] = rank + 1
    return {"status": "ok", "takes": ordered,
            "recommended_index": 0 if ordered else -1,
            "recommended": ordered[0] if ordered else None}
