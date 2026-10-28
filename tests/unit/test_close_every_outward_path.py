"""NS-36.3-12 (BD-11a, slice A6): every outward action has its decision and a
terminal receipt from its own result.

A5 closed the executors that call `record_external`; the paths that carry out
an action an allowing `authorize_external` let through still left their
decision open, and a site publish the provider was still building stayed
unconfirmed. Here each of those paths runs with a stubbed action:

  * a stubbed success writes exactly one terminal receipt (executed, or
    completed_verified where the service returned its confirmation) for that
    decision, a stubbed failure writes failed, and an exception writes failed
    and is raised as before;
  * the outward audit over a run of all of them finds nothing open and
    nothing unconfirmed;
  * a site publication left unconfirmed keeps its decision on its record and
    the reconcile closes it once the final state is known (also after a
    restart, and never twice); one nobody reconciles is closed on expiry;
  * the same for a domain change.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals

TERMINAL_OK = (G.EXECUTED, G.COMPLETED_VERIFIED)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(G, "verify_claws", lambda *a, **k: (True, "intact"))
    monkeypatch.setattr(G, "outward_hold", lambda: None)
    monkeypatch.setattr(G, "_send_grant", lambda *a, **k: None)
    yield


def _words(ref):
    return [e["rec"]["outcome"] for e in L.entries(["outcome"], newest_first=False)
            if e["rec"].get("action_id") == ref["action_id"]]


def _decisions(tool):
    return [e["rec"] for e in L.entries(["decision"], newest_first=False)
            if e["rec"].get("tool") == tool]


class _Gate:
    """authorize_external as it answers when an approved, unused card for exactly
    this action exists: allow, with the decision it wrote (`decision_ref`)."""

    def __init__(self, monkeypatch):
        self.refs = []
        monkeypatch.setattr(G, "authorize_external", self)

    def __call__(self, action, detail, **kw):
        ref = G.record_external(action, surface="test", approval_id=kw.get("approval_id") or "ap-test")
        self.refs.append(ref)
        return G.Verdict("allow", G.OUTWARD, "approved on a card", detail={"decision_ref": ref})

    @property
    def ref(self):
        assert len(self.refs) == 1, self.refs
        return self.refs[0]


def _wait_for_terminal(ref, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if any(w in G.TERMINAL_OUTCOMES for w in _words(ref)):
            return
        time.sleep(0.02)


# ── each path, with a stubbed action ────────────────────────────────────────

def _compute(mp, tmp_path, ok):
    from agent_friday import core
    from agent_friday.routes import compute
    gate = _Gate(mp)
    mp.setattr(compute.prov, "accept_job", lambda data: (True, "ok"))
    mp.setattr(compute.prov, "execute_job",
               lambda data: {"status": "COMPLETED" if ok else "FAILED", "job_id": data.get("job_id")})
    view = getattr(compute.receive_job, "__wrapped__", compute.receive_job)
    with core.app.test_request_context("/api/federation/compute/request", method="POST",
                                       json={"job_id": "job-1", "capability": "text.generate", "prompt": "hi"}):
        view()
        # the job runs after the answer: the request's settlement leaves its decision alone
        assert G.settle_request_externals(True) == 0
    _wait_for_terminal(gate.ref)
    return gate.ref


def _creations_open(mp, tmp_path, ok):
    from agent_friday import core
    from agent_friday.routes import creations
    from agent_friday.services import agent, open_safety
    gate = _Gate(mp)
    mp.setattr(agent, "_resolve_open_target", lambda p: p)
    mp.setattr(open_safety, "judge", lambda p: (False, "it could run a program"))
    mp.setattr(open_safety, "real_target", lambda p: p)
    mp.setattr(creations, "_perform_open",
               lambda t: "Done — I opened **run.exe** for you." if ok else "I tried to open it but hit an error: no")
    view = getattr(creations.api_computer_open, "__wrapped__", creations.api_computer_open)
    with core.app.test_request_context("/api/computer/open", method="POST", json={"path": "C:/tools/run.exe"}):
        view()
    return gate.ref


class _Loc:
    def __init__(self, ok):
        self.ok = ok

    def _go(self, *a, **k):
        if not self.ok:
            raise RuntimeError("the page went away")

    click = press = fill = select_option = _go


def _browser_session(ok):
    from agent_friday.services import browser_session as bs
    s = bs.BrowserSession.__new__(bs.BrowserSession)
    s._pending, s._held_values, s._nonce, s.display_name = {}, {}, "n0", "Friday"
    s._page = SimpleNamespace(url="https://shop.example/pay")
    s._approval_binding = lambda: {}
    s._preview = lambda i, nonce=None: {"title": "Checkout"}
    s._detail = lambda prev, how: {"handler": bs.HANDLER, "op": "submit", "how": how,
                                   "url": "https://shop.example/f", "fields": [], "attachments": [],
                                   "provenance": []}
    s._words_for = lambda detail, title="": ("Submit a form", "Friday wants to submit")
    s._guard = lambda: None
    s._cursor_action = lambda *a, **k: None
    s._settle = lambda: None
    loc = _Loc(ok)
    s._locator = lambda i, nonce=None: loc
    return s


def _browser_submit(mp, tmp_path, ok):
    gate = _Gate(mp)
    s = _browser_session(ok)
    if ok:
        assert s._submit(0, how="click")["submitted"] is True
    else:
        with pytest.raises(RuntimeError):
            s._submit(0, how="click")
    return gate.ref


def _browser_fill(mp, tmp_path, ok):
    gate = _Gate(mp)
    s = _browser_session(ok)
    if ok:
        assert s._hold_fill(0, {"name": "Card number", "field": "cc"}, "4111111111111111", "")["filled"]
    else:
        with pytest.raises(RuntimeError):
            s._hold_fill(0, {"name": "Card number", "field": "cc"}, "4111111111111111", "")
    return gate.ref


def _inbox_cleanup(mp, tmp_path, ok):
    from agent_friday.services import inbox_cleanup as ic
    gate = _Gate(mp)
    mp.setattr(ic, "_notify", lambda *a, **k: None)
    mp.setattr(ic, "unsubscribe", lambda ids, **kw: (
        {"done": [{"id": "shop.example"}], "not_done": []} if ok else
        {"done": [], "not_done": [{"id": "shop.example", "why": "It refused (HTTP 500)."}]}))
    ic._on_decision({"status": "approved", "approval_id": "ap-inbox",
                     "payload": {"handler": ic.HANDLER, "action": "unsubscribe", "senders": ["shop.example"],
                                 "urls": {"shop.example": "https://unsub.shop.example/x"}, "hosts": {}}})
    return gate.ref


def _item_actions(mp, tmp_path, ok):
    from agent_friday.services import item_actions as ia
    gate = _Gate(mp)
    mp.setattr(approvals, "claim_for_execution", lambda aid: True)
    mp.setattr(approvals, "mark_used", lambda *a, **k: None)
    for name in ("_post_back", "_tell_call", "_notify", "_tell_pages", "_tell_held"):
        mp.setattr(ia, name, lambda *a, **k: None)

    def execute(detail, aid):
        if not ok:
            raise ia.Refused("Google did not take the change")
        return {"status": "complete", "summary": "Archived 2 conversations.", "receipt_id": "rcpt-1"}
    mp.setattr(ia, "_execute", execute)
    ia._on_decision({"status": "approved", "approval_id": "ap-items",
                     "payload": {"handler": ia.HANDLER, "domain": "email", "action": "archive",
                                 "conversation_id": ""}})
    assert ia.wait_for("ap-items", 5) is not None
    return gate.ref


def _mail_proposals(mp, tmp_path, ok):
    from agent_friday.services import mail_proposals as mpp
    gate = _Gate(mp)
    mp.setattr(mpp, "_notify", lambda *a, **k: None)
    mp.setattr(mpp, "_run", lambda detail: {"status": "ok" if ok else "error"})
    mpp._on_decision({"status": "approved", "approval_id": "ap-mail",
                      "payload": mpp._detail("archive", ["m1"], [], {})})
    return gate.ref


def _media_publish(mp, tmp_path, ok):
    from agent_friday.services import media_index as mi
    gate = _Gate(mp)
    mp.setattr(mi, "get", lambda cid: {"id": cid, "status": "kept", "source_kind": "creation", "kind": "image",
                                       "title": "Sunset", "targets": [], "sources": [], "extra": {},
                                       "signed": False})
    mp.setattr(mi, "_publish_description", lambda c: "Publish it.")
    mp.setattr(mi, "complete_publish", lambda cid, approval_id=None: {"status": "ok" if ok else "error"})
    mi.publish("card-1")
    return gate.ref


def _media_tidy(mp, tmp_path, ok):
    from agent_friday.services import media_tidy as mt
    gate = _Gate(mp)
    mp.setattr(mt, "report", lambda *a, **k: {"ids": ["c1"], "groups": [], "stale": [{"id": "c1"}], "bytes": 10})
    mp.setattr(mt, "proposals_dir", lambda: tmp_path / "tidy")
    mp.setattr(mt, "apply", lambda pid, approval_id=None: (
        {"status": "ok", "moved": [{"id": "c1"}], "failed": []} if ok else
        {"status": "ok", "moved": [], "failed": [{"id": "c1", "message": "locked"}]}))
    mt.propose()
    return gate.ref


def _meeting_calendar(mp, tmp_path, ok):
    from agent_friday.services import meeting_calendar as mcal
    gate = _Gate(mp)
    mp.setattr(mcal, "_notify", lambda *a, **k: None)
    mp.setattr(mcal, "_write", lambda **kw: (
        {"ok": True, "changed": [{"id": "ev1"}], "skipped": []} if ok else
        {"ok": False, "error": "Google Calendar did not take it"}))
    mcal._on_decision({"status": "approved", "approval_id": "ap-meet",
                       "payload": {"handler": mcal.HANDLER, "meeting_id": "m", "event_id": "ev1",
                                   "query": "Launch", "note": "n"}})
    return gate.ref


def _setting_hold(mp, tmp_path, ok):
    import agent_friday.services.agent as agent
    from agent_friday.services import setting_proposals as sp
    gate = _Gate(mp)
    mp.setattr(sp, "_store", lambda: tmp_path / "setting_changes.json")
    mp.setattr(sp, "_snapshot", lambda keys: {})
    writes = []

    def tool(inp):                       # a setting tool: asks hold() before it writes
        held = sp.hold("probe_setting_tool", inp, old="off", new="on")
        if held:
            return held
        if not ok:
            return "SETTING_FAIL: that row is read-only"
        writes.append(dict(inp))
        return "Done: probe is on."
    mp.setitem(agent.CLAUDE_TOOL_HANDLERS, "probe_setting_tool", tool)
    said = tool({"mode": "on"})
    assert said == ("Done: probe is on." if ok else "SETTING_FAIL: that row is read-only")
    assert len(writes) == (1 if ok else 0)               # written once, not twice
    return gate.ref


def _telegram(mp, tmp_path, ok):
    from agent_friday.services.channels import base
    from agent_friday.services.channels.telegram_bridge import TelegramBridge
    mp.setattr(base, "_gate_channel_text", lambda text, ch: text)
    mp.setattr(base, "record_channel_send", lambda *a, **k: None)
    a = TelegramBridge.__new__(TelegramBridge)
    a._last_error = None
    a._call = lambda method, params, timeout=30.0: (
        {"ok": True, "result": {"message_id": 42}} if ok else {"ok": False, "description": "Forbidden"})
    a.send("123456789", "hello")
    return _decisions("channel:telegram:send")[-1]


#: (path, its stubbed success's terminal word)
PATHS = [
    ("compute", _compute, G.EXECUTED),
    ("creations_open", _creations_open, G.EXECUTED),
    ("browser_submit", _browser_submit, G.EXECUTED),
    ("browser_fill", _browser_fill, G.EXECUTED),
    ("inbox_cleanup", _inbox_cleanup, G.COMPLETED_VERIFIED),
    ("item_actions", _item_actions, G.EXECUTED),
    ("mail_proposals", _mail_proposals, G.EXECUTED),
    ("media_publish", _media_publish, G.EXECUTED),
    ("media_tidy", _media_tidy, G.EXECUTED),
    ("meeting_calendar", _meeting_calendar, G.COMPLETED_VERIFIED),
    ("setting_hold", _setting_hold, G.EXECUTED),
    ("telegram", _telegram, G.COMPLETED_VERIFIED),
]


@pytest.mark.parametrize("name, run, word", PATHS, ids=[p[0] for p in PATHS])
def test_a_stubbed_success_writes_exactly_one_terminal_receipt(monkeypatch, tmp_path, name, run, word):
    ref = run(monkeypatch, tmp_path, True)
    assert _words(ref) == [word], name


@pytest.mark.parametrize("name, run, word", PATHS, ids=[p[0] for p in PATHS])
def test_a_stubbed_failure_writes_failed(monkeypatch, tmp_path, name, run, word):
    ref = run(monkeypatch, tmp_path, False)
    assert _words(ref) == [G.FAILED], name


def test_an_exception_closes_failed_and_is_raised_as_before(monkeypatch, tmp_path):
    from agent_friday.routes import compute
    from agent_friday.services import mail_proposals as mpp
    from agent_friday.services import media_index as mi

    def boom(*a, **k):
        raise RuntimeError("the service fell over")
    ref = G.record_external("run a federated compute job", surface="test")
    monkeypatch.setattr(compute.prov, "execute_job", boom)
    with pytest.raises(RuntimeError):
        compute.run_job_closing({"job_id": "j"}, ref)
    assert _words(ref) == [G.FAILED]

    gate = _Gate(monkeypatch)
    monkeypatch.setattr(mpp, "_run", boom)
    with pytest.raises(RuntimeError):
        mpp.propose("archive", ["m1"], [], requested_by="friday")
    assert _words(gate.ref) == [G.FAILED]

    gate = _Gate(monkeypatch)
    monkeypatch.setattr(mi, "get", lambda cid: {"id": cid, "status": "kept", "source_kind": "creation",
                                                "kind": "image", "title": "t", "targets": [], "sources": [],
                                                "extra": {}, "signed": False})
    monkeypatch.setattr(mi, "_publish_description", lambda c: "Publish it.")
    monkeypatch.setattr(mi, "complete_publish", boom)
    with pytest.raises(RuntimeError):
        mi.publish("card-2")
    assert _words(gate.ref) == [G.FAILED]


def test_the_real_checkpoint_hands_its_decision_to_the_executor():
    """decision_ref_of reads what an allowing authorize_external really returns."""
    detail = {"what": "probe-a6"}
    first = G.authorize_external("probe:a6", detail, requested_by="test")
    assert first.action == "card" and G.decision_ref_of(first) is None
    card = approvals.find_for_subject("external_action", G.external_subject("probe:a6", detail),
                                      "governed_action")
    approvals.decide(card["approval_id"], "approve", decided_by="owner")
    v = G.authorize_external("probe:a6", detail, requested_by="test")
    ref = G.decision_ref_of(v)
    assert v.action == "allow" and ref and ref["approval"] == card["approval_id"]
    assert G.close_external(ref, True, detail="done")
    assert _words(ref) == [G.EXECUTED]
    assert G.decision_ref_of(G.Verdict("deny", G.OUTWARD, "no")) is None
    assert G.decision_ref_of(None) is None


def test_arming_a_post_publishes_nothing_and_says_so(monkeypatch):
    """request_publish_approval only raises cards: should the check use an
    approved card there, that decision is closed failed (nothing was sent);
    dispatch decides again for the send."""
    from agent_friday.services import publisher as pub
    gate = _Gate(monkeypatch)
    post = {"targets": [{"id": "tgt-a6", "status": "PENDING", "platform": "bluesky", "post_id": "p-a6"}]}
    monkeypatch.setattr(pub.store, "get_post", lambda pid: {"ok": True, "post": post})
    monkeypatch.setattr(pub, "_final_texts", lambda t, p: ["hello"])
    monkeypatch.setattr(pub, "_media_sha", lambda t, p: "")
    monkeypatch.setattr(pub, "_auth_get", lambda tid: {})
    monkeypatch.setattr(pub, "_fresh_after", lambda *a, **k: None)
    monkeypatch.setattr(pub, "_latest_card", lambda subject: None)
    monkeypatch.setattr(pub, "_ensure_decision_hook", lambda: None)
    monkeypatch.setattr(pub, "_note_card_due", lambda t, p: None)
    monkeypatch.setattr(pub, "_card_words", lambda *a, **k: {})
    out = pub.request_publish_approval("p-a6")
    assert out["ok"] is True
    assert _words(gate.ref) == [G.FAILED]


def test_the_closing_helpers_write_nothing_for_anything_but_a_decision():
    assert G.close_external(None, True) is False
    assert G.close_persisted(None, True) is False
    assert G.portable_ref({"class": G.OUTWARD}) is None


# ── site publications: unconfirmed, then reconciled or expired ──────────────

def test_a_site_publication_left_pending_closes_when_reconciled():
    from agent_friday.services import sites_operations as so
    ref = G.record_external(so.KIND, surface="approval_card", approval_id="ap-site-rec")
    op = {"status": "provider_accepted"}
    so._settle_decision(ref, op)                          # the provider took it; not live yet
    assert _words(ref) == [G.UNCONFIRMED]
    assert op["decision_ref"]["action_id"] == ref["action_id"]
    op = json.loads(json.dumps(op))                       # read back after a restart
    op["status"] = "provider_pending"
    assert so._close_reconciled(op) is None and "decision_ref" in op
    op["status"] = "verified_live"
    close = so._close_reconciled(op)
    assert close is not None and "decision_ref" not in op
    close()
    assert _words(ref) == [G.UNCONFIRMED, G.COMPLETED_VERIFIED]
    # a stale copy of the record reconciled again writes nothing more
    stale = {"status": "verified_live", "decision_ref": G.portable_ref(ref)}
    so._close_reconciled(stale)()
    assert _words(ref) == [G.UNCONFIRMED, G.COMPLETED_VERIFIED]


def test_a_site_publication_whose_provider_build_failed_closes_failed():
    from agent_friday.services import sites_operations as so
    ref = G.record_external(so.KIND, surface="approval_card", approval_id="ap-site-fail")
    op = {"status": "unknown"}
    so._settle_decision(ref, op)
    op["status"] = "failed"
    so._close_reconciled(op)()
    assert _words(ref) == [G.UNCONFIRMED, G.FAILED]


def test_a_finished_site_operation_keeps_no_decision():
    from agent_friday.services import sites_operations as so
    ref = G.record_external(so.KIND, surface="approval_card", approval_id="ap-site-built")
    op = {"status": "built", "decision_ref": G.portable_ref(ref)}
    so._settle_decision(ref, op)
    assert _words(ref) == [G.EXECUTED] and "decision_ref" not in op


def _site_op(root, n, status, age_ref):
    from agent_friday.services import sites_operations as so
    site_id = "site-" + "a" * 32
    op_id = "op-" + str(n) * 32
    path = root / site_id / "operations" / (op_id + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"operation_id": op_id, "site_id": site_id, "action": "publish",
                                "status": status, "privacy_generation": 1,
                                "decision_ref": G.portable_ref(age_ref)}), encoding="utf-8")
    assert so._read(path)["decision_ref"]["action_id"] == age_ref["action_id"]
    return path


def test_an_abandoned_site_publication_expires_to_failed(tmp_path, monkeypatch):
    from agent_friday.services import sites_operations as so
    root = tmp_path / "sites"
    monkeypatch.setattr(so, "_root", lambda: root)
    monkeypatch.setattr(so.sites_privacy, "require_generation", lambda *a, **k: None)
    refs = {}
    for n, status in ((1, "unknown"), (2, "content_pending")):
        ref = G.record_external(so.KIND, surface="approval_card", approval_id="ap-site-exp-%d" % n)
        G.close_external(ref, True, outcome=G.UNCONFIRMED, detail="site_provider_accepted")
        refs[status] = (ref, _site_op(root, n, status, ref))
    assert so.expire_decisions() == 0                     # not old yet
    later = time.time() + so.DECISION_EXPIRE_S + 60
    assert so.expire_decisions(now=later) == 2
    abandoned, path = refs["unknown"]
    assert _words(abandoned) == [G.UNCONFIRMED, G.FAILED]
    assert "decision_ref" not in json.loads(path.read_text(encoding="utf-8"))
    built, _ = refs["content_pending"]                    # the provider built it; live content unproven
    assert _words(built) == [G.UNCONFIRMED, G.EXECUTED]
    assert so.expire_decisions(now=later) == 0            # once only


# ── domain changes: the same ────────────────────────────────────────────────

@pytest.fixture
def _domain_store(monkeypatch):
    from agent_friday.services import domain_operations as dom
    state = {"operations": {}}
    monkeypatch.setattr(dom.accounts, "read", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(dom.accounts, "write", lambda data: state.update(json.loads(json.dumps(data))))
    monkeypatch.setattr(dom.sites_privacy, "require_generation", lambda *a, **k: None)
    return state


def test_a_domain_change_left_unknown_closes_when_reconciled_once(_domain_store):
    from agent_friday.services import domain_operations as dom
    ref = G.record_external(dom.KIND, surface="approval_card", approval_id="ap-dom-rec")
    dom._close_decision(ref, "unknown")
    assert _words(ref) == [G.UNCONFIRMED]
    op = {"operation_id": "dop-1", "status": "provider_verified", "plan": {"owner": {"privacy_generation": 1}},
          "decision_ref": G.portable_ref(ref)}
    close = dom._close_reconciled(op)
    assert "decision_ref" not in op
    close()
    assert _words(ref) == [G.UNCONFIRMED, G.COMPLETED_VERIFIED]
    # the attempt's own close after its in-run reconcile closed the kept copy: nothing more
    dom._close_decision(ref, "provider_verified")
    assert _words(ref) == [G.UNCONFIRMED, G.COMPLETED_VERIFIED]


def test_an_abandoned_domain_change_expires_to_failed(_domain_store):
    from agent_friday.services import domain_operations as dom
    ref = G.record_external(dom.KIND, surface="approval_card", approval_id="ap-dom-exp")
    dom._close_decision(ref, "unknown")
    _domain_store["operations"]["dop-2"] = {"operation_id": "dop-2", "status": "unknown",
                                            "plan": {"owner": {"privacy_generation": 1}},
                                            "decision_ref": G.portable_ref(ref)}
    assert dom.expire_decisions() == 0
    later = time.time() + dom.DECISION_EXPIRE_S + 60
    assert dom.expire_decisions(now=later) == 1
    assert _words(ref) == [G.UNCONFIRMED, G.FAILED]
    assert "decision_ref" not in _domain_store["operations"]["dop-2"]
    assert dom.expire_decisions(now=later) == 0


# ── the audit over a run of all of them ─────────────────────────────────────

def test_the_audit_over_a_run_of_every_path_finds_nothing_open_or_unconfirmed(monkeypatch, tmp_path, _domain_store):
    from agent_friday.services import domain_operations as dom
    from agent_friday.services import outward_audit
    from agent_friday.services import sites_operations as so
    refs = []
    for name, run, _word in PATHS:
        with monkeypatch.context() as mp:
            refs.append(run(mp, tmp_path / name, True))
    for name, run, _word in PATHS:
        with monkeypatch.context() as mp:
            refs.append(run(mp, tmp_path / (name + "-f"), False))
    site = G.record_external(so.KIND, surface="approval_card", approval_id="ap-site-run")
    op = {"status": "provider_accepted"}
    so._settle_decision(site, op)
    op["status"] = "verified_live"
    so._close_reconciled(op)()
    domain = G.record_external(dom.KIND, surface="approval_card", approval_id="ap-dom-run")
    dom._close_decision(domain, "provider_verified")
    refs += [site, domain]
    for ref in refs:
        assert len([w for w in _words(ref) if w in G.TERMINAL_OUTCOMES]) == 1, ref.get("tool")
    r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])
    c = r["counts"]
    assert c["open"] == 0 and c["unconfirmed"] == 0 and c["duplicate"] == 0, r
    assert c["closed"] >= len(refs)
