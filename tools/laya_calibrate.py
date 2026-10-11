"""Fit the router head's calibration on dev sets 1-3 and write the shipped table.

    python tools/laya_calibrate.py [--out src/agent_friday/resources/laya_calibration.json] [--dry-run]

What it does (swot spec c.4): for every case in dev sets 1-3 that the router's
tier 1 decides (rules decide the rest, and a rule is not a probability), it
takes the raw cosines against every prototype, splits the cases into a fit half
and a test half by a hash of the text, fits one temperature and one conformal
cut-off on the fit half, and reports ECE before and after on both halves. The
table it writes holds numbers and ids only. It never reads set 4 or set 5:
``laya_calibration.check_fit_sources`` refuses their files by name.

CPU only; loads the Laya 2 encoder the router already uses. Run it with
CUDA_VISIBLE_DEVICES=-1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "tests" / "fixtures"
UNWANTED_TARGET = 0.02


def truth_label(case: dict):
    d = case.get("decision")
    if d == "tool":
        return case.get("tool")
    return {"no_tool": "conversation", "defer": "act"}.get(d)


def side(text: str) -> str:
    return "fit" if int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % 2 == 0 else "test"


def _head_off(lr):
    """``head_mode="off"`` where this build's router has a tool head to switch off."""
    import inspect
    return {"head_mode": "off"} if "head_mode" in inspect.signature(lr.route).parameters else {}


def triage_available():
    """The triage heads and their labelled sets belong to another track; a build
    without them keeps the triage entries already in the shipped table."""
    try:
        from agent_friday.services import triage_heads  # noqa: F401
    except ImportError:
        return False
    return all((FIXTURES / n).exists() for n in TRIAGE_SETS.values())


def collect(sets, population="tier1"):
    from agent_friday.services import laya_router as lr
    lr._prototypes()
    rows = []
    skipped = {"rule": 0, "no_truth": 0}
    for name in sets:
        for c in json.loads((FIXTURES / name).read_text(encoding="utf-8"))["cases"]:
            t = truth_label(c)
            r = lr.route(c["text"], budget_ms=2000, log=False, **_head_off(lr))
            if population == "tier1" and r.layer != "laya2":
                skipped["rule"] += 1
                continue
            ranked = lr._score(" ".join(c["text"].split()))
            labels = [l for l, _c, _s in ranked]
            if t not in labels:
                skipped["no_truth"] += 1
                continue
            rows.append({"set": name, "sims": [s for _l, _c, s in ranked], "labels": labels,
                         "truth": labels.index(t), "side": side(c["text"])})
    return rows, skipped


TRIAGE_SETS = {"mail": "triage_eval_mail.json", "news": "triage_eval_news.json",
               "notify": "triage_eval_notifications.json", "memory": "triage_eval_memory.json"}
TRIAGE_FITTED_ON = "author-written triage sets (not independent)"


def triage_text(head, item):
    from agent_friday.services import triage_heads as th
    if head == "mail":
        return th.mail_text(item.get("subject"), item.get("snippet"))
    if head == "news":
        return th.news_text(item)
    return th._clip(item.get("text"))


def collect_triage(head):
    """(sims rows, truth) over the head's labelled set, cosines against its prototypes."""
    from agent_friday.services import triage_heads as th
    items = json.loads((FIXTURES / TRIAGE_SETS[head]).read_text(encoding="utf-8"))["items"]
    protos = th.head(head).prototypes(block=True)
    labels = sorted(protos)
    rows = []
    for it in items:
        if it.get("label") not in protos:
            continue
        text = triage_text(head, it)
        (vec,) = th._embed([th._clip(text)])
        rows.append(([sum(a * b for a, b in zip(protos[l], vec)) for l in labels],
                     labels.index(it["label"]), side(text)))
    return labels, rows


def fit_triage(head):
    from agent_friday.services import laya_calibration as lc
    from agent_friday.services import triage_heads as th
    labels, rows = collect_triage(head)
    fit = [r for r in rows if r[2] == "fit"]
    test = [r for r in rows if r[2] == "test"]
    res = lc.fit_head([r[0] for r in fit], [r[1] for r in fit], [r[0] for r in test], [r[1] for r in test],
                      base_tau=1.0 / th.K[head])
    res.update(labels=labels, shadow=True, fitted_on=TRIAGE_FITTED_ON,
               set=TRIAGE_SETS[head], thresholds={})
    return res


