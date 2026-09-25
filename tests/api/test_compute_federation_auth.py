"""`POST /api/federation/compute/request` (routes/compute.py) routes to code
execution. The question these tests answer: is that "unauthenticated arbitrary
code execution"? Two separate things are true:

1. The DANGEROUS CAPABILITY is real. receive_job() ->
   accept_job() gates on nothing but a caller-SELF-REPORTED
   `requester_trust_score` (services/compute_provider.py:171); capability
   "analysis.run" then runs the caller's own `prompt` field as a Python
   script via subprocess with `env={**os.environ}` -- the full process
   environment (services/worker_adapters/python_script_adapter.py). This is
   real, and TestArbitraryCodeExecutionIsReal below proves it with a live,
   harmless payload through the real HTTP route and the real background
   execution thread -- no mocks anywhere in that chain.

2. "Unauthenticated" is not accurate. `core.check_auth()`
   (`@app.before_request`, core/__init__.py) runs before EVERY route in this
   app, decorated or not, and fail-closes any non-loopback caller with no
   FRIDAY_REMOTE_KEY. The tests below show a non-loopback caller gets the same
   401 from receive_job() as from a `@login_required`-decorated sibling
   (active_jobs). The decorator makes no difference to the enforcement outcome
   for either the network vector or the loopback vector -- loopback bypasses
   BOTH check_auth and login_required identically. routes/compute.py carries
   `@login_required` on receive_job, job_status and receive_result for
   consistency and as an independent second layer.

The remaining risk -- any LOOPBACK caller (any process running as the user,
not just the browser) reaching code execution via a trust check that is a
self-reported float -- cannot be closed by an auth decorator. The governance
checkpoint holds such a job for the owner's approval (see below).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


NON_LOOPBACK = "203.0.113.7"  # TEST-NET-3 (RFC 5737) -- not a real host


def _poc_payload(marker_path: Path) -> dict:
    """A REAL, harmless analysis.run job: the code just proves it ran, by
    writing a file identifying the server process -- the same class of side
    effect a real attacker's payload would have, minus the harm."""
    script = (
        "import os, pathlib\n"
        f"pathlib.Path(r{str(marker_path)!r}).write_text(\n"
        "    'RCE-POC ran as pid=%d cwd=%s\\n' % (os.getpid(), os.getcwd())\n"
        ")\n"
    )
    return {
        "job_id": str(uuid.uuid4()),
        "requester_id": "attacker",
        "requester_trust_score": 1.0,       # self-reported -- the only "trust" check there is
        "capability": "analysis.run",
        "prompt": script,                    # becomes worker_script.py, executed verbatim
        "offered_mψ": 5_000,                 # comfortably clears the price floor
        "context": {},
    }


def _wait_for_marker(marker_path: Path, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if marker_path.exists():
            return True
        time.sleep(0.2)
    return False


class TestArbitraryCodeExecutionIsReal:
    """The part of the claim that holds. No mocks: real HTTP route, real
    background thread, real subprocess, real file written to disk."""

    def test_loopback_caller_gets_a_script_executed_only_after_the_owner_approves(
            self, client, tmp_path, monkeypatch):
        """A peer's job passes the governance
        checkpoint and waits on an approval card. Approved, it runs."""
        from agent_friday.services import approvals
        monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
        monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
        marker = tmp_path / "rce_poc_marker.txt"
        held = client.post("/api/federation/compute/request",
                           data=json.dumps(_poc_payload(marker)),
                           content_type="application/json")
        assert held.status_code == 403 and held.get_json()["status"] == "HELD"
        assert not _wait_for_marker(marker, timeout=2.0), "it ran before the owner decided"
        (card,) = approvals.list_approvals(status="pending", kind="governed_action")
        approvals.decide(card["approval_id"], "approve", decided_by="owner")
        resp = client.post(
            "/api/federation/compute/request",
            data=json.dumps(_poc_payload(marker)),
            content_type="application/json",
            # No REMOTE_ADDR override -- the `client` fixture's own docstring:
            # "Requests originate from 127.0.0.1, which Friday's auth treats
            # as the trusted local user, so routes are reachable without
            # login." That IS this app's accepted trust boundary; the point
            # of this test is that "trusted local user" here means "any
            # process able to reach this loopback port with a self-reported
            # trust score", not "the user, specifically".
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert _wait_for_marker(marker, timeout=15.0), (
            "the script did not execute -- if this capability was ever "
            "removed or gated, that's good news, but it means this test "
            "needs updating, not deleting"
        )
        assert "RCE-POC ran as pid=" in marker.read_text(encoding="utf-8")

    def test_trust_score_gate_is_satisfied_by_simply_stating_a_high_score(self):
        """The only 'trust' check accept_job performs, isolated from the
        HTTP layer: does self-reporting 1.0 actually satisfy it?"""
        from agent_friday.services import compute_provider as prov

        accepted, reason = prov.accept_job({
            "requester_trust_score": 1.0,
            "capability": "analysis.run",
            "prompt": "print('hi')",
            "offered_mψ": 5_000,
        })
        assert accepted, f"expected the self-reported score to pass; got: {reason}"


class TestNetworkExposureClaimWasWrong:
    """The part of the claim that does NOT hold: `check_auth()`'s
    global before_request hook already fail-closes a non-loopback caller
    for EVERY route in the app, with or without @login_required. Proven by
    comparing receive_job (this file's target) against active_jobs (an
    undisputed @login_required sibling in the same file) under an identical
    spoofed non-loopback caller -- same verdict, same status code, same
    body, because the SAME hook produced both.
    """

    def test_receive_job_refuses_a_non_loopback_caller(self, client, tmp_path):
        marker = tmp_path / "rce_poc_marker_remote.txt"
        resp = client.post(
            "/api/federation/compute/request",
            data=json.dumps(_poc_payload(marker)),
            content_type="application/json",
            environ_overrides={"REMOTE_ADDR": NON_LOOPBACK},
        )
        assert resp.status_code in (401, 403), resp.get_data(as_text=True)
        assert not _wait_for_marker(marker, timeout=3.0), (
            "a non-loopback caller with no remote key got code executed"
        )

    def test_an_undisputed_login_required_sibling_behaves_identically(self, client):
        """active_jobs has always had @login_required. If the decorator were
        doing distinct enforcement work, this and the test above would be
        able to disagree. They don't -- same caller, same verdict, same
        body -- because core.check_auth() produced both, not the decorator."""
        r_job = client.post(
            "/api/federation/compute/request",
            data=json.dumps({"capability": "text.generate", "prompt": "hi"}),
            content_type="application/json",
            environ_overrides={"REMOTE_ADDR": NON_LOOPBACK},
        )
        r_sibling = client.get(
            "/api/compute/jobs", environ_overrides={"REMOTE_ADDR": NON_LOOPBACK})
        assert r_job.status_code == r_sibling.status_code == 401
        assert r_job.get_json() == r_sibling.get_json() == {"error": "unauthorized"}
