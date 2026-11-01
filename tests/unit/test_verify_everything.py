"""A10: the owner can verify everything (NS-18.9-2).

  * artifact fingerprints: each file the artifact manifest recorded is hashed
    again against its signed fingerprint: ok, changed, missing; bounded
    (files, bytes, time) and partial past a cap; never a path in the result;
  * outward effects without a decision receipt: an approved card an outward
    executor carried out, and an approved batch run, must name a decision the
    ledger holds; a send the egress ledger records names the action id of
    the decision that authorised it (texts, chat channels, mail, site
    publishes, posts), matched like a batch run's; a send whose row has none
    is counted as not linkable; a post keeps its decision's id in the publish
    log;
  * edited lines and decisions with no final outcome, labelled;
  * the integrity view, the route, the Doctor (code within 160 characters,
    a failure is unverified, never a crash) and the Ledger page show them.
Synthetic files, ids and records only; ids are made at run time.
"""
from __future__ import annotations

import hashlib
import json
import time
import types
from pathlib import Path

import pytest

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals
from agent_friday.services import artifact_manifest as am

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from agent_friday.services import egress_gate, idempotency_index, outward_audit
    from agent_friday.services import verify_everything as VE
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(G, "verify_claws", lambda *a, **k: (True, "intact"))
    monkeypatch.setattr(G, "outward_hold", lambda: None)
    monkeypatch.setattr(G, "_send_grant", lambda *a, **k: None)
    monkeypatch.setattr(am, "manifest_path", lambda: tmp_path / "artifact-manifest.jsonl")
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "egress-log.jsonl")
    idempotency_index.reset()
    outward_audit.reset_cache()
    VE.reset_cache()
    yield
    idempotency_index.reset()
    outward_audit.reset_cache()
    VE.reset_cache()


