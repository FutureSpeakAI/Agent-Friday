"""The safety property, checked against the REAL model instead of a fake.

WHY THIS EXISTS ALONGSIDE tests/unit/test_laya_union_gate.py

That suite fakes Laya at the `predict` boundary, deliberately: it has to run on
a machine with no torch, in seconds, and it has to be able to test adversaries
that a real checkpoint will not produce on demand - a model that says `soft` to
everything, one that inverts the incumbent, one that returns garbage.

But every one of those fakes is a claim about what the real thing does. This
closes that gap in the other direction: one checkpoint, actually on disk,
actually loaded, driven through `approvals.classify` and the real policy table.

    python tools/laya_live_gate_check.py

Exits non-zero if enabling `laya-union` removed a single approval card. NOT run
by pytest - it needs the model, ~800 MB resident and roughly a minute to load.

MEASURED 2026-09-22 on this machine: 0 lost, 6 added (the five outward actions
phrased with no marker word, which the substring scan cannot catch by design,
plus one of the three cases marked `arguable`), 0 of 13 firm hard cases left
ungated.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
# Keeps the import inert - this is a probe, not a running Friday.
os.environ.setdefault("FRIDAY_TESTING", "1")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from agent_friday.services import approvals, decisions, laya_backend  # noqa: E402
from severity_eval import CASES  # noqa: E402


def main() -> int:
    # The probe must not append to the real corpus. Rows written here were
    # never in front of a real action and would be indistinguishable later.
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="laya-live-check-"))
    decisions.log_path = lambda: tmp / "decisions.jsonl"

    laya_backend.register()
    print("  backends: %s" % decisions.available_backends())

    t0 = time.time()
    laya_backend._load_now()
    print("  real model loaded in %.1fs  ready=%s  error=%s"
          % (time.time() - t0, laya_backend.is_ready(), laya_backend._load_error))
    if not laya_backend.is_ready():
        print("\n  INCONCLUSIVE - the model did not load, so this proves nothing.")
        return 2

    lost, added, same = [], [], 0
    for text, label, _why, _arguable in CASES:
        os.environ["FRIDAY_DECISION_BACKEND"] = "keyword"
        kw = approvals.classify(text)
        os.environ["FRIDAY_DECISION_BACKEND"] = "laya-union"
        un = approvals.classify(text)
        if kw["gated"] and not un["gated"]:
            lost.append((text, kw["policy_class"], un["policy_class"]))
        elif un["gated"] and not kw["gated"]:
            added.append((text, label))
        else:
            same += 1

    print()
    print("  unchanged   : %d" % same)
    print("  cards ADDED : %d" % len(added))
    print("  cards LOST  : %d   <- the property; must be 0" % len(lost))
    for t, l in added:
        print("      + [%-4s] %s" % (l, t[:58]))
    for t, a, b in lost:
        print("      ! LOST  %s   (%s -> %s)" % (t[:48], a, b))

    firm_hard = [c for c in CASES if c[1] == "hard" and not c[3]]
    os.environ["FRIDAY_DECISION_BACKEND"] = "laya-union"
    ungated = [c[0] for c in firm_hard if not approvals.classify(c[0])["gated"]]
    print()
    print("  firm HARD cases left ungated by union: %d of %d"
          % (len(ungated), len(firm_hard)))
    for t in ungated:
        print("      ! %s" % t[:60])

    print()
    print("  RESULT: %s" % ("PASS - no approval card was removed" if not lost
                            else "FAIL - enabling laya-union removed a card"))
    return 1 if lost else 0


if __name__ == "__main__":
    raise SystemExit(main())
