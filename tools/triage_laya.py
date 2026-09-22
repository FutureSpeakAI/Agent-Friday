"""Score the triage eval set with Laya, alongside the existing rules.

Kept out of tools/triage_eval.py on purpose: that harness must stay importable
and runnable with no ML dependencies at all, so the incumbent can always be
measured even on a machine where Laya is not installed. This is the optional
second scorer.

Runs on CPU. bonsai2 owns ~10 GB of the 12 GB card and a decision that happens
before an action can afford a few hundred milliseconds; competing for VRAM
with the local model would be a bad trade for a 0.4B encoder.

Writes triage_scores_laya.jsonl in the same shape the rules scorer writes, so
`triage_eval.py report` grades both identically.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

os.environ.setdefault("USE_TF", "0")          # transformers deadlocks probing TF
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

EVAL_DIR = pathlib.Path.home() / ".friday" / "eval"
SET_FILE = EVAL_DIR / "triage_set.jsonl"
OUT_FILE = EVAL_DIR / "triage_scores_laya.jsonl"

#: The criteria are Stephen's own lanes, described the way his preferences
#: note describes them - the model is being asked HIS question, not a generic
#: one. ~/.friday/wiki/professional/email-triage-preferences.md is the source.
LANE_CRITERIA = {
    "career": "job search, recruiters, interviews, professional opportunities, "
              "industry news relevant to his work in AI",
    "finance": "banking, invoices, bills, payments, taxes, statements",
    "futurespeak": "his own company FutureSpeak.AI, clients, contracts, "
                   "partnerships, product work",
    "family": "personal correspondence from family and friends, school, health",
    "subscriptions": "newsletters and briefings he chose to receive and "
                     "actually reads",
    "noise": "marketing, cold outreach, automated notifications, receipts he "
             "will never open, anything that is genuinely clutter",
}

QUESTIONS = {
    "lane": {
        "type": "choice",
        "instructions": "Which lane does this email belong in for Stephen, a "
                        "journalist-turned-AI-architect job-hunting at "
                        "director level while running FutureSpeak.AI?",
        "criteria": LANE_CRITERIA,
    },
    "needs_reply": {
        "type": "noul",
        "instructions": "Does this email need a personal reply from Stephen?",
    },
    "signal": {
        "type": "noul",
        "instructions": "Is this real signal worth his attention today, as "
                        "opposed to daily clutter he can ignore?",
    },
}


def _state(card: dict) -> dict:
    return {"from": card.get("sender", ""),
            "subject": card.get("subject", ""),
            "body": card.get("snippet", "")}


def main() -> None:
    rows = []
    for line in SET_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    print(f"  {len(rows)} message(s) to score")

    import laya
    t0 = time.time()
    agent = laya.load("convaiinnovations/laya", device="cpu")
    print(f"  model loaded in {time.time() - t0:.1f}s (CPU)")

    out = []
    for i, card in enumerate(rows, 1):
        t = time.time()
        try:
            res = agent.predict(_state(card), QUESTIONS)
            ans = res.get("answers") or {}
            lane_a = ans.get("lane") or {}
            lane = lane_a.get("choice")
            conf = lane_a.get("confidence")
            if conf is None:
                probs = lane_a.get("probabilities") or {}
                conf = max(probs.values()) if probs else None
            reply = (ans.get("needs_reply") or {}).get("noul")
            signal = (ans.get("signal") or {}).get("noul")
            err = None
        except Exception as e:
            lane, conf, reply, signal = "ERROR", 0.0, None, None
            err = f"{type(e).__name__}: {e}"
        ms = (time.time() - t) * 1000
        out.append({"id": card["id"], "lane": lane,
                    "confidence": round(float(conf), 4) if conf is not None else None,
                    "needs_reply": reply, "signal": signal,
                    "reasons": [err] if err else [], "ms": round(ms, 2)})
        if i % 10 == 0:
            print(f"    {i}/{len(rows)}  ({ms:.0f} ms last)")

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    ok = [r for r in out if r["lane"] != "ERROR"]
    ms = [r["ms"] for r in out] or [0]
    print(f"\n  wrote {OUT_FILE}")
    print(f"  scored ok : {len(ok)}/{len(out)}")
    print(f"  latency   : mean {sum(ms)/len(ms):.0f} ms, max {max(ms):.0f} ms")
    if ok and out[0]["reasons"]:
        print(f"  first error: {out[0]['reasons'][0]}")


if __name__ == "__main__":
    main()
