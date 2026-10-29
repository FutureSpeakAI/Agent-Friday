"""A10: the owner can verify everything (NS-18.9-2).

  * artifact fingerprints: each file the artifact manifest recorded is hashed
    again against its signed fingerprint: ok, changed, missing; bounded
    (files, bytes, time) and partial past a cap; never a path in the result;
  * outward effects without a decision receipt: an approved card an outward
    executor carried out, and an approved batch run, must name a decision the
    ledger holds; sends the egress ledger records carry no action id and are
    counted as not linkable;
  * edited lines and decisions with no final outcome, labelled;
  * the integrity view, the route, the Doctor (code within 160 characters,
    a failure is unverified, never a crash) and the Ledger page show them.
Synthetic files, ids and records only; ids are made at run time.
"""
from __future__ import annotations

import hashlib
import json
import time
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
    b = VE.artifact_check(max_bytes=1)
    assert b["partial"] is True and b["checked"] == 0 and b["counts"]["unchecked"] == 3
    c = VE.artifact_check(max_seconds=-1.0)
    assert c["partial"] is True and c["checked"] == 0
    assert VE.artifact_check()["partial"] is False


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
