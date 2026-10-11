"""Decision diff for services/laya_router: the chain's voice route before and after the router generation.

The invariant: for every phrase the chain already handled, the voice route (``route()`` as the local voice
front calls it, ``trusted`` False; ``laya.route_turn``; and the degraded ``rules_route``) makes exactly the same
decision, tool and arguments after the port as before. The one class of difference the port is allowed to make
is NARROWER: a phrase that holds a change anywhere in the turn becomes a ``defer`` (reason "an imperative that
changes something"), never the other way round, and never a different tool. That class is reported on its own,
split into the full route and the degraded (rules_route) path, so each can be read by hand.

    python tools/laya_router_decision_diff.py run  --base <chain tip worktree> --new <this worktree> --scratch <dir>
    python tools/laya_router_decision_diff.py dump --src <worktree> --cases <cases.json> --out <out.json>
    python tools/laya_router_decision_diff.py compare <base.json> <new.json>

``run`` builds the case list once (the router eval sets, every router_rule / router_seed phrasing in the
manifests, and probes for a waiting card, follow-ups and the owner-mark phrase), dumps it from both trees in
separate processes with FRIDAY_HOME, HOME and USERPROFILE on the scratch directory and CUDA_VISIBLE_DEVICES=-1,
and compares. Exit 0 only when every difference is in the allowed class. CPU only; nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_SETS = ("laya_router_eval.json", "laya_router_holdout2.json", "laya_router_holdout3.json")

#: Probes the eval sets do not carry: (text, kwargs). A waiting card, a follow-up that needs the previous
#: look-up, the voice owner-mark phrase, and the compounds the write guard exists for.
PROBES = [
    ("yes", {"pending_card": "ap_probe"}), ("no", {"pending_card": "ap_probe"}),
    ("yes please", {"pending_card": "ap_probe"}), ("sure go ahead", {"pending_card": "ap_probe"}),
    ("what about tomorrow", {"last_route": {"tool": "query_calendar", "args": {}, "age": 0}}),
    ("any from Sarah", {"last_route": {"tool": "check_email", "args": {}, "age": 0}}),
    ("what about the bank", {"last_route": {"tool": "search_email", "args": {"query": "x"}, "age": 0}}),
    ("quiet until morning", {}), ("be quiet until morning", {}), ("stop", {}),
    ("cancel the background task", {}), ("undo that", {}), ("pause the podcast", {}),
    ("play some jazz and delete my old mail", {}),
    ("look up the dentist's hours and put them on my calendar", {}),
    ("email my sister that the party moved", {}),
    ("archive these", {}), ("open the second one", {}), ("open the budget spreadsheet", {}),
]


def build_cases(root: Path) -> list:
    cases, seen = [], set()

    def add(text, kw=None):
        key = (text, json.dumps(kw or {}, sort_keys=True))
        if text and key not in seen:
            seen.add(key)
            cases.append({"text": text, "kw": kw or {}})
    for name in EVAL_SETS:
        p = root / "tests" / "fixtures" / name
        if p.is_file():
            for c in json.loads(p.read_text(encoding="utf-8"))["cases"]:
                add(c["text"])
    for p in sorted((root / "src" / "agent_friday" / "resources" / "laya_manifests").glob("*.json")):
        for v in json.loads(p.read_text(encoding="utf-8")).get("verbs", []):
            if v.get("laya_tier") in ("router_rule", "router_seed"):
                for ph in v.get("phrasings", []):
                    add(ph)
    for text, kw in PROBES:
        add(text, kw)
    return cases


def _row(r) -> dict:
    return {"decision": r.decision, "tool": r.tool, "args": r.args, "layer": r.layer, "reason": r.reason,
            "question": r.question}


def dump(src: Path, cases_path: Path, out: Path) -> int:
    sys.path.insert(0, str(src / "src"))
    from agent_friday.services import laya, laya_router as lr
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    rows = []
    for c in cases:
        t, kw = c["text"], dict(c["kw"])
        row = {"text": t, "kw": kw}
        r = lr.route(t, budget_ms=1000, log=False, **kw)
        row["route"] = _row(r)
        r = laya.route_turn(t, caller="eval.diff", budget_ms=1000, log=False, **kw)
        row["route_turn"] = _row(r)
        r = lr.rules_route(t, pending_card=kw.get("pending_card"), last_route=kw.get("last_route"))
        row["rules_route"] = _row(r)
        rows.append(row)
    out.write_text(json.dumps({"src": str(src), "rows": rows}, indent=1), encoding="utf-8")
    print("dumped %d cases from %s" % (len(rows), src))
    return 0


WRITE_REASON = "an imperative that changes something"


def _same(a: dict, b: dict) -> bool:
    return (a["decision"], a["tool"], a["args"], a["question"]) == (b["decision"], b["tool"], b["args"], b["question"])


def compare(base: Path, new: Path) -> int:
    A = {(r["text"], json.dumps(r["kw"], sort_keys=True)): r for r in json.loads(base.read_text(encoding="utf-8"))["rows"]}
    B = {(r["text"], json.dumps(r["kw"], sort_keys=True)): r for r in json.loads(new.read_text(encoding="utf-8"))["rows"]}
    assert A.keys() == B.keys(), "the two dumps are not over the same cases"
    allowed = {"route": [], "route_turn": [], "rules_route": []}
    bad = []
    same = 0
    for k in sorted(A):
        for surface in ("route", "route_turn", "rules_route"):
            a, b = A[k][surface], B[k][surface]
            if _same(a, b):
                same += 1
                continue
            narrowed = (b["decision"] == "defer" and b["tool"] is None and WRITE_REASON in (b["reason"] or "")
                        and a["decision"] != "defer")
            (allowed[surface] if narrowed else bad).append((surface, k[0], k[1], a, b))
    print("compared %d cases x 3 surfaces: %d identical" % (len(A), same))
    for surface, rows in allowed.items():
        print("\nALLOWED (narrower: a change anywhere in the turn is a defer) on %s: %d" % (surface, len(rows)))
        for _, text, kw, a, b in rows:
            print("   %-64s %s %s  ->  defer" % (text[:64], a["decision"], a["tool"] or ""))
    print("\nNOT ALLOWED: %d" % len(bad))
    for surface, text, kw, a, b in bad:
        print("   [%s] %-60s %s | %s %s %s  ->  %s %s %s" % (surface, text[:60], kw, a["decision"], a["tool"], a["args"],
                                                         b["decision"], b["tool"], b["args"]))
    return 1 if bad else 0


def run(base: Path, new: Path, scratch: Path) -> int:
    scratch.mkdir(parents=True, exist_ok=True)
    cases = scratch / "cases.json"
    cases.write_text(json.dumps(build_cases(new), indent=1), encoding="utf-8")
    env = dict(os.environ, FRIDAY_HOME=str(scratch), HOME=str(scratch), USERPROFILE=str(scratch),
               CUDA_VISIBLE_DEVICES="-1", PYTHONDONTWRITEBYTECODE="1")
    outs = []
    for name, tree in (("base", base), ("new", new)):
        out = scratch / ("decisions_%s.json" % name)
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "dump", "--src", str(tree),
                        "--cases", str(cases), "--out", str(out)], check=True, env=env)
        outs.append(out)
    return compare(outs[0], outs[1])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base", required=True)
    r.add_argument("--new", required=True)
    r.add_argument("--scratch", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--src", required=True)
    d.add_argument("--cases", required=True)
    d.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("new")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(Path(a.base), Path(a.new), Path(a.scratch))
    if a.cmd == "dump":
        return dump(Path(a.src), Path(a.cases), Path(a.out))
    return compare(Path(a.base), Path(a.new))


if __name__ == "__main__":
    sys.exit(main())
