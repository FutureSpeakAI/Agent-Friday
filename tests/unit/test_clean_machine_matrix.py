"""The clean-machine matrix (north star §34.3-1, -2, -4, -5 and -8).

.github/workflows/clean-machine.yml installs the setup program on fresh Windows
VMs in the situations a release must survive beyond installer.yml's
fresh-install job: two Windows images, a memory cap, a non-English locale and
a standard (non-administrator) account with a non-ASCII profile path, and
writes one evidence record over every leg. These tests pin what the matrix
proves and that its evidence can never say "passed" over a leg that did not
run. The legs themselves run in CI on Windows.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "clean-machine.yml"
INSTALLER = REPO / ".github" / "workflows" / "installer.yml"
LEG_DIR = REPO / "packaging" / "windows" / "tests" / "clean-machine"
VERIFY = LEG_DIR / "verify-clean-machine.ps1"
AS_USER = LEG_DIR / "run-as-standard-user.ps1"
EVIDENCE = REPO / "scripts" / "clean_machine_evidence.py"


def _workflow() -> dict:
    assert WORKFLOW.exists(), "no clean-machine workflow"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    return wf.get("on") or wf.get(True) or {}       # YAML 1.1 reads a bare `on` as True


def _legs(wf: dict) -> dict:
    return {row["leg"]: row for row in wf["jobs"]["leg"]["strategy"]["matrix"]["include"]}


def _evidence_module():
    assert EVIDENCE.exists(), "no evidence script"
    spec = importlib.util.spec_from_file_location("clean_machine_evidence", EVIDENCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── what the matrix covers ──────────────────────────────────────────────────

def test_the_release_build_calls_it_and_the_release_waits_for_it():
    on = _triggers(_workflow())
    assert "workflow_call" in on and "workflow_dispatch" in on
    inst = yaml.safe_load(INSTALLER.read_text(encoding="utf-8"))
    job = inst["jobs"]["clean-machine"]
    assert job["uses"] == "./.github/workflows/clean-machine.yml"
    assert job["with"]["called"] is True and job["needs"] == "build"
    assert "clean-machine" in inst["jobs"]["release"]["needs"]
    # A beta tag only records (A19); a public tag must pass.
    ro = str(job["with"]["report_only"])
    assert "contains(github.ref_name, '-')" in ro
    assert "!startsWith(github.ref, 'refs/tags/v')" in ro and "||" in ro
    # The record ships with the release it proves (§34.3-8).
    rel = INSTALLER.read_text(encoding="utf-8").split("\n  release:", 1)[1]
    assert "name: clean-machine-evidence" in rel and "artifact/clean-machine-evidence.json" in rel


def test_called_by_the_release_it_tests_the_release_build_not_its_own():
    wf = _workflow()
    assert wf["jobs"]["build"]["if"] == "${{ inputs.called != true }}"
    assert "needs.build.result == 'skipped'" in wf["jobs"]["leg"]["if"]


def test_a_beta_only_records_and_a_missing_build_hash_fails():
    wf = _workflow()
    # A19: a failing leg does not block a beta; a public tag never sets report_only.
    assert wf["jobs"]["leg"]["continue-on-error"] == "${{ inputs.report_only == true }}"
    run = " ".join(s.get("run", "") for s in wf["jobs"]["evidence"]["steps"])
    assert "^[0-9a-f]{64}$" in run and "exit 1" in run


def test_the_standard_user_leg_leaves_nothing_behind():
    src = (REPO / "packaging/windows/tests/clean-machine/run-as-standard-user.ps1").read_text(encoding="utf-8-sig")
    fin = src.split("finally {", 1)[1]
    for step in ("Unregister-ScheduledTask -TaskName 'friday-standard-user-leg'",
                 "Remove-LocalUser", "Win32_UserProfile", "Remove-CimInstance",
                 "Remove-Item -LiteralPath $shared"):
        assert step in fin, step


def test_two_windows_images_get_the_full_fresh_install_check():        # §34.3-1
    legs = _legs(_workflow())
    assert legs["windows-2022"]["os"] == "windows-2022"
    assert legs["windows-2025"]["os"] == "windows-2025"
    assert legs["windows-2022"]["variant"] == legs["windows-2025"]["variant"] == "image"
    run = " ".join(s.get("run", "") for s in _workflow()["jobs"]["leg"]["steps"])
    assert "verify-clean-install.ps1" in run


def test_the_situations_beyond_a_plain_fresh_install_each_have_a_leg():
    legs = _legs(_workflow())
    assert legs["memory-cap"]["variant"] == "memory-cap"        # §34.3-2
    assert legs["locale"]["variant"] == "locale"                # §34.3-4
    assert legs["standard-user"]["variant"] == "standard-user"  # §34.3-5
    wf = _workflow()
    assert int(wf["env"]["MEMORY_CAP_MIB"]) > 0
    assert wf["jobs"]["leg"]["strategy"]["fail-fast"] is False, "one leg failing must not hide the others"


def test_the_locale_leg_is_not_english():
    steps = _workflow()["jobs"]["leg"]["steps"]
    locale = [s for s in steps if s.get("if") == "matrix.variant == 'locale'"]
    assert locale and "Set-Culture tr-TR" in locale[0]["run"]
    run = " ".join(s.get("run", "") for s in steps)
    assert "-ExpectCulture 'tr-TR'" in run
    verify = VERIFY.read_text(encoding="utf-8-sig")
    assert "$culture -ceq $ExpectCulture" in verify


def test_the_evidence_job_reads_every_leg_even_when_one_failed():   # §34.3-8
    job = _workflow()["jobs"]["evidence"]
    assert "leg" in job["needs"] and job["if"] == "${{ always() }}"
    run = " ".join(s.get("run", "") for s in job["steps"])
    assert "scripts/clean_machine_evidence.py" in run and "--installer-sha256" in run


def test_every_leg_in_the_workflow_is_one_the_evidence_expects():
    assert set(_legs(_workflow())) == set(_evidence_module().LEGS)


# ── the leg scripts ─────────────────────────────────────────────────────────

def _verify() -> bytes:
    return VERIFY.read_bytes() if VERIFY.exists() else b""


def test_the_leg_refuses_to_run_off_a_github_hosted_runner():
    src = _verify().decode("utf-8-sig")
    assert "runs only on a disposable GitHub-hosted runner" in src
    assert "$env:RUNNER_ENVIRONMENT -eq 'github-hosted'" in src


def test_the_leg_script_has_a_bom_and_always_writes_its_result():
    raw = _verify()
    assert raw.startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 reads a BOM-less file as ANSI"
    src = raw.decode("utf-8-sig")
    assert "detail = 'did not run'" in src, "every check starts as failed until it really runs"
    finally_block = src.split("finally {", 1)[1]
    assert "Save-Result" in finally_block and "unins000.exe" in finally_block


def test_the_memory_cap_is_a_job_object_and_reaching_it_is_a_failure():
    src = _verify().decode("utf-8-sig")
    assert "JOB_MEMORY = 0x200" in src and "KILL_ON_CLOSE = 0x2000" in src
    assert "[FridayJob]::Assign($job, $server.Handle)" in src
    assert "PeakJobMemoryUsed" in src
    assert "$peakMiB -lt [math]::Floor($MemoryCapMiB * 0.9)" in src


def test_the_standard_user_is_not_an_administrator_and_has_a_non_ascii_profile():
    src = AS_USER.read_bytes() if AS_USER.exists() else b""
    assert src.startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 needs the BOM to read the account name"
    text = src.decode("utf-8-sig")
    assert "S-1-5-32-545" in text and "S-1-5-32-544" in text   # Users, and the Administrators check
    assert "Remove-LocalUser" in text
    name = text.split('[string] $UserName = "', 1)[1].split('"', 1)[0]
    assert any(ord(c) > 127 for c in name), name
    verify = VERIFY.read_text(encoding="utf-8-sig")
    assert "if ($Variant -eq 'standard-user') { (-not $isAdmin) -and (-not $asciiProfile) }" in verify
    assert "$null -eq $exit" in text, "a leg whose exit code is unknown is not a pass"


# ── the evidence record ─────────────────────────────────────────────────────

HASH = "ab" * 32


def _leg_result(tmp: Path, leg: str, checks: dict | None = None, *, variant=None, image=None,
                digest=HASH, flat=False) -> None:
    mod = _evidence_module()
    want = mod.LEGS[leg]
    if checks is None:
        checks = {c: {"ok": True, "detail": ""} for c in want["checks"]}
    d = tmp / ("clean-machine-" + leg)
    d.mkdir(parents=True, exist_ok=True)
    body = dict(checks) if flat else {"variant": variant or want["variant"], "os": "Windows", "checks": checks}
    (d / "RESULT.json").write_text(json.dumps(body), encoding="utf-8-sig")
    (d / "LEG.json").write_text(json.dumps({"leg": leg, "image": image or want["image"] or "win25",
                                            "installer_sha256": digest}), encoding="utf-8-sig")


def _all_pass(tmp: Path, skip=()):
    mod = _evidence_module()
    for leg in mod.LEGS:
        if leg not in skip:
            _leg_result(tmp, leg, flat=mod.LEGS[leg]["variant"] == "image")
    return mod


def test_every_leg_passing_is_a_passing_record(tmp_path):
    mod = _all_pass(tmp_path)
    rec = mod.collect(tmp_path, "abc123")
    assert rec["ok"] is True and rec["commit"] == "abc123"
    assert set(rec["rows"]) == {"NS-34.3-1", "NS-34.3-2", "NS-34.3-4", "NS-34.3-5"}
    assert rec["rows"]["NS-34.3-1"]["legs"] == ["windows-2022", "windows-2025"]


def test_a_missing_leg_fails_the_record_and_its_row(tmp_path):
    mod = _all_pass(tmp_path, skip=("standard-user",))
    rec = mod.collect(tmp_path, "abc")
    assert rec["ok"] is False
    assert rec["legs"]["standard-user"]["missing"] is True
    assert rec["rows"]["NS-34.3-5"]["ok"] is False and rec["rows"]["NS-34.3-4"]["ok"] is True


def test_a_failed_check_names_itself(tmp_path):
    mod = _all_pass(tmp_path, skip=("locale",))
    checks = {c: {"ok": True} for c in mod.LEGS["locale"]["checks"]}
    checks["health"] = {"ok": False, "detail": "x"}
    _leg_result(tmp_path, "locale", checks)
    rec = mod.collect(tmp_path, "abc")
    assert rec["ok"] is False and "checks failed: health" in rec["legs"]["locale"]["problems"]


def test_a_leg_that_skipped_the_check_that_proves_its_row_is_not_a_pass(tmp_path):
    mod = _all_pass(tmp_path, skip=("memory-cap",))
    checks = {c: {"ok": True} for c in mod.LEGS["memory-cap"]["checks"] if c != "memory"}
    _leg_result(tmp_path, "memory-cap", checks)
    rec = mod.collect(tmp_path, "abc")
    assert rec["legs"]["memory-cap"]["ok"] is False
    assert any("memory" in p for p in rec["legs"]["memory-cap"]["problems"])


def test_a_leg_that_ran_another_variant_or_image_is_not_a_pass(tmp_path):
    mod = _all_pass(tmp_path, skip=("standard-user", "windows-2022"))
    _leg_result(tmp_path, "standard-user", variant="locale")
    _leg_result(tmp_path, "windows-2022", image="win25", flat=True)
    rec = mod.collect(tmp_path, "abc")
    assert rec["legs"]["standard-user"]["ok"] is False and rec["legs"]["windows-2022"]["ok"] is False


def test_every_leg_must_have_installed_the_release_build(tmp_path):
    mod = _all_pass(tmp_path, skip=("locale",))
    _leg_result(tmp_path, "locale", digest="cd" * 32)
    assert mod.collect(tmp_path, "abc")["ok"] is False
    mod = _all_pass(tmp_path / "again")
    assert mod.collect(tmp_path / "again", "abc", installer_sha256=HASH)["ok"] is True
    assert mod.collect(tmp_path / "again", "abc", installer_sha256="ef" * 32)["ok"] is False


def test_the_script_exits_1_unless_every_leg_passed_but_a_beta_only_records(tmp_path):
    mod = _all_pass(tmp_path, skip=("windows-2022",))
    out = tmp_path / "e" / "evidence.json"
    assert mod.main(["--dir", str(tmp_path), "--sha", "s", "--out", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["ok"] is False
    assert mod.main(["--dir", str(tmp_path), "--sha", "s", "--out", str(out), "--report-only"]) == 0
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["ok"] is False and rec["report_only"] is True
    _leg_result(tmp_path, "windows-2022", flat=True)
    assert mod.main(["--dir", str(tmp_path), "--sha", "s", "--out", str(out)]) == 0


@pytest.mark.parametrize("bad", ["not json", "[1, 2]"])
def test_an_unreadable_result_is_missing_not_passed(tmp_path, bad):
    mod = _all_pass(tmp_path, skip=("locale",))
    d = tmp_path / "clean-machine-locale"
    d.mkdir()
    (d / "RESULT.json").write_text(bad, encoding="utf-8")
    assert mod.collect(tmp_path, "s")["legs"]["locale"]["missing"] is True