def _made(tmp_path, name: str, data: bytes, tool: str = "write_file") -> Path:
    """A file and its signed manifest line, as artifact_manifest.record_call writes one."""
    d = tmp_path / "made"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(data)
    line = am._sign({"kind": "artifact", "ts": "2026-10-29T10:00:00Z", "path": str(p.resolve()),
                     "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "tool": tool,
                     "task_id": None, "turn_id": None, "args_hash": None, "receipt": None,
                     "provenance": False, "qa": {"checked": False, "ok": True, "findings": []}})
    with open(am.manifest_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    return p


# ── 1. artifact fingerprints ────────────────────────────────────────────────

def test_an_intact_file_is_ok_a_changed_one_changed_and_a_deleted_one_missing(tmp_path):
    from agent_friday.services import ledger_integrity
    from agent_friday.services import verify_everything as VE
    _made(tmp_path, "intact.md", b"made by friday")
    edited = _made(tmp_path, "edited.md", b"first words")
    gone = _made(tmp_path, "gone.md", b"soon gone")
    edited.write_bytes(b"other words")              # same size, other bytes: hashed, changed
    gone.unlink()
    VE.reset_cache()
    a = ledger_integrity.integrity()["artifacts"]
    c = a["counts"]
    assert (c["ok"], c["changed"], c["missing"], c["unreadable"]) == (1, 1, 1, 0), a
    assert a["checked"] == 3 and a["partial"] is False and c["bad_lines"] == 0
    assert sorted(i["state"] for i in a["items"]) == ["changed", "missing"]
    text = json.dumps(a)
    assert str(tmp_path) not in text and "edited.md" not in text, "never a path"


def test_a_file_of_another_size_is_changed_and_a_forged_manifest_line_is_counted(tmp_path):
    from agent_friday.services import verify_everything as VE
    p = _made(tmp_path, "grown.md", b"short")
    p.write_bytes(b"a good deal longer now")
    forged = dict(json.loads(am.manifest_path().read_text(encoding="utf-8").splitlines()[0]),
                  path=str(tmp_path / "elsewhere.md"))
    with open(am.manifest_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(forged) + "\n")
    a = VE.artifact_check()
    assert a["counts"]["changed"] == 1 and a["counts"]["bad_lines"] == 1


def test_the_caps_report_a_partial_check(tmp_path):
    from agent_friday.services import verify_everything as VE
    for i in range(3):
        _made(tmp_path, "f%d.md" % i, b"content %d" % i)
    a = VE.artifact_check(max_files=1)
    assert a["partial"] is True and a["checked"] == 1 and a["counts"]["unchecked"] == 2
    VE.reset_cache()
    b = VE.artifact_check(max_bytes=1)           # each file alone is more than the byte cap
    assert b["counts"]["not_hashed"] == 3 and b["checked"] == 3 and b["counts"]["unchecked"] == 0
    assert b["hashed_bytes"] == 0
    c = VE.artifact_check(max_seconds=-1.0)
    assert c["partial"] is True and c["checked"] == 0
    assert VE.artifact_check()["partial"] is False


def test_a_file_past_the_byte_cap_does_not_stop_older_files_being_checked(tmp_path):
    from agent_friday.services import verify_everything as VE
    small = _made(tmp_path, "small.md", b"first words")          # oldest, 11 bytes
    _made(tmp_path, "middle.md", b"m" * 20)                       # 20 bytes
    _made(tmp_path, "fits.md", b"f" * 25)                         # 25 bytes
    _made(tmp_path, "big.md", b"b" * 100)                         # newest, alone past the cap
    small.write_bytes(b"other words")                             # same size, other bytes
    VE.reset_cache()
    a = VE.artifact_check(max_bytes=40)
    c = a["counts"]
    # big: not_hashed; fits: ok (25); middle: 25+20 > 40, skipped; small: 25+11, changed
    assert c["not_hashed"] == 1 and c["ok"] == 1 and c["unchecked"] == 1 and c["changed"] == 1, a
    assert a["partial"] is True and a["checked"] == 3 and a["hashed_bytes"] == 36
    assert [i["state"] for i in a["items"]] == ["changed"]


def test_a_file_that_grew_after_the_stat_is_changed_and_read_no_further(tmp_path, monkeypatch):
    from agent_friday.services import verify_everything as VE
    p = _made(tmp_path, "growing.md", b"g" * 10)
    real_open = open
    opened = []

    class _Growing:                       # the file as read: longer than its stat said
        def __init__(self, f):
            self.f, self.asked = f, []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def read(self, n):
            self.asked.append(n)
            return b"g" * n

    def fake_open(path, mode="r", *a, **k):
        if str(path) == str(p.resolve()) and "b" in mode:
            g = _Growing(real_open(path, mode, *a, **k))
            opened.append(g)
            return g
        return real_open(path, mode, *a, **k)
    with monkeypatch.context() as m:
        m.setattr("builtins.open", fake_open)
        digest, read, out_of_time = VE._hash_file(str(p.resolve()), 10, p.stat().st_mtime_ns)
    assert digest is None and read == 11 and out_of_time is False
    assert sum(opened[0].asked) == 11, "at most the stat size and one byte more"


def test_the_time_cap_is_checked_while_one_big_file_is_read(tmp_path, monkeypatch):
    from agent_friday.services import verify_everything as VE
    monkeypatch.setattr(VE, "_CHUNK", 4)
    _made(tmp_path, "older.md", b"older")
    _made(tmp_path, "long.md", b"x" * 64)                          # newest: 16 chunks
    VE.reset_cache()
    clock = iter(range(1000))           # one second per look at the clock, in this module only
    monkeypatch.setattr(VE, "time", types.SimpleNamespace(time=time.time,
                                                          monotonic=lambda: float(next(clock))))
    a = VE.artifact_check(max_seconds=3.0)
    assert a["partial"] is True and a["checked"] == 0 and a["counts"]["unchecked"] == 2, a
    assert 0 < a["hashed_bytes"] < 64, "stopped part way through the file"


# ── 2. outward effects without a decision receipt ───────────────────────────

def _used_card(card_id: str, by: str, detail: dict) -> None:
    approvals._upsert({"approval_id": card_id, "status": "approved", "kind": "external_message",
                       "title": "a10 probe", "created_at": time.time()})
    approvals.mark_used(card_id, by, detail)


def test_a_card_carried_out_with_no_decision_is_reported_and_one_with_its_decision_is_not():
    from agent_friday.services import ledger_integrity
    alone = "ap_a10_" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]
    decided = alone + "_d"
    _used_card(alone, "gmail_send", {"message_id": "m-" + alone[-4:]})
    ref = G.record_external("gmail:send", surface="mail", approval_id=decided, target="probe")
    G.close_external(ref, True, verified=True)
    _used_card(decided, "gmail_send", {"message_id": "m-" + decided[-4:]})
    _used_card(alone + "_x", "gmail_send", {})          # did not go out: not an effect
    _used_card(alone + "_y", "locks", {"ok": True})     # not an outward executor
    u = ledger_integrity.integrity()["unlinked"]
    assert u["cards"] == {"checked": 2, "linked": 1, "no_decision": 1}, u
    assert u["problems"] == 1
    assert [(i["source"], i["ref"], i["problem"]) for i in u["items"]] == [("card", alone, "no_decision")]


