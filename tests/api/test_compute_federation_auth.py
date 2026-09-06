"""A claim relayed from the grow-button spec session, investigated 2026-09-03:
`POST /api/federation/compute/request` (routes/compute.py) had no
`@login_required`, unlike most of its siblings in the same file, and routes
to code execution -- so the claim was "unauthenticated arbitrary code
execution, shipped and running."

VERIFIED, AND PARTLY WRONG. Two separate things are true here:

1. The DANGEROUS CAPABILITY claim holds completely. receive_job() ->
   accept_job() gates on nothing but a caller-SELF-REPORTED
   `requester_trust_score` (services/compute_provider.py:171); capability
   "analysis.run" then runs the caller's own `prompt` field as a Python
   script via subprocess with `env={**os.environ}` -- the full process
   environment (services/worker_adapters/python_script_adapter.py). This is
   real, and TestArbitraryCodeExecutionIsReal below proves it with a live,
   harmless payload through the real HTTP route and the real background
   execution thread -- no mocks anywhere in that chain.

2. The "unauthenticated / no auth decorator" framing does NOT mean what it
   sounds like. `core.check_auth()` (`@app.before_request`, core/__init__.py
   :2499) runs before EVERY route in this app, decorated or not, and already
   fail-closes any non-loopback caller with no FRIDAY_REMOTE_KEY. Verified
   empirically below: a non-loopback caller hitting the undecorated
   receive_job() got 401 BEFORE any decorator was added to this file, and a
   `@login_required`-decorated sibling (active_jobs) produces the identical
   401 for the identical caller. The decorator makes zero difference to
   enforcement outcome for either the network vector or the loopback vector
   -- loopback bypasses BOTH check_auth and login_required identically. It
   was added to routes/compute.py anyway (three routes: receive_job,
   job_status, receive_result), for consistency with the file's own pattern
   and as an independent second layer, not because it closes a gap that
   existed. This file's tests prove that framing precisely, not the
   originally-relayed one.

The REAL remaining risk -- any LOOPBACK caller (i.e., any process running as
The maintainer, not just his browser) gets free code execution here via a trust
check that is a self-reported float -- is unchanged by this fix and cannot
be closed by an auth decorator. It is reported, not silently redesigned;
see the session's report to the maintainer.
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

    def test_loopback_caller_gets_a_script_executed_with_full_env(self, client, tmp_path):
        marker = tmp_path / "rce_poc_marker.txt"
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
            # trust score", not "the maintainer, specifically".
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
    """The part of the relayed claim that does NOT hold: `check_auth()`'s
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
