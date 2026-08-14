"""Regenerate the committed golden plans.

    python tests/golden/residency/_generate.py

A policy change that moves a plan MUST move a committed file in the same
commit. That is the whole point: the diff is the review surface for a change
in placement behaviour, which is otherwise invisible.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests import residency_fixtures as fx            # noqa: E402
from agent_friday.services import residency_policy as rp   # noqa: E402

here = pathlib.Path(__file__).parent
for name, profile in fx.ALL_PROFILES.items():
    plan = rp.plan(profile, fx.catalog(profile))
    path = here / ("%s.json" % name)
    path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print("wrote %s" % path.name)