def test_a_batch_run_whose_action_id_has_no_decision_is_reported():
    from agent_friday.services import policy_version
    from agent_friday.services import verify_everything as VE
    ref = G.record_external("item_actions:email", surface="approval_card", approval_id="ap_a10_b",
                            target="probe")
    keys = ("seq", "hash", "tool", "class", "decision", "action_id", "policy_version", "approval")

    def run(run_id, decision_ref):
        rec = {"run_id": run_id, "state": "started", "approval_id": "", "domain": "email",
               "action": "archive", "count": 1, "pid": 0, "at": time.time()}
        if decision_ref is not None:
            rec["decision_ref"] = decision_ref
        L.append(VE.RUN_KIND, rec, durable=True)

    run("brun_a10_linked", {k: ref.get(k) for k in keys})
    run("brun_a10_nodec", dict({k: ref.get(k) for k in keys}, action_id=policy_version.new_action_id()))
    run("brun_a10_noid", None)
    L.append(VE.RUN_KIND, {"run_id": "brun_a10_linked", "state": "finished", "ok": True})
    u = VE.unlinked_outward()
    assert u["batch_runs"] == {"checked": 3, "linked": 1, "no_decision": 1, "missing_id": 1}, u
    got = {(i["ref"], i["problem"]) for i in u["items"]}
    assert got == {("brun_a10_nodec", "no_decision"), ("brun_a10_noid", "missing_id")}
    from agent_friday.services import item_actions
    assert VE.RUN_KIND == item_actions.RUN_KIND


def test_sends_with_no_action_id_are_counted_not_linkable_by_channel():
    from agent_friday.services import egress_gate
    from agent_friday.services import verify_everything as VE
    now = time.time()
    rows = [
        {"ts": now - 40 * 86400, "kind": "call", "via": "service:mail", "channel": "mail", "result": "sent"},
        {"ts": now - 60, "kind": "call", "via": "service:mail", "channel": "mail", "result": "sent"},
        {"ts": now - 50, "kind": "call", "via": "service:sms", "channel": "sms", "result": "sent_unconfirmed"},
        {"ts": now - 40, "kind": "call", "via": "service:mail", "channel": "mail", "result": "failed"},
        {"ts": now - 30, "kind": "call", "via": "service:web_fetch", "channel": "web_fetch", "result": "sent"},
        {"ts": now - 20, "kind": "call", "via": "chat", "provider": "probe", "result": "sent"},
    ]
    with open(egress_gate._DEFAULT_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r) for r in rows) + "\n")
    u = VE.unlinked_outward()
    assert u["not_linkable"] == {"total": 2, "by_channel": {"mail": 1, "sms": 1}}, u
    assert u["problems"] == 0, "not linkable is counted, never guessed to be a problem"


# ── 2b. sends that name the decision that authorised them ──────────────────

def _write_egress(rows) -> None:
    from agent_friday.services import egress_gate
    with open(egress_gate._DEFAULT_LOG, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))


def _send_row(channel: str, call_id: str, action_id=None, *, ago: float = 60.0) -> dict:
    row = {"ts": time.time() - ago, "kind": "call", "call_id": call_id, "via": "service:" + channel,
           "channel": channel, "result": "sent"}
    if action_id is not None:
        row["action_id"] = action_id
    return row


