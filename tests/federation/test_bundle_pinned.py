"""The Federation / Economy conformance bundle (tests/federation/bundle) is the one Stephen
sent on 2026-11-06, byte for byte, and its five checks pass under `python -I`.

Tampering with a fixture, schema or tool, adding an unpinned file, or editing the manifest
fails here (and in the federation-conformance CI job, which runs the same runner). The
checks prove the public fixtures and the bundle's reference verifiers agree; they do not
run Friday code (NS-40 rows that name them stay "fixtures only" until Friday code does).
"""
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run_conformance.py"
BUNDLE = HERE / "bundle"
EXPECTED_FILES = {
    "fixtures/check_economy.py", "fixtures/economy-vectors.json", "fixtures/linked-stage.json",
    "fixtures/registry-v1.json", "fixtures/wire-v2.json",
    "schema/economic-schedule-v1.schema.json", "schema/peer-event-v2.schema.json",
    "schema/stage-policy-v1.schema.json", "schema/terms-v2.schema.json",
    "tools/check_linked_stage.py", "tools/check_project_merge.py", "tools/check_wire.py",
    "tools/verify_registry_vector.py",
}


def _runner_module():
    assert RUNNER.is_file(), "the conformance runner and its pinned bundle are not in the repo"
    spec = importlib.util.spec_from_file_location("fed_run_conformance", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_file_matches_its_pin_and_nothing_else_is_there():
    pins = _runner_module().pinned_files()
    assert set(pins) == EXPECTED_FILES


def test_the_manifest_is_itself_pinned(monkeypatch):
    mod = _runner_module()
    monkeypatch.setattr(mod, "MANIFEST_SHA256", "0" * 64)
    with pytest.raises(SystemExit, match="manifest changed"):
        mod.pinned_files()


def test_a_changed_fixture_or_an_extra_file_is_refused(tmp_path, monkeypatch):
    mod = _runner_module()
    copy = tmp_path / "bundle"
    for p in BUNDLE.rglob("*"):
        if p.is_file():
            dest = copy / p.relative_to(BUNDLE)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(p.read_bytes())
    monkeypatch.setattr(mod, "BUNDLE", copy)
    target = copy / "fixtures" / "economy-vectors.json"
    original = target.read_bytes()
    target.write_bytes(original + b" ")
    with pytest.raises(SystemExit, match="file changed.*economy-vectors"):
        mod.pinned_files()
    target.write_bytes(original)
    (copy / "fixtures" / "extra.json").write_text("{}")
    with pytest.raises(SystemExit, match="unpinned"):
        mod.pinned_files()


def test_the_manifest_digest_in_the_runner_is_the_manifest_on_disk():
    assert hashlib.sha256((BUNDLE / "SHA256SUMS.txt").read_bytes()).hexdigest() ==         _runner_module().MANIFEST_SHA256


def test_the_five_checks_pass_under_isolated_python():
    pytest.importorskip("cryptography")
    assert RUNNER.is_file(), "the conformance runner and its pinned bundle are not in the repo"
    done = subprocess.run([sys.executable, "-I", str(RUNNER)], capture_output=True, text=True,
                          timeout=240, cwd=str(HERE.parent))
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    assert "all 5 conformance checks passed" in done.stdout
