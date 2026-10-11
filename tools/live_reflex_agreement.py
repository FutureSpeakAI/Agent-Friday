"""Per-class agreement between Laya's shadow reflex and Gemini Live's own calls.

Reads the shadow log that services/live_laya writes during cloud voice calls
(one row per turn where either side acted: Laya's would-be tool, Gemini's calls,
and agree / differ / none) and reports, per reflex class, whether the class may
be enabled:

    agreement = same / (same + different + laya_only)    over rows where Laya decided
    eligible  = rows >= MIN_ROWS  and  agreement >= 0.95  and  unwanted == 0

An "unwanted" row is one where Laya would have acted and Gemini, hearing the
same words, made no call at all. services/live_laya reads this report before it
will run any class, so the gate is code, not a setting alone.

    python tools/live_reflex_agreement.py [--log PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MIN_ROWS = 30
FLOOR = 0.95


def _rows(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def report(path, min_rows: int = MIN_ROWS, floor: float = FLOOR) -> dict:
    """{"classes": {class: {...}}, "rows": N} from a shadow log."""
    classes: dict = {}
    total = 0
    for row in _rows(Path(path)):
        total += 1
        c = classes.setdefault(str(row.get("class") or "other"), {
            "same": 0, "different": 0, "laya_only": 0, "gemini_only": 0, "unwanted": 0, "executed": 0})
        agreement, side = row.get("agreement"), row.get("side")
        if agreement == "same":
            c["same"] += 1
        elif agreement == "different":
            c["different"] += 1
        elif side == "laya_only":
            c["laya_only"] += 1
        elif side == "gemini_only":
            c["gemini_only"] += 1
        if row.get("unwanted"):
            c["unwanted"] += 1
        if row.get("executed"):
            c["executed"] += 1
    for name, c in classes.items():
        compared = c["same"] + c["different"] + c["laya_only"]
        c["compared"] = compared
        c["agreement"] = (c["same"] / compared) if compared else 0.0
        c["eligible"] = bool(name != "other" and compared >= min_rows
                             and c["agreement"] >= floor and c["unwanted"] == 0)
    return {"rows": total, "min_rows": min_rows, "floor": floor, "classes": classes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", help="shadow log path (default: the Friday home's)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    path = Path(args.log) if args.log else None
    if path is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agent_friday.services import live_laya
        path = live_laya.shadow_log_path()
    rep = report(path)
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0
    print("%d rows in %s" % (rep["rows"], path))
    for name, c in sorted(rep["classes"].items()):
        print("%-7s agreement %5.1f%% over %d  (same %d, different %d, laya-only %d, gemini-only %d)  "
              "unwanted %d  -> %s" % (name, c["agreement"] * 100, c["compared"], c["same"], c["different"],
                                      c["laya_only"], c["gemini_only"], c["unwanted"],
                                      "ELIGIBLE" if c["eligible"] else "not yet"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