def test_a_send_whose_action_id_names_a_decision_is_linked():
    from agent_friday.services import verify_everything as VE
    ref = G.record_external("phone:sms", surface="phone", target="probe")
    G.close_external(ref, True)
    assert ref.get("action_id"), ref
    _write_egress([_send_row("sms", "eg-a10-linked", ref["action_id"])])
    u = VE.unlinked_outward()
    assert u["sends"] == {"checked": 1, "linked": 1, "no_decision": 0}, u
    assert u["not_linkable"]["total"] == 0 and u["problems"] == 0 and u["linked"] >= 1


def test_a_send_whose_action_id_names_no_decision_is_reported():
    from agent_friday.services import policy_version
    from agent_friday.services import verify_everything as VE
    _write_egress([_send_row("chat_channel", "eg-a10-nodec", policy_version.new_action_id())])
    u = VE.unlinked_outward()
    assert u["sends"] == {"checked": 1, "linked": 0, "no_decision": 1}, u
    assert u["problems"] == 1
    assert [(i["source"], i["ref"], i["by"], i["problem"]) for i in u["items"]] == [
        ("send", "eg-a10-nodec", "chat_channel", "no_decision")]


def test_only_a_send_without_an_action_id_is_not_linkable():
    from agent_friday.services import verify_everything as VE
    ref = G.record_external("gmail:send", surface="mail", target="probe")
    G.close_external(ref, True)
    _write_egress([_send_row("mail", "eg-a10-old"), _send_row("mail", "eg-a10-new", ref["action_id"])])
    u = VE.unlinked_outward()
    assert u["not_linkable"] == {"total": 1, "by_channel": {"mail": 1}}, u
    assert u["sends"]["linked"] == 1 and u["problems"] == 0


@pytest.fixture
def _quiet_egress(monkeypatch):
    """A real egress row, without the kept text or the context manifest (not under test here)."""
    from agent_friday.services import context_manifest, egress_gate
    monkeypatch.setattr(egress_gate, "_fingerprint", lambda obj: (None, 0, ""))
    monkeypatch.setattr(egress_gate, "_off_record_now", lambda **k: False)
    monkeypatch.setattr(context_manifest, "write", lambda *a, **k: None)


def _decisions_made(monkeypatch) -> list:
    """Every decision record_external returns, as the send path got it."""
    made = []
    real = G.record_external

    def spy(*a, **k):
        ref = real(*a, **k)
        made.append(ref)
        return ref
    monkeypatch.setattr(G, "record_external", spy)
    return made


def _egress_calls() -> list:
    from agent_friday.services import egress_gate
    p = egress_gate._DEFAULT_LOG
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []
    return [r for r in rows if r.get("kind") == "call"]


def test_a_text_to_the_owner_records_its_decisions_action_id(monkeypatch, _quiet_egress):
    from agent_friday.phone import config, service
    from agent_friday.services import spend_guard
    from agent_friday.services import verify_everything as VE
    owner = "+15550000001"

    class _Client:                      # Twilio, stubbed: nothing reaches a network
        def send_sms(self, **kw):
            return {"sid": "sid-a10-probe", "status": "queued", "num_segments": 1}
    monkeypatch.setattr(config, "load", lambda: {"enabled": True, "phone_number": "+15550000000",
                                                 "owner_sms_per_hour": 10, "owner_sms_per_day": 10})
    monkeypatch.setattr(config, "api_credentials", lambda: True)
    monkeypatch.setattr(config, "verified_owner_cell", lambda: owner)
    monkeypatch.setattr(spend_guard, "check", lambda *a, **k: None)
    monkeypatch.setattr(service.spool, "ledger_rows", lambda **k: [])
    monkeypatch.setattr(service.spool, "ledger_add", lambda **k: None)
    monkeypatch.setattr(service, "_client", lambda: _Client())
    monkeypatch.setattr(service, "_status_url", lambda p: "")
    monkeypatch.setattr(service, "_gate_text", lambda text: text)
    made = _decisions_made(monkeypatch)
    service.text_owner("A synthetic alert", reason="alert")
    assert len(made) == 1 and made[0].get("action_id"), made
    row = [r for r in _egress_calls() if r.get("channel") == "sms"][-1]
    assert row["action_id"] == made[0]["action_id"]
    assert "synthetic alert" not in json.dumps(row)
    u = VE.unlinked_outward()
    assert u["sends"] == {"checked": 1, "linked": 1, "no_decision": 0}, u


