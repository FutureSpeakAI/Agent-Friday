#!/usr/bin/env python3
"""The release gate: nothing is published unless the tests ran green on the exact commit.

The installer workflow calls this twice on a version tag: once as its own job before the
release job may start, and again inside the release job immediately before ``gh release
create``. It asks GitHub for the runs of each required workflow (``tests.yml`` and
``codeql.yml``) whose ``head_sha`` is the commit being released and decides:

  green    every required workflow has at least one finished run on that commit whose
           conclusion is ``success``, and no finished run of it on that commit ended in
           failure (a failure is not outvoted by a later green run of a different event;
           a rerun of the same run replaces its conclusion, which GitHub reports).
  pending  a required workflow has a queued or running run on that commit, or none yet.
  red      anything else.

Only ``push`` and ``workflow_dispatch`` runs count. A ``pull_request`` run carries the
pull request's head commit as ``head_sha`` but tests a merge of it with the base branch,
so it is not a test of that exact commit. A run from another repository (a fork) never
counts. Cancelled and skipped runs neither pass nor fail.

The decision is a pure function (:func:`verdict`) so it can be tested without GitHub.
Exit codes: 0 green, 1 red, 2 still pending when the wait ran out, 3 usage or API error.
Standard library only: the release job runs on a bare ubuntu runner.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

COUNTED_EVENTS = frozenset({"push", "workflow_dispatch"})
FAILED = frozenset({"failure", "timed_out", "startup_failure", "action_required", "stale"})
NEUTRAL = frozenset({"cancelled", "skipped", "neutral"})
DEFAULT_WORKFLOWS = ("tests.yml", "codeql.yml")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _counts(run: dict, sha: str, repo: str) -> bool:
    if str(run.get("head_sha", "")).lower() != sha:
        return False
    if run.get("event") not in COUNTED_EVENTS:
        return False
    head_repo = (run.get("head_repository") or {}).get("full_name")
    if repo and head_repo and head_repo.lower() != repo.lower():
        return False
    return True


def verdict(runs_by_workflow: dict, sha: str, repo: str = "") -> tuple[str, list[str]]:
    """Decide green / pending / red for ``sha`` from each workflow's list of run objects
    (the ``workflow_runs`` array GitHub returns). Returns (state, reasons); reasons say
    what was seen for every workflow, so a refusal names its cause."""
    sha = (sha or "").strip().lower()
    if not _SHA.match(sha):
        return "red", [f"not a full commit id: {sha!r}"]
    if not runs_by_workflow:
        return "red", ["no required workflow was named"]
    states: list[str] = []
    reasons: list[str] = []
    for wf, runs in runs_by_workflow.items():
        mine = [r for r in (runs or []) if isinstance(r, dict) and _counts(r, sha, repo)]
        running = [r for r in mine if r.get("status") != "completed"]
        done = [r for r in mine if r.get("status") == "completed"]
        failed = [r for r in done if r.get("conclusion") in FAILED or
                  (r.get("conclusion") not in NEUTRAL and r.get("conclusion") != "success")]
        green = [r for r in done if r.get("conclusion") == "success"]
        ids = lambda rs: ", ".join(f"#{r.get('id')} {r.get('event')} {r.get('conclusion') or r.get('status')}" for r in rs)
        if failed:
            states.append("red")
            reasons.append(f"{wf}: a run on {sha[:12]} did not pass ({ids(failed)})")
        elif running:
            states.append("pending")
            reasons.append(f"{wf}: still running on {sha[:12]} ({ids(running)})")
        elif green:
            states.append("green")
            reasons.append(f"{wf}: green on {sha[:12]} ({ids(green)})")
        elif done:
            states.append("pending")
            reasons.append(f"{wf}: only cancelled or skipped runs on {sha[:12]} ({ids(done)}); run it again")
        else:
            states.append("pending")
            reasons.append(f"{wf}: no push or manual run on {sha[:12]} yet")
    if "red" in states:
        return "red", reasons
    if "pending" in states:
        return "pending", reasons
    return "green", reasons


def fetch_runs(repo: str, workflow: str, sha: str) -> list[dict]:
    """Every run of ``workflow`` on ``sha`` through the GitHub CLI (GH_TOKEN in the env)."""
    out: list[dict] = []
    page = 1
    while True:
        cmd = ["gh", "api", "-H", "Accept: application/vnd.github+json",
               f"repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100&page={page}"]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120)
        if res.returncode != 0:
            raise RuntimeError(f"gh api failed for {workflow}: {res.stderr.strip()[:400]}")
        body = json.loads(res.stdout or "{}")
        batch = body.get("workflow_runs") or []
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--sha", required=True, help="the exact commit being released (40 hex)")
    ap.add_argument("--workflow", action="append", default=None,
                    help="a required workflow file name; repeatable (default: tests.yml, codeql.yml)")
    ap.add_argument("--wait-minutes", type=float, default=0.0,
                    help="keep polling while the verdict is pending, up to this long")
    ap.add_argument("--poll-seconds", type=float, default=60.0)
    a = ap.parse_args(argv)
    workflows = a.workflow or list(DEFAULT_WORKFLOWS)
    deadline = time.monotonic() + max(0.0, a.wait_minutes) * 60.0
    while True:
        try:
            runs = {wf: fetch_runs(a.repo, wf, a.sha.lower()) for wf in workflows}
        except Exception as e:  # an API we cannot read never counts as green
            print(f"release gate: REFUSED, could not read the workflow runs: {e}", flush=True)
            return 3
        state, reasons = verdict(runs, a.sha, a.repo)
        for r in reasons:
            print(f"  {r}", flush=True)
        if state == "green":
            print(f"release gate: GREEN on {a.sha}", flush=True)
            return 0
        if state == "red":
            print(f"release gate: REFUSED, the required checks are not green on {a.sha}", flush=True)
            return 1
        if time.monotonic() >= deadline:
            print(f"release gate: REFUSED, the required checks had not finished on {a.sha}", flush=True)
            return 2
        time.sleep(max(1.0, a.poll_seconds))


if __name__ == "__main__":
    sys.exit(main())
