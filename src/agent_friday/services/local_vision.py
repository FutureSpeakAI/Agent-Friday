"""Describe an image on-device, using the seat that is already resident.

Why this exists
---------------
Until 2026-08-23 Friday had exactly one way to look at a picture: send it to
`gemini-2.5-flash` and paste the returned sentence into the prompt as
`vision_description`. That path ran on ANY attached image, before
`model_routing` was read, so a user on **Local only** — a mode whose help text
promises *"Never leaves the machine"* — had a screenshot of their desktop sent
to Google. A screenshot is not a bounded payload; it contains whatever was on
screen.

The capability to avoid that now exists locally. `residency_arbiter._spawn`
passes `--mmproj` when the extracted projector is beside the weights, so a seat
running `gemma4:12b` answers image questions itself. Verified end to end rather
than by reading the command line: two images, two colours, correct both times.

What this is not
----------------
The cost is much lower than the first measurement suggested, and the first
measurement is why this paragraph is specific. A throwaway CPU-only server
(`-ngl 0`) took ~30 s, which read as "local vision is a slow fallback". The
REAL seat runs at `-ngl 99` on the card, and measured through this module on
2026-08-23 it described a 128x128 image in **7.2 s**. End to end through
`/api/chat` in local_only the whole turn took 25.7 s against 17.1 s for the
same turn on Gemini — a difference of about eight seconds, not the order of
magnitude the CPU number implied.

So this is not a degraded fallback. The choice still lives in the caller
(`routes/chat.py`) rather than here, because it is a privacy decision before it
is a latency one. This module answers exactly one question — "can this machine
describe this image, and what does it say?" — and reports honestly when it
cannot.

Every failure returns a reason. A vision path that silently produces nothing is
the same defect class as the rest of this codebase: a surface that looks like it
is working while doing nothing.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

_log = logging.getLogger("friday.local_vision")

# Short and concrete. The local seat is smaller than Gemini and a long
# instruction spends its budget on the instruction.
DEFAULT_PROMPT = ("Briefly describe what is visible in this image. Focus on "
                  "text, UI elements, and data shown. Be concise (2-3 "
                  "sentences).")

# gemma4 declares `thinking`, and a small budget is consumed ENTIRELY by the
# reasoning trace: measured 2026-08-23, max_tokens=24 returned an empty string
# with finish_reason "length" while the projector was loaded and working
# perfectly. An empty reply from a thinking model is a budget symptom that looks
# exactly like a broken capability -- ollama_manager.probe_generate documents
# the same effect for num_predict=10.
#
# 400 was the first fix and it was not enough, which is the more useful lesson.
# It held for a terse prompt on a 140x140 image and failed the next morning on
# a 160x160 one through the upload path: same model, same projector, same code,
# empty reply, finish_reason=length. The trace length varies with the image and
# the prompt, so a budget that merely clears the trace SOMETIMES produces an
# intermittent blindness that reads as a broken seat -- worse than a consistent
# failure, because it teaches the user the feature is unreliable rather than
# unconfigured.
#
# 1200 is sized for the trace plus a real answer with headroom. The cost of
# being wrong upward is a few hundred unused tokens on a local model; the cost
# of being wrong downward is a capability that appears to come and go.
DEFAULT_MAX_TOKENS = 1200


def reasoning_model(settings: dict | None = None) -> str | None:
    """The model in the conversational seat, as SETTINGS name it."""
    try:
        if settings is None:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        cr = (settings.get("capability_routing") or {})
        mid = ((cr.get("reasoning") or {}).get("model") or "").strip()
        return mid or None
    except Exception:
        return None


def projector_for(model: str):
    """The extracted vision tower for `model`, or None if it has none."""
    try:
        from agent_friday.services import gguf_extract as gx
        p = gx.projector_path(model)
        return p if p.exists() else None
    except Exception:
        return None


def capability(settings: dict | None = None) -> dict:
    """Can this machine describe an image right now, and if not, why not?

    Pure inspection -- no model is loaded and no request is sent, so this is
    safe to call on every turn to decide routing.
    """
    model = reasoning_model(settings)
    if not model:
        return {"ok": False, "model": None,
                "reason": "no model is assigned to the conversational seat"}
    proj = projector_for(model)
    if proj is None:
        return {"ok": False, "model": model,
                "reason": f"{model} has no vision projector on this machine"}
    try:
        from agent_friday.services.local_call import seat_endpoint
        base = seat_endpoint(model)
    except Exception as e:
        return {"ok": False, "model": model,
                "reason": f"could not resolve the seat endpoint ({e})"}
    if not base:
        return {"ok": False, "model": model,
                "reason": f"{model} is not currently being served on a local port"}
    return {"ok": True, "model": model, "endpoint": base,
            "projector": str(proj), "reason": "ready"}


def describe(image_b64: str, *, mime: str = "image/png",
             prompt: str = DEFAULT_PROMPT, settings: dict | None = None,
             timeout: float = 180.0,
             max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
    """Describe an image using the local seat. Never raises.

    Returns {ok, text, model, seconds, reason}. `ok` False always carries a
    reason a human can act on.
    """
    cap = capability(settings)
    if not cap.get("ok"):
        return {"ok": False, "text": None, "model": cap.get("model"),
                "seconds": 0.0, "reason": cap.get("reason")}

    body = {
        "model": cap["model"],
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ]}],
    }
    url = cap["endpoint"].rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        _log.warning("local vision HTTP %s from %s: %s", e.code, url, detail)
        return {"ok": False, "text": None, "model": cap["model"],
                "seconds": round(time.time() - t0, 1),
                "reason": f"the local seat answered HTTP {e.code}: {detail}"}
    except Exception as e:
        _log.warning("local vision unreachable at %s: %s", url, e)
        return {"ok": False, "text": None, "model": cap["model"],
                "seconds": round(time.time() - t0, 1),
                "reason": f"could not reach the local seat ({e})"}

    choice = (payload.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "").strip()
    finish = choice.get("finish_reason")
    secs = round(time.time() - t0, 1)
    if not text:
        # Say WHICH kind of empty. "length" means the budget was spent before
        # the answer started -- a configuration fact, not a blind model.
        why = ("the reply was cut off by the token budget before any text was "
               "produced (finish_reason=length); raise max_tokens"
               if finish == "length" else
               f"the local seat returned no text (finish_reason={finish})")
        return {"ok": False, "text": None, "model": cap["model"],
                "seconds": secs, "reason": why}
    return {"ok": True, "text": text, "model": cap["model"],
            "seconds": secs, "reason": "described locally"}