def test_a_chat_channel_send_records_its_decisions_action_id(monkeypatch, _quiet_egress):
    from agent_friday.services.channels import base
    monkeypatch.setattr(base, "_gate_channel_text", lambda text, channel: text)
    made = _decisions_made(monkeypatch)

    class _Telegram(base.ChannelAdapter):
        name = "telegram"

        def _send_raw(self, chat_id, text):      # the platform, stubbed
            return {"ok": True, "message_id": 7}
    _Telegram().send("chat-a10", "A synthetic reply")
    assert len(made) == 1 and made[0].get("action_id"), made
    row = [r for r in _egress_calls() if r.get("channel") == "chat_channel"][-1]
    assert row["action_id"] == made[0]["action_id"]


def test_an_approved_mail_send_records_its_decisions_action_id(tmp_path, monkeypatch, _quiet_egress):
    import googleapiclient.discovery as _disc
    from agent_friday.services import dissent_gate, google_accounts
    from agent_friday.services import gmail_send as gs
    from agent_friday.services import verify_everything as VE

    class _Gmail:                       # Gmail, stubbed: nothing reaches a network
        def users(self):
            return self

        def messages(self):
            return self

        def send(self, userId=None, body=None):     # noqa: N803 (Google's API)
            return self

        def execute(self):
            return {"id": "msg-a10-probe", "threadId": "thr-a10-probe"}
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(gs, "_outbox_path", lambda: tmp_path / "sent_mail.jsonl")
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [
        {"id": "acct_a10", "email": "owner@example.com", "label": "main"}])
    monkeypatch.setattr(gs, "scope_granted", lambda account_id=None: True)
    monkeypatch.setattr(google_accounts, "credentials_for", lambda aid: object())
    monkeypatch.setattr(_disc, "build", lambda *a, **k: _Gmail(), raising=False)
    monkeypatch.setattr(approvals, "_HOOKS", {})    # the test drives send() itself
    card = gs.request_send(to="someone@example.com", subject="A10 probe",
                           body="A synthetic body")["approval_id"]
    approvals.decide(card, "approve")
    made = _decisions_made(monkeypatch)
    assert gs.send(card)["ok"] is True
    assert len(made) == 1 and made[0].get("action_id"), made
    row = [r for r in _egress_calls() if r.get("channel") == "mail"][-1]
    assert row["result"] == "sent" and row["action_id"] == made[0]["action_id"]
    assert "synthetic body" not in json.dumps(row)
    u = VE.unlinked_outward()
    assert u["sends"] == {"checked": 1, "linked": 1, "no_decision": 0}, u
    assert u["cards"]["no_decision"] == 0 and u["problems"] == 0


def test_an_approved_publish_records_its_decisions_action_id(tmp_path, monkeypatch, _quiet_egress):
    from agent_friday.services import publish_hosting
    from agent_friday.services import publish_web as PW
    from agent_friday.services import verify_everything as VE
    bundle = PW.Bundle(files={"index.html": b"<p>A synthetic page</p>"}, title="A10 probe",
                       slug="a10-probe", kind="page", artifact_id="art-a10", conversation_id="",
                       version=1)
    monkeypatch.setattr(PW, "_load_bundle_from_staging", lambda payload: bundle)
    monkeypatch.setattr(PW, "_staging_root", lambda: tmp_path / "staging")
    monkeypatch.setattr(PW, "_read_index", lambda: [])
    monkeypatch.setattr(PW, "_write_index", lambda items: None)
    monkeypatch.setattr(PW, "_post_back", lambda record, text: None)
    # the host, stubbed: nothing reaches a network
    monkeypatch.setattr(publish_hosting, "publish_remote",
                        lambda adapter, b: "https://a10-probe.example.invalid/")
    card = "ap_a10_pub_" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]
    record = {"approval_id": card, "status": "approved", "kind": PW.KIND, "title": "a10 probe",
              "created_at": time.time(),
              "payload": {"title": "A10 probe", "slug": "a10-probe", "adapter": "probe_host",
                          "staging": "stg-a10"}}
    approvals._upsert(dict(record))
    made = _decisions_made(monkeypatch)
    PW._on_decision(record)
    assert len(made) == 1 and made[0].get("action_id"), made
    row = [r for r in _egress_calls() if r.get("channel") == "web_publish"][-1]
    assert row["result"] == "sent" and row["action_id"] == made[0]["action_id"]
    assert (approvals.get_approval(card).get("used_detail") or {}).get("ok") is True
    u = VE.unlinked_outward()
    assert u["sends"] == {"checked": 1, "linked": 1, "no_decision": 0}, u
    assert u["cards"] == {"checked": 1, "linked": 1, "no_decision": 0}, u


