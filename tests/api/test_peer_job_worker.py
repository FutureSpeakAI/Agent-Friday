"""Running an invited peer's job needs the owner's approval of that exact job (Federation /
Economy spec, "Work While I'm Away" pilot; roadmap F7).

A job names one owner-installed task class by digest (no job-supplied code), is off unless the
`unattended_worker` capability is on, never runs while the kill switch is engaged, never runs
before or without an approval card for this exact job, shows on that card who sent it, what it
does, what it can touch, its limits and spend, and what data leaves, runs only under the
smaller of the offer's and the owner's limits, and leaves an audit trail. Real approval cards
in a temp file; the sandbox is stubbed except one real trivial run. Offline; fictitious public
test keys only."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

import agent_friday.core as core

try:
    from agent_friday.services import peer_job_worker as pjw
except ImportError:  # red evidence: the worker is not in the tree yet
    pjw = None

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "wire-v2.json").read_text(encoding="utf-8"))
OFFER = VECTORS["cases"][0]["envelope"]["body"]
REQUESTER = OFFER["actor"]
ON = {"held_features": {"federation": True, "fed_unattended_worker": True}}
SOURCE = "print('result: 42')\n"


@pytest.fixture(autouse=True)
def _the_worker_exists():
    assert pjw is not None, "services/peer_job_worker.py does not exist: no gated way to run a peer's job"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)


class Sandbox:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"ok": True, "exit_code": 0, "stdout": "result: 42\n"}

    def __call__(self, code, **kw):
        self.calls.append((code, kw))
        return dict(self.result)


def _catalog(tmp_path, source=SOURCE):
    cat = pjw.TaskCatalog(tmp_path / "tasks")
    return cat, cat.install(source, "adds up the numbers in the input")


def _offer(digest, **scope):
    body = copy.deepcopy(OFFER)
    s = body["payload"]["terms"]["scope"]
    s["approved_tools"] = [digest]
    s["output_bytes"] = 60_000          # the bundle offer asks for 100,000, above the default policy
    s.update(scope)
    return body


def _plan(tmp_path, **scope):
    cat, digest = _catalog(tmp_path)
    return cat, pjw.plan_from_offer(_offer(digest, **scope), {REQUESTER}, cat, now=OFFER["issued_at"] + 1,
                                    peer_names={REQUESTER: "Janet's desktop"})


def _worker(tmp_path, cat, sandbox, settings=ON, **kw):
    return pjw.PeerJobWorker(cat, pjw.AuditLog(tmp_path / "audit.db"), sandbox_run=sandbox,
                             settings=lambda: settings, **kw)


def _card():
    from agent_friday.services import approvals
    cards = approvals.list_approvals(status="pending", kind="governed_action")
    assert len(cards) == 1, cards
    return cards[0]


def _decide(verdict):
    from agent_friday.services import approvals
    approvals.decide(_card()["approval_id"], verdict, decided_by="owner")


# ── the red-twin core: no approval, no run ────────────────────────────────────────────

def test_a_job_does_not_run_until_the_owner_approves_it(tmp_path):
    cat, plan = _plan(tmp_path)
    box = Sandbox()
    worker = _worker(tmp_path, cat, box)
    first = worker.run(plan)
    assert first["status"] == "HELD" and box.calls == [], "the job ran before any approval"
    again = worker.run(plan)
    assert again["status"] == "HELD" and box.calls == [], "waiting on the card must not run it"
    _decide("approve")
    done = worker.run(plan)
    assert done["status"] == "COMPLETED" and len(box.calls) == 1 and done["output"] == "result: 42\n"
    assert worker.run(plan)["status"] == "HELD" and len(box.calls) == 1, "one approval covers one run"


def test_a_declined_job_never_runs(tmp_path):
    cat, plan = _plan(tmp_path)
    box = Sandbox()
    worker = _worker(tmp_path, cat, box)
    worker.run(plan)
    _decide("deny")
    assert worker.run(plan)["status"] == "DECLINED" and box.calls == []


def test_an_approval_for_one_job_does_not_cover_another(tmp_path):
    cat, plan = _plan(tmp_path)
    other = pjw.plan_from_offer({**_offer(plan.task_digest), "job": "J-002"}, {REQUESTER}, cat,
                                now=OFFER["issued_at"] + 1)
    box = Sandbox()
    worker = _worker(tmp_path, cat, box)
    worker.run(plan)
    _decide("approve")
    assert worker.run(other)["status"] == "HELD" and box.calls == []


# ── off by default, kill switch ───────────────────────────────────────────────────────

@pytest.mark.parametrize("settings", [
    {}, {"held_features": {"federation": True}},
    {"held_features": {"federation": True, "fed_unattended_worker": True, "fed_kill_switch": True}},
    {"held_features": {"fed_unattended_worker": True}},
])
def test_off_by_default_and_the_kill_switch_wins(tmp_path, settings):
    cat, plan = _plan(tmp_path)
    box = Sandbox()
    answer = _worker(tmp_path, cat, box, settings).run(plan)
    assert answer["status"] == "OFF" and box.calls == []
    from agent_friday.services import approvals
    assert approvals.list_approvals(status="pending", kind="governed_action") == [], "no card while off"


def test_stop_all_blocks_new_jobs_and_discards_a_running_one(tmp_path):
    cat, plan = _plan(tmp_path)
    holder = {}

    def stopping_sandbox(code, **kw):
        holder["w"].stop_all()          # the owner presses stop while the job runs
        return {"ok": True, "exit_code": 0, "stdout": "late result"}

    worker = _worker(tmp_path, cat, stopping_sandbox)
    holder["w"] = worker
    worker.run(plan)
    _decide("approve")
    stopped = worker.run(plan)
    assert stopped["status"] == "STOPPED" and "output" not in stopped
    box = Sandbox()
    worker._sandbox_run = box
    assert worker.run(plan)["status"] == "OFF" and box.calls == []
    worker.resume()
    steps = [s for s, _ in worker.audit.steps(plan.event_id)]
    assert "stop_requested" in steps and "stopped" in steps


# ── what the card says ────────────────────────────────────────────────────────────────

def test_the_card_says_who_what_touch_limits_spend_and_what_leaves(tmp_path):
    cat, plan = _plan(tmp_path)
    _worker(tmp_path, cat, Sandbox()).run(plan)
    card = _card()
    text = json.dumps(card)
    detail = pjw.approval_detail(plan)
    assert set(detail) >= {"from", "will_do", "can_touch", "limits", "spend", "data_leaves", "event"}
    assert "Janet's desktop" in detail["from"] and REQUESTER[:12] in detail["from"]
    assert detail["will_do"] == "adds up the numbers in the input"
    assert "not your files, vault, keys, the network, the cloud" in detail["can_touch"]
    assert "30 s" in detail["limits"] and "MB memory" in detail["limits"] and "bytes of output" in detail["limits"]
    assert "test credits" in detail["spend"] and "moves no credits" in detail["spend"]
    assert "nothing leaves this computer" in detail["data_leaves"] and "needs your release" in detail["data_leaves"]
    for needle in ("Janet's desktop", plan.event_id):
        assert needle in text, "the card does not show " + needle


# ── what an offer is allowed to ask ───────────────────────────────────────────────────

@pytest.mark.parametrize("scope,code", [
    ({"network": "open"}, "outward_access"), ({"cloud": True}, "outward_access"),
    ({"delegate": True}, "outward_access"), ({"approved_tools": []}, "tool_count"),
    ({"compute_ms": 0}, "unbounded"), ({"output_bytes": 0}, "unbounded"),
    ({"compute_ms": 31_000}, "over_policy"), ({"memory_bytes": 512 * 1024 * 1024}, "over_policy"),
    ({"output_bytes": 10_000_000}, "over_policy"),
])
def test_an_offer_asking_for_more_than_the_pilot_allows_is_refused(tmp_path, scope, code):
    cat, digest = _catalog(tmp_path)
    body = _offer(digest, **scope)
    with pytest.raises(pjw.JobRefused) as e:
        pjw.plan_from_offer(body, {REQUESTER}, cat, now=OFFER["issued_at"] + 1)
    assert e.value.code == code


def test_an_uninstalled_or_altered_task_class_is_refused_never_run(tmp_path):
    cat, digest = _catalog(tmp_path)
    with pytest.raises(pjw.JobRefused) as e:
        pjw.plan_from_offer(_offer("0" * 64), {REQUESTER}, cat, now=OFFER["issued_at"] + 1)
    assert e.value.code == "unknown_task_class"
    (tmp_path / "tasks" / f"{digest}.py").write_text("print('swapped')\n")   # tampered after install
    with pytest.raises(pjw.JobRefused) as e:
        pjw.plan_from_offer(_offer(digest), {REQUESTER}, cat, now=OFFER["issued_at"] + 1)
    assert e.value.code == "unknown_task_class"


def test_an_expired_or_untrusted_offer_is_refused(tmp_path):
    cat, digest = _catalog(tmp_path)
    with pytest.raises(pjw.JobRefused) as e:
        pjw.plan_from_offer(_offer(digest), {REQUESTER}, cat, now=OFFER["expires_at"] + 1)
    assert e.value.code == "expired"
    with pytest.raises(pjw.JobRefused) as e:
        pjw.plan_from_offer(_offer(digest), set(), cat, now=OFFER["issued_at"] + 1)
    assert e.value.code == "untrusted_key"


# ── how it runs ───────────────────────────────────────────────────────────────────────

def test_it_runs_under_the_offers_limits_with_only_the_promised_inputs(tmp_path):
    data = b"1 2 3"
    digest = hashlib.sha256(data).hexdigest()
    cat, plan = _plan(tmp_path, inputs=[{"digest": digest, "media_type": "text/plain", "max_bytes": 100,
                                         "recipient": REQUESTER, "purpose": "numbers"}])
    box = Sandbox()
    worker = _worker(tmp_path, cat, box, inputs_lookup={digest: data}.get)
    worker.run(plan)
    _decide("approve")
    assert worker.run(plan)["status"] == "COMPLETED"
    code, kw = box.calls[0]
    assert kw["timeout_s"] == 30 and kw["memory_mb"] == 96 and code.endswith(SOURCE)
    assert digest in code.splitlines()[0], "the input reaches the task only as base64 in INPUTS"


def test_a_missing_or_wrong_input_stops_the_job_before_any_card(tmp_path):
    digest = hashlib.sha256(b"right").hexdigest()
    cat, plan = _plan(tmp_path, inputs=[{"digest": digest, "media_type": "text/plain", "max_bytes": 100,
                                         "recipient": REQUESTER, "purpose": "numbers"}])
    box = Sandbox()
    answer = _worker(tmp_path, cat, box, inputs_lookup={digest: b"wrong"}.get).run(plan)
    assert answer["reason"] == "input_unavailable" and box.calls == []
    from agent_friday.services import approvals
    assert approvals.list_approvals(status="pending", kind="governed_action") == []


def test_output_is_capped_and_a_failed_job_is_reported_as_failed(tmp_path):
    cat, plan = _plan(tmp_path, output_bytes=1000)
    box = Sandbox({"ok": True, "exit_code": 0, "stdout": "x" * 5000})
    worker = _worker(tmp_path, cat, box)
    worker.run(plan)
    _decide("approve")
    assert len(worker.run(plan)["output"]) == 1000


def test_a_nonzero_exit_is_reported_as_failed(tmp_path):
    cat, plan = _plan(tmp_path)
    box = Sandbox({"ok": True, "exit_code": 1, "stdout": "", "stderr": "boom"})
    worker = _worker(tmp_path, cat, box)
    worker.run(plan)
    _decide("approve")
    assert worker.run(plan)["status"] == "FAILED"


def test_every_step_is_in_the_audit_trail(tmp_path):
    cat, plan = _plan(tmp_path)
    worker = _worker(tmp_path, cat, Sandbox())
    worker.run(plan)
    _decide("approve")
    worker.run(plan)
    steps = [s for s, _ in worker.audit.steps(plan.event_id)]
    assert steps == ["requested", "held", "requested", "started", "finished"]


def test_a_real_sandboxed_run_of_a_trivial_task(tmp_path):
    cat, plan = _plan(tmp_path)
    worker = pjw.PeerJobWorker(cat, pjw.AuditLog(tmp_path / "audit.db"), settings=lambda: ON)
    worker.run(plan)
    _decide("approve")
    done = worker.run(plan)
    assert done["status"] == "COMPLETED" and "result: 42" in done["output"], done
