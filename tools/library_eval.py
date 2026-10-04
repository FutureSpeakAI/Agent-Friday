"""Measure the Library's search against labelled questions.

    python tools/library_eval.py [--docs 30] [--encoder real|fake] [--floor-tier]
                                 [--calibrate] [--out DIR]

Builds the synthetic fixture library (tests/rig/library_golden), indexes it in a
scratch Friday home, asks every question and reports:

  evidence recall@12   a gold answer is inside a returned passage (measured apart
                       from answer wording, the discipline that keeps retrieval
                       honest)
  top-1 precision      the first passage holds the gold answer (the strict check
                       behind citation precision: a footnote on that passage is
                       right)
  not-found honesty    questions with no answer that end with "not found" (a
                       'brain' fallback or only guesses) rather than a confident hit
  time to evidence     p50 and p95, milliseconds
  Laya escalations     share of menus that needed Laya (0 when it is not loaded)

and checks them against the promotion gate (recall >= 0.85, honesty >= 0.90, top-1
>= 0.95). It writes a JSON receipt under runtime/library/receipts/. `--calibrate`
sweeps the encoder's temperature and no-match floor and prints the lowest-risk pair;
nothing is written to a live Friday home.

Nothing here opens a network connection. The sentence encoder is read from the
installed Library artifact (set FRIDAY_LIBRARY_ENCODER_DIR) or replaced by a
deterministic stand-in with --encoder fake.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

GATE = {"recall": 0.85, "honesty": 0.90, "top1": 0.95}


def _setup(encoder: str):
    home = Path(tempfile.mkdtemp(prefix="library_eval_"))
    os.environ["FRIDAY_HOME"] = str(home / ".friday")
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
    os.environ["FRIDAY_TESTING"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    from agent_friday.services import file_grants as fg
    fg._ledger_path = lambda: home / "privacy" / "file_grants.jsonl"
    from agent_friday.services.library import embed, shelf
    shelf._vault_key = lambda: None
    shelf.tier_of = lambda title, sample: 1          # the classifier is not what is measured here
    if encoder == "fake":
        class MP:
            @staticmethod
            def setattr(o, n, v):
                setattr(o, n, v)
        from tests.library_fixtures import install_fake_encoder
        install_fake_encoder(MP)
    elif not embed.available():
        sys.exit("The sentence encoder is not available; set FRIDAY_LIBRARY_ENCODER_DIR or use --encoder fake.")
    return home


def _index(home: Path, docs: int):
    from agent_friday.services.library import grants, indexer
    from agent_friday.services.library.store import store_for
    from tests.rig.library_golden.build import build
    root = home / "Library"
    gold = build(root, n_docs=docs)
    grants.add_scope("owner", str(root))
    st = store_for("owner")
    t = time.monotonic()
    out = indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    return st, gold, out, time.monotonic() - t


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0.0


def evaluate(gold: dict, floor_tier: bool) -> dict:
    from agent_friday.services.library import search
    rec = top1 = n_ans = 0
    honest = n_miss = 0
    lat, menus, laya = [], 0, 0
    misses = []
    for q in gold["questions"]:
        t = time.monotonic()
        res = search.run(q["q"], floor_tier=floor_tier)
        ms = (time.monotonic() - t) * 1000
        ev = res.get("evidence") or []
        fb = (res.get("searched") or {}).get("fallback")
        if q["kind"] == "answerable":
            n_ans += 1
            lat.append(ms)
            hit = any(q["gold"] in e["text"] for e in ev)
            rec += hit
            top1 += bool(ev and q["gold"] in ev[0]["text"])
            if not hit:
                misses.append(q["q"])
        elif q["kind"] == "missing":
            n_miss += 1
            confident = bool(ev) and fb != "brain" and any(e["sure"] != "a guess" for e in ev)
            honest += not confident
        menus += len([e for e in res.get("events", []) if e.get("event") == "decision"])
        laya += sum(1 for e in res.get("events", []) if e.get("answerer") == "L")
    return {
        "answerable": n_ans, "missing": n_miss,
        "evidence_recall_at_12": round(rec / max(1, n_ans), 4),
        "top1_precision": round(top1 / max(1, n_ans), 4),
        "not_found_honesty": round(honest / max(1, n_miss), 4),
        "time_to_evidence_ms": {"p50": round(statistics.median(lat), 1) if lat else 0, "p95": round(_pct(lat, 95), 1)},
        "laya_menu_share": round(laya / max(1, menus), 4),
        "misses": misses[:10],
    }


def calibrate(gold: dict) -> dict:
    """The no-match floor, temperature and 'found' line with the best recall that
    still keeps not-found honesty at the gate. Retrieval recall does not depend on
    the 'found' line, so that line is swept apart from the others."""
    from agent_friday.services.library import route
    best = None
    base = dict(route.DEFAULTS)
    sample = {"questions": gold["questions"][::3]}
    for T in (0.04, 0.06, 0.1):
        for floor in (0.12, 0.2, 0.3):
            for weak in (0.28, 0.35, 0.42, 0.5):
                route.DEFAULTS = {**base, "temp": {k: T for k in base["temp"]}, "floor": floor,
                                  "p_weak": weak, "p_strong": max(base["p_strong"], weak + 0.2)}
                m = evaluate(sample, False)
                score = (m["not_found_honesty"] >= GATE["honesty"], m["evidence_recall_at_12"], m["top1_precision"], -weak)
                if best is None or score > best[0]:
                    best = (score, {"temp": T, "floor": floor, "p_weak": weak, "metrics": m})
    route.DEFAULTS = base
    return best[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=30)
    ap.add_argument("--encoder", choices=("real", "fake"), default="real")
    ap.add_argument("--floor-tier", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "runtime" / "library" / "receipts"))
    a = ap.parse_args(argv)
    home = _setup(a.encoder)
    st, gold, idx, secs = _index(home, a.docs)
    print("indexed %s in %.1f s" % (idx, secs))
    m = evaluate(gold, a.floor_tier)
    m["encoder"] = a.encoder
    m["index_seconds"] = round(secs, 1)
    verdict = {"recall": m["evidence_recall_at_12"] >= GATE["recall"], "honesty": m["not_found_honesty"] >= GATE["honesty"],
               "top1": m["top1_precision"] >= GATE["top1"]}
    m["gate"] = verdict
    print(json.dumps(m, indent=1))
    if a.calibrate:
        print("calibration:", json.dumps(calibrate(gold)))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / ("eval_%d.json" % time.time())).write_text(json.dumps(m, indent=1), encoding="utf-8")
    return 0 if all(verdict.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