def test_a_send_never_carries_anything_but_a_well_formed_action_id(_quiet_egress):
    from agent_friday.services import egress_gate
    egress_gate.record_service_send("mail", "gmail", content="body", action_id="not an id: words")
    egress_gate.record_service_send("mail", "gmail", content="body", action_id=None)
    assert all("action_id" not in r for r in _egress_calls())
    from agent_friday.services import policy_version
    aid = policy_version.new_action_id()
    assert egress_gate.action_id_of({"envelope": {"action_id": aid}}) == aid
    assert egress_gate.action_id_of({"action_id": "act_with words"}) is None


def test_a_post_keeps_its_decisions_action_id_in_the_publish_log(monkeypatch):
    from agent_friday.services import publisher
    ref = G.record_external(publisher.PUBLISH_ACTION, surface="publisher", target="probe")
    lines = []
    monkeypatch.setattr(publisher.store, "append_publish_log", lambda line: lines.append(line))
    monkeypatch.setitem(publisher._DECISIONS, "tgt-a10", ref)
    publisher._log_attempt({"id": "tgt-a10", "post_id": "post-a10", "platform": "probe"}, "confirmed")
    assert lines[-1]["action_id"] == ref["action_id"]
    publisher._DECISIONS.pop("tgt-a10", None)
    publisher._log_attempt({"id": "tgt-a10", "post_id": "post-a10", "platform": "probe"}, "failed")
    assert "action_id" not in lines[-1]


# ── 3. edits and missing final outcomes, labelled ───────────────────────────

def test_an_edited_line_and_decisions_with_no_final_outcome_are_labelled():
    from agent_friday.services import ledger_integrity
    from tests.ledger_support import tamper
    ref = G.record_external("probe:a10_terminal", surface="test")
    v = ledger_integrity.integrity()
    assert v["terminal"]["no_final_outcome"] == v["outward"]["open"]
    assert v["terminal"]["unconfirmed"] == v["outward"]["unconfirmed"]
    assert v["terminal"]["in_flight"] >= 1                 # decided a moment ago, no outcome yet
    assert v["signatures"]["edited"] == 0
    G.close_external(ref, True)
    tamper("decision", lambda r: r.get("tool") == "probe:a10_terminal", lambda r: r.update(target="x"))
    v = ledger_integrity.integrity()
    assert v["signatures"]["edited"] >= 1 and v["signatures"]["bad"] >= v["signatures"]["edited"]


# ── 4. the view, the route, the Doctor, the page ────────────────────────────

def test_the_route_and_the_doctor_show_the_new_sections(tmp_path):
    from agent_friday import core
    from agent_friday.routes import ledger as ledger_routes
    from agent_friday.services import doctor, observer_access
    from agent_friday.services import verify_everything as VE
    ref = G.record_external("probe:a10_clean", surface="test")
    G.close_external(ref, True)
    _made(tmp_path, "kept.md", b"kept as made")
    state, code = doctor._c_record_integrity()
    assert state == "healthy", code
    assert "art:0/0/0/0" in code and "unl:0/0" in code and len(code) <= 160
    route = ledger_routes.api_ledger_integrity
    assert hasattr(route, "__wrapped__")
    assert not any("/api/ledger/integrity".startswith(p) for p in observer_access.READ_ONLY_PREFIXES)
    with core.app.test_request_context("/api/ledger/integrity?days=7"):
        d = route.__wrapped__().get_json()
    v = d["integrity"]
    assert {"artifacts", "unlinked", "terminal"} <= set(v) and "edited" in v["signatures"]
    assert v["artifacts"]["counts"]["ok"] == 1 and v["unlinked"]["window_days"] == 7.0
    _used_card("ap_a10_doctor", "phone_sms", {"sid": "SMa10"})
    VE.reset_cache()
    state, code = doctor._c_record_integrity()
    assert state == "unverified" and "unl:1/0" in code, code


