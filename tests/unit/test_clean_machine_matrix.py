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

def test_it_runs_on_every_version_tag_and_by_hand():
    on = _triggers(_workflow())
    assert "v*" in on["push"]["tags"]
    assert "workflow_dispatch" in on


def test_two_windows_images_are_proven():                      # §34.3-1
    legs = _legs(_workflow())
    assert legs["windows-2022"]["os"] == "windows-2022"
    assert legs["windows-2025"]["os"] == "windows-2025"


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


def test_the_evidence_job_reads_every_leg_even_when_one_failed():   # §34.3-8
    job = _workflow()["jobs"]["evidence"]
    assert job["needs"] == "leg" and job["if"] == "always()"
    run = " ".join(s.get("run", "") for s in job["steps"])
    assert "scripts/clean_machine_evidence.py" in run


def test_every_leg_in_the_workflow_is_one_the_evidence_expects():
    assert set(_legs(_workflow())) == set(_evidence_module().LEGS)


# ── the leg scripts ─────────────────────────────────────────────────────────

def test_the_leg_refuses_to_run_off_a_ci_runner():
    src = VERIFY.read_text(encoding="utf-8") if VERIFY.exists() else ""
    assert "runs only on a disposable CI runner" in src
    assert "$env:GITHUB_ACTIONS -eq 'true'" in src


def test_the_memory_cap_is_a_job_object_over_everything_the_server_starts():
    src = VERIFY.read_text(encoding="utf-8") if VERIFY.exists() else ""
    assert "JOB_MEMORY = 0x200" in src and "KILL_ON_CLOSE = 0x2000" in src
    assert "[FridayJob]::Assign($job, $server.Handle)" in src
    assert "PeakJobMemoryUsed" in src


def test_the_standard_user_is_not_an_administrator_and_has_a_non_ascii_profile():
    src = AS_USER.read_bytes() if AS_USER.exists() else b""
    assert src.startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 needs the BOM to read the account name"
    text = src.decode("utf-8-sig")
    assert "S-1-5-32-545" in text and "S-1-5-32-544" in text   # Users, and the Administrators check
    assert "Remove-LocalUser" in text
    name = text.split('[string] $UserName = "', 1)[1].split('"', 1)[0]
    assert any(ord(c) > 127 for c in name), name
    verify = VERIFY.read_text(encoding="utf-8")
    assert "'standard-user' { (-not $isAdmin) -and (-not $asciiProfile) }" in verify


# ── the evidence record ─────────────────────────────────────────────────────

def _leg_result(tmp: Path, leg: str, checks: dict) -> None:
    d = tmp / ("clean-machine-" + leg)
    d.mkdir(parents=True)
    (d / "RESULT.json").write_text(json.dumps({"variant": leg, "image": "win25", "os": "Windows",
                                               "checks": checks}), encoding="utf-8-sig")


def _all_pass(tmp: Path, skip=()):
    mod = _evidence_module()
    for leg in mod.LEGS:
        if leg not in skip:
            _leg_result(tmp, leg, {"install": {"ok": True, "detail": ""}, "health": {"ok": True, "detail": ""}})
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
    _leg_result(tmp_path, "locale", {"install": {"ok": True}, "health": {"ok": False, "detail": "x"}})
    rec = mod.collect(tmp_path, "abc")
    assert rec["ok"] is False and rec["legs"]["locale"]["failed_checks"] == ["health"]


def test_a_leg_with_no_checks_is_not_a_pass(tmp_path):
    mod = _all_pass(tmp_path, skip=("memory-cap",))
    _leg_result(tmp_path, "memory-cap", {})
    assert mod.collect(tmp_path, "abc")["legs"]["memory-cap"]["ok"] is False


def test_the_script_exits_1_unless_every_leg_passed(tmp_path):
    mod = _all_pass(tmp_path, skip=("windows-2022",))
    out = tmp_path / "e" / "evidence.json"
    assert mod.main(["--dir", str(tmp_path), "--sha", "s", "--out", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["ok"] is False
    _leg_result(tmp_path, "windows-2022", {"install": {"ok": True}})
    assert mod.main(["--dir", str(tmp_path), "--sha", "s", "--out", str(out)]) == 0


@pytest.mark.parametrize("bad", ["not json", "[1, 2]"])
def test_an_unreadable_result_is_missing_not_passed(tmp_path, bad):
    mod = _all_pass(tmp_path, skip=("locale",))
    d = tmp_path / "clean-machine-locale"
    d.mkdir()
    (d / "RESULT.json").write_text(bad, encoding="utf-8")
    assert mod.collect(tmp_path, "s")["legs"]["locale"]["missing"] is True
