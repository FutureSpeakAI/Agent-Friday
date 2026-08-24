"""Restore the settings the 2026-08-24 BOM incident erased. Run when ready.

WHY THIS IS A SCRIPT AND NOT A DONE DEED
----------------------------------------
At 13:08:58 `_save_settings` persisted DEFAULT_SETTINGS over Stephen's 83 keys
(see docs/audits/workflow-run-forensics-2026-08-24.md, addendum A, and the
commit that fixed both halves). His real values survive in
`~/.friday/settings.json.bak-preheal`, taken at 12:39.

Restoring was deliberately NOT done automatically, because three of the lost
keys have no safe default answer while a run is in flight:

  * `model_routing.mode` is currently the factory `cloud_only`. Putting back
    `local_preferred` is cheaper and is what he actually chose -- and it would
    move an in-flight agent off a frontier model onto gemma4:12b mid-task,
    which could wreck the very run he is evaluating. Cheaper is not obviously
    better here.
  * `orchestrator_model` has THREE plausible answers and they conflict:
    `claude-opus-5` (what he set, per bak-preheal), `gemma4:12b` (what commit
    8a30831's heal deliberately moved it to, so that local turns stop 404ing
    and escalating), or `claude-sonnet-5` (where the reset left it).
  * `capability_routing.heavy_hitter` must NOT come back as `gemma4:26b` from
    the 12:39 backup. That backup predates the heal; the 26b is 16.95 GB on a
    12 GB card with no live endpoint, and restoring it would undo the other
    agent's morning.

So this script restores only what is unambiguous -- preferences that do not
decide which model answers a turn -- and prints the three contested keys with
their candidates rather than picking for him.

    python scripts/restore_settings_2026-08-24.py            # show the plan
    python scripts/restore_settings_2026-08-24.py --apply    # write it

A timestamped backup is taken before any write.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

FRIDAY = Path.home() / ".friday"
LIVE = FRIDAY / "settings.json"
BACKUP = FRIDAY / "settings.json.bak-preheal"

# Keys restored without asking: none of them change which model answers a turn.
SAFE_KEYS = [
    "creative_model",
    "music_model",
    "voice_model",
    "local_voice_asr_model",
    "local_voice_gpu_asr_model",
    "user_modeling",
    "custom_models",
]
SAFE_ROUTING = ["creative_image", "creative_video", "creative_music", "voice"]

# Restored to the HEAL's value, not the backup's -- see the docstring.
HEALED_HEAVY = {"provider": "llama-cpp-local", "model": "gemma4:12b"}

CONTESTED = {
    "model_routing.mode": (
        "cloud_only (now)  |  local_preferred (his choice, per bak-preheal)",
        "local_preferred is cheaper and is what he set; switching mid-run moves "
        "an in-flight agent onto gemma4:12b, which may ruin the run.",
    ),
    "orchestrator_model / capability_routing.reasoning": (
        "claude-sonnet-5 (now)  |  claude-opus-5 (his choice)  |  "
        "gemma4:12b (the heal's choice)",
        "All three are defensible. Opus 5 is what he set for the "
        "self-improvement test; gemma4:12b is what stops local turns 404ing.",
    ),
    "capability_routing.heavy_hitter": (
        "'' (now)  |  gemma4:12b (the heal)  |  gemma4:26b (bak-preheal - DO NOT)",
        "This script proposes the heal's gemma4:12b. The 26b in the backup is "
        "unreachable and predates the fix.",
    ),
}


def load(p: Path) -> dict:
    # utf-8-sig on purpose. Reading these two files as plain utf-8 is the bug
    # this whole script exists to clean up after.
    return json.loads(p.read_text(encoding="utf-8-sig"))


def main() -> int:
    apply = "--apply" in sys.argv
    if not LIVE.exists():
        print("no settings.json at %s" % LIVE)
        return 1
    if not BACKUP.exists():
        print("no backup at %s -- nothing to restore from" % BACKUP)
        return 1

    live, old = load(LIVE), load(BACKUP)
    plan: list[tuple[str, object, object]] = []

    for k in SAFE_KEYS:
        if k in old and live.get(k) != old[k]:
            plan.append((k, live.get(k), old[k]))

    lcr = live.get("capability_routing") or {}
    ocr = old.get("capability_routing") or {}
    for cap in SAFE_ROUTING:
        if cap in ocr and lcr.get(cap) != ocr[cap]:
            plan.append(("capability_routing.%s" % cap, lcr.get(cap), ocr[cap]))

    if lcr.get("heavy_hitter") != HEALED_HEAVY:
        plan.append(("capability_routing.heavy_hitter",
                     lcr.get("heavy_hitter"), HEALED_HEAVY))

    print("RESTORE PLAN (%s)" % ("APPLYING" if apply else "dry run"))
    print("  from %s" % BACKUP.name)
    if not plan:
        print("  nothing to do -- the safe keys already match.")
    for k, was, now in plan:
        print("    %-42s %r  ->  %r" % (k, was, now))

    print()
    print("NOT TOUCHED -- these are yours to decide:")
    for k, (options, why) in CONTESTED.items():
        print("  %s" % k)
        print("      options: %s" % options)
        print("      %s" % why)

    if not apply:
        print()
        print("Dry run. Re-run with --apply to write.")
        return 0
    if not plan:
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%S")
    dest = FRIDAY / ("settings.json.bak-restore-%s" % stamp)
    shutil.copy2(LIVE, dest)
    print()
    print("  backed up live settings -> %s" % dest.name)

    for k, _was, now in plan:
        if k.startswith("capability_routing."):
            live.setdefault("capability_routing", {})[k.split(".", 1)[1]] = now
        else:
            live[k] = now

    # Same shape the app writes: temp file, then replace. No BOM.
    tmp = LIVE.with_suffix(".restore-tmp")
    tmp.write_text(json.dumps(live, indent=2), encoding="utf-8")
    tmp.replace(LIVE)
    print("  wrote %s (%d keys)" % (LIVE.name, len(live)))
    print("  the running server picks this up within its 2s settings cache TTL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
