"""A version tag publishes nothing unless the tests are green on that exact commit.

Before this change the installer workflow's release job needed only the install and
upgrade jobs, so a tag pushed onto a commit whose tests were red (or still running, or
never run) published the installer and a GitHub release anyway. The rule lived in
docs/development/release-process.md, not in CI.

Now ``scripts/release_gate.py`` decides from the GitHub runs of ``tests.yml`` and
``codeql.yml`` on the tag's commit; the workflow runs it as its own job before the release
job and again right before ``gh release create``. These tests read the workflow files and
drive the gate's decision with synthetic run objects; nothing here talks to GitHub.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WF = REPO / ".github" / "workflows"
SHA = "a" * 40
OTHER = "b" * 40
REPO_NAME = "example/friday"


def _wf(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _gate():
    path = REPO / "scripts" / "release_gate.py"
    assert path.exists(), "scripts/release_gate.py is missing: nothing checks the tests before a release"
    spec = importlib.util.spec_from_file_location("release_gate_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_script_lines(step: dict) -> str:
    return str(step.get("run") or "")


# --- the workflow files -------------------------------------------------------------------

def _gate_jobs(jobs: dict) -> set[str]:
    out = set()
    for name, job in jobs.items():
        for step in job.get("steps") or []:
            run = _run_script_lines(step)
            if "scripts/release_gate.py" in run and "github.sha" in run \
                    and "tests.yml" in run and "codeql.yml" in run:
                out.add(name)
    return out


def test_the_release_job_needs_a_job_that_checks_the_tests_on_this_commit():
    jobs = _wf("installer.yml")["jobs"]
    release = jobs["release"]
    needs = release.get("needs") or []
    needs = [needs] if isinstance(needs, str) else list(needs)
    gates = _gate_jobs(jobs)
    assert gates & set(needs), (
        f"the release job needs only {needs}; none of them asks scripts/release_gate.py "
        "whether tests.yml and codeql.yml are green on github.sha")
    # The gate job runs on every tag, like the release job.
    for g in gates & set(needs):
        assert "refs/tags/v" in str(jobs[g].get("if", "")), jobs[g].get("if")
        assert "actions" in (jobs[g].get("permissions") or {}), "the gate must be able to read the runs"


def test_the_publish_step_asks_the_gate_again_before_it_creates_the_release():
    steps = _wf("installer.yml")["jobs"]["release"]["steps"]
    publishing = [s for s in steps if "gh release create" in _run_script_lines(s)]
    assert len(publishing) == 1, "exactly one step publishes"
    run = _run_script_lines(publishing[0])
    assert "scripts/release_gate.py" in run, "the publish step does not re-check the tests"
    assert run.index("scripts/release_gate.py") < run.index("gh release create")
    assert "github.sha" in run.split("gh release create")[0]
    # GitHub runs `run:` under bash -e, so a refusing gate (non-zero exit) stops the step.
    assert "|| true" not in run and "continue-on-error" not in publishing[0]


def test_nothing_else_publishes_a_release():
    for path in sorted(WF.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jname, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                run = _run_script_lines(step)
                uses = str(step.get("uses") or "")
                if "gh release create" in run or "gh release upload" in run \
                        or "action-gh-release" in uses or "create-release" in uses:
                    assert (path.name, jname) == ("installer.yml", "release"), (path.name, jname)


@pytest.mark.parametrize("name", ["tests.yml", "codeql.yml"])
def test_the_required_workflows_run_on_the_tag_itself(name):
    """So the tag's exact commit always gets a run for the gate to wait for."""
    push = _on(_wf(name)).get("push") or {}
    tags = push.get("tags") or []
    assert any(str(t).startswith("v") for t in tags), f"{name} does not run on a version tag: {push}"
    assert "workflow_dispatch" in _on(_wf(name)), f"{name} cannot be run by hand on a commit"


# --- the decision -------------------------------------------------------------------------

def _run(rid, *, sha=SHA, event="push", status="completed", conclusion="success", repo=REPO_NAME):
    return {"id": rid, "head_sha": sha, "event": event, "status": status,
            "conclusion": conclusion, "head_repository": {"full_name": repo}}


def test_green_only_when_both_workflows_passed_on_the_exact_commit():
    g = _gate()
    state, why = g.verdict({"tests.yml": [_run(1)], "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    assert state == "green", why


def test_red_tests_refuse():
    g = _gate()
    state, why = g.verdict({"tests.yml": [_run(1, conclusion="failure")],
                            "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    assert state == "red"
    assert any("tests.yml" in w and "did not pass" in w for w in why), why


def test_a_failure_is_not_outvoted_by_another_green_run():
    g = _gate()
    state, _ = g.verdict({"tests.yml": [_run(1, conclusion="failure"), _run(3, event="workflow_dispatch")],
                          "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    assert state == "red"


@pytest.mark.parametrize("conclusion", ["timed_out", "startup_failure", "action_required", "stale", "weird"])
def test_any_unsuccessful_ending_refuses(conclusion):
    g = _gate()
    state, _ = g.verdict({"tests.yml": [_run(1, conclusion=conclusion)], "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    assert state == "red"


def test_running_or_missing_tests_are_pending_not_green():
    g = _gate()
    running = g.verdict({"tests.yml": [_run(1, status="in_progress", conclusion=None)],
                         "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    missing = g.verdict({"tests.yml": [], "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    cancelled = g.verdict({"tests.yml": [_run(1, conclusion="cancelled")],
                           "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    assert running[0] == missing[0] == cancelled[0] == "pending"


def test_green_on_another_commit_does_not_count():
    g = _gate()
    state, _ = g.verdict({"tests.yml": [_run(1, sha=OTHER)], "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    assert state == "pending"


def test_a_pull_request_run_does_not_count_it_tested_a_merge():
    g = _gate()
    state, _ = g.verdict({"tests.yml": [_run(1, event="pull_request")], "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    assert state == "pending"


def test_a_run_from_a_fork_does_not_count():
    g = _gate()
    state, _ = g.verdict({"tests.yml": [_run(1, repo="someone/fork")], "codeql.yml": [_run(2)]}, SHA, REPO_NAME)
    assert state == "pending"


def test_a_short_or_empty_sha_is_refused():
    g = _gate()
    assert g.verdict({"tests.yml": [_run(1)]}, "abc123", REPO_NAME)[0] == "red"
    assert g.verdict({}, SHA, REPO_NAME)[0] == "red"


# --- the command line -----------------------------------------------------------------------

def test_the_command_exits_non_zero_unless_green(monkeypatch, capsys):
    g = _gate()
    table = {"tests.yml": [_run(1, conclusion="failure")], "codeql.yml": [_run(2)]}
    monkeypatch.setattr(g, "fetch_runs", lambda repo, wf, sha: table[wf])
    assert g.main(["--repo", REPO_NAME, "--sha", SHA]) == 1
    table["tests.yml"] = [_run(1, status="queued", conclusion=None)]
    assert g.main(["--repo", REPO_NAME, "--sha", SHA, "--wait-minutes", "0"]) == 2
    table["tests.yml"] = [_run(1)]
    assert g.main(["--repo", REPO_NAME, "--sha", SHA]) == 0
    assert "GREEN" in capsys.readouterr().out


def test_an_unreadable_api_refuses(monkeypatch):
    g = _gate()

    def boom(*_a, **_k):
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(g, "fetch_runs", boom)
    assert g.main(["--repo", REPO_NAME, "--sha", SHA]) == 3
