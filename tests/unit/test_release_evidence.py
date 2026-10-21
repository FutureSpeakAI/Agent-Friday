"""A release says what it was proven on, and is proven on more than the runner's default.

Before this change:
  * tests/integration never ran in CI, so the office end-to-end test (the real pinned
    document engine) and the cross-service flows were only ever run by hand;
  * the installer was checked on one kind of machine only: English formats, an ASCII temp
    folder, and the runner's administrator account;
  * the release carried the installer and its checksum but no record of what it was tested
    on; that lived in Actions logs, which expire.

Now tests.yml runs the integration suite (with the pinned binary put in place by the
installer's own code on Windows, and a skip turned into a failure there), installer.yml
adds a German-formats leg and a standard-user leg, and the release job writes
release-evidence.json (scripts/release_evidence.py) from the gate's record
(scripts/release_gate.py --record) and every leg's RESULT.json and attaches it.
Nothing here talks to GitHub.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WF = REPO / ".github" / "workflows"
TESTS_PS = REPO / "packaging" / "windows" / "tests"
SHA = "c" * 40


def _wf(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def _load(name: str):
    path = REPO / "scripts" / f"{name}.py"
    assert path.exists(), f"scripts/{name}.py is missing"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _runs(job: dict) -> str:
    return "\n".join(str(s.get("run") or "") for s in job.get("steps") or [])


# --- tests.yml: the integration suite runs, and cannot pass by skipping ----------------

def test_the_integration_suite_runs_in_ci():
    job = _wf("tests.yml")["jobs"]["pytest"]
    steps = job["steps"]
    runs = [s for s in steps if "pytest tests/integration" in str(s.get("run") or "")]
    assert runs, "tests/integration is not run by tests.yml"
    step = runs[0]
    assert "cancelled()" in str(step.get("if", "")) or "if" not in step
    assert "FRIDAY_REQUIRE_OFFICECLI" in (step.get("env") or {})
    assert "Windows" in str(step["env"]["FRIDAY_REQUIRE_OFFICECLI"])


def test_ci_installs_the_document_engine_with_the_installers_own_code():
    steps = _wf("tests.yml")["jobs"]["pytest"]["steps"]
    install = [s for s in steps if "Install-OfficeCli" in str(s.get("run") or "")]
    assert install, "nothing puts the pinned officecli in place before the integration suite"
    run = install[0]["run"]
    assert "packaging\\windows\\lib\\OfficeCli.ps1" in run
    assert "throw" in run
    names = [s.get("name") for s in steps]
    integ = next(i for i, s in enumerate(steps) if "pytest tests/integration" in str(s.get("run") or ""))
    assert names.index(install[0]["name"]) < integ


def test_the_office_tests_fail_rather_than_skip_when_required(monkeypatch):
    src = (REPO / "tests" / "integration" / "test_office_end_to_end.py").read_text(encoding="utf-8")
    assert "FRIDAY_REQUIRE_OFFICECLI" in src
    assert "not _REQUIRED and not _REAL_BINARY.exists()" in src


# --- installer.yml: clean-machine legs ------------------------------------------------

def test_the_clean_machine_legs_exist_and_gate_the_release():
    jobs = _wf("installer.yml")["jobs"]
    cm = jobs.get("clean-machine")
    assert cm, "no clean-machine job"
    assert set(cm["strategy"]["matrix"]["leg"]) >= {"locale-de-DE", "standard-user"}
    assert "clean-machine" in jobs["release"]["needs"]
    runs = _runs(cm)
    assert "Set-Culture de-DE" in runs
    assert "-ExpectCulture de-DE" in runs
    assert "run-as-standard-user.ps1" in runs
    assert not cm.get("continue-on-error")
    for s in cm["steps"]:
        assert not s.get("continue-on-error"), s.get("name")


def test_the_verify_script_knows_the_legs():
    src = (TESTS_PS / "verify-clean-install.ps1").read_text(encoding="utf-8")
    assert "[string] $ExpectCulture" in src and "[switch] $StandardUser" in src
    assert "Check 'standard-user'" in src and "Check 'locale'" in src
    assert "MACHINE.json" in src
    # A skipped check is named as skipped, never silently passed.
    assert "skipped = $true" in src
    # Administrators counted even when UAC filters it to deny-only.
    assert "S-1-5-32-544" in src


def test_the_standard_user_runs_as_itself_not_with_borrowed_environment():
    src = (TESTS_PS / "run-as-standard-user.ps1").read_text(encoding="utf-8")
    assert "GITHUB_ACTIONS" in src
    assert "Register-ScheduledTask" in src and "-RunLevel Limited" in src
    assert "Start-Process -Credential" not in src.split("#>", 1)[1]
    assert "S-1-5-32-544" in src  # refuses if the account is an administrator
    assert "SeBatchLogonRight" in src
    assert "-StandardUser" in src
    # The password is generated, never a constant, and never printed.
    assert "RandomNumberGenerator" in src
    assert "Write-Host $plain" not in src and "Write-Host \"$plain" not in src


# --- the release carries its evidence -------------------------------------------------

def test_the_release_writes_and_attaches_the_evidence_record():
    jobs = _wf("installer.yml")["jobs"]
    gate_runs = _runs(jobs["tests-green"])
    assert "--record" in gate_runs
    rel = jobs["release"]
    steps = rel["steps"]
    ev = [i for i, s in enumerate(steps) if "scripts/release_evidence.py" in str(s.get("run") or "")]
    pub = [i for i, s in enumerate(steps) if "gh release create" in str(s.get("run") or "")]
    assert ev and pub and ev[0] < pub[0]
    run = steps[ev[0]]["run"]
    for leg in ("fresh-install-evidence", "clean-machine-evidence-locale-de-DE",
                "clean-machine-evidence-standard-user"):
        assert f"--require {leg}" in run
    assert "--require-prefix upgrade-evidence-" in run
    assert "artifact/release-evidence.json" in steps[pub[0]]["run"]
    assert not steps[ev[0]].get("continue-on-error")


def _gate_rec(sha=SHA, verdict="green"):
    return {"verdict": verdict, "sha": sha, "workflows": {"tests.yml": [{"id": 1}]}}


def _leg(root: Path, name: str, result, machine=None):
    d = root / name
    d.mkdir(parents=True)
    if result is not None:
        (d / "RESULT.json").write_text(json.dumps(result), encoding="utf-8")
    if machine is not None:
        (d / "MACHINE.json").write_text(json.dumps(machine), encoding="utf-8")


def _installer(tmp: Path, good=True) -> Path:
    exe = tmp / "AgentFriday-Setup-1.2.3.exe"
    exe.write_bytes(b"MZ setup")
    h = hashlib.sha256(b"MZ setup").hexdigest() if good else "0" * 64
    (tmp / (exe.name + ".sha256")).write_text(f"{h}  {exe.name}\n", encoding="utf-8")
    return exe


def _all_legs(ev: Path):
    ok = {"install": {"ok": True, "detail": ""}, "health": {"ok": True, "detail": ""}}
    _leg(ev, "fresh-install-evidence", ok, {"ram_gb": 16})
    _leg(ev, "clean-machine-evidence-locale-de-DE", dict(ok, locale={"ok": True, "detail": "de-DE"}))
    _leg(ev, "clean-machine-evidence-standard-user",
         dict(ok, address={"ok": True, "skipped": True, "detail": "needs admin"}))
    _leg(ev, "upgrade-evidence-v1.2.0-beta.1", {"vault_decrypted": True, "label": "x"})


REQ = ["fresh-install-evidence", "clean-machine-evidence-locale-de-DE",
       "clean-machine-evidence-standard-user"]


def test_a_complete_green_release_gets_a_record(tmp_path):
    m = _load("release_evidence")
    ev = tmp_path / "evidence"
    _all_legs(ev)
    rec, problems = m.assemble(ev, _gate_rec(), SHA, "v1.2.3", _installer(tmp_path), REQ,
                               ["upgrade-evidence-"])
    assert problems == []
    assert rec["commit"] == SHA and rec["installer"]["sha256"] == hashlib.sha256(b"MZ setup").hexdigest()
    assert rec["skipped_checks"] == {"clean-machine-evidence-standard-user": ["address"]}
    assert rec["legs"]["fresh-install-evidence"]["machine"] == {"ram_gb": 16}
    assert any("RAM" in s for s in rec["not_covered"])


def test_a_missing_leg_refuses(tmp_path):
    m = _load("release_evidence")
    ev = tmp_path / "evidence"
    _all_legs(ev)
    import shutil
    shutil.rmtree(ev / "clean-machine-evidence-standard-user")
    _, problems = m.assemble(ev, _gate_rec(), SHA, "v1", None, REQ, ["upgrade-evidence-"])
    assert any("standard-user" in p for p in problems)


def test_a_failed_check_refuses(tmp_path):
    m = _load("release_evidence")
    ev = tmp_path / "evidence"
    _all_legs(ev)
    (ev / "clean-machine-evidence-locale-de-DE" / "RESULT.json").write_text(
        json.dumps({"locale": {"ok": False, "detail": "en-US"}}), encoding="utf-8")
    _, problems = m.assemble(ev, _gate_rec(), SHA, "v1", None, REQ, ["upgrade-evidence-"])
    assert any("locale" in p for p in problems)


def test_no_upgrade_leg_refuses_and_a_vault_that_did_not_open_refuses(tmp_path):
    m = _load("release_evidence")
    ev = tmp_path / "evidence"
    _all_legs(ev)
    (ev / "upgrade-evidence-v1.2.0-beta.1" / "RESULT.json").write_text(
        json.dumps({"vault_decrypted": False}), encoding="utf-8")
    _, problems = m.assemble(ev, _gate_rec(), SHA, "v1", None, REQ, ["upgrade-evidence-"])
    assert any("vault_decrypted" in p for p in problems)
    _, problems = m.assemble(ev, _gate_rec(), SHA, "v1", None, REQ, ["nothing-like-this-"])
    assert any("nothing-like-this-" in p for p in problems)


def test_a_required_leg_with_no_result_refuses(tmp_path):
    m = _load("release_evidence")
    ev = tmp_path / "evidence"
    _all_legs(ev)
    (ev / "fresh-install-evidence" / "RESULT.json").unlink()
    _, problems = m.assemble(ev, _gate_rec(), SHA, "v1", None, REQ, ["upgrade-evidence-"])
    assert any("fresh-install-evidence" in p for p in problems)


def test_another_jobs_evidence_in_its_own_shape_is_recorded_not_judged(tmp_path):
    m = _load("release_evidence")
    ev = tmp_path / "evidence"
    _all_legs(ev)
    _leg(ev, "some-other-evidence", {"whatever": 1})
    rec, problems = m.assemble(ev, _gate_rec(), SHA, "v1", None, REQ, ["upgrade-evidence-"])
    assert problems == []
    assert rec["legs"]["some-other-evidence"]["passed"] is None


@pytest.mark.parametrize("gate", [None, _gate_rec(verdict="red"), _gate_rec(sha="d" * 40)])
def test_no_green_gate_record_for_this_commit_refuses(tmp_path, gate):
    m = _load("release_evidence")
    ev = tmp_path / "evidence"
    _all_legs(ev)
    _, problems = m.assemble(ev, gate, SHA, "v1", None, REQ, ["upgrade-evidence-"])
    assert any("gate" in p for p in problems)


def test_a_checksum_that_does_not_match_refuses(tmp_path):
    m = _load("release_evidence")
    ev = tmp_path / "evidence"
    _all_legs(ev)
    _, problems = m.assemble(ev, _gate_rec(), SHA, "v1", _installer(tmp_path, good=False), REQ,
                             ["upgrade-evidence-"])
    assert any("SHA-256" in p for p in problems)


def test_the_command_writes_nothing_when_it_refuses(tmp_path):
    m = _load("release_evidence")
    ev = tmp_path / "evidence"
    _all_legs(ev)
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps(_gate_rec(verdict="red")), encoding="utf-8")
    out = tmp_path / "release-evidence.json"
    args = ["--dir", str(ev), "--gate", str(gate), "--sha", SHA, "--tag", "v1", "--out", str(out)]
    assert m.main(args) == 1 and not out.exists()
    gate.write_text(json.dumps(_gate_rec()), encoding="utf-8")
    assert m.main(args) == 0 and json.loads(out.read_text(encoding="utf-8"))["tag"] == "v1"


# --- the gate writes what its green rested on ------------------------------------------

def _run(i, conclusion="success", sha=SHA, event="push"):
    return {"id": i, "head_sha": sha, "event": event, "status": "completed",
            "conclusion": conclusion, "html_url": f"https://x/{i}"}


def test_the_gate_records_its_green_runs_and_nothing_on_red(monkeypatch, tmp_path):
    g = _load("release_gate")
    out = tmp_path / "gate.json"
    monkeypatch.setattr(g, "fetch_runs", lambda repo, wf, sha: [_run(1), _run(2, event="pull_request")])
    assert g.main(["--repo", "o/r", "--sha", SHA, "--record", str(out)]) == 0
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["verdict"] == "green" and rec["sha"] == SHA
    assert [r["id"] for r in rec["workflows"]["tests.yml"]] == [1]   # the PR run did not count
    out.unlink()
    monkeypatch.setattr(g, "fetch_runs", lambda repo, wf, sha: [_run(1, "failure")])
    assert g.main(["--repo", "o/r", "--sha", SHA, "--record", str(out)]) == 1
    assert not out.exists()
