"""Show (or cancel) the R0 experiment.

    python tools/task_eval/r0.py            # where R0 stands
    python tools/task_eval/r0.py --cancel   # stop it for good

R0 does not run from here. It runs only inside Friday's nightly practice-trials
job (services/r0_nightly), which is visible on the desktop, yields to the
owner, runs on AC power only and inside the nightly testing cap. It schedules
itself; this tool reads its state under FRIDAY_HOME and prints the report path
once it has finished.
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cancel", action="store_true", help="stop R0 for good")
    opts = ap.parse_args(argv)
    from agent_friday.services import r0_nightly
    st = r0_nightly.cancel("cancelled from tools/task_eval/r0.py") if opts.cancel else r0_nightly.state()
    print(json.dumps(st or {"status": "not scheduled yet (the nightly job schedules it)"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
