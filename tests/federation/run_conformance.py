"""Run the Federation / Economy conformance checks from the pinned bundle.

    python -I tests/federation/run_conformance.py

Offline, standard library plus `cryptography`. Isolated mode (-I) keeps the working
directory, the script directory and PYTHONPATH off sys.path, so nothing next to a check can
stand in for a module it imports; the bundle's own tools directory is added here on purpose,
after every file has matched its pinned SHA256. The checks verify public fixtures and the
bundle's reference verifiers; they never touch a wallet, a key or the Friday tree.
"""
import hashlib
import runpy
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # the checks must not leave files in the pinned bundle
HERE = Path(__file__).resolve().parent
BUNDLE = HERE / "bundle"
# Pinned: the manifest lists every file's SHA256; this pins the manifest itself.
MANIFEST_SHA256 = "fb2600545969ddedf8189352adeb007c07f73fdff7b8a2e19c7894c0f5f68a43"
CHECKS = ("tools/check_wire.py", "fixtures/check_economy.py", "tools/verify_registry_vector.py",
          "tools/check_linked_stage.py", "tools/check_project_merge.py")


def pinned_files():
    """{relative path: sha256} from the manifest; exits if the manifest or a file changed."""
    raw = (BUNDLE / "SHA256SUMS.txt").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise SystemExit("conformance bundle manifest changed: refusing to run")
    pins = {}
    for line in raw.decode("utf-8").splitlines():
        digest, _, name = line.partition("  ")
        pins[name.strip()] = digest
    bad = [n for n, d in pins.items()
           if hashlib.sha256((BUNDLE / n).read_bytes()).hexdigest() != d]
    if bad:
        raise SystemExit(f"conformance bundle file changed: {', '.join(bad)}")
    on_disk = {p.relative_to(BUNDLE).as_posix() for p in BUNDLE.rglob("*")
               if p.is_file() and p.name not in ("SHA256SUMS.txt", ".gitattributes")
               and "__pycache__" not in p.parts}
    stray = sorted(on_disk - set(pins))
    if stray:
        raise SystemExit(f"conformance bundle holds unpinned file(s): {', '.join(stray)}")
    return pins


def main() -> int:
    pinned_files()
    sys.path.insert(0, str(BUNDLE / "tools"))
    failed = []
    for rel in CHECKS:
        print(f"== {rel}", flush=True)
        try:
            runpy.run_path(str(BUNDLE / rel), run_name="__main__")
        except SystemExit as e:
            if e.code not in (None, 0):
                failed.append(rel)
        except Exception as e:  # a check that raises has not passed
            print(f"{type(e).__name__}: {e}")
            failed.append(rel)
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print(f"all {len(CHECKS)} conformance checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
