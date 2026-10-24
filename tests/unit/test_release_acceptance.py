"""A public non-beta release waits for the section 34 checklist (north-star A26).

``scripts/release_acceptance.py`` refuses a version tag that is not a beta unless every
row of ``docs/release/acceptance-34.json`` passed with its evidence, or is exempted by the
amendment that covers that very row. Betas pass it (A19). These tests drive the decision
with synthetic checklists and read the installer workflow; nothing talks to GitHub.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CHECKLIST = REPO / "docs" / "release" / "acceptance-34.json"
WORKFLOW = REPO / ".github" / "workflows" / "installer.yml"


def _gate():
    path = REPO / "scripts" / "release_acceptance.py"
    assert path.exists(), "scripts/release_acceptance.py is missing: nothing holds a non-beta release"
    spec = importlib.util.spec_from_file_location("release_acceptance_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _all_green(version="1.0.0"):
    """The real checklist with every open row recorded as passed, for ``version``."""
    doc = json.loads(CHECKLIST.read_text(encoding="utf-8"))
    for row in doc["rows"]:
        if row["status"] == "open":
            row["status"], row["evidence"] = "pass", "synthetic evidence"
    doc["verified_for"] = version
    return doc


def _row(doc, rid):
    return next(r for r in doc["rows"] if r["id"] == rid)


# --- the tag ------------------------------------------------------------------------------

@pytest.mark.parametrize("tag", ["v1.2.0-beta.3", "v1.0.0-beta.1", "refs/tags/v2.0.0-beta.12"])
def test_a_beta_is_exempt(tag):
    assert _gate().parse_tag(tag)[1] is True


@pytest.mark.parametrize("tag", ["v1.0.0", "v1.0.0-rc.1", "v1.0.0-alpha.1", "v1.0.0-beta",
                                 "v1.0.0-beta.1-hotfix", "v1.0.0-BETA.1", "v1.0.0-prebeta.1"])
def test_anything_else_is_a_public_release(tag):
    assert _gate().parse_tag(tag)[1] is False


@pytest.mark.parametrize("tag", ["1.0.0", "v1.0", "release-1", ""])
def test_a_non_version_tag_is_a_usage_error(tag):
    assert _gate().main(["--tag", tag]) == 3


def test_a_beta_passes_even_with_no_checklist(tmp_path):
    assert _gate().main(["--tag", "v1.3.0-beta.1", "--checklist", str(tmp_path / "none.json")]) == 0


# --- the checklist as it stands -----------------------------------------------------------

def test_the_checklist_names_every_section_34_row_once():
    g = _gate()
    doc = json.loads(CHECKLIST.read_text(encoding="utf-8"))
    ids = [r["id"] for r in doc["rows"]]
    assert ids == list(g.ROWS)
    assert len(ids) == 114


def test_today_a_public_release_is_refused(capsys):
    assert _gate().main(["--tag", "v1.0.0"]) == 1
    out = capsys.readouterr().out
    assert "REFUSED v1.0.0" in out and "not passed" in out


def test_the_exemptions_in_the_checklist_are_the_amendments_own():
    g = _gate()
    doc = json.loads(CHECKLIST.read_text(encoding="utf-8"))
    exempt = {r["id"]: (r["status"], r.get("amendment")) for r in doc["rows"] if r["status"] not in ("open", "pass", "fail")}
    want = {rid: (status, a) for a, (status, rows) in g.EXEMPTIONS.items() for rid in rows}
    assert exempt == want


# --- the decision -------------------------------------------------------------------------

def test_all_green_for_this_version_releases():
    assert _gate().verdict(_all_green("1.0.0"), "1.0.0") == (True, [])


def test_results_for_another_version_do_not_carry_over():
    green, reasons = _gate().verdict(_all_green("1.0.0"), "1.0.1")
    assert not green and any("verified_for" in r for r in reasons)


@pytest.mark.parametrize("status", ["open", "fail"])
def test_one_row_not_passed_refuses(status):
    doc = _all_green()
    _row(doc, "NS-34.6-4")["status"] = status
    green, reasons = _gate().verdict(doc, "1.0.0")
    assert not green and any("NS-34.6-4" in r for r in reasons)


def test_a_pass_without_evidence_refuses():
    doc = _all_green()
    _row(doc, "NS-34.16-1")["evidence"] = "  "
    green, reasons = _gate().verdict(doc, "1.0.0")
    assert not green and any("NS-34.16-1: pass without evidence" in r for r in reasons)


def test_a_missing_row_refuses():
    doc = _all_green()
    doc["rows"] = [r for r in doc["rows"] if r["id"] != "NS-34.17-1"]
    green, reasons = _gate().verdict(doc, "1.0.0")
    assert not green and any("NS-34.17-1: missing" in r for r in reasons)


def test_a_repeated_or_unknown_row_refuses():
    doc = _all_green()
    doc["rows"].append(copy.deepcopy(_row(doc, "NS-34.2-1")))
    doc["rows"].append({"id": "NS-34.99-1", "status": "pass", "evidence": "x"})
    green, reasons = _gate().verdict(doc, "1.0.0")
    assert not green
    assert any("NS-34.2-1: listed 2 times" in r for r in reasons)
    assert any("unknown row 'NS-34.99-1'" in r for r in reasons)


def test_an_unknown_status_refuses():
    doc = _all_green()
    _row(doc, "NS-34.3-1")["status"] = "waived"
    assert not _gate().verdict(doc, "1.0.0")[0]


@pytest.mark.parametrize("rid,status,amendment", [
    ("NS-34.6-4", "deferred", "A10"),     # A10 defers the signing rows only
    ("NS-34.4-1", "moved", "A10"),        # A10 defers; it does not move
    ("NS-34.4-1", "deferred", "A26"),     # an amendment with no exemption here
    ("NS-34.4-1", "deferred", None),
])
def test_an_exemption_the_amendment_does_not_make_refuses(rid, status, amendment):
    doc = _all_green()
    row = _row(doc, rid)
    row["status"] = status
    row.pop("amendment", None)
    if amendment:
        row["amendment"] = amendment
    green, reasons = _gate().verdict(doc, "1.0.0")
    assert not green and any(rid in r and "no amendment allows" in r for r in reasons)


def test_an_unreadable_checklist_refuses(tmp_path):
    bad = tmp_path / "acceptance-34.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _gate().main(["--tag", "v1.0.0", "--checklist", str(bad)]) == 1
    assert _gate().main(["--tag", "v1.0.0", "--checklist", str(tmp_path / "none.json")]) == 1


def test_the_command_releases_an_all_green_checklist(tmp_path):
    p = tmp_path / "acceptance-34.json"
    p.write_text(json.dumps(_all_green("1.0.0")), encoding="utf-8")
    assert _gate().main(["--tag", "v1.0.0", "--checklist", str(p)]) == 0
    assert _gate().main(["--tag", "v1.0.1", "--checklist", str(p)]) == 1


# --- the workflow -------------------------------------------------------------------------

def _jobs():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _runs(job) -> str:
    return " ".join(str(s.get("run") or "") for s in job.get("steps") or [])


def test_the_release_job_needs_the_acceptance_job():
    jobs = _jobs()
    needs = jobs["release"]["needs"]
    needs = [needs] if isinstance(needs, str) else list(needs)
    gates = [n for n in needs if "scripts/release_acceptance.py" in _runs(jobs[n])
             and "TAG_NAME" in _runs(jobs[n])]
    assert gates, f"the release job needs only {needs}; none runs scripts/release_acceptance.py"


def test_the_publish_step_asks_the_acceptance_gate_again():
    publish = [s for s in _jobs()["release"]["steps"] if "gh release create" in str(s.get("run") or "")][0]
    run = str(publish["run"])
    head = run.split("gh release create")[0]
    assert 'scripts/release_acceptance.py --tag "$TAG_NAME"' in head
    assert publish["env"]["TAG_NAME"] == "${{ github.ref_name }}"


def test_the_acceptance_job_cannot_be_talked_past():
    job = _jobs()["acceptance"]
    assert job.get("if") == "startsWith(github.ref, 'refs/tags/v')"
    assert not job.get("continue-on-error")
    for step in job["steps"]:
        assert not step.get("continue-on-error") and "shell" not in step
