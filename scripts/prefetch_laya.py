"""Pull the Laya checkpoint ahead of first use, so the gate is armed at boot.

WHY THIS IS NOT LEFT LAZY, unlike all-MiniLM-L6-v2. That model backs
conversation memory, and a first turn that is slightly less well recalled is a
small thing. This one decides whether an action needs the owner's sign-off, and
it is ON by default - so a fresh machine that leaves it lazy spends its first
minutes deciding with the keyword scan alone. That degradation is safe and it
announces itself, but it is avoidable, and a 808 MB download is better spent
during an install the user is already watching than in front of their first
approval card.

Exit code is ALWAYS 0. A machine with no network, a proxy, or a Hugging Face
outage must not fail an install over a feature whose absence costs nothing but
a second opinion.

    python scripts/prefetch_laya.py [--check]

--check reports whether the checkpoint is already present without downloading.
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

MODEL_ID = "convaiinnovations/laya"


def _present() -> bool:
    """True when the snapshot is already on disk, without touching the network."""
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(MODEL_ID, local_files_only=True)
        return True
    except Exception:
        return False


def main(argv) -> int:
    check_only = "--check" in argv

    if _present():
        print("  laya checkpoint: already present")
        return 0
    if check_only:
        print("  laya checkpoint: NOT present")
        return 0

    try:
        from huggingface_hub import snapshot_download
    except Exception as e:
        print("  laya checkpoint: skipped (huggingface_hub unavailable: %s)" % e)
        return 0

    print("  laya checkpoint: downloading ~808 MB ...")
    t0 = time.time()
    try:
        snapshot_download(MODEL_ID)
    except Exception as e:
        # The whole point of the fallback path: an absent checkpoint is a
        # degraded feature with a working substitute, never a failed install.
        print("  laya checkpoint: NOT downloaded (%s: %s)" % (type(e).__name__, e))
        print("  Friday will decide with the keyword scan alone until it is "
              "fetched; the Settings panel says so.")
        return 0
    print("  laya checkpoint: ready in %.0fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
