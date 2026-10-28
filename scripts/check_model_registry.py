"""Model and capability registry validation (north star §34.2-16).

    python scripts/check_model_registry.py

Prints every inconsistency between the shipped provider registry, the default
capability seats, seat_policy's local names, the brain ladder and the download
shortlist, and exits 1 if there is any. The rules are in
src/agent_friday/services/registry_check.py; CI runs them as
tests/unit/test_model_registry_is_valid.py.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("FRIDAY_TESTING", "1")
# Importing the app writes into its home; never the person's own.
import tempfile  # noqa: E402
os.environ.setdefault("FRIDAY_HOME", tempfile.mkdtemp(prefix="friday-check-"))


def main() -> int:
    from agent_friday.services.registry_check import problems
    found = problems()
    for p in found:
        print("[model-registry] " + p)
    if found:
        print("[model-registry] FAIL - %d problem(s)" % len(found))
        return 1
    print("[model-registry] OK - providers, seats, local names, ladder and shortlist agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