def check_on(rows, labels, tau, base_tau, q_hat):
    """The fitted temperature and cut-off applied, unchanged, to ``rows``."""
    from agent_friday.services import laya_calibration as lc
    order = sorted(labels)
    sims, truth = [], []
    for r in rows:
        sims.append([r["sims"][r["labels"].index(l)] for l in order])
        truth.append(order.index(r["labels"][r["truth"]]))
    out = {"n": len(rows)}
    if not rows:
        return out
    for key, t in (("before", base_tau), ("after", tau)):
        pr = [lc.probs_from_sims(x, t) for x in sims]
        out["ece_" + key] = lc.ece([max(p) for p in pr], [p.index(max(p)) == y for p, y in zip(pr, truth)])
    pr = [lc.probs_from_sims(x, tau) for x in sims]
    out["coverage_seen"] = round(sum(1 for p, y in zip(pr, truth) if 1.0 - p[y] <= q_hat + 1e-12) / len(rows), 4)
    out["set_size_mean"] = round(sum(sum(1 for v in p if 1.0 - v <= q_hat + 1e-12) for p in pr) / len(rows), 3)
    return out


def main(argv=None) -> int:
    from agent_friday.services import laya_calibration as lc
    from agent_friday.services import laya_router as lr
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "src" / "agent_friday" / "resources" / lc.TABLE_NAME))
    ap.add_argument("--sets", nargs="*", default=list(lc.DEV_SETS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--population", choices=("tier1", "all"), default="all",
                    help="tier1: only the turns the router's tier 1 decides (what the head faces in use); "
                         "all: every labelled turn, scored by the head whether or not a rule decided it")
    a = ap.parse_args(argv)
    lc.check_fit_sources(a.sets)
    rows, skipped = collect(a.sets, a.population)
    # the prototype order is fixed, so every row has the same label order
    labels = rows[0]["labels"]
    fit = [r for r in rows if r["side"] == "fit"]
    test = [r for r in rows if r["side"] == "test"]
    # rows may order labels by their own score: put every row in prototype order
    def norm(r):
        order = [r["labels"].index(l) for l in sorted(labels)]
        return [r["sims"][i] for i in order], sorted(labels).index(r["labels"][r["truth"]])
    fx = [norm(r) for r in fit]
    tx = [norm(r) for r in test]
    res = lc.fit_head([x for x, _ in fx], [t for _, t in fx], [x for x, _ in tx], [t for _, t in tx],
                      base_tau=lr.TEMPERATURE)
    entry = {k: v for k, v in res.items()}
    entry["labels"] = sorted(labels)
    entry["thresholds"] = {"act": lr.ACT_THRESHOLD, "margin": lr.MARGIN_THRESHOLD}
    entry["shadow"] = True
    entry["sets"] = {n: hashlib.sha256((FIXTURES / n).read_bytes()).hexdigest()[:16] for n in a.sets}
    entry["skipped"] = skipped
    entry["population"] = a.population
    # What the head faces in use is the turns the rules decline: report it on its own.
    t1_rows, _ = collect(a.sets, "tier1")
    entry["tier1_only"] = check_on(t1_rows, labels, entry["tau"], lr.TEMPERATURE, entry["q_hat"])
    table = {"version": "", "note": "Numbers and ids only. Fitted on dev sets 1-3 by tools/laya_calibrate.py; "
             "shadow: the calibrated decision is logged next to the current one and changes nothing.",
             "heads": {"router": entry}}
    entry["fitted_on"] = ("dev sets 1-3, every labelled turn whether or not a rule decided it; shadow only. "
                          "The honest check is the sealed set 5, scored once by the lead.")
    if triage_available():
        for h in TRIAGE_SETS:
            table["heads"]["triage." + h] = fit_triage(h)
    else:
        kept = (json.loads(Path(a.out).read_text(encoding="utf-8")).get("heads") or {}) if Path(a.out).exists() else {}
        for h in TRIAGE_SETS:
            if "triage." + h in kept:
                table["heads"]["triage." + h] = kept["triage." + h]
        print("triage heads or their sets are not in this build: kept the existing triage entries")
    for h in ("gate_opinion", "resolve"):
        table["heads"][h] = {"fitted": False, "tau": None,
                             "reason": "no labelled data for this head in dev sets 1-3; reads at its shipped temperature"}
    body = json.dumps(table["heads"], sort_keys=True)
    table["version"] = "cal-" + hashlib.sha256(body.encode()).hexdigest()[:10]
    print(json.dumps({"n_fit": len(fit), "n_test": len(test), "skipped": skipped,
                      "router": {k: entry[k] for k in ("tau", "base_tau", "q_hat", "coverage", "fit", "test", "tier1_only") if k in entry}},
                     indent=1))
    if not a.dry_run:
        Path(a.out).write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