def test_a_part_that_fails_is_unverified_never_a_crash(monkeypatch):
    from agent_friday.services import doctor, ledger_integrity
    from agent_friday.services import verify_everything as VE

    def boom(*a, **k):
        raise RuntimeError("probe")
    monkeypatch.setattr(VE, "artifact_check", boom)
    monkeypatch.setattr(VE, "unlinked_outward", boom)
    v = ledger_integrity.integrity()
    assert v["artifacts_error"] == v["unlinked_error"] == "RuntimeError" and v["ok"] is False
    state, code = doctor._c_record_integrity()
    assert state == "unverified" and "art:err" in code and "unl:err" in code


def test_the_doctor_code_stays_within_160_characters(monkeypatch):
    from agent_friday.services import ledger_integrity
    big = 10 ** 9
    fake = {"ok": False, "policy_version": "pv_" + "a" * 40,
            "signatures": {"ok": True, "bad": big}, "chain": {"breaks": big, "gaps": big},
            "unsigned": {"lines": big},
            "outward": {k: big for k in ("open", "unconfirmed", "duplicate", "duplicate_key", "false_done")},
            "activity": {"state": "intact", "lines": big, "legacy_unchained": big, "activity_breaks": big},
            "artifacts": {"counts": {"changed": big, "missing": big, "unreadable": big, "bad_lines": big},
                          "partial": True},
            "unlinked": {"problems": big, "not_linkable": {"total": big}}}
    monkeypatch.setattr(ledger_integrity, "integrity", lambda *a, **k: dict(fake))
    state, code = ledger_integrity.doctor_check()
    assert state == "unverified" and 0 < len(code) <= 160 and code.startswith("sig_bad:")
    from agent_friday.services import doctor
    assert doctor._SAFE.match(code)


def test_a_clean_doctor_code_keeps_the_whole_policy_version(monkeypatch):
    from agent_friday.services import doctor, ledger_integrity
    pv = "pv_" + "0123456789abcdef"
    fake = {"ok": True, "policy_version": pv,
            "signatures": {"ok": True, "bad": 0}, "chain": {"breaks": 0, "gaps": 0},
            "unsigned": {"lines": 0},
            "outward": {k: 0 for k in ("open", "unconfirmed", "duplicate", "duplicate_key", "false_done")},
            "activity": {"state": "intact", "lines": 10 ** 8, "legacy_unchained": 10 ** 7,
                         "activity_breaks": 0},
            "artifacts": {"counts": {"changed": 0, "missing": 0, "unreadable": 0, "bad_lines": 0},
                          "partial": True},
            "unlinked": {"problems": 0, "not_linkable": {"total": 10 ** 6}}}
    monkeypatch.setattr(ledger_integrity, "integrity", lambda *a, **k: dict(fake))
    state, code = ledger_integrity.doctor_check()
    assert state == "healthy" and len(code) <= ledger_integrity.CODE_MAX == 160, code
    assert code.startswith("sig_bad:0,breaks:0,gaps:0,unsigned:0,open:0")
    assert code.endswith(",pv:" + pv[:16]) and "duplicate_key:0" in code, code
    assert doctor._SAFE.match(code)


def test_the_ledger_page_shows_the_new_block_with_text_only():
    html = (ROOT / "static" / "ledger.html").read_text(encoding="utf-8")
    assert 'id="verify-everything"' in html and "verifyEverything(v)" in html
    start = html.index("function verifyEverything")
    body = html[start:html.index("\n  }\n", start)]
    for words in ("Edited lines", "Decisions with no final outcome", "Files Friday made",
                  "without a decision receipt", "not linkable"):
        assert words in body, words
    assert "textContent" in body and "innerHTML" not in body and "insertAdjacentHTML" not in body
    assert "innerHTML" not in html
