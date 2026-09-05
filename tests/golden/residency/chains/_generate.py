"""Regenerate the committed golden chain plans (headroom.md Phase 3.2).

    python tests/golden/residency/chains/_generate.py

Same rule as the sibling plan/_generate.py: a policy change that moves a
chain plan MUST move a committed file in the same commit -- the diff is the
review surface for a placement change that would otherwise be invisible.
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests import residency_fixtures as fx                  # noqa: E402
from agent_friday.services import residency_catalog as rc   # noqa: E402
from agent_friday.services import residency_policy as rp    # noqa: E402

# Isolate the measurement store so this always reflects the committed seed
# figures, never whatever happens to be recorded on the machine running it.
_tmp = pathlib.Path(tempfile.mkdtemp())
rc.store_path = lambda: _tmp / "m.json"
rc.reset_cache()
fx.seed_image_footprints()

here = pathlib.Path(__file__).parent
for name, profile in fx.ALL_PROFILES.items():
    plan = rp.plan_chain(profile, fx.catalog(profile), fx.CHAIN_STAGES,
                         resident=fx.CHAIN_RESIDENT[name])
    path = here / ("%s.json" % name)
    path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print("wrote %s" % path.name)
